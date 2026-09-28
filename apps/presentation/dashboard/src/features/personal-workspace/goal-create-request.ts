import type { WorkspaceTranslate } from "./i18n";
import type { WorkspaceActionPreviewRequest } from "./personal-workspace-model";

type GoalContent = { objective: string; completion: string; boundary: string };

export function goalCreateContent(input: GoalContent, t: WorkspaceTranslate) {
  const objective = input.objective.trim();
  const completion = input.completion.trim();
  const boundary = input.boundary.trim();
  return {
    title: objective.slice(0, 80),
    objective: [objective, t("goal.objectiveCompletion", { criteria: completion }),
      boundary ? t("goal.objectiveBoundary", { boundary }) : ""].filter(Boolean).join("\n"),
    completion_criteria: completion, execution_boundary: boundary,
    initial_todos: [t("goal.initialTodo", { criteria: completion })],
  };
}

/** One preview builder for explicit forms and conversational drafts. No effects. */
export function goalCreateRequest(input: GoalContent & {
  agentId: string; permission: string; contextGoalId: string | null; operationId?: string;
}, t: WorkspaceTranslate): WorkspaceActionPreviewRequest {
  const content = goalCreateContent(input, t);
  return {
    actionKind: "goal.create", context: { kind: "manager", goal_id: input.contextGoalId },
    idempotencyKey: `workspace-goal.create-${input.operationId ?? crypto.randomUUID()}`,
    summary: t("proposal.summary.goalCreate", { title: content.title }),
    normalizedParameters: {
      ...content, goal_id: `goal-${input.operationId ?? crypto.randomUUID()}`,
      agent_id: input.agentId, permission: input.permission,
      workspace_ref: "current", heartbeat: { enabled: false, cadence: "1d", timezone: "Asia/Shanghai" },
      stop_condition: "goal_complete",
    },
  };
}
