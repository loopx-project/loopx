import type {ExploreHistoryEvent} from "../../../../../../loopx/control_plane/presentation/explore_history.ts";

/** Search source records, including superseded revisions, before display paging.
 * Tokens are literal, case-insensitive AND terms; no inferred evidence or status. */
export function searchResearchRecords(events: readonly ExploreHistoryEvent[], query: string, status = "") {
  const terms = query.trim().toLowerCase().split(/\s+/u).filter(Boolean);
  return events.filter(event => {
    if (status && event.status !== status) return false;
    if (!terms.length) return true;
    const fields = [event.title, event.summary, event.blocked_reason, event.event_id,
      event.result_id, event.node_id, event.parent_id, event.from_node, event.to_node,
      event.run_id, event.agent_id, event.supersedes, ...event.evidence_refs].map(field => field.toLowerCase());
    return terms.every(term => fields.some(field => field.includes(term)));
  });
}
