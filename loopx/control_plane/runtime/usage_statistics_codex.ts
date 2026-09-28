/** Read only timing envelopes from one explicitly bound session, in bounded chunks. */
import { open } from "node:fs/promises";
import type { FileHandle } from "node:fs/promises";
import type { GoalObservation, Host } from "./usage_statistics_goal_contract.ts";
import { object } from "./usage_statistics_contract.ts";
export type CodexCursor = { offset: number; inode: string; since: number; seen: number; skipping?: boolean; open?: { id: string; start: number; confirmed: number } };
const BUDGET = 1024 * 1024;
// The first line is not a short id record: Codex's recorder writes
// `base_instructions` and the dynamic tool list into `session_meta`, and the
// first lines observed in this project's Codex homes reach ~50 KiB. Frame the
// line by LF under its own bounded budget instead of assuming one fixed 64 KiB
// chunk holds it, which truncated the JSON and stranded the whole session.
const HEADER_CHUNK = 65536;
const HEADER_BUDGET = 2 * 1024 * 1024;
type HeaderLine = { line: string } | { error: "incomplete" | "too_large" };
function timestamp(value: unknown): number { return typeof value === "string" ? Date.parse(value) : NaN; }
/**
 * Read the opening record whole, framed by LF, up to a fixed budget.
 *
 * `incomplete` means the writer has not finished the record yet and the caller
 * should read it again later; `too_large` means the identity cannot be verified
 * at all, which is named rather than reported as a mismatch.
 */
async function readHeaderLine(file: FileHandle, size: number): Promise<HeaderLine> {
  const parts: Buffer[] = [];
  let offset = 0;
  while (offset < size && offset < HEADER_BUDGET) {
    const take = Math.min(HEADER_CHUNK, size - offset, HEADER_BUDGET - offset);
    const buffer = Buffer.alloc(take);
    const read = await file.read(buffer, 0, take, offset);
    if (read.bytesRead <= 0) break;
    const chunk = buffer.subarray(0, read.bytesRead);
    offset += read.bytesRead;
    const newline = chunk.indexOf(10);
    parts.push(newline < 0 ? chunk : chunk.subarray(0, newline));
    if (newline >= 0) return { line: Buffer.concat(parts).toString("utf8") };
  }
  return offset >= HEADER_BUDGET ? { error: "too_large" } : { error: "incomplete" };
}
export async function readCodexTiming(path: string, thread: string, previous: CodexCursor | undefined, now: number, key: string, host: Host) {
  const file = await open(path, "r");
  try {
    const stat = await file.stat();
    const inode = `${stat.dev}:${stat.ino}`;
    const header = await readHeaderLine(file, stat.size);
    if ("error" in header) {
      if (header.error === "incomplete") {
        // Nothing may be emitted before the identity is verified, and nothing is
        // lost: the record is simply not written yet, so read it again later.
        const cursor: CodexCursor = previous?.inode === inode && previous.offset <= stat.size
          ? { ...structuredClone(previous), seen: now }
          : { offset: 0, inode, since: now, seen: now };
        return { cursor, observations: [] as GoalObservation[] };
      }
      throw new Error(`usage_session_header_too_large:${stat.size}`);
    }
    let meta: unknown;
    try { meta = JSON.parse(header.line); } catch { throw new Error("usage_session_header_unparsable"); }
    const record = object(meta) ? meta : {};
    const payload = object(record.payload) ? record.payload : {};
    if (record.type !== "session_meta" || (payload.id ?? payload.session_id) !== thread) throw new Error("usage_session_identity_mismatch");
    const reusable = previous?.inode === inode && previous.offset <= stat.size;
    const cursor: CodexCursor = reusable ? structuredClone(previous!) : { offset: Math.max(0, stat.size - BUDGET), inode, since: now, seen: now };
    cursor.seen = now;
    const buffer = Buffer.alloc(Math.min(BUDGET, stat.size - cursor.offset));
    const read = await file.read(buffer, 0, buffer.length, cursor.offset);
    const bytes = buffer.subarray(0, read.bytesRead);
    const last = bytes.lastIndexOf(10);
    if (last < 0) {
      // Large content records must not permanently strand later timing events.
      if (read.bytesRead === BUDGET) { cursor.offset += read.bytesRead; cursor.skipping = true; cursor.open = undefined; }
      return { cursor, observations: [] as GoalObservation[] };
    }
    let body = bytes.subarray(0, last + 1).toString("utf8");
    if (cursor.skipping || !reusable && cursor.offset > 0) body = body.slice(body.indexOf("\n") + 1);
    delete cursor.skipping;
    cursor.offset += last + 1;
    const observations: GoalObservation[] = [];
    const emit = (start: number, end: number) => {
      start = Math.max(start, cursor.since);
      if (Number.isSafeInteger(start) && Number.isSafeInteger(end) && end > start && end <= now + 1000 && end - start <= 7 * 86400000)
        observations.push({ key, start, end, measurement: "codex_turn", host });
    };
    for (const line of body.split("\n")) {
      if (!line) continue;
      let event: unknown;
      try { event = JSON.parse(line); } catch { cursor.open = undefined; continue; }
      if (!object(event) || event.type !== "event_msg" || !object(event.payload)) continue;
      const payload = event.payload;
      const id = typeof payload.turn_id === "string" && payload.turn_id.length <= 128 ? payload.turn_id : "";
      if (payload.type === "task_started" && id) {
        const start = timestamp(payload.started_at ?? event.timestamp);
        if (Number.isFinite(start)) cursor.open = { id, start, confirmed: Math.max(start, cursor.since) };
      } else if (["task_complete", "task_completed", "turn_aborted"].includes(String(payload.type)) && id) {
        const start = timestamp(payload.started_at);
        const end = timestamp(payload.completed_at ?? event.timestamp);
        // New Codex records carry their own exact start; older records require
        // the matching open Turn. Never pair an unrelated terminal by proximity.
        const matched = cursor.open?.id === id ? cursor.open : undefined;
        if (Number.isFinite(start)) emit(start, end);
        else if (matched) emit(matched.confirmed, end);
        if (matched) cursor.open = undefined;
      } else if (cursor.open && payload.type === "token_count") {
        const confirmed = timestamp(event.timestamp);
        emit(cursor.open.confirmed, confirmed);
        if (Number.isFinite(confirmed)) cursor.open.confirmed = Math.max(cursor.open.confirmed, confirmed);
      }
    }
    return { cursor, observations };
  } finally { await file.close(); }
}
