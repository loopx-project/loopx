import { cp, lstat, mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { startCodeLogin } from './code-login.js';
import { configDirectory } from './session-location.js';
import type { LoginSession } from './types.js';

export const AGY_AUTH_ENV = ['GOOGLE_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_APPLICATION_CREDENTIALS',
  'GOOGLE_CLOUD_ACCESS_TOKEN', 'AGY_ADC_AUTH', 'AGY_ACP_ENABLE_GATEWAY_AUTH',
  'AGY_LLM_GATEWAY_URL', 'AGY_LLM_GATEWAY_PROXY_URL', 'AGY_LLM_GATEWAY_CLIENT_CERT', 'AGY_LLM_GATEWAY_CLIENT_KEY'];

export async function copyAgyAccount(source: string, home: string, target: string, env: Record<string, string>): Promise<Record<string, string>> {
  const config = join(target, '.gemini', 'antigravity-cli');
  await mkdir(config, { recursive: true, mode: 0o700 });
  const copy = async (from: string, to: string) => {
    try { await lstat(from); } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return;
      throw error;
    }
    await mkdir(dirname(to), { recursive: true, mode: 0o700 });
    await cp(from, to, { recursive: true, dereference: true, force: false, errorOnExist: true });
  };
  // Materialize portable customizations. OAuth, project trust, caches and sessions stay put.
  for (const name of ['GEMINI.md', 'AGENTS.md', 'skills', 'agents', 'commands', 'hooks', 'hooks.json', 'rules.json']) {
    await copy(join(source, name), join(config, name));
  }
  await copy(join(home, '.gemini', 'GEMINI.md'), join(target, '.gemini', 'GEMINI.md'));
  await copy(join(home, '.agents', 'skills'), join(target, '.agents', 'skills'));
  const copyJson = async (from: string, to: string, keys: string[]) => {
    let contents: Record<string, unknown>;
    try {
      const text = await readFile(from, 'utf8');
      if (!text.trim()) return; // Native optional configuration may be an empty file.
      contents = JSON.parse(text);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return;
      throw error;
    }
    if (!contents || typeof contents !== 'object' || Array.isArray(contents)) throw new Error('invalid agy configuration');
    await mkdir(dirname(to), { recursive: true, mode: 0o700 });
    await writeFile(to, JSON.stringify(Object.fromEntries(keys.filter(key => key in contents).map(key => [key, contents[key]])), null, 2) + '\n', { mode: 0o600 });
  };
  await copyJson(join(source, 'settings.json'), join(config, 'settings.json'),
    ['colorScheme', 'useTerminalCursor', 'seasonalThemesOff', 'toolFormatterType', 'model', 'effort', 'permissions']);
  await copyJson(join(home, '.gemini', 'config', 'mcp_config.json'), join(target, '.gemini', 'config', 'mcp_config.json'), ['mcpServers']);
  // Print mode ignores LOOCHAT_AGY_HOME: native CLI and its sidecars need their own actual home.
  return { ...env, ...Object.fromEntries(AGY_AUTH_ENV.map(key => [key, ''])), HOME: target,
    XDG_CONFIG_HOME: join(target, '.config'), XDG_DATA_HOME: join(target, '.local', 'share'), XDG_CACHE_HOME: join(target, '.cache'),
    LOOCHAT_AGY_HOME: config };
}

export function startAgyLogin(command: string[], extraEnv: Record<string, string>, onSuccess: (account?: string) => Promise<void>, timeoutMs = 10 * 60_000): LoginSession {
  const env = { ...process.env, ...extraEnv, ANTIGRAVITY_REMOTE_OOB_AUTH: '1', BROWSER: 'true' };
  return startCodeLogin([...command, 'acp', '--login'], env,
    url => url.hostname === 'accounts.google.com' && url.pathname === '/o/oauth2/auth' &&
      url.searchParams.get('redirect_uri') === 'https://antigravity.google/oauth-callback',
    async () => {
      const path = join(configDirectory(extraEnv, 'LOOCHAT_AGY_HOME', ['.gemini', 'antigravity-cli'], process.cwd()), 'antigravity-oauth-token');
      const credentials = JSON.parse(await readFile(path, 'utf8'));
      if (typeof credentials.token?.access_token !== 'string' || !credentials.token.access_token ||
          typeof credentials.token.refresh_token !== 'string' || !credentials.token.refresh_token ||
          typeof credentials.id_token_email !== 'string' || !credentials.id_token_email) throw new Error('login verification failed');
      return credentials.id_token_email;
    }, onSuccess, timeoutMs, 'code-or-url');
}
