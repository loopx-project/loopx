import assert from "node:assert/strict";
import test from "node:test";
import {
  WORKER_ROSTER_STATES,
  deriveAttachedWorkerStatus,
  deriveCollaborationCardStatus,
  deriveCollaborationRosterSummary,
  deriveGoalWorkerRoster,
  formatProgressCounter,
  sanitizeFocusLine,
} from "./collaboration-status.ts";

function makeCollaborationRequest(overrides = {}) {
  return {
    schema_version: "collaboration_request_readback_v0",
    request_id: "req-1",
    agent_id: "worker-a",
    goal_id: "goal-ui",
    request_revision: 1,
    brief: {
      purpose: "Refine collaboration header layout",
      context: "Check desktop and narrow spacing",
      constraints: ["Keep zero horizontal overflow at 390px"],
      inputs: [],
      acceptance: ["Verified visual layout across viewports"],
      return_requirement: "Report updated contrast and responsive screenshots",
    },
    read_status: "pending",
    decision: "pending",
    returns: [],
    ...overrides,
  };
}

function makeControlView(overrides = {}) {
  return {
    schema_version: "collaboration_request_control_v0",
    request_id: "req-1",
    goal_ref: { goal_id: "goal-ui", goal_instance_id: "inst-1" },
    receiver_agent_id: "worker-a",
    revision: 2,
    control_version: 2,
    revision_digest: "deadbeef",
    brief: makeCollaborationRequest().brief,
    reason: "Initial delegation",
    read_at: "2026-11-21T10:00:00Z",
    read_readback: "recorded",
    assessment: { decision: "adopt", reason: "Accepted UX slice", assessed_at: "2026-11-21T10:01:00Z" },
    assessment_readback: "recorded",
    control_state: "open",
    cancellation: null,
    active_reservation: null,
    terminal_effects: [],
    execution_authority_granted: false,
    ...overrides,
  };
}

test("sanitizeFocusLine strips JSON payloads, ANSI codes, shell noise, and preserves clean surrogate-safe text", () => {
  const noisy = [
    '{"schema_version":"v0","ok":true,"turn_id":"t_123"}',
    "$ npm run build",
    "[stderr] Traceback (most recent call last):",
    "2026-11-21T10:05:00Z Now: \u001b[32mPASS\u001b[0m \u001b[1mAlign roster badges & truncate long CJK titles\u001b[0m {\"exit\":0}",
    "Second line ignored",
  ].join("\n");
  assert.equal(
    sanitizeFocusLine(noisy, 80),
    "PASS Align roster badges & truncate long CJK titles",
  );

  const emojiAndCjk = "🔥 迁移并核验多 Worker 协作状态看板与响应式卡片在 390px 窄屏下的换行表现避免截断";
  const truncated = sanitizeFocusLine(emojiAndCjk, 18);
  assert.ok(Array.from(truncated).length <= 18);
  assert.ok(truncated.endsWith("…"));
  assert.ok(!/[\uD800-\uDFFF]/.test(truncated.replace(/[\u{10000}-\u{10FFFF}]/gu, "")));

  assert.equal(sanitizeFocusLine('{"raw":"only"}'), "");
  assert.equal(sanitizeFocusLine("sha256:0123456789abcdef0123456789abcdef"), "");
});

test("formatProgressCounter keeps unknown measurements distinct instead of fabricating zero", () => {
  assert.deepEqual(formatProgressCounter(2, 5), { done: 2, total: 5, open: null, label: "[2/5]" });
  assert.deepEqual(formatProgressCounter(3, null, 2), { done: 3, total: 5, open: 2, label: "[3/5]" });
  assert.deepEqual(formatProgressCounter(3, null), { done: 3, total: null, open: null, label: "[3/—]" });
  assert.deepEqual(formatProgressCounter(null, null, 4, "en"), { done: null, total: null, open: 4, label: "[4 open]" });
  assert.deepEqual(formatProgressCounter(null, null, 4, "zh-CN"), { done: null, total: null, open: 4, label: "[4 待办]" });
  assert.deepEqual(formatProgressCounter(null, null, null), { done: null, total: null, open: null, label: "" });
});

test("request receipts preserve delivery and decision without asserting worker execution", () => {
  const waitingCard = deriveCollaborationCardStatus({
    request: makeCollaborationRequest(),
    locale: "en",
  });
  assert.equal(waitingCard.state, "waiting");
  assert.equal(waitingCard.scope, "goal-ui / worker-a");
  assert.equal(waitingCard.progress.label, "[0/3]");

  const queuedCard = deriveCollaborationCardStatus({
    request: makeCollaborationRequest({
      read_status: "supplied",
      decision: "adopt",
      decision_reason: "Starting implementation",
      returns: [{ phase: "conclusion", status: "queued", request_revision: 1 }],
    }),
    locale: "zh-CN",
  });
  assert.equal(queuedCard.state, "waiting");
  assert.equal(queuedCard.conclusionLabel, "回复等待送达");
  assert.equal(queuedCard.now, "回复等待送达");
  assert.equal(queuedCard.progress.label, "[2/3]");

  const adoptedBeforeReadSyncCard = deriveCollaborationCardStatus({
    request: makeCollaborationRequest({
      read_status: "pending",
      decision: "adopt",
      decision_reason: "Independent check underway",
    }),
    locale: "zh-CN",
  });
  assert.equal(adoptedBeforeReadSyncCard.state, "waiting");
  assert.equal(adoptedBeforeReadSyncCard.now, "已采纳，等待执行记录");

  const decisionCard = deriveCollaborationCardStatus({
    request: makeCollaborationRequest({
      read_status: "supplied",
      decision: "defer",
      decision_reason: "Waiting for schema owner confirmation",
    }),
    observed: makeControlView({
      assessment: { decision: "defer", reason: "Needs owner sign-off\n{\"internal\":1}", assessed_at: "2026-11-21T10:05:00Z" },
      control_state: "effect_pending",
    }),
    locale: "en",
  });
  assert.equal(decisionCard.state, "needs_decision");
  assert.equal(decisionCard.now, "Needs owner sign-off");

  const unverifiedCard = deriveCollaborationCardStatus({
    request: makeCollaborationRequest({
      read_status: "supplied",
      decision: "adopt",
      returns: [
        { phase: "conclusion", status: "verification_required", error: "initial_delivery_receipt_unavailable", request_revision: 1 },
      ],
    }),
    locale: "en",
  });
  assert.equal(unverifiedCard.state, "waiting");
  assert.equal(unverifiedCard.conclusionLabel, "Awaiting original handoff proof");
  assert.match(unverifiedCard.now, /delivery unverified/);

  const failedRead = deriveCollaborationCardStatus({
    request: makeCollaborationRequest({ read_status: "supplied", decision: "adopt" }),
    unavailable: true,
    locale: "en",
  });
  assert.equal(failedRead.state, "waiting");
  assert.equal(failedRead.readLabel, "Readback unavailable");
  assert.match(failedRead.now, /Check the original conversation/);

  const settledCard = deriveCollaborationCardStatus({
    request: makeCollaborationRequest({
      read_status: "supplied",
      decision: "adopt",
      decision_reason: "Completed roster polish",
      returns: [{ phase: "conclusion", status: "delivered", request_revision: 1 }],
    }),
    locale: "zh-CN",
  });
  assert.equal(settledCard.state, "waiting");
  assert.equal(settledCard.progress.label, "[3/3]");
});

test("deriveCollaborationRosterSummary rolls up multi-recipient roster counts and progress", () => {
  const summary = deriveCollaborationRosterSummary([
    makeCollaborationRequest({ request_id: "r1", agent_id: "builder", read_status: "supplied", decision: "adopt" }),
    makeCollaborationRequest({ request_id: "r2", agent_id: "reviewer", read_status: "supplied", decision: "adopt", returns: [{ phase: "conclusion", status: "delivered", request_revision: 1 }] }),
    makeCollaborationRequest({ request_id: "r3", agent_id: "qa", read_status: "supplied", decision: "reject", decision_reason: "Scope mismatch" }),
    makeCollaborationRequest({ request_id: "r4", agent_id: "ops", read_status: "unavailable", decision: "unavailable" }),
  ], "en");

  assert.equal(summary.total, 4);
  assert.equal(summary.concluded, 1);
  assert.equal(summary.progress.label, "[1/4]");
  assert.deepEqual(summary.counts, {
    working: 0,
    waiting: 3,
    needs_decision: 1,
    lease_expired: 0,
  });
});

test("deriveGoalWorkerRoster classifies populated multi-worker states from observed typed facts without fabricating liveness", () => {
  const nowMs = Date.parse("2026-11-21T12:00:00Z");
  const goal = {
    goalId: "goal-ui",
    agentId: "coordinator",
    agentLabel: "Coordinator",
    registeredAgentIds: ["coordinator", "builder", "verifier", "scout", "recoverer"],
    agentLanes: [
      { agentId: "builder", label: "Builder Agent with an Extraordinarily Long Multi-Word Roster Name", hostSurfaces: ["codex-app"] },
      { agentId: "verifier", label: "Verifier", hostSurfaces: ["loopx"] },
      { agentId: "scout", label: "Scout", hostSurfaces: ["codex-app"] },
      { agentId: "recoverer", label: "Recoverer", state: "lease_expired" },
      { agentId: "coordinator", label: "Coordinator", hostSurfaces: ["loopx"] },
    ],
    agentSentence: "Shipping workspace polish",
    nextSentence: "Verify narrow screen",
    activationState: "active",
    state: "安静运行",
    title: "Personal Workspace Roster UX",
    doneTodoCount: 3,
    openTodoCount: 4,
    agentTodos: [
      { todoId: "t1", done: true, claimedBy: "builder", taskDomain: "dashboard", text: "Extract pure collaboration status helper" },
      { todoId: "t2", done: false, claimedBy: "builder", taskDomain: "dashboard", text: "Render Roster rings in overview" },
      { todoId: "t3", done: false, claimedBy: "verifier", taskDomain: "qa", status: "blocked", blockedReason: "Confirm narrow 390px overflow policy", text: "Review PR head" },
      { todoId: "t4", done: false, claimedBy: "scout", taskDomain: "research", text: "Inspect legacy screenshot fixtures" },
      { todoId: "t5", done: true, claimedBy: "coordinator", taskDomain: "ops", text: "Wire worktree environment" },
      { todoId: "t6", done: false, claimedBy: "coordinator", taskDomain: "ops", status: "deferred", resumeWhen: "After verifier approves", text: "Prepare handoff note" },
    ],
    hostThreadActivity: {
      completeness: "complete",
      threads: [
        { agentId: "builder", hostSurface: "codex-app", state: "turn_open", lastEventAt: "2026-11-21T11:57:00Z", deliveryWindow: { state: "fresh" } },
        { agentId: "scout", hostSurface: "codex-app", state: "turn_open", lastEventAt: "2026-11-21T10:30:00Z", deliveryWindow: { state: "stale" } },
      ],
    },
    workerObservations: {
      read_status: "read",
      rows: [],
    },
  };

  const digest = {
    schema: "goal_workspace_digest_v0",
    observedAt: "2026-11-21T12:00:00Z",
    objective: "Polish Roster UX",
    lifecycle: { activation: "active", finishingTurn: false },
    attentionSummary: { blocking: 1, optional: 0, coverage: "complete" },
    work: {
      open: 4,
      done: 3,
      total: 7,
      coverage: "complete",
      listedOpen: 4,
      classes: { running_observed: 1, needs_you: 0, waiting_dependency: 0, open: 1, blocked: 1, deferred: 1, done: 3 },
      rows: [
        { todoId: "t2", ownerId: "builder", displayClass: "running_observed", prerequisiteIds: [] },
        { todoId: "t3", ownerId: "verifier", displayClass: "blocked", prerequisiteIds: [] },
        { todoId: "t4", ownerId: "scout", displayClass: "open", prerequisiteIds: [] },
        { todoId: "t6", ownerId: "coordinator", displayClass: "deferred", prerequisiteIds: [] },
      ],
      groups: [],
      rail: { needsYou: [], open: ["t2", "t4"], blocked: ["t3", "t6"], done: ["t1", "t5"] },
    },
    needsYou: { availability: "ok", blocking: 1, optional: 0, coverage: "complete" },
    vitals: { attention: { kind: "ok" }, execution: { kind: "idle" }, progress: { done: 3, total: 7 }, usage: { kind: "empty" } },
  };

  const items = [
    {
      id: "run-1",
      kind: "run",
      run: {
        runId: "run-1",
        agentId: "builder",
        agentLabel: "Builder",
        completedSteps: 2,
        totalSteps: 4,
        goalId: "goal-ui",
        goalTitle: "Personal Workspace Roster UX",
        latestActivity: "{\"event\":\"step\"}\nRefining CollaborationCard status pills",
        status: "running",
        title: "Build Roster Strip",
      },
    },
  ];

  const roster = deriveGoalWorkerRoster({
    goal,
    digest,
    items,
    locale: "en",
    nowMs,
  });

  assert.equal(roster.progress.label, "[3/7]");
  assert.deepEqual(
    roster.workers.map((w) => [w.agentId, w.state]),
    [
      ["verifier", "needs_decision"],
      ["builder", "working"],
      ["recoverer", "lease_expired"],
      ["coordinator", "waiting"],
      ["scout", "waiting"],
    ],
  );

  const builder = roster.workers.find((w) => w.agentId === "builder");
  assert.equal(builder?.scope, "dashboard");
  assert.equal(builder?.now, "Refining CollaborationCard status pills");
  assert.equal(builder?.progress.total, null);
  assert.equal(builder?.progress.label, "[≥1 done]");
  assert.ok((builder?.label.length ?? 0) <= 48);

  const scout = roster.workers.find((w) => w.agentId === "scout");
  assert.equal(scout?.state, "waiting");
  assert.match(scout?.now ?? "", /Host delivery window stale/);

  const verifier = roster.workers.find((w) => w.agentId === "verifier");
  assert.equal(verifier?.now, "Confirm narrow 390px overflow policy");

  assert.deepEqual(
    new Set(roster.workers.map((w) => w.state)),
    new Set(WORKER_ROSTER_STATES),
  );
});

test("worker task totals require both complete open rows and complete completed rows", () => {
  const agentTodos = [
    { todoId: "done", done: true, claimedBy: "worker", text: "Publish first version" },
    { todoId: "open", done: false, claimedBy: "worker", text: "Verify second version" },
  ];
  const goal = { goalId: "goal-ui", registeredAgentIds: ["worker"], agentTodos };
  const digest = { work: { rows: [], coverage: "partial", done: 10, open: 1, projectedOpen: 23, total: null } };
  const items = [{
    kind: "run",
    run: {
      goalId: "goal-ui", agentId: "worker", status: "running", completedSteps: 2, totalSteps: 4,
    },
  }];
  const partial = deriveGoalWorkerRoster({
    goal: { ...goal, doneTodoCount: 10, openTodoCount: 23 }, digest, items, locale: "en",
  });
  assert.equal(partial.progress.label, "[10/33]");
  assert.equal(partial.workers[0].state, "working");
  assert.deepEqual(partial.workers[0].progress, { done: 1, open: 1, total: null, label: "[≥1 done]" });

  const unknown = deriveGoalWorkerRoster({ goal, locale: "zh-CN" });
  assert.equal(unknown.progress.total, null);
  assert.equal(unknown.workers[0].progress.label, "[≥1 已完成]");

  const complete = deriveGoalWorkerRoster({
    goal: { ...goal, doneTodoCount: 1, openTodoCount: 1 }, locale: "en",
  });
  assert.equal(complete.workers[0].progress.label, "[1/2]");

  const duplicate = deriveGoalWorkerRoster({
    goal: { ...goal, agentTodos: [...agentTodos, agentTodos[1]], doneTodoCount: 1, openTodoCount: 2 },
    locale: "en",
  });
  assert.deepEqual(duplicate.workers[0].progress, { done: 1, open: 1, total: null, label: "[≥1 done]" });
});

test("deriveAttachedWorkerStatus reflects active turn, idle accepting, and disconnected states", () => {
  const live = deriveAttachedWorkerStatus({
    agentId: "builder",
    label: "Frontend Builder",
    connected: true,
    canReply: false,
    activeTurn: true,
    turnModel: { model: "gpt-5.4", reasoning_effort: "high" },
    locale: "en",
  });
  assert.equal(live.state, "working");
  assert.equal(live.scope, "codex-app / builder");
  assert.match(live.now, /gpt-5.4 · high/);

  const broken = deriveAttachedWorkerStatus({
    agentId: "builder",
    bindingRejected: true,
    activeTurn: false,
    locale: "zh-CN",
  });
  assert.equal(broken.state, "waiting");
  assert.equal(broken.now, "无法验证原会话绑定与访问权限");

  const quiet = deriveAttachedWorkerStatus({
    agentId: "builder",
    connected: true,
    canReply: true,
    activeTurn: false,
    includeFallbackNow: false,
    locale: "zh-CN",
  });
  assert.equal(quiet.state, "waiting");
  assert.equal(quiet.now, "");
});
