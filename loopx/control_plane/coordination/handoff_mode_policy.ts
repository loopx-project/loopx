/** One policy owner: two current execution modes, historical mode decoding and migration. */
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject, requireStringArray, requireStringLiteral} from "../runtime_decode.ts";

import {HANDOFF_MODES, type HandoffMode} from "./handoff_mode_vocabulary.ts";
export {HANDOFF_MODES, EXECUTION_HANDOFF_MODES, type HandoffMode, type ExecutionHandoffMode} from "./handoff_mode_vocabulary.ts";
export const HANDOFF_MODE_PLAN_SCHEMA = "loopx_handoff_mode_plan_request_v0";

import type {PersistedHandoffMode} from "./handoff_mode_facts.ts";

export const HANDOFF_MODE_RETIRED_REASON = "Legacy ownership is historical only. Release retained execution, then review handoff-mode plan-migration/migrate to soft_claim or hard_lease; policy migration does not require another storage promotion.";

/** Historical mode is readable and releasable, never present-tense execution.
 * Receipt recovery runs before fresh admission; readback must not turn it into a grant. */
export function retiredHandoffModeRejection(mode: unknown): "handoff_mode_retired" | null {
  return mode === "legacy" ? "handoff_mode_retired" : null;
}

export function decideHandoffMode(previous: PersistedHandoffMode, requested: HandoffMode,
  claims: number, leases: number): JsonObject {
  if (requested === "legacy") return {outcome: "rejected", code: "handoff_mode_retired",
    changed: false, previous_mode: previous.value, handoff_mode: requested};
  const unchanged = previous.kind === "valid" && previous.value === requested;
  const rejected = !unchanged && (claims > 0 || leases > 0);
  return {outcome: unchanged ? "no_change" : rejected ? "rejected" : "apply",
    code: unchanged ? "handoff_mode_unchanged" : rejected ? "handoff_mode_not_quiescent" : "handoff_mode_transition",
    idempotent: unchanged, previous_mode: previous.value, handoff_mode: requested};
}

export function planHandoffMode(value: unknown): JsonObject {
  const input = requireJsonObject(value, "handoff mode plan");
  if (input.schema_version !== HANDOFF_MODE_PLAN_SCHEMA) throw new Error("handoff mode plan schema mismatch");
  const previous = requireStringLiteral(input.previous_mode, HANDOFF_MODES, "previous_mode");
  const requested = requireStringLiteral(input.requested_mode, HANDOFF_MODES, "requested_mode");
  const claims = requireStringArray(input.active_claimed_todo_ids, "active_claimed_todo_ids");
  const leases = requireStringArray(input.active_lease_todo_ids, "active_lease_todo_ids");
  return {schema_version: "loopx_handoff_mode_plan_result_v0",
    ...decideHandoffMode({kind: "valid", value: previous}, requested, claims.length, leases.length)};
}
