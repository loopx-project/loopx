import type { WorkspaceTranslate } from "./i18n";
import type { WorkspaceActionPreviewRequest, WorkspaceAgentOption } from "./personal-workspace-model";
import {compactPythonWhitespace, hasPythonNonWhitespaceText} from "../../../../../../loopx/control_plane/coordination/python_whitespace.ts";

type GoalContent = { objective: string; completion: string; boundary: string };

export function goalDraftReady(draft: {question: string; objective: string; completion_criteria: string}): boolean {
  return !draft.question && hasPythonNonWhitespaceText(draft.objective) && hasPythonNonWhitespaceText(draft.completion_criteria);
}

/** A read-only conversation projection cannot be the first Goal's executor. */
export function goalCreationAgentId(selectedId: string, agents: readonly WorkspaceAgentOption[]): string {
  const selected = agents.find((agent) => agent.agentId === selectedId);
  if (selected?.adapterKind !== "status_projection") return selectedId;
  const executors = agents.filter((agent) => agent.adapterKind !== "status_projection");
  return executors.find((agent) => agent.available)?.agentId ?? executors[0]?.agentId ?? selectedId;
}

export function goalCreateContent(input: GoalContent, t: WorkspaceTranslate) {
  const objective = compactPythonWhitespace(input.objective);
  const completion = compactPythonWhitespace(input.completion);
  const boundary = compactPythonWhitespace(input.boundary);
  return {
    title: Array.from(objective).slice(0, 80).join(""),
    objective: compactPythonWhitespace([objective, t("goal.objectiveCompletion", { criteria: completion }),
      boundary ? t("goal.objectiveBoundary", { boundary }) : ""].filter(Boolean).join("\n")),
    completion_criteria: completion, execution_boundary: boundary,
    initial_todos: [compactPythonWhitespace(t("goal.initialTodo", { criteria: completion }))],
  };
}

/** One preview builder for explicit forms and conversational drafts. No effects. */
export function goalCreateRequest(input: GoalContent & {
  workspaceRef?: string; agentId: string; permission: string; contextGoalId: string | null; operationId?: string;
}, t: WorkspaceTranslate): WorkspaceActionPreviewRequest {
  const content = goalCreateContent(input, t);
  return {
    actionKind: "goal.create", context: { kind: "manager", goal_id: input.contextGoalId },
    idempotencyKey: `workspace-goal.create-${input.operationId ?? crypto.randomUUID()}`,
    summary: t("proposal.summary.goalCreate", { title: content.title }),
    normalizedParameters: {
      ...content, goal_id: `goal-${input.operationId ?? crypto.randomUUID()}`,
      agent_id: input.agentId, permission: input.permission,
      workspace_ref: input.workspaceRef ?? "current", heartbeat: { enabled: false, cadence: "1d", timezone: "Asia/Shanghai" },
      stop_condition: "goal_complete",
    },
  };
}
