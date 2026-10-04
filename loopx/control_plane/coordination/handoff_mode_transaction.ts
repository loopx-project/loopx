/** Provider-neutral mode transition: quiescence and mode share one CAS snapshot. */
import type {JsonObject} from "../effect_program.ts";
import type {AuthorityStore} from "./authority_store.ts";
import {AuthorityStoreProtocolError, canonicalAuthorityObject, canonicalAuthoritySha256, requireAuthorityStoreId} from "./authority_store_codec.ts";
import {indexCoordinationProjection, validateCoordinationTodoReadModel} from "./coordination_projection.ts";
import {HANDOFF_MODES, decideHandoffMode} from "./handoff_mode_policy.ts";
import {requireBoolean, requireStringLiteral} from "../runtime_decode.ts";
import {parseIsoTimestamp} from "../runtime_timestamp.ts";
import {handoffQuiescence} from "./handoff_mode_facts.ts";
import {CoordinationCommandReceipt} from "./command_receipt.ts";

export const HANDOFF_MODE_SET_SCHEMA = "loopx_coordination_handoff_mode_set_request_v0";
const RESULT_SCHEMA = "loopx_coordination_handoff_mode_set_result_v0";
const RECEIPT_SCHEMA = "loopx_coordination_handoff_mode_receipt_v0";
export interface HandoffModeSetInput {
  goal_id: string;
  operation_id: string;
  requested_mode: string;
  observed_at: string;
  dry_run: boolean;
}

function failure(reason_code: string, reason: string, failureKind?: "decision_rejection"): JsonObject & {schema_version: typeof RESULT_SCHEMA} {
  return {schema_version: RESULT_SCHEMA, status: "failed", changed: false, reason_code, reason,
    ...(failureKind ? {failure_kind: failureKind} : {})};
}

function commandReceipt(input: HandoffModeSetInput, hash: string) {
  return new CoordinationCommandReceipt({result_schema: RESULT_SCHEMA,
    identity: {schema_version: RECEIPT_SCHEMA, goal_id: input.goal_id,
      operation_id: input.operation_id, request_sha256: hash},
    failure: (code, reason) => failure(code, reason, "decision_rejection"),
    decode(record) {
      const decision = canonicalAuthorityObject(record.decision, "handoff mode decision receipt");
      if (typeof decision.changed !== "boolean" || decision.goal_id !== input.goal_id ||
          decision.operation_id !== input.operation_id || decision.handoff_mode !== input.requested_mode ||
          decision.previous_mode_valid !== true ||
          !HANDOFF_MODES.some(mode => mode === decision.previous_mode)) {
        throw new AuthorityStoreProtocolError("invalid handoff mode decision receipt");
      }
      return {fields: decision, changed: decision.changed};
    }});
}

export async function executeHandoffModeSet(store: AuthorityStore, raw: HandoffModeSetInput): Promise<JsonObject> {
  let input: HandoffModeSetInput;
  try {
    input = {...raw, goal_id: requireAuthorityStoreId(raw.goal_id, "goal id"),
      operation_id: requireAuthorityStoreId(raw.operation_id, "operation id"),
      requested_mode: requireStringLiteral(raw.requested_mode, HANDOFF_MODES, "requested_mode"),
      dry_run: requireBoolean(raw.dry_run, "dry_run")};
    const parsed = typeof input.observed_at === "string" ? parseIsoTimestamp(input.observed_at) : null;
    if (!parsed) throw new Error("observed_at must be a valid ISO timestamp");
  } catch (error) { return failure("invalid_handoff_mode_request", String(error)); }
  // Retry time is observation context, not a new intent. Preview never consumes an operation id.
  const hash = canonicalAuthoritySha256({goal_id: input.goal_id, requested_mode: input.requested_mode});
  const receipt = commandReceipt(input, hash);
  if (!input.dry_run) {
    const previous = await receipt.read(store);
    if (previous) return previous;
  }
  if (input.requested_mode === "legacy") return failure("handoff_mode_retired",
    "legacy is a historical source mode; select soft_claim or hard_lease", "decision_rejection");
  const loaded = await store.loadAuthority();
  if (loaded.status !== "loaded") return {schema_version: RESULT_SCHEMA, ...loaded, changed: false};
  let decision: JsonObject;
  try {
    const head = loaded.head;
    const indexed = indexCoordinationProjection(head, input.goal_id);
    validateCoordinationTodoReadModel(head, input.goal_id);
    const previous = requireStringLiteral(head.handoff_mode ?? "legacy", HANDOFF_MODES, "canonical handoff_mode");
    const {claimed_todos: claimed, active_leases: leases} = handoffQuiescence(
      [...indexed.todos.values()], [...indexed.leases.values()], input.observed_at);
    const plan = decideHandoffMode({kind: "valid", value: previous},
      requireStringLiteral(input.requested_mode, HANDOFF_MODES, "requested_mode"), claimed.length, leases.length);
    decision = {goal_id: input.goal_id, operation_id: input.operation_id, previous_mode: previous,
      previous_mode_valid: true, handoff_mode: input.requested_mode, changed: plan.outcome === "apply"};
    if (plan.outcome === "rejected") return {...failure(String(plan.code),
      "handoff_mode can only change without unfinished claimed Todos or time-active leases",
      "decision_rejection"),
      ...decision, claimed_todos: claimed, active_leases: leases, provider_revision: loaded.provider_revision};
  } catch (error) { return failure("invalid_handoff_mode_authority", String(error)); }
  if (input.dry_run) return {schema_version: RESULT_SCHEMA, ...decision, status: "planned",
    provider_revision: loaded.provider_revision};
  // Seal even an unchanged accepted intent: retry after another mode switch must not reapply it.
  const result = await receipt.commit(store, {operation_id: input.operation_id,
    expected_provider_revision: loaded.provider_revision,
    next_projection: {...loaded.head, handoff_mode: input.requested_mode},
    events: decision.changed ? [{schema_version: "loopx_handoff_mode_changed_v0", ...decision}] : [],
    receipts: [{schema_version: RECEIPT_SCHEMA, goal_id: input.goal_id, operation_id: input.operation_id,
      request_sha256: hash, decision}]});
  // This command historically reports a committed no-op as applied. Preserve
  // that wire contract while sharing durable recovery and strict receipt decode.
  return result.status === "no_change" ? {...result, status: "applied"} : result;
}
