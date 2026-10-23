import assert from "node:assert/strict";
import test from "node:test";
import {projectReceiptBoundWait, RECEIPT_BOUND_WAIT_REQUEST_SCHEMA} from "../../loopx/control_plane/quota/blocked_wait.ts";
import {evaluateTodoResumeConditions, TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION} from "../../loopx/control_plane/todos/resume_condition.ts";

function request() {
  return {schema_version: RECEIPT_BOUND_WAIT_REQUEST_SCHEMA, agent_id: "agent-a",
    todo_id: "todo_waiting", turn_instance_id: "host-turn-1", observed_at: "2026-01-01T00:00:00Z",
    todos: [{todo_id: "todo_waiting", role: "agent", task_class: "advancement_task",
      status: "open", archive_state: "active", claimed_by: "agent-a", resume_ready: false,
      resume_when: "monitor_changed:todo_monitor", resume_monitor_generation: 2,
      resume_condition: {schema_version: "todo_resume_condition_v0", kind: "monitor_changed",
        target_todo_id: "todo_monitor", target_status: "open", target_task_class: "continuous_monitor",
        resume_when: "monitor_changed:todo_monitor", baseline_generation: 2, material_change_generation: 2, satisfied: false}},
    {todo_id: "todo_monitor", role: "agent", task_class: "continuous_monitor",
      status: "open", archive_state: "active", material_change_generation: 2}]};
}

test("the current Turn retains a qualified dependency closeout and grants no work/spend", () => {
  const input = request(), before = structuredClone(input);
  const result = projectReceiptBoundWait(input);
  assert.equal(result.status, "recovery_required");
  const recovery = result.recovery as Record<string, unknown>;
  assert.equal(recovery.scope, "current_turn");
  assert.equal(recovery.turn_instance_id, input.turn_instance_id);
  assert.equal(recovery.binding_id, "todo_waiting");
  assert.equal(recovery.repair, "blocked_writeback");
  assert.equal((result.obligation as Record<string, unknown>).delivery_allowed, false);
  assert.deepEqual(input, before);
});

for (const [label, patch] of Object.entries({
  runnable: {resume_ready: true}, completed: {status: "done"}, monitor: {task_class: "continuous_monitor"},
  archived: {archive_state: "archive"}, foreign: {claimed_by: "agent-b"}, user: {role: "user"},
})) {
  test(`${label} cannot borrow the bound waiting closeout`, () => {
    const input = request();
    Object.assign(input.todos[0]!, patch);
    assert.deepEqual(projectReceiptBoundWait(input), {status: "none"});
  });
}

test("a fabricated, missing, or changed dependency cannot qualify blocked closeout", () => {
  const input = request();
  assert.throws(() => projectReceiptBoundWait({...input, todos: [input.todos[0]]}), /registered pending/);
  input.todos[1]!.material_change_generation = 3;
  assert.throws(() => projectReceiptBoundWait(input), /registered pending/);
});

for (const resume_when of ["resume_at:2026-01-01T00:10:00Z", "resume_at:2026-01-01T02:00:00Z",
  "pr_merged:example/project#1", "capacity_available:git_push"]) {
  test(`${resume_when} keeps its existing route instead of entering Todo-dependency recovery`, () => {
    const input = request();
    const waiting = {...input.todos[0]!, resume_when};
    const projection = evaluateTodoResumeConditions({
      schema_version: TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION,
      items: [waiting], source_items: [waiting], evaluated_at: input.observed_at,
      available_capabilities: [],
    });
    const condition = (projection.conditions as {condition: Record<string, unknown>}[])[0]!.condition;
    assert.equal(condition.satisfied, false);
    assert.deepEqual(projectReceiptBoundWait({...input, todos: [
      {...waiting, resume_condition: condition, resume_ready: false},
    ]}), {status: "none"});
  });
}

function externalWaitRequest(resume_when = "pr_merged:example/project#1") {
  const input = request();
  const waiting = {...input.todos[0]!, resume_when};
  const evaluated = evaluateTodoResumeConditions({
    schema_version: TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION,
    items: [waiting], source_items: [waiting], evaluated_at: input.observed_at,
    available_capabilities: [],
  });
  const condition = (evaluated.conditions as {condition: Record<string, unknown>}[])[0]!.condition;
  return {...input, goal_id: "fixture", replay_phase: "open", heartbeat_receipt: {
    goal_id: "fixture", agent_id: input.agent_id, todo_id: input.todo_id,
    run_id: input.turn_instance_id, status: "normal_run", details: {
      ok: true, should_run: true, must_attempt_work: true, delivery_allowed: true,
      quiet_noop_allowed: false, todo_id: input.todo_id,
      settlement_effect_id: `fixture:${input.agent_id}:${input.todo_id}:${input.turn_instance_id}`,
      semantic_replan_obligation_id: "",
    },
  }, todos: [
    {...waiting, resume_condition: condition, resume_ready: false},
  ], settlement_progress: {schema_version: "quota_settlement_progress_v0",
    state: "writeback_required", next_step: "durable_writeback"}};
}

for (const resume_when of ["resume_at:2026-01-01T00:10:00Z", "resume_at:2026-01-01T02:00:00Z",
  "pr_merged:example/project#1", "capacity_available:git_push"]) {
  for (const status of ["open", "deferred"]) {
    test(`${status} ${resume_when} retains only the original writeback duty`, () => {
      const input = externalWaitRequest(resume_when);
      input.todos[0]!.status = status;
      const before = structuredClone(input), result = projectReceiptBoundWait(input);
      assert.equal((result.recovery as Record<string, unknown>).repair, "durable_writeback");
      assert.equal((result.recovery as Record<string, unknown>).binding_id, input.todo_id);
      assert.equal((result.recovery as Record<string, unknown>).turn_instance_id, input.turn_instance_id);
      assert.equal((result.obligation as Record<string, unknown>).delivery_allowed, false);
      assert.equal((result.obligation as Record<string, unknown>).spend_allowed_now, undefined);
      assert.deepEqual(input, before);
    });
  }
}

test("ordinary receipts resolve their Todo from canonical details without a top-level mirror", () => {
  const input = externalWaitRequest();
  const {todo_id: _mirror, ...receipt} = input.heartbeat_receipt;
  const result = projectReceiptBoundWait({...input, heartbeat_receipt: receipt});
  assert.equal((result.recovery as Record<string, unknown>).repair, "durable_writeback");
});

for (const resume_when of ["resume_at:2026-01-01T00:10:00Z",
  "pr_merged:example/project#1", "capacity_available:git_push"]) {
  test(`an already satisfied ${resume_when} still owes the original writeback`, () => {
    const input = externalWaitRequest(resume_when);
    input.todos[0]!.resume_ready = true;
    Object.assign(input.todos[0]!.resume_condition as Record<string, unknown>, {satisfied: true});
    const result = projectReceiptBoundWait(input);
    assert.equal((result.recovery as Record<string, unknown>).repair, "durable_writeback");
    assert.equal((result.recovery as Record<string, unknown>).binding_id, input.todo_id);
    assert.equal((result.obligation as Record<string, unknown>).delivery_allowed, false);
  });
}

for (const status of ["open", "deferred"]) {
  test(`${status} due wait cannot replace an original successor replan`, () => {
    const input = externalWaitRequest("resume_at:2026-01-01T00:10:00Z");
    input.todos[0]!.status = status;
    input.todos[0]!.resume_ready = true;
    Object.assign(input.todos[0]!.resume_condition as Record<string, unknown>, {satisfied: true});
    input.heartbeat_receipt.details.semantic_replan_obligation_id = "replan-0000000000000001";
    assert.deepEqual(projectReceiptBoundWait(input),
      {status: "none"});
  });
}

for (const facts of [{ok: false}, {should_run: false}, {must_attempt_work: false},
  {delivery_allowed: false}, {quiet_noop_allowed: true}, {delivery_allowed: "true"}]) {
  test(`an original repair or denied guard cannot invent result closeout: ${JSON.stringify(facts)}`, () => {
    const input = externalWaitRequest();
    Object.assign(input.heartbeat_receipt.details, facts);
    assert.deepEqual(projectReceiptBoundWait(input), {status: "none"});
  });
}

for (const field of ["goal_id", "agent_id", "todo_id", "run_id"] as const) {
  test(`another receipt ${field} cannot supply work admission`, () => {
    const input = externalWaitRequest();
    input.heartbeat_receipt[field] = "other-identity";
    assert.deepEqual(projectReceiptBoundWait(input), {status: "none"});
  });
}

for (const receipt of [null, {goal_id: "fixture"}]) {
  test("missing original work admission cannot be inferred from an open binding", () => {
    assert.deepEqual(projectReceiptBoundWait({...externalWaitRequest(), heartbeat_receipt: receipt}), {status: "none"});
  });
}

test("another effect cannot lend its positive admission", () => {
  const input = externalWaitRequest();
  input.heartbeat_receipt.details.settlement_effect_id = "other-effect";
  assert.deepEqual(projectReceiptBoundWait(input), {status: "none"});
});

for (const patch of [{replay_phase: "settled"}, {replay_phase: null},
  {settlement_progress: null}, {settlement_progress: {schema_version: "other",
    state: "writeback_required", next_step: "durable_writeback"}}]) {
  test(`an external wait needs original unsettled receipt progress: ${JSON.stringify(patch)}`, () => {
    assert.deepEqual(projectReceiptBoundWait({...externalWaitRequest(), ...patch}), {status: "none"});
  });
}

for (const patch of [{claimed_by: "agent-b"}, {role: "user"}, {archive_state: "archive"},
  {task_class: "continuous_monitor"},
  {resume_condition: {kind: "pr_merged", resume_when: "pr_merged:example/project#2", satisfied: false}},
  {resume_condition: {kind: "unknown", resume_when: "pr_merged:example/project#1", satisfied: false}}]) {
  test(`an incompatible external wait cannot borrow writeback: ${JSON.stringify(patch)}`, () => {
    const input = externalWaitRequest();
    Object.assign(input.todos[0]!, patch);
    assert.deepEqual(projectReceiptBoundWait(input), {status: "none"});
  });
}


test("a deferred Todo closes its original Turn rather than selecting a replacement", () => {
  const input = request();
  input.todos[0].status = "deferred";
  const result = projectReceiptBoundWait(input);
  assert.equal(result.status, "recovery_required");
  const recovery = result.recovery as Record<string, unknown>;
  assert.equal(recovery.binding_id, "todo_waiting");
  assert.equal(recovery.turn_instance_id, "host-turn-1");
});

function completedRequest() {
  const input = {...request(), replay_phase: "settlement_pending",
    settlement_progress: {schema_version: "quota_settlement_progress_v0",
      state: "writeback_required", next_step: "durable_writeback"}};
  input.todos[0]!.status = "done";
  return input;
}

for (const state of ["writeback_required", "writeback_receipt_required"]) {
  for (const archive_state of ["active", "archive"]) {
    test(`verified completion retains ${state} for its ${archive_state} original row`, () => {
      const input = completedRequest();
      input.settlement_progress.state = state;
      input.todos[0]!.archive_state = archive_state;
      const before = structuredClone(input);
      const result = projectReceiptBoundWait(input);
      const recovery = result.recovery as Record<string, unknown>;
      assert.equal(recovery.repair, "durable_writeback");
      assert.equal(recovery.binding_id, input.todo_id);
      assert.equal(recovery.turn_instance_id, input.turn_instance_id);
      const obligation = result.obligation as Record<string, unknown>;
      assert.equal(obligation.delivery_allowed, false);
      assert.equal(obligation.spend_allowed_now, undefined);
      assert.deepEqual(input, before);
    });
  }
}

for (const replay_phase of [null, "open", "settled", "unknown"]) {
  test(`completion status without verified pending phase ${replay_phase} grants no recovery`, () => {
    assert.deepEqual(projectReceiptBoundWait({...completedRequest(), replay_phase}), {status: "none"});
  });
}

test("archival cannot hide the original completed Turn's verified spend duty", () => {
  const input = completedRequest();
  input.todos[0]!.archive_state = "archive";
  input.settlement_progress = {schema_version: "quota_settlement_progress_v0",
    state: "spend_required", next_step: "quota_spend"};
  const result = projectReceiptBoundWait(input);
  assert.equal((result.recovery as Record<string, unknown>).repair, "quota_spend");
  assert.equal((result.obligation as Record<string, unknown>).delivery_allowed, false);
});

for (const progress of [null, {},
  {schema_version: "other", state: "writeback_required", next_step: "durable_writeback"},
  {schema_version: "quota_settlement_progress_v0", state: "identity_required", next_step: "durable_writeback"},
  {schema_version: "quota_settlement_progress_v0", state: "writeback_required", next_step: "quota_spend"},
  {schema_version: "quota_settlement_progress_v0", state: "settled", next_step: null},
]) {
  test(`completion with unknown or incompatible progress ${JSON.stringify(progress)} grants no recovery`, () => {
    assert.deepEqual(projectReceiptBoundWait({...completedRequest(), settlement_progress: progress}), {status: "none"});
  });
}

for (const patch of [{claimed_by: "agent-b"}, {role: "user"}, {task_class: "continuous_monitor"}]) {
  test(`a mismatched completed row ${JSON.stringify(patch)} cannot borrow settlement recovery`, () => {
    const input = completedRequest();
    Object.assign(input.todos[0]!, patch);
    assert.deepEqual(projectReceiptBoundWait(input), {status: "none"});
    assert.deepEqual(projectReceiptBoundWait({...input, todos: []}), {status: "none"});
    assert.deepEqual(projectReceiptBoundWait({...input, todos: [input.todos[0], input.todos[0]]}), {status: "none"});
  });
}

for (const state of ["spend_required", "spend_receipt_required"]) {
  test(`${state} preserves durable writeback before projecting a later dependency wait`, () => {
    const input = {...request(), settlement_progress: {schema_version: "quota_settlement_progress_v0",
      state, next_step: "quota_spend"}};
    const before = structuredClone(input);
    const result = projectReceiptBoundWait(input);
    assert.equal((result.recovery as Record<string, unknown>).repair, "quota_spend");
    assert.equal((result.recovery as Record<string, unknown>).binding_id, input.todo_id);
    assert.equal((result.recovery as Record<string, unknown>).turn_instance_id, input.turn_instance_id);
    assert.equal((result.obligation as Record<string, unknown>).delivery_allowed, false);
    assert.equal((result.obligation as Record<string, unknown>).spend_allowed_now, true);
    assert.deepEqual(input, before);
  });
}

for (const progress of [null, {},
  {schema_version: "quota_settlement_progress_v0", state: "identity_required", next_step: "validation"},
  {schema_version: "quota_settlement_progress_v0", state: "writeback_required", next_step: "durable_writeback"},
  {schema_version: "quota_settlement_progress_v0", state: "writeback_receipt_required", next_step: "durable_writeback"},
  {schema_version: "quota_settlement_progress_v0", state: "settled", next_step: null},
  {schema_version: "quota_settlement_progress_v0", state: "spend_required", next_step: "durable_writeback"},
  {schema_version: "other", state: "spend_required", next_step: "quota_spend"},
]) {
  test(`incomplete or inapplicable receipt progress ${JSON.stringify(progress)} retains blocked no-spend`, () => {
    const result = projectReceiptBoundWait({...request(), settlement_progress: progress});
    assert.equal((result.recovery as Record<string, unknown>).repair, "blocked_writeback");
    assert.equal((result.obligation as Record<string, unknown>).spend_allowed_now, undefined);
    assert.equal((result.obligation as Record<string, unknown>).spend_policy, "no spend for blocked closeout");
  });
}
