/** Explicit candidate service policy, reread atomically for every admission.
 * It owns service-scoped peers only; it does not promote a local Goal registry. */
import {createHash, timingSafeEqual} from "node:crypto";
import {open} from "node:fs/promises";
import type {JsonObject} from "../effect_program.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {canonicalAuthorityObject, canonicalAuthoritySha256, hasExactAuthorityKeys} from "./authority_store_codec.ts";
import {POSTGRESQL_STORE_IDENTITY_PATTERN} from "./postgresql_authority_store.ts";
import {REMOTE_AUTHORITY_OPERATIONS, remoteAuthorityId, type RemoteAuthorityRequest} from "./remote_authority_contract.ts";
import type {RemoteAuthorityAdmission} from "./remote_authority_service.ts";

export const REMOTE_AUTHORITY_POLICY = "loopx_remote_authority_policy_v0";
const sameScope = (a: JsonObject, b: {tenant_id: string; goal_id: string; store_identity: string}) =>
  a.tenant_id === b.tenant_id && a.goal_id === b.goal_id && a.store_identity === b.store_identity;
function object(value: unknown, keys: string[], name: string): JsonObject {
  const result = canonicalAuthorityObject(value, name);
  if (!hasExactAuthorityKeys(result, keys)) throw new TypeError(`Invalid ${name}`);
  return result;
}
export function decodeRemoteAuthorityPolicy(value: unknown): JsonObject {
  const policy = object(value, ["schema_version", "scopes", "principals"], "service policy");
  if (policy.schema_version !== REMOTE_AUTHORITY_POLICY || !Array.isArray(policy.scopes) ||
      !Array.isArray(policy.principals) || policy.scopes.length > 128 || policy.principals.length > 256) throw new TypeError("Invalid service policy");
  const scopes = policy.scopes.map(raw => {
    const scope = object(raw, ["tenant_id", "goal_id", "store_identity", "registered_agents"], "service scope");
    remoteAuthorityId(scope.tenant_id, "tenant"); remoteAuthorityId(scope.goal_id, "Goal");
    if (typeof scope.store_identity !== "string" || !POSTGRESQL_STORE_IDENTITY_PATTERN.test(scope.store_identity) ||
        !Array.isArray(scope.registered_agents) || scope.registered_agents.length === 0 || scope.registered_agents.length > 128) throw new TypeError("Invalid service scope");
    scope.registered_agents.forEach(a => remoteAuthorityId(a, "registered actor"));
    if (new Set(scope.registered_agents).size !== scope.registered_agents.length) throw new TypeError("Duplicate registered actor");
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
      const grant = object(rawGrant, ["tenant_id", "goal_id", "actor_id", "operations"], "grant");
      const scope = scopes.find(s => s.tenant_id === grant.tenant_id && s.goal_id === grant.goal_id);
      if (!scope || !(scope.registered_agents as string[]).includes(String(grant.actor_id)) ||
          !Array.isArray(grant.operations) || grant.operations.length === 0 ||
          grant.operations.some(op => !REMOTE_AUTHORITY_OPERATIONS.includes(op as typeof REMOTE_AUTHORITY_OPERATIONS[number])) ||
          new Set(grant.operations).size !== grant.operations.length) throw new TypeError("Invalid grant");
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
  request: RemoteAuthorityRequest): RemoteAuthorityAdmission {
  const digest = createHash("sha256").update(credential).digest();
  const principal = (policy.principals as JsonObject[]).find(p =>
    timingSafeEqual(Buffer.from(String(p.credential_sha256), "hex"), digest));
  if (!principal) return {status: "rejected", reason_code: "principal_unauthenticated"};
  const scope = (policy.scopes as JsonObject[]).find(s => sameScope(s, request));
  const grant = (principal.grants as JsonObject[]).find(g => g.tenant_id === request.tenant_id &&
    g.goal_id === request.goal_id && g.actor_id === request.actor_id && (g.operations as string[]).includes(request.operation));
  if (!scope || !grant) return {status: "rejected", reason_code: "remote_authority_scope_denied"};
  return {status: "allowed", principal_id: String(principal.principal_id),
    policy_revision: canonicalAuthoritySha256(policy), registered_agents: [...scope.registered_agents as string[]]};
}

export function fileRemoteAuthorityAdmission(path: string) {
  return async (credential: string, request: RemoteAuthorityRequest): Promise<RemoteAuthorityAdmission> => {
    // A rename publishes one complete policy. Missing, malformed or unreadable
    // policy fails closed; never retain a stale last-known-good grant.
    return remoteAuthorityPolicyAdmission(await readRemoteAuthorityPolicy(path), credential, request);
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
