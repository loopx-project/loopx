import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {decisionScopeConsistency, decisionScopeCovers, evaluateDecisionScope, projectTodoGateScopes, todoGateRelation} from "../../loopx/control_plane/todos/decision_scope.ts";
import {productionScaleCoordinationFixture} from "./production_scale_coordination_fixture.ts";

const scope = {kind: "write_scope", granularity: "action", scope_key: "release"};
const item: JsonObject = {todo_id: "todo_work", status: "open", claimed_by: "agent-a", required_decision_scopes: [scope]};
const gate: JsonObject = {todo_id: "todo_gate", status: "open", is_gate: true, blocks_agent: "agent-a", decision_scope: scope};
const request = (users: JsonObject[], extra: JsonObject = {}): JsonObject => ({
  agent_id: "agent-a", registered_agents: ["agent-a", "agent-b"], agent_items: [item], user_items: users, ...extra,
});

test("scope is exact or an explicit wildcard, not a prefix or a prose match", () => {
  for (const [patch, expected] of [
    [{scope_key: "*", granularity: "goal"}, true], [{scope_key: "release/*"}, false],
    [{kind: "private_read"}, false], [{granularity: "invalid"}, false],
  ] as const) assert.equal(decisionScopeCovers({...scope, ...patch}, scope), expected);
  assert.equal(decisionScopeCovers(scope, {...scope, granularity: "goal"}), false);
});

test("explicit recipient takes precedence over claim; another agent does not inherit it", () => {
  const claimed = {...gate, claimed_by: "agent-b"};
  assert.equal(decisionScopeConsistency(request([claimed])).ok, true);
  const other = decisionScopeConsistency(request([claimed], {agent_id: "agent-b", agent_items: [{...item, claimed_by: "agent-b"}]}));
  assert.equal((other.errors as JsonObject[])[0].reason_code, "required_decision_scope_gate_owner_mismatch");
  assert.equal(decisionScopeConsistency(request([{...claimed, global_gate: true}])).ok, true);
});

test("exact target conflicts cannot become scope-wide approval or be masked by another gate", () => {
  const conflicting = {...gate, unblocks_todo_id: "todo_other"};
  for (const gates of [[conflicting], [conflicting, gate], [gate, conflicting]]) {
    const result = decisionScopeConsistency(request(gates));
    assert.equal(result.ok, false);
    assert.equal(result.standing_authority_match_count, 0);
    assert.equal((result.errors as JsonObject[])[0].reason_code, "required_decision_scope_target_mismatch");
  }
  assert.equal(todoGateRelation(conflicting, item)?.state, "projection_repair_required");
  assert.equal(todoGateRelation({...gate, unblocks_todo_id: "todo_work"}, item)?.state, "gate_targets_todo");
});

test("nonblocking actions and closed gates do not satisfy a live requirement", () => {
  for (const patch of [{is_gate: false}, {status: "done", done: true}, {status: "deferred"}]) {
    assert.equal(decisionScopeConsistency(request([{...gate, ...patch}])).ok, false);
  }
  assert.equal(decisionScopeConsistency(request([gate])).standing_authority_match_count, 0);
});

test("terminal outcomes remain obligations: rejection requires a blocked target", () => {
  const rejected = {...item, decision_scope_outcomes: [{outcome: "reject", decision_scope: scope, source_todo_id: "todo_gate"}]};
  assert.equal(decisionScopeConsistency(request([], {agent_items: [rejected]})).ok, false);
  const blocked = decisionScopeConsistency(request([], {agent_items: [{...rejected, status: "blocked"}]}));
  assert.equal(blocked.ok, true);
  assert.equal(blocked.terminal_outcome_count, 1);
});

test("complete production-scale history preserves the conflict beyond display limits without mutation", () => {
  const fixture = productionScaleCoordinationFixture("goal-fixture");
  const records = fixture.projection.todos as JsonObject[];
  const input = request([...records.filter(row => row.role === "user").map(row => ({...row, is_gate: row.task_class === "user_gate"})),
    {...gate, unblocks_todo_id: "todo_other"}], {agent_items: [...records.filter(row => row.role === "agent"), item]});
  const before = JSON.stringify(input);
  const result = decisionScopeConsistency(input);
  assert.ok((result.errors as JsonObject[]).some(error => error.agent_todo_id === "todo_work" && error.reason_code === "required_decision_scope_target_mismatch"));
  assert.equal(JSON.stringify(input), before);
});

test("batched relations preserve pair ordering and fail on unknown protocol operations", () => {
  const result = evaluateDecisionScope({schema_version: "todo_decision_scope_request_v0", operation: "relations", gates: [gate], items: [item, {...item, required_decision_scopes: []}]});
  assert.deepEqual(result.result, [[todoGateRelation(gate, item), todoGateRelation(gate, {...item, required_decision_scopes: []})]]);
  assert.throws(() => evaluateDecisionScope({schema_version: "todo_decision_scope_request_v0", operation: "approve"}), /unsupported/);
});


test("gate scope projection qualifies every addressed live dependency without granting authority", () => {
  const independent = {...gate, decision_scope: undefined, unblocks_todo_id: "todo_other"};
  const project = (gates: JsonObject[]) => (projectTodoGateScopes({agent_id: "agent-a", gates, items: [item]}).items as JsonObject[])[0];
  assert.deepEqual(project([independent]), {schema_version: "todo_gate_scope_projection_v0",
    agent_id: "agent-a", todo_id: "todo_work", gate_count: 1, state: "independent"});
  for (const patch of [
    {unblocks_todo_id: "todo_work"}, {unblocks_todo_id: null}, {global_gate: true}, {decision_scope: scope},
  ]) assert.equal(project([independent, {...independent, ...patch}]).state, "blocked");
  for (const patch of [
    {status: "done", done: true}, {archive_state: "archive"}, {is_gate: false}, {blocks_agent: "agent-b"},
  ]) assert.equal(project([{...independent, ...patch}]).state, "clear");
  assert.equal(project([{...independent, status: "blocked"}]).state, "independent");
});
