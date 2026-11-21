import { execFile } from 'node:child_process';

export interface RuntimeDiagnostic { protocol: string; version: string | null }

/** Only a version flag, never a prompt or a login/config mutation. */
export function cliDiagnostic(command: string[], env: Record<string, string>, protocol: string): Promise<RuntimeDiagnostic> {
  const args = command.slice(1).filter(arg => !['app-server', 'agent-server', '--acp', 'acp'].includes(arg));
  return new Promise(resolve => {
    execFile(command[0], [...args, '--version'], { env: { ...process.env, ...env }, timeout: 5000, maxBuffer: 8192 }, (error, stdout) => {
      // Do not put arbitrary CLI output (possibly credentials) in a diagnostic report.
      const version = !error ? stdout.match(/\b\d+\.\d+(?:\.\d+)?(?:[-+][\w.-]+)?\b/)?.[0] ?? null : null;
      resolve({ protocol, version });
    });
  });
}

export function diagnosticFailure(error: unknown): { state: string; message: string } {
  const message = String(error);
  if (/unauthori[sz]ed|authentication|not logged|sign.?in required|未登录|凭据|401/i.test(message)) return { state: 'authentication', message: '登录或凭据校验失败，请在该实例的原生 CLI 中检查登录状态。' };
  if (/method not found|unsupported|protocol|升级|不支持|无效的协议/i.test(message)) return { state: 'unsupported', message: '原生 CLI 的协议或功能不兼容，请检查版本。' };
  if (/timeout|timed out|超时/i.test(message)) return { state: 'timeout', message: '读取超时，请检查该机器上的 CLI 和网络后重试。' };
  return { state: 'failed', message: '模型读取失败，请在该机器的原生 CLI 中检查配置、登录及连接。' };
}

/** Probes have a deadline and always release their own process. Live prompts are never timed out here. */
export async function probeWithDeadline<T>(run: () => Promise<T>, close: () => unknown, timeoutMs = 30000): Promise<T> {
  let timer: NodeJS.Timeout | undefined;
  try {
    return await Promise.race([run(), new Promise<never>((_, reject) => {
      timer = setTimeout(() => { void close(); reject(new Error('模型读取超时')); }, timeoutMs);
    })]);
  } finally { clearTimeout(timer); }
}
