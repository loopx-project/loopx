import assert from "node:assert/strict";
import test from "node:test";

import {
  resolveRefreshRecommendation,
  resolveLaneRecommendation,
} from "../../loopx/control_plane/work_items/refresh_recommendation.ts";

const baseRequest = {
  schema_version: "refresh_recommendation_request_v0",
  explicit_action: null,
  agent_id: "agent-a",
  settlement_identity: {
    effect_id: "goal-shared:agent-a:todo_selected:turn-1",
    goal_id: "goal-shared",
    agent_id: "agent-a",
    todo_id: "todo_selected",
    turn_instance_id: "turn-1",
  },
  settlement_candidate: {
    todo_id: "todo_selected",
    text: "Continue the receipt-bound slice.",
    status: "open",
    task_class: "advancement_task",
    claimed_by: "agent-a",
    selection_binding: "heartbeat_receipt",
  },
  agent_lane_candidate: {
    todo_id: "todo_higher_priority",
    text: "Start a newer higher-priority slice.",
    status: "open",
    task_class: "advancement_task",
    claimed_by: "agent-a",
  },
  active_state_next_action: "Follow another agent's shared action.",
  unscoped_agent_todo_fallback: null,
  default_action: "Inspect refreshed state.",
};

test("exact settlement binding outranks lane re-selection and shared prose", () => {
  const result = resolveRefreshRecommendation(baseRequest);

  assert.equal(result.recommended_action, "Continue the receipt-bound slice.");
  assert.equal(result.recommended_action_source, "settlement_bound_todo");
  assert.equal(result.authority, "settlement");
  assert.equal(result.settlement_alignment, "exact");
  assert.equal(result.todo_id, "todo_selected");
});

test("ineligible settlement Todo falls through to a runnable lane candidate", () => {
  const result = resolveRefreshRecommendation({
    ...baseRequest,
    settlement_candidate: {
      ...baseRequest.settlement_candidate,
      status: "blocked",
    },
  });

  assert.equal(result.recommended_action, "Start a newer higher-priority slice.");
  assert.equal(result.recommended_action_source, "agent_lane_selected_todo");
  assert.equal(result.settlement_alignment, "unavailable");
  assert.equal(result.settlement_gap_reason, "candidate_ineligible");
});

test("a peer-claimed lane candidate cannot shadow the shared fallback", () => {
  const result = resolveRefreshRecommendation({
    ...baseRequest,
    settlement_identity: null,
    settlement_candidate: null,
    agent_lane_candidate: {
      ...baseRequest.agent_lane_candidate,
      claimed_by: "agent-b",
    },
  });

  assert.equal(result.recommended_action, "Follow another agent's shared action.");
  assert.equal(result.recommended_action_source, "active_state_next_action");
});

test("agent-scoped resolution never consumes the unscoped compatibility lane", () => {
  const result = resolveRefreshRecommendation({
    ...baseRequest,
    settlement_identity: null,
    settlement_candidate: null,
    agent_lane_candidate: null,
    active_state_next_action: null,
    unscoped_agent_todo_fallback: {
      todo_id: "todo_peer_only",
      text: "Run the peer-only Todo.",
      status: "open",
      task_class: "advancement_task",
      claimed_by: "agent-b",
    },
  });

  assert.equal(result.recommended_action, "Inspect refreshed state.");
  assert.equal(result.recommended_action_source, "default_refresh_action");
});

test("an unclaimed agent-lane candidate preserves its claim prerequisite", () => {
  const result = resolveRefreshRecommendation({
    ...baseRequest,
    settlement_identity: null,
    settlement_candidate: null,
    agent_lane_candidate: {
      ...baseRequest.agent_lane_candidate,
      claimed_by: undefined,
      claim_required_before_work: true,
    },
  });

  assert.equal(result.recommended_action_source, "agent_lane_selected_todo");
  assert.equal(result.claim_required_before_work, true);
});

test("missing settlement Todo is a typed gap before legal lane fallback", () => {
  const result = resolveRefreshRecommendation({
    ...baseRequest,
    settlement_candidate: null,
  });

  assert.equal(result.recommended_action_source, "agent_lane_selected_todo");
  assert.equal(result.settlement_alignment, "unavailable");
  assert.equal(result.settlement_gap_reason, "candidate_missing");
});

test("settlement identity drift is rejected instead of trusted as read-model input", () => {
  assert.throws(
    () =>
      resolveRefreshRecommendation({
        ...baseRequest,
        settlement_identity: {
          ...baseRequest.settlement_identity,
          effect_id: "goal-shared:agent-a:todo_other:turn-1",
        },
      }),
    /effect_id mismatch/,
  );
});

test("explicit recommendation remains authoritative", () => {
  const result = resolveRefreshRecommendation({
    ...baseRequest,
    explicit_action: "Record the validated successor.",
  });

  assert.equal(result.recommended_action, "Record the validated successor.");
  assert.equal(result.recommended_action_source, "explicit_arg");
  assert.equal(result.authority, "explicit");
  assert.equal(result.settlement_alignment, "not_applicable");
});

const laneContext = {
  goal_id: "goal-shared", source_revision: "sha256:" + "a".repeat(64),
  registered_agents: ["agent-a", "agent-b"], agent_id: "agent-a",
  selected_todo: baseRequest.agent_lane_candidate,
  task_facts: baseRequest.agent_lane_candidate, prior_resolution: null,
};

test("a lane step refines the selected task without rewriting its identity or text", () => {
  const read = resolveLaneRecommendation(laneContext);
  const result = resolveLaneRecommendation({...laneContext, write: {
    text: "Try a smaller experiment.", expected_basis: read.basis,
  }});
  assert.equal(result.admitted, true);
  const resolution = result.resolution as Record<string, unknown>;
  assert.equal(resolution.todo_id, "todo_higher_priority");
  assert.equal(resolution.recommended_action_source, "agent_lane_step");
  const projected = resolveLaneRecommendation({...laneContext, prior_resolution: resolution});
  const selected = projected.selected_todo as Record<string, unknown>;
  assert.equal(selected.text, baseRequest.agent_lane_candidate.text);
  assert.equal(selected.next_step, "Try a smaller experiment.");
  assert.notEqual(projected.basis, read.basis);
});

test("a peer's step is not adopted; a stale task or source cannot resurrect a step", () => {
  const first = resolveLaneRecommendation({...laneContext, write: {text: "Try an experiment."}});
  for (const change of [
    {agent_id: "agent-b"},
    {task_facts: {...laneContext.task_facts, updated_at: "later"}},
    {source_revision: "sha256:" + "b".repeat(64)},
    {selected_todo: {...laneContext.selected_todo, status: "done"}},
  ]) {
    const result = resolveLaneRecommendation({...laneContext, ...change, prior_resolution: first.resolution});
    assert.equal((result.selected_todo as Record<string, unknown>).next_step, undefined);
  }
});

test("ordinary personal writeback needs no goal report scope; the same actor's old basis conflicts", () => {
  const read = resolveLaneRecommendation(laneContext);
  const first = resolveLaneRecommendation({...laneContext, write: {text: "Inspect evidence.", expected_basis: read.basis}});
  const conflict = resolveLaneRecommendation({...laneContext, prior_resolution: first.resolution,
    write: {text: "Replace the old step.", expected_basis: read.basis}});
  assert.equal(conflict.admitted, false);
  assert.equal(conflict.error_code, "next_action_basis_conflict");
});

test("unknown actors, absent tasks and peer-owned tasks fail closed", () => {
  for (const change of [
    {registered_agents: []}, {agent_id: "unknown"}, {selected_todo: null},
    {selected_todo: {...laneContext.selected_todo, claimed_by: "agent-b"}},
  ]) {
    assert.equal(resolveLaneRecommendation({...laneContext, ...change, write: {text: "Try."}}).admitted, false);
  }
});
