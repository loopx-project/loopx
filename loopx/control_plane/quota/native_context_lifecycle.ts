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
  const sameSession = state.session_id === session;
  const same = sameSession && state.cwd === cwd;
  const name = event.hook_event_name;
  // PostToolUse reports the command's workdir. It can verify selected bytes
  // from this session's current capture, but cannot replace the host workspace.
  const selectedFromToolWorkdir = !same && sameSession && name === "PostToolUse"
    && typeof state.cwd === "string" && state.cwd.length > 0 && request.pages_verified === true;
  if (!same && !selectedFromToolWorkdir) {
    if (name !== "SessionStart") return state;
    state = {session_id: session, cwd};
  }
  const recoveryFile = jsonObject(state.recovery);
  const fileEpochCurrent = recoveryFile?.continuity_basis === "captured_file"
    && jsonObject(recoveryFile.target)?.context_epoch === state.context_epoch;
  const invalidate = () => {
    // Reset is idempotent while already invalid. Pre/PostCompact and the
    // subsequent SessionStart(compact) therefore do not rotate three epochs.
    if (!state.context_epoch || state.candidate || state.pending || fileEpochCurrent)
      state.context_epoch = requireNonEmptyString(request.new_epoch, "new native context epoch");
    delete state.candidate;
    delete state.pending;
    delete state.active_call; // retire legacy diagnostic-call binding
    delete state.consumed_pointers;
    delete state.confirmed_read_sections;
    delete state.section_reads;
    delete state.selected_resume;
  };
  if (name === "SessionStart") {
    // The provider may qualify the native-selected replay source against the
    // completed read checkpoint. Neither an event's same ID nor a disk journal
    // is that observation. Unknown/replaced/truncated histories still reset.
    const candidate = jsonObject(state.candidate);
    if (same && event.source === "resume" && request.resume_verified === true
        && !state.pending && candidate?.basis === NATIVE_CONTEXT_BASIS
        && candidate.capture_bytes_verified === true) return state;
    if (same && event.source === "resume" && fileEpochCurrent) {
      // Selected outputs can establish their own literal sections on a
      // qualified replay. They never establish a whole-input candidate.
      const readScope = jsonObject(state.read_scope);
      const readScopeCurrent = readScope?.session_id === session && readScope.context_epoch === state.context_epoch;
      const previous = jsonObject(state.consumed_pointers);
      const replayed = jsonObject(request.resume_sections);
      const retained = Object.fromEntries(Object.entries(replayed ?? {}).filter(([address, digest]) =>
        typeof digest === "string" && previous?.[address] === digest));
      if (candidate?.native_resume || (state.candidate && !candidate)) {
        // Failed complete replay may conceal compaction. Rotate the file
        // epoch, keeping only the independently verified literal selections.
        invalidate();
      } else {
        // Ordinary resume alone is not compaction or proof of model retention.
        delete state.candidate;
        delete state.pending;
        delete state.active_call;
        delete state.section_reads;
      }
      if (readScopeCurrent && Object.keys(retained).length > 0) {
        // Only the replay-verified sections move to the new epoch. The next
        // projection still matches the rest of the scope and each field's digest.
        state.read_scope = {...readScope, context_epoch: state.context_epoch};
        state.consumed_pointers = retained;
        state.confirmed_read_sections = Object.keys(retained);
      } else {
        delete state.consumed_pointers;
        delete state.confirmed_read_sections;
        delete state.selected_resume;
      }
      return state;
    }
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
    const selected = jsonObject(page.read_sections);
    if (selected) {
      // IO has checked exact selected bytes against this journal's current full
      // source. This is observed tool delivery, never a full-input baseline.
      if (request.pages_verified !== true || page.ok !== true
          || page.source_sha256 !== jsonObject(state.source_recovery)?.sha256) continue;
      const offset = page.offset, total = page.total_bytes, end = page.eof === true ? total : page.next_offset;
      if (!Number.isSafeInteger(total) || Number(total) <= 0 || !Number.isSafeInteger(offset)
          || !Number.isSafeInteger(end) || Number(offset) < 0 || Number(end) <= Number(offset)
          || Number(end) > Number(total) || typeof page.content !== "string"
          || Buffer.byteLength(page.content) !== Number(end) - Number(offset)
          || (page.eof === true && page.next_offset !== null)
          || (page.eof === false && Number(end) >= Number(total)) || typeof page.sha256 !== "string") continue;
      const reads = jsonObject(state.section_reads) ?? {};
      const previous = jsonObject(reads[page.sha256]);
      const ranges = mergedRanges(previous?.ranges, Number(offset), Number(end));
      reads[page.sha256] = {ranges};
      if (ranges.length === 1 && ranges[0][0] === 0 && ranges[0][1] === total) {
        state.consumed_pointers = {...jsonObject(state.consumed_pointers), ...selected};
        state.confirmed_read_sections = Object.keys(state.consumed_pointers as JsonObject);
        delete reads[page.sha256];
      }
      state.section_reads = reads;
      continue;
    }
    if (selectedFromToolWorkdir || "selection_sha256" in page || "selected_pointers" in page || "list_fields" in page) continue;
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
    const sections = jsonObject(reads.read_sections);
    if (sections) {
      state.consumed_pointers = {...jsonObject(state.consumed_pointers), ...sections};
      state.confirmed_read_sections = Object.keys(state.consumed_pointers as JsonObject);
    }
    delete state.pending;
  }
  return state;
}
