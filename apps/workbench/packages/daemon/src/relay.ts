import { createHash, randomBytes, randomUUID, timingSafeEqual } from 'node:crypto';
import type { IncomingHttpHeaders, IncomingMessage, ServerResponse } from 'node:http';
import type { Duplex, Readable } from 'node:stream';
import { WebSocketServer, type WebSocket } from 'ws';
import { HttpError } from './errors.js';
import type { RemoteRun, RemoteRunOptions } from './remote-files.js';

/**
 * The hub's end of machines that connect out to it, the way Mew's daemons reach their platform:
 * each machine keeps one WebSocket open to the hub, and that socket only ever carries a wakeup.
 * Work is claimed over plain HTTP (`poll`), a request body is fetched over HTTP, and the answer is
 * streamed back over HTTP. Nothing on the machine listens for the hub, so a laptop or a box behind
 * NAT works as well as a server, and neither side needs an inbound node connection.
 *
 *   machine ──WebSocket──▶ hub   hello, then wakeups only
 *   machine ──POST poll──▶ hub   claims every task waiting for it
 *   machine ──GET body───▶ hub   the page's request body, streamed
 *   machine ──POST result▶ hub   status, headers, then the answer streamed (SSE included)
 *   machine ──GET tunnel─▶ hub   upgraded to a raw byte pipe, for the desktop's WebSocket
 */

export type RelayFailure = 'not_joined' | 'relay_lost';

/** What a machine is asked to do. The machine runs it against its own private socket or shell. */
export type RelayTaskSpec =
  | { id: string; kind: 'http'; method: string; path: string; headers: Record<string, string | string[]>; body: boolean }
  | { id: string; kind: 'exec'; command: string; input: string; timeoutMs: number; maxBytes: number }
  | { id: string; kind: 'upgrade'; method: string; path: string; headers: string[] };

/** The machine's answer to an http task, as it starts to arrive. */
export interface RelayResponse {
  status: number;
  headers: Record<string, string | string[]>;
  body: IncomingMessage;
}

interface Task {
  spec: RelayTaskSpec;
  claimed: boolean;
  timer: NodeJS.Timeout;
  /** The request body still to be fetched by the machine. */
  body?: Readable | Buffer;
  /** Called with the machine's result request. */
  result?: (req: IncomingMessage, res: ServerResponse) => void;
  /** Called with the machine's tunnel once it is upgraded. */
  tunnel?: (socket: Duplex, head: Buffer) => void;
  fail: (err: HttpError) => void;
}

/** A task nobody claimed by now is not going to be: the machine is gone without having said so. */
const CLAIM_TIMEOUT_MS = 15_000;
/** How long a claimed http task may take before its answer starts. Streams may run on after. */
const ANSWER_TIMEOUT_MS = 60_000;
/** The hub pings every machine this often; one that missed two is cut off. */
const PING_MS = 20_000;
/** Headers that belong to one connection, not to the request. */
const HOP_BY_HOP = new Set(['connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade']);

export function relayForwardable(headers: IncomingHttpHeaders | Record<string, string | string[] | undefined>, drop: string[] = []): Record<string, string | string[]> {
  const out: Record<string, string | string[]> = {};
  for (const [name, value] of Object.entries(headers)) {
    if (value === undefined || HOP_BY_HOP.has(name) || drop.includes(name)) continue;
    out[name] = value;
  }
  return out;
}

const hashSecret = (secret: string): string => createHash('sha256').update(secret).digest('hex');

/** A fresh join token for machine `id`: the id says which machine, the secret proves it. */
export function newJoinToken(id: string): { token: string; hash: string } {
  const secret = randomBytes(32).toString('base64url');
  return { token: `${id}.${secret}`, hash: hashSecret(secret) };
}

function sameHash(a: string, b: string): boolean {
  const x = Buffer.from(a, 'hex');
  const y = Buffer.from(b, 'hex');
  return x.length === y.length && x.length > 0 && timingSafeEqual(x, y);
}

/** One machine that connects out to the hub: its socket, if it has one now, and the work waiting for it. */
export class RelayLink {
  status: 'online' | 'offline' = 'offline';
  code?: RelayFailure = 'not_joined';
  error?: string;
  version?: string;
  /** What the machine calls itself, from its hello. */
  hostname?: string;
  private ws: WebSocket | null = null;
  private ping: NodeJS.Timeout | null = null;
  private readonly tasks = new Map<string, Task>();

  constructor(
    readonly id: string,
    private tokenHash: string,
  ) {}

  /** Whether `secret` is this machine's. */
  owns(secret: string): boolean {
    return sameHash(hashSecret(secret), this.tokenHash);
  }

  /** Takes the machine's new socket, replacing an older one (a reconnect the hub has not noticed yet). */
  attach(ws: WebSocket): void {
    if (this.ws && this.ws !== ws) this.ws.terminate();
    this.ws = ws;
    let alive = true;
    ws.on('pong', () => (alive = true));
    if (this.ping) clearInterval(this.ping);
    this.ping = setInterval(() => {
      if (!alive) return ws.terminate();
      alive = false;
      ws.ping();
    }, PING_MS);
    ws.on('message', (data) => {
      let msg: { type?: string; version?: unknown; hostname?: unknown };
      try {
        msg = JSON.parse(String(data));
      } catch {
        return;
      }
      if (msg.type !== 'hello' || this.ws !== ws) return;
      this.version = typeof msg.version === 'string' ? msg.version.slice(0, 40) : undefined;
      this.hostname = typeof msg.hostname === 'string' ? msg.hostname.slice(0, 120) : undefined;
      this.status = 'online';
      this.code = undefined;
      this.error = undefined;
      // Whatever came in while it was away is waiting for it.
      if (this.tasks.size) this.wake();
    });
    ws.on('close', () => this.detach(ws));
    ws.on('error', () => this.detach(ws));
  }

  /** Every task not yet claimed, now claimed: the machine's `poll`. */
  claim(): RelayTaskSpec[] {
    const out: RelayTaskSpec[] = [];
    for (const task of this.tasks.values()) {
      if (task.claimed) continue;
      task.claimed = true;
      // Claimed: the clock now runs until the answer starts.
      clearTimeout(task.timer);
      const limit = task.spec.kind === 'exec' ? task.spec.timeoutMs + 10_000 : ANSWER_TIMEOUT_MS;
      task.timer = setTimeout(() => this.drop(task.spec.id, new HttpError(504, 'the machine did not answer in time', { code: 'unreachable' })), limit);
      out.push(task.spec);
    }
    return out;
  }

  /** The machine fetches a task's request body. */
  body(id: string, res: ServerResponse): void {
    const task = this.tasks.get(id);
    if (!task?.claimed || !task.body) throw new HttpError(404, 'no such body');
    const body = task.body;
    task.body = undefined;
    res.writeHead(200, { 'Content-Type': 'application/octet-stream' });
    if (Buffer.isBuffer(body)) res.end(body);
    else body.pipe(res);
  }

  /** The machine's answer to a task arrives. */
  result(id: string, req: IncomingMessage, res: ServerResponse): void {
    const task = this.tasks.get(id);
    if (!task?.claimed || !task.result) throw new HttpError(404, 'no such task');
    clearTimeout(task.timer);
    this.tasks.delete(id);
    const error = req.headers['x-relay-error'];
    if (typeof error === 'string') {
      req.resume();
      res.writeHead(204).end();
      return task.fail(new HttpError(502, decodeURIComponent(error), { code: 'host_offline' }));
    }
    task.result(req, res);
  }

  /** The machine's tunnel for an upgrade task has been upgraded. */
  tunnel(id: string, socket: Duplex, head: Buffer): boolean {
    const task = this.tasks.get(id);
    if (!task?.claimed || !task.tunnel) return false;
    clearTimeout(task.timer);
    this.tasks.delete(id);
    task.tunnel(socket, head);
    return true;
  }

  /** Asks the machine's daemon one HTTP request; resolves as soon as its status and headers are known. */
  request(spec: { method: string; path: string; headers: Record<string, string | string[]> }, body: Readable | Buffer | null, signal?: AbortSignal): Promise<RelayResponse> {
    return new Promise((resolve, reject) => {
      const id = randomUUID();
      this.enqueue({
        spec: { id, kind: 'http', ...spec, body: body !== null },
        body: body ?? undefined,
        result: (req, res) => {
          const status = Number(req.headers['x-relay-status']) || 502;
          let headers: Record<string, string | string[]> = {};
          try {
            headers = JSON.parse(Buffer.from(String(req.headers['x-relay-headers'] ?? ''), 'base64').toString('utf8'));
          } catch {
            // An answer without readable headers still has its body.
          }
          // The machine's request ends when the answer has all passed through, or is abandoned.
          req.on('end', () => res.writeHead(204).end());
          resolve({ status, headers, body: req });
        },
        fail: reject,
      });
      signal?.addEventListener('abort', () => this.drop(id, new HttpError(499, 'cancelled')), { once: true });
    });
  }

  /** Runs one shell command on the machine, through the active relay. */
  exec(command: string, opts: RemoteRunOptions = {}): Promise<RemoteRun> {
    const timeoutMs = opts.timeoutMs ?? 20_000;
    const maxBytes = opts.maxBytes ?? 2 * 1024 * 1024;
    return new Promise((resolve, reject) => {
      const id = randomUUID();
      this.enqueue({
        spec: { id, kind: 'exec', command, input: opts.input ?? '', timeoutMs, maxBytes },
        result: (req, res) => {
          const chunks: Buffer[] = [];
          let size = 0;
          req.on('data', (c: Buffer) => {
            size += c.length;
            // base64 of the limit, plus room for the rest of the answer.
            if (size > maxBytes * 1.4 + 64 * 1024) return req.destroy();
            chunks.push(c);
          });
          req.on('error', () => reject(new HttpError(502, 'the machine went away mid-answer', { code: 'host_offline' })));
          req.on('end', () => {
            res.writeHead(204).end();
            try {
              const v = JSON.parse(Buffer.concat(chunks).toString('utf8')) as { stdout?: string; stderr?: string; code?: number; error?: string };
              if (v.error === 'too_large') return reject(new HttpError(413, 'the machine sent more than this operation accepts', { code: 'too_large' }));
              if (v.error === 'timeout') return reject(new HttpError(504, 'the machine did not answer in time', { code: 'unreachable' }));
              resolve({ stdout: Buffer.from(v.stdout ?? '', 'base64'), stderr: String(v.stderr ?? '').slice(-4000), code: typeof v.code === 'number' ? v.code : 255 });
            } catch {
              reject(new HttpError(502, 'the machine sent an answer that could not be read', { code: 'host_offline' }));
            }
          });
        },
        fail: reject,
      });
    });
  }

  /**
   * Joins a page's WebSocket to the machine's daemon: the machine opens a tunnel to the hub, the
   * hub hands the page's socket to it, and from then on bytes pass both ways untouched.
   */
  upgrade(path: string, req: IncomingMessage, socket: Duplex, head: Buffer): boolean {
    if (this.status !== 'online') return false;
    const headers = Object.entries(req.headers)
      .filter(([name, value]) => value !== undefined && name !== 'authorization' && name !== 'host')
      .flatMap(([name, value]) => (Array.isArray(value) ? value : [value]).map((v) => `${name}: ${v}`));
    const id = randomUUID();
    this.enqueue({
      spec: { id, kind: 'upgrade', method: req.method ?? 'GET', path, headers },
      tunnel: (machine, machineHead) => {
        machine.write('HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: calli-relay\r\n\r\n');
        if (head.length) machine.write(head);
        if (machineHead.length) socket.write(machineHead);
        machine.pipe(socket);
        socket.pipe(machine);
        machine.on('error', () => socket.destroy());
        socket.on('error', () => machine.destroy());
        machine.on('close', () => socket.destroy());
        socket.on('close', () => machine.destroy());
      },
      fail: () => socket.destroy(),
    });
    socket.on('close', () => this.drop(id, new HttpError(499, 'cancelled')));
    return true;
  }

  /** Forgets the machine's socket; work waiting for it fails now instead of at its timeout. */
  stop(): void {
    this.ws?.terminate();
    this.detach(this.ws);
  }

  private detach(ws: WebSocket | null): void {
    if (ws !== this.ws) return;
    this.ws = null;
    if (this.ping) clearInterval(this.ping);
    this.ping = null;
    if (this.status === 'online') {
      this.status = 'offline';
      this.code = 'relay_lost';
      this.error = 'the machine closed its connection';
    }
    for (const [id, task] of this.tasks) if (!task.claimed) this.drop(id, new HttpError(502, 'this machine is not connected', { code: 'host_offline' }));
  }

  private enqueue(task: Omit<Task, 'claimed' | 'timer'>): void {
    if (this.status !== 'online') return task.fail(new HttpError(502, 'this machine is not connected', { code: 'host_offline', reason: this.code }));
    const id = task.spec.id;
    this.tasks.set(id, {
      ...task,
      claimed: false,
      timer: setTimeout(() => this.drop(id, new HttpError(504, 'the machine did not pick up the request', { code: 'unreachable' })), CLAIM_TIMEOUT_MS),
    });
    this.wake();
  }

  private drop(id: string, err: HttpError): void {
    const task = this.tasks.get(id);
    if (!task) return;
    clearTimeout(task.timer);
    this.tasks.delete(id);
    if (task.body && !Buffer.isBuffer(task.body)) task.body.resume();
    task.fail(err);
  }

  /** Only a nudge: what to do is fetched by the machine, so a lost wakeup costs a delay, not a request. */
  private wake(): void {
    if (this.ws?.readyState === 1) this.ws.send('{"type":"wakeup"}');
  }
}

/**
 * The relay's HTTP side on the hub. Every route here is the machine's, authorized by its own join
 * token, never the hub's: a machine can claim and answer its own work, and do nothing else.
 */
export class RelayServer {
  private readonly wss = new WebSocketServer({ noServer: true, perMessageDeflate: false, maxPayload: 64 * 1024 });

  constructor(private readonly links: () => Iterable<RelayLink>) {}

  /** The machine a bearer token belongs to. */
  private machine(req: IncomingMessage): RelayLink | null {
    const auth = req.headers.authorization ?? '';
    const token = auth.startsWith('Bearer ') ? auth.slice(7) : '';
    const dot = token.indexOf('.');
    if (dot <= 0) return null;
    const id = token.slice(0, dot);
    for (const link of this.links()) if (link.id === id && link.owns(token.slice(dot + 1))) return link;
    return null;
  }

  /** `/api/relay/...` over HTTP: poll, body and result. */
  handle(req: IncomingMessage, res: ServerResponse, path: string): void {
    const link = this.machine(req);
    if (!link) throw new HttpError(401, 'unauthorized');
    const method = req.method ?? 'GET';
    if (method === 'POST' && path === '/api/relay/poll') {
      req.resume();
      const body = JSON.stringify({ tasks: link.claim() });
      res.writeHead(200, { 'Content-Type': 'application/json' }).end(body);
      return;
    }
    const m = /^\/api\/relay\/tasks\/([0-9a-f-]{36})\/(body|result)$/.exec(path);
    if (m && method === 'GET' && m[2] === 'body') return link.body(m[1], res);
    if (m && method === 'POST' && m[2] === 'result') return link.result(m[1], req, res);
    throw new HttpError(404, 'not found');
  }

  /** `/api/relay/...` upgrades: the machine's control socket, and its tunnels. False when not ours. */
  upgrade(req: IncomingMessage, socket: Duplex, head: Buffer): boolean {
    const path = new URL(req.url ?? '/', 'http://localhost').pathname;
    if (!path.startsWith('/api/relay/')) return false;
    const link = this.machine(req);
    if (!link) {
      socket.end('HTTP/1.1 401 Unauthorized\r\nConnection: close\r\nContent-Length: 0\r\n\r\n');
      return true;
    }
    if (path === '/api/relay/connect') {
      this.wss.handleUpgrade(req, socket, head, (ws) => link.attach(ws));
      return true;
    }
    const m = /^\/api\/relay\/tasks\/([0-9a-f-]{36})\/tunnel$/.exec(path);
    if (!m || String(req.headers.upgrade).toLowerCase() !== 'calli-relay' || !link.tunnel(m[1], socket, head)) {
      socket.end('HTTP/1.1 404 Not Found\r\nConnection: close\r\nContent-Length: 0\r\n\r\n');
    }
    return true;
  }
}
