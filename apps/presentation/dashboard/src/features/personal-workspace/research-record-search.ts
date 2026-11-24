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
export type ResearchRecordsLocation = {open: boolean; query: string; status: string; expanded: string[]; focused?: string; limit: number};
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
    return {open: value.open, query: value.query, status: value.status, expanded: [...new Set<string>(value.expanded)],
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

/** Search source records, including superseded revisions, before display paging.
 * Optional display text broadens matching; results remain the original records.
 * Tokens are literal, case-insensitive AND terms; no inferred evidence or status. */
export function searchResearchRecords(events: readonly ExploreHistoryEvent[], query: string, status = "", display?: ResearchRecordDisplay) {
  const terms = query.trim().toLowerCase().split(/\s+/u).filter(Boolean);
  return events.filter(event => {
    if (status && event.status !== status) return false;
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
