import assert from "node:assert/strict";
import test from "node:test";

import {
  CAPABILITY_HOOK_REGISTRATION_SCHEMA_VERSION,
  CAPABILITY_INTENT_SCHEMA_VERSION,
  INTERACTION_PROJECTION_HOOK_RESULT_SCHEMA_VERSION,
  POST_WRITEBACK_HOOK_INPUT_SCHEMA_VERSION,
  POST_WRITEBACK_HOOK_RECEIPT_SCHEMA_VERSION,
  POST_WRITEBACK_HOOK_REGISTRATION_SCHEMA_VERSION,
  POST_WRITEBACK_HOOK_RESULT_SCHEMA_VERSION,
  TURN_START_HOOK_REGISTRATION_SCHEMA_VERSION,
  TURN_START_HOOK_RESULT_SCHEMA_VERSION,
  validateInteractionProjectionHookInvocation,
  validatePostWritebackHookInput,
  validatePostWritebackHookInvocation,
  validatePostWritebackHookReceipt,
  validateTurnStartHookInvocation,
  validateTurnStartHookRegistration,
} from "../../loopx/control_plane/capability_hooks.ts";

function postWritebackRegistration(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: POST_WRITEBACK_HOOK_REGISTRATION_SCHEMA_VERSION,
    hook_id: "periodic_report.stage_completion",
    capability_id: "periodic-report",
    policy_version: "weekly-v1",
    phase: "post_writeback",
    event_kinds: ["refresh_state", "todo_complete"],
    intent_kinds: ["periodic_report.trigger_evaluation"],
    budget: {
      max_invocations_per_dispatch: 1,
      max_input_bytes: 64 * 1024,
      max_result_bytes: 16 * 1024,
    },
    failure_policy: "isolate",
    requested_read_scope: ["stage_completion"],
    requested_write_scope: [],
    ...overrides,
  };
}

function postWritebackInput(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: POST_WRITEBACK_HOOK_INPUT_SCHEMA_VERSION,
    receipt: {
      schema_version: "loopx_rollout_event_v0",
      event_id: "evt-stage-1",
      event_kind: "refresh_state",
      status: "appended",
      recorded_at: "2026-08-30T01:00:00+08:00",
      durable: true,
    },
    identity: {
      goal_id: "goal-1",
      agent_id: "agent-1",
      todo_id: "todo-1",
      turn_instance_id: "turn-1",
      effect_id: "goal-1:agent-1:todo-1:turn-1",
    },
    state_version: "vision-revision-2",
    projection: {
      stage_completion: {
        schema_version: "periodic_report_stage_completion_receipt_v0",
        stage_identity: "stage-123",
      },
    },
    ...overrides,
  };
}

function postWritebackResult(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: POST_WRITEBACK_HOOK_RESULT_SCHEMA_VERSION,
    hook_id: "periodic_report.stage_completion",
    capability_id: "periodic-report",
    phase: "post_writeback",
    status: "intent",
    intent: {
      schema_version: CAPABILITY_INTENT_SCHEMA_VERSION,
      intent_kind: "periodic_report.trigger_evaluation",
      idempotency_key: "periodic-report:stage-123",
      source_receipt_id: "evt-stage-1",
      payload: {
        stage_identity: "stage-123",
      },
      requested_write_scope: [],
    },
    ...overrides,
  };
}

test("post-writeback hook admits one receipt-bound effect-free intent", () => {
  const input = validatePostWritebackHookInput({
    registration: postWritebackRegistration(),
    hook_input: postWritebackInput(),
  });
  assert.equal((input.receipt as Record<string, unknown>).durable, true);

  const result = validatePostWritebackHookInvocation({
    registration: postWritebackRegistration(),
    hook_input: postWritebackInput(),
    result: postWritebackResult(),
  });
  assert.equal(result.status, "intent");
  assert.equal(
    (result.intent as Record<string, unknown>).source_receipt_id,
    "evt-stage-1",
  );
});

test("post-writeback hook admits durable Todo completion events", () => {
  const input = postWritebackInput();
  (input.receipt as Record<string, unknown>).event_kind = "todo_complete";

  const admitted = validatePostWritebackHookInput({
    registration: postWritebackRegistration(),
    hook_input: input,
  });

  assert.equal(
    (admitted.receipt as Record<string, unknown>).event_kind,
    "todo_complete",
  );
});

test("post-writeback hook rejects pending receipts, undeclared reads, and write authority", () => {
  const pending = postWritebackInput();
  (pending.receipt as Record<string, unknown>).durable = false;
  assert.throws(
    () => validatePostWritebackHookInput({
      registration: postWritebackRegistration(),
      hook_input: pending,
    }),
    /durable event/,
  );

  assert.throws(
    () => validatePostWritebackHookInput({
      registration: postWritebackRegistration(),
      hook_input: postWritebackInput({ projection: { raw_task: "private" } }),
    }),
    /requested_read_scope/,
  );

  const withWrite = postWritebackResult();
  (withWrite.intent as Record<string, unknown>).requested_write_scope = ["lark_send"];
  assert.throws(
    () => validatePostWritebackHookInvocation({
      registration: postWritebackRegistration(),
      hook_input: postWritebackInput(),
      result: withWrite,
    }),
    /cannot grant write scope/,
  );
});

test("post-writeback hook binds intent to the exact durable receipt", () => {
  const mismatched = postWritebackResult();
  (mismatched.intent as Record<string, unknown>).source_receipt_id = "evt-other";
  assert.throws(
    () => validatePostWritebackHookInvocation({
      registration: postWritebackRegistration(),
      hook_input: postWritebackInput(),
      result: mismatched,
    }),
    /does not bind/,
  );

  assert.throws(
    () => validatePostWritebackHookInvocation({
      registration: postWritebackRegistration(),
      hook_input: postWritebackInput(),
      result: postWritebackResult({ intent: [] }),
    }),
    /object/,
  );
});

test("post-writeback hook accepts Todo-less identity but rejects an empty Todo id", () => {
  const todoLess = postWritebackInput();
  (todoLess.identity as Record<string, unknown>).todo_id = null;
  const admitted = validatePostWritebackHookInput({
    registration: postWritebackRegistration(),
    hook_input: todoLess,
  });
  assert.equal((admitted.identity as Record<string, unknown>).todo_id, null);

  const incomplete = postWritebackInput();
  (incomplete.identity as Record<string, unknown>).todo_id = "";
  assert.throws(
    () => validatePostWritebackHookInput({
      registration: postWritebackRegistration(),
      hook_input: incomplete,
    }),
    /todo_id/,
  );
});

test("post-writeback sidecar receipt revalidates the exact typed intent", () => {
  const receipt = validatePostWritebackHookReceipt({
    registration: postWritebackRegistration(),
    hook_input: postWritebackInput(),
    receipt: {
      schema_version: POST_WRITEBACK_HOOK_RECEIPT_SCHEMA_VERSION,
      dispatch_id: `pwh_${"a".repeat(64)}`,
      hook_id: "periodic_report.stage_completion",
      capability_id: "periodic-report",
      source_receipt_id: "evt-stage-1",
      status: "intent_recorded",
      intent: (postWritebackResult().intent as Record<string, unknown>),
      error_code: null,
      attempt_count: 1,
      recorded_at: "2026-08-30T01:00:00+08:00",
    },
  });
  assert.equal(receipt.status, "intent_recorded");

  assert.throws(
    () => validatePostWritebackHookReceipt({
      registration: postWritebackRegistration(),
      hook_input: postWritebackInput(),
      receipt: { ...receipt, source_receipt_id: "evt-other" },
    }),
    /identity/,
  );

  const retryable = validatePostWritebackHookReceipt({
    registration: postWritebackRegistration(),
    hook_input: postWritebackInput(),
    receipt: {
      ...receipt,
      status: "retryable_failure",
      intent: null,
      error_code: "producer_failed",
      attempt_count: 2,
    },
  });
  assert.equal(retryable.status, "retryable_failure");
});

function registration(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: CAPABILITY_HOOK_REGISTRATION_SCHEMA_VERSION,
    hook_id: "repository_change_window.repository_delivery",
    capability_id: "repository-change-window",
    phase: "interaction_projection",
    projection_slots: ["repository_delivery"],
    budget: {
      max_invocations_per_dispatch: 1,
      max_result_bytes: 16 * 1024,
    },
    failure_policy: "isolate",
    requested_read_scope: ["repository_status"],
    requested_write_scope: [],
    ...overrides,
  };
}

function status(allowed: boolean) {
  return {
    ok: true,
    schema_version: "repository_change_window_git_hook_status_v2",
    status: "ready",
    installed: true,
    enabled: true,
    provider_id: "git-hook",
    enforcement_level: "reference_guard",
    contains_personal_path: false,
    checks: [{ check: "provider_schema", ok: true, status: "current" }],
    decision: {
      schema_version: "repository_change_window_decision_v0",
      allowed,
      reason: allowed ? "outside_blocked_window" : "inside_blocked_window",
      observed_at: "2026-08-24T11:00:00+08:00",
      next_eligible_at: allowed
        ? "2026-08-24T11:00:00+08:00"
        : "2026-08-24T12:00:00+08:00",
    },
  };
}

function candidate(payload: unknown, overrides: Record<string, unknown> = {}) {
  return {
    schema_version: INTERACTION_PROJECTION_HOOK_RESULT_SCHEMA_VERSION,
    hook_id: "repository_change_window.repository_delivery",
    capability_id: "repository-change-window",
    phase: "interaction_projection",
    status: "candidate",
    projection_slot: "repository_delivery",
    payload,
    ...overrides,
  };
}

test("verified capability candidate projects separate preparation and delivery admission", () => {
  const blocked = validateInteractionProjectionHookInvocation({
    registration: registration(),
    result: candidate(status(false)),
  });
  assert.equal(blocked.status, "projected");
  assert.deepEqual(blocked.projection, {
    schema_version: "repository_delivery_gate_v0",
    provider_id: "git-hook",
    provider_verified: true,
    authority_scope: "local_repository_change_window",
    enforcement_level: "reference_guard",
    state: "blocked",
    change_window_admission: {
      prepare_dirty_worktree: true,
      validate_dirty_worktree: true,
      commit: false,
      push: false,
    },
    reason: "inside_blocked_window",
    linked_worktrees_share_provider: true,
    separate_clones_in_scope: false,
    path_free: true,
    remote_write_authority_granted: false,
    next_eligible_at: "2026-08-24T12:00:00+08:00",
  });

  const admitted = validateInteractionProjectionHookInvocation({
    registration: registration(),
    result: candidate(status(true)),
  });
  assert.equal(
    (admitted.projection as Record<string, unknown>).state,
    "admitted",
  );
  assert.equal(
    Object.hasOwn(admitted.projection as object, "next_eligible_at"),
    false,
  );
});

test("pending periodic report projects generation with configured standing delivery authority", () => {
  const result = validateInteractionProjectionHookInvocation({
    registration: {
      schema_version: CAPABILITY_HOOK_REGISTRATION_SCHEMA_VERSION,
      hook_id: "periodic_report.pending_intent",
      capability_id: "periodic-report",
      phase: "interaction_projection",
      projection_slots: ["pending_capability_intent"],
      budget: { max_invocations_per_dispatch: 1, max_result_bytes: 16384 },
      failure_policy: "isolate",
      requested_read_scope: ["post_writeback_intent_journal"],
      requested_write_scope: [],
    },
    result: {
      schema_version: INTERACTION_PROJECTION_HOOK_RESULT_SCHEMA_VERSION,
      hook_id: "periodic_report.pending_intent",
      capability_id: "periodic-report",
      phase: "interaction_projection",
      status: "candidate",
      projection_slot: "pending_capability_intent",
      payload: {
        schema_version: "pending_capability_intent_projection_v0",
        capability_id: "periodic-report",
        intent_kind: "periodic_report.trigger_evaluation",
        idempotency_key: "periodic-report:stage-example",
        intent_digest: `sha256:${"a".repeat(64)}`,
        goal_id: "goal-example",
        agent_id: "agent-example",
        state: "pending",
        action_kind: "consume_periodic_report_intent",
        action_summary: "Generate the exact report and queue configured delivery.",
        command: "loopx periodic-report consume-pending --goal-id goal-example --agent-id agent-example --execute",
        generation_authorized: true,
        external_delivery_authorized: true,
        agent_read_required: true,
      },
    },
  });
  assert.equal(result.status, "projected");
  assert.equal(
    (result.projection as JsonObject).external_delivery_authorized,
    true,
  );
  assert.equal(
    (result.projection as JsonObject).agent_read_required,
    true,
  );
});

test("uninstalled or drifted provider is diagnostic-only", () => {
  const external = {
    ...status(true),
    installed: false,
    enabled: false,
    status: "effective_external_guard_detected",
  };
  const result = validateInteractionProjectionHookInvocation({
    registration: registration(),
    result: candidate(external),
  });
  assert.equal(result.status, "not_applicable");
  assert.equal(result.projection, null);

  const failedCheck = status(true);
  failedCheck.checks[0].ok = false;
  assert.equal(validateInteractionProjectionHookInvocation({
    registration: registration(),
    result: candidate(failedCheck),
  }).status, "not_applicable");
});

test("registration denies effects and candidates cannot escape declared slots", () => {
  assert.throws(
    () => validateInteractionProjectionHookInvocation({
      registration: registration({ requested_write_scope: ["git_config"] }),
      result: candidate(status(true)),
    }),
    /cannot request write scope/,
  );
  assert.throws(
    () => validateInteractionProjectionHookInvocation({
      registration: registration(),
      result: candidate(status(true), { projection_slot: "other_slot" }),
    }),
    /not registered/,
  );
});

function turnStartRegistration(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: TURN_START_HOOK_REGISTRATION_SCHEMA_VERSION,
    hook_id: "operator_inbox.turn_start_sync",
    capability_id: "operator-inbox",
    phase: "turn_start",
    budget: {
      max_invocations_per_dispatch: 1,
      max_result_bytes: 16 * 1024,
    },
    failure_policy: "isolate",
    requested_read_scope: ["provider_history"],
    requested_write_scope: ["owner_private_inbox", "owner_private_cursor"],
    required_read: {
      kind: "operator_inbox",
      command: "loopx inbox drain --goal-id fixture",
      reason: "read newly synchronized operator evidence",
      ordering: "before_work",
    },
    ...overrides,
  };
}

function turnStartResult(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: TURN_START_HOOK_RESULT_SCHEMA_VERSION,
    hook_id: "operator_inbox.turn_start_sync",
    capability_id: "operator-inbox",
    phase: "turn_start",
    status: "observed",
    observation_count: 2,
    agent_read_required: true,
    external_reads_performed: true,
    external_writes_performed: false,
    local_private_state_mutated: true,
    private_content_returned: false,
    provider_payload_returned: false,
    error_code: null,
    ...overrides,
  };
}

test("turn-start read commands retain explicit routes with a bounded byte budget", () => {
  const read = {kind: "capability_intent", reason: "Read due report facts", ordering: "before_work"};
  const command = `loopx --registry /${"project/".repeat(35)}registry.json --runtime-root /${"runtime/".repeat(35)}state periodic-report consume-pending --goal-id example --agent-id reporter`;
  assert.ok(command.length > 360 && command.length < 1024);
  validateTurnStartHookRegistration(turnStartRegistration({required_read: {...read, command}}));
  for (const invalid of ["x".repeat(1025), "界".repeat(342), "loopx\nother-command"]) {
    assert.throws(() => validateTurnStartHookRegistration(turnStartRegistration({
      required_read: {...read, command: invalid},
    })), /outside the admitted envelope/);
  }
});

test("turn-start reads can opt into a bounded prompt budget without changing defaults", () => {
  const registration = turnStartRegistration();
  const read = registration.required_read;
  assert.deepEqual(validateTurnStartHookRegistration(registration).required_read, read);
  const budgeted = { ...read, prompt_budget_bytes: 1_536 };
  assert.deepEqual(validateTurnStartHookRegistration({ ...registration, required_read: budgeted }).required_read, budgeted);
  for (const value of [0, -1, 2_049, 1.5, true, "1536", null]) {
    assert.throws(() => validateTurnStartHookRegistration({ ...registration,
      required_read: { ...read, prompt_budget_bytes: value },
    }), /prompt budget/);
  }
  assert.throws(() => validateTurnStartHookRegistration({ ...registration,
    required_read: { ...read, prompt_budget_bytes: 64 },
  }), /exceeds its declared prompt budget/);
});

test("turn-start observations require Agent reading without returning private content", () => {
  const observed = validateTurnStartHookInvocation({
    registration: turnStartRegistration(),
    result: turnStartResult(),
  });
  assert.equal(observed.status, "observed");
  assert.equal(observed.agent_read_required, true);
  assert.equal(observed.observation_count, 2);

  assert.throws(
    () => validateTurnStartHookInvocation({
      registration: turnStartRegistration(),
      result: turnStartResult({ agent_read_required: false }),
    }),
    /Agent reading|inconsistent/,
  );
  assert.throws(
    () => validateTurnStartHookInvocation({
      registration: turnStartRegistration(),
      result: turnStartResult({ private_content_returned: true }),
    }),
    /private provider content/,
  );
  assert.throws(
    () => validateTurnStartHookInvocation({
      registration: turnStartRegistration({ required_read: null }),
      result: turnStartResult(),
    }),
    /required read route is missing/,
  );
  assert.throws(
    () => validateTurnStartHookInvocation({
      registration: turnStartRegistration({
        required_read: {
          kind: "operator_inbox",
          command: "loopx inbox drain --goal-id fixture",
          reason: "read newly synchronized operator evidence",
          ordering: "before_work",
          private_message_text: "must-not-enter-the-contract",
        },
      }),
      result: turnStartResult(),
    }),
    /required_read fields are invalid/,
  );
});

test("turn-start empty, provider failure, and owner-private write scopes stay distinct", () => {
  const empty = validateTurnStartHookInvocation({
    registration: turnStartRegistration(),
    result: turnStartResult({
      status: "empty",
      observation_count: 0,
      agent_read_required: false,
      local_private_state_mutated: true,
    }),
  });
  assert.equal(empty.status, "empty");

  const failed = validateTurnStartHookInvocation({
    registration: turnStartRegistration(),
    result: turnStartResult({
      status: "failed",
      observation_count: 0,
      agent_read_required: false,
      local_private_state_mutated: false,
      error_code: "provider_contract_error",
    }),
  });
  assert.equal(failed.error_code, "provider_contract_error");

  assert.throws(
    () => validateTurnStartHookInvocation({
      registration: turnStartRegistration({
        requested_write_scope: ["repository_write"],
      }),
      result: turnStartResult(),
    }),
    /not admitted/,
  );

  assert.throws(
    () => validateTurnStartHookInvocation({
      registration: turnStartRegistration(),
      result: turnStartResult({ external_writes_performed: true }),
    }),
    /undeclared external write/,
  );

  const reacted = validateTurnStartHookInvocation({
    registration: turnStartRegistration({
      requested_write_scope: [
        "owner_private_inbox",
        "owner_private_cursor",
        "provider_message_reaction",
      ],
    }),
    result: turnStartResult({ external_writes_performed: true }),
  });
  assert.equal(reacted.external_writes_performed, true);
});


test("Explore settlement hint cannot inject a command or impose a work obligation", () => {
  const reg = registration({hook_id: "explore.settlement_attachment", capability_id: "explore",
    projection_slots: ["explore_result_attachment"], requested_read_scope: ["goal.explore_policy"]});
  const result = {schema_version: INTERACTION_PROJECTION_HOOK_RESULT_SCHEMA_VERSION,
    hook_id: reg.hook_id, capability_id: "explore", phase: "interaction_projection",
    status: "candidate", projection_slot: "explore_result_attachment", payload: {}};
  const projected = validateInteractionProjectionHookInvocation({registration: reg, result});
  const hint = projected.projection as Record<string, unknown>;
  assert.equal(hint.required, false);
  assert.equal(hint.option, "--explore-result-json <result.json>");
  assert.equal(hint.inline_option, "--agent-vision-json <vision.json>");
  assert.equal(hint.inline_field, "explore_result");
  assert.equal(hint.attachment_schema, "explore_result_attachment_v0");
  for (const payload of [{required: true}, {command: "run something"}, {guidance: "Always write"}]) {
    assert.throws(() => validateInteractionProjectionHookInvocation({registration: reg,
      result: {...result, payload}}), /presence must be empty/);
  }
  assert.throws(() => validateInteractionProjectionHookInvocation({
    registration: {...reg, capability_id: "other"}, result: {...result, capability_id: "other"},
  }), /owning capability/);
});
