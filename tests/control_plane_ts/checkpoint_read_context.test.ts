import {randomUUID} from "node:crypto";
import {tmpdir} from "node:os";
import {resolve} from "node:path";
import assert from "node:assert/strict";
import test from "node:test";
import {evaluateCheckpointReadContext as evaluate, evaluateResultWorkBinding as work, executeResultWorkBinding} from "../../loopx/control_plane/goals/checkpoint_read_context.ts";
import {settlementIdentity, type JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {selectEffectiveHeartbeatReceipt, heartbeatReceiptFactFromEvent} from "../../loopx/control_plane/quota/heartbeat_receipt_identity.ts";

const identity = {goal_id: "goal", agent_id: "agent", todo_id: "task", turn_instance_id: "turn", effect_id: "effect"};
test("completion replay preserves the exact managed key without aliasing another scope", () => {
  const scoped = settlementIdentity({goal_id: "goal", agent_id: "agent",
    todo_id: "todo_original", turn_instance_id: "original-turn"});
  const todo = {todo_id: "todo_original", claimed_by: "agent", status: "done",
    completion_turn_key: "original-turn"};
  const key = (snapshot: JsonObject = todo, bound: JsonObject = scoped) => work({
    phase: "completion_identity", goal_id: "goal", agent_id: "agent", todo_id: "todo_original",
    todo: snapshot, identity: bound,
  }).completion_turn_key;
  assert.equal(key(), "original-turn");
  assert.equal(key({...todo, completion_turn_key: scoped.effect_id}), scoped.effect_id);
  for (const changed of [{...todo, status: "open"}, {...todo, todo_id: "todo_other"},
    {...todo, claimed_by: "another-agent"}, {...todo, completion_turn_key: "another-turn"}]) {
    assert.equal(key(changed), scoped.effect_id);
  }
  assert.throws(() => key(todo, {...scoped, effect_id: "forged"}), /original scoped Turn/);
  assert.throws(() => key(todo, settlementIdentity({goal_id: "other", agent_id: "agent",
    todo_id: "todo_original", turn_instance_id: "original-turn"})), /original scoped Turn/);
});
test("managed result replay cannot submit a different candidate, successor or closeout", () => {
  const scoped = settlementIdentity({goal_id: "goal", agent_id: "agent",
    todo_id: "todo_original", turn_instance_id: "original-turn"});
  const todo = {todo_id: "todo_original", claimed_by: "agent", role: "agent", status: "done",
    completion_turn_key: "original-turn", completion_continuation: "active_goal", no_followup: false,
    successor_todo_ids: [], evidence: "retained", completion_receipt_id: "original-receipt"};
  const replay = (candidate: JsonObject) => work({phase: "completion_identity",
    goal_id: "goal", agent_id: "agent", todo_id: "todo_original", todo, identity: scoped, candidate}).replay;
  const candidate = {evidence: "retained", role: "agent"};
  assert.deepEqual(replay(candidate), {ok: true, completed: true, changed: false, idempotent_replay: true,
    dry_run: false, goal_id: "goal", todo_id: "todo_original", status: "done",
    completion_continuation: "active_goal", completion_receipt_id: "original-receipt", updated_at: null});
  for (const changed of [{evidence: "replacement"}, {next_agent_todo: "new"},
    {next_excluded_agents: ["other"]}, {no_followup: true}, {role: "user"}, {clear_claim: true}]) {
    assert.equal(replay({...candidate, ...changed}), null);
  }
});
const facts = {
  todos: [
    {todo_id: "task", status: "open", text: "Build a page", depends_on_todo_ids: ["upstream"]},
    {todo_id: "upstream", status: "done", evidence: "result:1", depends_on_todo_id: "ancestor"},
    {todo_id: "ancestor", status: "done", evidence: "basis:1"},
    {todo_id: "unrelated", status: "open"},
  ],
  frontmatter: {objective: "A minimal page", updated_at: "old"}, goal_prose: "Acceptance: page renders",
  acceptance: {revision: 1, contract: {criteria: ["page renders"]}},
  agent_vision: {summary: "Build the page"}, source: {authority: "legacy_markdown"},
};
const read = (overrides: JsonObject = {}) => evaluate({phase: "read", identity, facts,
  prior: {vision_checkpoint: {decision: "missing_required", satisfied: false}},
  dependency_todo_ids: [], read_context_id: "read-A", ...overrides});
const check = (receipt: unknown, overrides: JsonObject = {}) => evaluate({
  phase: "check", identity, facts, read_context_id: "read-A", receipt, ...overrides,
});

test("Todo-only recovery cannot supply a complete Goal checkpoint basis", () => {
  const incomplete = {...facts, frontmatter: {...facts.frontmatter,
    goal_source_completeness: "todo_projection_only"}};
  for (const purpose of ["delivery_result", "first_delivery", "supplement_checkpoint"]) {
    assert.equal(read({purpose, facts: incomplete, admitted_turn: true,
      ...(purpose !== "supplement_checkpoint" ? {prior: null} : {}),
      full_frontier: true, decision_scope: "goal"}).error_code, "checkpoint_goal_source_incomplete");
  }
  assert.throws(() => read({facts: {...facts, frontmatter: {...facts.frontmatter,
    goal_source_completeness: "unknown"}}}), /unsupported Goal source completeness/);
  assert.equal(read().ok, true); // Existing complete documents need no new marker.
  assert.equal(check(read().receipt, {facts: incomplete}).error_code, "checkpoint_goal_source_incomplete");
});

test("new direction admission is explicit, historical absence stays historical", () => {
  assert.equal(work({phase: "direction_policy", heartbeat_receipt: {details: {}}}).required, false);
  assert.equal(work({phase: "direction_policy", heartbeat_receipt: {details: {
    checkpoint_continuation: "current_agent_v1"}}}).required, true);
  for (const invalid of [null, false, "future"]) assert.throws(() => work({phase: "direction_policy",
    heartbeat_receipt: {details: {checkpoint_continuation: invalid}}}), /incomplete or unsupported/);
});

test("direction admission cannot be downgraded and replan ACK waits for a fresh read", () => {
  const receipt = {event_kind: "quota_should_run", run_id: "turn", details: {
    todo_id: "todo_fixture", checkpoint_continuation: "current_agent_v1"}};
  const entries = [receipt, {...receipt, details: {todo_id: "todo_fixture"}}]
    .map(value => ({value, fact: heartbeatReceiptFactFromEvent(value)}));
  assert.throws(() => selectEffectiveHeartbeatReceipt("goal", "agent", entries), /cannot be downgraded/);
  const heartbeat_receipt = receipt;
  const prior = {vision_checkpoint: {replan_stage: "awaiting_direction"}};
  assert.deepEqual(work({phase: "replan_stage", heartbeat_receipt, requested: true}), {deferred: true, qualify: false});
  assert.deepEqual(work({phase: "replan_stage", heartbeat_receipt, prior, has_read_context: true}), {deferred: false, qualify: true});
  assert.deepEqual(work({phase: "replan_stage", prior, has_read_context: true}), {deferred: false, qualify: false});
});

test("pending closeout intent belongs to the original completed Turn", () => {
  const todo = {status: "done", no_followup: false, completion_continuation: "active_goal",
    completion_requested_continuation: "no_followup", completion_turn_key: "turn"};
  const request = {phase: "direction_terminal_intent", identity, todo};
  assert.equal(work(request).no_followup, true);
  assert.equal(work({...request, identity: {...identity, effect_id: "other", turn_instance_id: "other"}}).no_followup, false);
  assert.equal(work({...request, todo: {...todo, status: "open"}}).no_followup, false);
  assert.equal(work({...request, todo: {...todo, completion_requested_continuation: "active_goal"}}).no_followup, false);
  assert.throws(() => work({...request, todo: {...todo, completion_requested_continuation: "invalid"}}), /completion_continuation/);
});

test("direction retry uses the existing Host policy and a replaced read retains attempts", () => {
  for (const count of [0, 1]) assert.equal(work({phase: "direction_dispatch", receipt: {
    direction_host_attempt_count: count}}).attempt, count + 1);
  assert.equal(work({phase: "direction_dispatch", receipt: {direction_stopped: true}}).error_code, "checkpoint_direction_stopped");
  assert.equal(work({phase: "direction_dispatch", receipt: {direction_host_attempt_count: -1}}).ok, false);
  const current = structuredClone(facts);
  Object.assign(current.todos[0], {status: "done", completion_turn_key: "turn"});
  const request = {purpose: "first_delivery", admitted_turn: true, prior: null, checkpoint_continuation: true,
    full_frontier: true, decision_scope: "goal", facts: current};
  const first = read(request).receipt as JsonObject;
  const replacement = read({...request, receipt: {...first, direction_host_attempt_count: 2,
    direction_host_failure: {schema_version: "loopx_turn_host_failure_v0", kind: "executor_timeout", attempt: 2,
      retryable: true, retry: {strategy: "same_configuration", max_attempts: 2, backoff_seconds: 10}}},
    read_context_id: "read-B"}).receipt as JsonObject;
  assert.equal(replacement.direction_host_attempt_count, 2);
  assert.equal(work({phase: "direction_dispatch", receipt: replacement}).ok, false);
});

test("default direction binds the post-result input and response without relabelling", () => {
  assert.equal(read({purpose: "first_delivery", admitted_turn: true, prior: null, checkpoint_continuation: true,
    full_frontier: true, decision_scope: "goal"}).error_code, "checkpoint_result_not_committed");
  const current = structuredClone(facts);
  Object.assign(current.todos[0], {status: "done", completion_turn_key: "turn"});
  const direction = read({purpose: "first_delivery", admitted_turn: true, prior: null, checkpoint_continuation: true,
    full_frontier: true, decision_scope: "goal", facts: current});
  assert.equal(direction.ok, true);
  const decision = {read_context_id: "read-A", vision_unchanged_reason: "Current obligations preserve the route."};
  const request = {phase: "direction_response", identity, receipt: direction.receipt, decision};
  assert.equal(work(request).ok, true);
  assert.equal(work({...request, decision: {...decision, read_context_id: "read-B"}}).error_code,
    "checkpoint_direction_binding_mismatch");
  assert.equal(work({...request, identity: {...identity, agent_id: "another"}}).error_code,
    "checkpoint_direction_binding_mismatch");
  assert.equal(work({...request, receipt: {...direction.receipt as JsonObject, decision_basis: {}}}).error_code,
    "checkpoint_direction_basis_corrupt");
  const missing = read({purpose: "first_delivery", admitted_turn: true, prior: null, checkpoint_continuation: true,
    full_frontier: true, decision_scope: "goal", facts: {...current, agent_vision: null}});
  assert.equal(work({...request, receipt: missing.receipt}).error_code, "checkpoint_direction_baseline_required");
  assert.equal(work({...request, receipt: missing.receipt,
    decision: {read_context_id: "read-A", agent_vision: {vision_patch: {vision_summary: "Use current evidence."}}}}).ok, true);
  const display = structuredClone(current);
  Object.assign(display.todos[3], {index: 90, source_section: "Display"});
  display.frontmatter.updated_at = "later";
  assert.equal(check(direction.receipt, {purpose: "first_delivery", facts: display}).ok, true);
  display.todos[3].status = "done";
  assert.equal(check(direction.receipt, {purpose: "first_delivery", facts: display}).error_code, "checkpoint_read_context_stale");
});

test("default work input survives cold restore and validation cannot be relabelled", () => {
  const result = read({purpose: "delivery_result", admitted_turn: true, prior: null, result_only: true});
  const receipt = result.receipt as JsonObject;
  assert.deepEqual(work({phase: "restore", identity, receipt}).basis, result.basis);
  const candidate = {artifact: "sha256:candidate-A"};
  const validation = {ok: true, contract: "verifier-A"};
  const binding = work({phase: "bind_validation", identity, receipt, candidate, validation}).binding;
  const request = {phase: "check_validation", identity, receipt, candidate, validation, binding};
  assert.equal(work(request).ok, true);
  for (const altered of [
    {receipt: {...receipt, read_context_id: "read-B"}},
    {candidate: {artifact: "sha256:candidate-B"}},
    {validation: {...validation, contract: "verifier-B"}},
  ]) assert.equal(work({...request, ...altered}).error_code, "delivery_validation_binding_mismatch");
  assert.equal(work({...request, identity: {...identity, agent_id: "another"}}).error_code,
    "delivery_work_identity_mismatch");
  assert.equal(work({phase: "restore", identity, receipt: {...receipt,
    work_basis: {...result.basis as JsonObject, todo: {text: "corrupt"}}}}).error_code,
    "delivery_work_basis_corrupt");
});

test("admission policy belongs to the owner and malformed history cannot become historical absence", () => {
  const policy = "turn_result_freshness_required_v1";
  for (const authority of ["legacy_markdown", "file_v0", "sqlite_v0", "postgresql_v0"]) {
    assert.deepEqual(work({phase: "admission", todo_id: "task", source_authority: authority}).policy, policy);
  }
  assert.equal(work({phase: "policy", heartbeat_receipt: {details: {result_freshness: policy}}}).required, true);
  assert.equal(work({phase: "policy", heartbeat_receipt: {details: {}}}).required, false);
  for (const malformed of [null, false, {required: false}, "future"]) {
    assert.throws(() => work({phase: "policy", heartbeat_receipt: {details: {result_freshness: malformed}}}),
      /incomplete or unsupported/);
  }
});

test("original historical Turn and Goal incarnation do not inherit later admissions", () => {
  const old = {event_kind: "quota_should_run", agent_id: "agent", run_id: "old", details: {todo_id: "task"}};
  const current = {...old, run_id: "new", details: {todo_id: "task", result_freshness: "turn_result_freshness_required_v1"}};
  const request = {phase: "legacy_admission", events: [old, current], todo_id: "task", agent_id: "agent",
    completion_turn_key: "old", identity: {...identity, turn_instance_id: "old"}, goal_ref: null};
  assert.equal(work(request).required, false);
  assert.equal(work({...request, completion_turn_key: null, identity: null}).error_code, "delivery_work_context_required");
  assert.equal(work({...request, events: [{...current, goal_ref: {goal_id: "goal", goal_instance_id: "another"}}]}).required, false);
});

test("successful result precedes current freshness while stop/execution ownership stays separate", () => {
  const result = read({purpose: "delivery_result", admitted_turn: true, prior: null, result_only: true});
  const current = structuredClone(facts);
  Object.assign(current.todos[0], {status: "done", completion_turn_key: "turn"});
  current.goal_prose = "Changed after successful result";
  assert.equal(check(result.receipt, {purpose: "delivery_result", facts: current}).result_committed, true);
  Object.assign(current.todos[0], {completion_turn_key: "another-turn"});
  assert.equal(check(result.receipt, {purpose: "delivery_result", facts: current}).error_code, "checkpoint_read_context_stale");
});

test("read captures the exact task, transitive upstream results and goal acceptance", () => {
  const result = read();
  const basis = result.basis as JsonObject;
  assert.equal(result.ok, true);
  assert.deepEqual((basis.dependencies as JsonObject[]).map(row => row.todo_id), ["ancestor", "upstream"]);
  assert.equal(check(result.receipt).ok, true);
});

test("an unqualified blocked replan needs the exact committed source and no debit before reread", () => {
  const bound = settlementIdentity({goal_id: "goal", agent_id: "agent", turn_instance_id: "turn",
    replan_obligation_id: "replan-1111111111111111"});
  const prior = {goal_id: "goal", agent_id: "agent", turn_instance_id: "turn",
    replan_obligation_id: bound.replan_obligation_id, delivery_outcome: "outcome_gap",
    progress_observation: {schema_version: "typed_progress_observation_v0", result_class: "blocked",
      work_item_id: bound.replan_obligation_id, blocker_id: "blocker:source", evidence_ids: ["evidence:read"]},
    vision_checkpoint: {decision: "unchanged_with_reason", satisfied: true}};
  const pending = {identity: bound, prior, writeback_committed: true, quota_spend_committed: false,
    semantic_replan_guard: {selected_obligation_id: bound.replan_obligation_id}};
  assert.equal(read(pending).ok, true);
  for (const changed of [{...pending, writeback_committed: false}, {...pending, quota_spend_committed: true},
    {...pending, semantic_replan_guard: {}}, {...pending, prior: {...prior, agent_id: "other-agent"}},
    {...pending, prior: {...prior, autonomous_replan_ack: {recorded: true}}}]) {
    assert.equal(read(changed).error_code, "checkpoint_context_not_missing");
  }
});

test("old receipts and identical content from a different provider lineage require reread", () => {
  const source = {...facts.source, store_identity: "file:first"};
  const receipt = read({facts: {...facts, source}}).receipt as JsonObject;
  assert.equal(check({...receipt, schema_version: "checkpoint_read_context_v0"}).error_code,
    "checkpoint_read_context_unknown_or_replaced");
  const changed = check(receipt, {facts: {...facts, source: {...source, store_identity: "file:restored"}}});
  assert.equal(changed.error_code, "checkpoint_read_context_stale");
  assert.deepEqual(changed.changed_components, ["source"]);
});

test("each changed decision input rejects without permitting an append", () => {
  const receipt = read().receipt;
  for (const [component, mutate] of [
    ["todo", (value: typeof facts) => {value.todos[0].text = "Build a form";}],
    ["todo", (value: typeof facts) => {value.todos[0].status = "done";}],
    ["todo", (value: typeof facts) => {value.todos[0].depends_on_todo_ids = ["unrelated"];}],
    ["dependencies", (value: typeof facts) => {value.todos[1].evidence = "result:2";}],
    ["dependencies", (value: typeof facts) => {value.todos[2].evidence = "basis:2";}],
    ["goal", (value: typeof facts) => {value.acceptance.revision++;}],
    ["goal", (value: typeof facts) => {value.goal_prose = "Acceptance: include authentication";}],
    ["agent_vision", (value: typeof facts) => {value.agent_vision.summary = "A different route";}],
    ["source", (value: typeof facts) => {value.source.authority = "sqlite_v0";}],
  ] as const) {
    const current = structuredClone(facts);
    mutate(current);
    const result = check(receipt, {facts: current});
    assert.equal(result.error_code, "checkpoint_read_context_stale");
    assert.ok((result.changed_components as string[]).includes(component));
    assert.equal(result.reread_required, true);
  }
});

test("an unrelated task and display/timestamp changes do not invalidate the basis", () => {
  const current = structuredClone(facts);
  current.todos[3].status = "done";
  current.frontmatter.updated_at = "new";
  Object.assign(current.todos[0], {index: 99, source_section: "Agent Todo"});
  assert.equal(check(read().receipt, {facts: current}).ok, true);
});

test("legacy User Todo requirements remain covered even without an assigned Todo id", () => {
  const basis = {...facts, todos: [...facts.todos, {role: "user", text: "Owner requires a keyboard check"}]};
  const receipt = read({facts: basis}).receipt;
  const current = {...basis, todos: [...facts.todos, {role: "user", text: "Owner requires a screen reader check"}]};
  const result = check(receipt, {facts: current});
  assert.equal(result.error_code, "checkpoint_read_context_stale");
  assert.deepEqual(result.changed_components, ["goal"]);
});

test("extra used results are captured; missing dependencies and duplicate identities fail closed", () => {
  const receipt = read({dependency_todo_ids: ["unrelated"]}).receipt;
  const current = structuredClone(facts);
  current.todos[3].status = "done";
  assert.equal(check(receipt, {facts: current}).error_code, "checkpoint_read_context_stale");
  assert.throws(() => read({dependency_todo_ids: ["absent"]}), /absent/);
  assert.throws(() => read({facts: {...facts, todos: [...facts.todos, facts.todos[0]]}}), /duplicate/);
  assert.throws(() => read({facts: {...facts, todos: facts.todos.slice(1)}}), /Todo is absent/);
});

test("parallel reads cannot relabel an old operation as the latest read", () => {
  const a = read().receipt;
  const b = read({read_context_id: "read-B"}).receipt;
  assert.equal(check(a).ok, true);
  assert.equal(check(b).error_code, "checkpoint_read_context_unknown_or_replaced");
  assert.equal(check(b, {read_context_id: "read-B"}).ok, true);
  assert.equal(check(a, {read_context_id: null}).error_code, "checkpoint_read_context_required");
  assert.equal(check(null).error_code, "checkpoint_read_context_unknown_or_replaced");
  for (const field of ["goal_id", "agent_id", "todo_id", "turn_instance_id", "effect_id"]) {
    assert.equal(check(a, {identity: {...identity, [field]: "different"}}).error_code,
      "checkpoint_read_context_identity_mismatch");
  }
});

test("obligations cover the full frontier and a completed checkpoint cannot acquire another receipt", () => {
  const scope = {...identity, todo_id: null, replan_obligation_id: "obligation"};
  const receipt = read({identity: scope}).receipt;
  const current = structuredClone(facts);
  current.todos[3].status = "done";
  assert.equal(check(receipt, {identity: scope, facts: current}).error_code, "checkpoint_read_context_stale");
  assert.equal(read({prior: {vision_checkpoint: {decision: "patched", satisfied: true}}}).error_code,
    "checkpoint_context_not_missing");
});

test("first delivery has independent admission and cannot reuse a supplement receipt", () => {
  const first = {purpose: "first_delivery", decision_scope: "agent_lane", prior: null, admitted_turn: true};
  const receipt = read(first).receipt;
  assert.equal(check(receipt, first).ok, true);
  assert.equal(read({...first, admitted_turn: false}).error_code, "checkpoint_first_delivery_not_admitted");
  assert.equal(read({...first, prior: {vision_checkpoint: {decision: "missing_required", satisfied: false}}}).ok, false);
  assert.equal(check(receipt).ok, false);
  assert.equal(check(read().receipt, first).ok, false);
});

test("first direction detects frontier membership while ignoring independent peer work", () => {
  const first = {purpose: "first_delivery", decision_scope: "agent_lane", prior: null, admitted_turn: true};
  const source = {...facts, todos: [...facts.todos, {todo_id: "peer", claimed_by: "other", status: "open"}] as JsonObject[]};
  const receipt = read({...first, facts: source}).receipt;
  const changed = structuredClone(source);
  changed.todos[4].status = "done";
  assert.equal(check(receipt, {...first, facts: changed}).ok, true);
  changed.todos.push({todo_id: "new", claimed_by: "agent", status: "open"});
  assert.deepEqual(check(receipt, {...first, facts: changed}).changed_components, ["frontier"]);
  changed.todos.pop();
  changed.todos[4].claimed_by = "agent";
  assert.deepEqual(check(receipt, {...first, facts: changed}).changed_components, ["frontier"]);
  const whole = read({...first, decision_scope: "goal", facts: source}).receipt;
  assert.equal(check(whole, {...first, decision_scope: "agent_lane", facts: source}).error_code,
    "checkpoint_read_context_scope_mismatch");
  assert.equal(check(whole, {...first, decision_scope: "goal", facts: changed}).ok, false);
});

test("first result and direction bind authority, lease expiry, purpose and unknown fields", () => {
  for (const purpose of ["first_delivery", "delivery_result"]) {
    const request = {purpose, decision_scope: "goal", prior: null, admitted_turn: true};
    const baseline = {...facts, execution_lease: {owner: "agent", active: true},
      source: {authority: "file_v0", store_identity: "store-1", registry_goal: {registered_agents: ["agent"]}}};
    const receipt = read({...request, facts: baseline}).receipt;
    for (const change of [
      {...baseline, execution_lease: {owner: "agent", active: false}},
      {...baseline, execution_lease: {owner: "other", active: true}},
      {...baseline, source: {...baseline.source, store_identity: "store-2"}},
      {...baseline, source: {...baseline.source, registry_goal: {registered_agents: []}}},
      {...baseline, todos: baseline.todos.map((row, i) => i === 0 ? {...row, future_business_rule: "changed"} : row)},
    ]) assert.equal(check(receipt, {...request, facts: change}).error_code, "checkpoint_read_context_stale");
    assert.equal(check(receipt, {...request, purpose: purpose === "first_delivery" ? "delivery_result" : "first_delivery"}).ok, false);
  }
});

test("an uncertain direction cannot acquire a replacement read or reexecute", () => {
  const request = {purpose: "first_delivery", decision_scope: "goal", prior: null, admitted_turn: true};
  const receipt = {...read(request).receipt as JsonObject, commit_attempt: {json_path: "unindexed"}};
  for (const result of [read({...request, receipt}), check(receipt, request)]) {
    assert.equal(result.error_code, "checkpoint_commit_unknown");
    assert.equal(result.reread_required, false);
  }
});

test("explicit quota pause rejects new direction reads and cached checks while preserving result history", () => {
  const current: JsonObject = structuredClone(facts);
  Object.assign((current.todos as JsonObject[])[0], {status: "done", completion_turn_key: "turn"});
  current.source = {...facts.source, registry_goal: {quota: {compute: 1}}};
  const request = {purpose: "first_delivery", admitted_turn: true, prior: null,
    checkpoint_continuation: true, full_frontier: true, decision_scope: "goal", facts: current};
  const admitted = read(request);
  assert.equal(admitted.ok, true);
  for (const compute of [0, -1]) {
    const paused = {...current, source: {...facts.source, registry_goal: {quota: {compute}}}};
    for (const rejected of [read({...request, facts: paused}),
      check(admitted.receipt, {...request, facts: paused})]) {
      assert.equal(rejected.ok, false);
      assert.equal(rejected.error_code, "checkpoint_goal_paused");
      assert.equal(rejected.reread_required, false);
    }
  }
  assert.equal(check(admitted.receipt, request).ok, true);
  assert.equal(read({...request, facts: {...current, source: facts.source}}).ok, true);
  // This guard belongs to pending direction; historical result recovery remains
  // with its existing result/receipt owner rather than being erased by a pause.
  assert.equal(read({purpose: "delivery_result", admitted_turn: true, prior: null,
    facts: {...facts, source: {...facts.source, registry_goal: {quota: {compute: 0}}}}}).ok, true);
});


test("admitted native validation recovers its passed outcome and holds uncertain execution", () => {
  const receipt = read({purpose: "delivery_result", admitted_turn: true, prior: null}).receipt as JsonObject;
  const terminal = {actor_agent_id: "agent", lease_expected_version: 1,
    requested_completion_turn_key: "effect", evidence: "candidate", validation_declaration: {validation_command_argv: ["verify"]}};
  const issued = {status: "execute_validation", validation_effect: {kind: "caller_validation", validation_argv: ["verify"]}};
  const begin = (saved: JsonObject, input = terminal, effect = issued) => work({phase: "begin_terminal_validation",
    identity, receipt: saved, terminal_input: input, execution_result: effect});
  const pending = begin(receipt);
  assert.equal(pending.effect, "run");
  assert.equal(begin(pending.receipt as JsonObject).error_code, "delivery_validation_outcome_unknown");
  const replacement = read({purpose: "delivery_result", admitted_turn: true, prior: null, receipt: pending.receipt});
  assert.equal(replacement.error_code, "delivery_validation_outcome_unknown");
  assert.equal(replacement.reread_required, false);
  const updates = {validation_receipt: {passed: true, exit_code: 0, validation_declaration_sha256: "original"}};
  const finish = work({phase: "finish_terminal_validation", identity, receipt: pending.receipt,
    terminal_input: terminal, execution_result: issued, updates});
  const passed = finish.receipt as JsonObject;
  assert.deepEqual(begin(passed).updates, updates);
  assert.equal(begin(passed).effect, "replay");
  for (const changed of [{...terminal, evidence: "replacement"}, {...terminal, actor_agent_id: "other"},
      {...terminal, lease_expected_version: 2}, {...terminal, validation_declaration: {validation_command_argv: ["replacement"]}},
      {...terminal, requested_completion_turn_key: "new-turn"}]) {
    assert.equal(begin(passed, changed).error_code, "delivery_validation_binding_mismatch");
  }
  assert.equal(begin({...passed, read_context_id: "replacement"}).error_code, "delivery_validation_binding_mismatch");
  assert.equal(begin(passed, terminal, {...issued, validation_effect: {...issued.validation_effect, validation_argv: ["replacement"]}}).error_code,
    "delivery_validation_binding_mismatch");
  const corrupt = structuredClone(passed);
  ((corrupt.terminal_validation_effect as JsonObject).updates as JsonObject).validation_receipt = {passed: true, exit_code: 5};
  assert.equal(begin(corrupt).error_code, "delivery_validation_receipt_corrupt");
  const failed = work({phase: "finish_terminal_validation", identity, receipt: pending.receipt,
    terminal_input: terminal, execution_result: issued, updates: {validation_receipt: {passed: false, exit_code: 4}}});
  assert.equal(begin(failed.receipt as JsonObject).effect, "run", "known failed validation remains legitimately retryable");
  assert.throws(() => work({phase: "finish_terminal_validation", identity, receipt: pending.receipt,
    terminal_input: terminal, execution_result: issued, updates: {...updates, todo_done: true}}), /non-effect fields/);
});

test("Legacy commit policy distinguishes original terminal upgrade from new RESULT", () => {
  const transaction = {schema_version: "loopx_todo_completion_transaction_result_v0", decision: "commit",
    completion_identity_key: "original", fence: {status: "done", reason: "same_turn_terminal_upgrade"},
    completion_state: {continuation: "no_followup"}};
  assert.deepEqual(work({phase: "legacy_commit_policy", transaction, todo: {evidence: "original"}, candidate: {evidence: "original"}}), {required: false, recover_terminal_lease: true});
  for (const changed of [{...transaction, decision: "execute_validation"},
    {...transaction, fence: {status: "open", reason: "not_terminal"}},
    {...transaction, completion_identity_key: null},
    {...transaction, completion_state: {continuation: "active_goal"}}]) {
    assert.deepEqual(work({phase: "legacy_commit_policy", transaction: changed}), {required: true, recover_terminal_lease: false});
  }
});

test("Legacy runtime closeout rejects a missing original quota settlement", async () => {
  await assert.rejects(executeResultWorkBinding({phase: "legacy_commit_policy", runtime_root: resolve(tmpdir(), "loopx-missing-settlement-" + randomUUID()),
    todo: {evidence: "original"}, candidate: {evidence: "original"},
    goal_id: "goal", todo_id: "task", agent_id: "agent", transaction: {
      schema_version: "loopx_todo_completion_transaction_result_v0", decision: "commit",
      completion_identity_key: "original", fence: {status: "done", reason: "lifecycle_reentry_terminal_upgrade"},
      completion_state: {continuation: "no_followup"}}}), /original quota identity and settled receipts/);
});

test("Legacy terminal closeout preserves committed result fields", () => {
  const transaction = {schema_version: "loopx_todo_completion_transaction_result_v0", decision: "commit",
    completion_identity_key: "original", fence: {status: "done", reason: "lifecycle_reentry_terminal_upgrade"},
    completion_state: {continuation: "no_followup"}};
  const todo = {evidence: "original", note: "original note", reason: "original reason"};
  for (const field of ["evidence", "note", "reason"]) {
    assert.equal(work({phase: "legacy_commit_policy", transaction, todo,
      candidate: {[field]: "replacement"}}).error_code, "delivery_validation_binding_mismatch");
  }
  assert.deepEqual(work({phase: "legacy_commit_policy", transaction, todo, candidate: {}}),
    {required: false, recover_terminal_lease: true});
});
