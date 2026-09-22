/** A lease acquisition is one full-head admission/CAS with a retained receipt.
 * Unlike historical maintenance replay, success here must supply current proof. */
import type {JsonObject} from "../effect_program.ts";
import type {AuthorityStore} from "./authority_store.ts";
import {AuthorityStoreProtocolError, canonicalAuthorityObject, canonicalAuthoritySha256} from "./authority_store_codec.ts";
import {CoordinationCommandReceipt, commandReceiptResult} from "./command_receipt.ts";
import {prepareCoordinationProjectionCommit} from "./coordination_projection.ts";
import {canonicalTaskLease} from "./task_lease_state.ts";
import {decideTaskLeaseAcquire, materializeTaskLeaseAcquire} from "../work_items/task_lease_acquire_decision.ts";
import {acquisitionFacts, currentLeaseAcquisitionProof} from "./lease_acquisition_proof.ts";
import {acceptanceWorkGuard} from "../goals/acceptance_contract.ts";
import {normalizeGoalId, normalizeTodoId, normalizeOwner, normalizeIdempotencyKey,
  normalizeWriteScopes, normalizeTtl, leaseEpoch, leaseVersion, leaseIsActive,
  TaskLeaseAcquireError} from "../work_items/task_lease_acquire.ts";

export interface CanonicalTaskLeaseAcquireInput {
  goal_id: string; todo_id: string; owner: string; idempotency_key: string;
  expected_version: number | null; ttl_seconds: number | null;
  write_scopes: readonly string[]; registered_agents: readonly string[]; now: Date;
}

export async function executeCanonicalTaskLeaseAcquire(store: AuthorityStore, raw: CanonicalTaskLeaseAcquireInput,
  beforeCommit?: (lease: JsonObject) => Promise<void>): Promise<JsonObject> {
  const schema = "loopx_canonical_task_lease_acquire_result_v0";
  const failed = (code: string, reason: string, detail: JsonObject = {}): JsonObject & {schema_version: typeof schema} =>
    ({schema_version: schema, status: "failed", changed: false, reason_code: code, reason, failure_stage: "validation", ...detail});
  let input: CanonicalTaskLeaseAcquireInput & {ttl_seconds: number};
  try {
    input = {...raw, goal_id: normalizeGoalId(raw.goal_id), todo_id: normalizeTodoId(raw.todo_id),
      owner: normalizeOwner(raw.owner), idempotency_key: normalizeIdempotencyKey(raw.idempotency_key),
      write_scopes: normalizeWriteScopes(raw.write_scopes), ttl_seconds: normalizeTtl(raw.ttl_seconds),
      registered_agents: raw.registered_agents.map(normalizeOwner)};
    if (input.expected_version !== null && (!Number.isSafeInteger(input.expected_version) || input.expected_version < 0)) {
      return failed("invalid_expected_version", "expected lease version must be a non-negative safe integer");
    }
    if (!(input.now instanceof Date) || !Number.isFinite(input.now.valueOf())) return failed("invalid_clock", "lease acquire requires a valid clock");
  } catch (error) {
    return failed(error instanceof TaskLeaseAcquireError ? error.code : "invalid_canonical_acquire_request",
      error instanceof Error ? error.message : "invalid canonical lease acquire");
  }
  const identityFields = {goal_id: input.goal_id, todo_id: input.todo_id, owner: input.owner, idempotency_key: input.idempotency_key};
  const identity = {schema_version: "loopx_canonical_task_lease_acquire_receipt_v0",
    operation_id: `lease-acquire:${canonicalAuthoritySha256(identityFields)}`, goal_id: input.goal_id,
    request_sha256: canonicalAuthoritySha256({...identityFields, expected_version: input.expected_version,
      ttl_seconds: input.ttl_seconds, write_scopes: [...input.write_scopes].sort()})};
  const receipt = new CoordinationCommandReceipt({result_schema: schema, identity, failure: failed,
    decode(original) {
      const payload = commandReceiptResult(original);
      const lease = canonicalTaskLease(canonicalAuthorityObject(payload.fields.lease, "acquire receipt lease"), input.goal_id, input.todo_id);
      const scopes = [...(lease.write_scopes ?? []) as string[]].sort();
      if (JSON.stringify(scopes) !== JSON.stringify([...input.write_scopes].sort()) ||
          (lease.acquire_ttl_seconds != null && lease.acquire_ttl_seconds !== input.ttl_seconds) ||
          (payload.changed && input.expected_version !== null && leaseVersion(lease) !== input.expected_version + 1)) {
        throw new AuthorityStoreProtocolError("acquire receipt does not match its original parameters");
      }
      if (lease.owner !== input.owner || lease.idempotency_key !== input.idempotency_key || lease.status !== "active" ||
          leaseVersion(lease) < 1 || leaseEpoch(lease) < 1 || !leaseIsActive(lease, new Date(String(lease.acquired_at)))) {
        throw new AuthorityStoreProtocolError("acquire receipt does not match its execution identity");
      }
      return {...payload, fields: {...payload.fields, operation_id: identity.operation_id}};
    }});

  const currentProof = (result: JsonObject & {schema_version: typeof schema}) =>
    currentLeaseAcquisitionProof(store, {...input, operation_id: identity.operation_id}, result, failed);

  let committed = false;
  try {
    const replay = await receipt.read(store);
    if (replay !== null) return await currentProof(replay);
    const observation = await receipt.observe(store);
    if (observation.kind === "receipt") return await currentProof(observation.result);
    const {head, facts, mode} = acquisitionFacts(observation.authority, input);
    if (head.status !== "loaded" || facts === null) return failed("canonical_lease_authority_unavailable",
      "canonical lease authority is unavailable; restore the selected provider before retrying", {...head});
    const decision = decideTaskLeaseAcquire({handoff_mode: mode!, registered_agents: input.registered_agents,
      ...facts, command: input});
    if (decision.outcome === "rejected" || decision.outcome === "conflict") {
      return failed(decision.code, `canonical task lease acquire rejected: ${decision.code}`, {
        handoff_mode: mode, expected_version: input.expected_version, actual_version: leaseVersion(facts.current),
        ...(facts.todo ? {todo_status: facts.todo.status, claimed_by: facts.todo.claimed_by, excluded_agents: [...facts.todo.excluded_agents]} : {}),
        ...(decision.conflict_indexes.length ? {conflicts: decision.conflict_indexes.map(i => facts.other_leases[i])} : {})});
    }
    const acceptance = acceptanceWorkGuard(head.head, input.goal_id, input.todo_id);
    if (acceptance !== null && !acceptance.allowed) {
      return failed(String(acceptance.reason_code), `${String(acceptance.reason)} Inspect Goal acceptance and ask the owner to configure or rebind this Todo.`,
        {goal_acceptance_guard: acceptance});
    }
    const changed = decision.outcome === "apply";
    const lease = changed ? materializeTaskLeaseAcquire(input, input, decision, input.now) : facts.current;
    if (!lease) throw new AuthorityStoreProtocolError("accepted acquire lacks a lease");
    const commit = changed ? prepareCoordinationProjectionCommit({goal_id: input.goal_id,
      operation_id: identity.operation_id, expected_provider_revision: head.provider_revision, projection: head.head,
      mutations: [{kind: "lease_upsert", lease}]}) : {operation_id: identity.operation_id,
        expected_provider_revision: head.provider_revision, next_projection: head.head, events: [], receipts: []};
    commit.receipts = [{...identity, result: {changed, lease, handoff_mode: mode}}];
    await beforeCommit?.(lease);
    committed = true;
    const result = await receipt.commit(store, commit);
    return await currentProof(result);
  } catch (error) {
    if (committed) return {...failed("canonical_acquire_recovery_required", "acquire may be durable; retry the same request"),
      status: "ambiguous", failure_stage: "durable_writeback", recovery: {operation_id: identity.operation_id, retry_with_same_operation_id: true}};
    return failed(error instanceof TaskLeaseAcquireError ? error.code : "invalid_canonical_acquire_state",
      error instanceof Error ? error.message : "canonical acquisition state is invalid");
  }
}
