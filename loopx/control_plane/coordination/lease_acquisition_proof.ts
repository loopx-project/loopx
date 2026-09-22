/** Historical acquisition and current permission are different facts. Both
 * standalone acquire and atomic claim/acquire return this current proof. */
import type {JsonObject} from "../effect_program.ts";
import type {AuthorityStore, AuthorityStoreLoadResult} from "./authority_store.ts";
import {canonicalAuthorityObject} from "./authority_store_codec.ts";
import {indexCoordinationProjection, validateCoordinationTodoReadModel} from "./coordination_projection.ts";
import {canonicalTaskLease, canonicalTaskLeaseAcquireFacts} from "./task_lease_state.ts";
import {HANDOFF_MODES} from "./handoff_mode_policy.ts";
import {requireStringLiteral} from "../runtime_decode.ts";
import {leaseOwnerRejection} from "../work_items/task_lease_eligibility.ts";
import {leaseEpoch, leaseVersion, leaseIsActive} from "../work_items/task_lease_acquire.ts";
import {acceptanceWorkGuard} from "../goals/acceptance_contract.ts";

interface AcquisitionIdentity {
  goal_id: string; todo_id: string; owner: string; idempotency_key: string;
  registered_agents: readonly string[]; now: Date;
}

/** Decode only a loaded decision snapshot. Receipt precedence must be settled
 * before calling this: an already committed operation needs no new admission. */
export function acquisitionFacts(head: AuthorityStoreLoadResult, input: AcquisitionIdentity) {
  if (head.status !== "loaded") return {head, facts: null, mode: null};
  validateCoordinationTodoReadModel(head.head, input.goal_id);
  const index = indexCoordinationProjection(head.head, input.goal_id);
  return {head, facts: canonicalTaskLeaseAcquireFacts(index, input.goal_id, input.todo_id, input.registered_agents, input.now),
    mode: requireStringLiteral(head.head.handoff_mode ?? "legacy", HANDOFF_MODES, "canonical handoff_mode")};
}

export async function currentLeaseAcquisitionProof<S extends string>(store: AuthorityStore,
  input: AcquisitionIdentity & {operation_id: string; required_handoff_mode?: "hard_lease"},
  result: JsonObject & {schema_version: S},
  failed: (code: string, reason: string, detail?: JsonObject) => JsonObject & {schema_version: S},
): Promise<JsonObject & {schema_version: S}> {
  if (!["applied", "no_change", "replayed", "recovered"].includes(String(result.status))) return result;
  const recovery = {operation_id: input.operation_id, retry_with_same_operation_id: true};
  const unavailable = () => ({...failed("canonical_acquire_readback_required",
    "acquisition receipt is durable but current authority is unavailable; retry the same request"),
    status: "ambiguous", original_receipt: result.original_receipt, recovery});
  let loaded: AuthorityStoreLoadResult;
  try { loaded = await store.loadAuthority(); }
  catch { return unavailable(); }
  const {head, facts, mode} = acquisitionFacts(loaded, input);
  if (head.status !== "loaded" || facts === null) return unavailable();
  const original = canonicalTaskLease(canonicalAuthorityObject(result.lease, "original acquire lease"), input.goal_id, input.todo_id);
  const current = facts.current;
  const details = {handoff_mode: mode, original_receipt: result.original_receipt,
    current_provider_revision: head.provider_revision, current_cursor: head.cursor};
  const rejection = mode === "soft_claim" ? "handoff_mode_forbids_lease"
    : input.required_handoff_mode !== undefined && mode !== input.required_handoff_mode ? "claim_lease_requires_hard_lease"
    : leaseOwnerRejection(facts.todo, input.owner, input.registered_agents);
  if (rejection) return failed(rejection, `current authority rejects lease acquire replay: ${rejection}`, details);
  if (!current || !leaseIsActive(current, input.now) || current.owner !== input.owner ||
      current.idempotency_key !== input.idempotency_key || leaseEpoch(current) !== leaseEpoch(original) ||
      leaseVersion(current) < leaseVersion(original)) {
    return failed("idempotency_key_reuse", "acquire receipt belongs to a retired execution; use a new execution key", details);
  }
  const acceptance = acceptanceWorkGuard(head.head, input.goal_id, input.todo_id);
  if (acceptance !== null && !acceptance.allowed) {
    return failed(String(acceptance.reason_code), `${String(acceptance.reason)} Inspect Goal acceptance and ask the owner to configure or rebind this Todo.`,
      {...details, goal_acceptance_guard: acceptance});
  }
  // A renewal advances version/expiry within this execution. Never rewrite the
  // immutable acquisition receipt to make it look like the renewed decision.
  return {...result, ...details, lease: current};
}
