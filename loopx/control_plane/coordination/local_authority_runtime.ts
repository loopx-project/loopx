import {requirePromotionRegisteredAgents} from "./shadow_registry_source.ts";
import {readPromotionReceipt, commitPromotionAndReadBack} from './promotion_receipt.ts';
import {reviewedPromotionPlan, promotionPlanDigest, decodeReviewedPromotionOperation, REVIEWED_PROMOTION_OPERATION_RESULT_SCHEMA} from './reviewed_promotion_plan.ts';
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
import {decodeMonitorPollObservation} from "../todos/monitor_metadata.ts";
import {decodeCompletionValidationRevision} from "../todos/completion_validation_revision.ts";
import {executeCoordinationMonitorPoll, COORDINATION_MONITOR_POLL_REQUEST_SCHEMA, COORDINATION_LEASED_MONITOR_POLL_REQUEST_SCHEMA, COORDINATION_WITNESSED_MONITOR_POLL_REQUEST_SCHEMA, COORDINATION_GUARDED_MONITOR_POLL_REQUEST_SCHEMA,
  COORDINATION_MONITOR_POLL_RESULT_SCHEMA} from "./todo_monitor_poll.ts";
import { requireJsonObject, requireBoolean } from "../runtime_decode.ts";
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
} from "./coordination_projection.ts";
import { authorityStoreSourceAuthority, type AuthorityStore } from "./authority_store.ts";
import {
  authorityUnicodeCompare,
  canonicalAuthorityBytes,
  canonicalAuthorityObject,
  canonicalAuthoritySha256,
  requireAuthorityStoreId,
} from "./authority_store_codec.ts";
import { FileAuthorityStore } from "./file_authority_store.ts";
import {
  openLocalAuthorityStore,
  openRuntimeAuthorityStore as openRuntimeStore,
  requireLocalAuthorityRuntimeRoot as runtimeRoot,
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
  decodeRuntimeShadowRequest,
  qualifyCoordinationRuntimeShadowUnderLocks,
  qualifyCoordinationShadowLineageUnderLocks,
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
  COORDINATION_TODO_VALIDATION_REVISION_REQUEST_SCHEMA,
  COORDINATION_TODO_UPDATE_RESULT_SCHEMA,
  executeCoordinationTodoUpdate,
} from "./todo_update.ts";
import {
  COORDINATION_TODO_TERMINAL_LIFECYCLE_RESULT_SCHEMA,
  executeCoordinationTodoTerminalLifecycle,
  decodeTerminalOperationIntent,
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
import {compactPythonWhitespace, normalizeRegisteredTodoAgents} from "./todo_agents.ts";
import {
  normalizePromotionHandoffModeMigration,
  planPromotionHandoffMigration,
  publicPromotionHandoffMigrationPlan,
  type PromotionHandoffModeMigration,
} from "./promotion_handoff_migration.ts";

export const LOCAL_COORDINATION_TODO_CLAIM_REQUEST_SCHEMA =
  "loopx_local_coordination_todo_claim_request_v0";
export const LOCAL_COORDINATION_TODO_CLAIM_WITNESSED_REQUEST_SCHEMA = "loopx_local_coordination_todo_claim_request_v1";
export const LOCAL_COORDINATION_TODO_CREATE_REQUEST_SCHEMA =
  "loopx_local_coordination_todo_create_request_v0";
export const LOCAL_COORDINATION_TODO_CREATE_WITNESSED_REQUEST_SCHEMA = "loopx_local_coordination_todo_create_request_v1";
export const LOCAL_COORDINATION_TODO_TERMINAL_LIFECYCLE_REQUEST_SCHEMA =
  "loopx_local_coordination_todo_terminal_lifecycle_request_v3";
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
  let result: JsonObject;
  let sourceScope: {runtimeRoot: string; goalId: string} | null = null;
  try {
    const input = decodeRuntimeShadowRequest(
      value,
      LOCAL_COORDINATION_PROMOTION_REVIEW_REQUEST_SCHEMA,
      ["operation_id", "minimum_operations", "required_event_kinds", "execute",
        "handoff_mode_migration", "registered_agents", "expected_promotion_plan_sha256"],
    );
    sourceScope = {runtimeRoot: input.runtime_root, goalId: input.goal_id};
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
    const handoffModeMigration = normalizePromotionHandoffModeMigration(
      input.handoff_mode_migration,
    );
    const explicitHandoffMigration = input.handoff_mode_migration !== undefined;
    if (explicitHandoffMigration && !Array.isArray(input.registered_agents)) {
      throw new Error("registered_agents must be supplied for an explicit handoff-mode migration");
    }
    const registeredAgents = input.registered_agents === undefined || input.registered_agents === null
      ? []
      : normalizeRegisteredTodoAgents(input.registered_agents as string[]);
    const expectedPlan = input.expected_promotion_plan_sha256 === undefined
      ? null : promotionPlanDigest(input.expected_promotion_plan_sha256);
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

    result = await withShadowMaintenanceLock(input.runtime_root, input.goal_id, () =>
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
            legacy_fallback_used: false,
          };
        }
        const migration = planPromotionHandoffMigration(
          head,
          input.goal_id,
          handoffModeMigration,
          registeredAgents,
          new Date(),
        );
        const publicMigration = publicPromotionHandoffMigrationPlan(migration);
        if (!migration.ready) return {
          schema_version: schema,
          status: "not_ready",
          executed: false,
          reason_code: migration.reason_code ?? "handoff_mode_migration_conflict",
          reason: migration.reason ?? "handoff-mode migration is not ready",
          qualification: publicQualification,
          handoff_mode_migration: publicMigration,
          legacy_fallback_used: false,
        };
        const providerRevision = requireAuthorityStoreId(
          qualification.provider_revision,
          "qualified shadow provider revision",
        );
        const projectionSha256 = canonicalAuthoritySha256(head);
        const promotionPlanSha256 = localCoordinationPromotionPlanSha256({
          goal_id: input.goal_id,
          operation_id: operationId,
          canonical_authority: canonicalAuthority,
          expected_shadow_provider_revision: providerRevision,
          expected_shadow_projection_sha256: projectionSha256,
          minimum_operations: minimumOperations,
          required_event_kinds: requiredEventKinds,
          ...(explicitHandoffMigration ? {
            handoff_mode_migration: handoffModeMigration,
            registered_agents: registeredAgents,
            expected_target_projection_sha256: migration.target_projection_sha256,
          } : {}),
        });
        if (expectedPlan !== null && expectedPlan !== promotionPlanSha256) return {
          schema_version:schema, status:"not_ready", executed:false,
          reason_code:"local_authority_reviewed_plan_changed",
          reason:"The current promotion differs from the reviewed plan; preview and review the new plan.",
          expected_promotion_plan_sha256:expectedPlan,
          observed_promotion_plan_sha256:promotionPlanSha256,
          legacy_fallback_used:false,
        };
        const fence = canonicalAuthorityObject({
          schema_version: LEGACY_COORDINATION_WRITER_FENCE_SCHEMA,
          state: "engaged",
          goal_id: input.goal_id,
          fence_id: `legacy-writer-fence:${operationId}`,
          source_version: `shadow:${providerRevision}`,
          source_projection_sha256: projectionSha256,
          expected_shadow_provider_revision: providerRevision,
          promotion_plan_sha256: promotionPlanSha256,
        }, "reviewed legacy writer fence");
        const request: LocalCoordinationPromotionRequest = {
          runtime_root: input.runtime_root,
          goal_id: input.goal_id,
          operation_id: operationId,
          canonical_authority: canonicalAuthority,
          expected_shadow_provider_revision: providerRevision,
          expected_shadow_projection_sha256: projectionSha256,
          minimum_operations: minimumOperations,
          required_event_kinds: requiredEventKinds,
          ...(explicitHandoffMigration ? {
            handoff_mode_migration: handoffModeMigration,
            registered_agents: registeredAgents,
            expected_target_projection_sha256: migration.target_projection_sha256,
          } : {}),
          writer_fence: fence,
        };
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
              legacy_fallback_used: false,
            };
        }
        if (existing.status !== "missing") return {
          schema_version: schema,
          ...existing,
          executed: false,
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
            legacy_fallback_used: false,
          };
        }
        if (explicitHandoffMigration) requirePromotionRegisteredAgents(input.source_snapshot, registeredAgents);
        const plan = {
          reviewed_plan: reviewedPromotionPlan({schema_version:LOCAL_COORDINATION_PROMOTION_REQUEST_SCHEMA,...request}, promotionPlanSha256),
          operation_id: operationId,
          promotion_plan_sha256: promotionPlanSha256,
          canonical_authority: canonicalAuthority,
          expected_shadow_provider_revision: providerRevision,
          expected_shadow_projection_sha256: projectionSha256,
          minimum_operations: minimumOperations,
          required_event_kinds: [...requiredEventKinds].sort(authorityUnicodeCompare),
          handoff_mode_migration: publicMigration,
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
            legacy_fallback_used: false,
          };
          writerFenceVerified = true;
        }
        const identity = promotionIdentity(request);
        const attempted = await commitPromotionAndReadBack(canonical, {
          operation_id:operationId, receipt:identity,
          projection_sha256:promotionTargetProjectionSha256(request),
        }, migration.target_projection, {...identity,
          schema_version:"loopx_local_coordination_promotion_event_v0",
          mode_transition:`legacy_canonical_to_${canonicalAuthority}`,
          handoff_mode_transition:`${migration.previous_mode}_to_${migration.target_mode}`});
        const {commit:committed,readback}=attempted;
        if (readback.matched) return {
          schema_version: schema,
          ...promotionResult(
            request,
            committed?.status === "applied"
              ? recoveringFromFence ? "recovered" : "applied"
              : attempted.interrupted || committed?.status === "ambiguous" ? "recovered" : "replayed",
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
          reconciliation_required: attempted.interrupted || committed?.status === "ambiguous",
          qualification: publicQualification,
          legacy_fallback_used: false,
        };
      }),
    );
  } catch (error) {
    result = {
      schema_version: schema,
      status: "failed",
      executed: false,
      reason_code: error instanceof ShadowManagementError
        ? error.reason_code
        : "invalid_local_coordination_promotion_review_request",
      reason: error instanceof Error ? error.message : "promotion review unavailable",
      legacy_fallback_used: false,
      ...localAuthorityOpenFailure(error),
    };
  }
  // Qualification and plan mismatches return normally; exceptions are not the
  // only failed path. Report durable presence for every failed admission, not
  // whether this invocation got far enough to build or engage its own fence.
  if (result.status === "failed" || result.status === "not_ready") {
    let presence: boolean | null = null;
    if (sourceScope !== null) {
      try {
        const retained = await loadLegacyCoordinationWriterFence(sourceScope.runtimeRoot, sourceScope.goalId);
        presence = retained.status === "loaded" ? true : retained.status === "missing" ? false : null;
      } catch { /* Unreadable presence is unknown, never permission to write. */ }
    }
    result.legacy_writer_fenced = presence;
  }
  return result;
}

/** Monitor observation and successors share the existing writer/fence lifetime. */
export async function pollLocalCoordinationMonitor(value: unknown,
  dependencies: LocalAuthorityRuntimeDependencies = {}): Promise<JsonObject> {
  const evidence = {source_authority: "file_v0", decision_read_from_provider: true, legacy_fallback_used: false};
  try {
    const input = requireJsonObject(value, "local Monitor poll request");
    if (input.schema_version !== COORDINATION_MONITOR_POLL_REQUEST_SCHEMA &&
        input.schema_version !== COORDINATION_LEASED_MONITOR_POLL_REQUEST_SCHEMA &&
        input.schema_version !== COORDINATION_WITNESSED_MONITOR_POLL_REQUEST_SCHEMA &&
        input.schema_version !== COORDINATION_GUARDED_MONITOR_POLL_REQUEST_SCHEMA) throw new TypeError("Monitor poll schema mismatch");
    if (input.lease_proof != null && input.schema_version === COORDINATION_MONITOR_POLL_REQUEST_SCHEMA) {
      throw new TypeError("lease-backed Monitor poll requires request v1");
    }
    if (input.schema_version === COORDINATION_GUARDED_MONITOR_POLL_REQUEST_SCHEMA && input.gate_scope_guard !== true) {
      throw new TypeError("guarded Monitor request v3 requires gate_scope_guard=true");
    }
    if (input.gate_scope_guard === true && input.schema_version !== COORDINATION_GUARDED_MONITOR_POLL_REQUEST_SCHEMA) {
      throw new TypeError("commit-head Monitor gate scope requires request v3");
    }
    const authoritySourcesCurrent = registryAuthoritySourceCheck(input,
      input.schema_version === COORDINATION_WITNESSED_MONITOR_POLL_REQUEST_SCHEMA ||
      input.schema_version === COORDINATION_GUARDED_MONITOR_POLL_REQUEST_SCHEMA);
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
        lease_proof: proof, gate_scope_guard: input.gate_scope_guard == null ? false :
          requireBoolean(input.gate_scope_guard, "gate_scope_guard"), now: new Date(),
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
  createShadowStore?: (directory: string, goalId: string) => AuthorityStore;
  createCanonicalStore?: (directory: string, goalId: string) => AuthorityStore;
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
  canonical_authority: string;
  expected_shadow_provider_revision: string;
  expected_shadow_projection_sha256: string;
  minimum_operations: number;
  required_event_kinds: string[];
  handoff_mode_migration?: PromotionHandoffModeMigration;
  registered_agents?: string[];
  expected_target_projection_sha256?: string;
  writer_fence: JsonObject;
}

export interface LocalCoordinationPromotionPlanInput {
  goal_id: string;
  operation_id: string;
  canonical_authority: string;
  expected_shadow_provider_revision: string;
  expected_shadow_projection_sha256: string;
  minimum_operations: number;
  required_event_kinds: string[];
  handoff_mode_migration?: PromotionHandoffModeMigration;
  registered_agents?: string[];
  expected_target_projection_sha256?: string;
}

export function localCoordinationPromotionPlanSha256(
  value: LocalCoordinationPromotionPlanInput,
): string {
  const base = {
    goal_id: requireAuthorityStoreId(value.goal_id, "goal id"),
    operation_id: requireAuthorityStoreId(value.operation_id, "operation id"),
    canonical_authority: requireAuthorityStoreId(
      value.canonical_authority,
      "canonical authority",
    ),
    expected_shadow_provider_revision: requireAuthorityStoreId(
      value.expected_shadow_provider_revision,
      "expected shadow provider revision",
    ),
    expected_shadow_projection_sha256: requireAuthorityStoreId(
      value.expected_shadow_projection_sha256,
      "expected shadow projection sha256",
    ),
    minimum_operations: requiredPositiveSafeInteger(
      value.minimum_operations,
      "minimum_operations",
    ),
    required_event_kinds: requiredUniqueStrings(
      value.required_event_kinds,
      "required_event_kinds",
    ).sort(authorityUnicodeCompare),
  };
  const explicitMigration = value.handoff_mode_migration !== undefined;
  const plan = canonicalAuthorityObject(explicitMigration ? {
    schema_version: "loopx_local_coordination_promotion_plan_v1",
    ...base,
    handoff_mode_migration: normalizePromotionHandoffModeMigration(value.handoff_mode_migration),
    registered_agents: normalizeRegisteredTodoAgents(value.registered_agents ?? []),
    expected_target_projection_sha256: requireAuthorityStoreId(
      value.expected_target_projection_sha256,
      "expected target projection sha256",
    ),
  } : {
    schema_version: "loopx_local_coordination_promotion_plan_v0",
    ...base,
  }, "local coordination promotion plan");
  return canonicalAuthoritySha256(plan);
}

function decodePromotionRequest(value: unknown): LocalCoordinationPromotionRequest {
  const input = requireJsonObject(value, "local coordination promotion request");
  if (input.schema_version !== LOCAL_COORDINATION_PROMOTION_REQUEST_SCHEMA) {
    throw new Error("local coordination promotion request schema mismatch");
  }
  const fence = decodeLegacyCoordinationWriterFence(input.writer_fence);
  const explicitMigration = input.handoff_mode_migration !== undefined;
  if (explicitMigration && !Array.isArray(input.registered_agents)) {
    throw new Error("registered_agents must accompany handoff_mode_migration");
  }
  return {
    runtime_root: runtimeRoot(input.runtime_root),
    goal_id: requireAuthorityStoreId(input.goal_id, "goal id"),
    operation_id: requireAuthorityStoreId(input.operation_id, "operation id"),
    canonical_authority: requireAuthorityStoreId(
      input.canonical_authority,
      "canonical authority",
    ),
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
    ...(explicitMigration ? {
      handoff_mode_migration: normalizePromotionHandoffModeMigration(input.handoff_mode_migration),
      registered_agents: normalizeRegisteredTodoAgents(input.registered_agents as string[]),
      expected_target_projection_sha256: requireAuthorityStoreId(
        input.expected_target_projection_sha256,
        "expected target projection sha256",
      ),
    } : {}),
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
    promotion_plan_sha256: localCoordinationPromotionPlanSha256(request),
    ...(request.handoff_mode_migration === undefined ? {} : {
      handoff_mode_migration: request.handoff_mode_migration,
      target_projection_sha256: request.expected_target_projection_sha256,
    }),
  }, "local coordination promotion identity");
}

function promotionTargetProjectionSha256(request: LocalCoordinationPromotionRequest): string {
  return request.expected_target_projection_sha256 ?? request.expected_shadow_projection_sha256;
}

/** One receipt/lineage readback owner for reviewed and already-fenced promotion. */
function promotionReadback(store: AuthorityStore, request: LocalCoordinationPromotionRequest) {
  return readPromotionReceipt(store, {
    operation_id:request.operation_id, receipt:promotionIdentity(request),
    projection_sha256:promotionTargetProjectionSha256(request),
  });
}

function promotionResult(
  request: LocalCoordinationPromotionRequest,
  status: "applied" | "replayed" | "recovered",
  readback: Extract<Awaited<ReturnType<typeof promotionReadback>>, {matched:true}>,
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
    target_projection_sha256: promotionTargetProjectionSha256(request),
    writer_fence_id: request.writer_fence.fence_id,
    source_version: request.writer_fence.source_version,
    promotion_plan_sha256: localCoordinationPromotionPlanSha256(request),
    canonical_authority: canonicalAuthority,
    ...(request.handoff_mode_migration === undefined ? {} : {
      handoff_mode_migration: request.handoff_mode_migration,
    }),
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
  let execute = true;
  try {
    request = decodePromotionRequest(value);
    const raw=requireJsonObject(value,"promotion request");
    if(raw.execute !== undefined) {
      if(typeof raw.execute !== "boolean") throw new TypeError("execute must be a JSON boolean");
      execute=raw.execute;
    }
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
  let commitAttempted = false;
  try {
    return await withCanonicalWriter(request.runtime_root, request.goal_id, false, async () => {
      const shadow = dependencies.createShadowStore?.(
        shadowDirectory(request.runtime_root),
        request.goal_id,
      ) ?? new FileAuthorityStore(shadowDirectory(request.runtime_root), request.goal_id);
      const canonical = dependencies.createCanonicalStore?.(
        authorityDirectory(request.runtime_root),
        request.goal_id,
      ) ?? await openRuntimeStore(request.runtime_root, request.goal_id, dependencies);
      const canonicalAuthority = sourceAuthorityFor(canonical);
      if (request.canonical_authority !== canonicalAuthority) return {
        schema_version: LOCAL_COORDINATION_PROMOTION_RESULT_SCHEMA,
        status: "failed",
        reason_code: "local_authority_promotion_provider_mismatch",
        reason: "promotion request is not bound to the selected canonical authority provider",
        expected_canonical_authority: request.canonical_authority,
        observed_canonical_authority: canonicalAuthority,
        legacy_writer_fenced: false,
        legacy_fallback_used: false,
      };
      const promotionPlanSha256 = localCoordinationPromotionPlanSha256(request);
      if (request.writer_fence.promotion_plan_sha256 !== promotionPlanSha256) return {
        schema_version: LOCAL_COORDINATION_PROMOTION_RESULT_SCHEMA,
        status: "failed",
        reason_code: "local_authority_writer_fence_plan_mismatch",
        reason: "writer fence is not bound to the complete reviewed promotion plan",
        promotion_plan_sha256: promotionPlanSha256,
        legacy_writer_fenced: false,
        legacy_fallback_used: false,
      };
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

      const qualification = await qualifyCoordinationShadowLineageUnderLocks({
        runtime_root: request.runtime_root,
        goal_id: request.goal_id,
        projection: shadowHead.head,
      }, {createStore: () => shadow}, request.minimum_operations, request.required_event_kinds);
      if (qualification.status !== "qualified" || qualification.qualified !== true) return {
        schema_version: LOCAL_COORDINATION_PROMOTION_RESULT_SCHEMA,
        status: "failed",
        reason_code: "local_authority_shadow_not_qualified",
        reason: "shadow parity evidence does not satisfy the promotion policy",
        qualification_status: qualification.status,
        legacy_writer_fenced: true,
        legacy_fallback_used: false,
      };
      // A default reviewed promotion still requires the shadow's own
      // hard_lease policy; an explicit migration request supplies the target.
      const migration = planPromotionHandoffMigration(
        shadowHead.head,
        request.goal_id,
        request.handoff_mode_migration,
        request.registered_agents ?? [],
        new Date(),
      );
      if (!migration.ready) return {
        schema_version: LOCAL_COORDINATION_PROMOTION_RESULT_SCHEMA,
        status: "failed",
        reason_code: migration.reason_code ?? "handoff_mode_migration_conflict",
        reason: migration.reason ?? "handoff-mode migration is not ready",
        handoff_mode_migration: publicPromotionHandoffMigrationPlan(migration),
        legacy_writer_fenced: true,
        legacy_fallback_used: false,
      };
      if (migration.target_projection_sha256 !== promotionTargetProjectionSha256(request)) return {
        schema_version: LOCAL_COORDINATION_PROMOTION_RESULT_SCHEMA,
        status: "failed",
        reason_code: "local_authority_promotion_target_projection_mismatch",
        reason: "handoff-mode migration target differs from the reviewed promotion plan",
        observed_target_projection_sha256: migration.target_projection_sha256,
        expected_target_projection_sha256: promotionTargetProjectionSha256(request),
        legacy_writer_fenced: true,
        legacy_fallback_used: false,
      };

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

      const finalMigration = planPromotionHandoffMigration(
        finalShadowHead.head,
        request.goal_id,
        request.handoff_mode_migration,
        request.registered_agents ?? [],
        new Date(),
      );
      if (!finalMigration.ready ||
          finalMigration.target_projection_sha256 !== promotionTargetProjectionSha256(request)) return {
        schema_version: LOCAL_COORDINATION_PROMOTION_RESULT_SCHEMA,
        status: "failed",
        reason_code: finalMigration.reason_code ?? "local_authority_promotion_target_projection_mismatch",
        reason: finalMigration.reason ?? "handoff-mode migration changed during qualification",
        handoff_mode_migration: publicPromotionHandoffMigrationPlan(finalMigration),
        legacy_writer_fenced: true,
        legacy_fallback_used: false,
      };

      if(!execute) return {
        schema_version:LOCAL_COORDINATION_PROMOTION_RESULT_SCHEMA,
        status:"recovery_ready", executed:false, operation_id:request.operation_id,
        promotion_plan_sha256:promotionPlanSha256, canonical_authority:canonicalAuthority,
        legacy_writer_fenced:true, legacy_fallback_used:false,
      };
      const identity = promotionIdentity(request);
      commitAttempted = true;
      const attempted=await commitPromotionAndReadBack(canonical, {
        operation_id:request.operation_id,receipt:identity,
        projection_sha256:promotionTargetProjectionSha256(request),
      }, finalMigration.target_projection, {...identity,
        schema_version:"loopx_local_coordination_promotion_event_v0",
        mode_transition:`legacy_canonical_to_${canonicalAuthority}`,
        handoff_mode_transition:`${finalMigration.previous_mode}_to_${finalMigration.target_mode}`});
      const {commit:committed,readback}=attempted;
      if(readback.matched) return promotionResult(request,
        committed?.status === "applied" ? "applied"
          : attempted.interrupted || committed?.status === "ambiguous" ? "recovered" : "replayed",
        readback,canonicalAuthority);
      return {
        schema_version:LOCAL_COORDINATION_PROMOTION_RESULT_SCHEMA,
        ...(committed ?? {}), status:"failed", executed:true,
        reason_code:readback.reason_code,
        reason:"promotion did not produce an exact durable readback",
        reconciliation_required:attempted.interrupted || committed?.status === "ambiguous",
        legacy_writer_fenced:true, legacy_fallback_used:false,
      };
    });
  } catch (error) {
    return {
      schema_version: LOCAL_COORDINATION_PROMOTION_RESULT_SCHEMA,
      status: "failed", executed:commitAttempted,
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
        input.schema_version !== COORDINATION_TODO_OBSERVATION_UPDATE_REQUEST_SCHEMA &&
        input.schema_version !== COORDINATION_TODO_VALIDATION_REVISION_REQUEST_SCHEMA) {
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
    const validationRevisionUpdate = input.schema_version === COORDINATION_TODO_VALIDATION_REVISION_REQUEST_SCHEMA;
    if (!observationUpdate && Object.hasOwn(input, "monitor_observation")) {
      throw new TypeError("Monitor observation payload requires request v4");
    }
    if (observationUpdate && input.monitor_observation == null) throw new TypeError("Monitor update requires its observation payload");
    if (!completionUpdate && Object.hasOwn(input, "completion")) {
      throw new TypeError("Todo completion payload requires request v3");
    }
    if (completionUpdate && input.completion == null) throw new TypeError("Todo completion update requires its completion payload");
    if (!validationRevisionUpdate && Object.hasOwn(input, "completion_validation_revision")) {
      throw new TypeError("Completion validation revision payload requires request v5");
    }
    if (validationRevisionUpdate && input.completion_validation_revision == null) {
      throw new TypeError("Completion validation revision update requires its revision payload");
    }
    const reviewed = observationUpdate || completionUpdate || validationRevisionUpdate ||
      input.schema_version === COORDINATION_TODO_REVIEWED_UPDATE_REQUEST_SCHEMA;
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
        ...(validationRevisionUpdate ? {completion_validation_revision:
          decodeCompletionValidationRevision(input.completion_validation_revision)} : {}),
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
    if (input.schema_version !== LOCAL_COORDINATION_TODO_TERMINAL_LIFECYCLE_REQUEST_SCHEMA) {
      throw new TypeError("local coordination Todo terminal request schema mismatch; regenerate with the current runtime");
    }
    const operationIntent = decodeTerminalOperationIntent(input);
    const reviewBasis = input.review_basis == null ? undefined : requireJsonObject(input.review_basis, "terminal review basis");
    const authoritySourcesCurrent = registryAuthoritySourceCheck(input, true, reviewBasis?.registry_sha256);
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
        validation_source_provider_revision: input.validation_source_provider_revision == null
          ? null : requireAuthorityStoreId(input.validation_source_provider_revision, "validation source provider revision"),
        validation_declaration_sha256: input.validation_declaration_sha256 == null
          ? null : requireAuthorityStoreId(input.validation_declaration_sha256, "validation declaration digest"),
        ...(reviewBasis === undefined ? {} : {review_basis: {
          ...reviewBasis,
          provider_revision: requireAuthorityStoreId(reviewBasis.provider_revision, "review provider revision"),
          registry_sha256: requireAuthorityStoreId(reviewBasis.registry_sha256, "review registry digest"),
        }}),
        goal_id: goalId,
        todo_id: requireAuthorityStoreId(input.todo_id, "todo id"),
        expected_role: input.role === null || input.role === undefined
          ? null : requireAuthorityStoreId(input.role, "role") as "agent" | "user",
        ...operationIntent,
        actor_agent_id: input.actor_agent_id === null || input.actor_agent_id === undefined
          ? null : claimAgentValue(input.actor_agent_id, "actor_agent_id"),
        registered_agents: registeredAgents,
        lifecycle_grants: lifecycleGrants,
        authority_reason: input.authority_reason === null || input.authority_reason === undefined
          ? null : claimAgentValue(input.authority_reason, "authority_reason"),
        decision_outcome: input.decision_outcome === null || input.decision_outcome === undefined
          ? null : requireAuthorityStoreId(input.decision_outcome, "decision_outcome") as
            "approve" | "reject" | "cancel",
        lease_idempotency_key:
          input.lease_idempotency_key === null || input.lease_idempotency_key === undefined
            ? null : requireAuthorityStoreId(input.lease_idempotency_key, "lease idempotency key"),
        lease_expected_version: leaseExpectedVersion,
        allow_user_gate_auto_acquire: input.allow_user_gate_auto_acquire as boolean,
        requested_no_followup: input.requested_no_followup as boolean,
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
        completion_result: input.completion_result == null
          ? null : requireJsonObject(input.completion_result, "completion_result"),
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
        reason: "Use an explicitly promoted canonical authority; this command never promotes or falls back to Markdown"};
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

/** Saved review data cannot grant a fence or authorize another Goal. */
export async function executeReviewedCoordinationPromotion(
  value: unknown, dependencies: LocalAuthorityRuntimeDependencies = {},
): Promise<JsonObject> {
  try {
    const input=decodeReviewedPromotionOperation(value);
    const request=decodePromotionRequest(input.request);
    const digest=localCoordinationPromotionPlanSha256(request);
    if(digest !== input.expected_plan_sha256 || request.writer_fence.promotion_plan_sha256 !== digest) {
      throw new TypeError("reviewed promotion request does not match its plan digest");
    }
    const result=input.action === "recover"
      ? await promoteLocalCoordinationAuthority({...input.request,execute:input.execute},dependencies)
      : await reviewLocalCoordinationAuthorityPromotion({
        schema_version:LOCAL_COORDINATION_PROMOTION_REVIEW_REQUEST_SCHEMA,
        runtime_root:input.runtime_root,goal_id:input.goal_id,
        operation_id:request.operation_id,minimum_operations:request.minimum_operations,
        required_event_kinds:request.required_event_kinds,execute:input.execute,
        expected_promotion_plan_sha256:input.expected_plan_sha256,
        ...(request.handoff_mode_migration === undefined ? {} : {
          handoff_mode_migration: request.handoff_mode_migration,
          registered_agents: request.registered_agents,
        }),
        projection:input.projection,source_snapshot:input.source_snapshot,
      },dependencies);
    return {...result, reviewed_plan_sha256:input.expected_plan_sha256,
      reviewed_action:input.action, executed:result.executed === true || (input.execute &&
        ["applied","recovered"].includes(String(result.status)))};
  } catch(error) {
    return {schema_version:REVIEWED_PROMOTION_OPERATION_RESULT_SCHEMA,
      status:"failed",executed:false,reason_code:"invalid_reviewed_promotion_plan",
      reason:error instanceof Error ? error.message : "reviewed promotion is unavailable",
      legacy_writer_fenced:null,legacy_fallback_used:false};
  }
}
