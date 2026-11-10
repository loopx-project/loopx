/** Candidate wire contract. Clients submit intentions, never authority facts. */
import type {JsonObject} from "../effect_program.ts";
import {canonicalAuthorityObject, canonicalAuthoritySha256, hasExactAuthorityKeys,
  requireAuthorityStoreId} from "./authority_store_codec.ts";
import {POSTGRESQL_STORE_IDENTITY_PATTERN} from "./postgresql_authority_store.ts";
import {taskLeaseOperationIdentity} from "../work_items/task_lease_operation_identity.ts";
import {normalizeTodoId, normalizeGoalId, normalizeOwner} from "../work_items/task_lease_acquire.ts";

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
