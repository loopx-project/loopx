/** Candidate wire contract. Clients submit intentions, never authority facts. */
import type {JsonObject} from "../effect_program.ts";
import {canonicalAuthorityObject, canonicalAuthoritySha256, hasExactAuthorityKeys,
  requireAuthorityStoreId} from "./authority_store_codec.ts";
import {POSTGRESQL_STORE_IDENTITY_PATTERN} from "./postgresql_authority_store.ts";
import {taskLeaseOperationIdentity} from "../work_items/task_lease_operation_identity.ts";
import {normalizeTodoId, normalizeGoalId, normalizeOwner} from "../work_items/task_lease_acquire.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {parseIsoTimestamp} from "../runtime_timestamp.ts";

export const REMOTE_AUTHORITY_REQUEST = "loopx_remote_authority_request_v0";
export const REMOTE_AUTHORITY_RESPONSE = "loopx_remote_authority_response_v0";
export const REMOTE_AUTHORITY_OPERATIONS = [
  "read", "claim", "progress", "renew", "release",
  "complete", "block", "defer", "todo_add", "snapshot",
] as const;
export type RemoteAuthorityOperation = typeof REMOTE_AUTHORITY_OPERATIONS[number];
export interface RemoteAuthorityScope {
  tenant_id: string;
  goal_id: string;
  actor_id: string;
  store_identity: string;
}
interface Base extends RemoteAuthorityScope {
  schema_version: typeof REMOTE_AUTHORITY_REQUEST;
  todo_id?: string;
}
export type RemoteAuthorityRequest = Base & (
  | {operation: "read"; todo_id: string}
  | {operation: "claim"; todo_id: string; request_id: string}
  | {operation: "progress"; todo_id: string; request_id: string; claim_request_id: string; note: string}
  | {operation: "renew"; todo_id: string; idempotency_key: string; expected_version: number; ttl_seconds: number}
  | {operation: "release"; todo_id: string; idempotency_key: string; expected_version: number}
  | {operation: "complete"; todo_id: string; request_id: string; claim_request_id: string; evidence: string; note?: string}
  | {operation: "block"; todo_id: string; request_id: string; claim_request_id?: string; reason: string; resume_when?: string; note?: string}
  | {operation: "defer"; todo_id: string; request_id: string; claim_request_id?: string; reason: string; resume_when: string; note?: string}
  | {
      operation: "todo_add";
      todo_id: string;
      request_id: string;
      text: string;
      role?: "user" | "agent";
      priority?: string;
      task_class?: string;
      action_kind?: string;
      task_repository?: string;
      required_write_scopes?: readonly string[];
      claimed_by?: string;
      note?: string;
    }
  | {operation: "snapshot"; todo_id?: string}
);
export type RemoteAuthorityMode = "execute" | "recover";
export function remoteAuthorityId(value: unknown, name: string): string {
  const id = requireAuthorityStoreId(value, name);
  if (id.length > 200 || /[\x00-\x20\x7f]/u.test(id)) throw new TypeError(`Invalid ${name}`);
  return id;
}

const MANDATORY_KEYS: Record<RemoteAuthorityOperation, readonly string[]> = {
  read: ["todo_id"],
  claim: ["todo_id", "request_id"],
  progress: ["todo_id", "request_id", "claim_request_id", "note"],
  renew: ["todo_id", "ttl_seconds", "idempotency_key", "expected_version"],
  release: ["todo_id", "idempotency_key", "expected_version"],
  complete: ["todo_id", "request_id", "claim_request_id", "evidence"],
  block: ["todo_id", "request_id", "reason"],
  defer: ["todo_id", "request_id", "reason", "resume_when"],
  todo_add: ["todo_id", "request_id", "text"],
  snapshot: [],
};
const OPTIONAL_KEYS: Record<RemoteAuthorityOperation, readonly string[]> = {
  read: [],
  claim: [],
  progress: [],
  renew: [],
  release: [],
  complete: ["note"],
  block: ["claim_request_id", "resume_when", "note"],
  defer: ["claim_request_id", "note"],
  todo_add: ["role", "priority", "task_class", "action_kind", "task_repository", "required_write_scopes", "claimed_by", "note"],
  snapshot: ["todo_id"],
};

export function decodeRemoteAuthorityRequest(raw: unknown): RemoteAuthorityRequest {
  const value = canonicalAuthorityObject(raw, "remote authority request");
  if (value.schema_version !== REMOTE_AUTHORITY_REQUEST ||
      !REMOTE_AUTHORITY_OPERATIONS.includes(value.operation as RemoteAuthorityOperation)) {
    throw new TypeError("Invalid request schema or operation");
  }
  const op = value.operation as RemoteAuthorityOperation;
  const baseKeys = ["schema_version", "tenant_id", "goal_id", "actor_id", "store_identity", "operation"];
  const required = [...baseKeys, ...MANDATORY_KEYS[op]];
  for (const key of required) {
    if (!Object.hasOwn(value, key)) throw new TypeError(`Unexpected or missing request fields: missing ${key}`);
  }
  const allowed = new Set([...required, ...OPTIONAL_KEYS[op]]);
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) throw new TypeError(`Unexpected or missing request fields: unexpected ${key}`);
  }
  for (const key of ["tenant_id", "goal_id", "actor_id", "todo_id", "request_id", "claim_request_id", "idempotency_key", "claimed_by"]) {
    if (key in value && typeof value[key] === "string") remoteAuthorityId(value[key], key);
  }
  if (typeof value.todo_id === "string" && normalizeTodoId(value.todo_id) !== value.todo_id) {
    throw new TypeError("Use canonical task, Goal and actor identities");
  }
  if (normalizeGoalId(value.goal_id) !== value.goal_id || normalizeOwner(value.actor_id) !== value.actor_id) {
    throw new TypeError("Use canonical task, Goal and actor identities");
  }
  if (typeof value.store_identity !== "string" || !POSTGRESQL_STORE_IDENTITY_PATTERN.test(value.store_identity)) {
    throw new TypeError("Invalid store identity");
  }
  if ("expected_version" in value && (!Number.isSafeInteger(value.expected_version) || Number(value.expected_version) < 0)) {
    throw new TypeError("Invalid lease version");
  }
  if ("ttl_seconds" in value && (!Number.isSafeInteger(value.ttl_seconds) || Number(value.ttl_seconds) < 1 || Number(value.ttl_seconds) > 3600)) {
    throw new TypeError("Invalid lease duration");
  }
  if (value.operation === "progress" && (typeof value.note !== "string" || !value.note.trim() || Buffer.byteLength(value.note) > 4096)) {
    throw new TypeError("Progress requires a bounded note");
  }
  if ("note" in value && value.note !== undefined && (typeof value.note !== "string" || !value.note.trim() || Buffer.byteLength(value.note) > 4096)) {
    throw new TypeError("Note requires a bounded non-empty string");
  }
  if ("evidence" in value && (typeof value.evidence !== "string" || !value.evidence.trim() || Buffer.byteLength(value.evidence) > 16 * 1024)) {
    throw new TypeError("Evidence requires a bounded non-empty string");
  }
  if ("reason" in value && (typeof value.reason !== "string" || !value.reason.trim() || Buffer.byteLength(value.reason) > 4096)) {
    throw new TypeError("Reason requires a bounded non-empty string");
  }
  if ("resume_when" in value && (typeof value.resume_when !== "string" || !value.resume_when.trim() || Buffer.byteLength(value.resume_when) > 1024)) {
    throw new TypeError("Resume condition requires a bounded non-empty string");
  }
  if ("text" in value && (typeof value.text !== "string" || !value.text.trim() || Buffer.byteLength(value.text) > 4096)) {
    throw new TypeError("Text requires a bounded non-empty string");
  }
  if ("priority" in value && value.priority !== undefined && !["P0", "P1", "P2", "P3", "P4"].includes(String(value.priority))) {
    throw new TypeError("Invalid priority");
  }
  if ("role" in value && value.role !== undefined && value.role !== "user" && value.role !== "agent") {
    throw new TypeError("Invalid role");
  }
  if ("claimed_by" in value && value.claimed_by !== undefined && (typeof value.claimed_by !== "string" || normalizeOwner(value.claimed_by) !== value.claimed_by)) {
    throw new TypeError("Invalid claimed_by");
  }
  if ("required_write_scopes" in value && value.required_write_scopes !== undefined) {
    if (!Array.isArray(value.required_write_scopes) || value.required_write_scopes.some(s => typeof s !== "string" || !s.trim() || s.length > 256)) {
      throw new TypeError("Invalid required_write_scopes");
    }
  }
  for (const strField of ["task_class", "action_kind", "task_repository"]) {
    if (strField in value && value[strField] !== undefined && (typeof value[strField] !== "string" || !value[strField].trim() || value[strField].length > 256)) {
      throw new TypeError(`Invalid ${strField}`);
    }
  }
  return value as unknown as RemoteAuthorityRequest;
}

/** Stable across retries and service restarts, but not reusable by another identity. */
export function remoteAuthorityOperationId(request: RemoteAuthorityRequest, principalId: string, requestId?: string): string {
  if (request.operation === "renew" || request.operation === "release") {
    return `lease-${request.operation}:${taskLeaseOperationIdentity({operation: request.operation,
      goal_id: request.goal_id, todo_id: request.todo_id, owner: request.actor_id,
      idempotency_key: request.idempotency_key, expected_version: request.expected_version,
      ttl_seconds: request.operation === "renew" ? request.ttl_seconds : null,
      new_owner: null, new_idempotency_key: null})!}`;
  }
  return `remote-task:${canonicalAuthoritySha256({principal_id: principalId, tenant_id: request.tenant_id,
    goal_id: request.goal_id, actor_id: request.actor_id,
    request_id: requestId ?? ("request_id" in request ? request.request_id : "")})}`;
}
export function remoteAuthorityResponse(request: unknown, mode: RemoteAuthorityMode,
  payload: JsonObject): JsonObject {
  return {...payload, schema_version: REMOTE_AUTHORITY_RESPONSE,
    request_sha256: canonicalAuthoritySha256({mode, request})};
}

function responseRequires(condition: unknown): asserts condition {
  if (!condition) throw new TypeError("Invalid remote authority response evidence");
}
function responseRevision(value: unknown, request: RemoteAuthorityRequest): void {
  const prefix = `${request.store_identity}:`;
  responseRequires(typeof value === "string" && value.startsWith(prefix) && /^[1-9]\d*$/u.test(value.slice(prefix.length)));
}
function responseOperationId(value: unknown, request: RemoteAuthorityRequest): string {
  const id = remoteAuthorityId(value, "operation id");
  responseRequires(request.operation === "renew" || request.operation === "release"
    ? id === remoteAuthorityOperationId(request, "")
    : id.startsWith("remote-task:") && BARE_SHA256_PATTERN.test(id.slice("remote-task:".length)));
  return id;
}
function responseLease(raw: unknown, request: RemoteAuthorityRequest, mutation: boolean): JsonObject {
  const lease = canonicalAuthorityObject(raw, "response lease");
  responseRequires(typeof lease.owner === "string" && normalizeOwner(lease.owner) === lease.owner &&
    (lease.status === "active" || lease.status === "released") &&
    Number.isSafeInteger(lease.version) && Number(lease.version) >= 0 &&
    Number.isSafeInteger(lease.lease_epoch) && Number(lease.lease_epoch) >= 1);
  if (lease.status === "active" || lease.expires_at != null) {
    responseRequires(typeof lease.expires_at === "string" && parseIsoTimestamp(lease.expires_at) !== null);
  }
  if (mutation) {
    responseRequires(lease.goal_id === request.goal_id && lease.todo_id === request.todo_id && lease.owner === request.actor_id);
    remoteAuthorityId(lease.idempotency_key, "lease key");
    responseRequires(lease.status === (request.operation === "release" ? "released" : "active"));
    if (request.operation === "renew" || request.operation === "release") {
      responseRequires(lease.idempotency_key === request.idempotency_key &&
        lease.version === request.expected_version + (request.operation === "renew" ? 1 : 0));
    }
  }
  return lease;
}
function responseCommand(raw: unknown, request: RemoteAuthorityRequest, operationId: string, historical: boolean): void {
  const result = canonicalAuthorityObject(raw, "command response");
  if (!historical) {
    responseRequires(typeof result.status === "string");
    if (["failed", "conflict", "unavailable", "ambiguous"].includes(result.status)) {
      if (result.status !== "conflict") remoteAuthorityId(result.reason_code, "failure reason");
      return;
    }
    responseRequires(["applied", "no_change", "replayed", "recovered"].includes(result.status));
    responseRevision(result.provider_revision, request);
    responseRequires(typeof result.cursor === "string" && /^[1-9]\d*$/u.test(result.cursor));
    if (result.status === "applied") responseRequires(result.changed === true);
    if (result.status === "no_change" || result.status === "replayed") responseRequires(result.changed === false);
  }
  responseRequires(typeof result.changed === "boolean");
  if (result.operation_id !== undefined) responseRequires(result.operation_id === operationId);
  if (["claim", "progress", "complete", "block", "defer", "todo_add"].includes(request.operation)) {
    if (result.todo_id !== undefined && request.todo_id !== undefined) responseRequires(result.todo_id === request.todo_id);
  }
  if (request.operation === "claim") {
    responseRequires(result.claimed_by === request.actor_id && result.claim_operation_id === operationId);
  }
  if (request.operation === "complete" && !historical && result.status === "applied") {
    responseRequires(result.completed === true);
  }
  if (request.operation === "renew" || request.operation === "release") {
    responseRequires(result.handoff_mode === "hard_lease");
    if (request.operation === "release" && result.missing === true) {
      responseRequires(request.expected_version === 0 && result.changed === false &&
        result.released === false && result.lease == null);
      return;
    }
    responseLease(result.lease, request, true);
    if (request.operation === "renew") responseRequires(result.renewed === true);
    if (request.operation === "release") responseRequires(result.released === true);
  }
}

/** Correlation is separate from usable evidence. This validates the projected
 * wire shape, not current execution authority or a second copy of lease rules. */
export function decodeRemoteAuthorityResponse(raw: unknown, request: RemoteAuthorityRequest,
  mode: RemoteAuthorityMode): JsonObject {
  const value = canonicalAuthorityObject(raw, "remote authority response");
  responseRequires(value.schema_version === REMOTE_AUTHORITY_RESPONSE &&
    value.request_sha256 === canonicalAuthoritySha256({mode, request}));
  responseRequires(typeof value.status === "string");
  if (["rejected", "unavailable", "ambiguous"].includes(value.status)) {
    remoteAuthorityId(value.reason_code, "response reason");
    responseRequires(value.status !== "ambiguous" || mode === "execute" && request.operation !== "read" && request.operation !== "snapshot");
  } else if (mode === "recover" && request.operation !== "read" && request.operation !== "snapshot") {
    const id = responseOperationId(value.operation_id, request);
    if (value.status !== "missing") {
      responseRequires(value.status === "found" && value.historical_only === true &&
        typeof value.receipt_sha256 === "string" && BARE_SHA256_PATTERN.test(value.receipt_sha256) &&
        typeof value.cursor === "string" && /^[1-9]\d*$/u.test(value.cursor));
      responseRevision(value.provider_revision, request);
      responseCommand(value.result, request, id, true);
    }
  } else if (mode === "execute" && request.operation === "read") {
    if (value.status !== "missing") {
      responseRequires(value.status === "found");
      responseRevision(value.provider_revision, request);
      const todo = canonicalAuthorityObject(value.todo, "read Todo");
      responseRequires(todo.todo_id === request.todo_id);
      remoteAuthorityId(todo.status, "Todo status");
      if (todo.claimed_by != null) responseRequires(typeof todo.claimed_by === "string" && normalizeOwner(todo.claimed_by) === todo.claimed_by);
      if (todo.note !== undefined) responseRequires(todo.note === null || typeof todo.note === "string");
      if (todo.note_omitted !== undefined) responseRequires(todo.note_omitted === true && !Object.hasOwn(todo, "note"));
      if (value.lease !== null) responseLease(value.lease, request, false);
    }
  } else if (mode === "execute" && request.operation === "snapshot") {
    responseRequires(value.status === "snapshot");
    responseRevision(value.provider_revision, request);
    responseRequires(value.goal_id === request.goal_id && value.store_identity === request.store_identity &&
      typeof value.receipt_sha256 === "string" && BARE_SHA256_PATTERN.test(value.receipt_sha256) &&
      Array.isArray(value.todos) && Array.isArray(value.leases));
  } else {
    responseRequires(mode === "execute" && request.operation !== "read" && request.operation !== "snapshot" && value.status === "result");
    responseCommand(value.result, request, responseOperationId(value.operation_id, request), false);
  }
  return value;
}
