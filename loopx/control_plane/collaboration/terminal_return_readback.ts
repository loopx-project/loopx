import {createHash} from "node:crypto";
import type {JsonObject} from "../effect_program.ts";
import {parseTodoTimestampMicros} from "../runtime_timestamp.ts";
import {returnMessageId, returnTranscriptText} from "./result_publication.ts";

const SCHEMA = "manager_return_private_readback_v0";
export const TERMINAL_RETURN_READBACK_SUFFIX = ".delivery-readback.json";

export function terminalReturnIdentityConflict(state: JsonObject, requestId: string, key: string): boolean {
  return state.status === "delivered" && (key === "conclusion" || key.startsWith("conclusion-"))
    && state.message_id !== returnMessageId(requestId, key);
}

// Bind the new observation to the damaged receipt and the complete immutable
// result and route. Replacing any of them requires fresh source evidence.
function basis(state: JsonObject, reply: JsonObject, route: JsonObject): string {
  return createHash("sha256").update(JSON.stringify([state, reply, route])).digest("hex");
}

export function confirmedTerminalReturn(readback: JsonObject | null, state: JsonObject,
    reply: JsonObject, route: JsonObject): string | null {
  return readback?.schema_version === SCHEMA && readback.basis_sha256 === basis(state, reply, route)
    && typeof readback.delivered_at === "string" && readback.delivered_at.length <= 64
    && parseTodoTimestampMicros(readback.delivered_at) !== null ? readback.delivered_at : null;
}

export function observeTerminalReturn(message: JsonObject, messageId: string, turnId: unknown,
    state: JsonObject, reply: JsonObject, route: JsonObject, exact: boolean): JsonObject | null {
  const deliveredAt = message.created_at;
  if (typeof turnId !== "string" || message.turn_id !== turnId || message.message_id !== messageId
      || message.role !== "agent" || message.origin !== "manager_followup"
      || message.text !== returnTranscriptText(reply, exact)
      || (message.attachments != null && (!Array.isArray(message.attachments) || message.attachments.length > 0))
      || message.goal_draft != null || typeof deliveredAt !== "string" || deliveredAt.length > 64
      || parseTodoTimestampMicros(deliveredAt) === null) return null;
  return {schema_version: SCHEMA, basis_sha256: basis(state, reply, route), delivered_at: deliveredAt};
}
