import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';

const REQUEST_TIMEOUT_MS = 30_000;
const MAX_FRAME_LENGTH = 32 * 1024 * 1024;

/** ZCode uses NDJSON RPC without the JSON-RPC `jsonrpc` field. */
export class ZcodeWire {
  private readonly child: ChildProcessWithoutNullStreams;
  private readonly pending = new Map<number, { resolve(value: any): void; reject(error: Error): void; timer: NodeJS.Timeout }>();
  private nextId = 1;
  private buffer = '';
  private closed = false;
  onEvent: (method: string, params: any) => void = () => {};
  onRequest: (method: string, params: any) => Promise<unknown> | unknown = () => undefined;
  onClosed: (error: Error) => void = () => {};

  constructor(command: string[], cwd: string, env: NodeJS.ProcessEnv) {
    this.child = spawn(command[0], command.slice(1), { cwd, env, stdio: ['pipe', 'pipe', 'pipe'] });
    this.child.stdout.setEncoding('utf8');
    this.child.stdout.on('data', (chunk: string) => this.receive(chunk));
    // Avoid echoing provider configuration or credentials from an agent's diagnostic logs.
    this.child.stderr.resume();
    this.child.stdin.on('error', (error) => this.close(error));
    this.child.on('error', (error) => this.close(error));
    this.child.on('exit', (code, signal) => this.close(new Error(`zcode 已退出（${signal ?? code}）`)));
  }

  request(method: string, params: unknown, timeoutMs = REQUEST_TIMEOUT_MS, closeOnTimeout = true): Promise<any> {
    if (this.closed) return Promise.reject(new Error('zcode 连接已关闭'));
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        const error = new Error(`zcode 请求超时：${method}`);
        if (closeOnTimeout) this.close(error);
        else { this.pending.delete(id); reject(error); }
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      this.write({ id, method, params });
    });
  }

  close(error = new Error('zcode 连接已关闭')): void {
    if (this.closed) return;
    this.closed = true;
    for (const p of this.pending.values()) { clearTimeout(p.timer); p.reject(error); }
    this.pending.clear();
    this.child.kill();
    this.onClosed(error);
  }

  private write(value: unknown): void {
    if (!this.closed) this.child.stdin.write(JSON.stringify(value) + '\n');
  }

  private receive(chunk: string): void {
    this.buffer += chunk;
    let end: number;
    while ((end = this.buffer.indexOf('\n')) >= 0) {
      if (end > MAX_FRAME_LENGTH) return this.close(new Error('zcode 响应过大'));
      const line = this.buffer.slice(0, end).trim();
      this.buffer = this.buffer.slice(end + 1);
      if (!line) continue;
      try {
        const message = JSON.parse(line);
        if (!message || typeof message !== 'object') throw new Error('Invalid RPC frame');
        if (message.method && message.id !== undefined) {
          void this.reply(message);
        } else if (message.method) {
          this.onEvent(message.method, message.params);
        } else {
          const p = this.pending.get(message.id);
          if (!p) continue;
          clearTimeout(p.timer);
          this.pending.delete(message.id);
          if (message.error) p.reject(new Error(`zcode：${message.error.message ?? '请求失败'}`));
          else p.resolve(message.result);
        }
      } catch {
        this.close(new Error('zcode 返回了无效的协议消息'));
        return;
      }
    }
    if (this.buffer.length > MAX_FRAME_LENGTH) this.close(new Error('zcode 响应过大'));
  }

  private async reply(message: any): Promise<void> {
    try {
      const result = await this.onRequest(message.method, message.params);
      this.write(result === undefined
        ? { id: message.id, error: { code: -32601, message: 'Method not supported' } }
        : { id: message.id, result });
    } catch {
      this.write({ id: message.id, error: { code: -32603, message: 'Client request failed' } });
    }
  }
}
