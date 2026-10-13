import { ZCODE_GOAL_ACTIONS, ZCODE_NATIVE_GOAL_STATUSES, ZCODE_IDENTITY_SCOPES, type ZCodeGoalAction, type ZCodeGoalReadback, type ZCodeModelSelection } from "../../../../../loopx/zcode_goal_mode/contract.js";
import {z} from "zod";
import {requestJson} from "./chat.js";

const zcodeModelSelectionSchema = z.object({
  providerId: z.string().min(1), modelId: z.string().min(1),
  options: z.object({reasoningLevel: z.string().min(1)}).optional(),
});

// Validate provider transport observations; action permission remains with the typed provider owner.
const zcodeGoalReadbackSchema = z.object({
  ok: z.boolean(), available: z.boolean(), reason: z.string().optional(),
  goal_id: z.string(), agent_id: z.string(), goal_creation_operation_id: z.string().nullable(),
  goal_ref: z.object({goal_id: z.string(), goal_instance_id: z.string().min(1).optional()}),
  identity_scope: z.enum(ZCODE_IDENTITY_SCOPES).optional(),
  binding: z.object({mode: z.literal("managed_cli"), connected: z.boolean(), cli_path: z.string(), protocol: z.string()}).nullable(),
  native: z.object({
    session_id: z.string(), target_id: z.string().nullable(),
    status: z.enum(ZCODE_NATIVE_GOAL_STATUSES).nullable(), running: z.boolean(),
    session_status: z.string().optional(), raw_status: z.string().nullable().optional(), usage: z.null().optional(),
    objective_sha256: z.string().nullable().optional(), selected_model: zcodeModelSelectionSchema.nullable().optional(),
    available_models: z.array(z.object({selection: zcodeModelSelectionSchema, label: z.string(), provider_label: z.string().optional(),
      reasoning_levels: z.array(z.string()), default_reasoning_level: z.string().nullable(), disabled: z.boolean()})).optional(),
  }).nullable(),
  quota: z.object({should_run: z.boolean(), reason: z.string().optional(), checked_at: z.string()}).nullable(),
  actions: z.array(z.enum(ZCODE_GOAL_ACTIONS)),
});

function zcodeGoalUrl(goalId: string, agentId: string) {
  return `/api/goals/${encodeURIComponent(goalId)}/agents/${encodeURIComponent(agentId)}/zcode-goal`;
}

function zcodeGoalReadback(payload: unknown, goalId: string, agentId: string): ZCodeGoalReadback {
  const result = zcodeGoalReadbackSchema.parse(payload);
  if (result.goal_id !== goalId || result.goal_ref.goal_id !== goalId || result.agent_id !== agentId) {
    throw new Error("ZCode Goal source changed; read the current Goal and Agent again.");
  }
  return result;
}

export async function fetchZCodeGoal(goalId: string, agentId: string, signal?: AbortSignal) {
  return zcodeGoalReadback(await requestJson<unknown>(zcodeGoalUrl(goalId, agentId), {signal}), goalId, agentId);
}

export async function updateZCodeGoal(goalId: string, agentId: string, action: Exclude<ZCodeGoalAction, "status">,
  expectedBinding: Pick<ZCodeGoalReadback, "goal_ref" | "goal_creation_operation_id">,
  options?: {cliPath?: string; modelSelection?: ZCodeModelSelection}, signal?: AbortSignal) {
  const result = zcodeGoalReadback(await requestJson<unknown>(zcodeGoalUrl(goalId, agentId), {
    method: "POST", signal,
    body: JSON.stringify({action, expected_binding: expectedBinding,
      ...(action === "bind" && options?.cliPath?.trim() ? {cli_path: options.cliPath.trim()} : {}),
      ...(action === "select_model" && options?.modelSelection ? {model_selection: options.modelSelection} : {}),
    }),
  }), goalId, agentId);
  if (result.goal_ref.goal_instance_id !== expectedBinding.goal_ref.goal_instance_id
    || result.goal_creation_operation_id !== expectedBinding.goal_creation_operation_id) {
    throw new Error("ZCode Goal source changed; read the current Goal and Agent again.");
  }
  return result;
}
