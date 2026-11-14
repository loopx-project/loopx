import assert from "node:assert/strict";
import test from "node:test";

import {evaluateQuotaSchedulingWriteScopesOverlap} from "../../loopx/control_plane/work_items/task_lease_acquire_decision.ts";

import {
  createEffectRuntimeHandlers,
  dispatchEffectRuntimeMethod,
} from "../../loopx/control_plane/effect_runtime_handlers.ts";

const handlers = createEffectRuntimeHandlers({
  fingerprint: "test-fingerprint",
  requestShutdown: () => undefined,
});

test("runtime boundary rejects incomplete settlement identity", async () => {
  await assert.rejects(
    dispatchEffectRuntimeMethod(handlers, "settlement.identity", {
      goal_id: "goal",
      turn_instance_id: "turn",
    }),
    /identity\.agent_id must be a non-empty string/,
  );
});

test("runtime boundary rejects a result carrying value and failure", async () => {
  await assert.rejects(
    dispatchEffectRuntimeMethod(handlers, "settlement.bind_gate", {
      result: {
        value: { impossible: true },
        receipts: [],
        failure: {
          kind: "permission_denied",
          step_kind: "durable_writeback",
          reason: "denied",
        },
      },
    }),
    /cannot carry both a value and a failure/,
  );
});

test("runtime boundary rejects malformed journal inspection request", async () => {
  await assert.rejects(
    dispatchEffectRuntimeMethod(handlers, "turn_journal.inspect", {
      schema_version: "unsupported",
      journal: {},
      goal_id: "goal",
      agent_id: "agent",
      turn_key: "turn",
    }),
    /request schema mismatch/,
  );
});

test("runtime exposes settlement-addressed Turn evidence-chain readback", async () => {
  await assert.rejects(
    dispatchEffectRuntimeMethod(handlers, "turn_journal.evidence_chain", {}),
    /settlement_identity must be an object/,
  );
});

test("runtime exposes the native task-lease acquire transaction", async () => {
  await assert.rejects(
    dispatchEffectRuntimeMethod(handlers, "task_lease.acquire.native", {
      schema_version: "loopx_task_lease_acquire_native_v0",
    }),
    /authority must be an object/,
  );
});

for (const retired of ["task_lease.acquire.decide", "task_lease.lifecycle.decide",
  "coordination.handoff_mode.plan"]) {
  test(`retired Python-only decision RPC ${retired} fails closed`, async () => {
    await assert.rejects(dispatchEffectRuntimeMethod(handlers, retired, {}),
      /unsupported.*method|unknown.*method|not registered/i);
  });
}

test("runtime exposes the canonical task-lease write-scope rule", async () => {
  const result = await dispatchEffectRuntimeMethod(
    handlers,
    "task_lease.write_scopes.overlap",
    { left: ["docs/**"], right: ["docs/reference/rfc.md"] },
  ) as Record<string, unknown>;

  assert.equal(result.overlap, true);
});

test("scheduling write-scope overlap folds case and keeps subtree containment", async () => {
  const rows: [string, string, boolean][] = [
    ["src/Main.ts", "src/main.ts", true],
    ["src/MAIN.ts", "src/main.ts", true],
    ["src/\u03a3.ts", "src/\u03c2.ts", true],
    ["src/\u00df.ts", "src/SS.ts", true],
    ["src/", "SRC/", true],
    ["src/", "src/a.ts", true],
    ["src/*.ts", "src/*.TS", true],
    ["src/*.ts", "src/a.ts", true],
    ["src/a.ts", "src/b.ts", false],
    ["src/main.ts", "src/other.ts", false],
    ["src/a.ts", "src/a.txt", false],
    ["src", "src/a.ts", true],
    ["src/main", "src/main/x", true],
    ["src/a", "src/a/b", true],
    ["src/a.ts", "src/a.ts/b", true],
  ];
  for (const [left, right, overlap] of rows) {
    const scheduling = evaluateQuotaSchedulingWriteScopesOverlap({left: [left], right: [right]});
    assert.equal(scheduling.overlap, overlap, `scheduling ${left} vs ${right}`);
  }
  // The in-process scheduling owner stays wider than the canonical lease rule.
  const canonical = await dispatchEffectRuntimeMethod(
    handlers,
    "task_lease.write_scopes.overlap",
    { left: ["src"], right: ["src/a.ts"] },
  ) as Record<string, unknown>;
  assert.equal(canonical.overlap, false);
});

test("runtime exposes the source-session lifetime decisions", async () => {
  const goalRef = {
    goal_id: "release",
    goal_instance_id: "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  };
  const bindingFacts = {
    profile_id: "source_session_v1",
    operation_id: "bind-release",
    request_digest: `sha256:${"a".repeat(64)}`,
    session_id: "session-a",
    requested_goal_ref: goalRef,
    current_goal_ref: goalRef,
    current_binding: null,
    prior_receipt: null,
    binding_count: 0,
    receipt_count: 0,
  };

  assert.equal(
    (
      await dispatchEffectRuntimeMethod(
        handlers,
        "goal.source_session.bind.decide",
        bindingFacts,
      ) as Record<string, unknown>
    ).kind,
    "commit",
  );
  assert.equal(
    (
      await dispatchEffectRuntimeMethod(
        handlers,
        "goal.source_session.unbind.decide",
        bindingFacts,
      ) as Record<string, unknown>
    ).kind,
    "commit",
  );
  assert.equal(
    (
      await dispatchEffectRuntimeMethod(
        handlers,
        "goal.source_session.recreate.decide",
        {
          profile_id: "source_session_v1",
          operation_id: "recreate-release",
          request_digest: `sha256:${"b".repeat(64)}`,
          requested_goal_ref: goalRef,
          current_goal_ref: goalRef,
          reserved_goal_ref: {
            goal_id: "release",
            goal_instance_id: "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
          },
          prior_receipt: null,
          lifetime_receipt_count: 0,
          session_receipt_count: 0,
          retiring_binding_count: 0,
        },
      ) as Record<string, unknown>
    ).kind,
    "commit",
  );
  assert.deepEqual(
    await dispatchEffectRuntimeMethod(
      handlers,
      "goal.chat_session.lifecycle.decide",
      {
        operation: "select",
        profile_id: "source_session_v1",
        current_goal_ref: goalRef,
        candidates: [],
      },
    ),
    { kind: "create", goal_ref: goalRef },
  );
});

test("runtime exposes the canonical Goal acceptance lifecycle transition", async () => {
  const result = await dispatchEffectRuntimeMethod(
    handlers,
    "goal.acceptance.lifecycle.transition",
    {},
  ) as Record<string, unknown>;
  assert.equal(result.status, "failed");
  assert.match(String(result.reason), /runtime_root/);
});

test("runtime boundary registers the quota monitor-poll transaction", async () => {
  await assert.rejects(
    dispatchEffectRuntimeMethod(handlers, "quota.monitor_poll.commit", {}),
    /Quota monitor-poll commit request schema mismatch/,
  );
});

test("completion policy has no standalone runtime handler", async () => {
  await assert.rejects(
    dispatchEffectRuntimeMethod(
      handlers,
      "todo.completion_policy.resolve",
      {},
    ),
    /unsupported Effect runtime method/,
  );
});

test("preauthorized lease fence has no standalone runtime handler", async () => {
  await assert.rejects(
    dispatchEffectRuntimeMethod(
      handlers,
      "task_lease.terminal_fence.decide",
      {},
    ),
    /unsupported Effect runtime method/,
  );
});

for (const retired of [
  "todo.field_update.plan",
  "todo.public_update.plan",
  "todo.claim.decide",
  "todo.external_wait.plan",
  "quota.automation_cadence.schedule",
  "work_item.planning_horizon.project",
  "work_item.planning_inventory.project",
  "work_item.planning_inventory.detail",
  "goal.vision_wait.coverage",
  "task_lease.owner_eligibility",
  "coordination.ownership_observation",
  "coordination.local_authority.legacy_writer_fence.engage",
  "scheduler.monitor_successor.plan",
  "coordination.local_authority_shadow.record",
  "coordination.runtime_shadow.commit_entry",
  "governed_capability.validate_admission",
  "governed_capability.settlement_status",
  "collaboration.delegation.turn_plan",
  "settlement.receipt_bound_terminal_phase",
]) {
  test(`unused leaf RPC ${retired} fails closed`, async () => {
    assert.equal(handlers.has(retired), false);
    await assert.rejects(dispatchEffectRuntimeMethod(handlers, retired, {}),
      {code: "unsupported_method"});
  });
}


test("explicit Host retry is registered and requires a valid typed failure and intent", async () => {
  const failure = {schema_version: "loopx_turn_host_failure_v0", kind: "quota_exhausted",
    attempt: 1, retryable: false};
  assert.deepEqual(await dispatchEffectRuntimeMethod(handlers, "host_failure.retry",
    {failure, retry_failed: true}), {allowed: true, check: null});
  const held = await dispatchEffectRuntimeMethod(handlers, "host_failure.retry",
    {failure, retry_failed: false}) as Record<string, unknown>;
  assert.equal(held.allowed, false);
  for (const params of [{failure}, {failure, retry_failed: "true"},
    {failure, retry_failed: true, extra: true}, {retry_failed: true},
    {failure: {...failure, kind: "not_registered"}, retry_failed: true},
    {failure: {...failure, attempt: true}, retry_failed: true},
    {failure: {...failure, retryable: true}, retry_failed: true}]) {
    await assert.rejects(dispatchEffectRuntimeMethod(handlers, "host_failure.retry", params));
  }
});
