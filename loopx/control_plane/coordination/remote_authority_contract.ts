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
export const REMOTE_AUTHORITY_OPERATIONS = ["read", "claim", "progress", "renew", "release"] as const;
export type RemoteAuthorityOperation = typeof REMOTE_AUTHORITY_OPERATIONS[number];
export interface RemoteAuthorityScope {
  tenant_id: string;
  goal_id: string;
  actor_id: string;
  store_identity: string;
}
interface Base extends RemoteAuthorityScope {
  schema_version: typeof REMOTE_AUTHORITY_REQUEST;
  todo_id: string;
}
export type RemoteAuthorityRequest = Base & (
  | {operation: "read"}
  | {operation: "claim"; request_id: string}
  | {operation: "progress"; request_id: string; claim_request_id: string; note: string}
  | {operation: "renew"; idempotency_key: string; expected_version: number; ttl_seconds: number}
  | {operation: "release"; idempotency_key: string; expected_version: number}
);
export type RemoteAuthorityMode = "execute" | "recover";
export function remoteAuthorityId(value: unknown, name: string): string {
  const id = requireAuthorityStoreId(value, name);
  if (id.length > 200 || /[\x00-\x20\x7f]/u.test(id)) throw new TypeError(`Invalid ${name}`);
  return id;
}
export function decodeRemoteAuthorityRequest(raw: unknown): RemoteAuthorityRequest {
  const value = canonicalAuthorityObject(raw, "remote authority request");
  const keys = ["schema_version", "tenant_id", "goal_id", "actor_id", "store_identity", "todo_id", "operation"];
  if (value.schema_version !== REMOTE_AUTHORITY_REQUEST ||
      !REMOTE_AUTHORITY_OPERATIONS.includes(value.operation as RemoteAuthorityOperation)) throw new TypeError("Invalid request schema or operation");
  switch (value.operation) {
    case "claim": keys.push("request_id"); break;
    case "progress": keys.push("request_id", "claim_request_id", "note"); break;
    case "renew": keys.push("ttl_seconds", "idempotency_key", "expected_version"); break;
    case "release": keys.push("idempotency_key", "expected_version"); break;
  }
  if (!hasExactAuthorityKeys(value, keys)) throw new TypeError("Unexpected or missing request fields");
  for (const key of ["tenant_id", "goal_id", "actor_id", "todo_id", "request_id", "claim_request_id", "idempotency_key"]) {
    if (key in value) remoteAuthorityId(value[key], key);
  }
  if (normalizeTodoId(value.todo_id) !== value.todo_id || normalizeGoalId(value.goal_id) !== value.goal_id ||
      normalizeOwner(value.actor_id) !== value.actor_id) throw new TypeError("Use canonical task, Goal and actor identities");
  if (typeof value.store_identity !== "string" || !POSTGRESQL_STORE_IDENTITY_PATTERN.test(value.store_identity)) throw new TypeError("Invalid store identity");
  if ("expected_version" in value && (!Number.isSafeInteger(value.expected_version) || Number(value.expected_version) < 1)) throw new TypeError("Invalid lease version");
  if ("ttl_seconds" in value && (!Number.isSafeInteger(value.ttl_seconds) || Number(value.ttl_seconds) < 1 || Number(value.ttl_seconds) > 3600)) throw new TypeError("Invalid lease duration");
  if (value.operation === "progress" && (typeof value.note !== "string" || !value.note.trim() || Buffer.byteLength(value.note) > 4096)) throw new TypeError("Progress requires a bounded note");
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
  if (request.operation === "claim" || request.operation === "progress") responseRequires(result.todo_id === request.todo_id);
  if (request.operation === "claim") {
    responseRequires(result.claimed_by === request.actor_id && result.claim_operation_id === operationId);
  }
  if (request.operation !== "progress") {
    responseRequires(result.handoff_mode === "hard_lease");
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
    responseRequires(value.status !== "ambiguous" || mode === "execute" && request.operation !== "read");
  } else if (mode === "recover" && request.operation !== "read") {
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
  } else {
    responseRequires(mode === "execute" && request.operation !== "read" && value.status === "result");
    responseCommand(value.result, request, responseOperationId(value.operation_id, request), false);
  }
  return value;
}
