import {parseTodoTimestampMicros} from "../../../../../../loopx/control_plane/runtime_timestamp.ts";
import type {ExploreHistoryEvent, ExploreHistoryNode} from "../../../../../../loopx/control_plane/presentation/explore_history.ts";
import type {ExploreFindingSections} from "../../../../../../loopx/control_plane/presentation/explore_timeline.ts";

/** The history reader supplies its already qualified, current-language projection.
 *  */
export type ResearchRecordDisplay = {
  events?: ReadonlyMap<string, ExploreHistoryEvent>;
  sections?: ReadonlyMap<string, ExploreFindingSections | null>;
};

/** Local reading selection, fenced to the same Goal and physical source. It
 * carries no records, status judgment, source connection or execution grant. */
export type ResearchRecordsLocation = {
  open: boolean; query: string; status: string; expanded: string[]; focused?: string; limit: number;
  from?: string; to?: string;
};
export const defaultResearchRecordsLocation: ResearchRecordsLocation = {open: false, query: "", status: "", expanded: [], limit: 50};

export function readResearchRecordsLocation(search: URLSearchParams, scope: string): ResearchRecordsLocation {
  try {
    const entries = search.getAll("research_records");
    if (entries.length !== 1) return {...defaultResearchRecordsLocation};
    const value = JSON.parse(entries[0]!);
    if (!value || value.scope !== scope || typeof value.open !== "boolean" || typeof value.query !== "string"
        || typeof value.status !== "string" || !Array.isArray(value.expanded)
        || !value.expanded.every((id: unknown) => typeof id === "string" && id)
        || !Number.isSafeInteger(value.limit) || value.limit < 50) return {...defaultResearchRecordsLocation};
    if ((value.from !== undefined && !validResearchDate(value.from)) || (value.to !== undefined && !validResearchDate(value.to))) return {...defaultResearchRecordsLocation};
    return {...(value.from ? {from: value.from} : {}), ...(value.to ? {to: value.to} : {}),
      open: value.open, query: value.query, status: value.status, expanded: [...new Set<string>(value.expanded)],
      ...(typeof value.focused === "string" && value.focused ? {focused: value.focused} : {}), limit: value.limit};
  } catch {return {...defaultResearchRecordsLocation};}
}

export function writeResearchRecordsLocation(search: URLSearchParams, scope: string, value: ResearchRecordsLocation) {
  const next = new URLSearchParams(search);
  next.set("research_records", JSON.stringify({scope, ...value}));
  return next;
}

/** A node and its sole linked finding may describe the same research entry.
 * Multiple finding identities or conflicting runs leave the records separate.
 * Keep original records, including revisions, in their recorded order. */
export function researchRecordGroups(matches: readonly ExploreHistoryEvent[], nodes: readonly ExploreHistoryNode[]) {
  const linked = new Map<string, ExploreHistoryNode>();
  for (const node of nodes) {
    const members = [...node.revisions, ...node.findings];
    const findingIds = new Set(members.filter(event => event.event_kind === "finding").map(event => event.result_id));
    const runs = new Set(members.map(event => event.run_id).filter(Boolean));
    if (findingIds.size !== 1 || runs.size > 1) continue;
    for (const event of members) linked.set(event.event_id, node);
  }
  const groups = new Map<string, {id: string; node?: ExploreHistoryNode; events: ExploreHistoryEvent[]}>();
  for (const event of matches) {
    const node = linked.get(event.event_id);
    const id = node ? `node:${node.id}` : `event:${event.event_id}`;
    const group = groups.get(id);
    if (group) group.events.push(event);
    else groups.set(id, {id, node, events: [event]});
  }
  return [...groups.values()];
}

export type ResearchDateRange = {from?: string; to?: string};

/** Calendar days refer to recorded-at UTC, never an inferred execution period. */
export function validResearchDate(value: unknown): value is string {
  if (value === "") return true;
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const time = Date.parse(`${value}T00:00:00Z`);
  return Number.isFinite(time) && new Date(time).toISOString().slice(0, 10) === value;
}

export function validResearchDateRange({from, to}: ResearchDateRange): boolean {
  return validResearchDate(from ?? "") && validResearchDate(to ?? "") && !(from && to && from > to);
}

/** Reuse the native reader's ISO grammar and microsecond UTC conversion.
 * Date filtering additionally requires an explicit zone and bounded offset
 * components; Python accepts overflowing offset minutes, but this view does not.
 * Parse a trimmed copy only: exports retain the exact original timestamp. */
function recordedResearchTime(value: string): bigint | null {
  const source = value.trim();
  const timestamp = /^(?:\d{4}-\d{2}-\d{2}|\d{8}|\d{4}-W\d{2}(?:-[1-7])?|\d{4}W\d{2}[1-7]?)[^Zz](.+)$/u.exec(source);
  if (!timestamp) return null;
  const zone = /(?:[Zz]|[+-](\d{2})(?:(:?)(\d{2})(?:\2(\d{2}))?)?(?:[.,]\d+)?)$/u.exec(timestamp[1]);
  if (!zone || Number(zone[1] ?? 0) > 23 || Number(zone[3] ?? 0) > 59
      || Number(zone[4] ?? 0) > 59) return null;
  return parseTodoTimestampMicros(source);
}

/** Search source records, including superseded revisions, before display paging.
 * Optional display text broadens matching; results remain the original records.
 * Tokens are literal, case-insensitive AND terms; no inferred evidence or status. */
export function searchResearchRecords(events: readonly ExploreHistoryEvent[], query: string, status = "", display?: ResearchRecordDisplay, range: ResearchDateRange = {}) {
  if (!validResearchDateRange(range)) return [];
  const from = range.from ? BigInt(Date.parse(`${range.from}T00:00:00Z`)) * 1000n : null;
  const until = range.to ? BigInt(Date.parse(`${range.to}T00:00:00Z`)) * 1000n + 86400000000n : null;
  const terms = query.trim().toLowerCase().split(/\s+/u).filter(Boolean);
  return events.filter(event => {
    if (status && event.status !== status) return false;
    if (range.from || range.to) {
      const recorded = recordedResearchTime(event.recorded_at);
      if (recorded === null || (from !== null && recorded < from) || (until !== null && recorded >= until)) return false;
    }
    if (!terms.length) return true;
    const translated = display?.events?.get(event.event_id);
    const sections = display?.sections?.get(event.event_id);
    const fields = [event.title, event.summary, event.blocked_reason, event.event_id,
      event.result_id, event.node_id, event.parent_id, event.from_node, event.to_node,
      event.run_id, event.agent_id, event.supersedes, ...event.evidence_refs,
      translated?.title, translated?.summary, translated?.blocked_reason, translated?.execution_observation?.change_reason,
      sections?.applicability, sections?.observation, sections?.interpretation,
      ...(sections?.decision?.changed ?? []), ...(sections?.decision?.stopped ?? []), ...(sections?.decision?.retained ?? []),
    ].filter((field): field is string => typeof field === "string").map(field => field.toLowerCase());
    return terms.every(term => fields.some(field => field.includes(term)));
  });
}
