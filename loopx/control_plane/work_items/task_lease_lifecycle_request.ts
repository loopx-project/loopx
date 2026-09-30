/** Decode the shipped lease protocols before choosing a persistence path.
 * The discriminant keeps canonical commands separate from legacy held fences. */
import {createHash, randomUUID} from "node:crypto";
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {decodeTaskLeaseAuthority, normalizeAgent, normalizeGoalId, normalizeTodoId,
  normalizeOwner, normalizeIdempotencyKey, normalizeTtl,
  type AuthorityFacts, type TodoFact, type TodoFactField} from "./task_lease_acquire.ts";
import {decodeLocalAuthorityShadowBinding, type LocalAuthorityShadowBinding} from "../coordination/local_authority_shadow_outbox.ts";
import type {LocalLeaseRequest} from "./canonical_task_lease_lifecycle.ts";
import {TASK_LEASE_LIFECYCLE_REQUEST_SCHEMA, TASK_LEASE_CANONICAL_RENEW_REQUEST_SCHEMA,
  TASK_LEASE_CANONICAL_LIFECYCLE_REQUEST_SCHEMA, TASK_LEASE_CANONICAL_CLAIM_TRANSFER_REQUEST_SCHEMA}
  from "../coordination/coordination_state_contract.generated.ts";
import { BARE_SHA256_PATTERN } from "../content_digest.ts";

export const TASK_LEASE_LIFECYCLE_REQUEST_SCHEMA_VERSION =
  TASK_LEASE_LIFECYCLE_REQUEST_SCHEMA;
export const TASK_LEASE_LIFECYCLE_OPERATIONS = [
  "renew",
  "transfer",
  "release",
  "terminal_verify",
  "holder_verify",
  "fence_close",
] as const;
export type TaskLeaseLifecycleOperation =
  (typeof TASK_LEASE_LIFECYCLE_OPERATIONS)[number];

export type LifecycleStage = "validation" | "durable_writeback";

export interface CanonicalLifecycleRequest extends LocalLeaseRequest {
  current_time: Date | null;
}
export type DecodedLifecycleRequest =
  | {kind: "canonical"; request: CanonicalLifecycleRequest}
  | {kind: "legacy"; request: LifecycleRequest};

export interface LifecycleRequest {
  schema_version: typeof TASK_LEASE_LIFECYCLE_REQUEST_SCHEMA_VERSION;
  operation: TaskLeaseLifecycleOperation;
  runtime_root: string;
  goal_id: string;
  todo_id: string;
  owner: string | null;
  idempotency_key: string | null;
  expected_version: number | null;
  ttl_seconds: number | null;
  new_owner: string | null;
  new_idempotency_key: string | null;
  authority: AuthorityFacts | null;
  todo: TodoFact | null;
  delegated_authority: boolean;
  allow_user_gate_auto_acquire: boolean;
  require_active_when_fence_supplied: boolean;
  lock_token: string | null;
  committed: boolean;
  release_lease: boolean;
  fence_owner: string | null;
  fence_idempotency_key: string | null;
  fence_expected_version: number | null;
  fence_expected_lease_epoch: number | null;
  fence_operation_id: string | null;
  current_time: Date | null;
  owner_pid: number | null;
  runtime_shadow: LocalAuthorityShadowBinding | null;
}


export interface LifecycleErrorInfo {
  code: string;
  message: string;
  payload: JsonObject;
  stage: LifecycleStage;
}

export class TaskLeaseLifecycleError extends Error {
  readonly code: string;
  readonly payload: JsonObject;
  readonly stage: LifecycleStage;

  constructor(
    message: string,
    code: string,
    payload: JsonObject = {},
    stage: LifecycleStage = "validation",
  ) {
    super(message);
    this.name = "TaskLeaseLifecycleError";
    this.code = code;
    this.payload = payload;
    this.stage = stage;
  }
}

function compact(value: unknown): string {
  if (value === null || value === undefined) return "";
  const raw = typeof value === "string"
    ? value
    : typeof value === "number" || typeof value === "boolean"
      ? String(value)
      : "";
  return raw.trim().split(/\s+/u).filter(Boolean).join(" ");
}

function optionalInteger(value: unknown, label: string): number | null {
  if (value === null || value === undefined) return null;
  if (
    typeof value !== "number" ||
    !Number.isSafeInteger(value) ||
    value < 0
  ) {
    throw new TaskLeaseLifecycleError(
      `${label} must be a non-negative safe integer or null`,
      "invalid_request",
    );
  }
  return value;
}

function optionalExpectedVersion(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "number" || !Number.isSafeInteger(value)) {
    throw new TaskLeaseLifecycleError(
      "expected_version must be a safe integer or null",
      "invalid_request",
    );
  }
  return value;
}

function optionalBoolean(
  value: unknown,
  label: string,
  defaultValue: boolean,
): boolean {
  if (value === undefined) return defaultValue;
  if (typeof value !== "boolean") {
    throw new TaskLeaseLifecycleError(
      `${label} must be a boolean when provided`,
      "invalid_request",
    );
  }
  return value;
}

function optionalPositiveInteger(value: unknown, label: string): number | null {
  if (value === undefined || value === null) return null;
  if (
    typeof value !== "number" ||
    !Number.isSafeInteger(value) ||
    value <= 0
  ) {
    throw new TaskLeaseLifecycleError(
      `${label} must be a positive safe integer or null`,
      "invalid_request",
    );
  }
  return value;
}

function optionalDate(value: unknown, label: string): Date | null {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value !== "string") {
    throw new TaskLeaseLifecycleError(
      `${label} must be an ISO-8601 string or null`,
      "invalid_clock",
    );
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) {
    throw new TaskLeaseLifecycleError(
      `${label} must be a valid ISO-8601 timestamp`,
      "invalid_clock",
    );
  }
  return parsed;
}

function optionalOwner(value: unknown, label: string): string | null {
  if (value === null || value === undefined || value === "") return null;
  try {
    return normalizeOwner(value);
  } catch (error) {
    const message = error instanceof Error ? error.message : `${label} is invalid`;
    throw new TaskLeaseLifecycleError(message, "invalid_owner");
  }
}

function optionalKey(value: unknown, label: string): string | null {
  if (value === null || value === undefined || value === "") return null;
  try {
    return normalizeIdempotencyKey(value);
  } catch (error) {
    const message = error instanceof Error ? error.message : `${label} is invalid`;
    throw new TaskLeaseLifecycleError(message, "invalid_idempotency_key");
  }
}

function optionalFenceOperationId(value: unknown): string | null {
  if (value === null || value === undefined || value === "") return null;
  if (
    typeof value !== "string" ||
    !BARE_SHA256_PATTERN.test(value.trim())
  ) {
    throw new TaskLeaseLifecycleError(
      "fence_operation_id must be a 64-character lowercase hexadecimal token",
      "invalid_fence_operation_id",
    );
  }
  return value.trim();
}

function requiredOwner(value: unknown): string {
  const owner = optionalOwner(value, "owner");
  if (owner === null) {
    throw new TaskLeaseLifecycleError(
      "owner must be a public-safe agent id",
      "invalid_owner",
    );
  }
  return owner;
}

function requiredKey(value: unknown): string {
  const key = optionalKey(value, "idempotency_key");
  if (key === null) {
    throw new TaskLeaseLifecycleError(
      "idempotency key must be a public-safe token",
      "invalid_idempotency_key",
    );
  }
  return key;
}

function decodeTodo(
  value: unknown,
  fallback: TodoFact | null,
  expectedTodoId: string,
): TodoFact | null {
  if (value === null || value === undefined) return fallback;
  const record = requireJsonObject(value, "todo");
  let todoId: string;
  try {
    todoId = normalizeTodoId(record.todo_id);
  } catch (error) {
    throw new TaskLeaseLifecycleError(
      error instanceof Error ? error.message : "todo id is invalid",
      "invalid_todo_id",
    );
  }
  if (todoId !== expectedTodoId) {
    // A caller-supplied snapshot is only an elaboration of the authority
    // projection for this lease; it cannot silently authorize a sibling todo.
    throw new TaskLeaseLifecycleError(
      "todo does not match the task-lease identity",
      "todo_identity_mismatch",
      { expected_todo_id: expectedTodoId, actual_todo_id: todoId },
    );
  }
  const excludedRaw = record.excluded_agents;
  const excluded = Array.isArray(excludedRaw)
    ? excludedRaw
    : typeof excludedRaw === "string" ? excludedRaw.split(",") : [];
  // The legacy Python callers pass the active-state row, which can be a
  // partial compatibility view (for example it has no derived
  // ``task_class``). Preserve which fields were actually present so the
  // canonical projection can fill omitted metadata without turning an
  // otherwise valid request into a false authority mismatch. Explicitly
  // supplied fields remain strict below.
  const providedFields = [
    "status",
    "claimed_by",
    "excluded_agents",
    "role",
    "task_class",
    "bound_agent",
    "blocks_agent",
  ] as TodoFactField[];
  const presentFields = providedFields.filter((field) =>
    Object.hasOwn(record, field)
  );
  return {
    todo_id: todoId,
    status: compact(record.status).toLowerCase(),
    claimed_by: normalizeAgent(record.claimed_by),
    excluded_agents: [...new Set(
      excluded.map(normalizeAgent).filter((item): item is string => item !== null),
    )].sort((left, right) => left.localeCompare(right)),
    role: typeof record.role === "string" ? compact(record.role).toLowerCase() : undefined,
    task_class: typeof record.task_class === "string"
      ? compact(record.task_class).toLowerCase()
      : null,
    bound_agent: normalizeAgent(record.bound_agent),
    blocks_agent: normalizeAgent(record.blocks_agent),
    provided_fields: presentFields,
  };
}

function decodeOperation(value: unknown): TaskLeaseLifecycleOperation {
  if (
    typeof value !== "string" ||
    !TASK_LEASE_LIFECYCLE_OPERATIONS.includes(
      value as TaskLeaseLifecycleOperation,
    )
  ) {
    throw new TaskLeaseLifecycleError(
      "task-lease lifecycle operation is unsupported",
      "invalid_operation",
    );
  }
  return value as TaskLeaseLifecycleOperation;
}

export function decodeTaskLeaseLifecycleRequest(value: unknown): DecodedLifecycleRequest {
  const input = requireJsonObject(value, "task lease lifecycle request");
  const canonicalRenew = input.schema_version === TASK_LEASE_CANONICAL_RENEW_REQUEST_SCHEMA;
  const claimTransfer = input.schema_version === TASK_LEASE_CANONICAL_CLAIM_TRANSFER_REQUEST_SCHEMA;
  const canonical = claimTransfer || canonicalRenew || input.schema_version === TASK_LEASE_CANONICAL_LIFECYCLE_REQUEST_SCHEMA;
  if (input.schema_version !== TASK_LEASE_LIFECYCLE_REQUEST_SCHEMA_VERSION && !canonical) {
    throw new TaskLeaseLifecycleError(
      "Task-lease lifecycle request schema mismatch",
      "schema_mismatch",
    );
  }
  const operation = decodeOperation(input.operation);
  if (claimTransfer ? operation !== "transfer" || input.transfer_claim !== true : Object.hasOwn(input, "transfer_claim")) {
    throw new TaskLeaseLifecycleError("claim transfer requires its canonical schema, operation=transfer and transfer_claim=true", "invalid_canonical_claim_transfer_request");
  }
  if (canonicalRenew && operation !== "renew") throw new TaskLeaseLifecycleError("canonical renewal schema only accepts renew", "invalid_operation");
  if (canonical && !["renew", "transfer", "release"].includes(operation)) {
    throw new TaskLeaseLifecycleError("canonical schema only accepts lease mutations", "invalid_operation");
  }
  if (canonical) {
    const fields = new Set(["schema_version", "operation", "runtime_root", "goal_id", "todo_id", "owner",
      "idempotency_key", "expected_version", "ttl_seconds", "authority", "current_time"]);
    if (!canonicalRenew && operation === "transfer") {fields.add("new_owner"); fields.add("new_idempotency_key");}
    if (claimTransfer) fields.add("transfer_claim");
    const unsupported = Object.keys(input).find(key => !fields.has(key));
    if (unsupported) throw new TaskLeaseLifecycleError(`canonical lease mutation does not accept ${unsupported}`, canonicalRenew ? "invalid_canonical_renew_request" : "invalid_canonical_lifecycle_request");
  }
  let goalId: string;
  let todoId: string;
  let invalidIdentity: "invalid_goal_id" | "invalid_todo_id" = "invalid_goal_id";
  try {
    goalId = normalizeGoalId(input.goal_id);
    invalidIdentity = "invalid_todo_id";
    todoId = normalizeTodoId(input.todo_id);
  } catch (error) {
    const message = error instanceof Error ? error.message : "lease identity is invalid";
    throw new TaskLeaseLifecycleError(message, invalidIdentity);
  }

  if (canonical && operation === "release" && input.ttl_seconds != null) {
    throw new TaskLeaseLifecycleError("release cannot set a lease TTL", "invalid_canonical_lifecycle_request");
  }
  const ordinary = operation === "renew" || operation === "transfer" || operation === "release";
  const needsOwner = operation === "renew" || operation === "transfer" || operation === "release" || operation === "holder_verify";
  const owner = needsOwner ? requiredOwner(input.owner) : optionalOwner(input.owner, "owner");
  const idempotencyKey = ordinary ? requiredKey(input.idempotency_key) : optionalKey(input.idempotency_key, "idempotency_key");
  const expectedVersion = optionalExpectedVersion(input.expected_version);
  const ttlSeconds = operation === "renew" || operation === "transfer"
    ? normalizeTtl(input.ttl_seconds)
    : null;
  const newOwner = operation === "transfer" ? requiredOwner(input.new_owner) : optionalOwner(input.new_owner, "new_owner");
  const newKey = operation === "transfer" ? requiredKey(input.new_idempotency_key) : optionalKey(input.new_idempotency_key, "new_idempotency_key");

  let authority: AuthorityFacts | null = null;
  if (input.authority !== undefined && input.authority !== null) {
    // A canonical-only request cannot fall back. Its real mode comes from the
    // provider; stale display frontmatter is not a lease decision input.
    authority = decodeTaskLeaseAuthority(canonical
      ? {...requireJsonObject(input.authority, "authority"), handoff_mode: "legacy"}
      : input.authority);
  }
  if (
    (operation === "renew" || operation === "transfer" || operation === "terminal_verify" || operation === "holder_verify") &&
    authority === null
  ) {
    throw new TaskLeaseLifecycleError(
      "authority is required for this task-lease lifecycle operation",
      "authority_required",
    );
  }
  const common = {
    operation, runtime_root: typeof input.runtime_root === "string" ? input.runtime_root : (() => {
      throw new TaskLeaseLifecycleError("runtime_root must be a string", "invalid_runtime_root");
    })(), goal_id: goalId, todo_id: todoId, owner, idempotency_key: idempotencyKey,
    expected_version: expectedVersion, ttl_seconds: ttlSeconds,
    new_owner: newOwner, new_idempotency_key: newKey, authority,
    current_time: optionalDate(input.current_time, "current_time"),
  };
  if (canonical) {
    if (operation !== "renew" && operation !== "transfer" && operation !== "release") {
      throw new TaskLeaseLifecycleError("canonical schema only accepts lease mutations", "invalid_operation");
    }
    return {kind: "canonical", request: {...common, operation,
      ...(claimTransfer ? {transfer_claim: true} : {})}};
  }
  const fallbackTodo = authority?.todos.get(todoId) ?? null;
  const todo = decodeTodo(input.todo, fallbackTodo, todoId);
  if (
    (operation === "terminal_verify" || operation === "holder_verify") &&
    todo === null
  ) {
    throw new TaskLeaseLifecycleError(
      "todo is required for this task-lease fence operation",
      "todo_not_found",
    );
  }
  const fenceOwner = optionalOwner(input.fence_owner, "fence_owner");
  const fenceKey = optionalKey(input.fence_idempotency_key, "fence_idempotency_key");
  const fenceExpectedVersion = optionalInteger(
    input.fence_expected_version,
    "fence_expected_version",
  );
  const fenceExpectedLeaseEpoch = optionalInteger(
    input.fence_expected_lease_epoch,
    "fence_expected_lease_epoch",
  );
  const requestedFenceOperationId = optionalFenceOperationId(input.fence_operation_id);
  // Holder gates are lock-scoped proofs rather than caller-idempotent
  // mutations. A fresh native id prevents a later ownership update in the
  // same lease generation from colliding with an already closed gate receipt.
  const fenceOperationId = requestedFenceOperationId ?? (
    operation === "holder_verify"
      ? createHash("sha256").update(randomUUID(), "utf8").digest("hex")
      : null
  );
  const lockToken = input.lock_token === null || input.lock_token === undefined
    ? null
    : typeof input.lock_token === "string" &&
        input.lock_token.trim().length > 0 &&
        input.lock_token.length <= 256
      ? input.lock_token.trim()
      : (() => {
          throw new TaskLeaseLifecycleError(
            "lock_token must be a non-empty string of at most 256 characters",
            "invalid_lock_token",
          );
        })();
  return {kind: "legacy", request: {
    ...common, schema_version: TASK_LEASE_LIFECYCLE_REQUEST_SCHEMA_VERSION,
    todo,
    delegated_authority: optionalBoolean(input.delegated_authority, "delegated_authority", false),
    allow_user_gate_auto_acquire: optionalBoolean(input.allow_user_gate_auto_acquire, "allow_user_gate_auto_acquire", false),
    require_active_when_fence_supplied: optionalBoolean(input.require_active_when_fence_supplied, "require_active_when_fence_supplied", true),
    lock_token: lockToken,
    committed: optionalBoolean(input.committed, "committed", false),
    release_lease: optionalBoolean(input.release_lease, "release_lease", false),
    fence_owner: fenceOwner,
    fence_idempotency_key: fenceKey,
    fence_expected_version: fenceExpectedVersion,
    fence_expected_lease_epoch: fenceExpectedLeaseEpoch,
    fence_operation_id: fenceOperationId,
    owner_pid: optionalPositiveInteger(input.owner_pid, "owner_pid"),
    runtime_shadow: decodeLocalAuthorityShadowBinding(input.runtime_shadow),
  }};
}
