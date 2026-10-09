import assert from "node:assert/strict";
import test from "node:test";
import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";
import { projectQuotaSelection, projectTodoQuotaPlanning } from "../../loopx/control_plane/todos/quota_selection.ts";
import { productionScaleCoordinationFixture } from "./production_scale_coordination_fixture.ts";
import {projectAdvancementFrontier} from "../../loopx/control_plane/todos/frontier_revision.ts";

function row(id: string, fields: JsonObject = {}): JsonObject {
  return {payload: {todo_id: id}, claim: null, bound: null, blocks: null, excluded: [],
    global: false, gate: false, removed: false, actionable: true, due: false,
    watch_only: false,
    task_class: "advancement_task", priority: 1, index: 1, profile_rank: 1,
    missing: [], raw_claimed: false, ...fields};
}
function request(items: JsonObject[], fields: JsonObject = {}): JsonObject {
  return {items, active_items: items, active_executable_items: [], agent_id: "agent-a",
    user_gate_scope: false, monitor_supported: true, diagnostic_limit: 3,
    backlog_limit: 8, visibility_limit: 16, profile: null, source_open_count: items.length, ...fields};
}
const ids = (value: unknown) => (value as JsonObject[]).map(item => item.todo_id);

test("route planning shares claim exclusion, preserves legacy visibility and never grants execution", () => {
  const fact = (identity: string, fields: JsonObject = {}) => ({identity,
    display: {todo_id: identity, text: "Review route"}, gate: false, replan: null,
    task_class: "advancement_task", claim: null, excluded: [], sort: [1, 1, "", identity], ...fields});
  const input = {schema_version: "todo_quota_planning_request_v2", selection: request([], {available: [], backlog_limit: 0}),
    source_contract: {},
    resume: {schema_version: "todo_resume_planning_request_v0", agent_id: "agent-a", item_limit: 8,
      has_deferred_count: false, has_visible_deferred_count: false, deferred_count: null,
      available_capabilities: null, sources: Object.fromEntries(["items", "backlog_items", "first_open_items", "deferred_items",
        "deferred_resume_candidates", "resume_blocked_items", "monitor_open_items",
        "current_agent_claimed_monitor_items", "claimed_monitor_open_items"].map(key => [key, []]))},
    handoff_items: [],
    route_items: [fact("free", {replan: false}), fact("free"), fact("own", {claim: "agent-a"}),
      fact("peer", {claim: "agent-b"}), fact("excluded", {excluded: ["agent-a"]}),
      fact("monitor", {task_class: "continuous_monitor"}), fact("missing", {gate: true}),
      fact("gate", {gate: true, replan: true}), fact(""), fact("peer")]};
  const before = structuredClone(input);
  assert.equal((projectTodoQuotaPlanning(input).lanes as JsonObject).gate_items, undefined);
  assert.equal(projectTodoQuotaPlanning(input).frontier_deadline, undefined);
  assert.throws(() => projectTodoQuotaPlanning({...input, current_time: "bad"}), /planning clock/);
  assert.throws(() => projectTodoQuotaPlanning({...input, current_time: null}), /planning clock/);
  const timed = projectTodoQuotaPlanning({...input, current_time: "2026-10-01T00:00:00Z"});
  assert.deepEqual((timed.lanes as JsonObject).gate_items, []);
  assert.equal(timed.frontier_deadline, null);
  const result = projectTodoQuotaPlanning(input), routes = result.route_lanes as JsonObject;
  const v3 = projectTodoQuotaPlanning({...input, schema_version: "todo_quota_planning_request_v3",
    selection: {...input.selection, observed_at: 100}});
  assert.deepEqual(v3.route_lanes, routes);
  assert.deepEqual(v3.source_completeness, result.source_completeness);
  assert.equal(routes.route_continuation_replan_count, 5);
  assert.equal(routes.current_agent_route_continuation_replan_count, 3);
  assert.equal(routes.unclaimed_route_continuation_replan_count, 3);
  assert.equal(routes.other_agent_route_continuation_replan_count, 2);
  assert.deepEqual(routes.route_continuation_replan_candidates, []);
  assert.deepEqual((result.lanes as JsonObject).executable_items, []);
  assert.deepEqual(input, before);
  const visible = structuredClone(input); visible.selection.backlog_limit = 8;
  assert.deepEqual(ids((projectTodoQuotaPlanning(visible).route_lanes as JsonObject)
    .current_agent_route_continuation_replan_candidates), ["free", "gate", "own"]);
  // Malformed current carriers fail; older co-deployed requests stay unchanged.
  assert.throws(() => projectTodoQuotaPlanning({...input, route_items: null}), /route items/);
  assert.throws(() => projectTodoQuotaPlanning({...input, route_items: [fact("bad", {replan: "true"})]}), /classification/);
  assert.equal(projectTodoQuotaPlanning({...input, schema_version: "todo_quota_planning_request_v1"}).route_lanes, undefined);
  assert.equal(projectTodoQuotaPlanning({...input, schema_version: "todo_quota_planning_request_v1"}).handoff_lanes, undefined);
  const legacy = {...input, schema_version: "todo_quota_planning_request_v0"};
  const {available: _available, ...oldSelection} = legacy.selection;
  assert.deepEqual(projectTodoQuotaPlanning({...legacy, selection: oldSelection}),
    projectTodoQuotaPlanning({...input, schema_version: "todo_quota_planning_request_v1"}));
});

test("handoff counts retain addressed review states, source order and duplicates before zero display", () => {
  const states = ["blocking", "cleared_without_successor", "cleared_with_successor",
    "cleared_no_followup", "superseded", "deferred", "historical_unknown"];
  const handoffs = states.map((gate_state, i) => ({display: {todo_id: `gate-${i}`, gate_state,
    claimed_by: "agent-b"}, excluded: ["agent-a"]}));
  handoffs.push(handoffs[1], handoffs[1], {display: {todo_id: "other", gate_state: states[1],
    claimed_by: "agent-a"}, excluded: ["agent-b"]});
  const input = {schema_version: "todo_quota_planning_request_v2", route_items: [], handoff_items: handoffs,
    source_contract: {},
    selection: request([], {available: [], backlog_limit: 0}),
    resume: {schema_version: "todo_resume_planning_request_v0", agent_id: "agent-a", item_limit: 8,
      has_deferred_count: false, has_visible_deferred_count: false, deferred_count: null,
      available_capabilities: null, sources: Object.fromEntries(["items", "backlog_items", "first_open_items", "deferred_items",
        "deferred_resume_candidates", "resume_blocked_items", "monitor_open_items",
        "current_agent_claimed_monitor_items", "claimed_monitor_open_items"].map(key => [key, []]))}};
  const before = structuredClone(input);
  const result = projectTodoQuotaPlanning(input), lanes = result.handoff_lanes as JsonObject;
  const v3 = projectTodoQuotaPlanning({...input, schema_version: "todo_quota_planning_request_v3",
    selection: {...input.selection, observed_at: 100}});
  assert.deepEqual(v3.handoff_lanes, lanes);
  assert.equal(lanes.handoff_gate_count, 10);
  assert.equal(lanes.current_agent_handoff_gate_count, 9);
  assert.equal(lanes.current_agent_cleared_without_successor_handoff_count, 3);
  assert.deepEqual(lanes.handoff_gates, []);
  assert.deepEqual(lanes.current_agent_handoff_gates, []);
  assert.deepEqual(lanes.current_agent_cleared_without_successor_handoff_gates, []);
  assert.deepEqual((result.lanes as JsonObject).executable_items, []);
  assert.deepEqual(input, before);
  const visible = {...input, selection: {...input.selection, backlog_limit: 8}};
  assert.deepEqual((projectTodoQuotaPlanning(visible).handoff_lanes as JsonObject).handoff_gates,
    handoffs.slice(0, 8).map(row => row.display));
  assert.deepEqual(projectTodoQuotaPlanning({...visible, handoff_items: []}).handoff_lanes, {});
  const unscoped = projectTodoQuotaPlanning({...visible, selection: {...visible.selection, agent_id: null}})
    .handoff_lanes as JsonObject;
  assert.equal(unscoped.handoff_gate_count, 10);
  assert.equal(unscoped.current_agent_handoff_gate_count, undefined);
  for (const handoff_items of [null, [{display: null, excluded: []}], [{display: {}, excluded: "agent-a"}]]) {
    assert.throws(() => projectTodoQuotaPlanning({...input, handoff_items}), /handoff/);
  }
});

test("gate applicability overrides execution claims, but not another lane's explicit scope", () => {
  const rows = [row("global", {gate: true, global: true, claim: "agent-b", excluded: ["agent-a"]}),
    row("targeted", {gate: true, blocks: "agent-a", claim: "agent-b"}),
    row("other", {gate: true, blocks: "agent-b", claim: "agent-a"}),
    row("legacy", {gate: true, claim: "agent-b"}),
    row("action", {bound: "agent-a", claim: "agent-b"}),
    row("other-action", {bound: "agent-b", claim: "agent-a"})];
  const input = request(rows, {user_gate_scope: true});
  const before = structuredClone(input);
  const lanes = projectQuotaSelection(input).lanes as JsonObject;
  assert.deepEqual(ids(lanes.open_items), ["global", "targeted"]);
  assert.deepEqual(ids(lanes.user_action_open_items), ["action"]);
  assert.deepEqual(ids(lanes.other_agent_scoped_items), ["other", "legacy"]);
  assert.deepEqual(ids(lanes.active_next_action_items), ["global", "targeted", "action"]);
  assert.equal(lanes.claim_scope, null);
  assert.deepEqual(input, before);
});

test("execution scope is shared with active-next-action, including removed-policy rejection", () => {
  const items = [row("excluded", {excluded: ["agent-a"]}), row("removed", {removed: true}),
    row("peer", {claim: "agent-b"}), row("unclaimed", {priority: 0, profile_rank: 0}),
    row("mine", {claim: "agent-a", priority: 4, profile_rank: 2})];
  const result = projectQuotaSelection(request(items));
  const lanes = result.lanes as JsonObject;
  assert.deepEqual(ids(lanes.open_items), ["mine", "unclaimed"]);
  assert.deepEqual(ids(lanes.active_next_action_items), ["unclaimed", "mine"]);
  assert.equal((lanes.claim_scope as JsonObject).executor_excluded_self_count, 1);
  assert.equal((lanes.claim_scope as JsonObject).removed_continuation_blocked_count, 1);
  assert.deepEqual(ids((result.claim_visibility as JsonObject).claimed_by_others_items), ["peer"]);
});

test("monitor eligibility preserves provider writeback and capability fences", () => {
  const items = [row("due", {task_class: "continuous_monitor", due: true}),
    row("watch", {task_class: "continuous_monitor", due: true, watch_only: true}),
    row("missing", {task_class: "continuous_monitor", due: true, missing: ["network"]}),
    row("future", {task_class: "continuous_monitor"})];
  const lanes = projectQuotaSelection(request(items)).lanes as JsonObject;
  assert.deepEqual(ids(lanes.monitor_due_items), ["due", "watch"]);
  assert.deepEqual(ids(lanes.watch_only_monitor_items), ["watch"]);
  assert.deepEqual(ids(lanes.watch_only_monitor_due_items), ["watch"]);
  assert.deepEqual(ids(lanes.non_watch_only_monitor_due_items), ["due"]);
  assert.deepEqual(ids(lanes.monitor_capability_blocked_due_items), ["missing"]);
  assert.deepEqual(ids(lanes.executable_items), []);
  const unsupported = projectQuotaSelection(request(items, {monitor_supported: false})).lanes as JsonObject;
  assert.deepEqual(unsupported.monitor_due_items, []);
  assert.deepEqual(unsupported.monitor_capability_blocked_due_items, []);
});

test("production-scale corpus counts remain complete while claimant display is bounded", () => {
  const fixture = productionScaleCoordinationFixture("goal-quota");
  const records = fixture.projection.todos as JsonObject[];
  const open = records.filter(item => item.role === "agent" && !item.done);
  const items = open.map((item, index) => row(String(item.todo_id), {
    payload: item, index, claim: item.claimed_by ?? null, raw_claimed: !!item.claimed_by,
    task_class: item.task_class, actionable: item.status === "open",
  }));
  const input = request(items, {visibility_limit: 2});
  const before = structuredClone(input);
  const result = projectQuotaSelection(input);
  const scope = (result.lanes as JsonObject).claim_scope as JsonObject;
  assert.equal(open.length, 80);
  assert.equal(scope.current_agent_claimed_open_count, 40);
  assert.equal(scope.other_agent_claimed_open_count, 40);
  const visible = (result.claim_visibility as JsonObject).claimed_open_items as JsonObject[];
  assert.equal(visible.length, 2);
  assert.deepEqual(new Set(visible.map(item => item.claimed_by)), new Set(["agent-a", "agent-b"]));
  assert.deepEqual(input, before);
});

test("stable ties and zero display never change selection or counts", () => {
  const result = projectQuotaSelection(request([row("zed"), row("alpha")], {
    visibility_limit: 0, backlog_limit: 0, diagnostic_limit: 0,
  }));
  assert.deepEqual(ids((result.lanes as JsonObject).open_items), ["zed", "alpha"]);
  assert.equal((result.lanes as JsonObject).open_count, 2);
  assert.deepEqual((result.claim_visibility as JsonObject).unclaimed_priority_open_items, []);
});

test("complete frontier counts survive hidden peer pressure without granting hidden work", () => {
  const mine = Array.from({length: 15}, (_, i) => ({id: `own-${i}`, claim: "agent-a",
    excluded: [], advancement: true, actionable: true, updated: "2026-09-01T00:00:00Z", serialized: "{}"}));
  const peers = Array.from({length: 20}, (_, i) => ({...mine[0], id: `peer-${i}`, claim: "agent-b"}));
  for (const full of [mine, [...mine, ...peers], [...mine, ...peers].reverse()]) {
    const index = projectAdvancementFrontier({schema_version: "todo_frontier_revision_request_v0",
      operation: "index", rows: full}).index;
    const observed = [row("visible-owned", {claim: "agent-a"}), row("visible-peer", {claim: "agent-b"})];
    const result = projectQuotaSelection(request(observed, {frontier_revision_index: index, source_open_count: full.length}));
    assert.equal((result.claim_visibility as JsonObject).current_agent_claimed_advancement_count, 15);
    // Counting hidden commitments does not synthesize a claim, lease or an
    // executable row. Selection still uses only the independently admitted rows.
    assert.deepEqual(ids((result.lanes as JsonObject).executable_items), ["visible-owned"]);
    assert.equal((result.work_counts as JsonObject).complete, false);
  }
});

test("missing historical census retains observed counts; malformed new totals reject", () => {
  const observed = [row("mine", {claim: "agent-a"})];
  const index = {schema_version: "todo_frontier_revision_index_v0"};
  assert.equal((projectQuotaSelection(request(observed, {frontier_revision_index: index}))
    .claim_visibility as JsonObject).current_agent_claimed_advancement_count, 1);
  for (const counts of [{"agent-a": -1}, {"agent-a": "15"}, {"agent-a": Number.MAX_SAFE_INTEGER + 1}, {" AGENT-A ": 15}]) {
    assert.throws(() => projectQuotaSelection(request(observed, {
      frontier_revision_index: {...index, claimed_advancement_counts: counts},
    })));
  }
});

test("malformed facts are rejected, not coerced into scope or execution authority", () => {
  for (const fields of [{gate: "false"}, {global: "true"}, {excluded: "agent-a"}, {priority: null}]) {
    assert.throws(() => projectQuotaSelection(request([row("bad", fields)])));
  }
  assert.throws(() => projectQuotaSelection(request([], {visibility_limit: -1})));
});

function clockRequest(items: JsonObject[], fields: JsonObject = {}): JsonObject {
  return {schema_version: "todo_quota_planning_request_v3", selection: request(items, {available: [], observed_at: 100, ...fields}),
    route_items: [], handoff_items: [], source_contract: {},
    resume: {schema_version: "todo_resume_planning_request_v0", sources: Object.fromEntries([
      "items", "backlog_items", "first_open_items", "deferred_items", "deferred_resume_candidates",
      "resume_blocked_items", "monitor_open_items", "current_agent_claimed_monitor_items", "claimed_monitor_open_items",
    ].map(key => [key, []])), agent_id: null, available_capabilities: null,
    item_limit: 8, has_deferred_count: false, has_visible_deferred_count: false}};
}

test("quota v3 derives due and gap from one clock, selecting older due work before display order", () => {
  const monitor = (id: string, fields: JsonObject = {}) => row(id, {task_class: "continuous_monitor",
    due_at: null, expires_at: null, required: [], targets: [], ...fields});
  const items = [monitor("gap-first", {index: 1}), monitor("gap-owned", {index: 2, claim: "agent-a"}),
    monitor("due", {due_at: 100, due: false}), monitor("future", {due_at: 101, due: true}),
    monitor("expired", {due_at: 90, expires_at: 100, due: true}),
    monitor("expired-gap", {expires_at: 100}), monitor("watch-gap", {watch_only: true}),
    monitor("watch-due", {watch_only: true, due_at: 90}),
    monitor("capability", {due_at: 90, required: ["compiler"]}),
    monitor("peer", {claim: "agent-b"}), monitor("excluded", {excluded: ["agent-a"]}),
    monitor("blocked", {actionable: false})];
  const input = clockRequest(items), before = structuredClone(input);
  const lanes = projectTodoQuotaPlanning(input).lanes as JsonObject;
  assert.deepEqual(ids(lanes.monitor_schedule_gap_items), ["gap-first", "gap-owned"]);
  assert.deepEqual(ids(lanes.monitor_due_items), ["watch-due", "due"]);
  assert.deepEqual(ids(lanes.monitor_capability_blocked_due_items), ["capability"]);
  assert.deepEqual(ids(lanes.watch_only_monitor_due_items), ["watch-due"]);
  assert.deepEqual(input, before);
  const unsupported = projectTodoQuotaPlanning(clockRequest(items, {monitor_supported: false})).lanes as JsonObject;
  assert.deepEqual(unsupported.monitor_schedule_gap_items, []);
  assert.deepEqual(unsupported.monitor_due_items, []);
});

test("recurring monitors cannot hide older due work behind an earlier display coordinate", () => {
  const monitor = (id: string, index: number, due_at: number) => row(id, {
    task_class: "continuous_monitor", claim: "agent-a", watch_only: true,
    index, due_at, expires_at: null, required: [], targets: [],
  });
  for (const observed_at of [100, 200, 300]) {
    const recent = monitor("frequent", 1, observed_at);
    const older = monitor("overdue", 25, 50);
    for (const items of [[recent, older], [older, recent]]) {
      const input = clockRequest(items, {observed_at, visibility_limit: 0, backlog_limit: 0});
      const before = structuredClone(input);
      const lanes = projectTodoQuotaPlanning(input).lanes as JsonObject;
      // This is the full eligible set consumed by the one-row quota projection.
      assert.deepEqual(ids(lanes.monitor_due_items), ["overdue", "frequent"]);
      assert.deepEqual(ids((lanes.monitor_due_items as JsonObject[]).slice(0, 1)), ["overdue"]);
      assert.deepEqual(ids(lanes.watch_only_monitor_due_items), ["overdue", "frequent"]);
      assert.deepEqual(input, before);
    }
  }
});

test("due-time fairness retains claim, profile and priority ranks, with stable time/index ties", () => {
  const monitor = (id: string, fields: JsonObject = {}) => row(id, {
    task_class: "continuous_monitor", claim: "agent-a", due_at: 90,
    expires_at: null, required: [], targets: [], ...fields,
  });
  const items = [
    monitor("unclaimed", {claim: null, due_at: 1}),
    monitor("ordinary-profile", {profile_rank: 2, due_at: 2}),
    monitor("lower-priority", {priority: 2, due_at: 3}),
    monitor("later", {index: 1, due_at: 90}),
    monitor("tie-b", {index: 8, due_at: 20}),
    monitor("tie-a", {index: 8, due_at: 20}),
    monitor("first-index", {index: 7, due_at: 20}),
    monitor("older", {index: 99, due_at: 19.999999}),
    monitor("higher-priority", {priority: 0, due_at: 99}),
    monitor("preferred-profile", {profile_rank: 0, due_at: 100}),
  ];
  const lanes = projectTodoQuotaPlanning(clockRequest(items)).lanes as JsonObject;
  assert.deepEqual(ids(lanes.monitor_due_items), [
    "preferred-profile", "higher-priority", "older", "first-index", "tie-b", "tie-a",
    "later", "lower-priority", "ordinary-profile", "unclaimed",
  ]);
  // General presentation is not repurposed as a scheduling order.
  assert.deepEqual(ids(lanes.monitor_items), [
    "preferred-profile", "higher-priority", "later", "first-index", "tie-b", "tie-a",
    "older", "lower-priority", "ordinary-profile", "unclaimed",
  ]);
});

test("an older date grants no eligibility and unsupported providers have no due lane", () => {
  const monitor = (id: string, fields: JsonObject = {}) => row(id, {
    task_class: "continuous_monitor", due_at: 1, expires_at: null,
    required: [], targets: [], ...fields,
  });
  const items = [
    monitor("peer", {claim: "agent-b"}), monitor("excluded", {excluded: ["agent-a"]}),
    monitor("blocked", {actionable: false}), monitor("removed", {removed: true}),
    monitor("expired", {expires_at: 100}), monitor("future", {due_at: 101}),
    monitor("missing-date", {due_at: null}), monitor("capability", {required: ["network"]}),
    monitor("recent", {index: 1, due_at: 90}), monitor("old", {index: 99, due_at: 20}),
  ];
  const lanes = projectTodoQuotaPlanning(clockRequest(items)).lanes as JsonObject;
  assert.deepEqual(ids(lanes.monitor_due_items), ["old", "recent"]);
  assert.deepEqual(ids(lanes.non_watch_only_monitor_due_items), ["old", "recent"]);
  assert.deepEqual(ids(lanes.monitor_capability_blocked_due_items), ["capability"]);
  const unsupported = projectTodoQuotaPlanning(clockRequest(items, {monitor_supported: false})).lanes as JsonObject;
  assert.deepEqual(unsupported.monitor_due_items, []);
});

test("quota v3 requires finite clock and schedule facts while v0/v1/v2 keep their old wire shape", () => {
  for (const observed_at of [undefined, null, "100", NaN, Infinity]) {
    assert.throws(() => projectTodoQuotaPlanning(clockRequest([], {observed_at})), /observed_at/);
  }
  for (const fields of [{due_at: undefined}, {due_at: "100"}, {due_at: Infinity}, {expires_at: false}]) {
    assert.throws(() => projectTodoQuotaPlanning(clockRequest([row("bad", {
      due_at: null, expires_at: null, required: [], targets: [], ...fields})])), /due_at|expires_at/);
  }
  assert.throws(() => projectTodoQuotaPlanning({...clockRequest([]), source_contract: undefined}), /closure source/);
  const selection = request([row("old", {due: true, task_class: "continuous_monitor", required: [], targets: []})], {available: []});
  const direct = projectQuotaSelection(selection);
  for (const schema_version of ["todo_quota_planning_request_v0", "todo_quota_planning_request_v1", "todo_quota_planning_request_v2"]) {
    const projected = projectTodoQuotaPlanning({...clockRequest([]), schema_version, selection});
    assert.deepEqual(projected.lanes, direct.lanes);
    assert.equal((projected.lanes as JsonObject).monitor_schedule_gap_items, undefined);
  }
});

test("quota v2 validates closure in the existing batch while retaining v0/v1 wire behavior", () => {
  const resume = {schema_version: "todo_resume_planning_request_v0", sources: {
    items: [], backlog_items: [], first_open_items: [], deferred_items: [], deferred_resume_candidates: [],
    resume_blocked_items: [], monitor_open_items: [], current_agent_claimed_monitor_items: [], claimed_monitor_open_items: []}, agent_id: null,
    item_limit: 8, has_deferred_count: false, has_visible_deferred_count: false, deferred_count: null, available_capabilities: null};
  const selection = request([], {available: []});
  const v0 = projectTodoQuotaPlanning({schema_version: "todo_quota_planning_request_v0", resume, selection});
  const v1 = projectTodoQuotaPlanning({schema_version: "todo_quota_planning_request_v1", resume, selection});
  assert.deepEqual(v0, v1);
  assert.equal(v0.source_completeness, undefined);
  assert.throws(() => projectTodoQuotaPlanning({schema_version: "todo_quota_planning_request_v2",
    resume, selection, route_items: [], handoff_items: []}), /closure source/);
  const v2 = projectTodoQuotaPlanning({schema_version: "todo_quota_planning_request_v2", resume, selection,
    route_items: [], handoff_items: [], source_contract: {}});
  // v2 carries the closure result plus the route/handoff hint lanes; the rest
  // of the projection must stay identical to v1.
  const {source_completeness, closure_intent, handoff_lanes, route_lanes, ...unchanged} = v2;
  assert.deepEqual(unchanged, v1);
  assert.deepEqual(handoff_lanes, {});
  assert.deepEqual(route_lanes, {});
  assert.equal((source_completeness as JsonObject).status, "invalid");
  assert.equal(closure_intent, null);
});


function planningDeadline(monitors: JsonObject[], gates: JsonObject[], fields: JsonObject = {}) {
  const items = [...monitors.map(payload => row(String(payload.todo_id),
    {payload, task_class: "continuous_monitor", required: [], targets: []})), ...gates.map(payload => row(String(payload.todo_id),
    {payload, task_class: "user_gate", gate: true, required: [], targets: []}))];
  return projectTodoQuotaPlanning({schema_version: "todo_quota_planning_request_v2",
    current_time: "2026-10-01T00:00:00Z", ...fields, selection: request(items, {available: []}),
    source_contract: {}, route_items: [], handoff_items: [],
    resume: {schema_version: "todo_resume_planning_request_v0", agent_id: "agent-a", item_limit: 8,
      has_deferred_count: false, has_visible_deferred_count: false, deferred_count: null,
      available_capabilities: null, sources: Object.fromEntries(["items", "backlog_items", "first_open_items",
        "deferred_items", "deferred_resume_candidates", "resume_blocked_items", "monitor_open_items",
        "current_agent_claimed_monitor_items", "claimed_monitor_open_items"].map(key => [key, []]))}});
}
test("the existing batch keeps full gate deadlines before transport caps, scope and equal-time source", () => {
  const early = {todo_id: "early", next_due_at: "2026-10-01T08:01:00.000001+08:00"};
  const monitors = [{todo_id: "expired", expires_at: "2026-10-01T00:00:00Z",
    next_due_at: "2026-10-01T00:00:01Z"}, {todo_id: "past", next_due_at: "2026-10-01T00:00:00Z"}, early,
    {...early, next_due_at: "2026-09-30T20:01:00.000001-04:00"}];
  const gates = Array.from({length: 25}, (_, i) => ({todo_id: `gate_${i}`,
    task_class: "user_gate", next_due_at: "2026-10-01T00:02:00Z"}));
  gates.push({todo_id: "bad", task_class: "user_gate", next_due_at: "2026-02-30T00:00:00Z"});
  const before = structuredClone({monitors, gates});
  const result = planningDeadline(monitors, gates);
  assert.deepEqual(result.frontier_deadline, {schema_version: "todo_frontier_deadline_v0", identity: "early",
    source: "continuous_monitor", next_due_at: "2026-10-01T00:01:00.000001+00:00", candidate_count: 26});
  assert.equal(((result.lanes as JsonObject).gate_items as JsonObject[]).length, 3);
  assert.deepEqual({monitors, gates}, before);
});
test("planning clock retains fresh projection extensions and rechecks stale/raw dates precisely", () => {
  const projection = {identity: "cached", source: "user_gate", next_due_at: "2026-10-01T08:01:00+08:00",
    candidate_count: 7, extension: {retained: true}};
  assert.deepEqual(planningDeadline([], [], {frontier_deadline: projection}).frontier_deadline, projection);
  assert.deepEqual(planningDeadline([], [{index: 0, task_class: "user_action", next_due_at:
    "2026-10-01T00:02:00Z"}], {frontier_deadline: projection, current_time: "2026-10-01T00:01:01Z"})
    .frontier_deadline, {schema_version: "todo_frontier_deadline_v0", identity: "0", source: "user_action",
      next_due_at: "2026-10-01T00:02:00+00:00", candidate_count: 1});
  assert.equal(planningDeadline([], [{}]).frontier_deadline, null);
  const timed = clockRequest([]);
  assert.throws(() => projectTodoQuotaPlanning({...timed, current_time: "2026-10-01T00:00:00Z"}), /clocks must match/);
  const current_time = "1970-01-01T00:01:40Z";
  const combined = projectTodoQuotaPlanning({...timed, current_time});
  assert.deepEqual(combined.handoff_lanes, projectTodoQuotaPlanning(timed).handoff_lanes);
  assert.deepEqual((combined.lanes as JsonObject).monitor_schedule_gap_items, []);
  assert.deepEqual((combined.lanes as JsonObject).gate_items, []);
  assert.equal((planningDeadline([], [{title: "Review", next_due_at: "1969-12-31T23:59:59.000001Z"}],
    {current_time: "1969-12-31T23:59:58Z"}).frontier_deadline as JsonObject).next_due_at,
    "1969-12-31T23:59:59.000001+00:00");
});
