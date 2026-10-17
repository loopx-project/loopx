/** Local-file adapter to Explore's existing display owners. This accepts records
 * for reading; it does not verify their claims or grant curated scene authority. */
import {parseExploreHistoryPage, projectExploreHistory, type ExploreHistoryEvent} from "../../../../../loopx/control_plane/presentation/explore_history.ts";
import {buildExploreTimeline, exploreTimelinePhaseAt} from "../../../../../loopx/control_plane/presentation/explore_timeline.ts";

// Bound synchronous browser parsing and the shared timeline's in-memory arrays.
// Reject the entire file at the limit; never publish a silently shortened log.
export const LOCAL_LOG_MAX_BYTES = 20 * 1024 * 1024;
export const LOCAL_LOG_MAX_RECORDS = 20_000;
// File-intake UI errors only; these do not extend Explore's event/status vocabulary.
export type LocalLogIssue = "size" | "empty" | "encoding" | "json" | "record" | "identity" | "read";
export class LocalLogError extends Error {
  readonly issue: LocalLogIssue;
  readonly line: number | null;
  constructor(issue: LocalLogIssue, line: number | null = null) {
    super(issue); this.issue = issue; this.line = line;
  }
}
export type LocalLogEntry = {line: number; event: ExploreHistoryEvent; source: Record<string, unknown>};
export type LocalExploreLog = ReturnType<typeof readLocalExploreLog>;

export function readLocalExploreLog(bytes: ArrayBuffer) {
  if (bytes.byteLength > LOCAL_LOG_MAX_BYTES) throw new LocalLogError("size");
  let text: string;
  try { text = new TextDecoder("utf-8", {fatal: true}).decode(bytes); }
  catch { throw new LocalLogError("encoding"); }
  const entries: LocalLogEntry[] = [];
  let goalId = "";
  let offset = 0, lineNumber = 0;
  // Blank lines are legal. Walk them without allocating an array containing
  // millions of empty entries for a newline-only file within the byte limit.
  while (offset <= text.length) {
    const end = text.indexOf("\n", offset);
    const line = text.slice(offset, end < 0 ? text.length : end);
    offset = end < 0 ? text.length + 1 : end + 1;
    lineNumber++;
    if (!line.trim()) continue;
    if (entries.length === LOCAL_LOG_MAX_RECORDS) throw new LocalLogError("size");
    let source: Record<string, unknown>;
    try { source = JSON.parse(line); }
    catch { throw new LocalLogError("json", lineNumber); }
    if (!source || Array.isArray(source) || typeof source !== "object" ||
        typeof source.goal_id !== "string" || !source.goal_id.trim()) throw new LocalLogError("record", lineNumber);
    if (!goalId) goalId = source.goal_id;
    let event: ExploreHistoryEvent;
    try {
      event = parseExploreHistoryPage({ok: true, goal_id: goalId, items: [source], total: 1, next_cursor: null}, goalId).items[0]!;
    } catch { throw new LocalLogError("record", lineNumber); }
    entries.push({line: lineNumber, event, source});
  }
  if (!entries.length) throw new LocalLogError("empty");
  let history: ReturnType<typeof projectExploreHistory>;
  try { history = projectExploreHistory(entries.map(entry => entry.event), goalId); }
  catch { throw new LocalLogError("identity"); }
  const timeline = buildExploreTimeline(history.nodes, history.edges, history.events);
  return {goalId, entries, history, timeline};
}

/** Filter source records without changing append order, labels or time semantics. */
export function selectLocalLogEntries(log: LocalExploreLog, query: string, status: string | null, phase: number | "unknown" | null) {
  const needle = query.trim().toLowerCase();
  return log.entries.filter(entry => {
    const event = entry.event;
    if (status !== null && event.status !== status) return false;
    if (phase !== null) {
      const parsed = Date.parse(event.recorded_at);
      const at = Number.isFinite(parsed) ? parsed : null;
      if (phase === "unknown" ? at !== null : exploreTimelinePhaseAt(log.timeline.phases, at) !== phase) return false;
    }
    return !needle || [event.title, event.summary, event.result_id, event.event_id, ...event.evidence_refs]
      .some(value => value.toLowerCase().includes(needle));
  });
}
