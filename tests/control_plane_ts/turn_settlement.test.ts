import assert from "node:assert/strict";
import test from "node:test";

import { effectRuntimeErrorPayload } from "../../loopx/control_plane/effect_runtime_errors.ts";
import { settlementIdentity } from "../../loopx/control_plane/effect_program.ts";
import {
  reduceTurnSettlementTransaction,
  TURN_SETTLEMENT_TRANSACTION_SCHEMA_VERSION,
} from "../../loopx/control_plane/turn_driver/settlement.ts";

const phases = [
  "host_execute",
  "typed_result",
  "validation",
  "durable_writeback",
  "quota_spend",
  "scheduler_apply",
  "scheduler_ack",
] as const;

const identity = settlementIdentity({
  goal_id: "goal",
  agent_id: "agent",
  todo_id: "todo",
  turn_instance_id: "turn",
});

function request(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    schema_version: TURN_SETTLEMENT_TRANSACTION_SCHEMA_VERSION,
    transaction_plan: { settlement_plan: { identity } },
    transaction_phases: [...phases],
    completed_phases: [...phases.slice(0, 5)],
    committed_effect_id: identity.effect_id,
    writeback_payload: { ok: true, appended: true, record: "writeback" },
    quota_spend_payload: { ok: true, appended: true, record: "spend" },
    terminal_closeout_required: false,
    terminal_closeout_payload: null,
    failed_provider_attempt: null,
    effect_attempts: {},
    provider_observations: {},
    ...overrides,
  };
}

test("complete Turn settlement constructs one ordered receipt chain", () => {
  const reduced = reduceTurnSettlementTransaction(request());

  assert.equal(reduced.result.failure, null);
  assert.deepEqual(
    reduced.result.receipts.map((receipt) => receipt.step_kind),
    ["validation", "durable_writeback", "quota_spend"],
  );
  assert.deepEqual(reduced.result.value, {
    completed_phases: [...phases.slice(0, 5)],
    writeback: { ok: true, appended: true, record: "writeback" },
    quota_spend: { ok: true, appended: true, record: "spend" },
  });
  assert.deepEqual(reduced.settlement_result, {
    ok: true,
    receipts: reduced.result.receipts.map((receipt) => ({
      schema_version: "quota_settlement_receipt_v1",
      ...receipt,
    })),
    failure: null,
  });
});

test("Turn settlement rejects an unknown result_kind at the typed boundary", () => {
  assert.throws(
    () => reduceTurnSettlementTransaction(request({ turn_result_kind: "done" })),
    /turn_result_kind is unsupported/,
  );
});

test("iteration failure cannot enter durable settlement", () => {
  assert.throws(
    () => reduceTurnSettlementTransaction(
      request({ turn_result_kind: "iteration_failed" }),
    ),
    /cannot run for iteration stop result_kind iteration_failed/,
  );
});

test("non-terminal completion requires a durable continuing Todo outcome", () => {
  const reduced = reduceTurnSettlementTransaction(
    request({ turn_result_kind: "validated_completion" }),
  );

  assert.equal(reduced.result.failure?.kind, "receipt_missing");
  assert.equal(reduced.result.failure?.step_kind, "durable_writeback");
});

test("non-terminal completion accepts a durable continuing Todo outcome", () => {
  const reduced = reduceTurnSettlementTransaction(
    request({
      turn_result_kind: "validated_completion",
      writeback_payload: {
        ok: true,
        appended: true,
        completion: { todo_id: "todo", continuation: "active_goal" },
      },
    }),
  );

  assert.equal(reduced.result.failure, null);
  assert.deepEqual(
    (reduced.settlement_result as Record<string, unknown>).turn_outcome,
    {
      schema_version: "loopx_turn_settlement_outcome_v0",
      result_kind: "validated_completion",
      completed_phases: [...phases.slice(0, 5)],
      failed_phase: null,
      completion: { todo_id: "todo", continuation: "active_goal" },
    },
  );
});

test("successful successor completion is included in the canonical outcome", () => {
  const reduced = reduceTurnSettlementTransaction(
    request({
      turn_result_kind: "validated_completion",
      writeback_payload: {
        ok: true,
        appended: true,
        completion: {
          todo_id: "todo",
          continuation: "successor",
          successor_todo_ids: ["next"],
        },
      },
    }),
  );

  assert.equal(reduced.result.failure, null);
  assert.deepEqual(
    (reduced.settlement_result as Record<string, unknown>).turn_outcome?.completion,
    {
      todo_id: "todo",
      continuation: "successor",
      successor_todo_ids: ["next"],
    },
  );
});

test("successor completion requires durable successor Todo ids", () => {
  const reduced = reduceTurnSettlementTransaction(
    request({
      turn_result_kind: "validated_completion",
      writeback_payload: {
        ok: true,
        appended: true,
        completion: { todo_id: "todo", continuation: "successor" },
      },
    }),
  );

  assert.equal(reduced.result.failure?.kind, "receipt_missing");
  assert.match(
    reduced.result.failure?.reason ?? "",
    /successor completion requires successor Todo ids/,
  );
});

test("terminal closeout requires a validated completion result", () => {
  const reduced = reduceTurnSettlementTransaction(
    request({
      turn_result_kind: "validated_progress",
      terminal_closeout_required: true,
    }),
  );

  assert.equal(reduced.decision, "failed");
  assert.equal(reduced.result.failure?.kind, "terminal_closeout_rejected");
  assert.equal(reduced.result.failure?.step_kind, "terminal_closeout");
  assert.match(
    reduced.result.failure?.reason ?? "",
    /terminal closeout requires a validated completion result/,
  );
});

test("preflight authorizes ordered providers without settling early", () => {
  const reduced = reduceTurnSettlementTransaction(
    request({
      completed_phases: [...phases.slice(0, 3)],
      writeback_payload: null,
      quota_spend_payload: null,
    }),
  );

  assert.equal(reduced.decision, "execute");
  assert.deepEqual(reduced.provider_effects, [
    {
      step_kind: "durable_writeback",
      action: "prepare_and_execute",
      effect_ref: `${identity.effect_id}#durable_writeback`,
      completed_phases: [...phases.slice(0, 4)],
    },
  ]);
  assert.equal(reduced.result, null);
  assert.equal(reduced.settlement_result, null);
});

test("mismatched replay fails before accepting committed provider outcomes", () => {
  const reduced = reduceTurnSettlementTransaction(
    request({ committed_effect_id: "another-effect" }),
  );

  assert.equal(reduced.result.value, null);
  assert.equal(reduced.result.failure?.kind, "identity_mismatch");
  assert.deepEqual(reduced.result.receipts, []);
});

test("writeback rejection retains validation receipt and typed failure", () => {
  const reduced = reduceTurnSettlementTransaction(
    request({
      turn_result_kind: "validated_progress",
      completed_phases: [...phases.slice(0, 3)],
      writeback_payload: null,
      quota_spend_payload: null,
      failed_provider_attempt: {
        step_kind: "durable_writeback",
        payload: {
          ok: false,
          appended: false,
          reason: "writeback denied",
        },
      },
    }),
  );

  assert.equal(reduced.result.failure?.kind, "writeback_rejected");
  assert.deepEqual(
    reduced.result.receipts.map((receipt) => receipt.step_kind),
    ["validation"],
  );
  assert.deepEqual(
    (reduced.settlement_result as Record<string, unknown>).turn_outcome,
    {
      schema_version: "loopx_turn_settlement_outcome_v0",
      result_kind: "writeback_failed",
      completed_phases: [...phases.slice(0, 3)],
      failed_phase: "durable_writeback",
    },
  );
});

test("internal settlement contradictions remain internal failures", () => {
  let caught: unknown;
  try {
    reduceTurnSettlementTransaction(
      request({
        completed_phases: [...phases.slice(0, 3)],
        writeback_payload: null,
        quota_spend_payload: null,
        failed_provider_attempt: {
          step_kind: "durable_writeback",
          payload: { ok: true, appended: true, record: "writeback" },
        },
      }),
    );
  } catch (error) {
    caught = error;
  }

  assert.deepEqual(effectRuntimeErrorPayload(caught), {
    kind: "internal_failure",
    code: "unexpected_handler_error",
    message: "Effect runtime handler failed unexpectedly",
  });
});

test("terminal closeout joins the same transaction after spend", () => {
  const reduced = reduceTurnSettlementTransaction(
    request({
      turn_result_kind: "validated_completion",
      terminal_closeout_required: true,
      terminal_closeout_payload: {
        ok: true,
        appended: true,
        completion: { todo_id: "todo", continuation: "no_followup" },
      },
    }),
  );

  assert.equal(reduced.result.failure, null);
  assert.deepEqual(
    reduced.result.receipts.map((receipt) => receipt.step_kind),
    [
      "validation",
      "durable_writeback",
      "quota_spend",
      "terminal_closeout",
    ],
  );
  assert.deepEqual(
    (reduced.settlement_result as Record<string, unknown>).turn_outcome,
    {
      schema_version: "loopx_turn_settlement_outcome_v0",
      result_kind: "validated_completion",
      completed_phases: [...phases.slice(0, 5)],
      failed_phase: null,
      completion: { todo_id: "todo", continuation: "no_followup" },
    },
  );
});

test("non-prefix journals fail closed instead of inventing provider work", () => {
  const reduced = reduceTurnSettlementTransaction(
    request({
      completed_phases: ["host_execute", "validation"],
      writeback_payload: null,
      quota_spend_payload: null,
    }),
  );

  assert.equal(reduced.result.failure?.kind, "receipt_missing");
  assert.match(
    reduced.result.failure?.reason ?? "",
    /ordered transaction prefix/,
  );
});

test("prepared provider attempts authorize one identity-bound readback", () => {
  const effectRef = `${identity.effect_id}#durable_writeback`;
  const reduced = reduceTurnSettlementTransaction(
    request({
      completed_phases: [...phases.slice(0, 3)],
      writeback_payload: null,
      quota_spend_payload: null,
      effect_attempts: {
        durable_writeback: { status: "prepared", effect_ref: effectRef },
      },
    }),
  );

  assert.equal(reduced.decision, "execute");
  assert.deepEqual(reduced.provider_effects, [
    {
      step_kind: "durable_writeback",
      action: "resolve_prepared",
      effect_ref: effectRef,
      completed_phases: [...phases.slice(0, 4)],
    },
  ]);
});

test("prepared provider attempts reject identity drift before readback", () => {
  const reduced = reduceTurnSettlementTransaction(
    request({
      completed_phases: [...phases.slice(0, 3)],
      writeback_payload: null,
      quota_spend_payload: null,
      effect_attempts: {
        durable_writeback: {
          status: "prepared",
          effect_ref: "another-effect#durable_writeback",
        },
      },
    }),
  );

  assert.equal(reduced.decision, "failed");
  assert.equal(reduced.result.failure?.kind, "identity_mismatch");
  assert.equal(reduced.result.failure?.step_kind, "durable_writeback");
  assert.deepEqual(reduced.provider_effects, []);
});

test("unknown prepared provider outcomes fail closed", () => {
  const effectRef = `${identity.effect_id}#durable_writeback`;
  const reduced = reduceTurnSettlementTransaction(
    request({
      completed_phases: [...phases.slice(0, 3)],
      writeback_payload: null,
      quota_spend_payload: null,
      effect_attempts: {
        durable_writeback: { status: "prepared", effect_ref: effectRef },
      },
      provider_observations: {
        durable_writeback: {
          kind: "unknown",
          payload: null,
          reason: "provider readback timed out",
        },
      },
    }),
  );

  assert.equal(reduced.decision, "failed");
  assert.equal(reduced.result.failure?.kind, "effect_outcome_unknown");
  assert.equal(reduced.result.failure?.step_kind, "durable_writeback");
  assert.match(reduced.result.failure?.reason ?? "", /readback timed out/);
  assert.deepEqual(reduced.provider_effects, []);
});

test("committed readback without a durable payload fails closed", () => {
  const effectRef = `${identity.effect_id}#durable_writeback`;
  const reduced = reduceTurnSettlementTransaction(
    request({
      completed_phases: [...phases.slice(0, 3)],
      writeback_payload: null,
      quota_spend_payload: null,
      effect_attempts: {
        durable_writeback: { status: "prepared", effect_ref: effectRef },
      },
      provider_observations: {
        durable_writeback: {
          kind: "committed",
          payload: { ok: true, appended: false },
          reason: null,
        },
      },
    }),
  );

  assert.equal(reduced.decision, "failed");
  assert.equal(reduced.result.failure?.kind, "receipt_missing");
  assert.equal(reduced.result.failure?.step_kind, "durable_writeback");
  assert.deepEqual(reduced.provider_effects, []);
});

test("prepared attempts cannot skip an earlier provider step", () => {
  const reduced = reduceTurnSettlementTransaction(
    request({
      completed_phases: [...phases.slice(0, 3)],
      writeback_payload: null,
      quota_spend_payload: null,
      effect_attempts: {
        quota_spend: {
          status: "prepared",
          effect_ref: `${identity.effect_id}#quota_spend`,
        },
      },
    }),
  );

  assert.equal(reduced.decision, "failed");
  assert.equal(reduced.result.failure?.kind, "receipt_missing");
  assert.equal(reduced.result.failure?.step_kind, "quota_spend");
  assert.deepEqual(reduced.provider_effects, []);
});

// The expected actions come from the recovery contract: only absence permits
// execution, only admitted commitment permits checkpoint, and uncertainty holds.
for (const [step, prefix] of [
  ["durable_writeback", 3], ["quota_spend", 4], ["terminal_closeout", 5],
] as const) {
  const effectRef = `${identity.effect_id}#${step}`;
  const committed = { ok: true, appended: true, effect_ref: effectRef };
  const pending = {
    completed_phases: [...phases.slice(0, prefix)],
    writeback_payload: prefix > 3 ? { ok: true, appended: true } : null,
    quota_spend_payload: prefix > 4 ? { ok: true, appended: true } : null,
    terminal_closeout_required: step === "terminal_closeout",
    effect_attempts: { [step]: { status: "prepared", effect_ref: effectRef } },
  };
  for (const [label, evidence, action, failure] of [
    ["returned commitment", { returned_provider_attempt: { step_kind: step, payload: committed } }, "checkpoint", null],
    ["returned rejection", { returned_provider_attempt: { step_kind: step, payload: { ok: false } } }, "abort_prepared", null],
    ["committed readback", { provider_observations: { [step]: { kind: "committed", payload: committed } } }, "checkpoint", null],
    ["absent readback", { provider_observations: { [step]: { kind: "absent" } } }, "execute_prepared", null],
    ["unknown readback", { provider_observations: { [step]: { kind: "unknown" } } }, null, "effect_outcome_unknown"],
    ["unsupported readback", { provider_observations: { [step]: { kind: "returned", payload: { ok: false } } } }, null, "effect_outcome_unknown"],
    ["contradictory absence", { provider_observations: { [step]: { kind: "absent", payload: committed } } }, null, "effect_outcome_unknown"],
    ["malformed commitment", { provider_observations: { [step]: { kind: "committed", payload: [] } } }, null, "receipt_missing"],
    ["mixed evidence", { returned_provider_attempt: { step_kind: step, payload: committed }, provider_observations: { [step]: { kind: "absent" } } }, null, "receipt_missing"],
    ["wrong identity", { provider_observations: { [step]: { kind: "committed", payload: { ...committed, effect_ref: "other" } } } }, null, "identity_mismatch"],
  ] as const) {
    test(`${step}: ${label} authorizes only its legal next action`, () => {
      const reduced = reduceTurnSettlementTransaction(request({ ...pending, ...evidence }));
      if (action === null) {
        assert.equal(reduced.decision, "failed");
        assert.equal(reduced.result?.failure?.kind, failure);
        assert.deepEqual(reduced.provider_effects, []);
      } else {
        assert.equal(reduced.decision, "execute");
        assert.equal(reduced.provider_effects.length, 1);
        assert.equal(reduced.provider_effects[0].action, action);
        assert.equal(reduced.provider_effects[0].step_kind, step);
        assert.equal(reduced.provider_effects[0].effect_ref, effectRef);
        // Evidence alone has not durably advanced any phase or made receipts.
        assert.equal(reduced.result, null);
        if (action === "checkpoint") assert.deepEqual(reduced.provider_effects[0].payload, committed);
      }
    });
  }
}

for (const source of ["returned_provider_attempt", "provider_observations"] as const) {
  for (const step of ["durable_writeback", "terminal_closeout"] as const) {
    test(`${source}: invalid ${step} completion is held before checkpoint`, () => {
      const effectRef = `${identity.effect_id}#${step}`;
      const payload = { ok: true, appended: true, effect_ref: effectRef };
      const reduced = reduceTurnSettlementTransaction(request({
        completed_phases: [...phases.slice(0, step === "durable_writeback" ? 3 : 5)],
        writeback_payload: step === "durable_writeback" ? null : { ok: true, appended: true },
        quota_spend_payload: step === "durable_writeback" ? null : { ok: true, appended: true },
        terminal_closeout_required: step === "terminal_closeout",
        turn_result_kind: "validated_completion",
        effect_attempts: { [step]: { status: "prepared", effect_ref: effectRef } },
        [source]: source === "returned_provider_attempt"
          ? { step_kind: step, payload }
          : { [step]: { kind: "committed", payload } },
      }));
      assert.equal(reduced.result?.failure?.kind, "receipt_missing");
      assert.deepEqual(reduced.provider_effects, []);
    });
  }
}

test("a completed journal cannot silently consume orphan provider evidence", () => {
  const reduced = reduceTurnSettlementTransaction(request({
    returned_provider_attempt: { step_kind: "quota_spend", payload: { ok: true, appended: true } },
  }));
  assert.equal(reduced.result?.failure?.kind, "receipt_missing");
});

for (const step of ["durable_writeback", "quota_spend", "terminal_closeout"] as const) {
  test(`replay rejects an explicit mismatched ${step} payload ref`, () => {
    const reduced = reduceTurnSettlementTransaction(request({
      terminal_closeout_required: step === "terminal_closeout",
      [`${step === "durable_writeback" ? "writeback" : step}_payload`]: {
        ok: true, appended: true, effect_ref: "another-operation",
      },
    }));
    assert.equal(reduced.result?.failure?.kind, "identity_mismatch");
    assert.deepEqual(reduced.provider_effects, []);
  });
}

test("the old interpreter contract is rejected before any provider authorization", () => {
  assert.throws(() => reduceTurnSettlementTransaction(request({
    schema_version: "loopx_turn_settlement_transaction_v0",
  })), /schema_version is unsupported/);
});

test("rejection cannot clear a prepared operation without an abort acknowledgement", () => {
  const reduced = reduceTurnSettlementTransaction(request({
    completed_phases: [...phases.slice(0, 3)],
    writeback_payload: null, quota_spend_payload: null,
    effect_attempts: {
      durable_writeback: { status: "prepared", effect_ref: `${identity.effect_id}#durable_writeback` },
    },
    failed_provider_attempt: { step_kind: "durable_writeback", payload: { ok: false } },
  }));
  assert.equal(reduced.result?.failure?.kind, "receipt_missing");
});

for (const terminal of [false, true]) {
  test(`completion checkpoint preserves compact lifecycle projection (terminal=${terminal})`, () => {
    const step = terminal ? "terminal_closeout" : "durable_writeback";
    const completion = Object.freeze({
      todo_id: "todo",
      continuation: terminal ? "no_followup" : "successor",
      successor_todo_ids: Object.freeze(["todo-next"]),
      provider_detail: "not part of the completion contract",
    });
    const returned = Object.freeze({ ok: true, appended: true, completion });
    const reduced = reduceTurnSettlementTransaction(request({
      completed_phases: [...phases.slice(0, terminal ? 5 : 3)],
      writeback_payload: terminal ? { ok: true, appended: true } : null,
      quota_spend_payload: terminal ? { ok: true, appended: true } : null,
      terminal_closeout_required: terminal, turn_result_kind: "validated_completion",
      effect_attempts: {
        [step]: { status: "prepared", effect_ref: `${identity.effect_id}#${step}` },
      },
      returned_provider_attempt: { step_kind: step, payload: returned },
    }));
    assert.equal(reduced.decision, "execute");
    const effect = reduced.provider_effects[0];
    assert.equal(effect.action, "checkpoint");
    assert.deepEqual(effect.payload.completion, {
      todo_id: "todo", continuation: terminal ? "no_followup" : "successor",
      ...(terminal ? {} : { successor_todo_ids: ["todo-next"] }),
    });
    assert.equal(returned.completion, completion);
    assert.equal(completion.provider_detail, "not part of the completion contract");
  });
}
