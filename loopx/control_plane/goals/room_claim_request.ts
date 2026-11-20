import {requireJsonObject, requireNonEmptyString, requireStringArray} from "../runtime_decode.ts";
import type {JsonObject} from "../effect_program.ts";
import {requireAuthorityStoreId} from "../coordination/authority_store_codec.ts";
import {normalizeTodoAgent} from "../coordination/todo_agents.ts";

/** One scoped claim intent, not an actor lifecycle or execution grant. */
export function decodeRoomClaimRequest(value: unknown): JsonObject {
  const p = requireJsonObject(value, "room claim request");
  const fields = ["schema_version", "command", "goal_id", "actor_id", "todo_id", "expected_revision",
    "idempotency_key", "authorized_principals", "expires_at"];
  if (Object.keys(p).length !== fields.length || fields.some(k => !Object.hasOwn(p, k)) ||
      p.schema_version !== "loopx_room_claim_request_v0" || p.command !== "claim_todo") {
    throw new Error("unsupported room claim request");
  }
  const principals = requireStringArray(p.authorized_principals, "authorized principals");
  if (!principals.length || principals.length > 8 || new Set(principals).size !== principals.length ||
      principals.some(v => !/^[a-z][a-z0-9_-]*:[A-Za-z0-9][A-Za-z0-9_.:-]{0,200}$/.test(v))) {
    throw new Error("invalid scoped principals");
  }
  const expires = requireNonEmptyString(p.expires_at, "expires_at");
  if (!/(?:Z|[+-]\d{2}:\d{2})$/.test(expires) || Number.isNaN(new Date(expires).valueOf())) {
    throw new Error("invalid room claim expiry");
  }
  return {...p, goal_id: requireAuthorityStoreId(p.goal_id, "goal id"),
    actor_id: normalizeTodoAgent(p.actor_id, "actor id"), todo_id: requireAuthorityStoreId(p.todo_id, "todo id"),
    expected_revision: requireAuthorityStoreId(p.expected_revision, "expected revision"),
    idempotency_key: requireAuthorityStoreId(p.idempotency_key, "idempotency key"),
    authorized_principals: [...principals].sort(), expires_at: new Date(expires).toISOString()};
}

/** Provider authentication is done by the Lark boundary before this admission. */
export function admitRoomClaimCallback(value: unknown): JsonObject {
  const p = requireJsonObject(value, "room claim callback admission");
  const request = decodeRoomClaimRequest(p.request);
  const now = new Date(requireNonEmptyString(p.observed_at, "observation time"));
  if (Number.isNaN(now.valueOf()) || !["active", "revoked"].includes(String(p.scope_state))) {
    throw new Error("invalid room claim scope observation");
  }
  const reason = p.scope_state === "revoked" ? "offer_revoked" :
    now >= new Date(String(request.expires_at)) ? "offer_expired" :
    !(request.authorized_principals as string[]).includes(String(p.principal)) ? "principal_not_authorized" : null;
  return {schema_version: "loopx_room_claim_admission_v0", allowed: reason === null,
    reason_code: reason, execution_authority_granted: false};
}
