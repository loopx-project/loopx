import type {ExploreHistoryEvent} from "../../../../../../loopx/control_plane/presentation/explore_history.ts";
import type {ExploreFindingSections} from "../../../../../../loopx/control_plane/presentation/explore_timeline.ts";

/** The history reader supplies its already qualified, current-language projection.
 * This search neither validates a translation nor borrows another snapshot. */
export type ResearchRecordDisplay = {
  events?: ReadonlyMap<string, ExploreHistoryEvent>;
  sections?: ReadonlyMap<string, ExploreFindingSections | null>;
};

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
