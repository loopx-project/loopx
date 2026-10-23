import assert from "node:assert/strict";
import test from "node:test";

import {EffectRuntimeRequestError} from "../../loopx/control_plane/effect_runtime_errors.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {projectInteractionExecution} from "../../loopx/control_plane/work_items/interaction_contract.ts";

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
