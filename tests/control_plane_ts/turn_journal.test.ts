import assert from "node:assert/strict";
import test from "node:test";

import {
  interpretTurnJournal,
  interpretTurnJournalEffect,
  type TurnJournalInspectionRequest,
} from "../../loopx/control_plane/turn_driver/turn_journal.ts";

const turnKey = `sha256:${"a".repeat(64)}`;
const todoId = "todo_fixture0001";

function effectId(
  goalId: string,
  agentId: string,
  boundTodoId = todoId,
): string {
  return `${goalId}:${agentId}:${boundTodoId}:${turnKey}`;
}

function request(status = "committed"): TurnJournalInspectionRequest {
  return {
    schema_version: "loopx_turn_journal_interpretation_request_v0",
    goal_id: "fixture-goal",
    agent_id: "fixture-agent",
    turn_key: turnKey,
    journal: {
      schema_version: "loopx_turn_journal_v0",
      goal_id: "fixture-goal",
      turn_key: turnKey,
      status,
      completed_phases: [
        "host_execute",
        "typed_result",
        "validation",
        "durable_writeback",
        "quota_spend",
        "scheduler_apply",
        "scheduler_ack",
      ],
      plan: {
        turn_envelope: {
          goal_id: "fixture-goal",
          agent_id: "fixture-agent",
          action: { selected_todo: { todo_id: todoId } },
        },
        transaction: {
          turn_key: turnKey,
          settlement_plan: {
            schema_version: "quota_settlement_plan_v1",
            identity: {
              schema_version: "quota_settlement_identity_v0",
              effect_id: effectId("fixture-goal", "fixture-agent"),
              goal_id: "fixture-goal",
              agent_id: "fixture-agent",
              todo_id: todoId,
              turn_instance_id: turnKey,
            },
          },
        },
      },
      host_result: { turn_key: turnKey, private_detail: "do-not-expose" },
      receipt: { turn_key: turnKey, body: "do-not-expose" },
    },
  };
}

function failedHostRetryRequest({
  attempt = 1,
  kind = "provider_capacity",
  backoffSeconds = 30,
  failureFields = {},
  retryFields = {},
}: {
  attempt?: number;
  kind?: string;
  backoffSeconds?: number;
  failureFields?: Record<string, unknown>;
  retryFields?: Record<string, unknown>;
} = {}): TurnJournalInspectionRequest {
  const input = request("failed");
  input.retry_failed = true;
  input.journal.completed_phases = [];
  input.journal.receipt = { turn_key: turnKey, failed_phase: "host_execute" };
  input.journal.host_attempt_count = attempt;
  input.journal.host_failure = {
    schema_version: "loopx_turn_host_failure_v0",
    kind,
    attempt,
    retryable: true,
    ...failureFields,
    retry: {
      strategy: "same_configuration",
      max_attempts: 3,
      backoff_seconds: backoffSeconds,
      ...retryFields,
    },
  };
  return input;
}

test("legal terminal replay is projected without effects or private fields", () => {
  const input = request();
  const before = structuredClone(input);
  const result = interpretTurnJournal(input);

  assert.equal(result.decision, "replay_legal");
  assert.equal(result.replay_legal, true);
  assert.deepEqual(result.violations, []);
  assert.deepEqual(result.effects, []);
  assert.equal(JSON.stringify(result).includes("do-not-expose"), false);
  assert.deepEqual(input, before);
});

test("journal replay uses its own decision without a quota action slot", () => {
  const turn = interpretTurnJournalEffect(request());

  assert.equal(turn.request.kind, "turn_journal");
  assert.equal(turn.interpretation.route, "turn_journal_replay");
  assert.equal(turn.observation.decision, "replay_legal");
  assert.equal(turn.observation.should_run, false);
  assert.equal("effective_action" in turn.observation, false);
  const blocked = interpretTurnJournalEffect({...request(), agent_id: "other-agent"});
  assert.equal(blocked.observation.decision, "replay_blocked");
  assert.equal("effective_action" in blocked.observation, false);
  assert.deepEqual(turn.next_effect.cli_actions, []);
  assert.deepEqual(interpretTurnJournal(request()), {
    ok: true,
    schema_version: "loopx_turn_journal_inspection_v1",
    decision: "replay_legal",
    journal_status: "committed",
    replay_legal: true,
    goal_matches: true,
    owner_matches: true,
    turn_key_matches: true,
    phases_form_ordered_prefix: true,
    completed_phases: [
      "host_execute",
      "typed_result",
      "validation",
      "durable_writeback",
      "quota_spend",
      "scheduler_apply",
      "scheduler_ack",
    ],
    tombstone_retained: true,
    violations: [],
    journal_consistent: true,
    recovery_decision: {
      schema_version: "loopx_turn_recovery_decision_v0",
      action: "return_existing",
      can_continue: false,
      resume_from: null,
      reinvoke_host: false,
      reason: "terminal_result_retained",
      retry_failed: false,
      checks: [{ kind: "journal_consistency", outcome: "passed" }],
    },
    last_recovery: null,
    effects: [],
  });
});

test("non-terminal replay blocking does not block executor recovery", () => {
  const input = request("in_progress");
  input.journal.completed_phases = ["host_execute", "typed_result"];

  const result = interpretTurnJournal(input);

  assert.equal(result.replay_legal, false);
  assert.equal(result.journal_consistent, true);
  assert.deepEqual(result.recovery_decision, {
    schema_version: "loopx_turn_recovery_decision_v0",
    action: "continue",
    can_continue: true,
    resume_from: "validation",
    reinvoke_host: false,
    reason: "resume_in_progress",
    retry_failed: false,
    checks: [{ kind: "journal_consistency", outcome: "passed" }],
  });
});

test("scheduler recovery resumes only scheduler apply", () => {
  const input = request("scheduler_action_required");
  input.journal.completed_phases = [
    "host_execute",
    "typed_result",
    "validation",
    "durable_writeback",
    "quota_spend",
  ];

  const result = interpretTurnJournal(input);

  assert.equal(result.recovery_decision.resume_from, "scheduler_apply");
  assert.equal(result.recovery_decision.reinvoke_host, false);
  assert.equal(result.recovery_decision.reason, "resume_scheduler");
});

test("settlement identity drift blocks non-terminal recovery", () => {
  const input = request("in_progress");
  input.journal.completed_phases = [
    "host_execute",
    "typed_result",
    "validation",
  ];
  const plan = input.journal.plan as Record<string, unknown>;
  const transaction = plan.transaction as Record<string, unknown>;
  const settlement = transaction.settlement_plan as Record<string, unknown>;
  const identity = settlement.identity as Record<string, unknown>;
  identity.goal_id = "other-goal";
  identity.agent_id = "other-agent";
  identity.effect_id = effectId("other-goal", "other-agent");

  const result = interpretTurnJournal(input);

  assert.deepEqual(result.violations, [
    "goal_mismatch",
    "owner_mismatch",
    "journal_not_terminal",
  ]);
  assert.equal(result.journal_consistent, false);
  assert.deepEqual(result.recovery_decision, {
    schema_version: "loopx_turn_recovery_decision_v0",
    action: "blocked",
    can_continue: false,
    resume_from: null,
    reinvoke_host: false,
    reason: "journal_inconsistent",
    retry_failed: false,
    checks: [{ kind: "journal_consistency", outcome: "failed" }],
  });
});

test("settlement Todo binding drift blocks non-terminal recovery", () => {
  const input = request("in_progress");
  input.journal.completed_phases = [
    "host_execute",
    "typed_result",
    "validation",
  ];
  const plan = input.journal.plan as Record<string, unknown>;
  const transaction = plan.transaction as Record<string, unknown>;
  const settlement = transaction.settlement_plan as Record<string, unknown>;
  const identity = settlement.identity as Record<string, unknown>;
  identity.todo_id = "todo_other0002";
  identity.effect_id = effectId(
    "fixture-goal",
    "fixture-agent",
    "todo_other0002",
  );

  const result = interpretTurnJournal(input);

  assert.deepEqual(result.violations, [
    "settlement_binding_mismatch",
    "journal_not_terminal",
  ]);
  assert.equal(result.journal_consistent, false);
  assert.equal(result.recovery_decision.action, "blocked");
  assert.equal(result.recovery_decision.can_continue, false);
  assert.equal(result.recovery_decision.resume_from, null);
});

test("adaptive primary Todo is the authoritative recovery binding", () => {
  const input = request("in_progress");
  input.journal.completed_phases = ["host_execute", "typed_result"];
  const plan = input.journal.plan as Record<string, unknown>;
  const envelope = plan.turn_envelope as Record<string, unknown>;
  envelope.action = {
    selected_todo: { todo_id: "todo_stale0002" },
  };
  envelope.task_orchestration_contract = {
    schema_version: "task_orchestration_contract_v2",
    mode: "adaptive",
    primary_todo_id: todoId,
  };

  const result = interpretTurnJournal(input);

  assert.equal(result.journal_consistent, true);
  assert.equal(result.recovery_decision.action, "continue");
  assert.equal(result.recovery_decision.resume_from, "validation");
});

test("malformed typed settlement identity blocks recovery", () => {
  const input = request("in_progress");
  input.journal.completed_phases = [
    "host_execute",
    "typed_result",
    "validation",
  ];
  const plan = input.journal.plan as Record<string, unknown>;
  const transaction = plan.transaction as Record<string, unknown>;
  const settlement = transaction.settlement_plan as Record<string, unknown>;
  const identity = settlement.identity as Record<string, unknown>;
  identity.effect_id = "not-the-canonical-effect-id";

  const result = interpretTurnJournal(input);

  assert.deepEqual(result.violations, [
    "settlement_identity_invalid",
    "journal_not_terminal",
  ]);
  assert.equal(result.journal_consistent, false);
  assert.equal(result.recovery_decision.action, "blocked");
});

test("failed Host recovery uses only the supplied Session Binding check", () => {
  const input = request("failed");
  input.retry_failed = true;
  input.journal.completed_phases = [];
  input.journal.receipt = { turn_key: turnKey, failed_phase: "host_execute" };
  input.journal.host_recovery = {
    schema_version: "loopx_turn_host_recovery_v0",
    kind: "resume_session",
  };
  input.session_recovery_check = {
    kind: "host_session_binding",
    outcome: "failed",
    reason: "session_binding_identity_mismatch",
  };

  const result = interpretTurnJournal(input);

  assert.equal(result.recovery_decision.action, "blocked");
  assert.equal(result.recovery_decision.reason, "session_binding_identity_mismatch");
  assert.deepEqual(result.recovery_decision.checks, [
    { kind: "journal_consistency", outcome: "passed" },
    {
      kind: "host_session_binding",
      outcome: "failed",
      reason: "session_binding_identity_mismatch",
    },
  ]);
});

test("retryable Host failure may reinvoke only inside its attempt budget", () => {
  for (const [kind, backoffSeconds] of [
    ["provider_capacity", 30],
    ["provider_overloaded", 30],
    ["rate_limited", 60],
  ] as const) {
    const input = failedHostRetryRequest({ kind, backoffSeconds });
    const result = interpretTurnJournal(input);

    assert.equal(result.recovery_decision.action, "continue");
    assert.equal(result.recovery_decision.reinvoke_host, true);
    assert.deepEqual(result.recovery_decision.checks, [
      { kind: "journal_consistency", outcome: "passed" },
      { kind: "host_retry_policy", outcome: "passed" },
    ]);
  }
});

test("exhausted Host retry budget blocks another invocation", () => {
  const input = failedHostRetryRequest({ attempt: 3, backoffSeconds: 120 });

  const result = interpretTurnJournal(input);

  assert.equal(result.recovery_decision.action, "blocked");
  assert.equal(result.recovery_decision.reinvoke_host, false);
  assert.equal(result.recovery_decision.reason, "host_retry_budget_exhausted");
});

test("caller-authored Host retryability fails closed", () => {
  const input = failedHostRetryRequest({ kind: "auth_failed" });

  const result = interpretTurnJournal(input);

  assert.equal(result.recovery_decision.action, "blocked");
  assert.equal(result.recovery_decision.reason, "host_retry_contract_invalid");
});

test("output budget exhaustion cannot reinvoke the Host", () => {
  const input = failedHostRetryRequest({ kind: "output_budget_exhausted" });
  input.journal.host_failure = {
    schema_version: "loopx_turn_host_failure_v0",
    kind: "output_budget_exhausted",
    attempt: 1,
    retryable: false,
  };

  const result = interpretTurnJournal(input);

  assert.equal(result.recovery_decision.action, "blocked");
  assert.equal(result.recovery_decision.reinvoke_host, false);
  assert.equal(result.recovery_decision.reason, "host_retry_not_available");
  assert.deepEqual(result.recovery_decision.checks, [
    { kind: "journal_consistency", outcome: "passed" },
    {
      kind: "host_retry_policy",
      outcome: "failed",
      reason: "host_retry_not_available",
    },
  ]);
});

test("Host retry metadata with extra fields fails closed", () => {
  let input = failedHostRetryRequest({
    failureFields: {
      provider_message: "must-not-cross-the-public-boundary",
    },
  });

  let result = interpretTurnJournal(input);
  assert.equal(result.recovery_decision.action, "blocked");
  assert.equal(result.recovery_decision.reason, "host_retry_contract_invalid");

  input = failedHostRetryRequest({
    retryFields: { private_detail: "must-not-persist" },
  });
  result = interpretTurnJournal(input);
  assert.equal(result.recovery_decision.action, "blocked");
  assert.equal(result.recovery_decision.reason, "host_retry_contract_invalid");
});

test("prepared effects are delegated to the existing provider readback step", () => {
  const input = request("in_progress");
  input.journal.completed_phases = ["host_execute", "typed_result", "validation"];
  input.journal.effect_attempts = {
    durable_writeback: { status: "prepared", effect_ref: "effect:fixture" },
  };

  const result = interpretTurnJournal(input);

  assert.equal(result.recovery_decision.reason, "resolve_prepared_effect");
  assert.deepEqual(result.recovery_decision.checks, [
    { kind: "journal_consistency", outcome: "passed" },
    {
      kind: "prepared_effect_readback",
      outcome: "required",
      step_kind: "durable_writeback",
    },
  ]);
});

test("identity and phase violations accumulate in stable order", () => {
  const input = request();
  input.journal.goal_id = "other-goal";
  input.journal.turn_key = "sha256:other-turn";
  input.journal.completed_phases = ["host_execute", "validation"];
  const plan = input.journal.plan as Record<string, unknown>;
  const transaction = (plan.transaction ?? {}) as Record<string, unknown>;
  const settlement = (transaction.settlement_plan ?? {}) as Record<string, unknown>;
  const identity = (settlement.identity ?? {}) as Record<string, unknown>;
  identity.agent_id = "other-agent";
  identity.effect_id = effectId("fixture-goal", "other-agent");

  const result = interpretTurnJournal(input);

  assert.equal(result.decision, "replay_blocked");
  assert.deepEqual(result.violations, [
    "goal_mismatch",
    "owner_mismatch",
    "turn_key_mismatch",
    "completed_phases_not_ordered_prefix",
  ]);
});

test("non-terminal and malformed state cannot replay", () => {
  const input = request("in_progress");
  input.journal.completed_phases = "host_execute";

  const result = interpretTurnJournal(input);

  assert.equal(result.replay_legal, false);
  assert.equal(result.phases_form_ordered_prefix, false);
  assert.deepEqual(result.completed_phases, []);
  assert.deepEqual(result.violations, [
    "completed_phases_invalid",
    "journal_not_terminal",
  ]);
});

test("present malformed optional identities block replay", () => {
  const input = request();
  const hostResult = input.journal.host_result as Record<string, unknown>;
  const receipt = input.journal.receipt as Record<string, unknown>;
  hostResult.turn_key = "";
  receipt.turn_key = 7;

  const result = interpretTurnJournal(input);

  assert.equal(result.turn_key_matches, false);
  assert.deepEqual(result.violations, ["turn_key_identity_missing"]);
});

test("JSON non-string phases keep the Python boundary normalization", () => {
  const input = request();
  input.journal.completed_phases = ["host_execute", null];

  const result = interpretTurnJournal(input);

  assert.deepEqual(result.completed_phases, ["host_execute", "None"]);
  assert.equal(result.phases_form_ordered_prefix, false);
  assert.equal(result.replay_legal, false);
});
