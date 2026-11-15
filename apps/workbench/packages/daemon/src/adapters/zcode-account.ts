import { readFile, readdir } from 'node:fs/promises';
import { createHash, randomUUID } from 'node:crypto';
import { homedir } from 'node:os';
import { join, resolve } from 'node:path';
import { decryptZcodeCredential } from './zcode-quota.js';

const PROVIDERS = ['account:bigmodel-individual-coding-plan', 'account:zai-individual-coding-plan'];
const identityKey = (provider: string) => `account-provider:${provider}:identity`;
const credentialKey = (provider: string, identity: string) => `account-provider:coding-plan:${provider}:account:${encodeURIComponent(identity)}:api-key`;
const missing = (error: unknown) => (error as NodeJS.ErrnoException)?.code === 'ENOENT';
const hash = (text: string) => createHash('sha256').update(text).digest('hex');

/** Read-only bridge for the standalone CLI's BigModel / Z.ai individual Coding Plan accounts. */
export async function loadZcodeAccounts(env: NodeJS.ProcessEnv, cwd: string): Promise<ZcodeAccounts | null> {
  const path = (value: string) => resolve(cwd, value.startsWith('~/') ? join(homedir(), value.slice(2)) : value);
  const directory = join(path(env.ZCODE_DATA_BASE_DIR || homedir()), '.zcode', 'v2');
  const credentialsPath = join(directory, 'credentials.json');
  let credentials: Record<string, unknown>;
  try { credentials = JSON.parse(await readFile(credentialsPath, 'utf8')); }
  catch (error) {
    if (missing(error)) return null;
    throw new Error('无法读取 zcode 登录凭据，请检查 zcode 登录状态');
  }
  const identities = new Map<string, string>();
  try {
    for (const provider of PROVIDERS) {
      const identity = decryptZcodeCredential(credentials[identityKey(provider)], env);
      if (identity && decryptZcodeCredential(credentials[credentialKey(provider, identity)], env)) identities.set(provider, identity);
    }
  } catch { throw new Error('无法解密 zcode 登录凭据，请检查凭据加密配置'); }
  if (!identities.size) return null;

  let builtinPath = env.ZCODE_BUILTIN_PROVIDER_CONFIG_FILE?.trim();
  if (builtinPath) builtinPath = path(builtinPath);
  else {
    const platform = `${process.platform === 'win32' ? 'windows' : process.platform}-${process.arch === 'x64' ? 'x86_64' : process.arch === 'arm64' ? 'aarch64' : process.arch}`;
    const origin = new URL(env.ZCODE_BASE_URL?.trim() || env.ZCODE_ENDPOINT_ORIGIN?.trim() || 'https://zcode.z.ai').origin;
    const root = join(directory, 'runtime', 'provider', platform);
    const candidates: string[] = [];
    try {
      for (const version of await readdir(root, { withFileTypes: true })) {
        if (!version.isDirectory() || (env.ZCODE_APP_VERSION && version.name !== env.ZCODE_APP_VERSION)) continue;
        const candidate = join(root, version.name, `endpoint-${hash(origin).slice(0, 32)}`, 'zcode-builtin.json');
        try { await readFile(candidate, 'utf8'); candidates.push(candidate); }
        catch (error) { if (!missing(error)) throw error; }
      }
    } catch (error) { if (!missing(error)) throw new Error('无法读取 zcode 内置模型目录'); }
    if (candidates.length !== 1) throw new Error('无法唯一确定 zcode 内置模型目录，请为此实例指定 ZCODE_BUILTIN_PROVIDER_CONFIG_FILE（原生 zcode-builtin.json 路径）');
    builtinPath = candidates[0];
  }
  let builtin: any;
  try { builtin = JSON.parse(await readFile(builtinPath, 'utf8')); }
  catch { throw new Error('无法读取 zcode 内置模型目录'); }
  if (builtin.schemaVersion !== 1 || !Number.isSafeInteger(builtin.revision) || builtin.revision < 0 || !Array.isArray(builtin.config?.providerConfigRules?.providerRules)) {
    throw new Error('zcode 内置模型目录格式不兼容，请升级 zcode');
  }
  const providers: Record<string, unknown> = {};
  const states: Record<string, unknown> = {};
  for (const rule of builtin.config.providerConfigRules.providerRules) {
    const provider = rule.providerId;
    const access = rule.config?.access;
    if (!PROVIDERS.includes(provider) || access?.type !== 'zhipu-account' || access.mode !== 'individual-coding-plan') continue;
    providers[provider] = { access: { type: 'zhipu-account', entitled: identities.has(provider) } };
    states[provider] = { availability: identities.has(provider) ? 'available' : 'unavailable', entitled: identities.has(provider), current: identities.has(provider) };
  }
  for (const provider of identities.keys()) if (!providers[provider]) identities.delete(provider);
  if (!identities.size) throw new Error('zcode 内置模型目录没有当前账号的 Coding Plan 供应商，请在 zcode 中更新模型目录');
  return new ZcodeAccounts(env, credentialsPath, identities, {
    // Pin both paths in the child only, so the overlay and worker use the same source revision.
    ZCODE_BUILTIN_PROVIDER_CONFIG_FILE: builtinPath,
    ZCODE_PERSONAL_PROVIDER_CONFIG_FILE: path(env.ZCODE_PERSONAL_PROVIDER_CONFIG_FILE || join(directory, 'provider_config.json')),
  }, {
    revision: randomUUID(),
    basedOnZCodeBuiltinRevision: `zcode-builtin:${builtin.revision}:${hash(resolve(builtinPath))}`,
    providers, states,
  });
}

export class ZcodeAccounts {
  constructor(
    private readonly env: NodeJS.ProcessEnv,
    private readonly credentialsPath: string,
    private readonly identities: Map<string, string>,
    readonly environment: Record<string, string>,
    readonly overlay: { revision: string; basedOnZCodeBuiltinRevision: string; providers: Record<string, unknown>; states: Record<string, unknown> },
  ) {}

  async authorize(params: any, sessionId: string): Promise<unknown> {
    const provider = params?.providerId;
    const identity = this.identities.get(provider);
    if (!sessionId || params?.sessionId !== sessionId || params?.modelSelection?.providerId !== provider
      || params?.accountAccess?.mode !== 'individual-coding-plan' || !identity) {
      return { headersApplied: false, errorMessage: 'zcode 请求的账号与当前会话不匹配' };
    }
    try {
      // Re-read for every request: signing out or switching accounts must revoke this session's access.
      const credentials = JSON.parse(await readFile(this.credentialsPath, 'utf8'));
      if (decryptZcodeCredential(credentials[identityKey(provider)], this.env) !== identity) throw new Error('Account changed');
      const apiKey = decryptZcodeCredential(credentials[credentialKey(provider, identity)], this.env);
      if (!apiKey) throw new Error('Missing credential');
      return { headersApplied: true, requestAuth: { apiKey } };
    } catch { return { headersApplied: false, errorMessage: 'zcode 登录已失效或账号已切换，请在 zcode 中登录后重新打开线程' }; }
  }
}
