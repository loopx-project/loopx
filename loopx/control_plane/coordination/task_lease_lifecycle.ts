import {canonicalTaskLease, canonicalLeaseTodoFact} from "./task_lease_state.ts";
/** Lease mutations share one canonical revision, decision and durable receipt.
 * Providers own persistence only; replay never grants current execution rights. */
import type {JsonObject} from "../effect_program.ts";
import type {AuthorityStore, AuthorityStoreCommit} from "./authority_store.ts";
import {AuthorityStoreProtocolError, canonicalAuthorityObject} from "./authority_store_codec.ts";
import {CoordinationCommandReceipt, commandReceiptResult} from "./command_receipt.ts";
import {indexCoordinationProjection, prepareCoordinationProjectionCommit, validateCoordinationTodoReadModel,
  type CoordinationProjectionMutation} from "./coordination_projection.ts";
import {planLeaseClaimTransfer} from "./lease_claim_transfer.ts";
import {decideTaskLeaseLifecycle, materializeTaskLeaseLifecycle,
  type TaskLeaseLifecycleDecisionOperation, type TaskLeaseLifecycleDecisionCommand} from "../work_items/task_lease_lifecycle_decision.ts";
import {requireStringLiteral} from "../runtime_decode.ts";
import {HANDOFF_MODES} from "./handoff_mode_policy.ts";
import {leaseEpoch, leaseIsActive, leaseVersion, normalizeAgent, normalizeGoalId,
  normalizeIdempotencyKey, normalizeOwner, normalizeTodoId,
  normalizeTtl, utcIsoformat, TaskLeaseAcquireError, type LeaseRecord} from "../work_items/task_lease_acquire.ts";
import {taskLeaseOperationIdentity, taskLeaseOperationRequestDigest} from "../work_items/task_lease_operation_identity.ts";

// The shipped renewal receipt namespace and request digest stay unchanged.
const CONTRACTS = {
  renew: {result: "loopx_canonical_task_lease_renew_result_v0", receipt: "loopx_canonical_task_lease_renew_receipt_v0", field: "renewed"},
  transfer: {result: "loopx_canonical_task_lease_transfer_result_v0", receipt: "loopx_canonical_task_lease_transfer_receipt_v0", field: "transferred"},
  release: {result: "loopx_canonical_task_lease_release_result_v0", receipt: "loopx_canonical_task_lease_release_receipt_v0", field: "released"},
} as const;

export interface CanonicalTaskLeaseLifecycleInput {
  operation: TaskLeaseLifecycleDecisionOperation;
  goal_id: string;
  todo_id: string;
  owner: string;
  idempotency_key: string;
  expected_version: number | null;
  ttl_seconds: number | null;
  new_owner?: string | null;
  new_idempotency_key?: string | null;
  transfer_claim?: boolean;
  registered_agents: readonly string[];
  now: Date;
}

export async function executeCanonicalTaskLeaseLifecycle(store: AuthorityStore, raw: CanonicalTaskLeaseLifecycleInput,
  beforeCommit?: (lease: JsonObject | null) => Promise<void>): Promise<JsonObject> {
  const contract = CONTRACTS[raw.operation];
  if (!contract) throw new AuthorityStoreProtocolError("unsupported canonical lease operation");
  const failed = (code: string, reason: string): JsonObject & {schema_version: string} =>
    ({schema_version: contract.result, status: "failed", changed: false, reason_code: code, reason, failure_stage: "validation"});
  let input: CanonicalTaskLeaseLifecycleInput;
  let command: TaskLeaseLifecycleDecisionCommand;
  try {
    input = {...raw, goal_id: normalizeGoalId(raw.goal_id), todo_id: normalizeTodoId(raw.todo_id),
      owner: normalizeOwner(raw.owner), idempotency_key: normalizeIdempotencyKey(raw.idempotency_key),
      ttl_seconds: raw.operation === "release" ? null : normalizeTtl(raw.ttl_seconds),
      registered_agents: raw.registered_agents.map(normalizeOwner)};
    if (input.expected_version === null) return failed("version_required", `task lease ${input.operation} requires the current lease version`);
    if (!Number.isSafeInteger(input.expected_version) || input.expected_version < 0) return failed("invalid_expected_version", "lease version must be a non-negative safe integer");
    if (!(input.now instanceof Date) || !Number.isFinite(input.now.valueOf())) return failed("invalid_clock", "lease mutation requires a valid runtime clock");
    if (raw.transfer_claim !== undefined && (typeof raw.transfer_claim !== "boolean" || raw.operation !== "transfer")) {
      return failed("invalid_canonical_lifecycle_request", "transfer_claim is a boolean valid only for transfer");
    }
    if ((input.operation !== "transfer" && (raw.new_owner != null || raw.new_idempotency_key != null)) ||
        (input.operation === "release" && raw.ttl_seconds != null)) {
      return failed("invalid_canonical_lifecycle_request", "lease operation received unrelated mutation fields");
    }
    command = {operation: input.operation, owner: input.owner, idempotency_key: input.idempotency_key,
      expected_version: input.expected_version, ttl_seconds: input.ttl_seconds,
      new_owner: input.operation === "transfer" ? normalizeOwner(input.new_owner) : null,
      new_idempotency_key: input.operation === "transfer" ? normalizeIdempotencyKey(input.new_idempotency_key) : null};
  } catch (error) {
    return failed(error instanceof TaskLeaseAcquireError ? error.code : "invalid_canonical_lifecycle_request",
      error instanceof Error ? error.message : "invalid canonical lease mutation");
  }
  const identityInput = {...command, goal_id: input.goal_id, todo_id: input.todo_id,
    ...(input.transfer_claim ? {transfer_claim: true} : {})};
  const identity = {schema_version: contract.receipt,
    operation_id: `lease-${input.operation}:${taskLeaseOperationIdentity(identityInput)!}`,
    goal_id: input.goal_id, request_sha256: taskLeaseOperationRequestDigest(identityInput)!};
  const receipt = new CoordinationCommandReceipt({result_schema: contract.result, identity, failure: failed,
    decode(original) {
      const payload = commandReceiptResult(original), fields = payload.fields;
      if (input.operation === "release" && fields.missing === true) {
        if (fields.released !== false || fields.lease != null || payload.changed || input.expected_version !== 0) {
          throw new AuthorityStoreProtocolError("missing-lease receipt does not match release intent");
        }
        return {...payload, fields: {...fields, operation_id: identity.operation_id}};
      }
      let lease: LeaseRecord;
      try {
        lease = canonicalTaskLease(canonicalAuthorityObject(fields.lease, "lease receipt record"), input.goal_id, input.todo_id);
        leaseIsActive(lease, new Date(0)); // Validate time syntax, not present-day authority.
      } catch (error) {
        throw new AuthorityStoreProtocolError(error instanceof Error ? error.message : "invalid lease receipt record");
      }
      const released = input.operation === "release";
      if (fields[contract.field] !== true || (!released && !payload.changed) ||
          lease.owner !== (command.new_owner ?? input.owner) ||
          lease.status !== (released ? "released" : "active") ||
          lease.idempotency_key !== (command.new_idempotency_key ?? input.idempotency_key) ||
          leaseVersion(lease) !== input.expected_version! + (released ? 0 : 1)) {
        throw new AuthorityStoreProtocolError("canonical lease receipt does not match its intent");
      }
      if (input.transfer_claim && (fields.transfer_claim !== true || fields.claimed_by !== command.new_owner ||
          fields.todo_id !== input.todo_id || fields.todo_changed !== (input.owner !== command.new_owner))) {
        throw new AuthorityStoreProtocolError("claim transfer receipt does not match its intent");
      }
      return {...payload, fields: {...fields, operation_id: identity.operation_id}};
    }});
  let commit: AuthorityStoreCommit;
  try {
    const replay = await receipt.read(store);
    if (replay !== null) return replay;
    const observation = await receipt.observe(store);
    if (observation.kind === "receipt") return observation.result;
    const head = observation.authority;
    if (head.status !== "loaded") return {schema_version: contract.result, ...head, failure_stage: "validation", changed: false};
    const index = indexCoordinationProjection(head.head, input.goal_id);
    validateCoordinationTodoReadModel(head.head, input.goal_id);
    const todo = index.todos.get(input.todo_id), rawLease = index.leases.get(input.todo_id);
    const lease = rawLease ? canonicalTaskLease(rawLease, input.goal_id, input.todo_id) : null;
    const excluded = todo?.excluded_agents ?? [];
    if (!Array.isArray(excluded) || excluded.some(value => typeof value !== "string")) return failed("invalid_coordination_projection", "Todo exclusions must be strings");
    const mode = requireStringLiteral(head.head.handoff_mode ?? "legacy", HANDOFF_MODES, "canonical handoff_mode");
    const claim = planLeaseClaimTransfer({requested: input.transfer_claim === true, handoff_mode: mode,
      todo, owner: input.owner, new_owner: command.new_owner, registered_agents: input.registered_agents,
      updated_at: utcIsoformat(input.now)});
    if (claim.status === "rejected") return failed(claim.code, `canonical claim transfer rejected: ${claim.code}`);
    const decision = decideTaskLeaseLifecycle({handoff_mode: mode, registered_agents: input.registered_agents,
      todo: canonicalLeaseTodoFact(claim.todo),
      lease: lease ? {present: true, active: leaseIsActive(lease, input.now), status: String(lease.status),
        owner: normalizeOwner(lease.owner), idempotency_key: normalizeIdempotencyKey(lease.idempotency_key),
        version: leaseVersion(lease), lease_epoch: leaseEpoch(lease), write_scopes: (lease.write_scopes ?? []) as string[], acquire_ttl_seconds: null} : null,
      command});
    if (decision.outcome === "rejected" || decision.outcome === "conflict") {
      return {...failed(decision.code, `canonical task lease ${input.operation} rejected: ${decision.code}`),
        handoff_mode: mode, expected_version: input.expected_version, actual_version: leaseVersion(lease),
        ...(todo ? {todo_status: todo.status, claimed_by: todo.claimed_by ?? null, excluded_agents: excluded} : {})};
    }
    const changed = decision.outcome === "apply";
    const next = changed && lease ? materializeTaskLeaseLifecycle(lease, command, decision, input.now) : lease;
    if (changed && !next) throw new AuthorityStoreProtocolError("applied lease decision lacks its record");
    const mutations: CoordinationProjectionMutation[] = [
      ...(changed ? [{kind: "lease_upsert" as const, lease: next!}] : []),
      ...(claim.status === "transfer" ? [{kind: "todo_upsert" as const, todo: claim.todo}] : []),
    ];
    commit = changed
      ? prepareCoordinationProjectionCommit({goal_id: input.goal_id, operation_id: identity.operation_id,
          expected_provider_revision: head.provider_revision, projection: head.head, mutations})
      : {operation_id: identity.operation_id, expected_provider_revision: head.provider_revision,
          next_projection: head.head, events: [], receipts: []};
    commit.receipts = [{...identity, result: {changed, [contract.field]: next !== null,
      ...(input.transfer_claim ? {transfer_claim: true, todo_id: input.todo_id,
        claimed_by: command.new_owner, todo_changed: claim.status === "transfer"} : {}),
      ...(next ? {lease: next} : {missing: true}), handoff_mode: mode}}];
    // Even a successful no-op seals its identity under the same source/CAS fence.
    await beforeCommit?.(next);
  } catch (error) {
    return failed(error instanceof TaskLeaseAcquireError ? error.code : "invalid_canonical_lifecycle_state",
      error instanceof Error ? error.message : "canonical lease state could not be read");
  }
  try {
    const result = await receipt.commit(store, commit);
    return ["applied", "no_change", "replayed", "recovered"].includes(String(result.status))
      ? result : {...result, failure_stage: "durable_writeback"};
  } catch {
    return {schema_version: contract.result, status: "ambiguous", changed: false,
      reason_code: "canonical_lease_recovery_required", reason: "lease response is uncertain; recover the same operation",
      recovery: {operation_id: identity.operation_id, retry_with_same_operation_id: true}, failure_stage: "durable_writeback"};
  }
}
