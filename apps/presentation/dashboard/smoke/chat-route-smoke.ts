import {
  closeChatSession,
  fetchChatHistory,
  mergeChatSessionMessages,
  parseCompletedDecisionHistory,
  resumeChatTurnStreaming,
  serializeCompletedDecisionHistory,
  type ChatSessionSnapshot,
  type StoredDecisionHistoryItem,
} from "../src/data/chat.js";
import {
  agentBackendLabel,
  answerLocalStatusQuestion,
  buildGoalStudioNodes,
  chatFailureMessage,
  completedGoalReviews,
  pendingGoalReviews,
  proposalReviewState,
  sessionInvalidatedByPayload,
  selectChatGoal,
  stewardPrompts,
  turnReplaySafeByPayload,
  todoApplyResultMatchesRequest,
  todoPreviewMatchesRequest,
  todoNoWriteReceiptFromPayload,
  todoNoWriteReceiptLabel,
  todoReceiptLabel,
  todoReceiptOutcomeLabel,
  todoReceiptProjected,
  type ChatStatus,
  type TodoNoWriteReceipt,
  type TodoProposal,
  type TodoWriteReceipt,
} from "../src/data/chat-model.js";
import * as chatModel from "../src/data/chat-model.js";

function check(condition: boolean, message: string) {
  if (!condition) {
    throw new Error(message);
  }
}

type ChatRouteCandidate = {
  agentId: string;
  available: boolean;
};

const capabilityAwareRoute = chatModel as unknown as {
  selectChatAgent?: (
    options: ChatRouteCandidate[],
    preferredAgentId: string | undefined,
    defaultAgentId: string,
  ) => ChatRouteCandidate;
};

check(
  typeof capabilityAwareRoute.selectChatAgent === "function",
  "Chat routing should expose stale-selection fallback",
);

const routeOptions: ChatRouteCandidate[] = [
  { agentId: "codex", available: true },
  { agentId: "registered-fixer", available: false },
  { agentId: "status-only", available: true },
];
check(
  capabilityAwareRoute.selectChatAgent!(routeOptions, "registered-fixer", "codex").agentId === "codex",
  "a persisted unavailable Agent should fall back to Codex",
);
check(
  capabilityAwareRoute.selectChatAgent!(
    routeOptions.map((option) => option.agentId === "codex" ? { ...option, available: false } : option),
    "registered-fixer",
    "status-only",
  ).agentId === "status-only",
  "Chat should fall back to status-only when no model adapter is available",
);
const unavailableProjectOptions = routeOptions.filter(option => option.agentId !== "status-only")
  .map(option => ({ ...option, available: false }));
const diagnosticRoute = capabilityAwareRoute.selectChatAgent!(unavailableProjectOptions, "registered-fixer", "codex");
check(diagnosticRoute.agentId === "registered-fixer" && diagnosticRoute.available === false,
  "An unavailable project keeps the preferred identity and its refusal, rather than crashing or granting execution");
const missingPreferred = capabilityAwareRoute.selectChatAgent!(unavailableProjectOptions, "status-only", "status-only");
check(missingPreferred.agentId === "codex" && missingPreferred.available === false,
  "A project without status projection keeps an actual unavailable executor, never a fabricated readonly route");

const fixture: ChatStatus = {
  ok: true,
  schema_version: "loopx_chat_status_v0",
  selected_goal_id: "goal-studio",
  goal_count: 1,
  goals: [
    {
      goal_id: "goal-studio",
      title: "LoopX Chat",
      objective: "Make the next decision visible and reviewable.",
      status: "active",
      waiting_on: "codex",
      severity: "action",
      gate: "review_required",
      next_action: "Review the proposed Todo.",
      top_todo: {
        todo_id: "todo-1",
        role: "agent",
        status: "open",
        priority: "P0",
        text: "Implement the Goal Studio review card.",
        action_kind: "loopx_chat_proposal",
        task_class: "advancement_task",
        claimed_by: null,
        evidence: null,
      },
      todos: [
        {
          todo_id: "gate-1",
          role: "user",
          status: "open",
          priority: null,
          text: "Approve the first-screen presentation.",
          action_kind: null,
          task_class: "user_gate",
          claimed_by: null,
          evidence: null,
        },
        {
          todo_id: "action-1",
          role: "user",
          status: "done",
          priority: null,
          text: "Review the original Goal Studio direction.",
          action_kind: null,
          task_class: "user_action",
          claimed_by: null,
          evidence: "Owner approved the direction.",
        },
        {
          todo_id: "deferred-1",
          role: "user",
          status: "deferred",
          priority: null,
          text: "Review a later visual refinement.",
          action_kind: null,
          task_class: "user_action",
          claimed_by: null,
          evidence: null,
        },
      ],
      evidence: ["Contract smoke passed."],
      quota: {
        state: "eligible",
        spent_slots: 2,
        allowed_slots: 24,
        reason: "eligible for the next bounded turn",
      },
    },
  ],
};

const selected = selectChatGoal(fixture, "goal-studio");
check(selected?.title === "LoopX Chat", "selected Goal should remain stable");

const nodes = buildGoalStudioNodes(selected!);
check(
  nodes.map((node) => node.kind).join(",") === "goal,todo,gate,evidence,next",
  "Goal Studio should preserve the five-node decision path",
);
check(nodes[1]?.detail === "Implement the Goal Studio review card.", "top Todo should feed the map");
check(nodes[3]?.detail === "Contract smoke passed.", "evidence should feed the map");
check(
  stewardPrompts.map((prompt) => prompt.id).join(",") === "next,gate,evidence",
  "问问管家 should expose bounded next-step, gate, and evidence prompts",
);
check(
  stewardPrompts.every((prompt) => prompt.prompt.trim().length > prompt.label.length),
  "问问管家 shortcuts should expand into concrete questions",
);
check(agentBackendLabel("codex_app_server") === "Codex app-server", "known Agent backend should be human-readable");
check(agentBackendLabel("multi_adapter") === "Local Agent endpoints", "multi-adapter backend should be human-readable");
check(agentBackendLabel(null) === "Local Agent", "missing capabilities should retain a safe local fallback");
check(
  pendingGoalReviews(selected!).map((todo) => todo.todo_id).join(",") === "gate-1",
  "Decision Inbox should include durable open user gates",
);
check(
  completedGoalReviews(selected!).map((todo) => todo.todo_id).join(",") === "action-1",
  "Decision Inbox history should include completed reviews without deferred work",
);

const proposal: TodoProposal = {
  kind: "todo",
  text: "Implement the Goal Studio review card.",
  priority: "P0",
  rationale: "It closes the first approval loop.",
};
check(proposalReviewState(proposal, null, "pending") === "needs_preview", "new proposal needs preview");
check(
  proposalReviewState(
    { ...proposal },
    { preview_id: "preview-1", todo: { goal_id: "goal-studio", text: proposal.text } },
    "pending",
  ) === "ready_to_approve",
  "matching preview should unlock approval",
);
check(
  proposalReviewState(
    proposal,
    { preview_id: "preview-1", todo: { goal_id: "goal-studio", text: "stale" } },
    "pending",
  ) === "needs_preview",
  "changed proposal should require a new preview",
);
check(
  proposalReviewState(
    proposal,
    { preview_id: "preview-1", todo: { goal_id: "goal-studio", text: proposal.text } },
    "approved",
  ) === "approved",
  "applied proposal should become approved",
);
check(
  proposalReviewState(proposal, null, "rejected") === "rejected",
  "rejected proposal should resolve without unlocking a write",
);
check(
  proposalReviewState(proposal, null, "cancelled") === "cancelled",
  "cancelled preview should resolve without unlocking a write",
);

const secondGoal = {
  ...fixture.goals[0],
  goal_id: "loopx-anthropic-ceo-research",
  title: "Anthropic CEO Research",
  objective: "Track a bounded research objective.",
};
const multiGoalFixture: ChatStatus = {
  ...fixture,
  goal_count: 2,
  goals: [fixture.goals[0], secondGoal],
};
const directStatusAnswer = answerLocalStatusQuestion(multiGoalFixture, "当前有哪些goal");
check(directStatusAnswer?.startsWith("当前有 2 个 Goal：") === true, "Goal list question should use local status");
check(directStatusAnswer?.includes("goal-studio") === true, "local answer should include the first Goal id");
check(
  directStatusAnswer?.includes("loopx-anthropic-ceo-research") === true,
  "local answer should include every connected Goal id",
);
const idTitleAnswer = answerLocalStatusQuestion(
  { ...fixture, goals: [{ ...fixture.goals[0], title: "goal-studio" }] },
  "当前有哪些 Goal？",
);
check(
  idTitleAnswer?.includes("goal-studio（goal-studio）") === false,
  "Goal id fallback titles should not be duplicated in the answer",
);
check(
  answerLocalStatusQuestion(multiGoalFixture, "结合当前 Goal，下一步最值得做什么？") === null,
  "reasoning questions should remain on the Agent path",
);
check(
  sessionInvalidatedByPayload({ session_invalidated: true }) === true,
  "server invalidation should clear the browser session",
);
check(
  sessionInvalidatedByPayload({ session_invalidated: false }) === false,
  "ordinary errors should preserve a healthy session",
);
check(
  turnReplaySafeByPayload({ session_invalidated: true, turn_replay_safe: true }) === true,
  "a missing server-side session should allow one automatic turn replay",
);
check(
  turnReplaySafeByPayload({ session_invalidated: true, turn_replay_safe: false }) === false,
  "an Agent failure must not opt into automatic turn replay",
);
check(
  turnReplaySafeByPayload({ turn_replay_safe: true }) === false,
  "turn replay requires explicit session invalidation",
);
check(
  chatFailureMessage("Codex app-server timed out.", true).includes("会话已自动重置，可以直接重试。"),
  "invalidated sessions should explain that a direct retry is safe",
);
check(
  chatFailureMessage("Temporary agent error.", false) === "Temporary agent error.",
  "ordinary failures should keep their original summary",
);

const receipt: TodoWriteReceipt = {
  schema_version: "loopx_chat_todo_receipt_v0",
  receipt_id: "receipt-1234567890abcdef",
  preview_id: "preview-1",
  goal_id: "goal-studio",
  todo_id: "todo-1",
  status: "applied",
  outcome: "todo_added",
  already_exists: false,
  preview_revision: "sha256:preview",
};
const preview = {
  preview_id: receipt.preview_id,
  todo: {
    goal_id: receipt.goal_id,
    text: proposal.text,
  },
};
check(
  todoPreviewMatchesRequest(preview, { goalId: receipt.goal_id, text: proposal.text }) === true,
  "Todo previews should match the requested Goal and Todo text before approval unlocks",
);
check(
  todoPreviewMatchesRequest(preview, { goalId: "other-goal", text: proposal.text }) === false,
  "a preview from another Goal must not unlock approval",
);
check(
  todoPreviewMatchesRequest(preview, { goalId: receipt.goal_id, text: "A different Todo" }) === false,
  "a preview for different Todo text must not unlock approval",
);
const applyResult = {
  applied: true as const,
  ok: true as const,
  receipt,
  todo: {
    text: proposal.text,
    todo_id: receipt.todo_id,
  },
};
const applyRequest = {
  goalId: receipt.goal_id,
  previewId: receipt.preview_id,
  text: proposal.text,
};
check(
  todoApplyResultMatchesRequest(applyResult, applyRequest) === true,
  "approved Todo receipts should match the requested Goal, preview, Todo id, and text",
);
check(
  todoApplyResultMatchesRequest(applyResult, { ...applyRequest, goalId: "other-goal" }) === false,
  "a receipt from another Goal must not complete the approval",
);
check(
  todoApplyResultMatchesRequest(applyResult, { ...applyRequest, previewId: "other-preview" }) === false,
  "a receipt from another preview must not complete the approval",
);
check(
  todoApplyResultMatchesRequest(
    { ...applyResult, todo: { ...applyResult.todo, todo_id: "other-todo" } },
    applyRequest,
  ) === false,
  "receipt and response Todo ids must agree before approval completes",
);
check(
  todoApplyResultMatchesRequest(
    { ...applyResult, todo: { ...applyResult.todo, text: "A different Todo" } },
    applyRequest,
  ) === false,
  "a response for different Todo text must not complete the approval",
);
check(
  todoReceiptLabel(receipt) === "todo-1 · 回执 receipt-1234",
  "approved Todo should expose a compact stable receipt label",
);
check(todoReceiptOutcomeLabel(receipt) === "Todo 已写入", "new Todo receipts should name the write outcome");
check(
  todoReceiptOutcomeLabel({
    ...receipt,
    outcome: "todo_already_exists",
    already_exists: true,
  }) === "Todo 已存在，未重复写入",
  "idempotent receipt retries should say that no duplicate write occurred",
);
check(
  todoReceiptProjected(fixture, receipt) === false,
  "receipt confirmation should require the Todo in the refreshed Goal projection",
);
const projectedFixture: ChatStatus = {
  ...fixture,
  goals: [
    {
      ...fixture.goals[0],
      todos: [
        ...fixture.goals[0].todos,
        {
          todo_id: receipt.todo_id,
          role: "agent",
          status: "open",
          priority: "P0",
          text: proposal.text,
          action_kind: "loopx_chat_proposal",
          task_class: "advancement_task",
          claimed_by: null,
          evidence: null,
        },
      ],
    },
  ],
};
check(
  todoReceiptProjected(projectedFixture, receipt) === true,
  "receipt confirmation should bind the stable Todo id to the refreshed Goal projection",
);

const noWriteReceipt: TodoNoWriteReceipt = {
  schema_version: "loopx_chat_todo_no_write_receipt_v0",
  receipt_id: "no-write-1234567890abcdef",
  goal_id: "goal-studio",
  status: "not_applied",
  outcome: "preview_stale",
  write_attempted: false,
  current_preview_id: "preview-current",
  state_revision: "sha256:current",
};
check(
  todoNoWriteReceiptFromPayload({ todo_receipt: noWriteReceipt })?.receipt_id === noWriteReceipt.receipt_id,
  "stale preview errors should expose a typed no-write receipt",
);
check(
  todoNoWriteReceiptLabel(noWriteReceipt) === "未写入 · 回执 no-write-123",
  "no-write receipts should expose a compact stable label",
);
check(
  todoNoWriteReceiptFromPayload({ todo_receipt: { ...noWriteReceipt, write_attempted: true } }) === null,
  "unproven write failures must not be presented as no-write receipts",
);

const completedHistory: StoredDecisionHistoryItem[] = [
  {
    id: "decision-approved",
    outcome: "approved",
    projectionVerified: true,
    proposal,
    receipt,
  },
  {
    id: "decision-rejected",
    outcome: "rejected",
    projectionVerified: null,
    proposal: { ...proposal, text: "Reject this proposal." },
    receipt: null,
  },
];
const serializedHistory = serializeCompletedDecisionHistory("goal-studio", completedHistory);
check(
  parseCompletedDecisionHistory(serializedHistory, "goal-studio").length === 2,
  "completed Decision Inbox history should survive a same-tab reload",
);
check(
  parseCompletedDecisionHistory(serializedHistory, "other-goal").length === 0,
  "completed decision history must remain scoped to its Goal",
);
check(
  parseCompletedDecisionHistory("{malformed", "goal-studio").length === 0,
  "malformed browser history must fail closed",
);
check(
  parseCompletedDecisionHistory(
    JSON.stringify({
      schema_version: "loopx_chat_decision_history_v0",
      goal_id: "goal-studio",
      decisions: [{ ...completedHistory[0], receipt: null }],
    }),
    "goal-studio",
  ).length === 0,
  "approved browser history without a receipt must fail closed",
);

async function checkSessionCloseContract() {
  const originalFetch = globalThis.fetch;
  let observedUrl = "";
  let observedInit: RequestInit | undefined;
  globalThis.fetch = (async (input, init) => {
    observedUrl = String(input);
    observedInit = init;
    return new Response(
      JSON.stringify({
        closed: true,
        ok: true,
        session_id: "session-goal-studio",
      }),
      { headers: { "Content-Type": "application/json" }, status: 200 },
    );
  }) as typeof fetch;
  try {
    const result = await closeChatSession("session-goal-studio");
    check(result.closed === true, "session close should return a typed close receipt");
    check(
      observedUrl === "/api/chat/sessions/session-goal-studio",
      "session close should target the exact browser session",
    );
    check(observedInit?.method === "DELETE", "session close should use DELETE");
    check(observedInit?.keepalive === true, "page teardown should keep the close request alive");
  } finally {
    globalThis.fetch = originalFetch;
  }
}

function historySnapshot(
  sessionId: string,
  status: string,
  messages: ChatSessionSnapshot["messages"],
): ChatSessionSnapshot {
  return {
    ok: true,
    schema_version: "loopx_chat_store_v1",
    session: {
      session_id: sessionId,
      goal_id: "goal-studio",
      agent_id: "codex",
      adapter_kind: "codex_app_server",
      channel_id: "manager",
      status,
      active_turn_id: null,
      last_error_code: status === "resume_failed" ? "resume_failed" : null,
      created_at: "2026-08-10T01:00:00Z",
      updated_at: "2026-08-10T02:00:00Z",
      last_activity_at: "2026-08-10T02:00:00Z",
      resumable: status === "ready",
    },
    messages,
    active_turn: null,
  };
}

async function checkSessionHistoryRecoveryContract() {
  const originalFetch = globalThis.fetch;
  const requests: string[] = [];
  const failed = historySnapshot("session-failed", "resume_failed", [{
    message_id: "message-old",
    turn_id: "turn-old",
    role: "user",
    text: "重启前的问题",
    created_at: "2026-08-10T01:00:00Z",
  }]);
  const ready = historySnapshot("session-ready", "ready", [{
    message_id: "message-new",
    turn_id: "turn-new",
    role: "agent",
    text: "重启前的回答",
    created_at: "2026-08-10T01:01:00Z",
  }]);
  globalThis.fetch = (async (input) => {
    const url = String(input);
    requests.push(url);
    const body = url.includes("?")
      ? {
          ok: true,
          schema_version: "loopx_chat_session_list_v1",
          sessions: [failed.session, ready.session],
        }
      : url.endsWith("session-failed")
        ? failed
        : ready;
    return new Response(JSON.stringify(body), {
      headers: { "Content-Type": "application/json" },
      status: 200,
    });
  }) as typeof fetch;
  try {
    const history = await fetchChatHistory({ agentId: "codex", channelId: "manager" });
    check(
      requests[0] === "/api/chat/sessions?agent_id=codex&channel_id=manager",
      "manager history should be loaded without requiring a selected Goal",
    );
    check(
      history.messages.map((message) => message.text).join("|") === "重启前的问题|重启前的回答",
      "local visible messages should survive an upstream resume failure",
    );
    check(
      mergeChatSessionMessages([ready, failed]).map((message) => message.message_id).join(",")
        === "message-old,message-new",
      "messages from multiple local sessions should retain chronological order",
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
}

async function checkActiveTurnStreamRecoveryContract() {
  const originalFetch = globalThis.fetch;
  const requests: string[] = [];
  let attempt = 0;
  const event = (id: string, kind: string, payload: Record<string, unknown>) =>
    `id: ${id}\nevent: ${kind}\ndata: ${JSON.stringify({
      event_id: id,
      sequence: Number(id),
      kind,
      created_at: "2026-08-10T02:00:00Z",
      payload,
    })}\n\n`;
  globalThis.fetch = (async (input) => {
    const url = String(input);
    requests.push(url);
    attempt += 1;
    const body = attempt === 1
      ? event("1", "answer.delta", { text: "恢复中的回答。" })
      : event("2", "turn.completed", {
          response: {
            schema_version: "loopx_chat_agent_response_v0",
            message: "恢复中的回答。",
            proposals: [],
            gate: null,
          },
        });
    return new Response(body, {
      headers: { "Content-Type": "text/event-stream" },
      status: 200,
    });
  }) as typeof fetch;
  const deltas: string[] = [];
  try {
    const result = await resumeChatTurnStreaming("session-active", "turn-active", {
      onDelta: (text) => deltas.push(text),
    });
    check(result.response.message === "恢复中的回答。", "active Turn recovery should return the final response");
    check(deltas.join("") === "恢复中的回答。", "active Turn recovery should replay visible deltas once");
    check(requests.length === 2, "a non-terminal SSE close should reconnect");
    check(
      requests[0]?.endsWith("/api/chat/sessions/session-active/turns/turn-active/events") === true,
      "active Turn recovery should use the persisted Session and Turn ids",
    );
    check(requests[1]?.endsWith("/events?after=1") === true, "SSE reconnect should continue after the last event id");

    globalThis.fetch = (async () => new Response("", {
      headers: { "Content-Type": "text/event-stream" },
      status: 200,
    })) as typeof fetch;
    let reconnectError: unknown = null;
    try {
      await resumeChatTurnStreaming("session-active", "turn-active");
    } catch (error) {
      reconnectError = error;
    }
    check(reconnectError instanceof Error, "exhausted SSE reconnects should surface an error");
    check(
      reconnectError instanceof Error
        && "payload" in reconnectError
        && (reconnectError as { payload: Record<string, unknown> }).payload.reconnectable === true,
      "exhausted SSE reconnects should preserve enough state for a Continue connection action",
    );

    globalThis.fetch = (async () => new Response(
      event("3", "turn.interrupted", {}),
      { headers: { "Content-Type": "text/event-stream" }, status: 200 },
    )) as typeof fetch;
    let interruptedError: unknown = null;
    try {
      await resumeChatTurnStreaming("session-active", "turn-active");
    } catch (error) {
      interruptedError = error;
    }
    check(
      interruptedError instanceof Error
        && "payload" in interruptedError
        && (interruptedError as { payload: Record<string, unknown> }).payload.error_code === "turn_interrupted",
      "an interrupted recovered Turn should surface an explicit interrupted result",
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
}

void checkSessionCloseContract()
  .then(checkSessionHistoryRecoveryContract)
  .then(checkActiveTurnStreamRecoveryContract)
  .then(() => console.log("chat-route-smoke: ok"))
  .catch((error) => {
    console.error(error);
    throw error;
  });
