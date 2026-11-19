import { open, stat } from 'node:fs/promises';
import type { UsageSample } from '../telemetry.js';

export const TOKEN_FIELDS = ['input', 'output', 'total', 'cacheRead', 'cacheWrite', 'reasoning'] as const;
export const validCount = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n) && n >= 0;
export type NativeUsage = { sample: UsageSample; at: number };

/** Timing is estimated between reports; the measured token count is never extrapolated. */
export function timedUsage(sample: UsageSample, start: number, end = Date.now()): UsageSample {
  return { ...sample, startedAt: new Date(Math.min(start, end)).toISOString(), endedAt: new Date(end).toISOString(), timing: 'estimated' };
}

/** Subtract live reports from the final protocol total, including model breakdowns and partial fields. */
export class UsageLedger {
  private readonly sums = new Map<string, Partial<Record<typeof TOKEN_FIELDS[number], number>>>();
  add(sample: UsageSample): void {
    for (const key of ['', ...(sample.model ? [sample.model] : [])]) {
      const sum = this.sums.get(key) ?? {};
      for (const field of TOKEN_FIELDS) if (validCount(sample[field])) sum[field] = (sum[field] ?? 0) + sample[field];
      this.sums.set(key, sum);
    }
  }
  remainder(sample: UsageSample): UsageSample | null {
    const sum = this.sums.get(sample.model ?? '');
    if (!sum) return sample;
    const out = { ...sample };
    let positive = false;
    for (const field of TOKEN_FIELDS) {
      if (!validCount(sample[field])) continue;
      out[field] = Math.max(0, sample[field] - (sum[field] ?? 0));
      positive ||= out[field]! > 0;
    }
    return positive || sample.cost ? out : null;
  }
}

type Cursor = { offset: number; inode: number; partial: Buffer; dropping: boolean };
const CHUNK = 256 * 1024;
const MAX_LINE = 2 * 1024 * 1024;

/** Read only appended JSONL bytes while one hub turn is active. Errors never invalidate old samples. */
export class LiveUsage {
  private readonly cursors = new Map<string, Cursor>();
  private readonly seen = new Map<string, UsageSample>();
  private readonly previousAt = new Map<string, number>();
  private timer?: NodeJS.Timeout;
  private pending: Promise<void> | null = null;
  private stopped = false;
  private sequence = 0;
  readonly ledger = new UsageLedger();
  readonly startedAt = Date.now();

  constructor(
    private readonly paths: () => Promise<string[]>,
    private readonly parse: (line: string) => NativeUsage | null,
    private readonly emit: (sample: UsageSample) => void,
  ) {}

  async start(): Promise<void> {
    // Establish the baseline BEFORE sending the prompt, including existing subagents.
    for (const path of await this.paths().catch(() => [])) {
      try {
        const info = await stat(path);
        if (info.isFile()) this.cursors.set(path, { offset: info.size, inode: info.ino, partial: Buffer.alloc(0), dropping: false });
      } catch { /* A missing/new file will be discovered on the next tick. */ }
    }
    if (!this.stopped) this.timer = setInterval(() => { void this.poll(); }, 2000);
    this.timer?.unref();
  }

  poll(): Promise<void> {
    if (this.pending) return this.pending;
    this.pending = this.read().catch(() => undefined).finally(() => { this.pending = null; });
    return this.pending;
  }

  async stop(): Promise<void> {
    this.stopped = true;
    clearInterval(this.timer);
    await this.pending;
    await this.read(true).catch(() => undefined);
  }

  private async read(final = false): Promise<void> {
    for (const path of await this.paths().catch(() => [])) {
      let file;
      try {
        file = await open(path, 'r');
        const info = await file.stat();
        if (!info.isFile()) continue;
        let cursor = this.cursors.get(path);
        if (!cursor || cursor.inode !== info.ino || info.size < cursor.offset) {
          cursor = { offset: 0, inode: info.ino, partial: Buffer.alloc(0), dropping: false };
          this.cursors.set(path, cursor);
        }
        // Bound each tick so large tool output cannot monopolize the event loop.
        const end = final ? info.size : Math.min(info.size, cursor.offset + 4 * 1024 * 1024);
        while (cursor.offset < end) {
          const buffer = Buffer.allocUnsafe(Math.min(CHUNK, end - cursor.offset));
          const { bytesRead } = await file.read(buffer, 0, buffer.length, cursor.offset);
          if (!bytesRead) break;
          cursor.offset += bytesRead;
          const bytes = Buffer.concat([cursor.partial, buffer.subarray(0, bytesRead)]);
          let start = 0, next: number;
          while ((next = bytes.indexOf(10, start)) >= 0) {
            if (!cursor.dropping && next - start <= MAX_LINE) this.line(bytes.subarray(start, next).toString('utf8'), path);
            cursor.dropping = false;
            start = next + 1;
          }
          cursor.partial = bytes.subarray(start);
          if (cursor.dropping || cursor.partial.length > MAX_LINE) { cursor.partial = Buffer.alloc(0); cursor.dropping = true; }
        }
      } catch { /* Retry later; never clear measured usage on a read/parse failure. */ }
      finally { await file?.close().catch(() => undefined); }
    }
  }

  private line(line: string, path: string): void {
    let record;
    try { record = this.parse(line); } catch { return; }
    if (!record || !Number.isFinite(record.at) || record.at < Math.floor(this.startedAt / 1000) * 1000) return;
    const { sample } = record;
    const prior = this.seen.get(sample.id);
    const delta = { ...sample };
    let positive = false;
    for (const key of TOKEN_FIELDS) {
      if (!validCount(sample[key])) { delete delta[key]; continue; }
      delta[key] = Math.max(0, sample[key] - (prior?.[key] ?? 0));
      positive ||= delta[key]! > 0;
    }
    // Repeated Claude content blocks carry the same request usage; later revisions may add output.
    const merged = { ...prior, ...sample };
    for (const key of TOKEN_FIELDS) if (validCount(prior?.[key])) merged[key] = Math.max(prior![key]!, sample[key] ?? 0);
    this.seen.set(sample.id, merged);
    if (!positive) return;
    const end = Math.max(this.startedAt, Math.min(Date.now(), record.at));
    const start = Math.min(end, this.previousAt.get(path) ?? this.startedAt);
    this.previousAt.set(path, end);
    const report = timedUsage({ ...delta, id: `${sample.id}:${++this.sequence}` }, start, end);
    this.ledger.add(report);
    this.emit(report);
  }
}
