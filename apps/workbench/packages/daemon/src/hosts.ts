import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import type { IncomingMessage, ServerResponse } from 'node:http';
import type { Duplex } from 'node:stream';
import { HttpError } from './errors.js';
import type { RemoteRun } from './remote-files.js';
import { newJoinToken, RelayLink, RelayServer, relayForwardable as forwardable, type RelayFailure } from './relay.js';

/** A machine that connects out to the hub; agents, threads and files stay on that machine. */
export interface HostEntry {
  id: string;
  label: string;
  /** Only the join secret's hash is persisted. The token is returned once when adding a machine. */
  tokenHash: string;
}

export type HostStatus = 'connecting' | 'online' | 'offline';
export type HostFailure = RelayFailure | 'unreachable' | 'host_offline' | 'too_large';

export interface HostInfo {
  id: string;
  label: string;
  /** The name reported by the node, or its label before it has connected. */
  target: string;
  status: HostStatus;
  code?: HostFailure;
  error?: string;
  version?: string;
}

export interface HostRegistryOptions {
  /** `hosts.json`, or null for an in-memory registry. */
  file: string | null;
}

/** Registered machines and the relay connections they initiate. */
export class HostRegistry {
  private readonly entries = new Map<string, HostEntry>();
  private readonly relays = new Map<string, RelayLink>();
  readonly relay = new RelayServer(() => this.relays.values());
  private readonly file: string | null;

  constructor(opts: HostRegistryOptions) {
    this.file = opts.file;
    if (this.file && existsSync(this.file)) {
      try {
        const parsed = JSON.parse(readFileSync(this.file, 'utf8'));
        let legacy = false;
        for (const h of Array.isArray(parsed.hosts) ? parsed.hosts : []) {
          if (h && typeof h.id === 'string' && typeof h.tokenHash === 'string' && (!h.transport || h.transport === 'relay')) {
            const entry: HostEntry = { id: h.id, label: typeof h.label === 'string' && h.label ? h.label : h.id, tokenHash: h.tokenHash };
            this.entries.set(h.id, entry);
            this.relays.set(h.id, new RelayLink(h.id, h.tokenHash));
          } else if (h && (h.transport === 'ssh' || typeof h.target === 'string' || typeof h.socket === 'string')) {
            legacy = true;
          }
        }
        if (legacy) console.warn('warning: SSH machines are no longer supported; add them again in Machines and configure each node to connect to this hub. hosts.json has not been changed.');
      } catch {
        console.warn('warning: hosts.json is not valid JSON; no other machines are connected');
      }
    }
  }

  list(): HostInfo[] {
    return [...this.entries.values()].map((entry) => this.info(entry));
  }

  /** Creates a join token without contacting the node. */
  add(input: { label?: unknown }): HostInfo & { token: string } {
    const label = typeof input?.label === 'string' ? input.label.trim().slice(0, 60) : '';
    if (!label) throw new HttpError(400, 'label is required');
    const id = this.freeId(label);
    const { token, hash } = newJoinToken(id);
    const entry: HostEntry = { id, label, tokenHash: hash };
    this.entries.set(id, entry);
    this.relays.set(id, new RelayLink(id, hash));
    this.save();
    return { ...this.info(entry), token };
  }

  remove(id: string): void {
    if (!this.entries.delete(id)) throw new HttpError(404, 'machine not found');
    this.relays.get(id)?.stop();
    this.relays.delete(id);
    this.save();
  }

  /** The node owns reconnection; a manual refresh only reads its current state. */
  async reconnect(id: string): Promise<HostInfo> {
    const entry = this.entries.get(id);
    if (!entry) throw new HttpError(404, 'machine not found');
    return this.info(entry);
  }

  /** Streams request and response bodies, including event streams, through the node. */
  proxy(id: string, path: string, req: IncomingMessage, res: ServerResponse): Promise<void> {
    const relay = this.relays.get(id);
    if (!relay) throw new HttpError(404, 'machine not found');
    return this.proxyRelay(relay, path, req, res);
  }

  /** Runs a collaboration or operator command on the node; file content travels on stdin. */
  exec(id: string, command: string, opts: { input?: string; timeoutMs?: number; maxBytes?: number } = {}): Promise<RemoteRun> {
    const relay = this.relays.get(id);
    if (!relay) return Promise.reject(new HttpError(404, 'machine not found'));
    return relay.exec(command, opts);
  }

  /** Requests JSON from the node on behalf of this daemon, including collaboration. */
  call<T = unknown>(id: string, method: string, path: string, body?: unknown): Promise<T> {
    const relay = this.relays.get(id);
    if (!relay) return Promise.reject(new HttpError(404, 'machine not found'));
    return this.callRelay<T>(relay, method, path, body);
  }

  /** The caller checks the desktop ticket before forwarding the WebSocket handshake. */
  upgrade(id: string, path: string, req: IncomingMessage, socket: Duplex, head: Buffer): boolean {
    return this.relays.get(id)?.upgrade(path, req, socket, head) ?? false;
  }

  private async proxyRelay(relay: RelayLink, path: string, req: IncomingMessage, res: ServerResponse): Promise<void> {
    const length = req.headers['content-length'];
    const hasBody = length !== undefined ? Number(length) > 0 : Boolean(req.headers['transfer-encoding']);
    const abort = new AbortController();
    const gone = () => abort.abort();
    res.on('close', gone);
    let up;
    try {
      up = await relay.request({ method: req.method ?? 'GET', path, headers: forwardable(req.headers, ['authorization', 'host', 'content-length']) }, hasBody ? req : null, abort.signal);
    } catch (err) {
      // Ended from this side: nothing is wrong with the machine.
      if (abort.signal.aborted) return;
      throw err;
    } finally {
      res.off('close', gone);
    }
    const body = up.body;
    res.writeHead(up.status, forwardable(up.headers));
    body.pipe(res);
    await new Promise<void>((resolve) => {
      res.on('close', () => {
        body.destroy();
        resolve();
      });
      body.on('error', () => res.destroy());
    });
  }

  private async callRelay<T>(relay: RelayLink, method: string, path: string, body?: unknown): Promise<T> {
    const payload = body === undefined ? null : Buffer.from(JSON.stringify(body));
    const up = await relay.request({ method, path, headers: payload ? { 'content-type': 'application/json' } : {} }, payload);
    const chunks: Buffer[] = [];
    for await (const c of up.body) chunks.push(c as Buffer);
    const text = Buffer.concat(chunks).toString('utf8');
    let value: any;
    try { value = text ? JSON.parse(text) : undefined; } catch { value = undefined; }
    if (up.status >= 400) throw new HttpError(up.status, value?.error ?? `HTTP ${up.status}`);
    return value as T;
  }

  dispose(): void {
    for (const relay of this.relays.values()) relay.stop();
  }

  private info(entry: HostEntry): HostInfo {
    const relay = this.relays.get(entry.id);
    return {
      id: entry.id,
      label: entry.label,
      target: relay?.hostname ?? entry.label,
      status: relay?.status ?? 'offline',
      ...(relay?.code ? { code: relay.code } : {}),
      ...(relay?.error ? { error: relay.error } : {}),
      ...(relay?.version ? { version: relay.version } : {}),
    };
  }

  private freeId(name: string): string {
    const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40) || 'host';
    let id = slug;
    for (let n = 2; this.entries.has(id); n++) id = `${slug}-${n}`;
    return id;
  }

  private save(): void {
    if (!this.file) return;
    writeFileSync(this.file, `${JSON.stringify({ hosts: [...this.entries.values()] }, null, 2)}\n`, { mode: 0o600 });
  }
}
