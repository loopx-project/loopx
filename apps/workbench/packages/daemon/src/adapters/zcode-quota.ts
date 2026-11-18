import { readFile } from 'node:fs/promises';
import { createDecipheriv, createHash } from 'node:crypto';
import { homedir, platform, userInfo } from 'node:os';
import { join } from 'node:path';
import type { PlanQuota, QuotaWindow } from './types.js';

// Read the standalone CLI's credential format without refreshing or modifying its store.
export function decryptZcodeCredential(value: unknown, env: NodeJS.ProcessEnv): string {
  if (typeof value !== 'string') return '';
  if (!value.startsWith('enc:v1:')) return value.trim();
  let username = 'unknown';
  try { username = userInfo().username; } catch { /* Match the CLI fallback. */ }
  const secret = env.ZCODE_CREDENTIAL_SECRET?.trim() || `zcode-credential-fallback:${platform()}:${homedir()}:${username}`;
  const parts = value.slice(7).split('.');
  if (parts.length !== 3) throw new Error('Invalid credential');
  const [iv, tag, bytes] = parts.map((v) => Buffer.from(v, 'base64url'));
  const cipher = createDecipheriv('aes-256-gcm', createHash('sha256').update(secret).digest(), iv);
  cipher.setAuthTag(tag);
  return Buffer.concat([cipher.update(bytes), cipher.final()]).toString('utf8').trim();
}

export function zcodeQuotaWindows(payload: any): QuotaWindow[] {
  if (payload?.success === false || (payload?.code !== undefined && payload.code !== 0 && payload.code !== 200)) throw new Error('额度服务拒绝查询，请检查 zcode 登录和套餐状态');
  if (!Array.isArray(payload?.data?.limits)) throw new Error('zcode 额度服务返回了无法识别的数据');
  return payload.data.limits.flatMap((limit: any) => {
    if (!['TOKENS_LIMIT', 'CREDIT_LIMIT', 'TIME_LIMIT'].includes(limit?.type) || typeof limit.percentage !== 'number' || !Number.isFinite(limit.percentage)) return [];
    const label = limit.unit === 3 && typeof limit.number === 'number' && limit.number > 0 ? `${limit.number}H` : limit.unit === 6 ? '7D' : limit.unit === 5 ? '月' : '额度';
    const reset = typeof limit.nextResetTime === 'number' && limit.nextResetTime > 0 ? new Date(limit.nextResetTime) : null;
    return [{ label, scope: limit.type === 'TIME_LIMIT' ? '工具调用' : '模型', usedPercent: Math.max(0, Math.min(100, limit.percentage)), ...(reset && Number.isFinite(reset.getTime()) ? { resetsAt: reset.toISOString() } : {}) }];
  });
}

export async function fetchZcodeQuota(extraEnv: Record<string, string> = {}): Promise<PlanQuota> {
  const env = { ...process.env, ...extraEnv };
  const checkedAt = new Date().toISOString();
  const empty = (note: string): PlanQuota => ({ windows: [], note, checkedAt });
  let authorization = env.ZCODE_BIGMODEL_USAGE_API_KEY?.trim() || env.BIGMODEL_USAGE_API_KEY?.trim();
  const source = authorization ? '环境变量指定凭据' : 'zcode 默认账号';
  let family = 'bigmodel';
  if (!authorization) {
    try {
      const base = env.ZCODE_DATA_BASE_DIR || homedir();
      const directory = join(base.startsWith('~/') ? join(homedir(), base.slice(2)) : base, '.zcode', 'v2');
      const config = JSON.parse(await readFile(env.ZCODE_PERSONAL_PROVIDER_CONFIG_FILE || join(directory, 'provider_config.json'), 'utf8'));
      const provider = config?.config?.defaultModelSelection?.providerId;
      if (provider !== 'account:bigmodel-individual-coding-plan' && provider !== 'account:zai-individual-coding-plan') return empty('当前 zcode 默认供应商未支持额度查询；目前支持 BigModel / Z.ai 个人 Coding Plan');
      family = provider.includes(':zai-') ? 'zai' : 'bigmodel';
      const credentials = JSON.parse(await readFile(join(directory, 'credentials.json'), 'utf8'));
      const identity = decryptZcodeCredential(credentials[`account-provider:${provider}:identity`], env);
      authorization = identity ? decryptZcodeCredential(credentials[`account-provider:coding-plan:${provider}:account:${encodeURIComponent(identity)}:api-key`], env) : '';
      if (!authorization) return empty('没有找到当前 zcode 账号的套餐凭据，请先在 zcode 中登录');
    } catch {
      return empty('无法读取 zcode 套餐配置或凭据，请检查 zcode 登录及凭据加密配置');
    }
  }
  const origin = family === 'zai' ? env.ZAI_BUSINESS_BASE_URL || 'https://api.z.ai' : env.BIGMODEL_API_BASE_URL || 'https://bigmodel.cn';
  const endpoint = env.ZCODE_BIGMODEL_USAGE_QUOTA_URL || env.BIGMODEL_USAGE_QUOTA_URL || new URL('/api/monitor/usage/quota/limit', origin).href;
  try {
    const response = await fetch(endpoint, { headers: { authorization }, signal: AbortSignal.timeout(15_000), redirect: 'error' });
    if (response.status === 401 || response.status === 403) return empty('zcode 套餐凭据已失效或无查询权限，请在 zcode 中重新登录');
    if (!response.ok) return empty(`zcode 额度查询失败（HTTP ${response.status}）`);
    const windows = zcodeQuotaWindows(await response.json());
    return { windows, checkedAt, note: windows.length ? `${family === 'zai' ? 'Z.ai' : 'BigModel'} Coding Plan · ${source}` : '当前账号未返回可用的套餐额度窗口' };
  } catch {
    return empty('zcode 额度查询失败，请检查网络、登录及套餐状态');
  }
}
