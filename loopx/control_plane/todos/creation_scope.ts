/** Compose the existing create-scope, wait and Monitor owners for one draft.
 * This is admission planning, never a provider commit or an execution grant. */
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireBoolean, requireJsonObject} from "../runtime_decode.ts";
import {planTodoAuthoringScope, TODO_AUTHORING_SCOPE_REQUEST_SCHEMA} from "./authoring_scope.ts";
import {planMonitorMetadata, TODO_MONITOR_METADATA_REQUEST_SCHEMA} from "./monitor_metadata.ts";

export const TODO_CREATION_SCOPE_REQUEST_SCHEMA = "todo_creation_scope_request_v0";
export const TODO_CREATION_SCOPE_RESULT_SCHEMA = "todo_creation_scope_result_v0";

export function planTodoCreationScope(value: unknown): JsonObject {
  const request = requireJsonObject(value, "Todo creation scope request");
  if (request.schema_version !== TODO_CREATION_SCOPE_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError("Todo creation scope schema mismatch");
  }
  const intent = requireJsonObject(request.intent, "Todo creation intent");
  const scope = planTodoAuthoringScope({
    schema_version: TODO_AUTHORING_SCOPE_REQUEST_SCHEMA,
    command: "create", todo: {}, role: request.role, goal_id: request.goal_id,
    registered_agents: request.registered_agents, intent,
  });
  // Preserve the public add diagnostic order: scope/wait first, then the
  // historical ID wire codec, then Monitor metadata. No second resume plan.
  if (requireBoolean(request.unblocks_todo_id_declared, "unblocks_todo_id_declared") &&
      request.unblocks_todo_id === null) {
    throw new EffectRuntimeRequestError(
      "unblocks_todo_id must use the public token shape todo_<letters-digits-underscore-hyphen>",
    );
  }
  const monitor = planMonitorMetadata({
    schema_version: TODO_MONITOR_METADATA_REQUEST_SCHEMA,
    existing: {}, metadata: request.monitor_metadata, role: request.role,
    task_class: intent.task_class, generated_at: request.generated_at,
    resume_when: scope.normalized_resume_when, enforce_boundedness: true,
  });
  return {...scope, schema_version: TODO_CREATION_SCOPE_RESULT_SCHEMA,
    unblocks_todo_id: request.unblocks_todo_id,
    monitor_metadata: monitor.metadata};
}
