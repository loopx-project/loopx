import type {AuthorityStore} from "../coordination/authority_store.ts";
import type {JsonObject} from "../effect_program.ts";
import {canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {openLocalAuthorityStoreHandle, localAuthorityOpenFailure,
  type LocalAuthorityProviderDependencies} from "../coordination/local_authority_provider.ts";
import {loadLegacyCoordinationWriterFence} from "../coordination/legacy_writer_fence.ts";
import {
  canonicalAuthorityRouteFailure,
  withCanonicalWriter,
  type CanonicalBusinessWrite,
} from "../coordination/local_authority_write.ts";
import {ShadowManagementError} from "../coordination/shadow_management.ts";
import {EffectRuntimeLockTimeoutError} from "../effect_runtime_errors.ts";
import {executeCanonicalTaskLeaseLifecycle} from "../coordination/task_lease_lifecycle.ts";
import {revalidateAuthoritySources, TaskLeaseAcquireError, type AuthorityFacts} from "./task_lease_acquire.ts";
import type {TaskLeaseLifecycleDecisionOperation} from "./task_lease_lifecycle_decision.ts";

export interface LocalLeaseRequest {
  operation: TaskLeaseLifecycleDecisionOperation;
  runtime_root: string; goal_id: string; todo_id: string;
  owner: string | null; idempotency_key: string | null;
  expected_version: number | null; ttl_seconds: number | null;
  new_owner: string | null; new_idempotency_key: string | null;
  transfer_claim?: boolean;
  authority: AuthorityFacts | null;
}

export interface LocalTaskLeaseDependencies {
  now: () => Date;
  beforeWrite?: (lease: JsonObject) => void | Promise<void>;
  authorityProvider?: LocalAuthorityProviderDependencies;
}

type CanonicalTaskLeaseBusinessOperation =
  | "acquire"
  | TaskLeaseLifecycleDecisionOperation;

const TASK_LEASE_BUSINESS_WRITES = {
  acquire: "task_lease_acquire",
  renew: "task_lease_renew",
  transfer: "task_lease_transfer",
  release: "task_lease_release",
} satisfies Record<CanonicalTaskLeaseBusinessOperation, CanonicalBusinessWrite>;

/** The same local promotion/source fence surrounds acquire and maintenance. */
export async function withCanonicalTaskLeaseAuthority(
  request: {runtime_root: string; goal_id: string; operation: CanonicalTaskLeaseBusinessOperation;
    owner: string | null; idempotency_key: string | null; authority: AuthorityFacts | null},
  dependencies: LocalTaskLeaseDependencies,
  execute: (store: AuthorityStore, guards: {
    revalidate: () => Promise<void>; beforeCommit: (lease: JsonObject | null) => Promise<void>;
  }) => Promise<JsonObject>): Promise<JsonObject> {
  const root = request.runtime_root, goalId = request.goal_id;
  const evidence: JsonObject = {source_authority: null, decision_read_from_provider: false, legacy_fallback_used: false};
  const rejected = (code: string, reason: string): JsonObject => ({status: "failed", changed: false,
    reason_code: code, reason, failure_stage: "validation", ...evidence});
  const businessWrite = TASK_LEASE_BUSINESS_WRITES[request.operation];
  try {
    return await withCanonicalWriter(root, goalId, false, async () => {
      const initialFence = await loadLegacyCoordinationWriterFence(root, goalId);
      if (initialFence.status !== "loaded") {
        throw new TaskLeaseAcquireError(
          "canonical lease writer fence changed; inspect authority before retrying",
          `canonical_${request.operation}_fence_changed`,
          {goal_id: goalId},
        );
      }
      const verifyFence = async () => {
        const current = await loadLegacyCoordinationWriterFence(root, goalId);
        if (current.status !== "loaded" || canonicalAuthoritySha256(current.fence) !== canonicalAuthoritySha256(initialFence.fence)) {
          throw new TaskLeaseAcquireError("canonical lease writer fence changed; inspect authority before retrying", `canonical_${request.operation}_fence_changed`, {goal_id: goalId});
        }
      };
      await verifyFence();
      const {store, sourceAuthority} = await openLocalAuthorityStoreHandle(root, goalId, dependencies.authorityProvider);
      evidence.source_authority = sourceAuthority;
      if (!request.owner || !request.idempotency_key || (request.operation !== "release" && !request.authority)) {
        return rejected("authority_required", "canonical lease mutation needs registered actor context");
      }
      evidence.decision_read_from_provider = true;
      const revalidate = async () => {
        await verifyFence();
        // Release relinquishes an existing proof even after actor deregistration.
        if (request.operation !== "release" && request.authority) {
          await revalidateAuthoritySources(request.authority.source_receipts);
        }
      };
      const result = await execute(store, {revalidate, beforeCommit: async lease => {
        if (lease) await dependencies.beforeWrite?.(lease);
        await revalidate();
      }});
      return {...result, ...evidence};
    }, {businessWrite});
  } catch (error) {
    if (error instanceof ShadowManagementError || error instanceof TaskLeaseAcquireError) return {...rejected(error.code, error.message), ...error.payload};
    if (error instanceof EffectRuntimeLockTimeoutError) return rejected("lock_acquire_timeout", "canonical lease maintenance lock timed out");
    return {...rejected("canonical_lease_route_failed", error instanceof Error ? error.message : "canonical lease route failed"),
      ...localAuthorityOpenFailure(error), ...canonicalAuthorityRouteFailure(error)};
  }
}

export async function mutateCanonicalTaskLease(request: LocalLeaseRequest,
  dependencies: LocalTaskLeaseDependencies): Promise<JsonObject> {
  return await withCanonicalTaskLeaseAuthority(request, dependencies, (store, guards) =>
    executeCanonicalTaskLeaseLifecycle(store, {operation: request.operation,
      goal_id: request.goal_id, todo_id: request.todo_id, owner: request.owner!, idempotency_key: request.idempotency_key!,
      expected_version: request.expected_version, ttl_seconds: request.ttl_seconds,
      new_owner: request.new_owner, new_idempotency_key: request.new_idempotency_key,
      ...(request.transfer_claim ? {transfer_claim: true} : {}),
      registered_agents: request.authority?.registered_agents ?? [], now: dependencies.now()}, guards.beforeCommit));
}
