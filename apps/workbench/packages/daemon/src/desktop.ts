import { createHmac, randomBytes, timingSafeEqual } from 'node:crypto';
import { readFileSync } from 'node:fs';
import type { IncomingMessage } from 'node:http';
import { connect, type Socket } from 'node:net';
import type { Duplex } from 'node:stream';
import { WebSocketServer, type WebSocket } from 'ws';

/**
 * The machine's Linux desktop, as a VNC server already shows it. The daemon starts no desktop and
 * sets no DISPLAY for agents: it finds a VNC server on this machine and passes a page's WebSocket
 * through to it, the job websockify does for noVNC. Like the terminal, it is a tool for the person.
 */

export interface DesktopTarget {
  host: string;
  port: number;
}

/** Where VNC servers listen by convention: 5900 + display number. Displays :0 to :9 are looked at. */
const DEFAULT_PORTS = Array.from({ length: 10 }, (_, i) => 5900 + i);
/** A status check and the connection right after it should not probe twice. */
const CACHE_MS = 5_000;
/** Above this much unsent output, the VNC side waits for the page to catch up. */
const HIGH_WATER = 4 * 1024 * 1024;

/** How long a desktop ticket opens a connection. It is used at once, by the page that asked. */
export const DESKTOP_TICKET_S = 60;

/** `CALLI_DESKTOP_VNC`: unset looks on this machine, `off` turns the desktop off, `host:port` names the server. */
export function parseDesktopSetting(value: string | undefined): DesktopTarget[] | null {
  const v = value?.trim();
  if (!v) return DEFAULT_PORTS.map((port) => ({ host: '127.0.0.1', port }));
  if (v === 'off') return null;
  const m = /^(?:\[([^\]]+)\]|([^:]+)):(\d{1,5})$/.exec(v);
  if (!m || Number(m[3]) > 65535) throw new Error(`CALLI_DESKTOP_VNC must be host:port or off, not ${v}`);
  return [{ host: m[1] ?? m[2], port: Number(m[3]) }];
}

const LOOPBACK = new Set(['127.0.0.1', 'localhost', '::1']);

/**
 * The TCP ports something listens on here, read from /proc; null where there is no /proc. The VNC
 * server is never connected to just to look: TigerVNC counts a connection that leaves before
 * signing in as a failed attempt, and after five it turns every client from that address away.
 */
export function listeningPorts(files = ['/proc/net/tcp', '/proc/net/tcp6']): Set<number> | null {
  const ports = new Set<number>();
  let read = false;
  for (const file of files) {
    let text: string;
    try {
      text = readFileSync(file, 'utf8');
    } catch {
      continue;
    }
    read = true;
    for (const line of text.split('\n').slice(1)) {
      const [, local, , state] = line.trim().split(/\s+/);
      // State 0A is LISTEN; the local address ends in the port, in hex.
      if (state === '0A' && local) ports.add(parseInt(local.slice(local.lastIndexOf(':') + 1), 16));
    }
  }
  return read ? ports : null;
}

function signature(secret: string, path: string, exp: number, nonce: string): string {
  return createHmac('sha256', secret).update(`desktop\0${path}\0${exp}\0${nonce}`).digest('base64url');
}

export class Desktop {
  private readonly wss = new WebSocketServer({ noServer: true, perMessageDeflate: false });
  private readonly targets: DesktopTarget[] | null;
  private cached: { at: number; target: DesktopTarget | null } | null = null;
  /** Tickets already used, until they expire: each opens one connection. */
  private readonly spent = new Map<string, number>();
  private readonly sockets = new Set<Socket>();

  constructor(opts: { targets: DesktopTarget[] | null; now?: () => number; listening?: () => Set<number> | null }) {
    this.targets = opts.targets;
    if (opts.now) this.now = opts.now;
    if (opts.listening) this.listening = opts.listening;
  }

  private listening = () => listeningPorts();

  private now = () => Date.now();

  /**
   * The first VNC port something listens on, in the order looked at; null when there is none. A
   * server on another host, or one named where there is no /proc, is taken as given.
   */
  async find(): Promise<DesktopTarget | null> {
    if (!this.targets) return null;
    if (this.cached && this.now() - this.cached.at < CACHE_MS) return this.cached.target;
    const ports = this.listening();
    const target =
      this.targets.find((t) => !LOOPBACK.has(t.host) || (ports ? ports.has(t.port) : this.targets!.length === 1)) ?? null;
    this.cached = { at: this.now(), target };
    return target;
  }

  /** A short-lived signed query for a WebSocket to `path`: a browser's WebSocket cannot send a token. */
  ticket(secret: string, path: string): string {
    const exp = Math.floor(this.now() / 1000) + DESKTOP_TICKET_S;
    // Two tickets asked for in the same second differ: each is spent on its own.
    const nonce = randomBytes(9).toString('base64url');
    return new URLSearchParams({ exp: String(exp), n: nonce, sig: signature(secret, path, exp, nonce) }).toString();
  }

  /** Whether the query opens `path`. A ticket opens one connection and is spent. */
  checkTicket(secret: string, path: string, exp: string | null, nonce: string | null, sig: string | null): boolean {
    const now = this.now();
    for (const [used, until] of this.spent) if (until * 1000 < now) this.spent.delete(used);
    if (!exp || !nonce || !sig || !/^\d+$/.test(exp) || Number(exp) * 1000 < now || this.spent.has(sig)) return false;
    const want = Buffer.from(signature(secret, path, Number(exp), nonce));
    const got = Buffer.from(sig);
    if (want.length !== got.length || !timingSafeEqual(want, got)) return false;
    this.spent.set(sig, Number(exp));
    return true;
  }

  /** Turns the upgrade into a WebSocket and joins it to the VNC server, byte for byte both ways. */
  async accept(req: IncomingMessage, socket: Duplex, head: Buffer): Promise<void> {
    const target = await this.find();
    if (!target) return refuse(socket, 404, 'no desktop on this machine');
    const vnc = connect(target.port, target.host);
    vnc.setNoDelay(true);
    // A server gone before the WebSocket is open: the page gets its socket closed, not a crash.
    const early = () => socket.destroy();
    vnc.on('error', early);
    this.sockets.add(vnc);
    vnc.on('close', () => this.sockets.delete(vnc));
    this.wss.handleUpgrade(req, socket, head, (ws) => {
      vnc.off('error', early);
      bridge(ws, vnc);
    });
  }

  /** Ends every open connection. */
  dispose(): void {
    for (const s of this.sockets) s.destroy();
    this.sockets.clear();
    for (const client of this.wss.clients) client.terminate();
    this.wss.close();
  }
}

function bridge(ws: WebSocket, vnc: Socket): void {
  ws.binaryType = 'nodebuffer';
  const send = (chunk: Buffer) =>
    ws.send(chunk, { binary: true }, () => {
      if (vnc.isPaused() && ws.bufferedAmount < HIGH_WATER / 2) vnc.resume();
    });
  // The server's 12-byte version line goes in a message of its own: noVNC reads nothing after it
  // in the same message. A server that refuses at once (TigerVNC after too many failures) sends
  // its reason right behind the version, and the page would only see the connection close.
  let first = true;
  vnc.on('data', (chunk) => {
    if (ws.readyState !== ws.OPEN) return;
    if (first && chunk.length > 12) {
      send(chunk.subarray(0, 12));
      chunk = chunk.subarray(12);
    }
    first = false;
    send(chunk);
    if (ws.bufferedAmount > HIGH_WATER) vnc.pause();
  });
  ws.on('message', (data: Buffer) => {
    if (!vnc.write(data)) ws.pause();
  });
  vnc.on('drain', () => ws.resume());
  // Closed, not dropped: what the server said last (why it turned the page away) is sent first.
  vnc.on('close', () => ws.close());
  vnc.on('error', () => undefined);
  ws.on('close', () => vnc.destroy());
  ws.on('error', () => vnc.destroy());
}

/** Answers an upgrade that is not taken, and closes it. */
export function refuse(socket: Duplex, status: number, message: string): void {
  const reason = { 401: 'Unauthorized', 403: 'Forbidden', 404: 'Not Found', 502: 'Bad Gateway' }[status] ?? 'Error';
  socket.end(`HTTP/1.1 ${status} ${reason}\r\nConnection: close\r\nContent-Type: text/plain\r\nContent-Length: ${Buffer.byteLength(message)}\r\n\r\n${message}`);
}
