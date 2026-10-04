import assert from "node:assert/strict";
import test from "node:test";

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
