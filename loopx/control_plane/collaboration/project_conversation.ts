import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { requireNonEmptyString } from "../runtime_decode.ts";
import {normalizeProjectContext} from "./conversation_scope.ts";

/** A workspace observation is supplied by the host, never by a model or transport.
 * Its workspace grant does not enroll a Goal, discover a portfolio or authorize peer work.
 */
function normalized(value: unknown): JsonObject {
  try {return normalizeProjectContext(value);}
  catch {throw new EffectRuntimeRequestError("invalid project conversation context");}
}

export function resolveProjectConversation(params: JsonObject): JsonObject {
  const ref = requireNonEmptyString(params.project_ref, "authorized project reference");
  if (!Array.isArray(params.available)) throw new EffectRuntimeRequestError("host workspace grants unavailable");
  const contexts = params.available.map(normalized);
  const matches = contexts.filter(row => row.project_ref === ref);
  if (matches.length !== 1) throw new EffectRuntimeRequestError("project is outside the host workspace grants");
  const context = matches[0];
  if (params.session_context !== undefined
      && JSON.stringify(normalized(params.session_context)) !== JSON.stringify(context)) {
    throw new EffectRuntimeRequestError("project conversation grant changed; open a new Session");
  }
  return {context, channel_id: `project.${ref}`};
}
