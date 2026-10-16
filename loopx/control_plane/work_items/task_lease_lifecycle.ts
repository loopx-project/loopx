import {mutateCanonicalTaskLease} from "./canonical_task_lease_lifecycle.ts";
import {taskLeaseStableValue as stableValue, taskLeaseDigest as digest,
  taskLeaseOperationIdentity as operationIdentity,
  taskLeaseOperationRequestDigest as operationRequestDigest} from "./task_lease_operation_identity.ts";
import { ShadowManagementError } from "../coordination/shadow_management.ts";
import { LegacyCoordinationWriteError, requireLegacyCoordinationPrimaryWriteAllowed } from "../coordination/legacy_writer_fence.ts";
import { createHash } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import { join } from "node:path";

import {
  EffectRuntimeConflictError,
  EffectRuntimeLockTimeoutError,
  EffectRuntimeRequestError,
  effectRuntimeErrorPayload,
} from "../effect_runtime_errors.ts";
import {
  acquireFileMutationLock,
  atomicWriteJson,
  claimFileMutationLock,
  mutationLockOwner,
  releaseFileMutationLock,
  releaseFileMutationLockClaim,
  withFileMutationLock,
  type FileMutationLockClaim,
} from "../effect_runtime_io.ts";
import {
  legacyCoordinationLeaseLockPath,
} from "../coordination/legacy_writer_fence.ts";
import {
  settlementIdentity,
  type JsonObject,
} from "../effect_program.ts";
import { requireJsonObject } from "../runtime_decode.ts";
import {
  leaseEpoch,
  leaseInteger,
  leaseIsActive,
  leaseVersion,
  normalizeAgent,
  normalizeGoalId,
  normalizeIdempotencyKey,
  normalizeOwner,
  normalizeTodoId,
  readLease,
  TaskLeaseAcquireError,
  taskLeaseDirectory,
  taskLeaseLockPath,
  taskLeasePath,
  type AuthorityFacts,
  type LeaseRecord,
} from "./task_lease_acquire.ts";
import {
  decideTaskLeaseLifecycle,
  releasedTaskLeaseRecord as releasedLease,
  type TaskLeaseLifecycleDecision,
} from "./task_lease_lifecycle_decision.ts";
import {decodeTaskLeaseLifecycleRequest, TaskLeaseLifecycleError, TASK_LEASE_LIFECYCLE_OPERATIONS,
  type LifecycleRequest, type CanonicalLifecycleRequest, type LifecycleErrorInfo, type LifecycleStage,
  type TaskLeaseLifecycleOperation} from "./task_lease_lifecycle_request.ts";
import { BARE_SHA256_PATTERN } from "../content_digest.ts";


// These exported names have existing direct callers; implementation is owned
// by the request boundary rather than the legacy held-fence executor.
export {TASK_LEASE_LIFECYCLE_REQUEST_SCHEMA_VERSION, TASK_LEASE_LIFECYCLE_OPERATIONS,
  type TaskLeaseLifecycleOperation} from "./task_lease_lifecycle_request.ts";
export const TASK_LEASE_LIFECYCLE_RECEIPT_SCHEMA = "task_lease_lifecycle_receipt_v0";
export const TASK_LEASE_FENCE_RECEIPT_SCHEMA = "task_lease_fence_receipt_v0";

interface LifecycleDependencies {
  now?: () => Date;
  beforeWrite?: (lease: JsonObject) => void | Promise<void>;
  authorityProvider?: import("../coordination/local_authority_provider.ts").LocalAuthorityProviderDependencies;
}


function lifecycleNow(
  request: Pick<LifecycleRequest, "current_time">,
  dependencies: LifecycleDependencies,
): Date {
  const at = dependencies.now?.() ?? request.current_time ?? new Date();
  if (Number.isNaN(at.valueOf())) {
    throw new TaskLeaseLifecycleError(
      "task-lease lifecycle clock returned an invalid date",
      "invalid_clock",
    );
  }
  return at;
}

function leasePathFor(request: Pick<LifecycleRequest, "runtime_root" | "goal_id" | "todo_id">): string {
  return taskLeasePath(request);
}

function lockPathFor(request: Pick<LifecycleRequest, "runtime_root" | "goal_id">): string {
  return taskLeaseLockPath(request);
}

function effectIdFor(request: Pick<LifecycleRequest, "goal_id" | "todo_id" | "owner" | "idempotency_key">): string | null {
  if (!request.owner || !request.idempotency_key) return null;
  return settlementIdentity({
    goal_id: request.goal_id,
    agent_id: request.owner,
    todo_id: request.todo_id,
    turn_instance_id: request.idempotency_key,
  }).effect_id;
}

function operationReceiptPath(request: LifecycleRequest): string | null {
  const id = operationIdentity(request);
  if (!id) return null;
  return join(taskLeaseDirectory(request), ".lifecycle-operations", `${id}.json`);
}

function fenceOperationId(request: LifecycleRequest): string {
  if (request.fence_operation_id) return request.fence_operation_id;
  // A verify request has no durable caller-side handle until its response is
  // received.  Derive one from its execution identity so the managed runtime
  // can replay a response after a transport loss.  The close path normally
  // carries the returned id explicitly; its fallback is only for older
  // callers that know the verified lease tuple but not this bridge field.
  const verifyKind = request.operation === "fence_close"
    ? request.fence_idempotency_key === null ? "holder_verify" : "terminal_verify"
    : request.operation;
  return digest({
    schema_version: TASK_LEASE_FENCE_RECEIPT_SCHEMA,
    kind: verifyKind,
    goal_id: request.goal_id,
    todo_id: request.todo_id,
    owner: request.operation === "fence_close"
      ? request.fence_owner
      : request.owner,
    idempotency_key: request.operation === "fence_close"
      ? request.fence_idempotency_key
      : request.idempotency_key,
    expected_version: request.operation === "fence_close"
      ? request.fence_expected_version
      : request.expected_version,
    expected_lease_epoch: request.operation === "fence_close"
      ? request.fence_expected_lease_epoch
      : null,
  });
}

function fenceRequestDigest(request: LifecycleRequest): string {
  return digest({
    schema_version: TASK_LEASE_FENCE_RECEIPT_SCHEMA,
    operation: request.operation,
    goal_id: request.goal_id,
    todo_id: request.todo_id,
    owner: request.owner,
    idempotency_key: request.idempotency_key,
    expected_version: request.expected_version,
    delegated_authority: request.delegated_authority,
    allow_user_gate_auto_acquire: request.allow_user_gate_auto_acquire,
    require_active_when_fence_supplied: request.require_active_when_fence_supplied,
  });
}

function fenceCloseRequestDigest(request: LifecycleRequest): string {
  return digest({
    schema_version: TASK_LEASE_FENCE_RECEIPT_SCHEMA,
    operation: request.operation,
    goal_id: request.goal_id,
    todo_id: request.todo_id,
    lock_token: request.lock_token,
    committed: request.committed,
    release_lease: request.release_lease,
    fence_owner: request.fence_owner,
    fence_idempotency_key: request.fence_idempotency_key,
    fence_expected_version: request.fence_expected_version,
    fence_expected_lease_epoch: request.fence_expected_lease_epoch,
  });
}

function fenceReceiptPath(
  request: LifecycleRequest,
  operationId = fenceOperationId(request),
): string {
  return join(
    taskLeaseDirectory(request),
    ".lifecycle-fences",
    `${operationId}.json`,
  );
}

function fenceReceiptError(path: string, message: string): TaskLeaseLifecycleError {
  return new TaskLeaseLifecycleError(
    message,
    "corrupt_fence_receipt",
    { receipt_path: path },
    "validation",
  );
}

async function readFenceReceipt(
  path: string,
  request: LifecycleRequest | null = null,
): Promise<FenceReceipt | null> {
  try {
    const raw = JSON.parse(await readFile(path, "utf8"));
    const record = requireJsonObject(raw, "task lease fence receipt");
    const state = record.state;
    if (
      record.schema_version !== TASK_LEASE_FENCE_RECEIPT_SCHEMA ||
      typeof record.operation_id !== "string" ||
      !BARE_SHA256_PATTERN.test(record.operation_id) ||
      typeof record.request_digest !== "string" ||
      !BARE_SHA256_PATTERN.test(record.request_digest) ||
      (record.close_request_digest !== undefined &&
        record.close_request_digest !== null &&
        (typeof record.close_request_digest !== "string" ||
          !BARE_SHA256_PATTERN.test(record.close_request_digest))) ||
      !["acquired", "held", "closed"].includes(state as string) ||
      typeof record.goal_id !== "string" ||
      typeof record.todo_id !== "string" ||
      typeof record.owner_pid !== "number" ||
      !Number.isSafeInteger(record.owner_pid) ||
      record.owner_pid <= 0 ||
      typeof record.lock_token !== "string" ||
      record.lock_token.length === 0 ||
      (record.fence_owner !== undefined && record.fence_owner !== null &&
        typeof record.fence_owner !== "string") ||
      (record.fence_idempotency_key !== undefined &&
        record.fence_idempotency_key !== null &&
        typeof record.fence_idempotency_key !== "string") ||
      (record.fence_expected_version !== undefined &&
        record.fence_expected_version !== null &&
        (typeof record.fence_expected_version !== "number" ||
          !Number.isSafeInteger(record.fence_expected_version))) ||
      (record.fence_expected_lease_epoch !== undefined &&
        record.fence_expected_lease_epoch !== null &&
        (typeof record.fence_expected_lease_epoch !== "number" ||
          !Number.isSafeInteger(record.fence_expected_lease_epoch))) ||
      (record.lease !== undefined && record.lease !== null &&
        (typeof record.lease !== "object" || Array.isArray(record.lease))) ||
      (record.response !== undefined && record.response !== null &&
        (typeof record.response !== "object" || Array.isArray(record.response)))
      || (record.verify_response !== undefined &&
        record.verify_response !== null &&
        (typeof record.verify_response !== "object" ||
          Array.isArray(record.verify_response)))
    ) {
      throw fenceReceiptError(path, "task lease fence receipt is malformed");
    }
    const normalizedGoal = normalizeGoalId(record.goal_id);
    const normalizedTodo = normalizeTodoId(record.todo_id);
    if (normalizedGoal !== record.goal_id || normalizedTodo !== record.todo_id) {
      throw fenceReceiptError(path, "task lease fence receipt identity is not normalized");
    }
    if (request && (
      record.goal_id !== request.goal_id ||
      record.todo_id !== request.todo_id
    )) {
      throw fenceReceiptError(path, "task lease fence receipt identity does not match the request");
    }
    if (request && request.operation !== "fence_close" &&
        record.request_digest !== fenceRequestDigest(request)) {
      throw new TaskLeaseLifecycleError(
        "task lease fence operation id was reused with different verification parameters",
        "fence_operation_reuse",
        { receipt_path: path },
        "validation",
      );
    }
    if (request?.operation === "fence_close" &&
        record.close_request_digest !== undefined &&
        record.close_request_digest !== null &&
        record.close_request_digest !== fenceCloseRequestDigest(request)) {
      throw new TaskLeaseLifecycleError(
        "task lease fence close was retried with different parameters",
        "fence_operation_reuse",
        { receipt_path: path },
        "validation",
      );
    }
    const basename = path.split(/[\\/]/u).pop() ?? "";
    if (basename !== `${record.operation_id}.json`) {
      throw fenceReceiptError(path, "task lease fence receipt path does not match its operation id");
    }
    return {
      schema_version: TASK_LEASE_FENCE_RECEIPT_SCHEMA,
      operation_id: record.operation_id,
      request_digest: record.request_digest,
      close_request_digest: record.close_request_digest === undefined
        ? null
        : record.close_request_digest as string,
      state: state as FenceReceiptState,
      goal_id: record.goal_id,
      todo_id: record.todo_id,
      owner_pid: record.owner_pid,
      lock_token: record.lock_token,
      fence_owner: record.fence_owner === undefined
        ? null
        : record.fence_owner as string | null,
      fence_idempotency_key: record.fence_idempotency_key === undefined
        ? null
        : record.fence_idempotency_key as string | null,
      fence_expected_version: record.fence_expected_version === undefined
        ? null
        : record.fence_expected_version as number | null,
      fence_expected_lease_epoch: record.fence_expected_lease_epoch === undefined
        ? null
        : record.fence_expected_lease_epoch as number | null,
      lease: record.lease === undefined ? null : record.lease as LeaseRecord | null,
      response: record.response === undefined
        ? null
        : record.response as JsonObject | null,
      verify_response: record.verify_response === undefined
        ? null
        : record.verify_response as JsonObject | null,
    };
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
    if (error instanceof TaskLeaseLifecycleError) throw error;
    if (error instanceof SyntaxError) {
      throw fenceReceiptError(path, "task lease fence receipt is not valid JSON");
    }
    throw fenceReceiptError(path, "task lease fence receipt is malformed");
  }
}

async function persistFenceReceipt(
  request: LifecycleRequest,
  state: FenceReceiptState,
  lockToken: string,
  options: {
    lease: LeaseRecord | null;
    response: JsonObject | null;
    operationId?: string;
    requestDigest?: string;
    ownerPid?: number;
    fenceOwner?: string | null;
    fenceIdempotencyKey?: string | null;
    fenceExpectedVersion?: number | null;
    fenceExpectedLeaseEpoch?: number | null;
    verifyResponse?: JsonObject | null;
    closeRequestDigest?: string | null;
    base?: FenceReceipt | null;
  },
): Promise<void> {
  const operationId = options.operationId ?? fenceOperationId(request);
  const base = options.base;
  const choose = <T>(
    value: T | undefined,
    fallback: T | null | undefined,
  ): T | null => value !== undefined ? value : fallback ?? null;
  const verifyResponse = options.verifyResponse !== undefined
    ? options.verifyResponse
    : base?.verify_response ?? (
      state === "held"
        ? options.response
        : state === "closed" &&
            base?.response !== null &&
            base?.response !== undefined &&
            base.response.action !== "fence_close"
          ? base.response
          : null
    );
  const closeRequestDigest = options.closeRequestDigest !== undefined
    ? options.closeRequestDigest
    : state === "held"
      ? null
      : base?.close_request_digest ?? null;
  await atomicWriteJson(fenceReceiptPath(request, operationId), {
    schema_version: TASK_LEASE_FENCE_RECEIPT_SCHEMA,
    operation_id: operationId,
    request_digest: options.requestDigest ?? base?.request_digest ?? fenceRequestDigest(request),
    close_request_digest: closeRequestDigest,
    state,
    goal_id: base?.goal_id ?? request.goal_id,
    todo_id: base?.todo_id ?? request.todo_id,
    owner_pid: options.ownerPid ?? base?.owner_pid ?? request.owner_pid ?? process.pid,
    lock_token: lockToken,
    fence_owner: choose(options.fenceOwner, base?.fence_owner),
    fence_idempotency_key: choose(options.fenceIdempotencyKey, base?.fence_idempotency_key),
    fence_expected_version: choose(options.fenceExpectedVersion, base?.fence_expected_version),
    fence_expected_lease_epoch: choose(options.fenceExpectedLeaseEpoch, base?.fence_expected_lease_epoch),
    lease: options.lease,
    response: options.response,
    verify_response: verifyResponse,
  });
}

async function findFenceReceiptByToken(
  request: LifecycleRequest,
): Promise<{ path: string; receipt: FenceReceipt } | null> {
  const directory = join(taskLeaseDirectory(request), ".lifecycle-fences");
  let names: string[];
  try {
    names = await readdir(directory);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw error;
  }
  const matches: Array<{ path: string; receipt: FenceReceipt }> = [];
  for (const name of names) {
    if (!name.endsWith(".json")) continue;
    const path = join(directory, name);
    const receipt = await readFenceReceipt(path);
    if (
      receipt &&
      receipt.goal_id === request.goal_id &&
      receipt.todo_id === request.todo_id &&
      receipt.lock_token === request.lock_token
    ) {
      matches.push({ path, receipt });
    }
  }
  if (matches.length > 1) {
    throw fenceReceiptError(
      directory,
      "multiple task lease fence receipts match the lock token",
    );
  }
  return matches[0] ?? null;
}

function fenceLeaseMatchesReceipt(
  lease: LeaseRecord | null,
  receipt: FenceReceipt | null,
): boolean {
  if (!lease || !receipt) return false;
  try {
    return (
      receipt.fence_owner !== null &&
      persistedOwner(lease) === receipt.fence_owner &&
      receipt.fence_expected_version !== null &&
      leaseVersion(lease) === receipt.fence_expected_version &&
      receipt.fence_expected_lease_epoch !== null &&
      leaseEpoch(lease) === receipt.fence_expected_lease_epoch &&
      (receipt.fence_idempotency_key === null ||
        persistedKey(lease) === receipt.fence_idempotency_key)
    );
  } catch {
    return false;
  }
}

function fenceVerificationResponse(
  receipt: FenceReceipt | null,
): JsonObject | null {
  if (!receipt) return null;
  if (receipt.verify_response) return receipt.verify_response;
  // Receipts written by the first native version stored the verification
  // payload directly in `response` while the fence was held. Preserve that
  // read-only compatibility shape, but never treat a close response as proof.
  if (receipt.state === "held" && receipt.response?.action !== "fence_close") {
    return receipt.response;
  }
  return null;
}

function isVerifiedHeldFence(receipt: FenceReceipt | null): boolean {
  if (!receipt || receipt.state !== "held") return false;
  const verification = fenceVerificationResponse(receipt);
  if (!verification || verification.lock_token !== receipt.lock_token) return false;
  return verification.active === true && (
    verification.execution_instance_verified === true ||
    verification.checked === true
  );
}

interface OperationReceipt extends JsonObject {
  schema_version: typeof TASK_LEASE_LIFECYCLE_RECEIPT_SCHEMA;
  operation_id: string;
  request_digest: string;
  state: "prepared" | "committed";
  planned_lease: LeaseRecord | null;
  response: JsonObject;
}

type FenceReceiptState = "acquired" | "held" | "closed";

interface FenceReceipt extends JsonObject {
  schema_version: typeof TASK_LEASE_FENCE_RECEIPT_SCHEMA;
  operation_id: string;
  request_digest: string;
  close_request_digest: string | null;
  state: FenceReceiptState;
  goal_id: string;
  todo_id: string;
  owner_pid: number;
  lock_token: string;
  fence_owner: string | null;
  fence_idempotency_key: string | null;
  fence_expected_version: number | null;
  fence_expected_lease_epoch: number | null;
  lease: LeaseRecord | null;
  response: JsonObject | null;
  verify_response: JsonObject | null;
}

function corruptLease(
  message: string,
  leasePath: string,
  details: JsonObject = {},
): TaskLeaseLifecycleError {
  return new TaskLeaseLifecycleError(
    message,
    "corrupt_lease",
    { lease_path: leasePath, ...details },
    "validation",
  );
}

/**
 * Validate only the persisted fields that lifecycle CAS depends on.  The
 * record codec intentionally keeps unknown fields untouched so older lease
 * producers remain readable, while malformed identity/generation fields fail
 * closed before any lifecycle write.
 */
function validatePersistedLease(
  lease: LeaseRecord,
  request: LifecycleRequest,
  leasePath: string,
): LeaseRecord {
  if (lease.schema_version !== "task_lease_v0") {
    throw corruptLease("lease record schema is unsupported", leasePath, {
      schema_version: lease.schema_version ?? null,
    });
  }
  if (typeof lease.goal_id !== "string" || lease.goal_id !== request.goal_id) {
    throw corruptLease("lease record goal identity does not match the request", leasePath, {
      goal_id: lease.goal_id ?? null,
      expected_goal_id: request.goal_id,
    });
  }
  if (typeof lease.todo_id !== "string" || lease.todo_id !== request.todo_id) {
    throw corruptLease("lease record todo identity does not match the request", leasePath, {
      todo_id: lease.todo_id ?? null,
      expected_todo_id: request.todo_id,
    });
  }
  try {
    normalizeOwner(lease.owner);
  } catch {
    throw corruptLease("lease record owner is invalid", leasePath, {
      owner: lease.owner ?? null,
    });
  }
  try {
    normalizeIdempotencyKey(lease.idempotency_key);
  } catch {
    throw corruptLease("lease record idempotency key is invalid", leasePath);
  }
  if (lease.status !== "active" && lease.status !== "released") {
    throw corruptLease("lease record status is invalid", leasePath, {
      status: lease.status ?? null,
    });
  }
  // These helpers reject bools, fractional values, and negative generations,
  // while preserving the legacy defaults (version 0, epoch 1).
  try {
    leaseVersion(lease);
    leaseEpoch(lease);
  } catch (error) {
    if (error instanceof TaskLeaseAcquireError) {
      throw corruptLease(error.message, leasePath, error.payload);
    }
    throw error;
  }
  if (lease.write_scopes !== undefined) {
    if (!Array.isArray(lease.write_scopes) || lease.write_scopes.some((scope) => typeof scope !== "string")) {
      throw corruptLease("lease record write_scopes must be an array of strings", leasePath);
    }
  }
  return lease;
}

function persistedOwner(lease: LeaseRecord): string {
  try {
    return normalizeOwner(lease.owner);
  } catch {
    throw new TaskLeaseLifecycleError(
      "lease record owner is invalid",
      "corrupt_lease",
      { owner: lease.owner ?? null },
      "validation",
    );
  }
}

function persistedKey(lease: LeaseRecord): string {
  try {
    return normalizeIdempotencyKey(lease.idempotency_key);
  } catch {
    throw new TaskLeaseLifecycleError(
      "lease record idempotency key is invalid",
      "corrupt_lease",
      {},
      "validation",
    );
  }
}

async function readOperationReceipt(
  path: string | null,
  request: LifecycleRequest,
  leasePath: string,
): Promise<OperationReceipt | null> {
  if (!path) return null;
  try {
    const raw = JSON.parse(await readFile(path, "utf8"));
    let record: JsonObject;
    try {
      record = requireJsonObject(raw, "task lease lifecycle receipt");
    } catch {
      throw new TaskLeaseLifecycleError(
        "task lease lifecycle receipt root must be a JSON object",
        "corrupt_lifecycle_receipt",
        { receipt_path: path },
        "validation",
      );
    }
    const expectedOperationId = operationIdentity(request);
    const expectedRequestDigest = operationRequestDigest(request);
    if (
      record.schema_version !== TASK_LEASE_LIFECYCLE_RECEIPT_SCHEMA ||
      typeof record.operation_id !== "string" ||
      !BARE_SHA256_PATTERN.test(record.operation_id) ||
      record.operation_id !== expectedOperationId ||
      typeof record.request_digest !== "string" ||
      !BARE_SHA256_PATTERN.test(record.request_digest) ||
      (record.state !== "prepared" && record.state !== "committed") ||
      (record.planned_lease !== null &&
        (typeof record.planned_lease !== "object" ||
          Array.isArray(record.planned_lease))) ||
      record.planned_lease === null ||
      typeof record.response !== "object" || record.response === null || Array.isArray(record.response)
    ) {
      throw new TaskLeaseLifecycleError(
        "task lease lifecycle receipt is malformed",
        "corrupt_lifecycle_receipt",
        { receipt_path: path },
        "validation",
      );
    }
    let plannedLease: LeaseRecord;
    try {
      plannedLease = validatePersistedLease(
        record.planned_lease as LeaseRecord,
        request,
        leasePath,
      );
    } catch (error) {
      if (error instanceof TaskLeaseLifecycleError && error.code === "corrupt_lease") {
        throw new TaskLeaseLifecycleError(
          "task lease lifecycle receipt contains an invalid planned lease",
          "corrupt_lifecycle_receipt",
          { receipt_path: path },
          "validation",
        );
      }
      throw error;
    }
    const response = record.response as JsonObject;
    if (
      response.ok !== true ||
      response.schema_version !== "task_lease_v0" ||
      response.action !== request.operation ||
      typeof response.lease !== "object" ||
      response.lease === null ||
      Array.isArray(response.lease) ||
      !plannedLeaseMatches(response.lease as LeaseRecord, plannedLease)
    ) {
      throw new TaskLeaseLifecycleError(
        "task lease lifecycle receipt response is malformed",
        "corrupt_lifecycle_receipt",
        { receipt_path: path },
        "validation",
      );
    }
    return {
      schema_version: TASK_LEASE_LIFECYCLE_RECEIPT_SCHEMA,
      operation_id: record.operation_id,
      request_digest: record.request_digest,
      state: record.state,
      planned_lease: plannedLease,
      response,
    };
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
    if (error instanceof TaskLeaseLifecycleError) throw error;
    if (error instanceof SyntaxError) {
      throw new TaskLeaseLifecycleError(
        "task lease lifecycle receipt is not valid JSON",
        "corrupt_lifecycle_receipt",
        { receipt_path: path },
        "validation",
      );
    }
    if ((error as NodeJS.ErrnoException).code) throw error;
    throw new TaskLeaseLifecycleError(
      "task lease lifecycle receipt is malformed",
      "corrupt_lifecycle_receipt",
      { receipt_path: path },
      "validation",
    );
  }
}

function lifecycleSettlement(
  request: Pick<LifecycleRequest, "goal_id" | "todo_id" | "owner" | "idempotency_key">,
  status: "committed" | "replayed",
  failure?: LifecycleErrorInfo,
): JsonObject {
  const effectId = effectIdFor(request);
  const result: JsonObject = {
    effect_id: effectId,
    receipts: effectId
      ? [{ step: "validation", status, effect_id: effectId }]
      : [],
  };
  if (failure) {
    result.failure = {
      step: failure.stage,
      kind: failure.code === "invalid_owner" || failure.code === "invalid_idempotency_key"
        ? "invalid_identity"
        : failure.stage === "validation" ? "permission_denied" : "writeback_rejected",
      code: failure.code,
    };
  } else if (status === "committed") {
    if (effectId) {
      (result.receipts as JsonObject[]).push({
        step: "durable_writeback",
        status,
        effect_id: effectId,
      });
    }
  }
  return result;
}

function failureEnvelope(
  request: Partial<LifecycleRequest> | null,
  error: LifecycleErrorInfo,
): JsonObject {
  const effectId = request && request.owner && request.idempotency_key
    ? effectIdFor(request as LifecycleRequest)
    : null;
  return {
    ok: false,
    schema_version: "task_lease_v0",
    action: request?.operation ?? "unknown",
    error: error.message,
    error_code: error.code,
    ...(request?.runtime_root && request.goal_id && request.todo_id
      ? { lease_path: leasePathFor(request as LifecycleRequest) }
      : {}),
    ...error.payload,
    settlement: {
      effect_id: effectId,
      receipts: effectId && error.stage !== "validation"
        ? [{ step: "validation", status: "committed", effect_id: effectId }]
        : [],
      failure: {
        step: error.stage,
        kind: error.code === "invalid_owner" || error.code === "invalid_idempotency_key"
          ? "invalid_identity"
          : error.stage === "validation" ? "permission_denied" : "writeback_rejected",
        code: error.code,
      },
    },
  };
}

function failureRequestContext(value: unknown): Partial<LifecycleRequest> | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  const input = value as Record<string, unknown>;
  const operation = typeof input.operation === "string" &&
      TASK_LEASE_LIFECYCLE_OPERATIONS.includes(
        input.operation as TaskLeaseLifecycleOperation,
      )
    ? input.operation as TaskLeaseLifecycleOperation
    : undefined;
  return operation === undefined ? null : { operation };
}

function errorInfo(error: unknown, fallbackStage: LifecycleStage = "durable_writeback"): LifecycleErrorInfo {
  if (error instanceof ShadowManagementError || error instanceof LegacyCoordinationWriteError) {
    return {code: error.code, message: error.message, payload: error.payload, stage: "validation"};
  }
  if (error instanceof TaskLeaseLifecycleError) {
    return {
      code: error.code,
      message: error.message,
      payload: error.payload,
      stage: error.stage,
    };
  }
  if (error instanceof EffectRuntimeLockTimeoutError) {
    return {
      code: "lock_acquire_timeout",
      message: "task lease mutation lock timed out",
      payload: {},
      stage: "validation",
    };
  }
  if (error instanceof EffectRuntimeRequestError) {
    return {
      code: error.code,
      message: error.message,
      payload: {},
      stage: "validation",
    };
  }
  if (error instanceof EffectRuntimeConflictError) {
    return {
      code: error.code,
      message: error.message,
      payload: {},
      stage: "validation",
    };
  }
  if (error instanceof TaskLeaseAcquireError) {
    return {
      code: error.code,
      message: error.message,
      payload: error.payload,
      stage: "validation",
    };
  }
  const payload = effectRuntimeErrorPayload(error);
  return {
    code: payload.code,
    message: payload.message,
    payload: {},
    stage: fallbackStage,
  };
}



function transitionError(
  request: LifecycleRequest,
  code: string,
  lease: LeaseRecord | null,
  leasePath: string,
): TaskLeaseLifecycleError {
  let message: string;
  let payload: JsonObject = { lease, lease_path: leasePath };
  if (code === "version_required") {
    message = `task lease ${request.operation} requires the current lease version`;
    payload = { action: request.operation };
  } else if (code === "version_mismatch") {
    const actual = leaseVersion(lease);
    message = `lease version mismatch: expected ${request.expected_version}, got ${actual}`;
    payload = { expected_version: request.expected_version, actual_version: actual };
  } else if (code === "lease_not_active") {
    message = "lease is missing or expired";
    payload = {};
  } else if (code === "lease_cas_mismatch") {
    message = "lease owner or idempotency key mismatch";
    payload = {};
  } else if (code === "idempotency_key_reuse") {
    message = request.operation === "transfer"
      ? "lease transfer must mint a new execution idempotency key"
      : "idempotency key was reused with different lifecycle parameters";
  } else if (code === "handoff_mode_forbids_lease") {
    message = `goal handoff mode 'soft_claim' forbids task lease ${request.operation}; release and inspect remain available for legacy leftovers`;
    payload = {
      goal_id: request.goal_id,
      todo_id: request.todo_id,
      action: request.operation,
      handoff_mode: request.authority?.handoff_mode,
    };
  } else {
    message = `task lease ${request.operation} rejected by authority core: ${code}`;
  }
  return new TaskLeaseLifecycleError(message, code, payload, "validation");
}

function responseForOrdinary(
  request: LifecycleRequest,
  leasePath: string,
  lease: LeaseRecord | null,
  idempotent: boolean,
  actionValue: JsonObject,
): JsonObject {
  return {
    ok: true,
    schema_version: "task_lease_v0",
    action: request.operation,
    ...actionValue,
    ...(lease === null ? {} : { lease }),
    lease_path: leasePath,
    ...(request.authority?.handoff_mode ? { handoff_mode: request.authority.handoff_mode } : {}),
    ...(idempotent ? { idempotent: true } : { idempotent: false }),
    settlement: lifecycleSettlement(request, idempotent ? "replayed" : "committed"),
  };
}

function plannedLeaseMatches(actual: LeaseRecord | null, planned: LeaseRecord | null): boolean {
  if (actual === null || planned === null) return actual === planned;
  return JSON.stringify(stableValue(actual)) === JSON.stringify(stableValue(planned));
}

async function loadLeaseForRequest(
  request: LifecycleRequest,
  leasePath: string,
): Promise<LeaseRecord | null> {
  const lease = await readLease(leasePath);
  return lease === null ? null : validatePersistedLease(lease, request, leasePath);
}

async function persistOperationReceipt(
  request: LifecycleRequest,
  state: OperationReceipt["state"],
  plannedLease: LeaseRecord | null,
  response: JsonObject,
): Promise<void> {
  const path = operationReceiptPath(request);
  const operationId = operationIdentity(request);
  const requestDigest = operationRequestDigest(request);
  if (!path || !operationId || !requestDigest) return;
  await atomicWriteJson(path, {
    schema_version: TASK_LEASE_LIFECYCLE_RECEIPT_SCHEMA,
    operation_id: operationId,
    request_digest: requestDigest,
    state,
    planned_lease: plannedLease,
    response,
  });
}

function retainedReleaseDecision(
  request: LifecycleRequest,
  lease: LeaseRecord | null,
  at: Date,
): TaskLeaseLifecycleDecision {
  return decideTaskLeaseLifecycle({
    // Release preserves historical cleanup in every decoded mode. It neither
    // reads Todo admission nor authorizes a fresh execution lease.
    handoff_mode: request.authority?.handoff_mode ?? "legacy",
    registered_agents: [],
    todo: null,
    lease: lease === null
      ? null
      : {
          present: true,
          active: leaseIsActive(lease, at),
          status: typeof lease.status === "string" ? lease.status : null,
          owner: persistedOwner(lease),
          idempotency_key: persistedKey(lease),
          version: leaseVersion(lease),
          lease_epoch: leaseEpoch(lease),
          write_scopes: [],
          acquire_ttl_seconds: null,
        },
    command: {
      operation: "release",
      owner: request.owner!,
      idempotency_key: request.idempotency_key!,
      expected_version: request.expected_version,
      ttl_seconds: null,
      new_owner: null,
      new_idempotency_key: null,
    },
  });
}

function rejectFreshSourceLeaseOperation(request: LifecycleRequest): never {
  throw new TaskLeaseLifecycleError(
  "Canonical authority is required; back up and migrate this Goal before mutating a task lease",
  "canonical_authority_required",
  {
    goal_id: request.goal_id,
    operation: `task_lease_${request.operation}`,
    write_admitted: false,
    decision_read_from_provider: false,
    legacy_fallback_used: false,
    recovery: {
      action: "migrate_goal_authority",
      backup_required: true,
    },
  },
);
}

async function ordinaryOperation(
  request: LifecycleRequest,
  dependencies: LifecycleDependencies,
): Promise<JsonObject> {
  const leasePath = leasePathFor(request);
  const operationPath = operationReceiptPath(request);
  const requestDigest = operationRequestDigest(request);
  const at = lifecycleNow(request, dependencies);

  // Preserve the legacy facade's diagnostic ordering.  Handoff policy and
  // required CAS version are request-level gates, so they must win over lease
  // file/receipt errors (and over an optional replay read) just as they did in
  // the Python writer.
  if (request.operation !== "release") {
    if (!request.authority) {
      throw new TaskLeaseLifecycleError("authority is required", "authority_required");
    }
    if (request.authority.handoff_mode === "soft_claim") {
      throw transitionError(request, "handoff_mode_forbids_lease", null, leasePath);
    }
  }
  if (request.expected_version === null) {
    throw transitionError(request, "version_required", null, leasePath);
  }

  const existing = await loadLeaseForRequest(request, leasePath);
  const existingReceipt = await readOperationReceipt(
    operationPath,
    request,
    leasePath,
  );
  if (existingReceipt && requestDigest && existingReceipt.request_digest !== requestDigest) {
    throw new TaskLeaseLifecycleError(
      "idempotency key was reused with different lifecycle parameters",
      "idempotency_key_reuse",
      { lease_path: leasePath },
      "validation",
    );
  }

  // A committed receipt is authoritative for this operation identity.  A
  // divergent lease means an external writer or partial repair changed state;
  // retrying the mutation would risk a second generation.
  if (existingReceipt?.state === "committed" &&
      !plannedLeaseMatches(existing, existingReceipt.planned_lease)) {
    throw new TaskLeaseLifecycleError(
      "task-lease lifecycle receipt does not match the persisted lease",
      "lifecycle_receipt_state_mismatch",
      { lease_path: leasePath },
      "durable_writeback",
    );
  }

  if (existingReceipt?.state === "committed") {
    const replay: JsonObject = { ...existingReceipt.response, idempotent: true };
    replay.settlement = lifecycleSettlement(request, "replayed");
    return replay;
  }
  if (existingReceipt?.state === "prepared" && plannedLeaseMatches(existing, existingReceipt.planned_lease)) {
    const replay: JsonObject = { ...existingReceipt.response, idempotent: true };
    replay.settlement = lifecycleSettlement(request, "replayed");
    await persistOperationReceipt(request, "committed", existingReceipt.planned_lease, replay);
    return replay;
  }

  // Prepared-with-matching-bytes and committed receipts above are recovery.
  // A missing/ambiguous effect cannot be renewed or transferred after cutoff.
  if (request.operation !== "release") {
    rejectFreshSourceLeaseOperation(request);
  }

  await requireLegacyCoordinationPrimaryWriteAllowed(request.runtime_root, request.goal_id);
  const decision = retainedReleaseDecision(request, existing, at);
  if (decision.outcome === "conflict" || decision.outcome === "rejected") {
    throw transitionError(request, decision.code, existing, leasePath);
  }

  if (decision.code === "lease_missing") {
    return {
      ok: true,
      schema_version: "task_lease_v0",
      action: "release",
      released: false,
      missing: true,
      lease_path: leasePath,
      ...(request.authority?.handoff_mode
        ? { handoff_mode: request.authority.handoff_mode }
        : {}),
      settlement: lifecycleSettlement(request, "committed"),
    };
  }
  if (decision.code === "lease_release_replay" && existing !== null) {
    return responseForOrdinary(request, leasePath, existing, true, { released: true });
  }
  if (existing === null || decision.next_lease === null) {
    throw transitionError(request, "invalid_lease_snapshot", existing, leasePath);
  }
  const next = releasedLease(existing, at);
  const response = responseForOrdinary(request, leasePath, next, false, { released: true });
  await persistOperationReceipt(request, "prepared", next, response);
  await dependencies.beforeWrite?.(next);
  await atomicWriteJson(leasePath, next);
  await persistOperationReceipt(request, "committed", next, response);
  return response;
}


async function replayClosedFenceClose(
  request: LifecycleRequest,
  receipt: FenceReceipt,
  receiptPath: string,
): Promise<JsonObject> {
  if (!receipt.response || receipt.response.action !== "fence_close") {
    throw fenceReceiptError(
      receiptPath,
      "closed task lease fence receipt omitted its close response",
    );
  }
  if (!receipt.close_request_digest) {
    // A close receipt without the request digest cannot prove that a retry has
    // the same commit/release intent. Treat old or hand-edited receipts as
    // corrupt instead of accepting a parameter-changing replay.
    throw fenceReceiptError(
      receiptPath,
      "closed task lease fence receipt omitted its close request digest",
    );
  }
  const expectedDigest = fenceCloseRequestDigest(request);
  if (receipt.close_request_digest !== expectedDigest) {
    throw new TaskLeaseLifecycleError(
      "task lease fence close was retried with different parameters",
      "fence_operation_reuse",
      { receipt_path: receiptPath },
      "validation",
    );
  }

  // Repair a response-loss window before taking a fresh goal lock. If a
  // native closer still owns the token claim, the release helper fails closed
  // and the acquisition below waits for that critical section to finish.
  await releaseFileMutationLock(
    lockPathFor(request),
    receipt.lock_token,
    null,
    true,
  );
  const lock = await acquireFileMutationLock(
    lockPathFor(request),
    request.owner_pid ?? process.pid,
  );
  try {
    const currentReceipt = await readFenceReceipt(receiptPath, request);
    if (!currentReceipt || currentReceipt.state !== "closed" ||
        currentReceipt.close_request_digest !== expectedDigest) {
      throw new TaskLeaseLifecycleError(
        "closed task lease fence receipt changed during replay",
        "fence_state_invalid",
        { receipt_path: receiptPath },
        "validation",
      );
    }
    const currentLease = await loadLeaseForRequest(
      request,
      leasePathFor(request),
    );
    const response = currentReceipt.response;
    if (!response || response.action !== "fence_close" ||
        typeof response.released !== "boolean") {
      throw fenceReceiptError(
        receiptPath,
        "closed task lease fence receipt close response is malformed",
      );
    }
    const tupleMatches = currentLease !== null &&
      fenceLeaseMatchesReceipt(currentLease, currentReceipt);
    if (response.released === true) {
      if (
        currentLease === null ||
        currentLease.status !== "released" ||
        currentReceipt.lease === null ||
        !tupleMatches ||
        !plannedLeaseMatches(currentLease, currentReceipt.lease)
      ) {
        throw new TaskLeaseLifecycleError(
          "closed task lease fence receipt does not match the current retired lease generation",
          "fence_state_invalid",
          {
            goal_id: request.goal_id,
            todo_id: request.todo_id,
            lease_path: leasePathFor(request),
            expected_version: currentReceipt.fence_expected_version,
            expected_lease_epoch: currentReceipt.fence_expected_lease_epoch,
            actual_version: currentLease ? leaseVersion(currentLease) : null,
            actual_lease_epoch: currentLease ? leaseEpoch(currentLease) : null,
          },
          "validation",
        );
      }
    } else if (
      currentReceipt.lease === null
        ? currentLease !== null
        : !tupleMatches || !plannedLeaseMatches(currentLease, currentReceipt.lease)
    ) {
      throw new TaskLeaseLifecycleError(
        "closed task lease fence receipt does not match the current lease generation",
        "fence_state_invalid",
        { receipt_path: receiptPath },
        "validation",
      );
    }
    return { ...response, idempotent: true };
  } finally {
    await releaseFileMutationLock(lock.targetPath, lock.token, null, true);
  }
}

/**
 * Whether a failure is the source-snapshot mismatch the caller retries.
 *
 * `revalidateAuthoritySources` re-reads the canonical registry before a write
 * commits.  The adapter answers this exact code by re-reading the graph and
 * re-sending the same held fence, so it must stay distinguishable from the
 * conflicts that genuinely end a fence.
 */
function isRetryableAuthoritySourceMismatch(error: unknown): boolean {
  return error instanceof TaskLeaseAcquireError &&
    error.code === "authority_source_changed";
}

async function fenceClose(
  request: LifecycleRequest,
  dependencies: LifecycleDependencies,
): Promise<JsonObject> {
  if (!request.lock_token) throw new TaskLeaseLifecycleError("fence close requires lock_token", "invalid_lock_token");
  let operationId = fenceOperationId(request);
  let receiptPath = fenceReceiptPath(request, operationId);
  let receipt = await readFenceReceipt(receiptPath, request);
  // Older Python callers did not carry the private operation id.  Resolve a
  // unique receipt by token as a compatibility readback path; all new calls
  // use the exact path returned by fence verification.
  if (!receipt && request.fence_operation_id === null) {
    const match = await findFenceReceiptByToken(request);
    if (match) {
      operationId = match.receipt.operation_id;
      receiptPath = match.path;
      receipt = match.receipt;
    }
  }
  if (receipt && receipt.lock_token !== request.lock_token) {
    throw new EffectRuntimeConflictError(
      "task lease fence receipt does not match the supplied lock token",
      "fence_token_invalid",
    );
  }
  if (receipt?.state === "closed") {
    return await replayClosedFenceClose(request, receipt, receiptPath);
  }
  if (request.committed && request.release_lease && !isVerifiedHeldFence(receipt)) {
    // A token alone is not a lease authority. In particular, an `acquired`
    // receipt can exist after an auto-acquire write lost its response, but it
    // never proves that terminal validation completed. Require a durable held
    // receipt with a verified response before allowing a lease release.
    throw new TaskLeaseLifecycleError(
      "committed task lease fence close requires a verified held fence",
      "fence_state_invalid",
      { receipt_path: receiptPath },
      "validation",
    );
  }
  const lockPath = lockPathFor(request);
  let claim: FileMutationLockClaim | null = null;
  let released = false;
  let alreadyReleased = false;
  try {
    const owner = await mutationLockOwner(lockPath);
    if (!owner || owner.token !== request.lock_token) {
      // A response may have been lost after the close write and lock release.
      // The persisted lease plus the held receipt is enough to finish the
      // close receipt without touching a new lock owner.
      const leasePath = leasePathFor(request);
      const rawLease = await readLease(leasePath);
      const recoveredLease = rawLease === null
        ? null
        : validatePersistedLease(rawLease, request, leasePath);
      if (
        receipt &&
        recoveredLease &&
        receipt.state === "held" &&
        request.committed &&
        request.release_lease &&
        recoveredLease.status === "released" &&
        fenceLeaseMatchesReceipt(recoveredLease, receipt)
      ) {
        const response = {
          ok: true,
          schema_version: "task_lease_v0",
          action: "fence_close",
          released: true,
          idempotent: true,
        };
        await persistFenceReceipt(request, "closed", request.lock_token, {
          operationId,
          lease: recoveredLease,
          response,
          base: receipt,
          requestDigest: receipt.request_digest,
          ownerPid: receipt.owner_pid,
          fenceOwner: receipt.fence_owner,
          fenceIdempotencyKey: receipt.fence_idempotency_key,
          fenceExpectedVersion: receipt.fence_expected_version,
          fenceExpectedLeaseEpoch: receipt.fence_expected_lease_epoch,
          closeRequestDigest: fenceCloseRequestDigest(request),
        });
        return response;
      }
      throw new EffectRuntimeConflictError("task lease fence token is no longer held", "fence_token_invalid");
    }
    claim = await claimFileMutationLock(lockPath, request.lock_token);
    if (!claim) {
      throw new EffectRuntimeConflictError(
        "task lease fence token is already being closed or is no longer held",
        "fence_token_invalid",
      );
    }
    const claimedOwner = await mutationLockOwner(lockPath);
    if (!claimedOwner || claimedOwner.token !== request.lock_token) {
      throw new EffectRuntimeConflictError("task lease fence token is no longer held", "fence_token_invalid");
    }
    if (request.committed && request.release_lease) {
      await requireLegacyCoordinationPrimaryWriteAllowed(request.runtime_root, request.goal_id);
      const at = lifecycleNow(request, dependencies);
      const leasePath = leasePathFor(request);
      const rawLease = await readLease(leasePath);
      if (!rawLease) {
        throw new TaskLeaseLifecycleError("task lease fence was active but its lease record is gone", "fence_state_invalid", { lease_path: leasePath }, "durable_writeback");
      }
      const lease = validatePersistedLease(rawLease, request, leasePath);
      const expectedOwner = request.fence_owner ?? receipt?.fence_owner;
      const expectedKey = request.fence_idempotency_key ?? receipt?.fence_idempotency_key;
      const expectedVersion = request.fence_expected_version ?? receipt?.fence_expected_version;
      const expectedEpoch = request.fence_expected_lease_epoch ?? receipt?.fence_expected_lease_epoch;
      const holderFence = receipt?.verify_response?.checked === true ||
        receipt?.response?.checked === true;
      // Holder verification proves ownership with the lease tuple and does
      // not require the caller to know the lease's execution key.  Terminal
      // verification still requires that key to prevent an actor-only close.
      if (
        expectedOwner === null ||
        expectedVersion === null ||
        expectedEpoch === null ||
        (!holderFence && expectedKey === null)
      ) {
        throw new TaskLeaseLifecycleError(
          holderFence
            ? "committed holder fence close requires its verified owner, version, and lease epoch"
            : "committed task lease fence close requires its verified owner, key, version, and lease epoch",
          "fence_cas_mismatch",
          { lease_path: leasePath },
          "durable_writeback",
        );
      }
      if (
        receipt &&
        ((receipt.fence_owner !== null && receipt.fence_owner !== expectedOwner) ||
          (receipt.fence_idempotency_key !== null && expectedKey !== null &&
            receipt.fence_idempotency_key !== expectedKey) ||
          (receipt.fence_expected_version !== null &&
            receipt.fence_expected_version !== expectedVersion) ||
          (receipt.fence_expected_lease_epoch !== null &&
            receipt.fence_expected_lease_epoch !== expectedEpoch))
      ) {
        throw new TaskLeaseLifecycleError(
          "task lease fence close does not match its verified lease tuple",
          "fence_cas_mismatch",
          { lease_path: leasePath },
          "durable_writeback",
        );
      }
      if (
        persistedOwner(lease) !== expectedOwner ||
        (expectedKey !== null && persistedKey(lease) !== expectedKey) ||
        leaseVersion(lease) !== expectedVersion ||
        leaseEpoch(lease) !== expectedEpoch
      ) {
        throw new TaskLeaseLifecycleError("task lease fence changed before close", "fence_cas_mismatch", { lease_path: leasePath, expected_lease_epoch: expectedEpoch, actual_lease_epoch: leaseEpoch(lease) }, "durable_writeback");
      }
      if (lease.status === "released") {
        // The lease write may have committed before the process lost its
        // response or fence receipt.  The verified tuple makes this a safe
        // exactly-once replay rather than a state error.
        released = true;
        alreadyReleased = true;
      } else if (lease.status !== "active") {
        throw new TaskLeaseLifecycleError("task lease fence no longer references an active lease record", "fence_state_invalid", { lease_path: leasePath, status: lease.status ?? null }, "durable_writeback");
      }
      const ownerBeforeWrite = await mutationLockOwner(lockPath);
      if (!ownerBeforeWrite || ownerBeforeWrite.token !== request.lock_token) {
        throw new EffectRuntimeConflictError("task lease fence token is no longer held", "fence_token_invalid");
      }
      if (!alreadyReleased) {
        const next = releasedLease(lease, at);
        await dependencies.beforeWrite?.(next);
        await atomicWriteJson(leasePath, next);
        released = true;
      }
    }
    const response = {
      ok: true,
      schema_version: "task_lease_v0",
      action: "fence_close",
      released,
      ...(alreadyReleased ? { idempotent: true } : {}),
    };
    await persistFenceReceipt(request, "closed", request.lock_token, {
      operationId,
      lease: released
        ? await loadLeaseForRequest(request, leasePathFor(request))
        : receipt?.lease ?? null,
      response,
      base: receipt,
      requestDigest: receipt?.request_digest,
      ownerPid: receipt?.owner_pid ?? request.owner_pid ?? process.pid,
      fenceOwner: request.fence_owner ?? receipt?.fence_owner,
      fenceIdempotencyKey: request.fence_idempotency_key ?? receipt?.fence_idempotency_key,
      fenceExpectedVersion: request.fence_expected_version ?? receipt?.fence_expected_version,
      fenceExpectedLeaseEpoch: request.fence_expected_lease_epoch ?? receipt?.fence_expected_lease_epoch,
      closeRequestDigest: fenceCloseRequestDigest(request),
    });
    return response;
  } catch (error) {
    // A changed authority source is a retryable precondition, not a lost
    // fence: the lease write never ran and the caller still holds this token.
    // Releasing the lock here would answer the caller's retry with
    // `fence_token_invalid` and strand the completed Todo's active lease, so
    // drop only this attempt's claim and leave the fence held.
    if (claim && isRetryableAuthoritySourceMismatch(error)) {
      try {
        await releaseFileMutationLockClaim(claim);
      } catch {
        // Claim cleanup is best effort; never replace the original error.
      }
      claim = null;
    }
    throw error;
  } finally {
    if (claim) {
      await releaseFileMutationLock(
        lockPath,
        request.lock_token,
        claim,
        // Once the lease/receipt write has committed, cleanup is best effort;
        // even on an earlier failure it must not mask the original result.
        true,
      );
    }
  }
}

function canonicalLifecycleEnvelope(request: CanonicalLifecycleRequest, result: JsonObject): JsonObject {
  const evidence = Object.fromEntries(["source_authority", "decision_read_from_provider", "legacy_fallback_used",
    "provider_revision", "cursor", "current_provider_revision", "current_cursor", "handoff_mode",
    "operation_id", "recovery", "commit_status", "receipt_status", "expected_version", "actual_version", "todo_status", "claimed_by", "excluded_agents"].filter(key => result[key] !== undefined)
    .map(key => [key, result[key]]));
  if (result.status === "applied" || result.status === "no_change" || result.status === "replayed" || result.status === "recovered") {
    const replayed = result.status === "replayed" || result.status === "recovered";
    const idempotent = replayed || result.status === "no_change";
    return {ok: true, schema_version: "task_lease_v0", action: request.operation, status: result.status,
      ...Object.fromEntries(["renewed", "transferred", "released", "missing", "lease"].filter(key => result[key] !== undefined).map(key => [key, result[key]])),
      idempotent, original_receipt: result.original_receipt,
      ...(request.transfer_claim ? {transfer_claim: true, todo_id: result.todo_id,
        todo_changed: result.todo_changed, projection_delivery: result.projection_delivery,
        projection_source: result.projection_source} : {}),
      ...evidence, settlement: lifecycleSettlement(request, replayed ? "replayed" : "committed")};
  }
  const envelope = failureEnvelope(request, {code: String(result.reason_code ?? result.conflict_kind ?? "canonical_lease_failed"),
    message: String(result.reason ?? "canonical lease mutation could not complete; inspect the result before retrying"),
    payload: {...evidence, status: result.status}, stage: result.failure_stage === "durable_writeback" ? "durable_writeback" : "validation"});
  delete envelope.lease_path;
  return envelope;
}

export async function executeTaskLeaseLifecycle(
  value: unknown,
  dependencies: LifecycleDependencies = {},
): Promise<JsonObject> {
  let context: Partial<LifecycleRequest> | null = null;
  try {
    const decoded = decodeTaskLeaseLifecycleRequest(value);
    context = decoded.request;
    if (decoded.kind === "canonical") {
      const request = decoded.request;
      const result = await mutateCanonicalTaskLease(request, {
        now: () => lifecycleNow(request, dependencies), beforeWrite: dependencies.beforeWrite,
        authorityProvider: dependencies.authorityProvider,
      });
      return canonicalLifecycleEnvelope(request, result);
    }
    const request = decoded.request;
    if (request.operation === "fence_close") return await fenceClose(request, dependencies);
    if (request.operation === "terminal_verify" || request.operation === "holder_verify") {
      throw new TaskLeaseLifecycleError(
        "Separate source-backed lease verification is retired; use the canonical Todo transaction",
        "task_lease_verification_retired",
        {
          action: request.operation,
          write_admitted: false,
          legacy_fallback_used: false,
        },
      );
    }
    // Presence only selects the recovery path. The existing owner re-reads the
    // receipt and current lease under both locks; it never mints a fresh grant.
    if (request.operation !== "release" && await readOperationReceipt(
      operationReceiptPath(request), request, leasePathFor(request),
    ) === null) rejectFreshSourceLeaseOperation(request);
    return await withFileMutationLock(
      legacyCoordinationLeaseLockPath(request.runtime_root, request.goal_id),
      () => withFileMutationLock(lockPathFor(request!), async () => {
        return await ordinaryOperation(request!, dependencies);
      }),
    );
  } catch (error) {
    const info = errorInfo(error);
    return failureEnvelope(context ?? failureRequestContext(value), info);
  }
}
