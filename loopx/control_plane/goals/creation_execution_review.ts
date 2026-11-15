/** Pure Goal creation intent and permission review shared with presentation.
 * The existing action owner confirms this request; native Task admission and
 * filesystem authority still require their current canonical owners.
 */
import type {JsonObject} from "../runtime_decode.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";

export type GoalCreationPermission = "read_only" | "workspace_write_on_confirmation";
export type GoalCreationExecutionReview = {
  requested: true; permission: GoalCreationPermission; writeScope: string[];
};

export function normalizeGoalCreationExecution(params: JsonObject): JsonObject {
  const result: JsonObject = {};
  if (params.permission !== undefined) {
    if (params.permission !== "read_only" && params.permission !== "workspace_write_on_confirmation") {
      throw new EffectRuntimeRequestError("permission must select the existing Goal creation read or workspace-write review");
    }
    result.permission = params.permission;
  }
  if (params.start_execution !== undefined) {
    if (typeof params.start_execution !== "boolean") {
      throw new EffectRuntimeRequestError("start_execution must be true or false");
    }
    result.start_execution = params.start_execution;
  }
  if (params.start_execution === true) {
    if (params.agent_id !== "codex" || params.permission === undefined) {
      throw new EffectRuntimeRequestError("original Task start requires the Codex Agent and an explicit permission choice");
    }
    if (params.native_token_budget != null) {
      throw new EffectRuntimeRequestError("select either the LoopX original Task or native host Goal activation for this creation");
    }
    if (!Array.isArray(params.initial_todos) || params.initial_todos.length !== 1
        || typeof params.completion_criteria !== "string" || !params.completion_criteria.trim()) {
      throw new EffectRuntimeRequestError("original Task start requires one initial Task and its completion criteria");
    }
  }
  return result;
}

export function goalCreationExecutionReview(parameters: JsonObject): GoalCreationExecutionReview | undefined {
  if (parameters.start_execution === undefined || parameters.start_execution === false) return undefined;
  const normalized = normalizeGoalCreationExecution(parameters);
  return {requested: true, permission: normalized.permission as GoalCreationPermission,
    writeScope: normalized.permission === "workspace_write_on_confirmation" ? ["**"] : []};
}
