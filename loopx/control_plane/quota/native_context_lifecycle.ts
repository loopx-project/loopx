/** Native lifecycle assumptions for optional context transport. No work authority.
 * Complete reader pages establish an explicit engineering assumption, not an
 * observation that the host retained those bytes in a later model request. */
import type {JsonObject} from "../effect_program.ts";
import {jsonObject, requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";

export const NATIVE_CONTEXT_BASIS = "native_hook_assumption";

function mergedRanges(value: unknown, start: number, end: number): number[][] {
  const ranges = Array.isArray(value) ? value.filter((range): range is number[] =>
    Array.isArray(range) && range.length === 2 && range.every(Number.isSafeInteger)) : [];
  ranges.push([start, end]);
  ranges.sort((a, b) => a[0] - b[0]);
  const merged: number[][] = [];
  for (const range of ranges) {
    const last = merged.at(-1);
    if (last && range[0] <= last[1]) last[1] = Math.max(last[1], range[1]);
    else merged.push([...range]);
  }
  return merged;
}

/** Reduce the existing private native journal. Ordinary tools and turn endings
 * preserve continuity. Failed/incomplete deliveries never create a baseline. */
export function updateNativeContextLifecycle(request: JsonObject): JsonObject {
  let state = structuredClone(requireJsonObject(request.state, "native context journal"));
  const event = requireJsonObject(request.event, "native lifecycle event");
  // Native subagent hooks carry the parent session id. They cannot update the
  // root journal; a child's own context must use its separate native session.
  if (typeof event.agent_id === "string" && event.agent_id) return state;
  const session = requireNonEmptyString(event.session_id, "native session id");
  const cwd = requireNonEmptyString(event.cwd, "native workspace");
  const same = state.session_id === session && state.cwd === cwd;
  const name = event.hook_event_name;
  if (!same) {
    if (name !== "SessionStart") return state;
    state = {session_id: session, cwd};
  }
  const invalidate = () => {
    // Reset is idempotent while already invalid. Pre/PostCompact and the
    // subsequent SessionStart(compact) therefore do not rotate three epochs.
    if (!state.context_epoch || state.candidate || state.pending)
      state.context_epoch = requireNonEmptyString(request.new_epoch, "new native context epoch");
    delete state.candidate;
    delete state.pending;
    delete state.active_call; // retire legacy diagnostic-call binding
  };
  if (name === "SessionStart") {
    // The provider may qualify the native-selected replay source against the
    // completed read checkpoint. Neither an event's same ID nor a disk journal
    // is that observation. Unknown/replaced/truncated histories still reset.
    const candidate = jsonObject(state.candidate);
    if (same && event.source === "resume" && request.resume_verified === true
        && !state.pending && candidate?.basis === NATIVE_CONTEXT_BASIS
        && candidate.capture_bytes_verified === true) return state;
    invalidate();
    return state;
  }
  if (name === "PreCompact" || name === "PostCompact") {
    invalidate();
    return state;
  }
  if (name === "Interrupt") {
    if (state.pending) invalidate();
    return state;
  }
  if (name !== "PostToolUse") return state;
  const recovery = jsonObject(state.recovery), pending = jsonObject(state.pending);
  if (!recovery || !jsonObject(recovery.target)) return state;
  const pages = Array.isArray(request.pages) ? request.pages : [];
  // The IO adapter authenticates bodies against journal-pinned capture bytes.
  // Self-reported lengths/digests are not that observation. Older callers and
  // metadata-only journals must establish a new verified full read.
  if (pages.length && request.pages_verified !== true) {
    invalidate();
    return state;
  }
  for (const value of pages) {
    const page = jsonObject(value);
    if (!page || page.schema_version !== "context_capture_page_v1") continue;
    const candidate = jsonObject(state.candidate);
    if (!state.pending && candidate?.capture_sha256 === recovery.sha256
        && candidate?.capture_bytes_verified === true
        && candidate?.target && page.ok === true) continue; // completed-page replay
    if (page.ok !== true) {
      if (pending) invalidate();
      return state;
    }
    const original = page.sha256 === recovery.sha256;
    const wire = pending && page.sha256 === pending.sha256;
    if (!original && !wire) continue; // late page for another immutable capture
    const total = original ? recovery.total_bytes : pending?.total_bytes;
    const offset = page.offset, end = page.eof === true ? total : page.next_offset;
    if (!Number.isSafeInteger(total) || Number(total) <= 0 || page.total_bytes !== total
        || typeof page.eof !== "boolean"
        || !Number.isSafeInteger(offset) || Number(offset) < 0 || !Number.isSafeInteger(end)
        || Number(end) <= Number(offset) || Number(end) > Number(total)
        || typeof page.content !== "string"
        || Buffer.byteLength(page.content, "utf8") !== Number(end) - Number(offset)
        || (page.eof === true && page.next_offset !== null)
        || (page.eof === false && Number(end) >= Number(total))) {
      invalidate();
      return state;
    }
    const reads = jsonObject(state.pending) ?? {target: recovery.target};
    const all = jsonObject(reads.ranges) ?? {};
    const key = String(page.sha256);
    const ranges = mergedRanges(all[key], Number(offset), Number(end));
    all[key] = ranges;
    reads.ranges = all;
    state.pending = reads;
    if (ranges.length !== 1 || ranges[0][0] !== 0 || ranges[0][1] !== total) continue;
    const target = {...requireJsonObject(recovery.target, "capture target"), context_epoch: state.context_epoch};
    state.candidate = {basis: NATIVE_CONTEXT_BASIS, capture_bytes_verified: true, target,
      capture_file: recovery.path, capture_sha256: recovery.sha256,
      ancestry: original ? 0 : pending?.ancestry ?? 0};
    delete state.pending;
  }
  return state;
}
