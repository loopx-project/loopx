import assert from "node:assert/strict";
import test from "node:test";
import {compileActionReviewPlan} from "../../loopx/control_plane/presentation/action_review_plan.ts";

const proposal = {proposal_id: "new-goal", expected_state_fingerprint: "reviewed-source",
  action_kind: "goal.create", permission_classification: "durable_write", status: "preview_ready",
  normalized_parameters: {goal_id: "new-goal", title: "New Goal", agent_id: "codex",
    initial_todos: ["Deliver the original requested work"]}};
const steps = {workspace_validated: {outcome: "workspace_validated", workspace_digest: "a".repeat(64)},
  goal_bootstrapped: {outcome: "goal_bootstrapped", goal_id: "new-goal"},
  agent_bound: {outcome: "agent_bound", goal_id: "new-goal", agent_id: "codex"},
  todos_created: {outcome: "todos_created", todo_ids: ["todo-original"]}};

test("a new default Codex Goal chooses managed Agent routing before its first Session", () => {
  for (const checkpoint of [undefined, null, {steps: {}}, {steps}]) {
    const plan = compileActionReviewPlan({...proposal, checkpoint});
    assert.deepEqual(plan.creationSession, {managedAgentId: "codex"});
    assert.equal(plan.canApply, true);
    assert.equal(plan.creationProgress?.sessionId, undefined);
    assert.equal(plan.creationProgress?.firstTurn, undefined);
  }
  assert.deepEqual(compileActionReviewPlan({...proposal, normalized_parameters: {
    ...proposal.normalized_parameters, native_token_budget: null}}).creationSession, {managedAgentId: "codex"});
});

test("explicit budgets and other endpoints preserve the legacy first Session route", () => {
  // Invalid explicit budget shapes are rejected upstream; a pure projection
  // must not turn them into a managed default through truthiness.
  for (const native_token_budget of [1, 10000, 0, false, "", "10000", {}, []]) {
    assert.equal(Object.hasOwn(compileActionReviewPlan({...proposal, normalized_parameters: {
      ...proposal.normalized_parameters, native_token_budget}}), "creationSession"), false);
  }
  for (const agent_id of ["antigravity", "claude-code", "other-codex", "codex@worker", " codex ", "", null, undefined]) {
    assert.equal(Object.hasOwn(compileActionReviewPlan({...proposal, normalized_parameters: {
      ...proposal.normalized_parameters, agent_id}}), "creationSession"), false);
  }
  assert.equal(compileActionReviewPlan({...proposal, action_kind: "todo.create"}).creationSession, undefined);
});

test("bootstrap retry chooses a new route only while no Session has been recorded", () => {
  for (const status of ["failed", "applying"]) {
    const plan = compileActionReviewPlan({...proposal, status, checkpoint: {steps}});
    assert.equal(plan.retryOriginal, true);
    assert.deepEqual(plan.creationSession, {managedAgentId: "codex"});
    assert.deepEqual(plan.creationProgress?.todoIds, ["todo-original"]);
  }
});

test("recorded legacy Session and first Turn retain their original recovery identity", () => {
  const first_session_opened = {outcome: "first_session_opened", session_id: "original-session"};
  const first_turn_started = {outcome: "first_turn_started", session_id: "original-session", turn_id: "original-turn"};
  for (const recorded of [{first_session_opened}, {first_turn_started}, {first_session_opened, first_turn_started}]) {
    const plan = compileActionReviewPlan({...proposal, status: "failed", checkpoint: {steps: {...steps, ...recorded}}});
    assert.equal(plan.retryOriginal, true);
    assert.equal(Object.hasOwn(plan, "creationSession"), false);
    if ("first_session_opened" in recorded) assert.equal(plan.creationProgress?.sessionId, "original-session");
    if ("first_turn_started" in recorded)
      assert.deepEqual(plan.creationProgress?.firstTurn, {sessionId: "original-session", turnId: "original-turn"});
  }
});

test("malformed recorded checkpoints cannot be treated as a new default Session", () => {
  for (const value of [undefined, null, false, [], {}, {session_id: "original-session"}]) {
    for (const field of ["first_session_opened", "first_turn_started"]) {
      const plan = compileActionReviewPlan({...proposal, status: "failed", checkpoint: {steps: {...steps, [field]: value}}});
      assert.equal(Object.hasOwn(plan, "creationSession"), false);
    }
  }
  for (const checkpoint of [[], false, {}, {steps: null}, {steps: []},
    {steps: {...steps, goal_bootstrapped: {...steps.goal_bootstrapped, goal_id: "other"}}},
    {steps: {...steps, first_session_opened: {outcome: "first_session_opened", session_id: "original-session"},
      first_turn_started: {outcome: "first_turn_started", session_id: "other-session", turn_id: "turn"}}}]) {
    assert.equal(compileActionReviewPlan({...proposal, status: "failed", checkpoint}).creationSession, undefined);
  }
});

test("terminal or persisted first-Turn readback does not advertise another Session", () => {
  assert.equal(compileActionReviewPlan({...proposal, status: "applied", receipt: {projection_verified: true}}).creationSession, undefined);
  for (const first_turn_readback of [null, {}, {status: "queued"},
    {status: "failed", completed_at: "2026-01-01T00:00:01Z", started_at: null, upstream_turn_id: null}]) {
    assert.equal(compileActionReviewPlan({...proposal, first_turn_readback}).creationSession, undefined);
  }
});

test("managed routing leaves requirement retention and action authority independent", () => {
  const input = {...proposal, normalized_parameters: {...proposal.normalized_parameters,
    completion_criteria: "Keep the original acceptance boundary"}, checkpoint: {steps}};
  const plan = compileActionReviewPlan(input);
  assert.deepEqual(plan.creationSession, {managedAgentId: "codex"});
  assert.deepEqual(plan.creationOutcome?.refinement.requirements,
    [{id: "completion", description: "Keep the original acceptance boundary"}]);
  assert.equal(Object.hasOwn(plan, "taskExecution"), false);
  assert.equal(Object.hasOwn(plan, "taskAccepted"), false);
  for (const status of ["gated", "stale", "rejected"]) {
    const held = compileActionReviewPlan({...input, status});
    assert.equal(held.canApply, false);
    assert.equal(held.retryOriginal, undefined);
  }
});
