import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";

import { interpretQuotaShouldRunPacket } from "../../loopx/control_plane/effect_program.ts";
import {
  decodeInteractionContract,
  projectInteractionRequiredReads,
  projectInteractionWorkContext,
  projectBlockedSuccessorWaitObservation,
  type AgentInteractionChannel,
} from "../../loopx/control_plane/work_items/interaction_contract.ts";
import { evaluateTodoResumeConditions } from "../../loopx/control_plane/todos/resume_condition.ts";

test("only a positively qualified unbound scheduled wait can omit observation", () => {
  const todo = {todo_id: "todo_scheduled", status: "deferred", role: "agent",
    task_class: "advancement_task", resume_when: "resume_at:2099-01-01T00:00:00Z"};
  const evaluated = evaluateTodoResumeConditions({schema_version: "todo_resume_evaluation_request_v0",
    items: [todo], source_items: [], evaluated_at: "2026-09-01T00:00:00Z"});
  const condition = (evaluated.conditions as JsonObject[])[0].condition as JsonObject;
  const wait = {schema_version: "goal_vision_wait_state_v0", state: "waiting",
    reason_code: "exact_blocked_successor", automatic_resume: true, agent_id: "agent-a", waiting_todo_count: 1,
    selected_todo_id: todo.todo_id, selected_todo_status: "deferred",
    resume_when: todo.resume_when, resume_condition: condition};
  const request = {effective_action: "agent_scope_wait", agent_id: "agent-a", vision_wait_state: wait};
  assert.deepEqual(projectBlockedSuccessorWaitObservation(request), {required: false});
  for (const status of ["committed", "replayed"]) {
    assert.deepEqual(projectBlockedSuccessorWaitObservation({...request,
      heartbeat_receipt: {status, settlement_binding_owed: true}}), {required: false});
  }
  for (const patch of [
    {heartbeat_receipt: {status: "committed", settlement_identity: {todo_id: todo.todo_id}}},
    {heartbeat_receipt: {status: "committed", settlement_identity: {todo_id: "todo_other"}}},
    {heartbeat_receipt: {status: "committed", pending_action_selection: {todo_id: "todo_other"}}},
    {heartbeat_receipt: {status: "unknown"}},
    {heartbeat_receipt: "invalid"},
    {agent_id: "agent-foreign"},
    {vision_wait_state: {...wait, waiting_todo_count: 2}},
    {vision_wait_state: {...wait, selected_todo_status: "done"}},
    {vision_wait_state: {...wait, resume_condition: {...condition, satisfied: true}}},
    {vision_wait_state: {...wait, resume_condition: {...condition, evaluated_at: "2099-01-01T00:00:00Z"}}},
    {vision_wait_state: {...wait, resume_condition: {...condition, clock_provider: "author"}}},
    {vision_wait_state: {...wait, resume_condition: {...condition, scheduled_for: "2098-01-01T00:00:00Z"}}},
    {vision_wait_state: {...wait, resume_when: "resume_at:2099-01-01T00:00:00"}},
    {vision_wait_state: {...wait, resume_condition: {kind: "resume_at", satisfied: false}}},
    {vision_wait_state: {...wait, resume_when: "todo_done:todo_dependency",
      resume_condition: {kind: "todo_done", satisfied: false}}},
  ]) assert.deepEqual(projectBlockedSuccessorWaitObservation({...request, ...patch}), {required: true});
  assert.deepEqual(projectBlockedSuccessorWaitObservation({...request, effective_action: "normal_run"}), {required: false});
});

for (const kind of ["todo_done", "monitor_changed"]) {
  test(`only a known pending ${kind} source can omit unbound successor observation`, () => {
    const todo = {todo_id: "todo_waiting", status: "open", role: "agent",
      task_class: "advancement_task", resume_when: `${kind}:todo_dependency`, resume_monitor_generation: 3};
    const source = {todo_id: "todo_dependency", status: "open", role: "agent",
      task_class: kind === "todo_done" ? "advancement_task" : "continuous_monitor",
      archive_state: "active", material_change_generation: 3,
      next_due_at: "2099-01-01T00:00:00Z", expires_at: "2100-01-01T00:00:00Z"};
    const evaluate = (sources: JsonObject[]) => (evaluateTodoResumeConditions({
      schema_version: "todo_resume_evaluation_request_v0", items: [todo], source_items: sources,
      evaluated_at: "2026-09-01T00:00:00Z",
    }).conditions as JsonObject[])[0].condition as JsonObject;
    const condition = evaluate([source]);
    const wait = {schema_version: "goal_vision_wait_state_v0", state: "waiting",
      reason_code: "exact_blocked_successor", automatic_resume: true, agent_id: "agent-a", waiting_todo_count: 1,
      selected_todo_id: todo.todo_id, selected_todo_status: "open", selected_todo_claimed_by: "agent-a",
      resume_when: todo.resume_when, resume_monitor_generation: todo.resume_monitor_generation,
      resume_condition: condition};
    const request = {effective_action: "agent_scope_wait", agent_id: "agent-a", vision_wait_state: wait};
    const before = structuredClone(request);
    for (const heartbeat_receipt of [undefined, {status: "committed"}, {status: "replayed"}]) {
      assert.deepEqual(projectBlockedSuccessorWaitObservation({...request, heartbeat_receipt}), {required: false});
    }
    assert.deepEqual(request, before);
    const proofFields = kind === "todo_done"
      ? ["target_status", "target_task_class", "target_archive_state", "target_todo_id"]
      : ["target_status", "target_task_class", "baseline_generation", "material_change_generation", "target_todo_id",
        "target_archive_state", "target_next_due_at", "target_expires_at", "evaluated_at", "clock_provider"];
    const incomplete = proofFields.map(field => {
      const copy = {...condition};
      delete copy[field];
      return {vision_wait_state: {...wait, resume_condition: copy}};
    });
    for (const patch of [
      ...incomplete,
      {vision_wait_state: {...wait, resume_condition: evaluate([])}},
      {vision_wait_state: {...wait, resume_condition: evaluate([{...source, status: "done"}])}},
      {vision_wait_state: {...wait, resume_condition: {...condition, target_todo_id: "todo_waiting"}}},
      {vision_wait_state: {...wait, selected_todo_claimed_by: "other-agent"}},
      {vision_wait_state: {...wait, waiting_todo_count: 2}},
      {heartbeat_receipt: {status: "committed", settlement_identity: {todo_id: todo.todo_id}}},
      {heartbeat_receipt: {status: "replayed", pending_action_selection: {}}},
      {heartbeat_receipt: {status: "pending"}},
      {agent_id: "other-agent"},
      ...(kind === "monitor_changed" ? [
        {vision_wait_state: {...wait, resume_monitor_generation: undefined}},
        {vision_wait_state: {...wait, resume_monitor_generation: 2}},
        {vision_wait_state: {...wait, resume_condition: evaluate([{...source, material_change_generation: 4}])}},
        {vision_wait_state: {...wait, resume_condition: evaluate([{...source, next_due_at: undefined}])}},
        {vision_wait_state: {...wait, resume_condition: evaluate([{...source, expires_at: "2026-01-01T00:00:00Z"}])}},
        {vision_wait_state: {...wait, resume_condition: evaluate([{...source, expires_at: source.next_due_at}])}},
        {vision_wait_state: {...wait, resume_condition: evaluate([{...source, archive_state: "archived"}])}},
      ] : []),
    ]) {
      assert.deepEqual(projectBlockedSuccessorWaitObservation({...request, ...patch}), {required: true}, JSON.stringify(patch));
    }
  });
}

// The host-facing type cannot represent delivery without an agent attempt.
// @ts-expect-error delivery requires must_attempt=true
const invalidDeliveryChannel: AgentInteractionChannel = {
  must_attempt: false,
  delivery_allowed: true,
  quiet_noop_allowed: false,
};
void invalidDeliveryChannel;

test("a closed contract can notify without requiring user action or spending", () => {
  const contract = {schema_version: "loopx_interaction_contract_v0", mode: "terminal_no_followup",
    user_channel: {action_required: false, notify: "NOTIFY"},
    agent_channel: {must_attempt: false, delivery_allowed: false, quiet_noop_allowed: true},
    cli_channel: {next_cli_actions: ["no quota spend until explicit goal resume"]}};
  assert.deepEqual(decodeInteractionContract(contract), contract);
});

function readFacts(): JsonObject {
  return {arbitration_facts: {
    schema_version: "loopx_interaction_contract_v0", mode: "bounded_delivery",
    user_channel: {present: true, action_required: false},
    agent_channel: {present: true, must_attempt: true, delivery_allowed: true, quiet_noop_allowed: false},
    cli_channel: {}, agent_scope_frontier_actions: [],
  }, required_reads: [{command: "read-existing-evidence", source: "existing"}],
    goal_id: "goal-requirements", goal_state_file: "/tmp/goal source/state's requirements.md",
    command_prefix: "loopx --registry '/tmp/goal source/registry.json' --runtime-root '/tmp/goal source/runtime'",
    goal_acceptance_checks_present: true, should_run: true, delivery_allowed: true, selection_required: false,
    has_replan: false, settlement_only: false, effective_action: "normal_run",
    selected_todo: {todo_id: "todo_selected"}};
}

test("only participating hooks add unavailable context; generic guidance has no preference recipe", () => {
  const request = {required_reads: [], source_results: []};
  const inactive = projectInteractionWorkContext(request).work_context as JsonObject;
  assert.equal(inactive.unavailable_context, undefined);
  assert.doesNotMatch(String(inactive.instruction), /preference|empty view|empty observations/);
  assert.match(String(inactive.instruction), /this guard's fulfilled pre-work reads/);
  assert.match(String(inactive.instruction), /Recheck freshness before later actions/);
  const denied = projectInteractionWorkContext({...request, hook_dispatch: {results: [{
    hook_id: "optional.context", capability_id: "optional-context",
    status: "unavailable", error_code: "source_denied"}], failures: []}}).work_context as JsonObject;
  assert.deepEqual((denied.unavailable_context as JsonObject).affected_hooks, [{
    hook_id: "optional.context", capability_id: "optional-context",
    status: "unavailable", error_code: "source_denied"}]);
  assert.equal((denied.unavailable_context as JsonObject).dependent_action_policy, "hold_until_fresh_context");
  assert.equal((denied.unavailable_context as JsonObject).independent_work_policy, "preserve_existing_authority");
});

test("shared reads retain all Goal sources before exact current work and existing hooks", () => {
  const facts = readFacts();
  const before = structuredClone(facts);
  const reads = projectInteractionRequiredReads(facts).required_reads as JsonObject[];
  assert.deepEqual(reads.map(read => read.source), ["existing", "goal_state", "goal_acceptance", "selected_todo"]);
  assert.equal(reads[1].command, `cat -- '/tmp/goal source/state'"'"'s requirements.md'`);
  const prefix = String(facts.command_prefix);
  assert.equal(reads[2].command, `${prefix} --format json goal-acceptance inspect --goal-id goal-requirements`);
  assert.equal(reads[3].command, `${prefix} --format json todo list --goal-id goal-requirements --todo-id todo_selected`);
  assert.deepEqual(facts, before);
  // Existing identical reads are obligations already; do not duplicate them.
  facts.required_reads = reads;
  assert.deepEqual(projectInteractionRequiredReads(facts).required_reads, reads);
});

test("one Goal acceptance read retains every applicable objective and acceptance obligation", () => {
  const objectiveClauses = [
    "revision/digest",
    "own objective_work_digest",
    "adoption",
    "Preserve root acceptance, permissions and stops",
  ];
  const outcomeClauses = [
    "cumulative requirements",
    "switching work",
    "Agent review",
    "Scoped acceptance never replaces Goal intent",
    "fresh guard",
  ];
  const checkClauses = [
    "non-goals",
    "criteria",
    "scope/revision",
    "Scoped acceptance does not replace Goal intent",
    "fresh guard",
  ];
  const cases = [
    {goal_objective_present: false, goal_outcomes_present: true, checks: false, clauses: outcomeClauses},
    {goal_objective_present: false, goal_outcomes_present: false, checks: true, clauses: checkClauses},
    {goal_objective_present: true, goal_outcomes_present: true, checks: false,
      clauses: [...objectiveClauses, ...outcomeClauses]},
    {goal_objective_present: true, goal_outcomes_present: false, checks: true,
      clauses: [...objectiveClauses, ...checkClauses]},
  ];
  for (const current of cases) {
    const facts: JsonObject = {...readFacts(), goal_state_file: null, selected_todo: null,
      goal_objective_present: current.goal_objective_present,
      goal_outcomes_present: current.goal_outcomes_present,
      goal_acceptance_checks_present: current.checks};
    const projected = projectInteractionRequiredReads(facts);
    const reads = projected.required_reads as JsonObject[];
    const acceptanceReads = reads.filter(read => read.source === "goal_acceptance");
    assert.equal(acceptanceReads.length, 1, JSON.stringify(current));
    const reason = String(acceptanceReads[0]!.reason);
    for (const clause of current.clauses) assert.ok(reason.includes(clause), `${clause}: ${reason}`);

    const replay = projectInteractionRequiredReads({...facts, required_reads: reads});
    assert.deepEqual(replay.required_reads, reads, "repeated projection must not duplicate guidance");

    const inline = projectInteractionWorkContext({
      required_reads: acceptanceReads,
      source_results: [{command: acceptanceReads[0]!.command, content: {
        goal_acceptance_contract: {goal_objective: {revision: 1}, criteria: [{id: "check"}]},
      }}],
    });
    assert.deepEqual(inline.required_reads, []);
    const source = ((inline.work_context as JsonObject).sources as JsonObject[])[0]!;
    assert.equal(source.reason, reason);
  }

  const facts: JsonObject = {...readFacts(), goal_state_file: null, selected_todo: null,
    goal_objective_present: true, goal_outcomes_present: false, goal_acceptance_checks_present: true};
  const inspect = `${String(facts.command_prefix)} --format json goal-acceptance inspect --goal-id goal-requirements`;
  const existing = {command: inspect, source: "goal_acceptance", reason: "Retain this existing acceptance obligation."};
  const merged = (projectInteractionRequiredReads({...facts, required_reads: [existing]}).required_reads as JsonObject[]);
  assert.equal(merged.length, 1);
  assert.equal(merged[0]!.command, inspect);
  assert.ok(String(merged[0]!.reason).startsWith(existing.reason));
  assert.ok(String(merged[0]!.reason).includes(checkClauses[0]));
  assert.ok(String(merged[0]!.reason).includes(objectiveClauses[0]));
});

test("shared requirement reads use final admission and add no work on non-delivery lanes", () => {
  for (const patch of [{should_run: false}, {delivery_allowed: false}, {selection_required: true},
    {settlement_only: true, has_replan: true}, {goal_id: null}, {effective_action: "governed_capability_intent"}]) {
    const facts: JsonObject = {...readFacts(), ...patch};
    assert.deepEqual(projectInteractionRequiredReads(facts).required_reads, facts.required_reads);
  }
  const replan = {...readFacts(), has_replan: true};
  assert.deepEqual((projectInteractionRequiredReads(replan).required_reads as JsonObject[]).map(read => read.source),
    ["existing", "goal_state", "goal_acceptance"]);
});

test("shared reads follow adaptive primary and never fabricate a missing Goal source", () => {
  const facts: JsonObject = {...readFacts(), goal_state_file: null, goal_acceptance_checks_present: false,
    task_orchestration_contract: {schema_version: "task_orchestration_contract_v2", mode: "adaptive", primary_todo_id: "todo_primary"}};
  const reads = projectInteractionRequiredReads(facts).required_reads as JsonObject[];
  assert.deepEqual(reads.map(read => read.source), ["existing", "selected_todo"]);
  assert.match(String(reads[1].command), /--todo-id todo_primary$/);
  facts.selected_todo = null;
  facts.task_orchestration_contract = {};
  assert.deepEqual(projectInteractionRequiredReads(facts).required_reads, facts.required_reads);
  for (const patch of [{delivery_allowed: "true"}, {goal_state_file: "bad\0path"},
    {required_reads: [{command: "bad\0command"}]}]) {
    assert.throws(() => projectInteractionRequiredReads({...readFacts(), ...patch}));
  }
});

function successorReplanContract(): Record<string, unknown> {
  return {
    schema_version: "loopx_interaction_contract_v0",
    mode: "successor_replan_required",
    user_channel: {
      action_required: false,
      notify: "NOTIFY",
      non_blocking: true,
      actions: ["Review the optional setting."],
    },
    agent_channel: {
      must_attempt: true,
      delivery_allowed: false,
      quiet_noop_allowed: false,
      primary_action: "reopen the ready deferred successor",
    },
    cli_channel: {
      next_cli_actions: [
        "loopx todo update --todo-id todo_ready_deferred --status open",
      ],
      spend_allowed_now: false,
    },
  };
}

test("inline context fulfills every source without truncating acceptance or inventing authority", () => {
  const reads = Array.from({length: 7}, (_, i) => ({command: `read-${i}`, source: "registered_hook"}));
  const tail = "Retain the final acceptance and stop before deployment.";
  const request: JsonObject = {required_reads: reads,
    source_results: reads.map(read => ({command: read.command, content: {text: "full source ".repeat(10_000) + tail}})),
    user_todos: {todos: [{todo_id: "todo_decision", role: "user", text: "Approve delivery.", blocks_agent: "agent-a"}]}};
  const before = structuredClone(request);
  const result = projectInteractionWorkContext(request);
  assert.deepEqual(result.required_reads, []);
  const context = result.work_context as JsonObject;
  assert.equal(context.complete, true);
  assert.equal((context.sources as JsonObject[]).length, 7);
  assert.ok(String(((context.sources as JsonObject[])[6].content as JsonObject).text).endsWith(tail));
  assert.deepEqual(context.user_todos, request.user_todos);
  assert.equal(result.delivery_allowed, undefined);
  assert.deepEqual(request, before);
});

test("failed, ambiguous or changed work never fulfills a pre-work read", () => {
  const read = {command: "read-todo", source: "selected_todo"};
  for (const result of [{error_code: "unavailable"}, {content: {matched: false, todo: null}},
    {content: {matched: true, todo: {todo_id: "todo_other", status: "open"}}},
    {content: {matched: true, todo: {todo_id: "todo_work", status: "done"}}},
    {content: {matched: true, todo: {todo_id: "todo_work", status: "open", claimed_by: "peer"}}}]) {
    const projected = projectInteractionWorkContext({required_reads: [read],
      selected_todo: {todo_id: "todo_work", status: "open", claimed_by: "agent-a"},
      source_results: [{command: read.command, ...result}]});
    assert.deepEqual(projected.required_reads, [read]);
    assert.equal((projected.work_context as JsonObject).complete, false);
  }
  const originalRevision = `sha256:${"a".repeat(64)}`;
  const changedRevision = `sha256:${"b".repeat(64)}`;
  const revisionChanged = projectInteractionWorkContext({required_reads: [read],
    selected_todo: {todo_id: "todo_work", status: "open", claimed_by: "agent-a", content_revision: originalRevision},
    source_results: [{command: read.command, content: {matched: true, todo: {
      todo_id: "todo_work", status: "open", claimed_by: "agent-a", content_revision: changedRevision,
    }}}]});
  assert.deepEqual(revisionChanged.required_reads, [read]);
  assert.equal((revisionChanged.work_context as JsonObject).complete, false);
  const revisionMissing = projectInteractionWorkContext({required_reads: [read],
    selected_todo: {todo_id: "todo_work", status: "open", claimed_by: "agent-a"},
    source_results: [{command: read.command, content: {matched: true, todo: {
      todo_id: "todo_work", status: "open", claimed_by: "agent-a", content_revision: originalRevision,
    }}}]});
  assert.deepEqual(revisionMissing.required_reads, [read]);
  assert.equal((revisionMissing.work_context as JsonObject).complete, false);
});

test("selected Todo references its admitted body only after an exact detail read", () => {
  const read = {command: "read-todo", source: "selected_todo"};
  const text = "Keep full acceptance.";
  const contentRevision = `sha256:${createHash("sha256").update(text, "utf8").digest("hex")}`;
  const task = {todo_id: "todo_work", status: "open", claimed_by: "agent-a",
    text, content_revision: contentRevision, note: "Do not publish before the final review."};
  const selected = {todo_id: task.todo_id, status: task.status, text: task.text,
    content_revision: contentRevision};
  const projected = projectInteractionWorkContext({required_reads: [read], selected_todo: selected,
    source_results: [{command: read.command, content: {matched: true, todo: task}}]});
  const context = projected.work_context as JsonObject;
  assert.equal(context.complete, true);
  assert.equal(context.selected_todo_ref, "selected_todo");
  assert.equal(context.selected_todo_text_verified, true);
  const sources = context.sources as JsonObject[];
  assert.equal(sources.length, 1);
  assert.equal(sources[0].source, "selected_todo");
  const sourceContent = sources[0].content as JsonObject;
  const sourceTodo = sourceContent.todo as JsonObject;
  assert.equal(sourceTodo.text, undefined);
  assert.equal(sourceTodo.claimed_by, task.claimed_by);
  assert.equal(sourceTodo.note, task.note);

  const changedBody = projectInteractionWorkContext({required_reads: [read], selected_todo: selected,
    source_results: [{command: read.command, content: {matched: true, todo: {...task, text: "Changed body"}}}]});
  assert.deepEqual(changedBody.required_reads, [read]);
  assert.equal((changedBody.work_context as JsonObject).complete, false);
});

test("bounded selected text validates its full source snapshot and preserves exact detail", () => {
  const read = {command: "read-todo", source: "selected_todo"};
  const body = "Keep every query result and its final acceptance condition.";
  const contentRevision = `sha256:${createHash("sha256").update(body, "utf8").digest("hex")}`;
  const task = {todo_id: "todo_work", status: "open", claimed_by: "agent-a", text: body,
    content_revision: contentRevision,
    note: "The final condition is to retain cancellation behavior."};
  const selected = {...task, text: "Keep every query result", _context_text_sha256:
    createHash("sha256").update(body, "utf8").digest("hex")};
  const projected = projectInteractionWorkContext({required_reads: [read], selected_todo: selected,
    source_results: [{command: read.command, content: {matched: true, todo: task}}]});
  const context = projected.work_context as JsonObject;
  assert.equal(context.complete, true);
  assert.equal(context.selected_todo_ref, undefined);
  assert.equal(context.selected_todo_text_verified, true);
  const source = (context.sources as JsonObject[])[0];
  const sourceContent = source.content as JsonObject;
  assert.equal((sourceContent.todo as JsonObject).text, body);
  assert.equal((sourceContent.todo as JsonObject).note, task.note);

  const changed = projectInteractionWorkContext({required_reads: [read], selected_todo: selected,
    source_results: [{command: read.command, content: {matched: true,
      todo: {...task, text: body + " Updated after selection."}}}]});
  assert.equal((changed.work_context as JsonObject).complete, false);
  assert.equal((changed.work_context as JsonObject).selected_todo_text_verified, undefined);
  assert.deepEqual(changed.required_reads, [read]);
});

test("mixed Goal document remains a full progressive read without dropping task requirements", () => {
  const goalRead = {command: "cat -- state.md", source: "goal_state", ordering: "before_work"};
  const taskRead = {command: "read-todo", source: "selected_todo"};
  const taskRevision = `sha256:${"a".repeat(64)}`;
  const task = {todo_id: "todo_work", status: "open", claimed_by: "agent-a", content_revision: taskRevision,
    text: "Preserve every requirement. ".repeat(400) + "Stop before deployment."};
  const projected = projectInteractionWorkContext({required_reads: [goalRead, taskRead],
    selected_todo: task, source_results: [
      {command: goalRead.command, content: {text: "Goal intent, other tasks and historical evidence."}},
      {command: taskRead.command, content: {matched: true, todo: task,
        authority_read: {source_authority: "markdown_active_state", provider_revision: "sha256:revision"}}},
    ]});
  assert.deepEqual(projected.required_reads, [goalRead]);
  const context = projected.work_context as JsonObject;
  assert.equal(context.complete, true); // No source failure; pending reads still apply.
  assert.equal(context.selected_todo_ref, "selected_todo");
  assert.equal(context.selected_todo_authority, "markdown_active_state@sha256:revision");
  assert.equal(context.sources, undefined);
  assert.equal(task.text, "Preserve every requirement. ".repeat(400) + "Stop before deployment.");
  const failed = projectInteractionWorkContext({required_reads: [goalRead],
    source_results: [{command: goalRead.command, error_code: "unavailable"}]});
  assert.deepEqual(failed.required_reads, [goalRead]);
  assert.equal((failed.work_context as JsonObject).complete, false);
});

test("decodes non-delivery successor work as a required agent channel", () => {
  const contract = decodeInteractionContract(successorReplanContract());

  assert.equal(contract.user_channel.action_required, false);
  assert.equal(contract.user_channel.non_blocking, true);
  assert.equal(contract.agent_channel.must_attempt, true);
  assert.equal(contract.agent_channel.delivery_allowed, false);
  assert.equal(contract.agent_channel.quiet_noop_allowed, false);
});

test("Effect interpretation consumes the decoded interaction contract", () => {
  const turn = interpretQuotaShouldRunPacket({
    decision: "successor_replan_required",
    should_run: true,
    effective_action: "successor_replan_required",
    recommended_action: "Reopen the ready successor.",
    interaction_contract: successorReplanContract(),
    work_lane_contract: {},
    scheduler_hint: { action: "run_now", cadence_class: "active_work" },
  });

  assert.equal(
    turn.interpretation.interaction_mode,
    "successor_replan_required",
  );
  assert.deepEqual(turn.next_effect.cli_actions, [
    "loopx todo update --todo-id todo_ready_deferred --status open",
  ]);
});

test("rejects contradictory host-facing channel states", () => {
  const requiredNotice = successorReplanContract();
  requiredNotice.user_channel = {
    action_required: true,
    notify: "NOTIFY",
    non_blocking: true,
  };
  assert.throws(
    () => decodeInteractionContract(requiredNotice),
    /both required and non-blocking/,
  );

  const falseNonBlockingMarker = successorReplanContract();
  falseNonBlockingMarker.user_channel = {
    action_required: false,
    notify: "NOTIFY",
    non_blocking: false,
  };
  assert.throws(
    () => decodeInteractionContract(falseNonBlockingMarker),
    /non_blocking must be true when present/,
  );

  const deliveryWithoutAttempt = successorReplanContract();
  deliveryWithoutAttempt.agent_channel = {
    must_attempt: false,
    delivery_allowed: true,
    quiet_noop_allowed: false,
  };
  assert.throws(
    () => decodeInteractionContract(deliveryWithoutAttempt),
    /delivery without an attempt/,
  );

  const quietRequiredAction = successorReplanContract();
  quietRequiredAction.agent_channel = {
    must_attempt: false,
    delivery_allowed: false,
    quiet_noop_allowed: true,
  };
  quietRequiredAction.user_channel = {
    action_required: true,
    notify: "NOTIFY",
  };
  assert.throws(
    () => decodeInteractionContract(quietRequiredAction),
    /quiet no-op conflicts/,
  );
});

test("scoped override preserves admission context without selecting new work", async () => {
  const {projectScopedOverride} = await import("../../loopx/control_plane/quota/scoped_override.ts");
  const override = {kind: "agent_scoped_user_gate_override", from_state: "operator_gate",
    to_state: "eligible", selected_action: "old candidate"};
  const interaction = successorReplanContract();
  const selected = {todo_id: "todo_bound", text: "Current work", selection_binding: "heartbeat_receipt"};
  const before = structuredClone({override, selected});
  assert.deepEqual(projectScopedOverride({override, selected_todo: selected,
    interaction_contract: interaction}), {kind: override.kind, from_state: "operator_gate", to_state: "eligible"});
  interaction.agent_channel = {must_attempt: true, delivery_allowed: true, quiet_noop_allowed: false};
  assert.equal(projectScopedOverride({override, selected_todo: selected,
    interaction_contract: interaction}).selected_action, "Current work");
  assert.equal(projectScopedOverride({override, selected_todo: null,
    interaction_contract: interaction}).selected_action, undefined);
  assert.deepEqual({override, selected}, before);
  assert.throws(() => projectScopedOverride({override, selected_todo: {text: "No identity"},
    interaction_contract: interaction}), /selected_todo.todo_id/);
});


test("an inline unfinished agenda cannot fulfill the full Inbox read", () => {
  const read = {kind: "operator_inbox", command: "loopx manager-inbox read", hook_id: "manager.context_inbox"};
  const content = {ok: true, read_complete: false, unfinished_delegations: {items: [{request_id: "pending"}]}};
  const result = projectInteractionWorkContext({required_reads: [read], source_results: [{command: read.command, content}]});
  assert.deepEqual(result.required_reads, [read]);
  const context = result.work_context as JsonObject;
  assert.equal(context.complete, true);
  assert.deepEqual((context.sources as JsonObject[])[0].content, content);
});


test("declared outcomes remain required reads without enabled checks after a work switch", () => {
  const facts = {...readFacts(), goal_acceptance_checks_present: false, goal_outcomes_present: true, selected_todo: {todo_id: "todo_other"}};
  const reads = projectInteractionRequiredReads(facts).required_reads as JsonObject[];
  const acceptance = reads.find(read => read.source === "goal_acceptance");
  assert.ok(acceptance);
  assert.match(String(acceptance.reason), /cumulative requirements/);
  const off = projectInteractionRequiredReads({...facts, goal_outcomes_present: false}).required_reads as JsonObject[];
  assert.ok(!off.some(read => read.source === "goal_acceptance"));
});


test("Goal narrative fulfillment needs complete source and ordered region lineage", () => {
  const read = {source: "goal_state", command: "cat -- goal.md"};
  const region = {role: "agent", start_line: 4, end_line: 8, revision: `sha256:${"b".repeat(64)}`};
  const content = {state_file: "goal.md", text: "Keep original goal and final stops.\n",
    revision: `sha256:${"a".repeat(64)}`, excluded_todo_regions: [region]};
  const project = (value: JsonObject) => projectInteractionWorkContext({
    required_reads: [read], source_results: [{command: read.command, content: value}],
  });
  const fulfilled = project(content);
  assert.deepEqual(fulfilled.required_reads, []);
  assert.deepEqual((fulfilled.work_context as JsonObject).sources, [{...read, content}]);
  for (const invalid of [
    {...content, text: null}, {...content, revision: "invalid"}, {...content, state_file: ""},
    {...content, excluded_todo_regions: []},
    {...content, excluded_todo_regions: [{...region, role: "objective"}]},
    {...content, excluded_todo_regions: [{...region, start_line: 0}]},
    {...content, excluded_todo_regions: [{...region, end_line: 3}]},
    {...content, excluded_todo_regions: [{...region, revision: "invalid"}]},
    {...content, excluded_todo_regions: [region, region]},
  ]) {
    const rejected = project(invalid);
    assert.deepEqual(rejected.required_reads, [read]);
    assert.equal((rejected.work_context as JsonObject).complete, false);
    assert.deepEqual((rejected.work_context as JsonObject).sources ?? [], []);
  }
  assert.deepEqual(project({text: content.text}).required_reads, [read]);
});
