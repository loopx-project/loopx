/** Admission from provider-verified addressing evidence. Capture alone never
 * grants a turn; this does not grant tools, delegation or protected operations. */
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";

export function resolveConversationTrigger(input: JsonObject): JsonObject {
  const mode = input.mode ?? "addressed";
  if (mode !== "addressed" && mode !== "human_messages") {
    throw new EffectRuntimeRequestError("conversation trigger must be addressed or human_messages");
  }
  let reason = "not_addressed";
  let authorized = false;
  if (input.historical === true) reason = "historical_context_only";
  else if (input.self_message === true) reason = "self_message";
  else if (mode === "human_messages" && input.bot_message === true) reason = "bot_message";
  else if (input.addressed === true) {
    authorized = true;
    reason = "addressed";
  } else if (mode === "human_messages" && input.human === true) {
    authorized = true;
    reason = "configured_human_message";
  } else if (mode === "human_messages") reason = "human_identity_unverified";
  return {mode, authorized, reason};
}
