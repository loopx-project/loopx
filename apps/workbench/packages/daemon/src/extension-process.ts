import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { join } from 'node:path';

const MAX_OUTPUT = 2 * 1024 * 1024;
/** Only a small execution environment crosses the host boundary, never the hub token. */
export function extensionEnvironment(extra: Record<string, string> = {}): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {};
  for (const key of ['PATH', 'HOME', 'USERPROFILE', 'TMPDIR', 'TMP', 'TEMP', 'LANG', 'LC_ALL', 'SystemRoot']) {
    if (process.env[key]) env[key] = process.env[key];
  }
  return { ...env, ...extra };
}

export function runExtensionCommand(command: string, args: string[], cwd: string, env: Record<string, string>, signal: AbortSignal, timeoutMs = 15000): Promise<{ code: number; stdout: string; stderr: string }> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) return reject(new Error('cancelled'));
    const child = spawn(command, args, { cwd, env: extensionEnvironment(env), stdio: ['ignore', 'pipe', 'pipe'], shell: false });
    let stdout = '', stderr = '', size = 0, finished = false;
    const finish = (error?: Error, code = -1) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      signal.removeEventListener('abort', cancel);
      if (error) { child.kill('SIGKILL'); reject(error); }
      else resolve({ code, stdout, stderr });
    };
    const cancel = () => finish(new Error('cancelled'));
    const timer = setTimeout(() => finish(new Error('command_timeout')), timeoutMs);
    signal.addEventListener('abort', cancel, { once: true });
    for (const [stream, append] of [[child.stdout, (s: string) => { stdout += s; }], [child.stderr, (s: string) => { stderr += s; }]] as const) {
      stream.setEncoding('utf8');
      stream.on('data', (s: string) => { size += Buffer.byteLength(s); if (size > MAX_OUTPUT) finish(new Error('output_limit')); else append(s); });
    }
    child.on('error', finish);
    child.on('close', code => finish(undefined, code ?? -1));
  });
}

/** Bounded NDJSON JSON-RPC transport. A failed child can be restarted at most twice. */
export class ExtensionProcess {
  private child: ChildProcessWithoutNullStreams | null = null;
  private pending = new Map<string, { resolve(v: any): void; reject(e: Error): void; timer: NodeJS.Timeout }>();
  private controller = new AbortController();
  private starts = 0;
  private hostRequests = 0;
  private stopped = false;
  constructor(private dir: string, private entry: string, private callHost: (method: string, params: any, signal: AbortSignal) => Promise<unknown>) {}
  private start(): void {
    if (this.child) return;
    if (this.stopped || this.starts >= 3) throw new Error('extension_unavailable');
    this.starts++;
    this.controller = new AbortController();
    const child = spawn(process.execPath, [join(this.dir, this.entry)], { cwd: this.dir, env: extensionEnvironment(), stdio: ['pipe', 'pipe', 'pipe'] });
    this.child = child;
    let buffer = '', stderrBytes = 0;
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', (chunk: string) => {
      if (this.child !== child) return;
      buffer += chunk;
      if (Buffer.byteLength(buffer) > MAX_OUTPUT) return this.fail(new Error('output_limit'));
      let index: number;
      while ((index = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, index); buffer = buffer.slice(index + 1);
        if (!line.trim()) continue;
        try { this.receive(JSON.parse(line), child, this.controller.signal); }
        catch { this.fail(new Error('invalid_extension_protocol')); return; }
      }
    });
    child.stderr.on('data', (chunk: Buffer) => { if (this.child !== child) return; stderrBytes += chunk.length; if (stderrBytes > MAX_OUTPUT) this.fail(new Error('output_limit')); });
    child.stdin.on('error', () => { if (this.child === child) this.fail(new Error('extension_pipe_closed')); });
    child.on('error', () => { if (this.child === child) this.fail(new Error('extension_start_failed')); });
    child.on('exit', () => { if (this.child === child) this.fail(new Error('extension_exited')); });
  }
  private receive(message: any, child: ChildProcessWithoutNullStreams, signal: AbortSignal): void {
    if (message?.jsonrpc !== '2.0') throw new Error('protocol');
    if (typeof message.method === 'string' && message.id !== undefined) {
      if (message.method !== 'commands.run') {
        child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id: message.id, error: { code: -32601, message: 'method_not_available' } }) + '\n');
        return;
      }
      if (this.hostRequests >= 4) throw new Error('too_many_host_requests');
      this.hostRequests++;
      void this.callHost(message.method, message.params, signal).then(
        result => { if (this.child === child && !signal.aborted) child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id: message.id, result }) + '\n'); },
        () => { if (this.child === child && !signal.aborted) child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id: message.id, error: { code: -32000, message: 'command_failed' } }) + '\n'); },
      ).finally(() => { this.hostRequests--; });
      return;
    }
    const pending = this.pending.get(String(message.id));
    if (!pending) return;
    this.pending.delete(String(message.id)); clearTimeout(pending.timer);
    if (message.error) pending.reject(new Error(String(message.error.message).slice(0, 200)));
    else pending.resolve(message.result);
  }
  request(method: string, params: unknown): Promise<any> {
    try { this.start(); } catch (e) { return Promise.reject(e); }
    const id = randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => this.fail(new Error('extension_timeout')), 30000);
      this.pending.set(id, { resolve, reject, timer });
      this.child!.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
    });
  }
  notify(method: string, params: unknown): void {
    if (!this.child && !this.stopped && method === 'extension.activated') { try { this.start(); } catch { return; } }
    if (this.child) this.child.stdin.write(JSON.stringify({ jsonrpc: '2.0', method, params }) + '\n');
  }
  private fail(error: Error): void {
    const child = this.child; this.child = null;
    this.controller.abort();
    child?.kill('SIGKILL');
    for (const p of this.pending.values()) { clearTimeout(p.timer); p.reject(error); }
    this.pending.clear();
  }
  stop(): void { this.stopped = true; this.fail(new Error('extension_stopped')); }
}
