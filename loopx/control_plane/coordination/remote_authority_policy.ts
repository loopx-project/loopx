/** Explicit candidate service policy, reread atomically for every admission.
 * It owns service-scoped peers only; it does not promote a local Goal registry. */
import {createHash, timingSafeEqual} from "node:crypto";
import {open} from "node:fs/promises";
import type {JsonObject} from "../effect_program.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {parseWireExactGoalRef, sameExactGoalRef, type WireExactGoalRef} from "../goals/acceptance_lifecycle.ts";
import {normalizeTodoId} from "../work_items/task_lease_acquire.ts";
import {canonicalAuthorityObject, canonicalAuthoritySha256, hasExactAuthorityKeys} from "./authority_store_codec.ts";
import {POSTGRESQL_STORE_IDENTITY_PATTERN} from "./postgresql_authority_store.ts";
import {REMOTE_AUTHORITY_OPERATIONS, remoteAuthorityId, remoteAuthorityOperationId,
  type RemoteAuthorityMode, type RemoteAuthorityRequest} from "./remote_authority_contract.ts";
import type {RemoteAuthorityAdmission} from "./remote_authority_service.ts";

export const REMOTE_AUTHORITY_POLICY = "loopx_remote_authority_policy_v0";
export const REMOTE_AUTHORITY_SCOPED_READ_POLICY = "loopx_remote_authority_scoped_read_policy_v1";
const sameScope = (a: JsonObject, b: {tenant_id: string; goal_id: string; store_identity: string}) =>
  a.tenant_id === b.tenant_id && a.goal_id === b.goal_id && a.store_identity === b.store_identity;
function object(value: unknown, keys: string[], name: string, optionalKeys: string[] = []): JsonObject {
  const result = canonicalAuthorityObject(value, name);
  if (!hasExactAuthorityKeys(result, [...keys, ...optionalKeys.filter(key => Object.hasOwn(result, key))])) {
    throw new TypeError(`Invalid ${name}`);
  }
  return result;
}
export function decodeRemoteAuthorityPolicy(value: unknown): JsonObject {
  const policy = object(value, ["schema_version", "scopes", "principals"], "service policy");
  const scoped = policy.schema_version === REMOTE_AUTHORITY_SCOPED_READ_POLICY;
  if (!(scoped || policy.schema_version === REMOTE_AUTHORITY_POLICY) || !Array.isArray(policy.scopes) ||
      !Array.isArray(policy.principals) || policy.scopes.length > 128 || policy.principals.length > 256) throw new TypeError("Invalid service policy");
  const scopes = policy.scopes.map(raw => {
    const scope = object(raw, ["tenant_id", "goal_id", "store_identity", "registered_agents",
      ...(scoped ? ["todo_read_acl"] : [])], "service scope");
    remoteAuthorityId(scope.tenant_id, "tenant"); remoteAuthorityId(scope.goal_id, "Goal");
    if (typeof scope.store_identity !== "string" || !POSTGRESQL_STORE_IDENTITY_PATTERN.test(scope.store_identity) ||
        !Array.isArray(scope.registered_agents) || scope.registered_agents.length === 0 || scope.registered_agents.length > 128) throw new TypeError("Invalid service scope");
    scope.registered_agents.forEach(a => remoteAuthorityId(a, "registered actor"));
    if (new Set(scope.registered_agents).size !== scope.registered_agents.length) throw new TypeError("Duplicate registered actor");
    if (scoped) {
      if (!Array.isArray(scope.todo_read_acl)) throw new TypeError("Invalid Todo read ACL");
      const actors = new Set(scope.registered_agents), entries = new Set<string>();
      for (const rawEntry of scope.todo_read_acl) {
        const entry = object(rawEntry, ["goal_ref", "todo_id", "principal_id", "actor_id"],
          "Todo read ACL entry", ["receipt_operation_ids", "todo_origin_operation_id"]);
        const goal = parseWireExactGoalRef(entry.goal_ref, "Todo read ACL Goal");
        remoteAuthorityId(entry.principal_id, "ACL principal");
        remoteAuthorityId(entry.todo_id, "ACL Todo");
        if (goal.goal_id !== scope.goal_id || !actors.has(entry.actor_id) || normalizeTodoId(entry.todo_id) !== entry.todo_id) {
          throw new TypeError("Invalid Todo read ACL scope");
        }
        const key = JSON.stringify([goal.goal_instance_id, entry.todo_id, entry.principal_id, entry.actor_id]);
        if (entries.has(key)) throw new TypeError("Duplicate Todo read ACL entry");
        entries.add(key);
        if (Object.hasOwn(entry, "todo_origin_operation_id")) {
          remoteAuthorityId(entry.todo_origin_operation_id, "Todo origin operation");
        }
        if (Object.hasOwn(entry, "receipt_operation_ids")) {
          const ids = entry.receipt_operation_ids;
          if (!Array.isArray(ids) || new Set(ids).size !== ids.length) throw new TypeError("Invalid receipt read ACL");
          ids.forEach(id => remoteAuthorityId(id, "receipt ACL operation"));
        }
      }
    }
    return scope;
  });
  const scopeKeys = scopes.map(s => JSON.stringify([s.tenant_id, s.goal_id]));
  if (new Set(scopeKeys).size !== scopeKeys.length) throw new TypeError("Duplicate service scope");
  const principals = policy.principals.map(raw => {
    const principal = object(raw, ["principal_id", "credential_sha256", "grants"], "principal");
    remoteAuthorityId(principal.principal_id, "principal");
    if (typeof principal.credential_sha256 !== "string" || !BARE_SHA256_PATTERN.test(principal.credential_sha256) ||
        !Array.isArray(principal.grants) || principal.grants.length > 128) throw new TypeError("Invalid principal");
    const seen = new Set<string>();
    principal.grants.forEach(rawGrant => {
      const grant = object(rawGrant, ["tenant_id", "goal_id", "actor_id", "operations",
        ...(scoped ? ["goal_ref"] : [])], "grant");
      const scope = scopes.find(s => s.tenant_id === grant.tenant_id && s.goal_id === grant.goal_id);
      if (!scope || !(scope.registered_agents as string[]).includes(String(grant.actor_id)) ||
          !Array.isArray(grant.operations) || grant.operations.length === 0 ||
          grant.operations.some(op => !REMOTE_AUTHORITY_OPERATIONS.includes(op as typeof REMOTE_AUTHORITY_OPERATIONS[number])) ||
          new Set(grant.operations).size !== grant.operations.length) throw new TypeError("Invalid grant");
      if (scoped && (parseWireExactGoalRef(grant.goal_ref, "read grant Goal").goal_id !== grant.goal_id ||
          grant.operations.some(op => op !== "read" && op !== "snapshot"))) throw new TypeError("Invalid scoped read grant");
      const key = JSON.stringify([grant.tenant_id, grant.goal_id, grant.actor_id]);
      if (seen.has(key)) throw new TypeError("Duplicate grant");
      seen.add(key);
    });
    return principal;
  });
  for (const field of ["principal_id", "credential_sha256"]) {
    if (new Set(principals.map(p => p[field])).size !== principals.length) throw new TypeError("Duplicate principal or credential");
  }
  return policy;
}

export function remoteAuthorityPolicyAdmission(policy: JsonObject, credential: string,
  request: RemoteAuthorityRequest, mode: RemoteAuthorityMode = "execute"): RemoteAuthorityAdmission {
  const digest = createHash("sha256").update(credential).digest();
  const principal = (policy.principals as JsonObject[]).find(p =>
    timingSafeEqual(Buffer.from(String(p.credential_sha256), "hex"), digest));
  if (!principal) return {status: "rejected", reason_code: "principal_unauthenticated"};
  const scope = (policy.scopes as JsonObject[]).find(s => sameScope(s, request));
  const scoped = policy.schema_version === REMOTE_AUTHORITY_SCOPED_READ_POLICY;
  const recoverScoped = scoped && mode === "recover";
  const operation = recoverScoped ? "read" : request.operation;
  const grant = (principal.grants as JsonObject[]).find(g => g.tenant_id === request.tenant_id &&
    g.goal_id === request.goal_id && g.actor_id === request.actor_id && (g.operations as string[]).includes(operation));
  if (!scope || !grant) return {status: "rejected", reason_code: "remote_authority_scope_denied"};
  const allowed = {status: "allowed" as const, principal_id: String(principal.principal_id),
    policy_revision: canonicalAuthoritySha256(policy), registered_agents: [...scope.registered_agents as string[]]};
  if (!scoped) return allowed;

  const goalRef = grant.goal_ref as WireExactGoalRef;
  const entries = (scope.todo_read_acl as JsonObject[])
    .filter(entry => entry.principal_id === principal.principal_id && entry.actor_id === request.actor_id &&
      sameExactGoalRef(entry.goal_ref as WireExactGoalRef, goalRef));
  const todoIds = entries.map(entry => String(entry.todo_id));
  if (todoIds.length === 0 || request.todo_id !== undefined && !todoIds.includes(request.todo_id)) {
    return {status: "rejected", reason_code: "remote_authority_scope_denied"};
  }
  const readScope = {goal_ref: goalRef, todo_ids: todoIds,
    todo_origins: Object.fromEntries(entries.filter(entry => entry.todo_origin_operation_id !== undefined)
      .map(entry => [String(entry.todo_id), String(entry.todo_origin_operation_id)]))};
  if (!recoverScoped) return {...allowed, read_scope: readScope};
  const id = remoteAuthorityOperationId(request, allowed.principal_id);
  if (!entries.some(entry => entry.todo_id === request.todo_id &&
      (entry.receipt_operation_ids as string[] | undefined)?.includes(id))) {
    return {status: "rejected", reason_code: "remote_authority_scope_denied"};
  }
  return {...allowed, read_scope: {...readScope, receipt_operation_id: id}};
}

export function fileRemoteAuthorityAdmission(path: string, expectedSchema: string = REMOTE_AUTHORITY_POLICY) {
  return async (credential: string, request: RemoteAuthorityRequest,
    mode: RemoteAuthorityMode = "execute"): Promise<RemoteAuthorityAdmission> => {
    // A rename publishes one complete policy. Missing, malformed or unreadable
    // policy fails closed; never retain a stale last-known-good grant.
    const policy = await readRemoteAuthorityPolicy(path);
    if (policy.schema_version !== expectedSchema) throw new TypeError("Service policy schema changed; restart the listener");
    return remoteAuthorityPolicyAdmission(policy, credential, request, mode);
  };
}

export async function readRemoteAuthorityPolicy(path: string): Promise<JsonObject> {
  const file = await open(path, "r");
  try {
    const bytes = Buffer.alloc(256 * 1024 + 1);
    let length = 0;
    while (length < bytes.length) {
      const {bytesRead} = await file.read(bytes, length, bytes.length - length);
      if (bytesRead === 0) break;
      length += bytesRead;
    }
    if (length === bytes.length) throw new TypeError("Service policy is too large");
    return decodeRemoteAuthorityPolicy(JSON.parse(bytes.subarray(0, length).toString("utf8")));
  } finally {await file.close();}
}
