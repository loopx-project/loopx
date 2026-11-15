import { FileHandle, open, stat } from 'node:fs/promises';
import type { DatabaseSync } from 'node:sqlite';
import {
  claudeConfigDirs, claudeTranscriptFiles, codexHomes, codexTranscriptFiles,
  parseClaudeUsageLine, parseCodexModel, parseCodexTokenCount, parseCodexTotal,
  transcriptSession,
} from './adapters/transcript-usage.js';
import { HttpError } from './errors.js';

/**
 * This machine's token usage, metered off the agents' own transcript files (persona,
 * docs/server/machines.md 「Token usage」): replies summed per minute, model and launcher — the
 * hub instance that ran the session, or none for one typed in a terminal. A reply is counted once
 * in the window even when it appears in several files (a fork copies the history it starts from).
 *
 * The window is 26 hours ("today" in any time zone, and the hour before). Minute rows are kept
 * 26 hours, then summed into 10-minute rows; everything goes after 7 days. A restart re-reads the
 * window from the files and replaces its rows, so nothing the files still show is double-counted.
 *
 * This store is independent of `telemetry_samples` (what agents report about the hub's own turns):
 * the two count different things and never meet; the machine page reads only this one.
 */

/** Persona's window: the newest hour twice over, so every "today" fits. */
const WINDOW_MS = 26 * 60 * 60 * 1000;
/** Per file and scan — the observability polls' own cap; a backlog continues on the next scan,
 *  and every read is async, so the hub's event loop is never held over a transcript. */
const MAX_FILE_ROUND = 4 << 20;
/** Longer lines (pictures) are skipped: usage lines are small. */
const MAX_LINE = 16 << 20;
/** Files at most this big are read whole on first sight; bigger ones start at the window. */
const SMALL_FILE = 4 << 20;

export type UsageBucket = { input: number; cacheWrite: number; cacheRead: number; output: number; replies: number };

interface FileState {
  session: string;
  /** File position after the last complete line read; a partial tail is read again next scan. */
  offset: number;
  /** Claude: every content block of a reply repeats its usage; only the first is a new reply. */
  lastId: string;
  /** Codex: the model of the latest turn_context. */
  model: string;
  /** Codex: the session's running total, to skip repeated token_count events. */
  lastTotal: number;
}

interface BucketKey {
  /** The minute's start, epoch seconds. */
  minute: number;
  model: string;
  launcher: string;
}

export interface MachineUsagePoint extends UsageBucket {
  /** The step's start, epoch seconds. */
  t: number;
  /** No usage and the daemon was not running: drawn as a break, not as zero. */
  gap: boolean;
}

export interface MachineUsageShare extends UsageBucket {
  name: string;
}

export interface MachineUsageReport {
  hours: 1 | 24 | 168;
  step: 60 | 600 | 3600;
  total: UsageBucket;
  points: MachineUsagePoint[];
  models: MachineUsageShare[];
  /** The empty name is the "other" launcher: a session the hub did not start. */
  launchers: MachineUsageShare[];
}

export interface MachineUsageDeps {
  db: DatabaseSync;
  /** Claude config directories and Codex homes to read, refreshed before every scan. */
  sources: () => { claudeDirs: string[]; codexHomes: string[] };
  /** Every agent-side session the hub has a thread for: sessionId → instance id. Read once per
   *  scan; the empty answer for a session names the "other" launcher (started in a terminal). */
  launchers: () => Map<string, string>;
  /** Overridable for tests. */
  now?: () => number;
}

export class MachineUsageMeter {
  private readonly files = new Map<string, FileState>();
  private readonly buckets = new Map<string, { key: BucketKey; usage: UsageBucket }>();
  private readonly counted = new Map<string, number>();
  private readonly db: DatabaseSync;
  private readonly deps: MachineUsageDeps;
  private owners = new Map<string, string>();
  private timer: ReturnType<typeof setInterval> | null = null;
  private scanning: Promise<void> | null = null;
  private full = false;

  constructor(deps: MachineUsageDeps) {
    this.deps = deps;
    this.db = deps.db;
    deps.db.exec(`
      CREATE TABLE IF NOT EXISTS machine_usage (
        minute INTEGER NOT NULL, step INTEGER NOT NULL, model TEXT NOT NULL, launcher TEXT NOT NULL,
        input INTEGER NOT NULL, cache_write INTEGER NOT NULL, cache_read INTEGER NOT NULL,
        output INTEGER NOT NULL, replies INTEGER NOT NULL,
        PRIMARY KEY(minute, step, model, launcher)
      );
      CREATE TABLE IF NOT EXISTS machine_uptime (slot INTEGER PRIMARY KEY);
    `);
  }

  /** Scans now and then on a timer; `stop` ends it. */
  start(everyMs = 60_000): void {
    const run = () => void this.scan().catch((error) =>
      console.warn(`warning: machine usage scan failed: ${error instanceof Error ? error.message : error}`));
    run();
    this.timer = setInterval(run, everyMs);
    this.timer.unref?.();
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  /** One pass over what the transcripts gained. Concurrent calls share the one pass. */
  scan(): Promise<void> {
    this.scanning ??= this.doScan().finally(() => { this.scanning = null; });
    return this.scanning;
  }

  private async doScan(): Promise<void> {
    const now = this.now();
    const cutoff = now - WINDOW_MS;
    // The daemon being up is what tells an idle minute from an offline one.
    this.db.prepare('INSERT OR IGNORE INTO machine_uptime(slot) VALUES (?)').run(Math.floor(now / 600_000) * 600);

    const { claudeDirs, codexHomes } = this.deps.sources();
    this.owners = this.deps.launchers();
    for (const path of await claudeTranscriptFiles(claudeDirs, cutoff)) await this.scanClaude(path, cutoff);
    for (const path of await codexTranscriptFiles(codexHomes, cutoff)) await this.scanCodex(path, cutoff);

    await this.prune(cutoff);
    this.persist();
    this.downsample(now);
  }

  private persist(): void {
    // A savepoint keeps the window's replace atomic with its inserts (Store's own pattern).
    this.db.exec('SAVEPOINT machine_usage_write');
    try {
      if (!this.full) {
        // The first pass after a start replaces the window: what the files say now is the record.
        this.db.prepare('DELETE FROM machine_usage WHERE step = 60 AND minute >= ?')
          .run(Math.floor((this.now() - WINDOW_MS) / 60_000) * 60);
        this.full = true;
      }
      const upsert = this.db.prepare(`INSERT INTO machine_usage(minute, step, model, launcher, input, cache_write, cache_read, output, replies)
        VALUES (?, 60, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(minute, step, model, launcher) DO UPDATE SET
          input = excluded.input, cache_write = excluded.cache_write, cache_read = excluded.cache_read,
          output = excluded.output, replies = excluded.replies`);
      for (const { key, usage } of this.buckets.values()) {
        upsert.run(key.minute, key.model, key.launcher, usage.input, usage.cacheWrite, usage.cacheRead, usage.output, usage.replies);
      }
      this.db.exec('RELEASE machine_usage_write');
    } catch (error) {
      this.db.exec('ROLLBACK TO machine_usage_write');
      this.db.exec('RELEASE machine_usage_write');
      throw error;
    }
  }

  /** Minute rows that left the window are summed into 10-minute rows, everything past a week goes. */
  private downsample(now: number): void {
    const cutoffMinute = Math.floor((now - WINDOW_MS) / 60_000) * 60;
    const weekAgo = Math.floor((now - (7 * 24 + 1) * 3600_000) / 1000);
    this.db.exec(`
      INSERT INTO machine_usage(minute, step, model, launcher, input, cache_write, cache_read, output, replies)
        SELECT (minute / 600) * 600, 600, model, launcher,
          SUM(input), SUM(cache_write), SUM(cache_read), SUM(output), SUM(replies)
        FROM machine_usage WHERE step = 60 AND minute < ${cutoffMinute}
        GROUP BY (minute / 600) * 600, model, launcher
        ON CONFLICT(minute, step, model, launcher) DO UPDATE SET
          input = machine_usage.input + excluded.input,
          cache_write = machine_usage.cache_write + excluded.cache_write,
          cache_read = machine_usage.cache_read + excluded.cache_read,
          output = machine_usage.output + excluded.output,
          replies = machine_usage.replies + excluded.replies;
      DELETE FROM machine_usage WHERE step = 60 AND minute < ${cutoffMinute};
      DELETE FROM machine_usage WHERE minute < ${weekAgo};
      DELETE FROM machine_uptime WHERE slot < ${Math.floor((now - 7 * 24 * 3600_000) / 1000)};
    `);
  }

  private async scanClaude(path: string, cutoff: number): Promise<void> {
    const state = await this.fileState(path, cutoff);
    await readLines(path, state, (line) => {
      const usage = parseClaudeUsageLine(line);
      if (!usage) return;
      if (usage.reply) {
        if (usage.reply === state.lastId) return; // another content block of the same reply
        state.lastId = usage.reply;
      }
      if (usage.model === '<synthetic>' || usage.at < cutoff) return; // an error Claude Code wrote itself
      this.add(state.session, usage.reply, usage.at, usage.model || 'unknown', usage);
    });
  }

  private async scanCodex(path: string, cutoff: number): Promise<void> {
    const state = await this.fileState(path, cutoff);
    await readLines(path, state, (line) => {
      const model = parseCodexModel(line);
      if (model) { state.model = model; return; }
      const usage = parseCodexTokenCount(line);
      if (!usage) return;
      // Codex repeats token_count (rate limit updates carry the same totals): only a grown total is a new request.
      const total = parseCodexTotal(line) ?? 0;
      if (total <= state.lastTotal) return;
      state.lastTotal = total;
      if (usage.at < cutoff) return;
      this.add(state.session, `codex:${usage.at}:${total}`, usage.at, state.model || 'codex', usage);
    });
  }

  /** Fresh state for a first-seen file, starting a big one where the window begins. */
  private async fileState(path: string, cutoff: number): Promise<FileState> {
    let state = this.files.get(path);
    const size = await sizeOf(path);
    if (state && size < state.offset) state = undefined; // rewritten: read it again
    if (!state) {
      state = {
        session: transcriptSession(path),
        offset: size > SMALL_FILE ? await startOffset(path, size, cutoff) : 0,
        lastId: '',
        model: '',
        lastTotal: 0,
      };
      this.files.set(path, state);
    }
    return state;
  }

  /** Counts one reply; `reply` (when the agent names one) keeps a fork's copy of it out. */
  private add(
    session: string,
    reply: string,
    at: number,
    model: string,
    usage: { input: number; cacheWrite: number; cacheRead: number; output: number },
  ): void {
    const minute = Math.floor(at / 60_000) * 60;
    if (reply) {
      if (this.counted.has(reply)) return;
      this.counted.set(reply, minute);
    }
    const key = { minute, model, launcher: this.owners.get(session) ?? '' };
    const id = `${key.minute}|${key.model}|${key.launcher}`;
    const bucket = this.buckets.get(id);
    if (bucket) {
      bucket.usage.input += usage.input;
      bucket.usage.cacheWrite += usage.cacheWrite;
      bucket.usage.cacheRead += usage.cacheRead;
      bucket.usage.output += usage.output;
      bucket.usage.replies += 1;
    } else {
      this.buckets.set(id, { key, usage: { ...usage, replies: 1 } });
    }
  }

  private async prune(cutoff: number): Promise<void> {
    const minute = Math.floor(cutoff / 60_000) * 60;
    for (const [id, { key }] of this.buckets) if (key.minute < minute) this.buckets.delete(id);
    for (const [reply, at] of this.counted) if (at < minute) this.counted.delete(reply);
    for (const [path] of this.files) {
      let alive = true;
      try {
        alive = (await stat(path)).mtimeMs >= cutoff;
      } catch {
        alive = false;
      }
      if (!alive) this.files.delete(path);
    }
  }

  /** The report `GET /api/machine-usage` serves. */
  report(hours: unknown, model: unknown, launcher: unknown): MachineUsageReport {
    if (hours !== 1 && hours !== 24 && hours !== 168) throw new HttpError(400, 'hours must be 1, 24 or 168');
    const step = hours === 1 ? 60 : hours === 24 ? 600 : 3600;
    const nowSec = Math.floor(this.now() / 1000);
    const start = Math.floor((nowSec - hours * 3600) / step) * step;
    const modelFilter = typeof model === 'string' && model ? model : null;
    // An empty launcher is a session the hub did not start; a URL cannot carry that as its own
    // value, so `-` names it (persona's convention).
    const launcherFilter = launcher === undefined || launcher === null ? null : launcher === '-' || launcher === '' ? '' : String(launcher);
    const rows = this.db.prepare(`SELECT minute, model, launcher, input, cache_write, cache_read, output, replies
      FROM machine_usage WHERE minute + step > ? AND minute < ?`).all(start, nowSec + step) as unknown as UsageRow[];
    const wanted = (row: UsageRow) =>
      (modelFilter === null || row.model === modelFilter) && (launcherFilter === null || row.launcher === launcherFilter);

    const total: UsageBucket = { input: 0, cacheWrite: 0, cacheRead: 0, output: 0, replies: 0 };
    const points = new Map<number, MachineUsagePoint>();
    for (let t = start; t < nowSec + step; t += step) {
      points.set(t, { t, input: 0, cacheWrite: 0, cacheRead: 0, output: 0, replies: 0, gap: true });
    }
    for (const row of rows) {
      const bucket = Math.floor(row.minute / step) * step;
      if (bucket + step <= start || bucket >= nowSec + step || !wanted(row)) continue;
      total.input += row.input; total.cacheWrite += row.cache_write; total.cacheRead += row.cache_read;
      total.output += row.output; total.replies += row.replies;
      const point = points.get(bucket);
      if (point) {
        point.input += row.input; point.cacheWrite += row.cache_write; point.cacheRead += row.cache_read;
        point.output += row.output; point.replies += row.replies;
        point.gap = false;
      }
    }
    // A step with nothing counted is offline only when the daemon was not running through it; an
    // idle but running step stays a zero. Uptime slots are 10 minutes, so a shorter step looks at
    // the slot it sits in.
    const slots = new Set((this.db.prepare('SELECT slot FROM machine_uptime WHERE slot >= ?').all(start - 600) as Array<{ slot: number }>).map((r) => r.slot));
    for (const point of points.values()) {
      if (!point.gap) continue;
      const first = Math.floor(point.t / 600) * 600;
      const alive = Array.from({ length: Math.ceil(step / 600) + 1 }, (_, i) => first + i * 600)
        .some((slot) => slot < point.t + step && slots.has(slot));
      point.gap = !alive;
    }
    return { hours, step, total, points: [...points.values()], models: this.shares(rows, 'model'), launchers: this.shares(rows, 'launcher') };
  }

  /** What each model (or launcher) ran in the window, from the unfiltered rows: the filter menus
   *  keep every option even while a filter is on. */
  private shares(rows: UsageRow[], field: 'model' | 'launcher'): MachineUsageShare[] {
    const out = new Map<string, UsageBucket>();
    for (const row of rows) {
      const usage = out.get(row[field]) ?? { input: 0, cacheWrite: 0, cacheRead: 0, output: 0, replies: 0 };
      usage.input += row.input; usage.cacheWrite += row.cache_write; usage.cacheRead += row.cache_read;
      usage.output += row.output; usage.replies += row.replies;
      out.set(row[field], usage);
    }
    return [...out.entries()].map(([name, usage]) => ({ name, ...usage }))
      .sort((a, b) => tokensOf(b) - tokensOf(a));
  }

  private now(): number {
    return this.deps.now?.() ?? Date.now();
  }
}

interface UsageRow {
  minute: number; model: string; launcher: string; input: number;
  cache_write: number; cache_read: number; output: number; replies: number;
}

function tokensOf(usage: UsageBucket): number {
  return usage.input + usage.cacheWrite + usage.cacheRead + usage.output;
}

/**
 * Reads what a transcript gained from `state.offset` (at most MAX_READ this pass), handing every
 * complete line to `onLine` and leaving the offset just past the last complete line — a partial
 * tail is read again next scan, so a line still being written is not lost. Lines longer than
 * MAX_LINE are skipped: usage lines are small, and a picture line would be megabytes.
 */
async function readLines(path: string, state: FileState, onLine: (line: string) => void): Promise<void> {
  const size = await sizeOf(path);
  if (size <= state.offset) return;
  const end = Math.min(size, state.offset + MAX_FILE_ROUND);
  let handle: FileHandle;
  try {
    handle = await open(path, 'r');
  } catch {
    return; // gone or unreadable between listing and reading
  }
  try {
    const chunk = Buffer.alloc(1 << 16);
    let offset = state.offset; // the read cursor
    let complete = state.offset; // the position after the last complete line seen
    let pending: Buffer[] = []; // the line under the cursor so far
    let pendingBytes = 0;
    let skipping = false;
    while (offset < end) {
      const { bytesRead } = await handle.read(chunk, 0, Math.min(chunk.length, end - offset), offset);
      if (bytesRead <= 0) break;
      let at = 0;
      while (at < bytesRead) {
        const nl = chunk.indexOf(0x0a, at);
        if (nl < 0 || nl >= bytesRead) {
          // The rest of the chunk belongs to a line that continues past it.
          if (!skipping) {
            pending.push(Buffer.from(chunk.subarray(at, bytesRead)));
            pendingBytes += bytesRead - at;
            if (pendingBytes > MAX_LINE) { pending = []; skipping = true; }
          }
          offset += bytesRead - at;
          break;
        }
        if (!skipping) {
          pending.push(Buffer.from(chunk.subarray(at, nl)));
          pendingBytes += nl - at;
          if (pendingBytes > MAX_LINE) {
            pending = [];
            skipping = true;
          } else {
            onLine(Buffer.concat(pending).toString('utf8'));
            pending = [];
            pendingBytes = 0;
          }
        }
        pending = [];
        pendingBytes = 0;
        skipping = false;
        offset += nl + 1 - at;
        complete = offset;
        at = nl + 1;
      }
    }
    state.offset = complete;
  } finally {
    await handle.close();
  }
}

/** Where in a big file the window starts: lines are written in time order, so step back from the
 *  end (doubling) until a line is older than the cutoff. */
async function startOffset(path: string, size: number, cutoff: number): Promise<number> {
  try {
    const buffer = Buffer.alloc(256 << 10);
    for (let step = SMALL_FILE; ; step *= 2) {
      const pos = size - step;
      if (pos <= 0) return 0;
      const handle = await open(path, 'r');
      try {
        const { bytesRead } = await handle.read(buffer, 0, buffer.length, pos);
        const nl = buffer.subarray(0, bytesRead).indexOf('\n');
        if (nl < 0) continue; // inside a long line: step further back
        const at = firstTimestamp(buffer.subarray(nl + 1, bytesRead).toString('utf8'));
        if (at !== null && at < cutoff) return pos + nl + 1;
      } finally {
        await handle.close();
      }
    }
  } catch {
    return 0;
  }
}

/** The first `timestamp` value in the text (escaped quotes inside messages do not match). */
function firstTimestamp(text: string): number | null {
  const at = text.indexOf('"timestamp":"');
  if (at < 0) return null;
  const end = text.indexOf('"', at + 13);
  if (end < 0) return null;
  const parsed = Date.parse(text.slice(at + 13, end));
  return Number.isFinite(parsed) ? parsed : null;
}

async function sizeOf(path: string): Promise<number> {
  try {
    return (await stat(path)).size;
  } catch {
    return 0;
  }
}
