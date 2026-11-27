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

const evaluate = (operation: string, facts: JsonObject) => evaluateDecisionScope({
  schema_version: "todo_decision_scope_request_v0", operation, ...facts,
});
const requiredScopeRepair = {
  schema_version: "todo_decision_scope_repair_plan_v0",
  trigger: "required_decision_scope_projection_drift",
  recommended_mode: "repair_required_decision_scope_projection",
  blocked_action_scope: "todo_decision_scope_projection",
  allowed: true,
  notify: "DONT_NOTIFY",
  reason: "an agent todo requires a decision scope that does not resolve to a compatible open user_gate",
  repair_focus: "remove stale required_decision_scopes, create the explicit blocking user_gate, or correct its agent ownership; user_action remains non-blocking",
  spend_policy: "spend once only after the todo/gate projection repair is validated and written back",
};

test("repair plans preserve all four priorities for either input form", () => {
  const reasons = ["dangling_required_decision_scope", "required_decision_scope_target_mismatch",
    "standing_decision_order_unresolved", "multi_agent_user_gate_missing_scope"];
  const expected = [
    requiredScopeRepair,
    {...requiredScopeRepair, reason: "a scope-covering user gate explicitly targets a different Todo",
      repair_focus: "reconcile the exact target and required scope against owner intent; do not remove the requirement, retarget the gate, or invent approval merely to clear the conflict"},
    {...requiredScopeRepair, reason: "contradictory standing decisions have no established chronological winner",
      repair_focus: "reconcile decision chronology against explicit owner evidence; do not remove required_decision_scopes or invent approval merely to clear this conflict"},
    {...requiredScopeRepair, trigger: "user_gate_scope_projection_drift",
      recommended_mode: "repair_user_gate_scope_projection", blocked_action_scope: "todo_user_gate_scope_projection",
      reason: "a multi-agent user_gate lacks blocks_agent or explicit global_gate=true, so its blocking authority is ambiguous",
      repair_focus: "set blocks_agent to one registered agent, set global_gate=true for intentional goal-wide authority, or downgrade the item to user_action",
      spend_policy: "spend once only after the gate-scope projection repair is validated and written back"},
  ];
  for (const [index, plan] of expected.entries()) {
    const reason_codes = reasons.slice(0, index + 1);
    assert.deepEqual(evaluate("repair_hint", {reason_codes}).repair_plan, plan);
    const errors = reason_codes.map(reason_code => ({reason_code}));
    assert.deepEqual(evaluate("repair_hint", {consistency: {ok: false, errors}}).repair_plan, plan);
  }
  assert.deepEqual(evaluate("repair_hint", {reason_codes: []}).repair_plan, requiredScopeRepair);
  for (const ok of [true, 0, null, undefined]) {
    assert.equal(evaluate("repair_hint", {consistency: {ok, errors: [{reason_code: reasons[3]}]}}).repair_plan, null);
  }
});

test("consistency carries the plan only on the outer envelope of a failed projection", () => {
  assert.deepEqual(evaluate("consistency", request([])), {
    schema_version: "todo_decision_scope_result_v0",
    result: {schema_version: "required_decision_scope_consistency_v0", ok: false,
      status: "projection_repair_required", agent_id: "agent-a", checked_agent_todo_count: 1,
      checked_required_scope_count: 1, terminal_outcome_count: 0, standing_authority_match_count: 0,
      errors: [{reason_code: "dangling_required_decision_scope", agent_todo_id: "todo_work",
        required_scope: "write_scope:action:release", related_user_todo_ids: []}]},
    repair_plan: requiredScopeRepair,
  });
  assert.equal(Object.hasOwn(evaluate("consistency", request([gate])), "repair_plan"), false);
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

test("large mixed-kind snapshots retain owner scoping, error order, and receipt counts", () => {
  const batches = Array.from({length: 90}, (_, index) => {
    const owner = ["agent-a", "agent-b", null][index % 3];
    const other = owner === "agent-b" ? "agent-a" : "agent-b";
    const scope_key = `work-${index}`;
    const target = {...scope, scope_key};
    const approval = {...target, kind: "resource"};
    const conflict = {...target, kind: "production"};
    const action = {...target, kind: "other"};
    const agent_todo_id = `todo_work-${index}`;
    const targetIds = [`gate-z-${index}`, `gate-a-${index}`, `gate-z-${index}`];
    const actionIds = [`action-z-${index}`, `action-a-${index}`, `action-z-${index}`];
    const recipient = {blocks_agent: owner, global_gate: owner === null};
    return {
      item: {...item, todo_id: agent_todo_id, claimed_by: owner, required_decision_scopes: [target, approval, conflict, action]},
      users: [
        ...targetIds.map(todo_id => ({...gate, ...recipient, todo_id, decision_scope: target, unblocks_todo_id: "todo_other"})),
        ...actionIds.map(todo_id => ({todo_id, decision_scope: action})),
        {todo_id: "action_hidden_by_conflict", decision_scope: conflict},
      ],
      entries: [
        {...recipient, active: true, decision_scope: approval},
        {...recipient, active: false, decision_scope: conflict},
        {blocks_agent: other, active: true, decision_scope: conflict},
      ],
      conflicts: [
        {...recipient, decision_scope: conflict, source_todo_ids: ["receipt-z", "receipt-a", "receipt-z", false]},
        {blocks_agent: other, decision_scope: conflict, source_todo_ids: ["unrelated-receipt"]},
      ],
      errors: [
        {reason_code: "required_decision_scope_target_mismatch", agent_todo_id,
          required_scope: `write_scope:action:${scope_key}`, related_user_todo_ids: targetIds},
        {reason_code: "standing_decision_order_unresolved", agent_todo_id,
          required_scope: `production:action:${scope_key}`, related_user_todo_ids: ["receipt-a", "receipt-z"]},
        {reason_code: "non_blocking_user_action_scope_collision", agent_todo_id,
          required_scope: `other:action:${scope_key}`, related_user_todo_ids: actionIds},
      ],
    };
  });
  const unscoped = ["unscoped-z", "unscoped-a"];
  const users = batches.flatMap(batch => batch.users);
  const input = request([{todo_id: unscoped[0], is_gate: true}, ...users, {todo_id: unscoped[1], is_gate: true}], {
    agent_id: null,
    agent_items: batches.map(batch => batch.item),
    standing_authority: {entries: batches.flatMap(batch => batch.entries), conflicts: batches.flatMap(batch => batch.conflicts)},
  });
  assert.deepEqual(decisionScopeConsistency(input), {
    schema_version: "required_decision_scope_consistency_v0", ok: false, status: "projection_repair_required", agent_id: null,
    checked_agent_todo_count: batches.length, checked_required_scope_count: batches.length * 4,
    terminal_outcome_count: 0, standing_authority_match_count: batches.length,
    errors: [
      ...unscoped.map(user_todo_id => ({reason_code: "multi_agent_user_gate_missing_scope", user_todo_id,
        registered_agent_ids: ["agent-a", "agent-b"]})),
      ...batches.flatMap(batch => batch.errors),
    ],
  });
});

test("index keys preserve raw kind identity and still count invalid requirements", () => {
  const arrayKind = {...scope, kind: [scope.kind]};
  const work = {...item, required_decision_scopes: [
    {...scope, scope_key: "?"}, {...arrayKind, kind: [scope.kind]}, arrayKind, scope,
  ]};
  const result = decisionScopeConsistency(request([
    {...gate, decision_scope: arrayKind}, {...gate, decision_scope: {...scope, granularity: ["goal"]}},
  ], {agent_items: [work]}));
  assert.equal(result.checked_required_scope_count, 4);
  assert.deepEqual(result.errors, ["write_scope:action:?", "write_scope:action:release"].map(required_scope => ({
    reason_code: "dangling_required_decision_scope", agent_todo_id: "todo_work", required_scope, related_user_todo_ids: [],
  })));
});

test("standing authority and outcomes are only validated on their original branches", () => {
  const standing_authority = {entries: [false]};
  assert.equal(decisionScopeConsistency(request([], {agent_items: [], standing_authority})).ok, true);
  assert.throws(() => decisionScopeConsistency(request([], {standing_authority})), /decision scope row must be an object/);

  const agent_items = [{...item, decision_scope_outcomes: {}}];
  assert.equal(decisionScopeConsistency(request([gate], {agent_items})).ok, true);
  const approval = {...gate, active: true};
  const uncoercible = {...approval, decision_scope: {...scope, kind: {toString: null}}};
  assert.equal(decisionScopeConsistency(request([], {
    agent_items, standing_authority: {entries: [approval, uncoercible], conflicts: [uncoercible]},
  })).standing_authority_match_count, 1);
  assert.throws(() => decisionScopeConsistency(request([], {
    agent_items, standing_authority: {entries: [uncoercible, approval]},
  })), /convert object to primitive/);
  assert.throws(() => decisionScopeConsistency(request([], {agent_items})), /decision scope rows must be an array/);
});

test("batched relations preserve pair ordering and fail on unknown protocol operations", () => {
  const result = evaluateDecisionScope({schema_version: "todo_decision_scope_request_v0", operation: "relations", gates: [gate], items: [item, {...item, required_decision_scopes: []}]});
  const matrix = result.result as JsonObject[][];
  assert.deepEqual(matrix.map(row => row.map(relation => relation.state)), [["gate_covers_action", "independent"]]);
  assert.deepEqual(matrix.map(row => row.map(relation => relation.agent_todo_id)), [["todo_work", "todo_work"]]);
  assert.throws(() => evaluateDecisionScope({schema_version: "todo_decision_scope_request_v0", operation: "approve"}), /unsupported/);
});

test("retired scalar transport operations fail closed while internal rules remain available", () => {
  for (const operation of ["covers", "scope_relation", "exact_relation"]) {
    assert.throws(() => evaluateDecisionScope({schema_version: "todo_decision_scope_request_v0",
      operation, gate_scope: scope, required_scope: scope, gate, item}), /unsupported decision scope operation/);
  }
  assert.equal(decisionScopeCovers(scope, scope), true);
  assert.equal(todoGateRelation({...gate, unblocks_todo_id: "todo_other"}, item)?.state, "projection_repair_required");
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

test("an explicitly authored other scope is custom, never an unknown or cross-kind wildcard", () => {
  const custom = {kind: "other", granularity: "action", scope_key: "study_conflict_review"};
  assert.equal(decisionScopeCovers(custom, custom), true);
  assert.equal(decisionScopeCovers(custom, {...custom, scope_key: "study_method_review"}), false);
  const broad = {...custom, granularity: "global", scope_key: "*"};
  assert.equal(decisionScopeCovers(broad, custom), true);
  for (const kind of ["private_read", "write_scope", "resource", "production", "public_claim", "direction"]) {
    assert.equal(decisionScopeCovers(broad, {...custom, kind}), false);
    assert.equal(decisionScopeCovers({...broad, kind}, custom), false);
  }
  assert.equal(decisionScopeCovers({...broad, kind: "othre"}, custom), false);
  assert.equal(decisionScopeCovers(broad, {...custom, kind: "othre"}), false);
});
