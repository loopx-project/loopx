/** ZCode provider observations and operations; LoopX Goal/quota authority stays in Core. */
import providerContract from "./contract.json" with {type: "json"};
export type ZCodeGoalAction = keyof typeof providerContract.actions;
export const ZCODE_GOAL_ACTIONS = Object.freeze(
  Object.keys(providerContract.actions) as [ZCodeGoalAction, ...ZCodeGoalAction[]],
);
export type GoalRef = {goal_id: string; goal_instance_id?: string};
export type ZCodeModelSelection = {providerId: string; modelId: string; options?: {reasoningLevel: string}};
export type ZCodeModelOption = {selection: ZCodeModelSelection; label: string; provider_label?: string;
  reasoning_levels: string[]; default_reasoning_level: string | null; disabled: boolean};
export type QuotaObservation = {should_run: boolean; reason?: string; checked_at: string};
export const ZCODE_NATIVE_GOAL_STATUSES = ["active", "paused", "completed", "budget_limited"] as const;
export type NativeObservation = {
  session_id: string; target_id: string | null;
  status: typeof ZCODE_NATIVE_GOAL_STATUSES[number] | null; running: boolean;
  raw_status?: string | null; session_status?: string; usage?: null;
  objective_sha256?: string | null; selected_model?: ZCodeModelSelection | null; available_models?: ZCodeModelOption[];
};
export const ZCODE_IDENTITY_SCOPES = ["exact_goal_instance", "legacy_goal_alias"] as const;
export type ZCodeGoalReadback = {
  ok: boolean; available: boolean; reason?: string;
  goal_id: string; goal_ref: GoalRef; agent_id: string;
  goal_creation_operation_id: string | null;
  identity_scope?: typeof ZCODE_IDENTITY_SCOPES[number];
  binding: {mode: "managed_cli"; connected: boolean; cli_path: string; protocol: string} | null;
  native: NativeObservation | null; quota: QuotaObservation | null;
  actions: ZCodeGoalAction[];
};
export type NativeRequest = {
  action: ZCodeGoalAction; project: string; registry: string;
  goal_id: string; goal_ref: GoalRef; agent_id: string;
  identity_scope?: typeof ZCODE_IDENTITY_SCOPES[number]; state_path: string;
  cli_command?: string[]; cli_path?: string; loopx_command: string[]; task_body?: string;
  validation_command: string[];
  goal_creation_operation_id?: string | null; model_selection?: ZCodeModelSelection;
};
export function sameBinding(a: NativeRequest, b: NativeRequest): boolean {
  return a.project === b.project && a.registry === b.registry && a.agent_id === b.agent_id
    && a.goal_ref.goal_id === b.goal_ref.goal_id
    && a.goal_ref.goal_instance_id === b.goal_ref.goal_instance_id
    && a.goal_creation_operation_id === b.goal_creation_operation_id;
}

/** Only these deliberately public provider failures cross the transport boundary. */
export class ZCodeGoalError extends Error {}
export function safeFailure(error: unknown): string {
  return error instanceof ZCodeGoalError ? error.message : "zcode_operation_failed";
}
