import assert from "node:assert/strict";
import test from "node:test";

import {
  commitStepPayload,
  effectProgramFromOrderedSteps,
  interpretQuotaShouldRunPacket,
  interpretTurnResultPacket,
  requireMatchingEffectId,
  seedCommittedSteps,
  settlementBindGate,
  settlementBindReduce,
  settlementFailed,
  settlementIdentity,
  settlementIdentityFromPlan,
  settlementIdentityPayload,
  settlementNextAction,
  settlementPure,
  settlementResultPayload,
} from "../../loopx/control_plane/effect_program.ts";
import {
  receiptBoundMonitorPhase,
  receiptBoundReplayPhase,
  receiptBoundTerminalPhase,
} from "../../loopx/control_plane/quota/settlement_phase.ts";
import type {
  BoundSettlementIdentity,
  SettlementIdentity,
  SettlementResult,
} from "../../loopx/control_plane/effect_program.ts";

const identityInput = {
  goal_id: "goal",
  agent_id: "agent",
  todo_id: "todo",
  turn_instance_id: "turn",
} as const;

// The native type contract must reject the previously expressible state in
// which one settlement result carried both a success value and a failure.
// @ts-expect-error successful settlement values cannot carry failures
const invalidSettlementResult: SettlementResult<{ value: number }> = {
  value: { value: 1 },
  receipts: [],
  failure: {
    kind: "permission_denied",
    step_kind: "durable_writeback",
    reason: "denied",
  },
};
void invalidSettlementResult;

const todoIdentity = {
  ...identityInput, binding_kind: "todo", binding_id: "todo",
  replan_obligation_id: null, effect_id: "goal:agent:todo:turn",
} as const satisfies SettlementIdentity;
// @ts-expect-error a Todo binding cannot simultaneously carry a replan target
const dualIdentity: SettlementIdentity = { ...todoIdentity, replan_obligation_id: "replan" };
// @ts-expect-error the discriminant cannot disagree with the active target
const wrongKindIdentity: SettlementIdentity = { ...todoIdentity, binding_kind: "autonomous_replan" };
const unboundIdentity = {
  ...todoIdentity, binding_kind: "unbound", binding_id: "", todo_id: null,
} as const satisfies SettlementIdentity;
// @ts-expect-error an unbound plan cannot enter Turn settlement
const executableIdentity: BoundSettlementIdentity = unboundIdentity;
void [dualIdentity, wrongKindIdentity, executableIdentity];

function identityTypes(identity: BoundSettlementIdentity): void {
  if (identity.binding_kind === "todo") {
    const todo: string = identity.todo_id;
    const replan: null = identity.replan_obligation_id;
    void [todo, replan];
  } else {
    const todo: null = identity.todo_id;
    const replan: string = identity.replan_obligation_id;
    void [todo, replan];
  }
  // @ts-expect-error a validated identity cannot be rebound through mutation
  identity.todo_id = "other";
}
void identityTypes;

test("Turn-result verdicts and host actions never become quota actions", () => {
  for (const resultKind of ["validated_progress", "repair_required", "wait", "host_failure"]) {
    for (const hostAction of [undefined, null, "", "normal_run", "agent_scope_wait", "wait", "foreign_action", 42, { action: "normal_run" }]) {
      const packet = {
        result_kind: resultKind,
        effective_action: hostAction,
        failed_phase: "validation",
        completed_phases: ["host_execute", "typed_result"],
        next_cli_actions: ["loopx status"],
        scheduler_hint: {
          action: "apply_rrule", cadence_class: "repair",
          codex_app: {
            ack_hint: { cli_args: ["quota", "scheduler-ack-current"] },
            failure_hint: { cli_args: ["quota", "scheduler-ack-current", "--failure"] },
          },
        },
      };
      const before = structuredClone(packet);
      const turn = interpretTurnResultPacket(packet);
      const noAction: null = turn.observation.effective_action;
      // @ts-expect-error result observations cannot hold even a valid quota action
      const foreignAction: typeof noAction = "normal_run";
      void foreignAction;
      assert.equal(noAction, null);
      assert.equal(turn.observation.decision, resultKind);
      assert.equal(turn.observation.should_run, false);
      assert.equal(turn.request.context.failed_phase, "validation");
      assert.deepEqual(turn.next_effect, {
        cli_actions: ["loopx status"], execution_mode: null,
        scheduler_action: "apply_rrule", cadence_class: "repair",
        ack_cli_args: ["quota", "scheduler-ack-current"],
        failure_cli_args: ["quota", "scheduler-ack-current", "--failure"],
      });
      assert.equal(JSON.parse(JSON.stringify(turn)).observation.effective_action, null);
      assert.deepEqual(packet, before);
    }
  }
});

test("quota observations retain their string action and ignore host verdict fields", () => {
  for (const action of ["normal_run", "quota_skip", "agent_scope_wait", "successor_replan_required"]) {
    const turn = interpretQuotaShouldRunPacket({
      decision: "run", should_run: true, effective_action: action, result_kind: "wait",
      interaction_contract: {
        schema_version: "loopx_interaction_contract_v0", mode: "bounded_delivery",
        user_channel: { action_required: false, notify: "DONT_NOTIFY" },
        agent_channel: { must_attempt: true, delivery_allowed: true, quiet_noop_allowed: false },
        cli_channel: {},
      },
    });
    const quotaAction: string = turn.observation.effective_action;
    assert.equal(quotaAction, action);
    assert.equal(turn.observation.decision, "run");
    assert.equal(turn.observation.should_run, true);
  }
  assert.throws(() => interpretQuotaShouldRunPacket({}), /interaction_contract must be an object/);
});

test("missing or malformed result packets cannot manufacture an action", () => {
  for (const packet of [undefined, null, [], "wait", {}, { effective_action: "normal_run" }]) {
    const turn = interpretTurnResultPacket(packet);
    assert.equal(turn.observation.effective_action, null);
    assert.equal(turn.observation.decision, "");
    assert.equal(turn.observation.should_run, false);
    assert.deepEqual(turn.next_effect.cli_actions, []);
  }
});

test("ordered Effect Program steps preserve data and skip malformed entries", () => {
  const program = effectProgramFromOrderedSteps(
    [
      { id: "one", kind: "read", command: "loopx status", extra: 1 },
      null,
      { id: "two", prompt: "inspect evidence" },
    ],
    "bounded",
  );

  assert.equal(program.execution_mode, "bounded");
  assert.deepEqual(program.steps.map((step) => step.step_id), ["one", "two"]);
  assert.equal(program.steps[0]?.raw.extra, 1);
  assert.equal(program.steps[1]?.command, "inspect evidence");
});

test("settlement identity makes illegal dual bindings unrepresentable", () => {
  assert.deepEqual(settlementIdentity(identityInput), {
    ...identityInput,
    replan_obligation_id: null,
    binding_kind: "todo",
    binding_id: "todo",
    effect_id: "goal:agent:todo:turn",
  });
  assert.throws(
    () =>
      settlementIdentity({
        ...identityInput,
        replan_obligation_id: "replan",
      }),
    /cannot bind both/,
  );
});

test("a committed receipt-bound monitor poll is always a no-spend closeout", () => {
  assert.equal(
    receiptBoundMonitorPhase({
      poll_present: false,
      material_change: false,
      durable_writeback_present: false,
      quota_spend_present: false,
    }),
    "poll_due",
  );
  assert.equal(
    receiptBoundMonitorPhase({
      poll_present: true,
      material_change: false,
      durable_writeback_present: false,
      quota_spend_present: false,
    }),
    "settled",
  );
  assert.equal(
    receiptBoundMonitorPhase({
      poll_present: true,
      material_change: true,
      durable_writeback_present: true,
      quota_spend_present: false,
    }),
    "settled",
  );
  assert.equal(
    receiptBoundMonitorPhase({
      poll_present: true,
      material_change: true,
      durable_writeback_present: false,
      quota_spend_present: false,
    }),
    "settled",
  );
  assert.equal(
    receiptBoundMonitorPhase({
      poll_present: true,
      material_change: true,
      durable_writeback_present: true,
      quota_spend_present: true,
    }),
    "settled",
  );
});

test("receipt-bound replay settlement follows its binding and full chain", () => {
  assert.equal(
    receiptBoundReplayPhase({
      completion_receipt_present: false,
      durable_writeback_present: true,
      quota_spend_present: true,
    }),
    "open",
  );
  assert.equal(
    receiptBoundReplayPhase({
      binding_kind: "autonomous_replan",
      completion_receipt_present: false,
      durable_writeback_present: false,
      quota_spend_present: true,
    }),
    "open",
  );
  assert.equal(
    receiptBoundReplayPhase({
      binding_kind: "autonomous_replan",
      completion_receipt_present: false,
      durable_writeback_present: true,
      quota_spend_present: false,
    }),
    "settlement_pending",
  );
  assert.equal(
    receiptBoundReplayPhase({
      binding_kind: "autonomous_replan",
      completion_receipt_present: false,
      durable_writeback_present: true,
      quota_spend_present: true,
    }),
    "settled",
  );
  assert.equal(
    receiptBoundReplayPhase({
      binding_kind: "todo",
      writeback_completes_binding: true,
      completion_receipt_present: false,
      durable_writeback_present: true,
      quota_spend_present: true,
    }),
    "settled",
  );
  assert.equal(
    receiptBoundReplayPhase({
      completion_receipt_present: true,
      durable_writeback_present: true,
      quota_spend_present: false,
    }),
    "settlement_pending",
  );
  assert.equal(
    receiptBoundReplayPhase({
      completion_receipt_present: true,
      durable_writeback_present: true,
      quota_spend_present: true,
    }),
    "settled",
  );
  assert.equal(
    receiptBoundTerminalPhase({
      terminal_closeout_present: true,
      durable_writeback_present: true,
      quota_spend_present: true,
    }),
    "settled",
  );
});

test("bind preserves receipt order and short-circuits typed failures", () => {
  const receipt = {
    step_kind: "validation" as const,
    status: "committed",
    effect_id: "goal:agent:todo:turn",
  };
  const first = settlementPure({ value: 1 }, [receipt]);
  const gate = settlementBindGate(first);
  assert.equal(gate.execute, true);

  const second = settlementPure({ value: 2 }, [
    { ...receipt, step_kind: "durable_writeback" },
  ]);
  assert.deepEqual(settlementBindReduce(first, second).receipts, [
    receipt,
    { ...receipt, step_kind: "durable_writeback" },
  ]);

  const failed = settlementFailed({
    kind: "permission_denied",
    step_kind: "durable_writeback",
    reason: "denied",
    receipts: [receipt],
  });
  const stopped = settlementBindGate(failed);
  assert.equal(stopped.execute, false);
  assert.equal(stopped.result.failure?.kind, "permission_denied");
  assert.deepEqual(stopped.result.receipts, [receipt]);
});

test("durable receipts replay only for the same effect and ordered prefix", () => {
  const identity = settlementIdentity(identityInput);
  const seeded = seedCommittedSteps({
    identity,
    ordered_steps: ["validation", "durable_writeback", "quota_spend"],
    committed_payloads: {
      durable_writeback: { ok: true, appended: true, record: "write" },
    },
    completed_phases: ["validation", "durable_writeback"],
    transaction_phases: [
      "validation",
      "durable_writeback",
      "quota_spend",
    ],
  });
  assert.equal(seeded.failure, null);
  assert.deepEqual(
    seeded.receipts.map((receipt) => receipt.step_kind),
    ["validation", "durable_writeback"],
  );
  assert.equal(
    requireMatchingEffectId("other-effect", identity.effect_id).failure?.kind,
    "identity_mismatch",
  );
});

test("commit reduction advances one phase only after a committed payload", () => {
  const committed = commitStepPayload({
    identity: identityInput,
    step_kind: "quota_spend",
    transaction_phases: [
      "validation",
      "durable_writeback",
      "quota_spend",
    ],
    payload: { ok: true, appended: true },
  });
  assert.deepEqual(committed.completed_phases, [
    "validation",
    "durable_writeback",
    "quota_spend",
  ]);
  assert.equal(committed.result.receipts[0]?.step_kind, "quota_spend");

  const rejected = commitStepPayload({
    identity: identityInput,
    step_kind: "quota_spend",
    transaction_phases: ["validation", "durable_writeback", "quota_spend"],
    payload: { ok: false, error: "budget exhausted" },
  });
  assert.equal(rejected.completed_phases, null);
  assert.equal(rejected.result.failure?.kind, "budget_rejected");
});

test("runtime selects the next uncommitted effect instead of Python orchestration", () => {
  const next = settlementNextAction({
    identity: identityInput,
    ordered_steps: ["validation", "durable_writeback", "quota_spend"],
    committed_payloads: {},
    completed_phases: ["validation"],
    transaction_phases: ["validation", "durable_writeback", "quota_spend"],
  });
  assert.equal(next.decision, "execute");
  assert.equal(next.step_kind, "durable_writeback");

  const complete = settlementNextAction({
    identity: identityInput,
    ordered_steps: ["validation", "durable_writeback", "quota_spend"],
    committed_payloads: {
      durable_writeback: { ok: true, appended: true },
      quota_spend: { ok: true, appended: true },
    },
    completed_phases: ["validation", "durable_writeback", "quota_spend"],
    transaction_phases: ["validation", "durable_writeback", "quota_spend"],
  });
  assert.equal(complete.decision, "complete");
  assert.equal(complete.step_kind, null);
});

test("plan identity and public result payload remain stable at the boundary", () => {
  const identity = settlementIdentity(identityInput);
  const result = settlementIdentityFromPlan({
    settlement_plan: { identity },
  });
  assert.equal(result.value?.effect_id, identity.effect_id);
  assert.deepEqual(settlementResultPayload(result), {
    ok: true,
    receipts: [],
    failure: null,
  });
});

test("settlement plan decoding preserves legacy and versioned binding identities", () => {
  for (const input of [identityInput, { ...identityInput, todo_id: null, replan_obligation_id: "replan" }]) {
    const expected = settlementIdentity(input);
    const wire = settlementIdentityPayload(input);
    for (const identity of [expected, wire, { ...wire, schema_version: undefined }]) {
      const plan = { settlement_plan: { identity } };
      const before = structuredClone(plan);
      assert.deepEqual(settlementIdentityFromPlan(plan).value, expected);
      assert.deepEqual(plan, before);
    }
  }
});

test("settlement plan decoding rejects coerced and contradictory identities", () => {
  const todo = settlementIdentityPayload(identityInput);
  const replan = settlementIdentityPayload({ ...identityInput, todo_id: null, replan_obligation_id: "replan" });
  const malformed = [
    ...["goal_id", "agent_id", "turn_instance_id", "todo_id"].flatMap((field) =>
      [42, true, ["todo"], { id: "todo" }].map((value) => {
        const identity = { ...todo, [field]: value };
        // Even a matching serialized effect id cannot make a non-string id legal.
        identity.effect_id = `${identity.goal_id}:${identity.agent_id}:${identity.todo_id}:${identity.turn_instance_id}`;
        return identity;
      }),
    ),
    { ...todo, schema_version: "future_identity" },
    { ...todo, schema_version: null },
    { ...todo, binding_kind: "autonomous_replan", binding_id: "todo" },
    { ...todo, binding_kind: "todo", binding_id: "other" },
    { ...todo, replan_obligation_id: "replan" },
    { ...todo, replan_obligation_id: false },
    { ...todo, todo_id: null, effect_id: "goal:agent::turn" },
    { ...replan, binding_kind: "todo" },
    { ...replan, binding_id: "other" },
    { ...replan, binding_kind: undefined },
    { ...replan, binding_id: undefined },
    { ...replan, schema_version: "quota_settlement_identity_v0" },
    { ...replan, replan_obligation_id: 42, binding_id: "42", effect_id: "goal:agent:autonomous_replan:42:turn" },
  ];
  for (const identity of malformed) {
    const result = settlementIdentityFromPlan({ settlement_plan: { identity } });
    assert.equal(result.failure?.kind, "invalid_identity", JSON.stringify(identity));
    assert.equal(result.value, null);
    assert.deepEqual(result.receipts, []);
  }
});
