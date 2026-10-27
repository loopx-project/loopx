import {EffectiveAction} from "../../loopx/control_plane/quota/effective_action.generated.ts";
import instructionCases from "./fixtures/primary_action_instruction_selection.json" with {type: "json"};
import assert from "node:assert/strict";
import test from "node:test";

import {EffectRuntimeRequestError} from "../../loopx/control_plane/effect_runtime_errors.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {selectPrimaryActionInstruction, projectInteractionExecution} from "../../loopx/control_plane/work_items/interaction_contract.ts";

const AGENT_SCOPE_ACTIONS = [
  "agent_scope_exhausted",
  "agent_scope_wait",
  "reassignment_required",
  "successor_replan_required",
] as const;

function executionFacts(patch: JsonObject = {}): JsonObject {
  return {
    schema_version: "interaction_execution_facts_v0",
    effective_action: "normal_run",
    state: "eligible",
    canonical_execution: {
      kind: "normal_run",
      must_attempt_work: true,
      delivery_allowed: true,
    },
    normal_delivery_allowed: false,
    recovery_delivery_allowed: false,
    self_repair_allowed: false,
    user_required: false,
    scoped_user_gate_fallback: false,
    user_gate_notification_suppressed: false,
    notify_user_on_gate: false,
    notify_user_on_open_todo: false,
    agent_work_mode: null,
    stop_if_unchanged: false,
    blocked_successor_wait_observation: false,
    observation_required: false,
    replan_required: false,
    agent_scope_frontier_actions: [...AGENT_SCOPE_ACTIONS],
    ...patch,
  };
}

test("one owner distinguishes business work, repair and quiet wait", () => {
  assert.deepEqual(
    projectInteractionExecution(executionFacts()),
    {
      schema_version: "interaction_execution_decision_v0",
      mode: "bounded_delivery",
      must_attempt: true,
      delivery_allowed: true,
      quiet_noop_allowed: false,
      attempt_basis: "canonical",
      delivery_basis: "canonical",
      normalization_diagnostics: [],
    },
  );

  assert.deepEqual(
    projectInteractionExecution(executionFacts({
      effective_action: "agent_workspace_repair",
      canonical_execution: {
        kind: "agent_workspace_repair",
        must_attempt_work: true,
        delivery_allowed: false,
      },
    })),
    {
      schema_version: "interaction_execution_decision_v0",
      mode: "agent_workspace_repair",
      must_attempt: true,
      delivery_allowed: false,
      quiet_noop_allowed: false,
      attempt_basis: "canonical",
      delivery_basis: "canonical",
      normalization_diagnostics: [],
    },
  );

  assert.deepEqual(
    projectInteractionExecution(executionFacts({
      effective_action: "agent_scope_wait",
      canonical_execution: {kind: "agent_scope_wait"},
    })),
    {
      schema_version: "interaction_execution_decision_v0",
      mode: "agent_scope_wait",
      must_attempt: false,
      delivery_allowed: false,
      quiet_noop_allowed: true,
      attempt_basis: "none",
      delivery_basis: "none",
      normalization_diagnostics: [],
    },
  );
});

test("should_run is not an execution-permission input", () => {
  const projected = projectInteractionExecution(executionFacts({
    should_run: true,
    canonical_execution: {kind: "normal_run"},
  }));
  assert.equal(projected.must_attempt, false);
  assert.equal(projected.delivery_allowed, false);
  assert.equal(projected.quiet_noop_allowed, true);
  assert.deepEqual(projected.normalization_diagnostics, []);
});

test("canonical null and false stop compatibility fallback", () => {
  for (const canonicalDelivery of [null, false]) {
    const projected = projectInteractionExecution(executionFacts({
      normal_delivery_allowed: true,
      recovery_delivery_allowed: true,
      self_repair_allowed: true,
      canonical_execution: {
        kind: "normal_run",
        must_attempt_work: true,
        delivery_allowed: canonicalDelivery,
      },
    }));
    assert.equal(projected.must_attempt, true);
    assert.equal(projected.delivery_allowed, false);
    assert.equal(
      projected.delivery_basis,
      canonicalDelivery === null ? "canonical_null" : "canonical",
    );
  }
});

test("the canonical replan attempt leaves ordinary delivery closed", () => {
  const current = executionFacts({effective_action: "autonomous_replan_required",
    replan_required: true, canonical_execution: {must_attempt_work: true}});
  const admitted = projectInteractionExecution(current);
  assert.equal(admitted.mode, "autonomous_replan");
  assert.equal(admitted.must_attempt, true);
  assert.equal(admitted.delivery_allowed, false);
  assert.equal(admitted.delivery_basis, "none");
  for (const denial of [false, null]) {
    const refused = projectInteractionExecution({...current,
      canonical_execution: {must_attempt_work: true, delivery_allowed: denial}});
    assert.equal(refused.delivery_allowed, false);
  }
  for (const patch of [
    {canonical_execution: {must_attempt_work: false}},
    {canonical_execution: {}},
    {effective_action: "normal_run"},
    {effective_action: "monitor_quiet_skip"},
    {replan_required: false},
  ]) {
    assert.equal(projectInteractionExecution({...current, ...patch}).delivery_allowed, false);
  }
});

test("missing canonical delivery retains compatibility precedence without should_run", () => {
  const normal = projectInteractionExecution(executionFacts({
    should_run: false,
    normal_delivery_allowed: true,
    canonical_execution: {kind: "normal_run", must_attempt_work: true},
  }));
  assert.equal(normal.delivery_allowed, true);
  assert.equal(normal.delivery_basis, "compatibility_normal");

  const recovery = projectInteractionExecution(executionFacts({
    normal_delivery_allowed: false,
    recovery_delivery_allowed: true,
    canonical_execution: {
      kind: "outcome_floor_recovery",
      must_attempt_work: true,
    },
  }));
  assert.equal(recovery.mode, "outcome_floor_recovery");
  assert.equal(recovery.delivery_allowed, true);
  assert.equal(recovery.delivery_basis, "compatibility_recovery");
});

test("a direct canonical contradiction is rejected instead of silently clamped", () => {
  assert.throws(
    () => projectInteractionExecution(executionFacts({
      canonical_execution: {
        kind: "normal_run",
        must_attempt_work: false,
        delivery_allowed: true,
      },
    })),
    (error: unknown) => error instanceof EffectRuntimeRequestError
      && error.code === "interaction_contract.delivery_without_attempt",
  );
});

test("a coarse inherited delivery grant is normalized with a concrete diagnostic", () => {
  const projected = projectInteractionExecution(executionFacts({
    effective_action: "monitor_quiet_skip",
    canonical_execution: {
      kind: "work_lane_contract",
      must_attempt_work: false,
    },
    normal_delivery_allowed: true,
  }));
  assert.equal(projected.mode, "monitor_quiet_skip");
  assert.equal(projected.must_attempt, false);
  assert.equal(projected.delivery_allowed, false);
  assert.equal(projected.quiet_noop_allowed, true);
  assert.deepEqual(projected.normalization_diagnostics, [
    "interaction_contract.delivery_without_attempt",
  ]);
});

test("malformed permissions and source shapes cannot become authority", () => {
  for (const [field, value] of [
    ["normal_delivery_allowed", "true"],
    ["recovery_delivery_allowed", 1],
    ["self_repair_allowed", []],
  ] as const) {
    assert.throws(
      () => projectInteractionExecution(executionFacts({[field]: value})),
      /must be a boolean/,
    );
  }
  for (const [field, value] of [
    ["must_attempt_work", "false"],
    ["delivery_allowed", 1],
  ] as const) {
    assert.throws(
      () => projectInteractionExecution(executionFacts({
        canonical_execution: {kind: "normal_run", [field]: value},
      })),
      /must be a boolean/,
    );
  }
  for (const malformed of [null, [], "facts"]) {
    assert.throws(() => projectInteractionExecution(malformed), /must be an object/);
  }
  assert.throws(
    () => projectInteractionExecution(executionFacts({canonical_execution: []})),
    /canonical_execution must be an object or null/,
  );
  assert.throws(
    () => projectInteractionExecution(executionFacts({schema_version: "future"})),
    /schema_version must be interaction_execution_facts_v0/,
  );
});

// Expected text was pinned before replacing the Python owner. Facts include its
// code-point truncation and repr views, rather than a JavaScript approximation.
test("primary instruction preserves every pinned mode, source and codec edge", () => {
  for (const item of instructionCases) {
    assert.equal(selectPrimaryActionInstruction(item.facts, item.mode), item.expected, item.name);
    assert.equal(selectPrimaryActionInstruction(item.facts, "bounded_delivery"),
      item.candidate || "advance scope-bounded work with validation", `${item.name}: candidate precedence`);
    assert.equal(selectPrimaryActionInstruction({...item.facts, lifecycle: {present: false, action: null}}, "autonomous_replan"),
      item.packet_instruction, `${item.name}: packet instruction`);
  }
});

const emptyInstructionFacts = instructionCases.find(item => item.name === "empty-bounded_delivery")!.facts;

test("selected execution mode owns the final instruction and keeps preflight shape", () => {
  const replan = projectInteractionExecution(executionFacts({replan_required: true,
    canonical_execution: {must_attempt_work: true, delivery_allowed: false},
    primary_action_facts: emptyInstructionFacts, selection_packet: {}}));
  assert.equal(replan.mode, "autonomous_replan");
  assert.equal(replan.primary_action, "produce one typed outcome from the host-projected replan action packet");
  assert.equal(replan.delivery_allowed, false);
  const monitor = projectInteractionExecution(executionFacts({effective_action: EffectiveAction.MONITOR_QUIET_SKIP,
    primary_action_facts: emptyInstructionFacts, selection_packet: {}}));
  assert.equal(monitor.primary_action, "ensure this heartbeat's idempotent quota receipt is committed, then stay quiet if unchanged; the guard records the eligible no-progress observation and an earlier heartbeat receipt does not satisfy this one");
  const workspace = projectInteractionExecution(executionFacts({effective_action: EffectiveAction.AGENT_WORKSPACE_REPAIR,
    primary_action_facts: emptyInstructionFacts, selection_packet: {}}));
  assert.equal(workspace.primary_action, "create or switch to an independent worktree/branch, then rerun quota guard before file edits");
  const capability = projectInteractionExecution(executionFacts({effective_action: EffectiveAction.CAPABILITY_BRIDGE_REPAIR,
    primary_action_facts: emptyInstructionFacts, selection_packet: {}}));
  assert.equal(capability.primary_action, "execute interaction_contract.agent_channel.next_task_action.operation once with its real task-facing tool and exact target_ref when projected; preflight_allowed is false, and the instruction is context rather than CLI text; on success execute cli_channel.next_cli_actions[0] in the same turn and continue only when quota allows");
  const advancement = projectInteractionExecution(executionFacts({primary_action_facts: {
    ...emptyInstructionFacts, agent_todos: {first_executable_items: [
      {label: "Due monitor", actionable: true, advancement: false},
      {label: "[P1] Advance the bounded product slice.", actionable: true, advancement: true},
    ], first_open_items: []}}, selection_packet: {}}));
  assert.equal(advancement.primary_action, "[P1] Advance the bounded product slice.");
  assert.equal(advancement.must_attempt, true);
  assert.equal(advancement.delivery_allowed, true);
  assert.equal(Object.hasOwn(projectInteractionExecution(executionFacts()), "primary_action"), false);
});

test("recovery never reads instruction facts or constructs executable text", () => {
  for (const state of ["rejected", "deferred"]) {
    const request = executionFacts({selection_packet: {action_selection_qualification: {
      state, requested_todo_id: "todo_requested", recovery_action: "reenter_guard_without_selection"}}});
    Object.defineProperty(request, "primary_action_facts", {enumerable: true,
      get() {assert.fail("unadmitted selection read executable instruction facts");}});
    const result = projectInteractionExecution(request);
    assert.equal(Object.hasOwn(result, "primary_action"), false);
    // A receipt-bound choice retains the existing admission exception.
    const admitted = projectInteractionExecution({...executionFacts(), primary_action_facts: emptyInstructionFacts,
      selection_packet: {...request.selection_packet as JsonObject,
        selected_todo: {selection_binding: "heartbeat_receipt"}}});
    assert.equal(admitted.primary_action, "advance scope-bounded work with validation");
  }
  assert.throws(() => projectInteractionExecution(executionFacts({primary_action_facts: {}, selection_packet: {}})), EffectRuntimeRequestError);
  assert.throws(() => projectInteractionExecution(executionFacts({primary_action_facts: emptyInstructionFacts})), EffectRuntimeRequestError);
});
