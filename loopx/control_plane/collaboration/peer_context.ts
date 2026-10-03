import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { requireJsonObject, requireNonEmptyString, requireStringArray } from "../runtime_decode.ts";
import { resolveConversationScope } from "./conversation_scope.ts";

/** Admission to receive source context only; never work or execution authority.
 * The adapter resolves immutable parent lineage and observes the current source
 * grant. A peer hop cannot turn an external request into an owner request.
 */
export function requirePeerContextAccess(params: JsonObject): JsonObject {
  const scope = resolveConversationScope(requireJsonObject(params.conversation, "source conversation"));
  if (scope.private_conversation) return { allowed: true };
  if (scope.kind !== "external_audience") {
    throw new EffectRuntimeRequestError("original source conversation unavailable for peer context forwarding");
  }
  const grant = requireJsonObject(params.grant, "source grant");
  const source = requireNonEmptyString(params.source_id, "original source id");
  if (grant.mode !== "context_only" || grant.source_id !== source || !Array.isArray(grant.targets)) {
    throw new EffectRuntimeRequestError("original source authorization unavailable for peer context forwarding");
  }
  const goal = requireNonEmptyString(params.goal_id, "peer Goal");
  const agents = requireStringArray(params.agent_ids, "peer context recipients");
  if (!agents.length || agents.some(agent => !agent.trim())) {
    throw new EffectRuntimeRequestError("peer context recipients must be nonempty");
  }
  const targets = grant.targets.map(target => requireJsonObject(target, "authorized context recipient"));
  const missing = agents.find(agent => !targets.some(target => target.goal_id === goal && target.agent_id === agent));
  if (missing !== undefined) {
    throw new EffectRuntimeRequestError(`peer context recipient ${missing} is not authorized for the original source`);
  }
  return { allowed: true };
}
