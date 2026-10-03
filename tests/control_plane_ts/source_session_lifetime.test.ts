import assert from "node:assert/strict";
import test from "node:test";

import {
  decideGoalRecreation,
  decideProjectSessionBind,
  decideProjectSessionUnbind,
  decideSourceTurnEffectAbsentResolution,
  decideSourceTurnEffectAdmission,
  decideSourceTurnEffectGate,
  decideSourceTurnEffectRelease,
} from "../../loopx/control_plane/goals/source_session_lifetime.ts";

const INSTANCE_A = "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const INSTANCE_B = "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const INSTANCE_C = "ginst_cccccccccccccccccccccccccccccccc";

function bindFacts(currentGoalInstanceId: string) {
  return {
    profile_id: "source_session_v1",
    operation_id: "bind-session-a",
    request_digest: `sha256:${"a".repeat(64)}`,
    session_id: "session-a",
    requested_goal_ref: {
      goal_id: "release",
      goal_instance_id: INSTANCE_A,
    },
    current_goal_ref: {
      goal_id: "release",
      goal_instance_id: currentGoalInstanceId,
    },
    current_binding: null,
    prior_receipt: null,
    binding_count: 0,
    receipt_count: 0,
  };
}

test("bind commits only while the captured Goal instance is current", () => {
  assert.deepEqual(
    decideProjectSessionBind(bindFacts(INSTANCE_A)),
    {
      kind: "commit",
      goal_ref: {
        goal_id: "release",
        goal_instance_id: INSTANCE_A,
      },
      changed: true,
    },
  );

  assert.deepEqual(
    decideProjectSessionBind(bindFacts(INSTANCE_B)),
    {
      kind: "reject",
      code: "stale_goal_instance",
    },
  );
});

test("bind replays an exact operation before current-state and capacity checks", () => {
  const receipt = {
    schema_version: "loopx_source_session_receipt_v1",
    operation: "bind",
    operation_id: "bind-session-a",
    request_digest: `sha256:${"a".repeat(64)}`,
    session_id: "session-a",
    goal_ref: {
      goal_id: "release",
      goal_instance_id: INSTANCE_A,
    },
    changed: true,
  };

  assert.deepEqual(
    decideProjectSessionBind({
      ...bindFacts(INSTANCE_B),
      prior_receipt: receipt,
      binding_count: 256,
      receipt_count: 4096,
    }),
    { kind: "replay", receipt },
  );

  assert.deepEqual(
    decideProjectSessionBind({
      ...bindFacts(INSTANCE_A),
      request_digest: `sha256:${"b".repeat(64)}`,
      prior_receipt: receipt,
    }),
    {
      kind: "reject",
      code: "operation_id_conflict",
    },
  );
});

test("unbind removes only the session binding to the captured instance", () => {
  const facts = {
    ...bindFacts(INSTANCE_A),
    operation_id: "unbind-session-a",
    current_binding: {
      session_id: "session-a",
      foreground_goal_ref: {
        goal_id: "release",
        goal_instance_id: INSTANCE_A,
      },
    },
  };

  assert.deepEqual(
    decideProjectSessionUnbind(facts),
    {
      kind: "commit",
      goal_ref: {
        goal_id: "release",
        goal_instance_id: INSTANCE_A,
      },
      changed: true,
    },
  );
  assert.deepEqual(
    decideProjectSessionUnbind({
      ...facts,
      current_goal_ref: {
        goal_id: "release",
        goal_instance_id: INSTANCE_B,
      },
    }),
    { kind: "reject", code: "stale_goal_instance" },
  );
});

test("session capacity rejects new work after preserving exact replay", () => {
  const unbindReceipt = {
    schema_version: "loopx_source_session_receipt_v1",
    operation: "unbind",
    operation_id: "unbind-session-a",
    request_digest: `sha256:${"a".repeat(64)}`,
    session_id: "session-a",
    goal_ref: {
      goal_id: "release",
      goal_instance_id: INSTANCE_A,
    },
    changed: true,
  };
  const unbindFacts = {
    ...bindFacts(INSTANCE_A),
    operation_id: "unbind-session-a",
    current_binding: {
      session_id: "session-a",
      foreground_goal_ref: {
        goal_id: "release",
        goal_instance_id: INSTANCE_A,
      },
    },
  };

  assert.deepEqual(
    decideProjectSessionUnbind({
      ...unbindFacts,
      prior_receipt: unbindReceipt,
      binding_count: 256,
      receipt_count: 4096,
    }),
    { kind: "replay", receipt: unbindReceipt },
  );
  assert.deepEqual(
    decideProjectSessionBind({
      ...bindFacts(INSTANCE_A),
      binding_count: 256,
    }),
    { kind: "reject", code: "binding_capacity_exhausted" },
  );
  assert.deepEqual(
    decideProjectSessionBind({
      ...bindFacts(INSTANCE_A),
      receipt_count: 4096,
    }),
    { kind: "reject", code: "history_capacity_exhausted" },
  );
  assert.deepEqual(
    decideProjectSessionUnbind({
      ...unbindFacts,
      receipt_count: 4096,
    }),
    { kind: "reject", code: "history_capacity_exhausted" },
  );
});

test("Turn effects admit once and release while retirement is closing", () => {
  const goalRef = {
    goal_id: "release",
    goal_instance_id: INSTANCE_A,
  };
  const admission = {
    schema_version: "loopx_source_turn_effect_admission_v1",
    goal_ref: goalRef,
    turn_key: `sha256:${"c".repeat(64)}`,
    step_kind: "durable_writeback",
    effect_ref: `sha256:${"d".repeat(64)}`,
  };
  const admitted = decideSourceTurnEffectAdmission({
    profile_id: "source_session_v1",
    requested_goal_ref: goalRef,
    current_goal_ref: goalRef,
    gate: null,
    admission,
    existing_admission: null,
  });
  assert.equal(admitted.kind, "commit");
  if (admitted.kind !== "commit") return;
  assert.equal(admitted.gate.state, "open");
  assert.throws(
    () => decideSourceTurnEffectAdmission({
      profile_id: "source_session_v1",
      requested_goal_ref: goalRef,
      current_goal_ref: goalRef,
      gate: admitted.gate,
      admission: {
        ...admission,
        step_kind: "validation",
      },
      existing_admission: null,
    }),
    /step_kind/,
  );
  assert.deepEqual(
    decideSourceTurnEffectAdmission({
      profile_id: "source_session_v1",
      requested_goal_ref: goalRef,
      current_goal_ref: goalRef,
      gate: admitted.gate,
      admission,
      existing_admission: admission,
    }),
    {
      kind: "replay",
      gate: admitted.gate,
      admission,
    },
  );

  const closing = decideSourceTurnEffectGate({
    profile_id: "source_session_v1",
    operation: "close",
    operation_id: "recreate-release",
    request_digest: `sha256:${"e".repeat(64)}`,
    requested_goal_ref: goalRef,
    current_goal_ref: goalRef,
    reserved_goal_ref: {
      goal_id: "release",
      goal_instance_id: INSTANCE_B,
    },
    gate: admitted.gate,
  });
  assert.equal(closing.kind, "commit");
  if (closing.kind !== "commit") return;
  assert.deepEqual(
    decideSourceTurnEffectAdmission({
      profile_id: "source_session_v1",
      requested_goal_ref: goalRef,
      current_goal_ref: goalRef,
      gate: closing.gate,
      admission: {
        ...admission,
        step_kind: "quota_spend",
        effect_ref: `sha256:${"f".repeat(64)}`,
      },
      existing_admission: null,
    }),
    { kind: "reject", code: "goal_retirement_in_progress" },
  );
  assert.deepEqual(
    decideSourceTurnEffectAbsentResolution({
      profile_id: "source_session_v1",
      current_goal_ref: goalRef,
      gate: closing.gate,
      admission,
      existing_admission: admission,
    }),
    { kind: "abort" },
  );
  assert.deepEqual(
    decideSourceTurnEffectRelease({
      profile_id: "source_session_v1",
      current_goal_ref: goalRef,
      gate: closing.gate,
      admission,
      existing_admission: admission,
    }),
    { kind: "commit" },
  );
});

test("Turn effect publication requires an empty matching closing gate", () => {
  const facts = {
    profile_id: "source_session_v1",
    operation: "close",
    operation_id: "recreate-release",
    request_digest: `sha256:${"e".repeat(64)}`,
    requested_goal_ref: {
      goal_id: "release",
      goal_instance_id: INSTANCE_A,
    },
    current_goal_ref: {
      goal_id: "release",
      goal_instance_id: INSTANCE_A,
    },
    reserved_goal_ref: {
      goal_id: "release",
      goal_instance_id: INSTANCE_B,
    },
    gate: null,
  };
  const closing = decideSourceTurnEffectGate(facts);
  assert.equal(closing.kind, "commit");
  if (closing.kind !== "commit") return;

  assert.deepEqual(
    decideSourceTurnEffectGate({
      ...facts,
      operation: "publish",
      gate: closing.gate,
      admission_count: 1,
    }),
    { kind: "reject", code: "effect_drain_required" },
  );
  assert.deepEqual(
    decideSourceTurnEffectGate({
      ...facts,
      operation: "publish",
      gate: closing.gate,
      admission_count: 0,
    }),
    {
      kind: "commit",
      gate: {
        schema_version: "loopx_source_turn_effect_gate_v1",
        state: "open",
        goal_ref: facts.reserved_goal_ref,
      },
    },
  );
  assert.deepEqual(
    decideSourceTurnEffectGate({
      ...facts,
      operation: "publish",
      current_goal_ref: facts.reserved_goal_ref,
      gate: null,
      admission_count: 0,
    }),
    {
      kind: "replay",
      gate: {
        schema_version: "loopx_source_turn_effect_gate_v1",
        state: "open",
        goal_ref: facts.reserved_goal_ref,
      },
    },
  );
});

test("historical recreation replay preserves a later Goal gate", () => {
  const repairFacts = {
    profile_id: "source_session_v1",
    operation: "repair",
    operation_id: "recreate-release-b",
    request_digest: `sha256:${"e".repeat(64)}`,
    requested_goal_ref: {
      goal_id: "release",
      goal_instance_id: INSTANCE_A,
    },
    reserved_goal_ref: {
      goal_id: "release",
      goal_instance_id: INSTANCE_B,
    },
    admission_count: 1,
  };
  const laterGate = {
    schema_version: "loopx_source_turn_effect_gate_v1",
    state: "closing",
    retired_goal_ref: {
      goal_id: "release",
      goal_instance_id: INSTANCE_B,
    },
    new_goal_ref: {
      goal_id: "release",
      goal_instance_id: INSTANCE_C,
    },
    operation_id: "recreate-release-c",
    request_digest: `sha256:${"f".repeat(64)}`,
  };
  assert.deepEqual(
    decideSourceTurnEffectGate({
      ...repairFacts,
      current_goal_ref: laterGate.retired_goal_ref,
      gate: laterGate,
    }),
    { kind: "preserve" },
  );
  assert.deepEqual(
    decideSourceTurnEffectGate({
      ...repairFacts,
      current_goal_ref: repairFacts.requested_goal_ref,
      gate: {
        schema_version: "loopx_source_turn_effect_gate_v1",
        state: "open",
        goal_ref: repairFacts.requested_goal_ref,
      },
    }),
    { kind: "reject", code: "recreation_operation_conflict" },
  );
  assert.deepEqual(
    decideSourceTurnEffectGate({
      ...repairFacts,
      current_goal_ref: laterGate.new_goal_ref,
      gate: {
        ...laterGate,
        retired_goal_ref: repairFacts.requested_goal_ref,
        operation_id: "conflicting-recreation",
      },
    }),
    { kind: "reject", code: "recreation_operation_conflict" },
  );
});

test("recreation publishes the reserved successor once and replays it exactly", () => {
  const facts = {
    profile_id: "source_session_v1",
    operation_id: "recreate-release-b",
    request_digest: `sha256:${"b".repeat(64)}`,
    requested_goal_ref: {
      goal_id: "release",
      goal_instance_id: INSTANCE_A,
    },
    current_goal_ref: {
      goal_id: "release",
      goal_instance_id: INSTANCE_A,
    },
    reserved_goal_ref: {
      goal_id: "release",
      goal_instance_id: INSTANCE_B,
    },
    prior_receipt: null,
    lifetime_receipt_count: 0,
    session_receipt_count: 0,
    retiring_binding_count: 2,
  };
  const committed = {
    kind: "commit",
    retired_goal_ref: {
      goal_id: "release",
      goal_instance_id: INSTANCE_A,
    },
    new_goal_ref: {
      goal_id: "release",
      goal_instance_id: INSTANCE_B,
    },
  };

  assert.deepEqual(decideGoalRecreation(facts), committed);
  assert.deepEqual(
    decideGoalRecreation({
      ...facts,
      current_goal_ref: {
        goal_id: "release",
        goal_instance_id: INSTANCE_B,
      },
    }),
    { kind: "reject", code: "stale_goal_instance" },
  );

  const receipt = {
    schema_version: "loopx_goal_recreation_receipt_v1",
    operation_id: "recreate-release-b",
    request_digest: `sha256:${"b".repeat(64)}`,
    retired_goal_ref: committed.retired_goal_ref,
    new_goal_ref: committed.new_goal_ref,
    retired_session_ids: ["session-a", "session-b"],
  };
  assert.deepEqual(
    decideGoalRecreation({
      ...facts,
      current_goal_ref: committed.new_goal_ref,
      prior_receipt: receipt,
      lifetime_receipt_count: 1024,
      session_receipt_count: 4096,
    }),
    { kind: "replay", receipt },
  );
});

test("recreation rejects new work that would exceed either history capacity", () => {
  const facts = {
    profile_id: "source_session_v1",
    operation_id: "recreate-release-b",
    request_digest: `sha256:${"b".repeat(64)}`,
    requested_goal_ref: {
      goal_id: "release",
      goal_instance_id: INSTANCE_A,
    },
    current_goal_ref: {
      goal_id: "release",
      goal_instance_id: INSTANCE_A,
    },
    reserved_goal_ref: {
      goal_id: "release",
      goal_instance_id: INSTANCE_B,
    },
    prior_receipt: null,
    lifetime_receipt_count: 0,
    session_receipt_count: 0,
    retiring_binding_count: 0,
  };

  assert.deepEqual(
    decideGoalRecreation({
      ...facts,
      lifetime_receipt_count: 1024,
    }),
    { kind: "reject", code: "history_capacity_exhausted" },
  );
  assert.deepEqual(
    decideGoalRecreation({
      ...facts,
      session_receipt_count: 4096,
      retiring_binding_count: 1,
    }),
    { kind: "reject", code: "session_history_capacity_exhausted" },
  );
  assert.deepEqual(
    decideGoalRecreation({
      ...facts,
      session_receipt_count: 4095,
      retiring_binding_count: 1,
    }),
    {
      kind: "commit",
      retired_goal_ref: facts.requested_goal_ref,
      new_goal_ref: facts.reserved_goal_ref,
    },
  );
});
