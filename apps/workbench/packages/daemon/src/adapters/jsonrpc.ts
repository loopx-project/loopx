import type { ChildProcessWithoutNullStreams } from 'node:child_process';

export interface RpcHandlers {
  onNotification(method: string, params: unknown): void;
  onRequest(method: string, params: unknown, id?: string | number): Promise<unknown> | unknown;
  /** Called once after the process is gone and pending requests have failed. */
  onClosed?(): void;
}

interface Pending {
  resolve(value: unknown): void;
  reject(err: Error): void;
}

export class RpcError extends Error {
  constructor(
    message: string,
    readonly code: number,
  ) {
    super(message);
  }
}

/**
 * Minimal JSON-RPC 2.0 peer over newline-delimited JSON on a child process's stdio.
 * Used by the ACP adapter. Stderr is forwarded to our own stderr, prefixed.
 */
export class JsonRpcPeer {
  private nextId = 1;
  private readonly pending = new Map<number, Pending>();
  private buffer = '';
  private closed = false;

  constructor(
    private readonly child: ChildProcessWithoutNullStreams,
    private readonly handlers: RpcHandlers,
    private readonly label: string,
  ) {
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', (chunk: string) => this.onData(chunk));
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', (chunk: string) => {
      for (const line of chunk.split('\n')) {
        if (line.trim()) console.error(`[${this.label}] ${line}`);
      }
    });
    child.on('exit', (code, signal) => this.failAll(new Error(`${this.label} exited (code=${code}, signal=${signal})`)));
    child.on('error', (err) => this.failAll(err));
  }

  get isClosed(): boolean {
    return this.closed;
  }

  request(method: string, params: unknown): Promise<any> {
    if (this.closed) return Promise.reject(new Error(`${this.label} is not running`));
    const id = this.nextId++;
    const payload = { jsonrpc: '2.0', id, method, params };
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.write(payload);
    });
  }

  notify(method: string, params: unknown): void {
    if (this.closed) return;
    this.write({ jsonrpc: '2.0', method, params });
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.child.kill();
  }

  private write(obj: unknown): void {
    this.child.stdin.write(JSON.stringify(obj) + '\n');
  }

  private onData(chunk: string): void {
    this.buffer += chunk;
    let idx: number;
    while ((idx = this.buffer.indexOf('\n')) >= 0) {
      const line = this.buffer.slice(0, idx).replace(/\r$/, '');
      this.buffer = this.buffer.slice(idx + 1);
      if (line.trim()) this.onLine(line);
    }
  }

  private onLine(line: string): void {
    let msg: any;
    try {
      msg = JSON.parse(line);
    } catch {
      console.error(`[${this.label}] non-JSON line on stdout: ${line.slice(0, 200)}`);
      return;
    }

    // Response to one of our requests.
    if (msg.id !== undefined && msg.method === undefined) {
      const p = this.pending.get(msg.id);
      if (!p) return;
      this.pending.delete(msg.id);
      if (msg.error) p.reject(new RpcError(msg.error.message ?? 'RPC error', msg.error.code ?? -32000));
      else p.resolve(msg.result);
      return;
    }

    // Request from the agent to us.
    if (msg.id !== undefined && msg.method !== undefined) {
      this.handleIncomingRequest(msg.id, msg.method, msg.params);
      return;
    }

    // Notification.
    if (msg.method !== undefined) {
      try {
        this.handlers.onNotification(msg.method, msg.params);
      } catch (err) {
        console.error(`[${this.label}] notification handler failed`, err);
      }
    }
  }

  private async handleIncomingRequest(id: number | string, method: string, params: unknown): Promise<void> {
    try {
      const result = await this.handlers.onRequest(method, params, id);
      // A pending human request may settle after its agent process has already exited.
      if (this.closed) return;
      if (result === undefined) {
        this.write({ jsonrpc: '2.0', id, error: { code: -32601, message: `Method not supported: ${method}` } });
      } else {
        this.write({ jsonrpc: '2.0', id, result });
      }
    } catch (err) {
      if (this.closed) return;
      const message = err instanceof Error ? err.message : String(err);
      this.write({ jsonrpc: '2.0', id, error: { code: -32603, message } });
    }
  }

  private failAll(err: Error): void {
    this.closed = true;
    for (const [, p] of this.pending) p.reject(err);
    this.pending.clear();
    this.handlers.onClosed?.();
  }
}
