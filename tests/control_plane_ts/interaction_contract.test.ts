import assert from "node:assert/strict";
import test from "node:test";
import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";

import { interpretQuotaShouldRunPacket } from "../../loopx/control_plane/effect_program.ts";
import {
  decodeInteractionContract,
  projectInteractionRequiredReads,
  projectInteractionWorkContext,
  type AgentInteractionChannel,
} from "../../loopx/control_plane/work_items/interaction_contract.ts";

// The host-facing type cannot represent delivery without an agent attempt.
// @ts-expect-error delivery requires must_attempt=true
const invalidDeliveryChannel: AgentInteractionChannel = {
  must_attempt: false,
  delivery_allowed: true,
  quiet_noop_allowed: false,
};
void invalidDeliveryChannel;

function readFacts(): JsonObject {
  return {required_reads: [{command: "read-existing-evidence", source: "existing"}],
    goal_id: "goal-requirements", goal_state_file: "/tmp/goal source/state's requirements.md",
    command_prefix: "loopx --registry '/tmp/goal source/registry.json' --runtime-root '/tmp/goal source/runtime'",
    goal_acceptance_enabled: true, should_run: true, delivery_allowed: true, selection_required: false,
    has_replan: false, settlement_only: false, effective_action: "normal_run",
    selected_todo: {todo_id: "todo_selected"}};
}

test("only participating hooks add unavailable context; generic guidance has no preference recipe", () => {
  const request = {required_reads: [], source_results: []};
  const inactive = projectInteractionWorkContext(request).work_context as JsonObject;
  assert.equal(inactive.unavailable_context, undefined);
  assert.doesNotMatch(String(inactive.instruction), /preference|empty view|empty observations/);
  assert.match(String(inactive.instruction), /this guard's pre-work checks/);
  assert.match(String(inactive.instruction), /source-specific freshness obligations/);
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
  const facts: JsonObject = {...readFacts(), goal_state_file: null, goal_acceptance_enabled: false,
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
});

test("mixed Goal document remains a full progressive read without dropping task requirements", () => {
  const goalRead = {command: "cat -- state.md", source: "goal_state", ordering: "before_work"};
  const taskRead = {command: "read-todo", source: "selected_todo"};
  const task = {todo_id: "todo_work", status: "open", claimed_by: "agent-a",
    text: "Preserve every requirement. ".repeat(400) + "Stop before deployment."};
  const projected = projectInteractionWorkContext({required_reads: [goalRead, taskRead],
    selected_todo: task, source_results: [
      {command: goalRead.command, content: {text: "Goal intent, other tasks and historical evidence."}},
      {command: taskRead.command, content: {matched: true, todo: task}},
    ]});
  assert.deepEqual(projected.required_reads, [goalRead]);
  const context = projected.work_context as JsonObject;
  assert.equal(context.complete, true); // No source failure; pending reads still apply.
  assert.deepEqual(context.sources, [{...taskRead, content: {matched: true, todo: task}}]);
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
