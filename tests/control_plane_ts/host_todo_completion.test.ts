import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import { settlementIdentity } from "../../loopx/control_plane/effect_program.ts";
import {
  evaluateHostTodoCompletion,
  HOST_ADAPTER_SETTLEMENT_SCHEMA_VERSION,
  HOST_TODO_COMPLETION_REDUCTION_SCHEMA_VERSION,
  HOST_TODO_COMPLETION_TRANSACTION_SCHEMA_VERSION,
  HOST_TODO_VISION_TRANSACTION_SCHEMA_VERSION,
} from "../../loopx/control_plane/turn_driver/host_todo_completion.ts";

const todoId = "todo_abc123";

test("vision refresh shares the original delivery command and identity without a spend", () => {
  const authored = {schema_version: HOST_TODO_VISION_TRANSACTION_SCHEMA_VERSION,
    vision_path: "fixture-vision.json"};
  const first = prepare(authored);
  const recovery = evaluateHostTodoCompletion(request("prepare", {...authored, phase: "vision_refresh",
    checkpoint_read_context_id: "receipt-a"}));
  const steps = (first.provider_effect as {steps: {step_kind: string; args: string[]}[]}).steps;
  assert.deepEqual(recovery.args, [...steps.find(step => step.step_kind === "durable_writeback")!.args,
    "--checkpoint-read-context", "receipt-a"]);
  assert.deepEqual(recovery.identity, first.identity);
  assert.equal(recovery.provider_effect, undefined);
  assert.equal((recovery.args as string[]).includes("spend-slot"), false);
  assert.equal((recovery.args as string[]).includes("--next-action"), false);
});

test("host context read uses the original identity and cannot carry a decision", () => {
  const input = {schema_version: HOST_TODO_VISION_TRANSACTION_SCHEMA_VERSION, phase: "vision_context"};
  const context = evaluateHostTodoCompletion(request("prepare", input));
  const identity = prepare().identity as Record<string, unknown>;
  assert.deepEqual(context.identity, identity);
  assert.deepEqual(context.args, ["checkpoint-context", "--goal-id", "goal",
    "--agent-id", "agent", "--todo-id", todoId, "--turn-instance-id", identity.turn_instance_id]);
  assert.equal(context.provider_effect, undefined);
  for (const field of ["vision_path", "vision_unchanged_reason", "checkpoint_read_context_id"]) {
    assert.throws(() => evaluateHostTodoCompletion(request("prepare", {...input, [field]: "value"})),
      /cannot submit/);
  }
  assert.throws(() => prepare({schema_version: HOST_TODO_VISION_TRANSACTION_SCHEMA_VERSION,
    checkpoint_read_context_id: "receipt-a"}), /only to vision recovery/);
});

test("host settlement commands preserve an exact GoalRef", () => {
  const instanceId = "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
  const goalRef = {goal_id: "goal", goal_instance_id: instanceId};
  const prepared = prepare({goal_ref: goalRef});
  const steps = (
    prepared.provider_effect as {steps: {args: string[]}[]}
  ).steps;
  for (const step of steps) {
    assert.deepEqual(
      step.args.slice(step.args.indexOf("--goal-instance-id"), step.args.indexOf("--goal-instance-id") + 2),
      ["--goal-instance-id", instanceId],
    );
  }
  const context = evaluateHostTodoCompletion(request("prepare", {
    schema_version: HOST_TODO_VISION_TRANSACTION_SCHEMA_VERSION,
    phase: "vision_context",
    goal_ref: goalRef,
  }));
  assert.deepEqual(
    (context.args as string[]).slice(-2),
    ["--goal-instance-id", instanceId],
  );
});

test("vision decisions require v1 and cannot combine patch with unchanged", () => {
  assert.throws(() => prepare({vision_path: "vision.json"}), /requires v1/);
  assert.throws(() => prepare({schema_version: HOST_TODO_VISION_TRANSACTION_SCHEMA_VERSION,
    vision_path: "vision.json", vision_unchanged_reason: "unchanged"}), /not both/);
  assert.throws(() => evaluateHostTodoCompletion(request("prepare", {
    schema_version: HOST_TODO_VISION_TRANSACTION_SCHEMA_VERSION, phase: "vision_refresh",
  })), /authored decision/);
  const base = prepare();
  assert.deepEqual(prepare({schema_version: HOST_TODO_VISION_TRANSACTION_SCHEMA_VERSION}), base);
});

test("unchanged vision authoring is validated before completion or recovery effects", () => {
  for (const phase of ["prepare", "vision_refresh"] as const) {
    const authored = {
      schema_version: HOST_TODO_VISION_TRANSACTION_SCHEMA_VERSION,
      phase,
      vision_unchanged_reason: "x".repeat(241),
    };
    assert.throws(() => evaluateHostTodoCompletion(request("prepare", authored)),
      /vision_unchanged_reason exceeds 240 chars/);
    const reduced = evaluateHostTodoCompletion(request("prepare", {
      ...authored, vision_unchanged_reason: `  ${"x".repeat(240)}  `,
    }));
    const args = phase === "vision_refresh" ? reduced.args :
      (reduced.provider_effect as {steps: {step_kind: string; args: string[]}[]})
        .steps.find(step => step.step_kind === "durable_writeback")!.args;
    const command = args as string[];
    assert.equal(command[command.indexOf("--vision-unchanged-reason") + 1], "x".repeat(240));
  }
});

test("vision-aware Todo closeout never certifies Goal termination", () => {
  const reduced = finalize(providerOutcomes(identityFrom(prepare())), {schema_version: HOST_TODO_VISION_TRANSACTION_SCHEMA_VERSION,
    vision_path: "vision.json"});
  assert.equal((reduced.result as Record<string, unknown>).completion_scope, "todo");
  const terminal = (reduced.result as Record<string, unknown>).goal_terminal as Record<string, unknown>;
  assert.equal(terminal.assessed, false);
  assert.equal(terminal.next_tool, "should_run");
});

function request(
  phase: "prepare" | "finalize" | "classify_guard",
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    schema_version: HOST_TODO_COMPLETION_TRANSACTION_SCHEMA_VERSION,
    phase,
    goal_id: "goal",
    agent_id: "agent",
    todo_id: todoId,
    runtime_profile: "claude_code",
    legacy_host_surface: "claude_code",
    scheduler_owner: "agent_cli_loop",
    execution_mode: "interactive",
    completion_args: [
      "todo",
      "complete",
      todoId,
      "--agent-id",
      "agent",
      "--no-follow-up",
    ],
    no_follow_up: true,
    ...overrides,
  };
}

function prepare(overrides: Record<string, unknown> = {}) {
  return evaluateHostTodoCompletion(request("prepare", overrides));
}

function identityFrom(reduced: Record<string, unknown>) {
  return reduced.identity as Record<string, unknown>;
}

function providerOutcomes(
  identity: Record<string, unknown>,
  overrides: Partial<Record<string, Record<string, unknown>>> = {},
) {
  const completion = {
    ok: true,
    completed: true,
    status: "done",
    completion_continuation: "active_goal",
    settlement_identity: identity,
    settlement_result: { failure: null },
  };
  const writeback = {
    ok: true,
    appended: true,
    settlement_identity: identity,
    settlement_result: { failure: null },
  };
  const spend = {
    ok: true,
    appended: true,
    settlement_identity: identity,
    settlement_result: { failure: null },
  };
  const terminal = {
    ...completion,
    completion_continuation: "no_followup",
  };
  const payloads: Record<string, Record<string, unknown>> = {
    guard: {
      ok: true,
      heartbeat_receipt: { settlement_identity: identity },
    },
    lifecycle_completion: completion,
    durable_writeback: writeback,
    quota_spend: spend,
    terminal_closeout: terminal,
    ...overrides,
  };
  return [
    "guard",
    "lifecycle_completion",
    "durable_writeback",
    "quota_spend",
    "terminal_closeout",
  ].map((step_kind) => ({
    step_kind,
    output: JSON.stringify(payloads[step_kind]),
  }));
}

function finalize(
  outcomes: readonly Record<string, unknown>[],
  overrides: Record<string, unknown> = {},
) {
  return evaluateHostTodoCompletion(
    request("finalize", { provider_outcomes: outcomes, ...overrides }),
  );
}

test("prepare owns the retry-stable identity and one ordered host CLI plan", () => {
  const original = request("prepare");
  const snapshot = structuredClone(original);
  const reduced = evaluateHostTodoCompletion(original);
  const identity = identityFrom(reduced);
  const turnInstanceId = `mcp-${createHash("sha256")
    .update(["goal", "agent", todoId].join("\0"), "utf8")
    .digest("hex")
    .slice(0, 32)}`;

  assert.deepEqual(original, snapshot);
  assert.equal(reduced.schema_version, HOST_TODO_COMPLETION_REDUCTION_SCHEMA_VERSION);
  assert.equal(reduced.phase, "prepare");
  assert.equal(reduced.decision, "execute");
  assert.deepEqual(identity, {
    schema_version: "quota_settlement_identity_v0",
    effect_id: `goal:agent:${todoId}:${turnInstanceId}`,
    goal_id: "goal",
    agent_id: "agent",
    todo_id: todoId,
    turn_instance_id: turnInstanceId,
  });

  const provider = reduced.provider_effect as Record<string, unknown>;
  assert.equal(provider.provider_id, "loopx_cli");
  assert.equal(provider.kind, "ordered_cli_sequence");
  const steps = provider.steps as Array<Record<string, unknown>>;
  assert.deepEqual(
    steps.map((step) => step.step_kind),
    [
      "guard",
      "lifecycle_completion",
      "durable_writeback",
      "quota_spend",
      "terminal_closeout",
    ],
  );
  assert.deepEqual(steps[0].args, [
    "quota",
    "should-run",
    "--goal-id",
    "goal",
    "--agent-id",
    "agent",
    "--todo-id",
    todoId,
    "--turn-instance-id",
    turnInstanceId,
    "--runtime-profile",
    "claude_code",
  ]);
  assert.deepEqual(steps[0].legacy_args, [
    "quota",
    "should-run",
    "--goal-id",
    "goal",
    "--agent-id",
    "agent",
    "--todo-id",
    todoId,
    "--turn-instance-id",
    turnInstanceId,
    "--host-surface",
    "claude_code",
    "--scheduler-owner",
    "agent_cli_loop",
    "--execution-mode",
    "interactive",
  ]);
  assert.deepEqual(steps[1].args, [
    "todo",
    "complete",
    todoId,
    "--agent-id",
    "agent",
    "--turn-instance-id",
    turnInstanceId,
  ]);
  assert.deepEqual(
    (steps[2].args as string[]).slice(-6),
    [
      "--completion-todo-id",
      todoId,
      "--completion-turn-key",
      identity.effect_id,
      "--no-global-sync",
      "--suppress-external-sinks",
    ],
  );
  assert.deepEqual((steps[3].args as string[]).slice(0, 4), [
    "quota",
    "spend-slot",
    "--goal-id",
    "goal",
  ]);
  assert.ok(steps.slice(0, -1).every((step) => step.continue_when));
  assert.equal(steps.at(-1)?.continue_when, null);
});

test("finalize validates every receipt and builds the canonical host result", () => {
  const prepared = prepare();
  const identity = identityFrom(prepared);
  const outcomes = providerOutcomes(identity);
  const reduced = finalize(outcomes);
  const payloads = outcomes.map((outcome) => JSON.parse(String(outcome.output)));

  assert.equal(reduced.phase, "finalize");
  assert.equal(reduced.decision, "complete");
  assert.deepEqual(reduced.result, {
    schema_version: HOST_ADAPTER_SETTLEMENT_SCHEMA_VERSION,
    ok: true,
    completed: true,
    status: "done",
    goal_id: "goal",
    todo_id: todoId,
    settlement_identity: identity,
    completion: payloads[4],
    settlement: {
      ok: true,
      guard_state: "selected",
      durable_writeback: payloads[2],
      lifecycle_completion: payloads[1],
      quota_spend: payloads[3],
      terminal_closeout: payloads[4],
    },
  });
});

test("guard classification keeps terminal, malformed, and mismatched states distinct", () => {
  const prepared = prepare();
  const identity = identityFrom(prepared);
  const cases = [
    {
      raw: "not-json",
      state: "invalid",
      reason: "quota guard returned malformed JSON",
    },
    {
      raw: JSON.stringify({
        ok: true,
        should_run: false,
        effective_action: "terminal_no_followup",
      }),
      state: "terminal_no_selection",
      reason: "quota guard has no selected Todo",
    },
    {
      raw: JSON.stringify({
        ok: true,
        heartbeat_receipt: {
          settlement_identity: { ...identity, effect_id: "wrong" },
        },
        selected_todo: { todo_id: todoId },
      }),
      state: "invalid",
      reason: "quota guard returned a mismatched settlement effect id",
    },
  ] as const;

  for (const fixture of cases) {
    const classified = evaluateHostTodoCompletion(
      request("classify_guard", { guard_output: fixture.raw }),
    );
    const selection = classified.selection as Record<string, unknown>;
    assert.equal(selection.state, fixture.state);
    assert.equal(
      selection.reason,
      fixture.state === "terminal_no_selection" ? null : fixture.reason,
    );

    const reduced = finalize([{ step_kind: "guard", output: fixture.raw }]);
    const result = reduced.result as Record<string, unknown>;
    const settlement = result.settlement as Record<string, unknown>;
    assert.equal(reduced.decision, "blocked");
    assert.equal(settlement.guard_state, fixture.state);
    assert.equal(settlement.reason, fixture.reason);
  }
});

test("selected_todo fallback is accepted only without an identity object", () => {
  const selected = JSON.stringify({
    ok: true,
    heartbeat_receipt: { settlement_identity: null },
    selected_todo: { todo_id: todoId.toUpperCase() },
  });
  const classified = evaluateHostTodoCompletion(
    request("classify_guard", { guard_output: selected }),
  );

  assert.deepEqual(classified.selection, {
    state: "selected",
    todo_id: todoId,
    reason: null,
    settlement_identity: null,
  });
});

test("a typed Todo completion rejection is returned without running later stages", () => {
  const prepared = prepare();
  const identity = identityFrom(prepared);
  const rejection = {
    ok: false,
    completed: false,
    status: "open",
    reason: "validation failed",
  };
  const outcomes = providerOutcomes(identity, {
    lifecycle_completion: rejection,
  }).slice(0, 2);
  const reduced = finalize(outcomes);

  assert.equal(reduced.decision, "provider_result");
  assert.deepEqual(reduced.result, rejection);
});

test("receipt failures are attributed to the first unproved stage", () => {
  const prepared = prepare();
  const identity = identityFrom(prepared);
  const fixtures = [
    {
      stage: "durable_writeback",
      overrides: {
        lifecycle_completion: {
          ok: true,
          completed: true,
          status: "done",
          settlement_identity: { ...identity, effect_id: "wrong" },
          settlement_result: { failure: null },
        },
      },
      count: 2,
      reason: "todo completion did not prove the expected settlement identity",
    },
    {
      stage: "durable_writeback",
      overrides: { durable_writeback: { ok: false, reason: "writeback denied" } },
      count: 3,
      reason: "writeback denied",
    },
    {
      stage: "quota_spend",
      overrides: { quota_spend: { ok: true, appended: false } },
      count: 4,
      reason: "quota spend did not append a receipt",
    },
    {
      stage: "terminal_closeout",
      overrides: { terminal_closeout: { ok: false, reason: "closeout denied" } },
      count: 5,
      reason: "closeout denied",
    },
  ] as const;

  for (const fixture of fixtures) {
    const outcomes = providerOutcomes(identity, fixture.overrides).slice(
      0,
      fixture.count,
    );
    const reduced = finalize(outcomes);
    const result = reduced.result as Record<string, unknown>;
    const settlement = result.settlement as Record<string, unknown>;
    assert.equal(reduced.decision, "blocked");
    assert.equal(settlement.failed_stage, fixture.stage);
    assert.equal(settlement.reason, fixture.reason);
  }
});

test("quota spend accepts every durable commit marker", () => {
  const prepared = prepare({ no_follow_up: false });
  const identity = identityFrom(prepared);

  for (const marker of ["appended", "idempotent_replay", "receipt_repaired"]) {
    const outcomes = providerOutcomes(identity, {
      quota_spend: {
        ok: true,
        [marker]: true,
        settlement_identity: identity,
        settlement_result: { failure: null },
      },
    }).slice(0, 4);
    const reduced = finalize(outcomes, { no_follow_up: false });
    assert.equal(reduced.decision, "complete", marker);
  }
});

test("the typed boundary rejects unknown schemas and reordered outcomes", () => {
  assert.throws(
    () => prepare({ schema_version: "future" }),
    /schema_version is unsupported/,
  );
  assert.throws(
    () => prepare({ todo_id: "not-a-todo" }),
    /todo_id must be a typed Todo id/,
  );
  assert.throws(
    () => prepare({ todo_id: todoId.toUpperCase() }),
    /todo_id must be a typed Todo id/,
  );

  const prepared = prepare();
  const identity = identityFrom(prepared);
  const outcomes = providerOutcomes(identity);
  outcomes[1].step_kind = "quota_spend";
  assert.throws(
    () => finalize(outcomes),
    /provider_outcomes\[1\].step_kind does not match the prepared plan/,
  );
});
