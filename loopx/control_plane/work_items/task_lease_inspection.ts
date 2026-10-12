/** Read-only lease inspection: one source, one clock, shared execution eligibility. */
import {isAbsolute, join} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {canonicalAuthoritySha256, AuthorityStoreProtocolError} from "../coordination/authority_store_codec.ts";
import {indexCoordinationProjection, validateCoordinationTodoReadModel} from "../coordination/coordination_projection.ts";
import {loadLegacyCoordinationWriterFence} from "../coordination/legacy_writer_fence.ts";
import {canonicalLeaseTodoFact, canonicalTaskLease} from "../coordination/task_lease_state.ts";
import {openLocalAuthorityStoreHandle, localAuthorityOpenFailure,
  type LocalAuthorityProviderDependencies} from "../coordination/local_authority_provider.ts";
import {decodeTaskLeaseAuthority, leaseIsActive, normalizeGoalId, normalizeTodoId, normalizeHandoffMode,
  readLease, revalidateAuthoritySources, TaskLeaseAcquireError, TASK_LEASE_SCHEMA_VERSION,
  type LeaseRecord, type TodoFact} from "./task_lease_acquire.ts";
import {leaseOwnerConstraint} from "./task_lease_eligibility.ts";
import {leaseRepositoryRejection} from "./task_lease_repository.ts";
import {todoExecutionDependencyRejection} from "../coordination/todo_execution_dependency.ts";

export const TASK_LEASE_INSPECT_REQUEST = "loopx_task_lease_inspect_request_v0";

export interface TaskLeaseInspectionDependencies {
  now?: () => Date;
  authorityProvider?: LocalAuthorityProviderDependencies;
}

/** Observation never grants a lease or substitutes for mutation-time proof. */
export async function inspectTaskLease(value: unknown,
  dependencies: TaskLeaseInspectionDependencies = {}): Promise<JsonObject> {
  let evidence: JsonObject = {};
  let goalId: string | null = null, todoId: string | null = null;
  try {
    const input = requireJsonObject(value, "task lease inspection");
    if (input.schema_version !== TASK_LEASE_INSPECT_REQUEST ||
        (input.source !== "canonical" && input.source !== "legacy") ||
        (input.phase !== undefined && input.phase !== "lease_record" && input.phase !== "effective_lease") ||
        (input.source === "canonical" && input.phase === "lease_record") ||
        Object.keys(input).some(key => !["schema_version", "source", "goal_id", "todo_id", "runtime_root", "authority", "phase"].includes(key))) {
      throw new TaskLeaseAcquireError("invalid task lease inspection request", "invalid_inspection_request");
    }
    goalId = normalizeGoalId(input.goal_id); todoId = normalizeTodoId(input.todo_id);
    if (typeof input.runtime_root !== "string" || !isAbsolute(input.runtime_root) ||
        input.runtime_root.trim() !== input.runtime_root) {
      throw new TaskLeaseAcquireError("runtime_root must be an absolute path", "invalid_runtime_root");
    }
    const root = input.runtime_root, canonical = input.source === "canonical";
    if (canonical) evidence = {source_authority: null, legacy_fallback_used: false};
    const beforeFence = await loadLegacyCoordinationWriterFence(root, goalId);
    if (beforeFence.status === "failed") throw new TaskLeaseAcquireError(beforeFence.reason, beforeFence.reason_code);
    if ((beforeFence.status === "loaded") !== canonical) {
      throw new TaskLeaseAcquireError("lease authority route changed; retry inspection", "authority_source_changed");
    }
    const rawAuthority = requireJsonObject(input.authority, "authority");
    // Canonical mode/Todos come only from the provider. Invalid legacy mode is
    // omitted from this diagnostic response, as in the historical inspect API.
    const authority = decodeTaskLeaseAuthority({...rawAuthority, handoff_mode: "legacy",
      ...(canonical ? {todos: [], todo_projection_error: null} : {})});
    await revalidateAuthoritySources(authority.source_receipts);
    const at = dependencies.now?.() ?? new Date();
    if (!Number.isFinite(at.valueOf())) throw new TaskLeaseAcquireError("invalid inspection clock", "invalid_inspection_clock");
    let lease: LeaseRecord | null, todo: TodoFact | null, mode: string | null, leasePath: string | null;
    let dependency: ReturnType<typeof todoExecutionDependencyRejection> = null;
    if (canonical) {
      const {store, sourceAuthority} = await openLocalAuthorityStoreHandle(root, goalId, dependencies.authorityProvider);
      evidence.source_authority = sourceAuthority;
      const loaded = await store.loadAuthority();
      if (loaded.status !== "loaded") {
        throw new TaskLeaseAcquireError("canonical lease snapshot is unavailable", "local_authority_snapshot_unavailable", {...loaded});
      }
      const index = indexCoordinationProjection(loaded.head, goalId);
      validateCoordinationTodoReadModel(loaded.head, goalId);
      const rawLease = index.leases.get(todoId);
      lease = rawLease ? canonicalTaskLease(rawLease, goalId, todoId) : null;
      todo = canonicalLeaseTodoFact(index.todos.get(todoId));
      dependency = todoExecutionDependencyRejection(index.todos, todoId, at);
      mode = normalizeHandoffMode(loaded.head.handoff_mode);
      leasePath = null;
      evidence.provider_revision = loaded.provider_revision;
    } else {
      leasePath = join(root, "goals", goalId, "task-leases", `${todoId}.json`);
      lease = await readLease(leasePath);
      todo = authority.todos.get(todoId) ?? null;
      try { mode = normalizeHandoffMode(rawAuthority.handoff_mode); }
      catch (error) {
        if (!(error instanceof TaskLeaseAcquireError) || error.code !== "invalid_handoff_mode") throw error;
        mode = null;
      }
    }
    const timeActive = leaseIsActive(lease, at);
    const needsProjection = !canonical && input.phase === "lease_record" && timeActive;
    const ownerConstraint = !timeActive || lease === null || needsProjection ? null
      : authority.todo_projection_error !== null
        ? {effective: false, reason: authority.todo_projection_error.code}
        : leaseOwnerConstraint(todo, typeof lease.owner === "string" ? lease.owner : null, authority.registered_agents);
    const repositoryRejection = canonical && ownerConstraint?.effective === true ? leaseRepositoryRejection(todo, lease) : null;
    const constraint = repositoryRejection !== null ? {effective: false, reason: repositoryRejection}
      : dependency !== null && ownerConstraint?.effective === true
        ? {effective: false, reason: dependency.code} : ownerConstraint;
    // Both registration and route must still describe the source we inspected.
    // No lock is held and no promise is made about later commits or expiry.
    await revalidateAuthoritySources(authority.source_receipts);
    const afterFence = await loadLegacyCoordinationWriterFence(root, goalId);
    if (canonicalAuthoritySha256(afterFence) !== canonicalAuthoritySha256(beforeFence)) {
      throw new TaskLeaseAcquireError("lease authority route changed; retry inspection", "authority_source_changed");
    }
    if (needsProjection) return {ok: true, schema_version: TASK_LEASE_SCHEMA_VERSION,
      action: "inspect", todo_projection_required: true};
    return {ok: true, schema_version: TASK_LEASE_SCHEMA_VERSION, action: "inspect",
      goal_id: goalId, todo_id: todoId, active: timeActive && constraint?.effective === true,
      lease, lease_path: leasePath, ...evidence,
      ...(mode === null ? {} : {handoff_mode: mode}),
      ...(constraint?.effective === false ? {executor_constraint: constraint} : {}),
      ...(dependency === null ? {} : {resume_condition: dependency.condition})};
  } catch (error) {
    const opening = localAuthorityOpenFailure(error);
    return {ok: false, schema_version: TASK_LEASE_SCHEMA_VERSION, action: "inspect",
      goal_id: goalId, todo_id: todoId, ...evidence,
      ...(error instanceof TaskLeaseAcquireError ? error.payload : {}), ...opening,
      error_code: typeof opening.reason_code === "string" ? opening.reason_code
        : error instanceof TaskLeaseAcquireError ? error.code
          : error instanceof AuthorityStoreProtocolError ? "local_authority_snapshot_invalid" : "task_lease_inspection_unavailable",
      error: error instanceof Error ? error.message : "task lease inspection unavailable"};
  }
}
