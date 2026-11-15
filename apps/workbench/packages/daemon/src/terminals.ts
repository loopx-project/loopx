import { randomUUID } from 'node:crypto';
import { existsSync, statSync } from 'node:fs';
import { resolve } from 'node:path';
import { spawn, type IPty } from 'node-pty';
import { HttpError } from './errors.js';
import { isWithinWorkdirs } from './workdirs.js';

/** What the API shows of a terminal. Exited ones disappear from the list but stay addressable briefly. */
export interface TerminalSnapshot {
  id: string;
  cwd: string;
  cols: number;
  rows: number;
  startedAt: number;
  lastActivity: number;
}

/** SSE frames of a terminal stream. Output is base64: programs may emit bytes that are not UTF-8. */
export type TerminalEvent =
  | { type: 'backlog'; data: string }
  | { type: 'out'; data: string }
  | { type: 'exit'; code: number };

type Listener = (ev: TerminalEvent) => void;

const MAX_INPUT = 128 * 1024;

class TerminalSession {
  readonly id = randomUUID();
  readonly startedAt = Date.now();
  lastActivity = this.startedAt;
  cols: number;
  rows: number;
  /** Set when the shell exits; the session lingers so late subscribers still get its tail. */
  exitCode: number | null = null;
  exitedAt: number | null = null;

  private readonly listeners = new Set<Listener>();
  private readonly backlog: Buffer[] = [];
  private backlogSize = 0;
  /** Stream-mode decoding holds incomplete UTF-8 tails back, so a split character survives the chunks. */
  private pending = new TextDecoder();
  private pty: IPty;

  constructor(
    readonly cwd: string,
    cols: number,
    rows: number,
    private readonly shell: string,
    private readonly maxBacklog: number,
    private readonly onExit: (s: TerminalSession) => void,
  ) {
    this.cols = cols;
    this.rows = rows;
    let pty: IPty;
    try {
      pty = spawn(shell, [], {
        name: 'xterm-256color',
        cols,
        rows,
        cwd,
        // The daemon is started from a login shell (see scripts/install-service.sh), so this
        // carries the agent credentials too: the terminal is as powerful as the person using it.
        env: { ...process.env, TERM: 'xterm-256color', COLORTERM: 'truecolor' } as Record<string, string>,
      });
    } catch (err) {
      throw new HttpError(500, `could not start the shell (${shell}): ${err instanceof Error ? err.message : err}`);
    }
    this.pty = pty;
    this.pty.onData((data) => this.onOutput(data));
    this.pty.onExit(({ exitCode }) => this.finish(exitCode));
  }

  private touch(): void {
    this.lastActivity = Date.now();
  }

  private onOutput(data: string): void {
    this.touch();
    const text = this.pending.decode(Buffer.from(data, 'utf8'), { stream: true });
    if (!text) return;
    const buf = Buffer.from(text, 'utf8');
    this.backlog.push(buf);
    this.backlogSize += buf.length;
    while (this.backlogSize > this.maxBacklog && this.backlog.length > 1) {
      this.backlogSize -= this.backlog.shift()!.length;
    }
    this.emit({ type: 'out', data: buf.toString('base64') });
  }

  private finish(code: number): void {
    // Whatever the decoder still held was a complete character after all.
    const rest = this.pending.decode();
    if (rest) this.emit({ type: 'out', data: Buffer.from(rest, 'utf8').toString('base64') });
    this.pending = new TextDecoder();
    this.exitCode = code;
    this.exitedAt = Date.now();
    this.emit({ type: 'exit', code });
    this.listeners.clear();
    this.onExit(this);
  }

  private emit(ev: TerminalEvent): void {
    for (const l of [...this.listeners]) l(ev);
  }

  /** Replays the backlog, then forwards live frames. For an exited session both arrive at once. */
  subscribe(listener: Listener): () => void {
    if (this.exitCode !== null) {
      listener({ type: 'backlog', data: this.snapshot().toString('base64') });
      listener({ type: 'exit', code: this.exitCode });
      return () => undefined;
    }
    listener({ type: 'backlog', data: this.snapshot().toString('base64') });
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private snapshot(): Buffer {
    return this.backlog.length ? Buffer.concat(this.backlog) : Buffer.alloc(0);
  }

  write(data: string): void {
    if (this.exitCode !== null) throw new HttpError(409, 'terminal has exited');
    this.touch();
    this.pty.write(data);
  }

  resize(cols: number, rows: number): void {
    if (this.exitCode !== null) throw new HttpError(409, 'terminal has exited');
    this.cols = cols;
    this.rows = rows;
    this.pty.resize(cols, rows);
  }

  kill(): void {
    if (this.exitCode === null) this.pty.kill();
  }
}

export interface TerminalManagerOptions {
  /** Terminal start directories are held to the same whitelist as threads. */
  /** Where shells may start; a function when the list can change while the hub runs. */
  workdirs: string[] | (() => string[]);
  /** Shell executable. Defaults to $SHELL, then /bin/bash; CALLI_TERM_SHELL reaches here. */
  shell?: string;
  maxTerminals?: number;
  /** A terminal with no input and no output for this long is killed. */
  idleMs?: number;
  /** Exited sessions stay addressable this long, so a reconnecting page can still show the tail. */
  exitedGraceMs?: number;
  backlogBytes?: number;
  /** Test hook: how often idle and exited sessions are swept. */
  sweepMs?: number;
}

/**
 * Web terminals: a real PTY each, alive only in memory. Nothing is persisted — a daemon restart
 * ends every terminal, by design. Output travels as SSE with base64 frames, input as POSTs.
 */
export class TerminalManager {
  private readonly sessions = new Map<string, TerminalSession>();
  private readonly sweeper: NodeJS.Timeout;

  constructor(private readonly opts: TerminalManagerOptions) {
    this.sweeper = setInterval(() => this.sweep(), opts.sweepMs ?? 60_000);
    this.sweeper.unref();
  }

  create(cwd: unknown): TerminalSnapshot {
    if (typeof cwd !== 'string' || cwd === '') throw new HttpError(400, 'cwd is required');
    const dir = resolve(cwd);
    const allowed = typeof this.opts.workdirs === 'function' ? this.opts.workdirs() : this.opts.workdirs;
    if (!isWithinWorkdirs(dir, allowed)) throw new HttpError(400, 'cwd is not in the allowed workdirs');
    if (!existsSync(dir) || !statSync(dir).isDirectory()) throw new HttpError(400, 'cwd does not exist');
    if (this.alive().length >= (this.opts.maxTerminals ?? 3)) {
      throw new HttpError(409, `at most ${this.opts.maxTerminals ?? 3} terminals at once; close one first`);
    }
    const s = new TerminalSession(
      dir,
      80,
      24,
      this.opts.shell ?? process.env.SHELL ?? '/bin/bash',
      this.opts.backlogBytes ?? 256 * 1024,
      () => undefined,
    );
    this.sessions.set(s.id, s);
    return this.view(s);
  }

  list(): TerminalSnapshot[] {
    return this.alive()
      .sort((a, b) => a.startedAt - b.startedAt)
      .map((s) => this.view(s));
  }

  input(id: string, data: unknown): void {
    if (typeof data !== 'string') throw new HttpError(400, 'data must be a string');
    if (data.length > MAX_INPUT) throw new HttpError(413, 'input too large');
    this.get(id).write(data);
  }

  resize(id: string, cols: unknown, rows: unknown): void {
    if (!Number.isInteger(cols) || !Number.isInteger(rows) || (cols as number) < 2 || (cols as number) > 500 || (rows as number) < 2 || (rows as number) > 300) {
      throw new HttpError(400, 'cols and rows must be integers, cols 2-500 and rows 2-300');
    }
    this.get(id).resize(cols as number, rows as number);
  }

  kill(id: string): void {
    this.get(id).kill();
  }

  /** Throws 404 for an unknown id; an exited-but-remembered session still resolves. */
  get(id: string): TerminalSession {
    const s = this.sessions.get(id);
    if (!s) throw new HttpError(404, `unknown terminal: ${id}`);
    return s;
  }

  subscribe(id: string, listener: Listener): () => void {
    return this.get(id).subscribe(listener);
  }

  private alive(): TerminalSession[] {
    return [...this.sessions.values()].filter((s) => s.exitCode === null);
  }

  private view(s: TerminalSession): TerminalSnapshot {
    return { id: s.id, cwd: s.cwd, cols: s.cols, rows: s.rows, startedAt: s.startedAt, lastActivity: s.lastActivity };
  }

  private sweep(): void {
    const now = Date.now();
    for (const s of this.sessions.values()) {
      if (s.exitCode === null && now - s.lastActivity >= (this.opts.idleMs ?? 30 * 60_000)) {
        s.kill(); // the exit event reaches subscribers through the normal path
      } else if (s.exitCode !== null && now - (s.exitedAt ?? now) >= (this.opts.exitedGraceMs ?? 60_000)) {
        this.sessions.delete(s.id);
      }
    }
  }

  dispose(): void {
    clearInterval(this.sweeper);
    for (const s of this.sessions.values()) s.kill();
    this.sessions.clear();
  }
}
