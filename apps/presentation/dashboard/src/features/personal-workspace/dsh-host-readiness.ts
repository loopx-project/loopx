import type { WorkspaceGoal } from "./personal-workspace-model";

export const DSH_NATIVE_HOST_SURFACE = "deepseek-harness-native";

export type DshManagedAdapter = {
  agent_id: string;
  available: boolean;
  unavailable_reason?: string | null;
  execution_profile?: string | null;
};

export type DshManagedReadiness =
  | {kind: "ready"; profile: string | null; reason: null}
  | {kind: "needs_runtime" | "needs_credential" | "needs_configuration" | "unknown";
      profile: string | null; reason: string | null}
  | {kind: "not_observed"; profile: null; reason: null};

export type DshInteractiveReadiness = {
  kind: "bound" | "not_bound";
  boundGoalCount: number;
  boundGoalIds: string[];
};

export function deriveDshManagedReadiness(
  adapters: readonly DshManagedAdapter[] | undefined,
): DshManagedReadiness {
  const adapter = adapters?.find((row) => row.agent_id === "dsh");
  if (!adapter) return {kind: "not_observed", profile: null, reason: null};

  const profile = adapter.execution_profile?.trim() || null;
  if (adapter.available) return {kind: "ready", profile, reason: null};

  const reason = adapter.unavailable_reason?.trim() || null;
  if (reason === "dsh_runtime_unavailable") return {kind: "needs_runtime", profile, reason};
  if (reason === "operator_credential_unconfigured") {
    return {kind: "needs_credential", profile, reason};
  }
  if (reason === "invalid_reasoning_effort"
    || reason === "invalid_output_token_limit"
    || reason === "unsupported_execution_host"
    || reason === "unsupported_operation_sandbox") {
    return {kind: "needs_configuration", profile, reason};
  }
  return {kind: "unknown", profile, reason};
}

export function deriveDshInteractiveReadiness(
  goals: readonly Pick<WorkspaceGoal, "goalId" | "boundHostSurfaces">[],
): DshInteractiveReadiness {
  const boundGoalIds = goals
    .filter((goal) => goal.boundHostSurfaces?.includes(DSH_NATIVE_HOST_SURFACE))
    .map((goal) => goal.goalId);
  return {
    kind: boundGoalIds.length ? "bound" : "not_bound",
    boundGoalCount: boundGoalIds.length,
    boundGoalIds,
  };
}
