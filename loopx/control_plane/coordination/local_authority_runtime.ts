import {registryAuthoritySourceCheck} from "./authority_source.ts";
import {decodeTaskLeaseProof} from "./task_lease_proof.ts";
import {COORDINATION_TODO_ARCHIVE_RESULT_SCHEMA} from "./todo_archive.ts";
import {readCoordinationOwnership} from "./ownership_observation.ts";
import {executeTodoContinuation} from "./todo_continuation.ts";
import {withCanonicalWriter} from "./local_authority_write.ts";
import { ShadowManagementError, withShadowMaintenanceLock } from "./shadow_management.ts";
import { isAbsolute, join } from "node:path";
import {readFile, realpath} from "node:fs/promises";
import {createHash} from "node:crypto";

import type { JsonObject } from "../effect_program.ts";
import {acceptanceWorkGuard, projectGoalAcceptance} from "../goals/acceptance_contract.ts";
import {decodeMonitorPollObservation} from "../todos/monitor_metadata.ts";
import {executeCoordinationMonitorPoll, COORDINATION_MONITOR_POLL_REQUEST_SCHEMA, COORDINATION_LEASED_MONITOR_POLL_REQUEST_SCHEMA, COORDINATION_WITNESSED_MONITOR_POLL_REQUEST_SCHEMA,
  COORDINATION_MONITOR_POLL_RESULT_SCHEMA} from "./todo_monitor_poll.ts";
import { requireJsonObject } from "../runtime_decode.ts";
import {
  LOCAL_COORDINATION_PROMOTION_RECEIPT_SCHEMA,
  LOCAL_COORDINATION_PROMOTION_REQUEST_SCHEMA,
  LOCAL_COORDINATION_PROMOTION_RESULT_SCHEMA,
  LOCAL_COORDINATION_PROMOTION_REVIEW_REQUEST_SCHEMA,
  LOCAL_COORDINATION_PROMOTION_REVIEW_RESULT_SCHEMA,
  LOCAL_COORDINATION_TODO_LIST_REQUEST_SCHEMA,
  LOCAL_COORDINATION_TODO_LIST_RESULT_SCHEMA,
  LOCAL_COORDINATION_TODO_READ_REQUEST_SCHEMA,
  LOCAL_COORDINATION_TODO_READ_RESULT_SCHEMA,
} from "./coordination_state_contract.generated.ts";
import {
  indexCoordinationProjection,
  indexCoordinationProjectionTodos,
  validateCoordinationTodoReadModel,
} from "./coordination_projection.ts";
import { authorityStoreSourceAuthority, type AuthorityStore, type AuthorityStoreReceiptResult } from "./authority_store.ts";
import {
  canonicalAuthorityBytes,
  canonicalAuthorityObject,
  canonicalAuthoritySha256,
  requireAuthorityStoreId,
} from "./authority_store_codec.ts";
import { FileAuthorityStore } from "./file_authority_store.ts";
import {
  openLocalAuthorityStore,
  localAuthorityOpenFailure,
  type LocalAuthorityProviderDependencies,
} from "./local_authority_provider.ts";
import {
  decodeLegacyCoordinationWriterFence,
  engageLegacyCoordinationWriterFenceUnderLocks,
  LEGACY_COORDINATION_WRITER_FENCE_SCHEMA,
  loadLegacyCoordinationWriterFence,
} from "./legacy_writer_fence.ts";
import {
  COORDINATION_RUNTIME_SHADOW_QUALIFY_REQUEST_SCHEMA,
  decodeRuntimeShadowRequest,
  qualifyCoordinationRuntimeShadowUnderLocks,
  qualifyCoordinationRuntimeShadow,
  withShadowSourceLocks,
} from "./runtime_shadow.ts";
import {
  COORDINATION_TODO_CLAIM_RESULT_SCHEMA,
  executeCoordinationTodoClaim,
} from "./todo_claim.ts";
import {
  COORDINATION_TODO_CREATE_RESULT_SCHEMA,
  executeCoordinationTodoCreate,
} from "./todo_create.ts";
import {
  COORDINATION_TODO_UPDATE_REQUEST_SCHEMA,
  COORDINATION_TODO_PLANNING_UPDATE_REQUEST_SCHEMA,
  COORDINATION_TODO_REVIEWED_UPDATE_REQUEST_SCHEMA,
  COORDINATION_TODO_COMPLETION_UPDATE_REQUEST_SCHEMA,
  COORDINATION_TODO_OBSERVATION_UPDATE_REQUEST_SCHEMA,
  COORDINATION_TODO_UPDATE_RESULT_SCHEMA,
  executeCoordinationTodoUpdate,
} from "./todo_update.ts";
import {
  COORDINATION_TODO_TERMINAL_LIFECYCLE_RESULT_SCHEMA,
  executeCoordinationTodoTerminalLifecycle,
} from "./todo_terminal_lifecycle.ts";
import {
  acknowledgeLocalArchiveAttempt,
  executeLocalArchiveAttempt,
  LOCAL_TODO_ARCHIVE_ACK_RESULT_SCHEMA,
} from "./local_archive_attempt.ts";
import {
  normalizeIdempotencyKey,
  normalizeTtl,
} from "../work_items/task_lease_acquire.ts";
import { compactPythonWhitespace } from "./todo_agents.ts";

export const LOCAL_COORDINATION_TODO_CLAIM_REQUEST_SCHEMA =
  "loopx_local_coordination_todo_claim_request_v0";
export const LOCAL_COORDINATION_TODO_CLAIM_WITNESSED_REQUEST_SCHEMA = "loopx_local_coordination_todo_claim_request_v1";
export const LOCAL_COORDINATION_TODO_CREATE_REQUEST_SCHEMA =
  "loopx_local_coordination_todo_create_request_v0";
export const LOCAL_COORDINATION_TODO_CREATE_WITNESSED_REQUEST_SCHEMA = "loopx_local_coordination_todo_create_request_v1";
export const LOCAL_COORDINATION_TODO_TERMINAL_LIFECYCLE_REQUEST_SCHEMA =
  "loopx_local_coordination_todo_terminal_lifecycle_request_v0";
export const LOCAL_COORDINATION_TODO_TERMINAL_LIFECYCLE_WITNESSED_REQUEST_SCHEMA = "loopx_local_coordination_todo_terminal_lifecycle_request_v1";
export const LOCAL_COORDINATION_TODO_ARCHIVE_REQUEST_SCHEMA =
  "loopx_local_coordination_todo_archive_request_v0";
export const LOCAL_COORDINATION_TODO_ARCHIVE_ACK_REQUEST_SCHEMA =
  "loopx_local_coordination_todo_archive_ack_request_v0";
export {
  LOCAL_COORDINATION_PROMOTION_RECEIPT_SCHEMA,
  LOCAL_COORDINATION_PROMOTION_REQUEST_SCHEMA,
  LOCAL_COORDINATION_PROMOTION_RESULT_SCHEMA,
  LOCAL_COORDINATION_PROMOTION_REVIEW_REQUEST_SCHEMA,
  LOCAL_COORDINATION_PROMOTION_REVIEW_RESULT_SCHEMA,
  LOCAL_COORDINATION_TODO_LIST_REQUEST_SCHEMA,
  LOCAL_COORDINATION_TODO_LIST_RESULT_SCHEMA,
  LOCAL_COORDINATION_TODO_READ_REQUEST_SCHEMA,
  LOCAL_COORDINATION_TODO_READ_RESULT_SCHEMA,
} from "./coordination_state_contract.generated.ts";
export { LEGACY_COORDINATION_WRITER_FENCE_SCHEMA } from "./legacy_writer_fence.ts";

export function sourceAuthorityFor(store: AuthorityStore) {
  return authorityStoreSourceAuthority(store);
}

/**
 * Reviewed operator path for a whole-Goal coordination-authority cutover.
 * Preview is effect-free.
 * Execute revalidates the exact legacy snapshot, qualifies the exact shadow
 * lineage, fences legacy writers and seeds canonical authority while holding
 * the shared maintenance/source locks.
 */
export async function reviewLocalCoordinationAuthorityPromotion(
  value: unknown,
  dependencies: LocalAuthorityRuntimeDependencies = {},
): Promise<JsonObject> {
  const schema = LOCAL_COORDINATION_PROMOTION_REVIEW_RESULT_SCHEMA;
  let writerFenceVerified = false;
  const fenceEvidence: {current: {runtimeRoot: string; goalId: string; fence: JsonObject} | null} = {
    current: null,
  };
  try {
    const input = decodeRuntimeShadowRequest(
      value,
      LOCAL_COORDINATION_PROMOTION_REVIEW_REQUEST_SCHEMA,
      ["operation_id", "minimum_operations", "required_event_kinds", "execute"],
    );
    const operationId = requireAuthorityStoreId(input.operation_id, "operation id");
    const minimumOperations = requiredPositiveSafeInteger(
      input.minimum_operations,
      "minimum_operations",
    );
    const requiredEventKinds = requiredUniqueStrings(
      input.required_event_kinds,
      "required_event_kinds",
    );
    if (typeof input.execute !== "boolean") throw new Error("execute must be a JSON boolean");
    const statePath = await realpath(String(input.source_snapshot.state_path));
    const shadow = dependencies.createShadowStore?.(
      shadowDirectory(input.runtime_root),
      input.goal_id,
    ) ?? new FileAuthorityStore(shadowDirectory(input.runtime_root), input.goal_id, { existingOnly: true });
    const canonical = dependencies.createCanonicalStore?.(
      authorityDirectory(input.runtime_root),
      input.goal_id,
    ) ?? await openRuntimeStore(input.runtime_root, input.goal_id, dependencies);
    const canonicalAuthority = sourceAuthorityFor(canonical);

    return await withShadowMaintenanceLock(input.runtime_root, input.goal_id, () =>
      withShadowSourceLocks(input, async () => {
        const qualification = await qualifyCoordinationRuntimeShadowUnderLocks(
          input,
          { createStore: () => shadow },
          minimumOperations,
          requiredEventKinds,
        );
        const head = qualification.head as JsonObject | undefined;
        const publicQualification = { ...qualification };
        delete publicQualification.head;
        if (qualification.status !== "qualified" || qualification.qualified !== true || head === undefined) {
          return {
            schema_version: schema,
            status: "not_ready",
            executed: false,
            reason_code: "local_authority_shadow_not_qualified",
            qualification: publicQualification,
            legacy_writer_fenced: false,
            legacy_fallback_used: false,
          };
        }
        if (head.handoff_mode !== "hard_lease") return {
          schema_version: schema,
          status: "not_ready",
          executed: false,
          reason_code: "local_authority_promotion_requires_hard_lease",
          reason: "v0 whole-Goal coordination-authority promotion requires an already-qualified hard_lease Goal",
          qualification: publicQualification,
          legacy_writer_fenced: false,
          legacy_fallback_used: false,
        };
        const providerRevision = requireAuthorityStoreId(
          qualification.provider_revision,
          "qualified shadow provider revision",
        );
        const projectionSha256 = canonicalAuthoritySha256(head);
        const fence = canonicalAuthorityObject({
          schema_version: LEGACY_COORDINATION_WRITER_FENCE_SCHEMA,
          state: "engaged",
          goal_id: input.goal_id,
          fence_id: `legacy-writer-fence:${operationId}`,
          source_version: `shadow:${providerRevision}`,
          source_projection_sha256: projectionSha256,
          expected_shadow_provider_revision: providerRevision,
        }, "reviewed legacy writer fence");
        const request: LocalCoordinationPromotionRequest = {
          runtime_root: input.runtime_root,
          goal_id: input.goal_id,
          operation_id: operationId,
          expected_shadow_provider_revision: providerRevision,
          expected_shadow_projection_sha256: projectionSha256,
          minimum_operations: minimumOperations,
          required_event_kinds: requiredEventKinds,
          writer_fence: fence,
        };
        fenceEvidence.current = {runtimeRoot: input.runtime_root, goalId: input.goal_id, fence};
        const existing = await canonical.loadAuthority();
        if (existing.status === "loaded") {
          const readback = await promotionReadback(canonical, request);
          return readback.matched
            ? {
              schema_version: schema,
              ...promotionResult(request, "replayed", readback, canonicalAuthority),
              executed: input.execute,
              qualification: publicQualification,
            }
            : {
              schema_version: schema,
              status: "failed",
              executed: false,
              reason_code: readback.reason_code ?? "local_authority_already_initialized",
              reason: "canonical local authority is already initialized by different content",
              legacy_writer_fenced: true,
              legacy_fallback_used: false,
            };
        }
        if (existing.status !== "missing") return {
          schema_version: schema,
          ...existing,
          executed: false,
          legacy_writer_fenced: false,
          legacy_fallback_used: false,
        };
        const persistedFence = await loadLegacyCoordinationWriterFence(
          input.runtime_root,
          input.goal_id,
        );
        const recoveringFromFence = persistedFence.status === "loaded";
        if (persistedFence.status === "failed") return {
          schema_version: schema,
          status: "failed",
          executed: false,
          reason_code: persistedFence.reason_code,
          reason: persistedFence.reason,
          legacy_writer_fenced: false,
          legacy_fallback_used: false,
        };
        if (recoveringFromFence) {
          writerFenceVerified = canonicalAuthorityBytes(persistedFence.fence).equals(
            canonicalAuthorityBytes(fence),
          );
          if (!writerFenceVerified) return {
            schema_version: schema,
            status: "failed",
            executed: false,
            reason_code: "local_authority_writer_fence_conflict",
            reason: "durable legacy writer fence belongs to a different reviewed promotion",
            legacy_writer_fenced: true,
            legacy_fallback_used: false,
          };
        }
        const plan = {
          operation_id: operationId,
          canonical_authority: canonicalAuthority,
          expected_shadow_provider_revision: providerRevision,
          expected_shadow_projection_sha256: projectionSha256,
          writer_fence: fence,
          rollback_identity: {
            provider: "file_v0",
            source_shadow_provider_revision: providerRevision,
            source_projection_sha256: projectionSha256,
          },
        };
        if (!input.execute) return {
          schema_version: schema,
          status: "preview_ready",
          executed: false,
          promotion_ready: true,
          plan,
          qualification: publicQualification,
          legacy_writer_fenced: writerFenceVerified,
          legacy_fallback_used: false,
        };
        if (!recoveringFromFence) {
          const fenceResult = await engageLegacyCoordinationWriterFenceUnderLocks(
            input.runtime_root,
            input.goal_id,
            statePath,
            fence,
          );
          if (fenceResult.status !== "applied" && fenceResult.status !== "replayed") return {
            schema_version: schema,
            status: "failed",
            executed: false,
            reason_code: fenceResult.reason_code ?? "local_authority_writer_fence_failed",
            reason: fenceResult.reason ?? "legacy writer fence could not be verified",
            qualification: publicQualification,
            legacy_writer_fenced: false,
            legacy_fallback_used: false,
          };
          writerFenceVerified = true;
        }
        const identity = promotionIdentity(request);
        const committed = await canonical.commitAuthority({
          expected_provider_revision: null,
          operation_id: operationId,
          events: [{
            ...identity,
            schema_version: "loopx_local_coordination_promotion_event_v0",
            mode_transition: `legacy_canonical_to_${canonicalAuthority}`,
          }],
          next_projection: head,
          receipts: [identity],
        });
        const readback = await promotionReadback(canonical, request);
        if (readback.matched) return {
          schema_version: schema,
          ...promotionResult(
            request,
            committed.status === "applied"
              ? recoveringFromFence ? "recovered" : "applied"
              : committed.status === "ambiguous" ? "recovered" : "replayed",
            readback,
            canonicalAuthority,
          ),
          executed: true,
          plan,
          qualification: publicQualification,
        };
        return {
          schema_version: schema,
          status: "failed",
          executed: true,
          reason_code: readback.reason_code ?? "local_authority_promotion_readback_mismatch",
          reason: "promotion did not produce an exact canonical readback",
          reconciliation_required: committed.status === "ambiguous",
          qualification: publicQualification,
          legacy_writer_fenced: true,
          legacy_fallback_used: false,
        };
      }),
    );
  } catch (error) {
    if (!writerFenceVerified && fenceEvidence.current !== null) {
      try {
        const evidence = fenceEvidence.current;
        const persistedFence = await loadLegacyCoordinationWriterFence(
          evidence.runtimeRoot,
          evidence.goalId,
        );
        writerFenceVerified = persistedFence.status === "loaded"
          && canonicalAuthorityBytes(persistedFence.fence).equals(
            canonicalAuthorityBytes(evidence.fence),
          );
      } catch {
        // The result below must not claim a fence that this call could not
        // read back exactly.
      }
    }
    return {
      schema_version: schema,
      status: "failed",
      executed: false,
      reason_code: error instanceof ShadowManagementError
        ? error.reason_code
        : "invalid_local_coordination_promotion_review_request",
      reason: error instanceof Error ? error.message : "promotion review unavailable",
      legacy_writer_fenced: writerFenceVerified,
      legacy_fallback_used: false,
      ...localAuthorityOpenFailure(error),
    };
  }
}

/** Monitor observation and successors share the existing writer/fence lifetime. */
export async function pollLocalCoordinationMonitor(value: unknown,
  dependencies: LocalAuthorityRuntimeDependencies = {}): Promise<JsonObject> {
  const evidence = {source_authority: "file_v0", decision_read_from_provider: true, legacy_fallback_used: false};
  try {
    const input = requireJsonObject(value, "local Monitor poll request");
    if (input.schema_version !== COORDINATION_MONITOR_POLL_REQUEST_SCHEMA &&
        input.schema_version !== COORDINATION_LEASED_MONITOR_POLL_REQUEST_SCHEMA &&
        input.schema_version !== COORDINATION_WITNESSED_MONITOR_POLL_REQUEST_SCHEMA) throw new TypeError("Monitor poll schema mismatch");
    if (input.lease_proof != null && input.schema_version === COORDINATION_MONITOR_POLL_REQUEST_SCHEMA) {
      throw new TypeError("lease-backed Monitor poll requires request v1");
    }
    const authoritySourcesCurrent = registryAuthoritySourceCheck(input,
      input.schema_version === COORDINATION_WITNESSED_MONITOR_POLL_REQUEST_SCHEMA);
    const proof = decodeTaskLeaseProof(input.lease_proof);
    if (input.schema_version === COORDINATION_LEASED_MONITOR_POLL_REQUEST_SCHEMA && !proof) {
      throw new TypeError("Monitor poll request v1 requires lease_proof");
    }
    const root = runtimeRoot(input.runtime_root);
    const goalId = requireAuthorityStoreId(input.goal_id, "goal id");
    if (!Array.isArray(input.registered_agents)) throw new TypeError("registered_agents must be an array");
    const registered = input.registered_agents.map(agent => claimAgentValue(agent, "registered agent"));
    return await withCanonicalWriter(root, goalId, input.dry_run === true, async () => {
      const store = await openRuntimeStore(root, goalId, dependencies);
      evidence.source_authority = sourceAuthorityFor(store);
      return {...await executeCoordinationMonitorPoll(store, {
        goal_id: goalId, operation_id: requireAuthorityStoreId(input.operation_id, "operation id"),
        actor_agent_id: input.actor_agent_id == null ? null : claimAgentValue(input.actor_agent_id, "actor_agent_id"),
        registered_agents: registered, dry_run: input.dry_run as boolean,
        observation: requireJsonObject(input.observation, "Monitor observation"),
        intent: requireJsonObject(input.intent, "Monitor successor intent"),
        lease_proof: proof, now: new Date(),
      }, authoritySourcesCurrent), ...evidence};
    });
  } catch (error) {
    return {schema_version: COORDINATION_MONITOR_POLL_RESULT_SCHEMA, status: "failed", changed: false,
      reason_code: error instanceof ShadowManagementError ? error.reason_code : "invalid_local_monitor_poll_request",
      reason: error instanceof Error ? error.message : String(error), ...evidence,
      ...localAuthorityOpenFailure(error)};
  }
}

interface LocalAuthorityRuntimeDependencies extends LocalAuthorityProviderDependencies {
  createStore?: (directory: string, goalId: string) => AuthorityStore;
  createShadowStore?: (directory: string, goalId: string) => AuthorityStore;
  createCanonicalStore?: (directory: string, goalId: string) => AuthorityStore;
}

/** One runtime seam owns provider construction for every local command. */
async function openRuntimeStore(
  root: string,
  goalId: string,
  dependencies: LocalAuthorityRuntimeDependencies,
): Promise<AuthorityStore> {
  if (dependencies.createStore !== undefined) {
    return dependencies.createStore(authorityDirectory(root), goalId);
  }
  return await openLocalAuthorityStore(root, goalId, dependencies);
}

export function runtimeRoot(value: unknown): string {
  if (typeof value !== "string" || value.trim() !== value || !isAbsolute(value)) {
    throw new Error("runtime_root must be an absolute path");
  }
  return value;
}

function claimAgentValue(value: unknown, label: string): string {
  if (typeof value !== "string" || value.trim().length === 0) {
    throw new Error(`${label} must be a non-empty string`);
  }
  return value;
}

function optionalProseValue(value: unknown, label: string): string | null {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value !== "string") {
    throw new Error(`${label} must be a string or null`);
  }
  return compactPythonWhitespace(value) || null;
}

function claimObservedAt(value: unknown): Date {
  if (typeof value !== "string" || value.trim() !== value) {
    throw new Error("observed_at must be a trimmed ISO-8601 timestamp");
  }
  const observedAt = new Date(value);
  if (Number.isNaN(observedAt.valueOf())) {
    throw new Error("observed_at must be a valid ISO-8601 timestamp");
  }
  return observedAt;
}

function authorityDirectory(root: string): string {
  return join(root, "authority", "file-v0");
}

function shadowDirectory(root: string): string {
  return join(root, "authority-shadow", "file-v0");
}

function requiredPositiveSafeInteger(value: unknown, label: string): number {
  if (!Number.isSafeInteger(value) || Number(value) < 1 || Number(value) > 10_000) {
    throw new Error(`${label} must be a positive safe integer no greater than 10000`);
  }
  return Number(value);
}

function requiredNonNegativeSafeInteger(value: unknown, label: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) {
    throw new TypeError(`${label} must be a non-negative safe integer`);
  }
  return value as number;
}

function archiveRole(value: unknown): "agent" | "user" {
  if (value !== "agent" && value !== "user") throw new TypeError("unsupported archive role");
  return value;
}

function optionalNonNegativeSafeInteger(value: unknown, label: string): number | null {
  return value === null || value === undefined
    ? null
    : requiredNonNegativeSafeInteger(value, label);
}

function requiredUniqueStrings(value: unknown, label: string): string[] {
  if (!Array.isArray(value) || value.length > 32) {
    throw new Error(`${label} must be an array with at most 32 entries`);
  }
  const values = value.map((entry, index) =>
    requireAuthorityStoreId(entry, `${label}[${index}]`)
  );
  if (new Set(values).size !== values.length) throw new Error(`${label} contains duplicates`);
  return values;
}

interface LocalCoordinationPromotionRequest {
  runtime_root: string;
  goal_id: string;
  operation_id: string;
  expected_shadow_provider_revision: string;
  expected_shadow_projection_sha256: string;
  minimum_operations: number;
  required_event_kinds: string[];
  writer_fence: JsonObject;
}

function decodePromotionRequest(value: unknown): LocalCoordinationPromotionRequest {
  const input = requireJsonObject(value, "local coordination promotion request");
  if (input.schema_version !== LOCAL_COORDINATION_PROMOTION_REQUEST_SCHEMA) {
    throw new Error("local coordination promotion request schema mismatch");
  }
  const fence = decodeLegacyCoordinationWriterFence(input.writer_fence);
  return {
    runtime_root: runtimeRoot(input.runtime_root),
    goal_id: requireAuthorityStoreId(input.goal_id, "goal id"),
    operation_id: requireAuthorityStoreId(input.operation_id, "operation id"),
    expected_shadow_provider_revision: requireAuthorityStoreId(
      input.expected_shadow_provider_revision,
      "expected shadow provider revision",
    ),
    expected_shadow_projection_sha256: requireAuthorityStoreId(
      input.expected_shadow_projection_sha256,
      "expected shadow projection sha256",
    ),
    minimum_operations: requiredPositiveSafeInteger(
      input.minimum_operations,
      "minimum_operations",
    ),
    required_event_kinds: requiredUniqueStrings(
      input.required_event_kinds,
      "required_event_kinds",
    ),
    writer_fence: fence,
  };
}

function promotionIdentity(request: LocalCoordinationPromotionRequest): JsonObject {
  return canonicalAuthorityObject({
    schema_version: LOCAL_COORDINATION_PROMOTION_RECEIPT_SCHEMA,
    operation_id: request.operation_id,
    goal_id: request.goal_id,
    source_shadow_provider_revision: request.expected_shadow_provider_revision,
    source_projection_sha256: request.expected_shadow_projection_sha256,
    writer_fence_id: request.writer_fence.fence_id,
    source_version: request.writer_fence.source_version,
  }, "local coordination promotion identity");
}

function matchingReceipt(
  result: AuthorityStoreReceiptResult,
  expected: JsonObject,
): Extract<AuthorityStoreReceiptResult, { status: "found" }> | null {
  return result.status === "found" && result.receipts.length === 1 &&
      canonicalAuthorityBytes(result.receipts[0]).equals(canonicalAuthorityBytes(expected))
    ? result
    : null;
}

async function promotionReadback(
  store: AuthorityStore,
  request: LocalCoordinationPromotionRequest,
): Promise<{ matched: boolean; provider_revision?: string; cursor?: string; reason_code?: string }> {
  const receiptResult = await store.readReceipt(request.operation_id);
  const receipt = matchingReceipt(receiptResult, promotionIdentity(request));
  if (receipt === null) {
    return {
      matched: false,
      reason_code: receiptResult.status === "found"
        ? "local_authority_promotion_identity_mismatch"
        : "local_authority_promotion_receipt_missing",
    };
  }
  const lineage = await store.scanCommitted(null, 1);
  const promotion = lineage.status === "page" ? lineage.transactions[0] : undefined;
  if (
    promotion === undefined ||
    promotion.cursor !== "1" ||
    promotion.operation_id !== request.operation_id ||
    canonicalAuthoritySha256(promotion.projection) !== request.expected_shadow_projection_sha256
  ) {
    return { matched: false, reason_code: "local_authority_promotion_lineage_mismatch" };
  }
  return {
    matched: true,
    provider_revision: receipt.provider_revision,
    cursor: receipt.cursor,
  };
}

function promotionResult(
  request: LocalCoordinationPromotionRequest,
  status: "applied" | "replayed" | "recovered",
  readback: Awaited<ReturnType<typeof promotionReadback>>,
  canonicalAuthority: string,
): JsonObject {
  return {
    schema_version: LOCAL_COORDINATION_PROMOTION_RESULT_SCHEMA,
    status,
    operation_id: request.operation_id,
    provider_revision: readback.provider_revision,
    cursor: readback.cursor,
    source_shadow_provider_revision: request.expected_shadow_provider_revision,
    source_projection_sha256: request.expected_shadow_projection_sha256,
    writer_fence_id: request.writer_fence.fence_id,
    source_version: request.writer_fence.source_version,
    canonical_authority: canonicalAuthority,
    legacy_writer_fenced: true,
    legacy_fallback_used: false,
  };
}

/**
 * Explicit Stage 2C cutover. The shadow must still match the caller's exact
 * qualified revision and digest after the legacy writer fence is engaged.
 * Nothing calls this from a read path, so canonical promotion cannot happen
 * implicitly as a side effect of observing a healthy shadow.
 */
export async function promoteLocalCoordinationAuthority(
  value: unknown,
  dependencies: LocalAuthorityRuntimeDependencies = {},
): Promise<JsonObject> {
  let request: LocalCoordinationPromotionRequest;
  try {
    request = decodePromotionRequest(value);
  } catch (error) {
    return {
      schema_version: LOCAL_COORDINATION_PROMOTION_RESULT_SCHEMA,
      status: "failed",
      reason_code: "invalid_local_coordination_promotion_request",
      reason: error instanceof Error ? error.message : "invalid promotion request",
      legacy_writer_fenced: false,
      legacy_fallback_used: false,
    };
  }
  if (request.writer_fence.source_projection_sha256 !== request.expected_shadow_projection_sha256) {
    return {
      schema_version: LOCAL_COORDINATION_PROMOTION_RESULT_SCHEMA,
      status: "failed",
      reason_code: "local_authority_writer_fence_projection_mismatch",
      reason: "writer fence is not bound to the selected shadow projection",
      legacy_writer_fenced: false,
      legacy_fallback_used: false,
    };
  }
  if (
    request.writer_fence.goal_id !== request.goal_id ||
    request.writer_fence.expected_shadow_provider_revision !==
      request.expected_shadow_provider_revision
  ) {
    return {
      schema_version: LOCAL_COORDINATION_PROMOTION_RESULT_SCHEMA,
      status: "failed",
      reason_code: "local_authority_writer_fence_revision_mismatch",
      reason: "writer fence is not bound to the selected goal and shadow revision",
      legacy_writer_fenced: false,
      legacy_fallback_used: false,
    };
  }

  // Provider opening can fail before durable fence readback. Report only
  // evidence this invocation actually verified, including in the outer catch.
  let writerFenceVerified = false;
  try {
    const shadow = dependencies.createShadowStore?.(
      shadowDirectory(request.runtime_root),
      request.goal_id,
    ) ?? new FileAuthorityStore(shadowDirectory(request.runtime_root), request.goal_id);
    const canonical = dependencies.createCanonicalStore?.(
      authorityDirectory(request.runtime_root),
      request.goal_id,
    ) ?? await openRuntimeStore(request.runtime_root, request.goal_id, dependencies);
    const canonicalAuthority = sourceAuthorityFor(canonical);
    const persistedFence = await loadLegacyCoordinationWriterFence(
      request.runtime_root,
      request.goal_id,
    );
    if (
      persistedFence.status !== "loaded" ||
      !canonicalAuthorityBytes(persistedFence.fence).equals(
        canonicalAuthorityBytes(request.writer_fence),
      )
    ) return {
      schema_version: LOCAL_COORDINATION_PROMOTION_RESULT_SCHEMA,
      status: "failed",
      reason_code: persistedFence.status === "failed"
        ? persistedFence.reason_code
        : "local_authority_writer_fence_not_verified",
      reason: persistedFence.status === "failed"
        ? persistedFence.reason
        : "exact durable legacy writer fence must be engaged before promotion",
      legacy_writer_fenced: false,
      legacy_fallback_used: false,
    };
    writerFenceVerified = true;
    const existing = await canonical.loadAuthority();
    if (existing.status === "loaded") {
      const readback = await promotionReadback(canonical, request);
      return readback.matched
        ? promotionResult(request, "replayed", readback, canonicalAuthority)
        : {
          schema_version: LOCAL_COORDINATION_PROMOTION_RESULT_SCHEMA,
          status: "failed",
          reason_code: readback.reason_code ?? "local_authority_already_initialized",
          reason: "canonical local authority is already initialized by different content",
          legacy_writer_fenced: true,
          legacy_fallback_used: false,
        };
    }
    if (existing.status !== "missing") return {
      schema_version: LOCAL_COORDINATION_PROMOTION_RESULT_SCHEMA,
      ...existing,
      legacy_writer_fenced: true,
      legacy_fallback_used: false,
    };

    const shadowHead = await shadow.loadAuthority();
    if (shadowHead.status !== "loaded") return {
      schema_version: LOCAL_COORDINATION_PROMOTION_RESULT_SCHEMA,
      status: "failed",
      reason_code: shadowHead.status === "missing"
        ? "local_authority_shadow_missing"
        : shadowHead.reason_code,
      reason: shadowHead.status === "missing" ? "qualified shadow authority is missing" : shadowHead.reason,
      legacy_writer_fenced: true,
      legacy_fallback_used: false,
    };
    const observedDigest = canonicalAuthoritySha256(shadowHead.head);
    if (
      shadowHead.provider_revision !== request.expected_shadow_provider_revision ||
      observedDigest !== request.expected_shadow_projection_sha256
    ) return {
      schema_version: LOCAL_COORDINATION_PROMOTION_RESULT_SCHEMA,
      status: "failed",
      reason_code: "local_authority_shadow_fence_mismatch",
      reason: "shadow revision or projection changed before promotion",
      observed_shadow_provider_revision: shadowHead.provider_revision,
      observed_shadow_projection_sha256: observedDigest,
      legacy_writer_fenced: true,
      legacy_fallback_used: false,
    };
    indexCoordinationProjection(shadowHead.head, request.goal_id);

    const qualification = await qualifyCoordinationRuntimeShadow({
      schema_version: COORDINATION_RUNTIME_SHADOW_QUALIFY_REQUEST_SCHEMA,
      runtime_root: request.runtime_root,
      goal_id: request.goal_id,
      projection: shadowHead.head,
      minimum_operations: request.minimum_operations,
      required_event_kinds: request.required_event_kinds,
    }, {
      createStore: () => shadow,
    });
    if (qualification.status !== "qualified" || qualification.qualified !== true) return {
      schema_version: LOCAL_COORDINATION_PROMOTION_RESULT_SCHEMA,
      status: "failed",
      reason_code: "local_authority_shadow_not_qualified",
      reason: "shadow parity evidence does not satisfy the promotion policy",
      qualification_status: qualification.status,
      legacy_writer_fenced: true,
      legacy_fallback_used: false,
    };

    return await withCanonicalWriter(request.runtime_root, request.goal_id, false, async () => {
      const finalShadowHead = await shadow.loadAuthority();
      if (
        finalShadowHead.status !== "loaded" ||
        finalShadowHead.provider_revision !== request.expected_shadow_provider_revision ||
        canonicalAuthoritySha256(finalShadowHead.head) !== request.expected_shadow_projection_sha256
      ) return {
        schema_version: LOCAL_COORDINATION_PROMOTION_RESULT_SCHEMA,
        status: "failed",
        reason_code: "local_authority_shadow_changed_during_qualification",
        reason: "shadow head changed while promotion evidence was being verified",
        legacy_writer_fenced: true,
        legacy_fallback_used: false,
      };

      const identity = promotionIdentity(request);
      const committed = await canonical.commitAuthority({
        expected_provider_revision: null,
        operation_id: request.operation_id,
        events: [{
          ...identity,
          schema_version: "loopx_local_coordination_promotion_event_v0",
          mode_transition: `legacy_canonical_to_${canonicalAuthority}`,
        }],
        next_projection: finalShadowHead.head,
        receipts: [identity],
      });
      if (committed.status === "applied") {
        const readback = await promotionReadback(canonical, request);
        return readback.matched
          ? promotionResult(request, "applied", readback, canonicalAuthority)
          : {
            schema_version: LOCAL_COORDINATION_PROMOTION_RESULT_SCHEMA,
            status: "failed",
            reason_code: readback.reason_code ?? "local_authority_promotion_readback_mismatch",
            reason: "promotion commit lacks an exact durable readback",
            legacy_writer_fenced: true,
            legacy_fallback_used: false,
          };
      }
      const readback = await promotionReadback(canonical, request);
      if (readback.matched) {
        return promotionResult(
          request,
          committed.status === "ambiguous" ? "recovered" : "replayed",
          readback,
          canonicalAuthority,
        );
      }
      return {
        schema_version: LOCAL_COORDINATION_PROMOTION_RESULT_SCHEMA,
        ...committed,
        ...(committed.status === "ambiguous" ? { reconciliation_required: true } : {}),
        legacy_writer_fenced: true,
        legacy_fallback_used: false,
      };
    });
  } catch (error) {
    return {
      schema_version: LOCAL_COORDINATION_PROMOTION_RESULT_SCHEMA,
      status: "failed",
      reason_code: error instanceof ShadowManagementError ? error.reason_code : "local_authority_promotion_unavailable",
      reason: error instanceof Error ? error.message : "promotion unavailable",
      legacy_writer_fenced: writerFenceVerified,
      legacy_fallback_used: false,
      ...localAuthorityOpenFailure(error),
    };
  }
}

/** Local provider adapter for the provider-neutral Todo claim transaction. */
export async function claimLocalCoordinationTodo(
  value: unknown,
  dependencies: LocalAuthorityRuntimeDependencies = {},
): Promise<JsonObject> {
  let sourceAuthority = "file_v0";
  try {
    const input = requireJsonObject(value, "local coordination Todo claim request");
    if (input.schema_version !== LOCAL_COORDINATION_TODO_CLAIM_REQUEST_SCHEMA &&
        input.schema_version !== LOCAL_COORDINATION_TODO_CLAIM_WITNESSED_REQUEST_SCHEMA) {
      throw new Error("local coordination Todo claim request schema mismatch");
    }
    const authoritySourcesCurrent = registryAuthoritySourceCheck(input,
      input.schema_version === LOCAL_COORDINATION_TODO_CLAIM_WITNESSED_REQUEST_SCHEMA);
    if (typeof input.dry_run !== "boolean") {
      throw new Error("dry_run must be a JSON boolean");
    }
    const root = runtimeRoot(input.runtime_root);
    const goalId = requireAuthorityStoreId(input.goal_id, "goal id");
    return await withCanonicalWriter(root, goalId, input.dry_run === true, async () => {
      const store = await openRuntimeStore(root, goalId, dependencies);
      sourceAuthority = sourceAuthorityFor(store);
      if (!Array.isArray(input.registered_agents)) {
        throw new Error("registered_agents must be a JSON array");
      }
      const registeredAgents = input.registered_agents.map(
        (value) => claimAgentValue(value, "registered agent"),
      );
      const leaseRequestValue = input.lease_request;
      const leaseRequest = leaseRequestValue === null || leaseRequestValue === undefined
        ? null
        : (() => {
          const request = requireJsonObject(leaseRequestValue, "lease_request");
          const expectedVersion = request.expected_version;
          if (expectedVersion !== null && expectedVersion !== undefined &&
              (!Number.isSafeInteger(expectedVersion) || Number(expectedVersion) < 0)) {
            throw new Error(
              "lease_request.expected_version must be a non-negative safe integer or null",
            );
          }
          return {
            idempotency_key: normalizeIdempotencyKey(request.idempotency_key),
            expected_version: expectedVersion === undefined ? null : expectedVersion as number | null,
            ttl_seconds: normalizeTtl(request.ttl_seconds),
          };
        })();
      const result = await executeCoordinationTodoClaim(store, {
        goal_id: goalId,
        todo_id: requireAuthorityStoreId(input.todo_id, "todo id"),
        claimed_by: claimAgentValue(input.claimed_by, "claimed_by"),
        actor_agent_id: input.actor_agent_id === null || input.actor_agent_id === undefined
          ? null
          : claimAgentValue(input.actor_agent_id, "actor_agent_id"),
        expected_role: input.role === null || input.role === undefined
          ? null
          : requireAuthorityStoreId(input.role, "role"),
        registered_agents: registeredAgents,
        operation_id: requireAuthorityStoreId(input.operation_id, "operation id"),
        lease_request: leaseRequest,
        dry_run: input.dry_run === true,
        now: claimObservedAt(input.observed_at),
      }, authoritySourcesCurrent);
      return {
        ...result,
        source_authority: sourceAuthority,
        decision_read_from_provider: true,
        legacy_fallback_used: false,
      };
    });
  } catch (error) {
    return {
      schema_version: COORDINATION_TODO_CLAIM_RESULT_SCHEMA,
      status: "failed",
      reason_code: error instanceof ShadowManagementError ? error.reason_code : "invalid_local_coordination_todo_claim_request",
      reason: error instanceof Error ? error.message : "invalid Todo claim request",
      source_authority: sourceAuthority,
      decision_read_from_provider: true,
      legacy_fallback_used: false,
      ...localAuthorityOpenFailure(error),
    };
  }
}

/** Local provider adapter for the provider-neutral work-item create transaction. */
export async function createLocalCoordinationTodo(
  value: unknown,
  dependencies: LocalAuthorityRuntimeDependencies = {},
): Promise<JsonObject> {
  let sourceAuthority = "file_v0";
  const providerEvidence = {
    source_authority: sourceAuthority,
    decision_read_from_provider: true,
    legacy_fallback_used: false,
  };
  try {
    const input = requireJsonObject(value, "local coordination Todo create request");
    if (input.schema_version !== LOCAL_COORDINATION_TODO_CREATE_REQUEST_SCHEMA &&
        input.schema_version !== LOCAL_COORDINATION_TODO_CREATE_WITNESSED_REQUEST_SCHEMA) {
      throw new TypeError("local coordination Todo create request schema mismatch");
    }
    const authoritySourcesCurrent = registryAuthoritySourceCheck(input,
      input.schema_version === LOCAL_COORDINATION_TODO_CREATE_WITNESSED_REQUEST_SCHEMA);
    if (typeof input.dry_run !== "boolean") {
      throw new TypeError("dry_run must be a JSON boolean");
    }
    const root = runtimeRoot(input.runtime_root);
    const goalId = requireAuthorityStoreId(input.goal_id, "goal id");
    return await withCanonicalWriter(root, goalId, input.dry_run === true, async () => {
      const store = await openRuntimeStore(root, goalId, dependencies);
      sourceAuthority = sourceAuthorityFor(store);
      providerEvidence.source_authority = sourceAuthority;
      if (!Array.isArray(input.registered_agents)) {
        throw new TypeError("registered_agents must be a JSON array");
      }
      const result = await executeCoordinationTodoCreate(store, {
        goal_id: goalId,
        todo: requireJsonObject(input.todo, "todo"),
        actor_agent_id: input.actor_agent_id === null || input.actor_agent_id === undefined
          ? null
          : claimAgentValue(input.actor_agent_id, "actor_agent_id"),
        registered_agents: input.registered_agents.map(
          (agent) => claimAgentValue(agent, "registered agent"),
        ),
        operation_id: requireAuthorityStoreId(input.operation_id, "operation id"),
        dry_run: input.dry_run === true,
        now: claimObservedAt(input.observed_at),
      }, authoritySourcesCurrent);
      return {
        ...result,
        ...providerEvidence,
      };
    });
  } catch (error) {
    return {
      schema_version: COORDINATION_TODO_CREATE_RESULT_SCHEMA,
      status: "failed",
      reason_code: error instanceof ShadowManagementError ? error.reason_code : "invalid_local_coordination_todo_create_request",
      reason: error instanceof Error ? error.message : "invalid Todo create request",
      ...providerEvidence,
      ...localAuthorityOpenFailure(error),
    };
  }
}

/** Local provider adapter for one provider-neutral metadata mutation. */
export async function updateLocalCoordinationTodo(
  value: unknown,
  dependencies: LocalAuthorityRuntimeDependencies = {},
): Promise<JsonObject> {
  let sourceAuthority = "file_v0";
  const providerEvidence = {source_authority: sourceAuthority,
    decision_read_from_provider: true, legacy_fallback_used: false};
  try {
    const input = requireJsonObject(value, "local coordination Todo update request");
    if (input.schema_version !== COORDINATION_TODO_UPDATE_REQUEST_SCHEMA &&
        input.schema_version !== COORDINATION_TODO_PLANNING_UPDATE_REQUEST_SCHEMA &&
        input.schema_version !== COORDINATION_TODO_REVIEWED_UPDATE_REQUEST_SCHEMA &&
        input.schema_version !== COORDINATION_TODO_COMPLETION_UPDATE_REQUEST_SCHEMA &&
        input.schema_version !== COORDINATION_TODO_OBSERVATION_UPDATE_REQUEST_SCHEMA) {
      throw new TypeError("local coordination Todo update request schema mismatch");
    }
    const planningIntent = input.planning_intent == null ? undefined :
      requireJsonObject(input.planning_intent, "Todo planning intent");
    if (planningIntent && Object.keys(planningIntent).length &&
        input.schema_version === COORDINATION_TODO_UPDATE_REQUEST_SCHEMA) {
      throw new TypeError("planning_intent requires the v1 Todo update request");
    }
    const completionUpdate = input.schema_version === COORDINATION_TODO_COMPLETION_UPDATE_REQUEST_SCHEMA;
    const observationUpdate = input.schema_version === COORDINATION_TODO_OBSERVATION_UPDATE_REQUEST_SCHEMA;
    if (!observationUpdate && Object.hasOwn(input, "monitor_observation")) {
      throw new TypeError("Monitor observation payload requires request v4");
    }
    if (observationUpdate && input.monitor_observation == null) throw new TypeError("Monitor update requires its observation payload");
    if (!completionUpdate && Object.hasOwn(input, "completion")) {
      throw new TypeError("Todo completion payload requires request v3");
    }
    if (completionUpdate && input.completion == null) throw new TypeError("Todo completion update requires its completion payload");
    const reviewed = observationUpdate || completionUpdate || input.schema_version === COORDINATION_TODO_REVIEWED_UPDATE_REQUEST_SCHEMA;
    if (!reviewed && ["lifecycle_grants", "authority_reason", "registry_source",
      "expected_provider_revision", "expected_registry_sha256"].some(field => Object.hasOwn(input, field))) {
      throw new TypeError("Todo update admission and revision fields require request v2");
    }
    const grants = reviewed ? input.lifecycle_grants : [];
    if (!Array.isArray(grants)) throw new TypeError("lifecycle_grants must be an array");
    const authoritySourcesCurrent = registryAuthoritySourceCheck(input, reviewed, input.expected_registry_sha256);
    const root = runtimeRoot(input.runtime_root);
    const goalId = requireAuthorityStoreId(input.goal_id, "goal id");
    return await withCanonicalWriter(root, goalId, input.dry_run === true, async () => {
      if (!Array.isArray(input.registered_agents) || !Array.isArray(input.clear_fields)) {
        throw new TypeError("registered_agents and clear_fields must be JSON arrays");
      }
      const store = await openRuntimeStore(root, goalId, dependencies);
      sourceAuthority = sourceAuthorityFor(store);
      providerEvidence.source_authority = sourceAuthority;
      return {...await executeCoordinationTodoUpdate(store, {
        goal_id: goalId, todo_id: requireAuthorityStoreId(input.todo_id, "todo id"),
        expected_role: input.role === null || input.role === undefined ? null :
          requireAuthorityStoreId(input.role, "role"),
        actor_agent_id: input.actor_agent_id === null || input.actor_agent_id === undefined ? null :
          claimAgentValue(input.actor_agent_id, "actor_agent_id"),
        registered_agents: input.registered_agents.map((agent) =>
          claimAgentValue(agent, "registered agent")),
        operation_id: requireAuthorityStoreId(input.operation_id, "operation id"),
        ...(reviewed ? {
          lifecycle_grants: grants.map((grant, index) => requireJsonObject(grant, `lifecycle_grants[${index}]`)),
          authority_reason: optionalProseValue(input.authority_reason, "authority_reason"),
          ...(input.expected_provider_revision == null ? {} : {
            expected_provider_revision: requireAuthorityStoreId(input.expected_provider_revision, "expected_provider_revision")}),
          ...(input.expected_registry_sha256 == null ? {} : {
            expected_registry_sha256: claimAgentValue(input.expected_registry_sha256, "expected_registry_sha256")}),
        } : {}),
        lease_idempotency_key: input.lease_idempotency_key == null ? null :
          requireAuthorityStoreId(input.lease_idempotency_key, "lease_idempotency_key"),
        lease_expected_version: optionalNonNegativeSafeInteger(input.lease_expected_version, "lease_expected_version"),
        patch: requireJsonObject(input.patch, "Todo update patch"),
        planning_intent: planningIntent,
        ...(observationUpdate ? {monitor_observation: decodeMonitorPollObservation(input.monitor_observation)} : {}),
        ...(completionUpdate ? {completion: requireJsonObject(input.completion, "Todo completion payload")} : {}),
        clear_fields: input.clear_fields.map((field) => claimAgentValue(field, "clear field")),
        dry_run: input.dry_run as boolean,
        now: claimObservedAt(input.observed_at),
      }, authoritySourcesCurrent), ...providerEvidence};
    });
  } catch (error) {
    return {schema_version: COORDINATION_TODO_UPDATE_RESULT_SCHEMA, status: "failed",
      changed: false, reason_code: error instanceof ShadowManagementError ? error.reason_code : "invalid_local_coordination_todo_update_request",
      reason: error instanceof Error ? error.message : "invalid Todo update request",
      ...providerEvidence,
      ...localAuthorityOpenFailure(error),
    };
  }
}

/** Local provider adapter for the provider-neutral terminal transaction. */
export async function terminalLifecycleLocalCoordinationTodo(
  value: unknown,
  dependencies: LocalAuthorityRuntimeDependencies = {},
): Promise<JsonObject> {
  let sourceAuthority = "file_v0";
  const providerEvidence = {source_authority: sourceAuthority,
    decision_read_from_provider: true, legacy_fallback_used: false};
  try {
    const input = requireJsonObject(value, "local coordination Todo terminal request");
    if (input.schema_version !== LOCAL_COORDINATION_TODO_TERMINAL_LIFECYCLE_REQUEST_SCHEMA &&
        input.schema_version !== LOCAL_COORDINATION_TODO_TERMINAL_LIFECYCLE_WITNESSED_REQUEST_SCHEMA) {
      throw new TypeError("local coordination Todo terminal request schema mismatch");
    }
    const authoritySourcesCurrent = registryAuthoritySourceCheck(input,
      input.schema_version === LOCAL_COORDINATION_TODO_TERMINAL_LIFECYCLE_WITNESSED_REQUEST_SCHEMA);
    if (!Array.isArray(input.registered_agents) || !Array.isArray(input.lifecycle_grants) ||
        !Array.isArray(input.successor_intents) ||
        !Array.isArray(input.linked_successor_todo_ids)) {
      throw new TypeError(
        "registered_agents, lifecycle_grants, successor_intents, and " +
          "linked_successor_todo_ids must be arrays",
      );
    }
    const root = runtimeRoot(input.runtime_root);
    const goalId = requireAuthorityStoreId(input.goal_id, "goal id");
    const leaseExpectedVersion = optionalNonNegativeSafeInteger(
      input.lease_expected_version,
      "lease_expected_version",
    );
    const registeredAgents = input.registered_agents.map((agent) =>
      claimAgentValue(agent, "registered agent"));
    const lifecycleGrants = input.lifecycle_grants.map((grant, index) =>
      requireJsonObject(grant, `lifecycle_grants[${index}]`));
    const linkedSuccessorTodoIds = input.linked_successor_todo_ids.map((todoId) =>
      requireAuthorityStoreId(todoId, "linked successor Todo id"));
    const successorIntents = input.successor_intents.map((intent, index) =>
      requireJsonObject(intent, `successor_intents[${index}]`));
    return await withCanonicalWriter(root, goalId, input.dry_run === true, async () => {
      const store = await openRuntimeStore(root, goalId, dependencies);
      sourceAuthority = sourceAuthorityFor(store);
      providerEvidence.source_authority = sourceAuthority;
      return {...await executeCoordinationTodoTerminalLifecycle(store, {
        goal_id: goalId,
        todo_id: requireAuthorityStoreId(input.todo_id, "todo id"),
        expected_role: input.role === null || input.role === undefined
          ? null : requireAuthorityStoreId(input.role, "role") as "agent" | "user",
        command: requireAuthorityStoreId(input.command, "command") as "complete" | "supersede",
        actor_agent_id: input.actor_agent_id === null || input.actor_agent_id === undefined
          ? null : claimAgentValue(input.actor_agent_id, "actor_agent_id"),
        registered_agents: registeredAgents,
        lifecycle_grants: lifecycleGrants,
        authority_reason: input.authority_reason === null || input.authority_reason === undefined
          ? null : claimAgentValue(input.authority_reason, "authority_reason"),
        decision_outcome: input.decision_outcome === null || input.decision_outcome === undefined
          ? null : requireAuthorityStoreId(input.decision_outcome, "decision_outcome") as
            "approve" | "reject" | "cancel",
        operation_id: requireAuthorityStoreId(input.operation_id, "operation id"),
        lease_idempotency_key:
          input.lease_idempotency_key === null || input.lease_idempotency_key === undefined
            ? null : requireAuthorityStoreId(input.lease_idempotency_key, "lease idempotency key"),
        lease_expected_version: leaseExpectedVersion,
        allow_user_gate_auto_acquire: input.allow_user_gate_auto_acquire as boolean,
        requested_no_followup: input.requested_no_followup as boolean,
        requested_completion_turn_key:
          input.requested_completion_turn_key === null ||
            input.requested_completion_turn_key === undefined
            ? null : claimAgentValue(
              input.requested_completion_turn_key,
              "requested_completion_turn_key",
            ),
        requested_completion_identity_source:
          input.requested_completion_identity_source === null ||
            input.requested_completion_identity_source === undefined
            ? null : requireAuthorityStoreId(
              input.requested_completion_identity_source,
              "requested_completion_identity_source",
            ) as "turn_settlement" | "unscoped_completion" | "lifecycle_reentry",
        linked_successor_todo_ids: linkedSuccessorTodoIds,
        successor_intents: successorIntents,
        note: optionalProseValue(input.note, "note"),
        evidence: optionalProseValue(input.evidence, "evidence"),
        reason: optionalProseValue(input.reason, "reason"),
        clear_claim: input.clear_claim as boolean,
        validation_declaration:
          input.validation_declaration === null || input.validation_declaration === undefined
            ? null : requireJsonObject(input.validation_declaration, "validation_declaration"),
        validation_receipt: input.validation_receipt === null || input.validation_receipt === undefined
          ? null : requireJsonObject(input.validation_receipt, "validation_receipt"),
        goal_acceptance_source_binding: input.goal_acceptance_source_binding == null
          ? null : requireJsonObject(input.goal_acceptance_source_binding, "goal_acceptance_source_binding"),
        goal_acceptance_validation_receipts: input.goal_acceptance_validation_receipts,
        completion_policy_request:
          input.completion_policy_request === null || input.completion_policy_request === undefined
            ? null : requireJsonObject(input.completion_policy_request, "completion_policy_request"),
        dry_run: input.dry_run as boolean,
        now: claimObservedAt(input.observed_at),
      }, authoritySourcesCurrent), ...providerEvidence};
    });
  } catch (error) {
    return {schema_version: COORDINATION_TODO_TERMINAL_LIFECYCLE_RESULT_SCHEMA,
      status: "failed", changed: false,
      reason_code: error instanceof ShadowManagementError ? error.reason_code :
        "invalid_local_coordination_todo_terminal_lifecycle_request",
      reason: error instanceof Error ? error.message : "invalid local Todo terminal request",
      ...providerEvidence,
      ...localAuthorityOpenFailure(error),
    };
  }
}

/** Local provider adapter for provider-owned completed-Todo compaction. */
export async function archiveLocalCoordinationTodos(
  value: unknown,
  dependencies: LocalAuthorityRuntimeDependencies = {},
): Promise<JsonObject> {
  let sourceAuthority = "file_v0";
  const providerEvidence = {source_authority: sourceAuthority,
    decision_read_from_provider: true, legacy_fallback_used: false};
  try {
    const input = requireJsonObject(value, "local coordination Todo archive request");
    if (input.schema_version !== LOCAL_COORDINATION_TODO_ARCHIVE_REQUEST_SCHEMA) {
      throw new TypeError("local coordination Todo archive request schema mismatch");
    }
    const root = runtimeRoot(input.runtime_root);
    const goalId = requireAuthorityStoreId(input.goal_id, "goal id");
    const maxActiveDone = requiredNonNegativeSafeInteger(
      input.max_active_done,
      "max_active_done",
    );
    const role = archiveRole(input.role);
    const operationId = requireAuthorityStoreId(input.operation_id, "operation id");
    const expectedRevision = input.expected_provider_revision === undefined ? undefined :
      requireAuthorityStoreId(input.expected_provider_revision, "expected provider revision");
    if (typeof input.dry_run !== "boolean") throw new TypeError("dry_run must be a boolean");
    const now = claimObservedAt(input.observed_at);
    return await withCanonicalWriter(root, goalId, input.dry_run === true, async () => {
      const store = await openRuntimeStore(root, goalId, dependencies);
      sourceAuthority = sourceAuthorityFor(store);
      providerEvidence.source_authority = sourceAuthority;
      return {...await executeLocalArchiveAttempt(store, root, {
        goal_id: goalId,
        role,
        max_active_done: maxActiveDone,
        operation_id: operationId,
        expected_provider_revision: expectedRevision,
        dry_run: input.dry_run as boolean,
        now,
      }), ...providerEvidence};
    });
  } catch (error) {
    return {schema_version: COORDINATION_TODO_ARCHIVE_RESULT_SCHEMA,
      status: "failed", changed: false,
      reason_code: error instanceof ShadowManagementError ? error.reason_code :
        "invalid_local_coordination_todo_archive_request",
      reason: error instanceof Error ? error.message : "invalid local Todo archive request",
      ...providerEvidence,
      ...localAuthorityOpenFailure(error),
    };
  }
}

/** Retire one local retry identity only after its compatibility projection succeeds. */
export async function acknowledgeLocalCoordinationTodoArchive(
  value: unknown,
  dependencies: LocalAuthorityRuntimeDependencies = {},
): Promise<JsonObject> {
  try {
    const input = requireJsonObject(value, "local Todo archive acknowledgement");
    if (input.schema_version !== LOCAL_COORDINATION_TODO_ARCHIVE_ACK_REQUEST_SCHEMA) {
      throw new TypeError("local Todo archive acknowledgement schema mismatch");
    }
    const root = runtimeRoot(input.runtime_root);
    const goalId = requireAuthorityStoreId(input.goal_id, "goal id");
    const role = archiveRole(input.role);
    const operationId = requireAuthorityStoreId(input.operation_id, "operation id");
    return await withCanonicalWriter(root, goalId, false, async () => {
      const store = await openRuntimeStore(root, goalId, dependencies);
      return acknowledgeLocalArchiveAttempt(store, root, goalId, role, operationId);
    });
  } catch (error) {
    return {
      schema_version: LOCAL_TODO_ARCHIVE_ACK_RESULT_SCHEMA,
      status: "failed", changed: false,
      reason_code: error instanceof ShadowManagementError ? error.reason_code :
        "invalid_local_coordination_todo_archive_ack_request",
      reason: error instanceof Error ? error.message : "invalid archive acknowledgement",
      ...localAuthorityOpenFailure(error),
    };
  }
}

/** Provider-first exact Todo read. Missing/unavailable state never falls back. */
export async function readLocalCoordinationTodo(
  value: unknown,
  dependencies: LocalAuthorityRuntimeDependencies = {},
): Promise<JsonObject> {
  let sourceAuthority = "file_v0";
  try {
    const input = requireJsonObject(value, "local coordination Todo read request");
    if (input.schema_version !== LOCAL_COORDINATION_TODO_READ_REQUEST_SCHEMA) {
      throw new Error("local coordination Todo read request schema mismatch");
    }
    const root = runtimeRoot(input.runtime_root);
    const goalId = requireAuthorityStoreId(input.goal_id, "goal id");
    const todoId = requireAuthorityStoreId(input.todo_id, "todo id");
    const store = await openRuntimeStore(root, goalId, dependencies);
    sourceAuthority = sourceAuthorityFor(store);
    const head = await store.loadAuthority();
    if (head.status !== "loaded") {
      return {
        schema_version: LOCAL_COORDINATION_TODO_READ_RESULT_SCHEMA,
        ...head,
        source_authority: sourceAuthority,
        decision_read_from_provider: true,
        legacy_fallback_used: false,
      };
    }
    const projection = indexCoordinationProjectionTodos(head.head, goalId);
    validateCoordinationTodoReadModel(head.head, goalId);
    const todo = projection.todos.get(todoId);
    const acceptance = todo === undefined ? null : acceptanceWorkGuard(head.head, goalId, todoId);
    return {
      schema_version: LOCAL_COORDINATION_TODO_READ_RESULT_SCHEMA,
      status: todo === undefined ? "missing" : "found",
      todo_id: todoId,
      ...(todo === undefined ? {} : { todo }),
      ...(acceptance === null ? {} : {goal_acceptance_guard: acceptance}),
      todo_ids: projection.todo_ids,
      provider_revision: head.provider_revision,
      cursor: head.cursor,
      source_authority: sourceAuthority,
      decision_read_from_provider: true,
      legacy_fallback_used: false,
    };
  } catch (error) {
    return {
      schema_version: LOCAL_COORDINATION_TODO_READ_RESULT_SCHEMA,
      status: "failed",
      reason_code: "invalid_local_coordination_todo_read_request",
      reason: error instanceof Error ? error.message : "invalid Todo read request",
      source_authority: sourceAuthority,
      decision_read_from_provider: true,
      legacy_fallback_used: false,
      ...localAuthorityOpenFailure(error),
    };
  }
}

/** Provider-first Todo collection read. Missing/unavailable state never falls back. */
export async function listLocalCoordinationTodos(
  value: unknown,
  dependencies: LocalAuthorityRuntimeDependencies = {},
): Promise<JsonObject> {
  let sourceAuthority = "file_v0";
  try {
    const input = requireJsonObject(value, "local coordination Todo list request");
    if (input.schema_version !== LOCAL_COORDINATION_TODO_LIST_REQUEST_SCHEMA) {
      throw new Error("local coordination Todo list request schema mismatch");
    }
    if (input.include_leases !== undefined && typeof input.include_leases !== "boolean") {
      throw new Error("include_leases must be a boolean");
    }
    const root = runtimeRoot(input.runtime_root);
    const goalId = requireAuthorityStoreId(input.goal_id, "goal id");
    const store = await openRuntimeStore(root, goalId, dependencies);
    sourceAuthority = sourceAuthorityFor(store);
    const head = await store.loadAuthority();
    if (head.status !== "loaded") {
      return {
        schema_version: LOCAL_COORDINATION_TODO_LIST_RESULT_SCHEMA,
        ...head,
        source_authority: sourceAuthority,
        decision_read_from_provider: true,
        legacy_fallback_used: false,
      };
    }
    const projection = indexCoordinationProjectionTodos(head.head, goalId);
    const todoReadModel = validateCoordinationTodoReadModel(head.head, goalId);
    const leaseIndex = input.include_leases === true
      ? indexCoordinationProjection(head.head, goalId) : null;
    const acceptance = projectGoalAcceptance(head.head, goalId);
    return {
      schema_version: LOCAL_COORDINATION_TODO_LIST_RESULT_SCHEMA,
      status: "loaded",
      todos: projection.todo_ids.map((todoId) => projection.todos.get(todoId)!),
      todo_ids: projection.todo_ids,
      todo_read_model: todoReadModel,
      ...(acceptance.enabled !== true ? {} : {goal_acceptance_contract: acceptance,
        goal_acceptance_work_guards: Object.fromEntries(projection.todo_ids.flatMap(id => {
          const guard = acceptanceWorkGuard(head.head, goalId, id);
          return guard === null ? [] : [[id, guard]];
        }))}),
      ...(leaseIndex === null ? {} : {
        leases: leaseIndex.lease_todo_ids.map((id) => leaseIndex.leases.get(id)!),
        handoff_mode: head.head.handoff_mode ?? "legacy",
      }),
      provider_revision: head.provider_revision,
      cursor: head.cursor,
      source_authority: sourceAuthority,
      decision_read_from_provider: true,
      legacy_fallback_used: false,
    };
  } catch (error) {
    return {
      schema_version: LOCAL_COORDINATION_TODO_LIST_RESULT_SCHEMA,
      status: "failed",
      reason_code: "invalid_local_coordination_todo_list_request",
      reason: error instanceof Error ? error.message : "invalid Todo list request",
      source_authority: sourceAuthority,
      decision_read_from_provider: true,
      legacy_fallback_used: false,
      ...localAuthorityOpenFailure(error),
    };
  }
}

/** The explicit local CLI continuation uses the existing promoted writer fence. */
export async function continueLocalTodo(
  value: unknown,
  dependencies: LocalAuthorityRuntimeDependencies = {},
): Promise<JsonObject> {
  const evidence = {source_authority: "file_v0", decision_read_from_provider: true, legacy_fallback_used: false};
  try {
    const input = requireJsonObject(value, "Todo continuation request");
    const root = runtimeRoot(input.runtime_root);
    const goalId = requireAuthorityStoreId(input.goal_id, "goal id");
    return await withCanonicalWriter(root, goalId, false, async () => {
      const store = await openRuntimeStore(root, goalId, dependencies);
      evidence.source_authority = sourceAuthorityFor(store);
      const fence = await loadLegacyCoordinationWriterFence(root, goalId);
      if (fence.status !== "loaded") return {ok: false, status: "rejected",
        reason_code: "continuation_requires_canonical_authority",
        reason: "Use an explicitly promoted local file authority; this command never promotes or falls back to Markdown"};
      return {...await executeTodoContinuation(store, input), ...evidence};
    });
  } catch (error) {
    return {ok: false, status: "failed",
      reason_code: error instanceof ShadowManagementError ? error.reason_code : "invalid_continuation_request",
      reason: error instanceof Error ? error.message : "Invalid continuation request", ...evidence,
      ...localAuthorityOpenFailure(error)};
  }
}

/** Goal Channel observes a complete provider snapshot through one coarse read. */
export async function observeLocalCoordinationOwnership(value: unknown): Promise<JsonObject> {
  let sourceAuthority = "canonical_unavailable";
  try {
    const input = requireJsonObject(value, "local ownership observation");
    if (input.schema_version !== "loopx_local_ownership_observation_request_v0") throw new Error("ownership observation schema mismatch");
    const root = runtimeRoot(input.runtime_root);
    const goalId = requireAuthorityStoreId(input.goal_id, "goal id");
    const store = await openLocalAuthorityStore(root, goalId);
    sourceAuthority = sourceAuthorityFor(store);
    return {...await readCoordinationOwnership(store, goalId, input.observed_at as string),
      source_authority: sourceAuthority, decision_read_from_provider: true, legacy_fallback_used: false};
  } catch (error) {
    return {schema_version: "loopx_ownership_observation_result_v0", status: "failed",
      reason_code: "coordination_observation_unavailable", source_authority: sourceAuthority,
      decision_read_from_provider: true, legacy_fallback_used: false, ...localAuthorityOpenFailure(error)};
  }
}
