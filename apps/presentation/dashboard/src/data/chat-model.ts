export type LoopXModeSettings = { agent_id: string; token_budget: number };

export type ChatTodo = {
  todo_id: string | null;
  role: string | null;
  status: string;
  priority: string | null;
  text: string;
  action_kind: string | null;
  task_class: string | null;
  claimed_by: string | null;
  evidence: string | null;
};

export type ChatGoal = {
  goal_id: string;
  title: string;
  objective: string;
  status: string;
  waiting_on: string | null;
  severity: string | null;
  gate: string;
  next_action: string;
  top_todo: ChatTodo | null;
  todos: ChatTodo[];
  evidence: string[];
  quota: {
    state: string | null;
    spent_slots: number | null;
    allowed_slots: number | null;
    reason: string | null;
  };
};

export type ChatStatus = {
  ok: boolean;
  schema_version: "loopx_chat_status_v0";
  selected_goal_id: string | null;
  goal_count: number;
  goals: ChatGoal[];
};

export type ChatCapabilities = {
  ok: true;
  schema_version: "loopx_chat_capabilities_v0" | "loopx_chat_capabilities_v1";
  agent_backend: string;
  sandbox: string;
  approval_policy: string;
  todo_write: string;
  goal_id: string | null;
  manager?: {
    scope: "owner_global";
    model: string;
    reasoning_effort: string;
    runtime: {
      schema_version: "manager_runtime_effective_profile_v0";
      runtime_profile: "restricted" | "trusted_owner";
      source: string;
      configuration_revision: string;
      standing_grant: string;
      sandbox: string;
      approval_policy: string;
      tool_classes: string[];
      status: string;
      repair?: string;
    };
  };
  streaming?: boolean;
  resume?: boolean;
  interrupt?: boolean;
  adapters?: Array<{
    agent_id: string;
    display_name: string;
    adapter_kind: string;
    available: boolean;
    streaming: boolean;
    resume: boolean;
    interrupt: boolean;
  }>;
};

export type CollaborationReadback = {
  schema_version: "collaboration_request_readback_v0";
  request_id: string;
  agent_id: string;
  brief: {
    purpose: string;
    context: string;
    constraints: string[];
    inputs: { ref: string; description: string; sha256?: string }[];
    acceptance: string[];
    return_requirement: string;
  };
  read_status: string;
  decision: string;
  returns: { phase: string; status: string }[];
};

export type ChatRouteCandidate = {
  agentId: string;
  available: boolean;
};

export function selectAvailableChatAgent<T extends ChatRouteCandidate>(
  options: T[],
  preferredAgentId: string | undefined,
  defaultAgentId: string,
) {
  const selected = options.find((option) =>
    option.agentId === preferredAgentId && option.available
  ) ?? options.find((option) =>
    option.agentId === defaultAgentId && option.available
  ) ?? options.find((option) => option.available);
  if (!selected) {
    throw new Error("Chat requires at least one available route");
  }
  return selected;
}

export type TodoProposal = {
  kind: "todo";
  text: string;
  priority: "P0" | "P1" | "P2";
  rationale: string;
};

/**
 * An admitted steward team plan that rode in a Turn response.
 *
 * It is not a Todo and it is not a confirmation: the host validated the plan
 * before it surfaced it, and the surface that confirms it is the typed
 * `team.plan` action the manager channel stores as a card. Reading it here only
 * keeps the answer intact, so a manager Turn that carries a preview still
 * reaches the owner instead of failing the response schema.
 */
export type TeamPlanPreviewProposal = {
  kind: "steward_team_plan_preview";
  preview: Record<string, unknown>;
};

export type AgentProposal = TodoProposal | TeamPlanPreviewProposal;

export function isTodoProposal(proposal: AgentProposal): proposal is TodoProposal {
  return proposal.kind === "todo";
}

export function isTeamPlanPreviewProposal(
  proposal: AgentProposal,
): proposal is TeamPlanPreviewProposal {
  return proposal.kind === "steward_team_plan_preview";
}

export type AgentResponse = {
  schema_version: "loopx_chat_agent_response_v0";
  message: string;
  proposals: AgentProposal[];
  gate: {
    kind: string;
    summary: string;
    next_action: string;
  } | null;
};

export type GoalStudioNode = {
  detail: string;
  eyebrow: string;
  kind: "goal" | "todo" | "gate" | "evidence" | "next";
  title: string;
  tone: "violet" | "blue" | "amber" | "mint" | "slate";
};

export type TodoPreview = {
  preview_id: string;
  todo: {
    goal_id: string;
    text: string;
    todo_id?: string;
  };
};

export type TodoPreviewRequestIdentity = {
  goalId: string;
  text: string;
};

export type TodoWriteReceipt = {
  schema_version: "loopx_chat_todo_receipt_v0";
  receipt_id: string;
  preview_id: string;
  goal_id: string;
  todo_id: string;
  status: "applied";
  outcome: "todo_added" | "todo_already_exists";
  already_exists: boolean;
  preview_revision: string | null;
};

export type TodoNoWriteReceipt = {
  schema_version: "loopx_chat_todo_no_write_receipt_v0";
  receipt_id: string;
  goal_id: string;
  status: "not_applied";
  outcome: "preview_stale";
  write_attempted: false;
  current_preview_id: string;
  state_revision: string | null;
};

export type TodoApplyResult = {
  applied: true;
  ok: true;
  receipt: TodoWriteReceipt;
  todo: {
    text: string;
    todo_id: string;
  };
};

export type TodoApplyRequestIdentity = {
  goalId: string;
  previewId: string;
  text: string;
};

export type ProposalDecisionOutcome = "pending" | "approved" | "rejected" | "cancelled";

export type ProposalReviewState =
  | "needs_preview"
  | "ready_to_approve"
  | "approved"
  | "rejected"
  | "cancelled";

export type StewardPrompt = {
  id: "next" | "gate" | "evidence";
  label: string;
  prompt: string;
};

export function todoReceiptLabel(receipt: TodoWriteReceipt) {
  return `${receipt.todo_id} · 回执 ${receipt.receipt_id.slice(0, 12)}`;
}

export function todoReceiptOutcomeLabel(receipt: TodoWriteReceipt) {
  return receipt.already_exists ? "Todo 已存在，未重复写入" : "Todo 已写入";
}

export function todoPreviewMatchesRequest(
  preview: TodoPreview,
  request: TodoPreviewRequestIdentity,
) {
  return preview.preview_id.length > 0 && preview.todo.goal_id === request.goalId && preview.todo.text === request.text;
}

export function todoApplyResultMatchesRequest(
  result: TodoApplyResult,
  request: TodoApplyRequestIdentity,
) {
  return (
    result.receipt.goal_id === request.goalId &&
    result.receipt.preview_id === request.previewId &&
    result.receipt.todo_id === result.todo.todo_id &&
    result.todo.text === request.text
  );
}

export function todoReceiptProjected(status: ChatStatus | null, receipt: TodoWriteReceipt) {
  const goal = status?.goals.find((candidate) => candidate.goal_id === receipt.goal_id);
  return Boolean(goal?.todos.some((todo) => todo.todo_id === receipt.todo_id));
}

export function todoNoWriteReceiptLabel(receipt: TodoNoWriteReceipt) {
  return `未写入 · 回执 ${receipt.receipt_id.slice(0, 12)}`;
}

export function todoNoWriteReceiptFromPayload(payload: unknown): TodoNoWriteReceipt | null {
  if (!payload || typeof payload !== "object") {
    return null;
  }
  const candidate = (payload as { todo_receipt?: unknown }).todo_receipt;
  if (!candidate || typeof candidate !== "object") {
    return null;
  }
  const receipt = candidate as Partial<TodoNoWriteReceipt>;
  if (
    receipt.schema_version !== "loopx_chat_todo_no_write_receipt_v0" ||
    typeof receipt.receipt_id !== "string" ||
    !receipt.receipt_id ||
    typeof receipt.goal_id !== "string" ||
    !receipt.goal_id ||
    receipt.status !== "not_applied" ||
    receipt.outcome !== "preview_stale" ||
    receipt.write_attempted !== false ||
    typeof receipt.current_preview_id !== "string" ||
    !receipt.current_preview_id ||
    (receipt.state_revision !== null && typeof receipt.state_revision !== "string")
  ) {
    return null;
  }
  return receipt as TodoNoWriteReceipt;
}

export const stewardPrompts: StewardPrompt[] = [
  {
    id: "next",
    label: "找下一步",
    prompt: "结合当前 Goal，告诉我现在最值得推进的一个动作，并说明理由。",
  },
  {
    id: "gate",
    label: "看阻塞",
    prompt: "当前 Goal 有哪些 Gate 或阻塞？哪些需要我决定？",
  },
  {
    id: "evidence",
    label: "查证据",
    prompt: "检查当前 Goal 的 Evidence，告诉我哪些结论已经有依据，哪些还需要验证。",
  },
];

export function agentBackendLabel(agentBackend: string | null | undefined) {
  if (agentBackend === "multi_adapter") {
    return "Local Agent endpoints";
  }
  if (agentBackend === "codex_app_server") {
    return "Codex app-server";
  }
  if (!agentBackend) {
    return "Local Agent";
  }
  return agentBackend.replaceAll("_", " ");
}

function normalizedStatusQuestion(value: string) {
  return value
    .trim()
    .toLocaleLowerCase()
    .replace(/[\s?？!！。,，:：;；]/g, "");
}

export function answerLocalStatusQuestion(status: ChatStatus | null, message: string) {
  if (!status) {
    return null;
  }
  const question = normalizedStatusQuestion(message);
  const asksForGoalList = [
    /^(当前|现在)?(有|共有)?哪些(goals?|目标)$/,
    /^(当前|现在)?(goals?|目标)有哪些$/,
    /^列出(当前|全部|所有)?(goals?|目标)$/,
    /^(show|list)(current|all)?goals?$/,
  ].some((pattern) => pattern.test(question));
  if (!asksForGoalList) {
    return null;
  }
  if (!status.goals.length) {
    return "当前没有已连接的 Goal。";
  }
  const lines = status.goals.map((goal, index) => {
    const label = goal.title === goal.goal_id ? goal.goal_id : `${goal.title}（${goal.goal_id}）`;
    return `${index + 1}. ${label} · ${goal.status || "状态未知"}`;
  });
  return `当前有 ${status.goals.length} 个 Goal：\n${lines.join("\n")}`;
}

export function sessionInvalidatedByPayload(payload: unknown) {
  return Boolean(
    payload &&
      typeof payload === "object" &&
      (payload as Record<string, unknown>).session_invalidated === true,
  );
}

export function turnReplaySafeByPayload(payload: unknown) {
  return Boolean(
    sessionInvalidatedByPayload(payload) &&
      (payload as Record<string, unknown>).turn_replay_safe === true,
  );
}

export function chatFailureMessage(summary: string, sessionInvalidated: boolean) {
  const message = summary.trim() || "Agent 会话暂时不可用。";
  return sessionInvalidated ? `${message} 会话已自动重置，可以直接重试。` : message;
}

export function selectChatGoal(status: ChatStatus | null, preferredGoalId: string) {
  if (!status) {
    return null;
  }
  return (
    status.goals.find((goal) => goal.goal_id === preferredGoalId) ??
    status.goals.find((goal) => goal.goal_id === status.selected_goal_id) ??
    status.goals[0] ??
    null
  );
}

export function buildGoalStudioNodes(goal: ChatGoal): GoalStudioNode[] {
  return [
    {
      kind: "goal",
      eyebrow: "GOAL",
      title: goal.title,
      detail: goal.objective || "Keep the long-running objective visible.",
      tone: "violet",
    },
    {
      kind: "todo",
      eyebrow: "TOP TODO",
      title: goal.top_todo?.priority || "NEXT",
      detail: goal.top_todo?.text || "Ask the Agent for one bounded next step.",
      tone: "blue",
    },
    {
      kind: "gate",
      eyebrow: "GATE",
      title: goal.waiting_on ? `Waiting on ${goal.waiting_on}` : "Clear",
      detail: goal.gate || "No active gate.",
      tone: "amber",
    },
    {
      kind: "evidence",
      eyebrow: "EVIDENCE",
      title: goal.evidence.length ? `${goal.evidence.length} signal${goal.evidence.length > 1 ? "s" : ""}` : "No evidence yet",
      detail: goal.evidence[0] || "Validation evidence will appear here.",
      tone: "mint",
    },
    {
      kind: "next",
      eyebrow: "NEXT ACTION",
      title: "Operator path",
      detail: goal.next_action || "Review the next bounded proposal.",
      tone: "slate",
    },
  ];
}

export function pendingGoalReviews(goal: ChatGoal) {
  return goal.todos.filter(
    (todo) =>
      todo.role === "user" &&
      !["done", "completed", "closed", "deferred"].includes(todo.status) &&
      ["user_gate", "user_action"].includes(todo.task_class || "user_gate"),
  );
}

export function completedGoalReviews(goal: ChatGoal) {
  return goal.todos.filter(
    (todo) =>
      todo.role === "user" &&
      ["done", "completed", "closed"].includes(todo.status) &&
      ["user_gate", "user_action"].includes(todo.task_class || "user_gate"),
  );
}

export function proposalReviewState(
  proposal: TodoProposal,
  preview: TodoPreview | null,
  outcome: ProposalDecisionOutcome,
): ProposalReviewState {
  if (outcome !== "pending") {
    return outcome;
  }
  if (preview?.preview_id && preview.todo.text === proposal.text) {
    return "ready_to_approve";
  }
  return "needs_preview";
}
