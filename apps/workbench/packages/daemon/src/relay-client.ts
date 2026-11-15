import { spawn } from 'node:child_process';
import { request as httpRequest, type ClientRequest, type IncomingMessage, type OutgoingHttpHeaders } from 'node:http';
import { request as httpsRequest } from 'node:https';
import { connect } from 'node:net';
import { homedir, hostname } from 'node:os';
import WebSocket from 'ws';
import type { RelayTaskSpec } from './relay.js';
import { relayForwardable } from './relay.js';

export interface RelayClientOptions {
  /** The hub's address, as the browser opens it: `https://hub.example:8787`. */
  hubUrl: string;
  /** The join token the hub gave for this machine. */
  token: string;
  /** This daemon's private socket: every request from the hub is answered there. */
  socket: string;
  version: string;
  /** The first wait before a dropped connection is opened again; it doubles up to a minute. */
  retryMs?: number;
}

const MAX_RETRY_MS = 60_000;
/** The hub pings every 20 seconds; this long without one means the connection is dead underneath. */
const SILENCE_MS = 50_000;

/**
 * This machine's end of the relay: it connects out to the hub, keeps that one WebSocket open, and
 * whenever the hub wakes it, claims the work waiting over HTTP and answers it from its own daemon.
 * The hub never connects in; a laptop on another network joins the same way as a server.
 */
export class RelayClient {
  status: 'connecting' | 'online' | 'offline' = 'connecting';
  error?: string;
  /** How many times the connection has opened: a number that grows says the machine keeps reconnecting. */
  connections = 0;
  private ws: WebSocket | null = null;
  private retry: NodeJS.Timeout | null = null;
  private silence: NodeJS.Timeout | null = null;
  private delay: number;
  private stopped = false;
  private draining = false;
  private again = false;
  private readonly base: URL;

  constructor(private readonly opts: RelayClientOptions) {
    this.base = new URL(opts.hubUrl);
    this.delay = opts.retryMs ?? 1_000;
  }

  start(): void {
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    if (this.retry) clearTimeout(this.retry);
    if (this.silence) clearTimeout(this.silence);
    this.ws?.terminate();
    this.ws = null;
    this.status = 'offline';
  }

  private connect(): void {
    if (this.stopped) return;
    const url = new URL('/api/relay/connect', this.base);
    url.protocol = this.base.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(url, { headers: { Authorization: `Bearer ${this.opts.token}` }, perMessageDeflate: false });
    this.ws = ws;
    const heard = () => {
      if (this.silence) clearTimeout(this.silence);
      this.silence = setTimeout(() => ws.terminate(), SILENCE_MS);
    };
    ws.on('open', () => {
      heard();
      ws.send(JSON.stringify({ type: 'hello', version: this.opts.version, hostname: hostname() }));
      this.status = 'online';
      this.connections++;
      this.error = undefined;
      this.delay = this.opts.retryMs ?? 1_000;
      // Work may have queued up while this machine was away.
      void this.drain();
    });
    ws.on('ping', heard);
    ws.on('message', (data) => {
      heard();
      try {
        if ((JSON.parse(String(data)) as { type?: string }).type === 'wakeup') void this.drain();
      } catch {
        // Not a message of ours.
      }
    });
    ws.on('unexpected-response', (_req, res) => {
      this.error = res.statusCode === 401 ? 'the hub does not know this join token' : `the hub answered HTTP ${res.statusCode}`;
      ws.terminate();
    });
    ws.on('error', (err) => {
      this.error ??= err.message;
    });
    ws.on('close', () => {
      if (this.ws !== ws) return;
      this.ws = null;
      if (this.silence) clearTimeout(this.silence);
      this.status = 'offline';
      if (this.stopped) return;
      this.retry = setTimeout(() => this.connect(), this.delay);
      this.delay = Math.min(this.delay * 2, MAX_RETRY_MS);
    });
  }

  /** Claims everything waiting until nothing is; a wakeup while claiming means one more round. */
  private async drain(): Promise<void> {
    if (this.draining) {
      this.again = true;
      return;
    }
    this.draining = true;
    try {
      do {
        this.again = false;
        const tasks = await this.poll();
        for (const task of tasks) void this.run(task);
        if (tasks.length) this.again = true;
      } while (this.again && !this.stopped);
    } catch (err) {
      // The next wakeup or reconnect tries again.
      this.error = err instanceof Error ? err.message : String(err);
    } finally {
      this.draining = false;
    }
  }

  private poll(): Promise<RelayTaskSpec[]> {
    return new Promise((resolve, reject) => {
      const req = this.hub('POST', '/api/relay/poll', { 'Content-Length': 0 });
      req.on('response', (res) => {
        const chunks: Buffer[] = [];
        res.on('data', (c: Buffer) => chunks.push(c));
        res.on('end', () => {
          if (res.statusCode !== 200) return reject(new Error(`poll: HTTP ${res.statusCode}`));
          try {
            resolve((JSON.parse(Buffer.concat(chunks).toString('utf8')) as { tasks: RelayTaskSpec[] }).tasks ?? []);
          } catch (err) {
            reject(err);
          }
        });
      });
      req.on('error', reject);
      req.end();
    });
  }

  private async run(task: RelayTaskSpec): Promise<void> {
    try {
      if (task.kind === 'http') await this.runHttp(task);
      else if (task.kind === 'exec') await this.runExec(task);
      else this.runUpgrade(task);
    } catch (err) {
      this.fail(task.id, err instanceof Error ? err.message : String(err));
    }
  }

  /** One request to this daemon, its body fetched from the hub and its answer streamed back. */
  private async runHttp(task: Extract<RelayTaskSpec, { kind: 'http' }>): Promise<void> {
    // Only this daemon's own API is reachable, whatever the hub asks.
    if (!task.path.startsWith('/api/')) throw new Error('only /api/ is relayed');
    const local = httpRequest({ socketPath: this.opts.socket, method: task.method, path: task.path, headers: task.headers, agent: false });
    local.on('response', (up) => {
      const post = this.hub('POST', `/api/relay/tasks/${task.id}/result`, {
        'Content-Type': 'application/octet-stream',
        'x-relay-status': String(up.statusCode ?? 502),
        'x-relay-headers': Buffer.from(JSON.stringify(relayForwardable(up.headers))).toString('base64'),
      });
      // Streams pass as they come: no buffering of an event stream into one late chunk.
      post.flushHeaders();
      up.pipe(post);
      // The hub hanging up (the page went away) ends the request here too.
      post.on('error', () => {
        up.destroy();
        local.destroy();
      });
      post.on('response', (r) => r.resume());
    });
    local.on('error', (err) => this.fail(task.id, err.message));
    if (!task.body) return void local.end();
    const body = await this.fetch(`/api/relay/tasks/${task.id}/body`).catch((err) => {
      local.destroy();
      throw err;
    });
    body.pipe(local);
    body.on('error', () => local.destroy());
  }

  /** One shell command, with a separate input stream: the hub writes the script, stdin carries the data. */
  private runExec(task: Extract<RelayTaskSpec, { kind: 'exec' }>): Promise<void> {
    return new Promise((resolve) => {
      const child = spawn('/bin/sh', ['-c', task.command], { cwd: homedir(), stdio: ['pipe', 'pipe', 'pipe'] });
      const out: Buffer[] = [];
      let size = 0;
      let stderr = '';
      let done = false;
      const finish = (answer: Record<string, unknown>) => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        this.post(task.id, answer).finally(resolve);
      };
      const timer = setTimeout(() => {
        child.kill();
        finish({ error: 'timeout' });
      }, task.timeoutMs);
      child.stdout.on('data', (c: Buffer) => {
        size += c.length;
        if (size > task.maxBytes) {
          child.kill();
          return finish({ error: 'too_large' });
        }
        out.push(c);
      });
      child.stderr.on('data', (c: Buffer) => (stderr = (stderr + c.toString('utf8')).slice(-4000)));
      child.stdin.on('error', () => undefined);
      child.on('error', (err) => finish({ stdout: '', stderr: err.message, code: 127 }));
      child.on('exit', (code) => finish({ stdout: Buffer.concat(out).toString('base64'), stderr, code: code ?? 255 }));
      child.stdin.end(task.input);
    });
  }

  /** Opens a tunnel to the hub and joins it to this daemon's socket, replaying the page's upgrade request. */
  private runUpgrade(task: Extract<RelayTaskSpec, { kind: 'upgrade' }>): void {
    if (!task.path.startsWith('/api/')) throw new Error('only /api/ is relayed');
    const req = this.hub('GET', `/api/relay/tasks/${task.id}/tunnel`, { Connection: 'Upgrade', Upgrade: 'calli-relay' });
    req.on('upgrade', (_res, hub, head) => {
      const local = connect(this.opts.socket);
      local.write([`${task.method} ${task.path} HTTP/1.1`, 'host: localhost', ...task.headers, '', ''].join('\r\n'));
      if (head.length) local.write(head);
      local.pipe(hub);
      hub.pipe(local);
      local.on('error', () => hub.destroy());
      hub.on('error', () => local.destroy());
      local.on('close', () => hub.destroy());
      hub.on('close', () => local.destroy());
    });
    req.on('response', (res) => res.resume());
    req.on('error', () => undefined);
    req.end();
  }

  private fail(id: string, message: string): void {
    const req = this.hub('POST', `/api/relay/tasks/${id}/result`, { 'x-relay-error': encodeURIComponent(message.slice(0, 500)), 'Content-Length': 0 });
    req.on('response', (r) => r.resume());
    req.on('error', () => undefined);
    req.end();
  }

  private post(id: string, answer: Record<string, unknown>): Promise<void> {
    const body = Buffer.from(JSON.stringify(answer));
    return new Promise((resolve) => {
      const req = this.hub('POST', `/api/relay/tasks/${id}/result`, { 'Content-Type': 'application/json', 'Content-Length': body.length });
      req.on('response', (r) => {
        r.resume();
        r.on('end', () => resolve());
      });
      req.on('error', () => resolve());
      req.end(body);
    });
  }

  private fetch(path: string): Promise<IncomingMessage> {
    return new Promise((resolve, reject) => {
      const req = this.hub('GET', path, {});
      req.on('response', (res) => (res.statusCode === 200 ? resolve(res) : (res.resume(), reject(new Error(`${path}: HTTP ${res.statusCode}`)))));
      req.on('error', reject);
      req.end();
    });
  }

  private hub(method: string, path: string, headers: OutgoingHttpHeaders): ClientRequest {
    const url = new URL(path, this.base);
    const send = url.protocol === 'https:' ? httpsRequest : httpRequest;
    return send(url, { method, agent: false, headers: { ...headers, Authorization: `Bearer ${this.opts.token}` } });
  }
}
