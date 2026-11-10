import type {JsonObject} from "../effect_program.ts";
import {canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";

// Exact field classification, never prose/substring relevance. Unknown future
// domain fields remain digest inputs by default. Execution observations and
// presentation must not revoke a confirmed work declaration.
export const NON_WORK_FIELDS = new Set([
  "schema_version", "source_section", "index", "title", "priority", "status", "done", "archive_state",
  "claimed_by", "created_by", "last_actor_agent_id", "updated_at", "completed_at", "completion_turn_key",
  "completion_validation_sha256", "completion_recovery", "completion_continuation", "no_followup", "decision_outcome",
  "completion_result", "completion_receipt_id",
  "decision_scope_outcomes", "note", "evidence", "reason", "handoff_note", "resume_ready",
  "resume_monitor_generation", "last_checked_at", "result_hash", "consecutive_no_change",
  "material_change", "material_change_generation", "material_change_scope",
  "material_change_scope_generations", "monitor_effect_id",
]);
export function goalAcceptanceTodoDigest(todo: JsonObject): string {
  return canonicalAuthoritySha256(Object.fromEntries(Object.entries(todo).filter(([key]) => !NON_WORK_FIELDS.has(key))));
}
