import assert from "node:assert/strict";
import test from "node:test";
import { projectGoalRouteHint } from "../../loopx/control_plane/work_items/goal_route_hint.ts";

// These are transport facts, not pre-selected routes. Python-only text/normalizer
// compatibility is covered at the adapter boundary, including Unicode truncation.
export function routeContext(overrides: Record<string, unknown> = {}) {
  return {
    agent_id: "current-agent",
    agent_todo_summary: {
      claimed_advancement_open_items: null,
      claimed_by_others_items: null,
      executable_backlog_items: null,
      unclaimed_priority_open_items: null,
      current_agent_handoff_gates: null,
      handoff_gates: null,
      current_agent_claimed_advancement_count: 0,
      claimed_advancement_open_count: 0,
    },
    agent_lane_next_action: null,
    agent_scope_frontier: null,
    agent_lane_frontier_hint: null,
    active_state_next_action: "Durable next action",
    selected_recommended_action: "Selected action",
    latest_run_recommended_action: "",
    actions_projection_aligned: false,
    goal_route_hint_override: null,
    ...overrides,
  };
}
function row(overrides: Record<string, unknown> = {}) {
  return {done: false, status: "open", task_class: "advancement_task",
    todo_id: null, claimed_by: null, blocks_agent: null, unblocks_todo_id: null,
    excluded_agents: [], text: "Do work", display_text: "Do work", gate_identity: "gate", gate_state: null,
    ...overrides};
}
function contextWithSummary(summary: Record<string, unknown>) {
  const context = routeContext();
  return {...context, agent_todo_summary: {...context.agent_todo_summary, ...summary}};
}
function current(claim = false) {
  return {todo_id: "todo_current", text: "", claim_required_before_work: claim};
}

test("current action outranks frontier, peers and unclaimed work, with ID-only current actions", () => {
  for (const claim of [false, true]) {
    const context = contextWithSummary({claimed_advancement_open_items: [row({claimed_by: "peer"})],
      executable_backlog_items: [row()]});
    const hint = projectGoalRouteHint({...context, agent_lane_next_action: current(claim),
      agent_scope_frontier: {action: "wait", reason: "blocked"}})!;
    assert.equal(hint.route_decision, claim ? "claim_then_run_current_agent" : "run_current_agent_lane");
    assert.deepEqual(hint.current_agent_next_action, {todo_id: "todo_current", ...(claim ? {claim_required_before_work: true} : {})});
  }
});

test("canonical frontier action wins; legacy action and empty frontier retain their distinct meaning", () => {
  for (const [frontier, decision] of [
    [{effective_action: "agent_scope_wait"}, "agent_scope_wait"],
    [{action: "successor_replan_required"}, "successor_replan_required"],
    [{action: "successor_replan_required", effective_action: "agent_scope_wait"}, "successor_replan_required"],
    [{}, "agent_scope_frontier"], [null, "durable_goal_route"],
  ] as const) {
    const hint = projectGoalRouteHint(routeContext({agent_scope_frontier: frontier}))!;
    assert.equal(hint.route_decision, decision);
    assert.equal(hint.preserves_goal_next_action, true);
    assert.equal(hint.goal_next_action_mutation, "none");
  }
});

test("present empty primary lists suppress stale fallback candidates and gates (existence mutation guard)", () => {
  const backup = row({excluded_agents: ["current-agent"], gate_state: "blocking"});
  const context = contextWithSummary({executable_backlog_items: [], unclaimed_priority_open_items: [backup],
    current_agent_handoff_gates: [], handoff_gates: [backup]});
  const hint = projectGoalRouteHint(context)!;
  assert.equal(hint.route_decision, "durable_goal_route");
  assert.equal(hint.unclaimed_next_actions, undefined);
  assert.equal(hint.blocking_handoff_gates, undefined);
  assert.deepEqual(hint.counts, {current_agent_claimed_advancement_count: 0,
    unclaimed_advancement_count: 0, other_agent_claimed_advancement_count: 0, blocking_handoff_gate_count: 0});
  const fallback = projectGoalRouteHint(contextWithSummary({unclaimed_priority_open_items: [backup], handoff_gates: [backup]}))!;
  assert.equal(fallback.route_decision, "wait_or_reassign_other_agent_lane");
  assert.equal(fallback.current_agent_next_action, undefined);
});

test("peer selection fallback uses selected results, while counts use the original full source", () => {
  const context = contextWithSummary({claimed_advancement_open_items: [row({claimed_by: "peer", text: ""})],
    claimed_by_others_items: [row({claimed_by: "peer", todo_id: "other"})]});
  const hint = projectGoalRouteHint(context)!;
  assert.equal(hint.route_decision, "wait_or_reassign_other_agent_lane");
  assert.deepEqual(hint.other_agent_next_actions, [{todo_id: "other", claimed_by: "peer"}]);
  assert.equal(hint.current_agent_next_action, undefined);
});

test("ordered display deduplicates to three but counts duplicates, empty text, and all actionable rows", () => {
  const source = [null, row({done: true}), row({status: "blocked"}), row({task_class: "continuous_monitor"}),
    row({claimed_by: "peer"}), row({todo_id: "a", text: ""}), row({todo_id: "a"}), row({todo_id: "a"}),
    row({text: "Same visible prefix with first long suffix", display_text: "No ID", blocks_agent: "blocked", priority: 0, action_kind: "implement"}),
    row({text: "Same visible prefix with different long suffix", display_text: "No ID"}), row({todo_id: "b"}), row({todo_id: "c"})];
  const hint = projectGoalRouteHint(contextWithSummary({executable_backlog_items: source}))!;
  assert.equal(hint.route_decision, "claim_unowned_in_scope");
  assert.deepEqual(hint.unclaimed_next_actions, [{todo_id: "a"},
    {text: "No ID", blocks_agent: "blocked", priority: 0, action_kind: "implement"}, {todo_id: "b"}]);
  assert.deepEqual(hint.counts, {current_agent_claimed_advancement_count: 0,
    unclaimed_advancement_count: 7, other_agent_claimed_advancement_count: 0, blocking_handoff_gate_count: 0});
});

test("other counts preserve supplied-total subtraction, zero fallback and displayed lower bound", () => {
  for (const [total, mine, expected] of [[10, 3, 7], [0, 8, 5], [1, 9, 3]]) {
    const hint = projectGoalRouteHint(contextWithSummary({claimed_advancement_open_count: total,
      current_agent_claimed_advancement_count: mine,
      claimed_advancement_open_items: Array.from({length: 5}, (_, i) => row({claimed_by: "peer", todo_id: `other${i}`}))}))!;
    assert.equal((hint.counts as Record<string, unknown>).other_agent_claimed_advancement_count, expected);
  }
});

test("blocking gates use exclusion and original identity, not candidate eligibility, capped at three", () => {
  const gate = (id: string, extra = {}) => row({todo_id: id, gate_identity: id, done: true,
    text: "", status: "closed", excluded_agents: ["current-agent"], gate_state: "blocking", ...extra});
  const hint = projectGoalRouteHint(contextWithSummary({handoff_gates: [null,
    gate("ignore", {excluded_agents: ["someone"]}), gate("ignore2", {gate_state: "cleared"}),
    gate("a"), gate("a"), gate("b"), gate("c"), gate("d")]}))!;
  assert.deepEqual(hint.blocking_handoff_gates, ["a", "b", "c"].map(todo_id => ({todo_id, excluded_agents: ["current-agent"], gate_state: "blocking"})));
  assert.equal((hint.counts as Record<string, unknown>).blocking_handoff_gate_count, 3);
});

test("typed task override removes current action before merging and never creates a missing-agent hint", () => {
  const patch = {omit_current_agent_next_action: true, kind: "task_orchestration", route_decision: "coordinate_task_bundle",
    reason: "task-scoped coordinator may spawn admitted child lanes", peer_lane_count: 2};
  const context = routeContext({agent_lane_next_action: current(), goal_route_hint_override: patch});
  const before = structuredClone(context);
  const hint = projectGoalRouteHint(context)!;
  for (const field of ["kind", "route_decision", "reason", "peer_lane_count"] as const) assert.equal(hint[field], patch[field]);
  assert.equal(hint.current_agent_next_action, undefined);
  assert.equal(hint.omit_current_agent_next_action, undefined);
  assert.equal(hint.has_durable_next_action, true);
  assert.deepEqual(context, before);
  assert.equal(projectGoalRouteHint({...context, agent_id: null}), null);
  assert.equal(projectGoalRouteHint({...context, agent_id: ""}), null);
  assert.deepEqual(projectGoalRouteHint({...context, goal_route_hint_override: {omit_current_agent_next_action: false}})!.current_agent_next_action, {todo_id: "todo_current"});
});

test("frontier metadata omission and named alignment compatibility remain read-only", () => {
  const context = routeContext({agent_lane_next_action: {...current(), todo_id: null, text: "text only", source: null, selected_by: "lane", confidence: 0},
    agent_lane_frontier_hint: {decision: "wait", source: null, reason_code: "cause", quiet_noop_allowed: false, ignored: "x"},
    latest_run_recommended_action: "latest", actions_projection_aligned: true});
  const before = structuredClone(context), hint = projectGoalRouteHint(context)!;
  assert.equal(hint.selected_action_differs_from_durable, false);
  assert.equal(hint.has_latest_run_recommended_action, true);
  assert.deepEqual(hint.frontier_hint, {decision: "wait", reason_code: "cause", quiet_noop_allowed: false});
  assert.deepEqual(hint.current_agent_next_action, {text: "text only", selected_by: "lane", confidence: 0});
  assert.deepEqual(context, before);
  assert.deepEqual(projectGoalRouteHint(routeContext({agent_lane_frontier_hint: {}}))!.frontier_hint, {});
});

test("malformed routing facts are rejected instead of cast into domain types", () => {
  for (const context of [null, {}, routeContext({agent_id: 3}), routeContext({actions_projection_aligned: "false"}),
    routeContext({agent_lane_next_action: {}}), routeContext({agent_scope_frontier: []}),
    routeContext({goal_route_hint_override: {peer_lane_count: -1}}), routeContext({goal_route_hint_override: {arbitrary: true}}),
    contextWithSummary({executable_backlog_items: {}}), contextWithSummary({executable_backlog_items: [{}]}),
    contextWithSummary({current_agent_claimed_advancement_count: -1})]) {
    assert.throws(() => projectGoalRouteHint(context));
  }
});


test("gate deduplication retains distinct raw identities even when normalized Todo IDs coincide", () => {
  const base = row({todo_id: "same-id", excluded_agents: ["current-agent"], gate_state: "blocking"});
  const hint = projectGoalRouteHint(contextWithSummary({handoff_gates: [
    {...base, gate_identity: " same-id "}, {...base, gate_identity: "same-id"},
    {...base, gate_identity: "same-id"},
  ]}))!;
  assert.deepEqual(hint.blocking_handoff_gates, [
    {todo_id: "same-id", excluded_agents: ["current-agent"], gate_state: "blocking"},
    {todo_id: "same-id", excluded_agents: ["current-agent"], gate_state: "blocking"},
  ]);
});
