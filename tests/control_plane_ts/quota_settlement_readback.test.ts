import assert from "node:assert/strict";
import {
  appendFile,
  mkdir,
  mkdtemp,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { settlementIdentity } from "../../loopx/control_plane/effect_program.ts";
import {
  projectSemanticReplanGuard,
  QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA,
  readQuotaSettlement,
} from "../../loopx/control_plane/quota/settlement_readback.ts";

const goalId = "settlement-goal";
const agentId = "codex-settlement";
const todoId = "todo_settlement";
const turnId = "turn-settlement-1";
const identity = settlementIdentity({
  goal_id: goalId,
  agent_id: agentId,
  todo_id: todoId,
  turn_instance_id: turnId,
});

test("semantic replan guard distinguishes legacy, none, and exact selection", () => {
  assert.deepEqual(projectSemanticReplanGuard({}), {
    schema_version: "semantic_replan_guard_v0",
    scope: "legacy_unscoped",
    selected_obligation_id: null,
  });
  assert.deepEqual(projectSemanticReplanGuard({
    semantic_replan_obligation_id: "",
  }), {
    schema_version: "semantic_replan_guard_v0",
    scope: "turn_guard",
    selected_obligation_id: null,
  });
  assert.deepEqual(projectSemanticReplanGuard({
    semantic_replan_obligation_id: "replan-0000000000000001",
  }), {
    schema_version: "semantic_replan_guard_v0",
    scope: "turn_guard",
    selected_obligation_id: "replan-0000000000000001",
  });
  assert.throws(
    () => projectSemanticReplanGuard({ semantic_replan_obligation_id: "bad" }),
    /semantic replan guard is malformed/,
  );
});

async function fixture(options: {
  guard?: boolean;
  /**
   * Commit the same-turn guard receipt the way the documented wake order does:
   * the guard runs before a work item is chosen, so the receipt exists for this
   * turn but carries no settlement binding.
   */
  guardUnbound?: boolean;
  writeback?: boolean;
  spend?: boolean;
  completion?: boolean;
  noFollowup?: boolean;
  workspace?: boolean;
  monitor?: boolean;
  writebackOutcome?: string;
  progressObservation?: Record<string, unknown>;
} = {}) {
  const runtimeRoot = await mkdtemp(join(tmpdir(), "loopx-settlement-readback-"));
  const goalRoot = join(runtimeRoot, "goals", goalId);
  const runsRoot = join(goalRoot, "runs");
  await mkdir(runsRoot, { recursive: true });
  const events: Record<string, unknown>[] = options.guard === false
    ? []
    : [{
      schema_version: "loopx_rollout_event_v0",
      event_id: "event-guard",
      event_kind: "quota_should_run",
      goal_id: goalId,
      agent_id: agentId,
      run_id: turnId,
      details: {
        ...(options.guardUnbound
          ? {}
          : {
            todo_id: todoId,
            settlement_effect_id: identity.effect_id,
          }),
        ...(options.workspace
          ? {
            delivery_workspace_causality_schema_version:
              "delivery_workspace_causality_v0",
            delivery_workspace_causality_todo_id: todoId,
            delivery_workspace_requirement: "required",
            delivery_workspace_causality_source: "selected_todo_contract",
            delivery_workspace_causality_reason:
              "declared_repository_or_write_contract",
          }
          : {}),
      },
    }];
  const runs: Record<string, unknown>[] = [];
  if (options.writeback) {
    events.push({
      schema_version: "loopx_rollout_event_v0",
      event_id: "event-writeback",
      event_kind: "refresh_state",
      goal_id: goalId,
      agent_id: agentId,
      run_id: turnId,
      details: { settlement_effect_id: identity.effect_id },
    });
    runs.push({
      classification: "state_refreshed",
      delivery_outcome: options.writebackOutcome ?? "outcome_progress",
      goal_id: goalId,
      agent_id: agentId,
      todo_id: todoId,
      turn_instance_id: turnId,
      settlement_identity: identity,
      ...(options.progressObservation
        ? { progress_observation: options.progressObservation }
        : {}),
    });
  }
  if (options.spend) {
    events.push({
      schema_version: "loopx_rollout_event_v0",
      event_id: "event-spend",
      event_kind: "quota_spend",
      goal_id: goalId,
      agent_id: agentId,
      run_id: turnId,
      details: { settlement_effect_id: identity.effect_id },
    });
    runs.push({
      classification: "quota_slot_spent",
      goal_id: goalId,
      agent_id: agentId,
      todo_id: todoId,
      turn_instance_id: turnId,
      settlement_identity: identity,
    });
  }
  if (options.completion) {
    events.push({
      schema_version: "loopx_rollout_event_v0",
      event_id: "event-completion",
      event_kind: "todo_complete",
      goal_id: goalId,
      agent_id: agentId,
      run_id: turnId,
      details: {
        settlement_effect_id: identity.effect_id,
        no_followup: options.noFollowup === true,
      },
    });
  }
  if (options.monitor) {
    runs.push({
      classification: "quota_monitor_poll",
      goal_id: goalId,
      agent_id: agentId,
      todo_id: todoId,
      turn_instance_id: turnId,
      material_change: true,
      quota_monitor_poll_commit: {
        schema_version: "quota_monitor_poll_commit_receipt_v0",
        effect_id: `quota-monitor-poll:${goalId}:${agentId}:${turnId}:todo:${todoId}`,
        request_digest: "fixture",
      },
    });
  }
  await writeFile(
    join(goalRoot, "rollout-event-log.jsonl"),
    `${events.map((event) => JSON.stringify(event)).join("\n")}\n`,
  );
  await writeFile(
    join(runsRoot, "index.jsonl"),
    `${runs.map((run) => JSON.stringify(run)).join("\n")}\n`,
  );
  return runtimeRoot;
}

function request(runtimeRoot: string, overrides: Record<string, unknown> = {}) {
  return {
    schema_version: QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA,
    runtime_root: runtimeRoot,
    goal_id: goalId,
    agent_id: agentId,
    todo_id: todoId,
    turn_instance_id: turnId,
    replan_obligation_id: null,
    infer_turn_instance_id: false,
    allow_unbound_binding: false,
    ...overrides,
  };
}

test("monitor closeout requires the exact committed effect, not a matching observation row", async t => {
  const effect = `quota-monitor-poll:${goalId}:${agentId}:${turnId}`;
  const cases: [string, Record<string, unknown>, string][] = [
    ["committed", {}, "settled"],
    ["legacy turn-only commit", {quota_monitor_poll_commit: {effect_id: effect}}, "settled"],
    ["preview without commit", {quota_monitor_poll_commit: null}, "poll_due"],
    ["malformed commit", {quota_monitor_poll_commit: []}, "poll_due"],
    ["missing effect", {quota_monitor_poll_commit: {}}, "poll_due"],
    ["wrong commit", {quota_monitor_poll_commit: {effect_id: `${effect}:todo:other`}}, "poll_due"],
    ["wrong agent", {agent_id: "another-agent"}, "poll_due"],
    ["wrong goal", {goal_id: "another-goal"}, "poll_due"],
    ["wrong Todo", {todo_id: "todo_other"}, "poll_due"],
    ["wrong Turn", {turn_instance_id: "other-turn"}, "poll_due"],
    ["ordinary writeback", {classification: "state_refreshed"}, "poll_due"],
  ];
  for (const [name, patch, expected] of cases) {
    await t.test(name, async () => {
      const root = await fixture({monitor: true});
      try {
        const path = join(root, "goals", goalId, "runs", "index.jsonl");
        const row = JSON.parse((await readFile(path, "utf8")).trim());
        await writeFile(path, `${JSON.stringify({...row, ...patch})}\n`);
        const result = await readQuotaSettlement(request(root));
        assert.equal(result.monitor_phase, expected);
        assert.equal(result.replay_phase, "open");
        assert.equal((result.spend as any).payload.ok, false);
      } finally { await rm(root, {recursive: true, force: true}); }
    });
  }
});

test("refresh recovery admission never survives a failed Turn identity", async () => {
  const root = await fixture({ guard: false, writeback: true });
  try {
    const result = await readQuotaSettlement(request(root, {
      refresh_retry: {
        vision: null, unchanged_reason: "Existing vision applies.", merge_patch: false,
        workspace_requested: false, mutation: {}, delivery_outcome: "outcome_progress",
        delivery_batch_scale: null, delivery_boundary: null, progress_observation: null,
      },
    }));
    assert.equal(result.refresh_recovery, null);
    assert.equal(result.writeback_run, null);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

async function appendSpendRun(runtimeRoot: string, extra: Record<string, unknown>) {
  await appendFile(
    join(runtimeRoot, "goals", goalId, "runs", "index.jsonl"),
    `${JSON.stringify({
      classification: "quota_slot_spent",
      goal_id: goalId,
      agent_id: agentId,
      todo_id: todoId,
      turn_instance_id: turnId,
      settlement_identity: identity,
      effect_ref: `${identity.effect_id}#quota_spend`,
      ...extra,
    })}\n`,
  );
}

test("reads the complete receipt chain and workspace causality once", async () => {
  const runtimeRoot = await fixture({
    writeback: true,
    spend: true,
    completion: true,
    noFollowup: true,
    workspace: true,
    monitor: true,
  });

  const result = await readQuotaSettlement(request(runtimeRoot));

  assert.equal(result.found, true);
  assert.equal((result.settlement as any).payload.ok, true);
  assert.deepEqual(
    (result.settlement as any).result.receipts.map((receipt: any) => receipt.step_kind),
    ["validation", "durable_writeback", "quota_spend"],
  );
  assert.equal((result.terminal_closeout as any).payload.ok, true);
  assert.equal(result.monitor_phase, "settled");
  assert.equal(result.replay_phase, "settled");
  assert.deepEqual(result.semantic_replan_guard, {
    schema_version: "semantic_replan_guard_v0",
    scope: "legacy_unscoped",
    selected_obligation_id: null,
  });
  assert.deepEqual(result.workspace_causality, {
    schema_version: "delivery_workspace_causality_v0",
    todo_id: todoId,
    requirement: "required",
    source: "selected_todo_contract",
    reason: "declared_repository_or_write_contract",
  });
});

test("keeps ordinary partial settlement fail-closed while the monitor poll is closed", async () => {
  const runtimeRoot = await fixture({ writeback: true, monitor: true });

  const result = await readQuotaSettlement(request(runtimeRoot));

  assert.equal((result.writeback as any).payload.ok, true);
  assert.equal((result.spend as any).payload.ok, false);
  assert.equal((result.settlement as any).result.failure.kind, "receipt_missing");
  assert.equal(result.monitor_phase, "settled");
  assert.equal(result.replay_phase, "open");
  assert.equal((result.writeback_run as any).delivery_outcome, "outcome_progress");
});

test("names the unbound same-turn receipt and the repair instead of a mismatch", async () => {
  // The documented wake order runs the guard before any work item is chosen, so
  // the turn's receipt exists with no settlement binding. This read model never
  // binds one (the guard's same-turn reconciliation owns that, so there is one
  // binder), which means the caller has to be told the state and the exact
  // repair rather than the binding mismatch a "receipt todo=missing" message
  // reports.
  const runtimeRoot = await fixture({ guardUnbound: true });

  const result = await readQuotaSettlement(request(runtimeRoot));

  const failure = (result.settlement as any).result.failure;
  assert.equal(failure.kind, "identity_mismatch");
  assert.match(failure.reason, /carries no settlement binding yet/);
  assert.match(
    failure.reason,
    new RegExp(
      `quota should-run --turn-instance-id ${turnId} --todo-id ${todoId}`,
    ),
  );
  assert.deepEqual(failure.details, {
    binding_kind: "unbound",
    requested_binding_kind: "todo",
    turn_instance_id: turnId,
  });
});

test("still reports a receipt bound to another work item as a mismatch", async () => {
  // The unbound state must not swallow the case where the receipt was bound and
  // the caller asked for something else: that is a real conflict, and its repair
  // is not "bind it".
  const runtimeRoot = await fixture({});
  const eventsPath = join(
    runtimeRoot,
    "goals",
    goalId,
    "rollout-event-log.jsonl",
  );
  const events = (await readFile(eventsPath, "utf8"))
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line));
  events[0].details.todo_id = "todo_other_work_item";
  events[0].details.settlement_effect_id = settlementIdentity({
    goal_id: goalId,
    agent_id: agentId,
    todo_id: "todo_other_work_item",
    turn_instance_id: turnId,
  }).effect_id;
  await writeFile(
    eventsPath,
    `${events.map((event) => JSON.stringify(event)).join("\n")}\n`,
  );

  const result = await readQuotaSettlement(request(runtimeRoot));

  const failure = (result.settlement as any).result.failure;
  assert.equal(failure.kind, "identity_mismatch");
  assert.match(failure.reason, /receipt todo=todo_other_work_item/);
  assert.equal(failure.details, undefined);
});

test("rejects non-ENOENT settlement readback I/O failures", async (t) => {
  const runtimeRoot = await fixture();
  const indexPath = join(runtimeRoot, "goals", goalId, "runs", "index.jsonl");
  await rm(indexPath);
  await mkdir(indexPath);

  await assert.rejects(
    readQuotaSettlement(request(runtimeRoot)),
    (error: unknown) =>
      typeof error === "object" &&
      error !== null &&
      "code" in error &&
      error.code === "EISDIR",
  );
});

test("recovers legacy quota commit rows by exact effect ref", async () => {
  const runtimeRoot = await fixture();
  await appendFile(
    join(runtimeRoot, "goals", goalId, "runs", "index.jsonl"),
    `${JSON.stringify({
      classification: "quota_slot_spent",
      goal_id: goalId,
      agent_id: agentId,
      effect_ref: `${identity.effect_id}#quota_spend`,
    })}\n`,
  );

  const result = await readQuotaSettlement(request(runtimeRoot));

  assert.equal((result.spend_run as any).effect_ref, `${identity.effect_id}#quota_spend`);
  assert.equal((result.spend as any).result.failure.kind, "receipt_missing");
});

test("rejects a writeback run persisted under another goal", async (t) => {
  const runtimeRoot = await fixture();
  await appendFile(
    join(runtimeRoot, "goals", goalId, "runs", "index.jsonl"),
    `${JSON.stringify({
      classification: "state_refreshed",
      delivery_outcome: "outcome_progress",
      goal_id: "other-goal",
      agent_id: agentId,
      todo_id: todoId,
      turn_instance_id: turnId,
      settlement_identity: identity,
    })}\n`,
  );

  const result = await readQuotaSettlement(request(runtimeRoot));

  assert.equal(result.writeback_run, null);
  assert.equal((result.writeback as any).result.failure.kind, "writeback_missing");
});

test("does not pair a writeback run from another settlement effect", async () => {
  const runtimeRoot = await fixture();
  await appendFile(
    join(runtimeRoot, "goals", goalId, "runs", "index.jsonl"),
    `${JSON.stringify({
      classification: "state_refreshed",
      delivery_outcome: "outcome_progress",
      goal_id: goalId,
      agent_id: agentId,
      todo_id: todoId,
      turn_instance_id: turnId,
      settlement_identity: { ...identity, effect_id: "other-effect" },
    })}\n`,
  );

  const result = await readQuotaSettlement(request(runtimeRoot));

  assert.equal(result.writeback_run, null);
  assert.equal((result.writeback as any).result.failure.kind, "writeback_missing");
});

test("does not pair a spend run from another settlement effect", async () => {
  const runtimeRoot = await fixture();
  await appendFile(
    join(runtimeRoot, "goals", goalId, "runs", "index.jsonl"),
    `${JSON.stringify({
      classification: "quota_slot_spent",
      goal_id: goalId,
      agent_id: agentId,
      todo_id: todoId,
      turn_instance_id: turnId,
      effect_ref: "other-effect#quota_spend",
    })}\n`,
  );

  const result = await readQuotaSettlement(request(runtimeRoot));

  assert.equal(result.spend_run, null);
  assert.equal((result.spend as any).result.failure.kind, "receipt_missing");
});

test("does not pair a spend run when effect identities conflict either way", async () => {
  for (const row of [
    {
      quota_spend_commit: { effect_id: identity.effect_id },
      effect_ref: "different-effect#quota_spend",
    },
    {
      quota_spend_commit: { effect_id: "different-effect" },
      effect_ref: `${identity.effect_id}#quota_spend`,
    },
  ]) {
    const runtimeRoot = await fixture();
    await appendFile(
      join(runtimeRoot, "goals", goalId, "runs", "index.jsonl"),
      `${JSON.stringify({
        classification: "quota_slot_spent",
        goal_id: goalId,
        agent_id: agentId,
        todo_id: todoId,
        turn_instance_id: turnId,
        ...row,
      })}\n`,
    );

    const result = await readQuotaSettlement(request(runtimeRoot));

    assert.equal(result.spend_run, null);
    assert.equal((result.spend as any).result.failure.kind, "receipt_missing");
  }
});

test("pairs a native spend row only when both persisted effect identities agree", async () => {
  const runtimeRoot = await fixture({ spend: true });
  await appendFile(
    join(runtimeRoot, "goals", goalId, "runs", "index.jsonl"),
    `${JSON.stringify({
      classification: "quota_slot_spent",
      goal_id: goalId,
      agent_id: agentId,
      todo_id: todoId,
      turn_instance_id: turnId,
      settlement_identity: identity,
      quota_spend_commit: { effect_id: `${identity.effect_id}#quota_spend` },
      effect_ref: `${identity.effect_id}#quota_spend`,
    })}\n`,
  );

  const result = await readQuotaSettlement(request(runtimeRoot));

  assert.equal((result.spend as any).payload.ok, true);
  assert.equal(
    (result.spend_run as any).quota_spend_commit.effect_id,
    `${identity.effect_id}#quota_spend`,
  );
});

test("does not pair a spend row with malformed native effect metadata", async () => {
  const quotaSpendCommit = null;
  const runtimeRoot = await fixture();
  await appendSpendRun(runtimeRoot, { quota_spend_commit: quotaSpendCommit });

  const result = await readQuotaSettlement(request(runtimeRoot));

  assert.equal((result.spend as any).payload.ok, false);
  assert.equal((result.spend as any).result.failure.kind, "receipt_missing");
});

test("does not pair a spend row with non-object native effect metadata", async () => {
  const quotaSpendCommit: unknown[] = [];
  const runtimeRoot = await fixture();
  await appendSpendRun(runtimeRoot, { quota_spend_commit: quotaSpendCommit });

  const result = await readQuotaSettlement(request(runtimeRoot));

  assert.equal((result.spend as any).payload.ok, false);
  assert.equal((result.spend as any).result.failure.kind, "receipt_missing");
});

test("does not pair a spend row with malformed persisted settlement identity", async () => {
  for (const settlementIdentity of [null, [], "not-an-identity", {}]) {
    const runtimeRoot = await fixture();
    await appendFile(
      join(runtimeRoot, "goals", goalId, "runs", "index.jsonl"),
      `${JSON.stringify({
        classification: "quota_slot_spent",
        goal_id: goalId,
        agent_id: agentId,
        todo_id: todoId,
        turn_instance_id: turnId,
        settlement_identity: settlementIdentity,
        effect_ref: `${identity.effect_id}#quota_spend`,
      })}\n`,
    );

    const result = await readQuotaSettlement(request(runtimeRoot));

    assert.equal(result.spend_run, null);
    assert.equal((result.spend as any).result.failure.kind, "receipt_missing");
  }
});

test("accepts only an attributable typed blocker as an outcome-gap writeback", async () => {
  const qualifiedRuntime = await fixture({
    writeback: true,
    writebackOutcome: "outcome_gap",
    progressObservation: {
      schema_version: "typed_progress_observation_v0",
      result_class: "blocked",
      work_item_id: todoId,
      blocker_id: "blocker-runtime-boundary",
      evidence_ids: ["evidence-runtime-boundary"],
    },
  });
  const qualified = await readQuotaSettlement(request(qualifiedRuntime));
  assert.equal((qualified.writeback as any).payload.ok, true);
  assert.equal((qualified.writeback_run as any).delivery_outcome, "outcome_gap");

  const bareRuntime = await fixture({
    writeback: true,
    writebackOutcome: "outcome_gap",
  });
  const bare = await readQuotaSettlement(request(bareRuntime));
  assert.equal((bare.writeback as any).payload.ok, false);
  assert.equal((bare.writeback as any).result.failure.kind, "writeback_missing");

  const mismatchedRuntime = await fixture({
    writeback: true,
    writebackOutcome: "outcome_gap",
    progressObservation: {
      schema_version: "typed_progress_observation_v0",
      result_class: "blocked",
      work_item_id: "todo_other",
      blocker_id: "blocker-runtime-boundary",
      evidence_ids: ["evidence-runtime-boundary"],
    },
  });
  const mismatched = await readQuotaSettlement(request(mismatchedRuntime));
  assert.equal((mismatched.writeback as any).payload.ok, false);

  for (const evidenceIds of [
    "evidence-runtime-boundary",
    { evidence: "runtime-boundary" },
    ["evidence-runtime-boundary", "invalid evidence id"],
  ]) {
    const malformedRuntime = await fixture({
      writeback: true,
      writebackOutcome: "outcome_gap",
      progressObservation: {
        schema_version: "typed_progress_observation_v0",
        result_class: "blocked",
        work_item_id: todoId,
        blocker_id: "blocker-runtime-boundary",
        evidence_ids: evidenceIds,
      },
    });
    const malformed = await readQuotaSettlement(request(malformedRuntime));
    assert.equal((malformed.writeback as any).payload.ok, false);
  }
});

test("rejects a guard bound to another Todo", async () => {
  const runtimeRoot = await fixture();

  const result = await readQuotaSettlement(
    request(runtimeRoot, { todo_id: "todo_other" }),
  );

  assert.equal((result.identity as any).result.failure.kind, "identity_mismatch");
  assert.match(
    (result.identity as any).result.failure.reason,
    /does not match the original quota guard/,
  );
});

test("rejects dual Todo and replan bindings before reading settlement facts", async () => {
  const runtimeRoot = await fixture({
    writeback: true,
    spend: true,
    completion: true,
    monitor: true,
  });

  const result = await readQuotaSettlement(request(runtimeRoot, {
    replan_obligation_id: "replan-0000000000000001",
  }));

  assert.equal((result.identity as any).result.failure.kind, "invalid_identity");
  assert.equal(result.monitor_phase, null);
  assert.equal(result.replay_phase, null);
  assert.equal(result.writeback_run, null);
  assert.equal(result.spend_run, null);
});

test("identity failure cannot promote unguarded later facts to a terminal phase", async () => {
  const runtimeRoot = await fixture();
  const unguardedTodoId = "todo_unguarded";
  const unguardedIdentity = settlementIdentity({
    goal_id: goalId,
    agent_id: agentId,
    todo_id: unguardedTodoId,
    turn_instance_id: turnId,
  });
  const goalRoot = join(runtimeRoot, "goals", goalId);
  await appendFile(
    join(goalRoot, "rollout-event-log.jsonl"),
    [
      {
        schema_version: "loopx_rollout_event_v0",
        event_id: "event-unguarded-writeback",
        event_kind: "refresh_state",
        goal_id: goalId,
        agent_id: agentId,
        run_id: turnId,
        details: { settlement_effect_id: unguardedIdentity.effect_id },
      },
      {
        schema_version: "loopx_rollout_event_v0",
        event_id: "event-unguarded-spend",
        event_kind: "quota_spend",
        goal_id: goalId,
        agent_id: agentId,
        run_id: turnId,
        details: { settlement_effect_id: unguardedIdentity.effect_id },
      },
    ].map((event) => `${JSON.stringify(event)}\n`).join(""),
  );
  await appendFile(
    join(goalRoot, "runs", "index.jsonl"),
    [
      {
        classification: "quota_monitor_poll",
        material_change: true,
        goal_id: goalId,
        agent_id: agentId,
        todo_id: unguardedTodoId,
        turn_instance_id: turnId,
      },
      {
        classification: "state_refreshed",
        delivery_outcome: "outcome_progress",
        goal_id: goalId,
        agent_id: agentId,
        todo_id: unguardedTodoId,
        turn_instance_id: turnId,
        settlement_identity: unguardedIdentity,
      },
      {
        classification: "quota_slot_spent",
        goal_id: goalId,
        agent_id: agentId,
        todo_id: unguardedTodoId,
        turn_instance_id: turnId,
        settlement_identity: unguardedIdentity,
      },
    ].map((run) => `${JSON.stringify(run)}\n`).join(""),
  );

  const result = await readQuotaSettlement(
    request(runtimeRoot, { todo_id: unguardedTodoId }),
  );

  assert.equal((result.identity as any).result.failure.kind, "identity_mismatch");
  assert.equal(result.monitor_phase, null);
  assert.equal(result.replay_phase, null);
  assert.equal(result.writeback_run, null);
  assert.equal(result.spend_run, null);
});

test("a missing guard keeps complete later facts non-terminal", async () => {
  const runtimeRoot = await fixture({
    guard: false,
    writeback: true,
    spend: true,
    monitor: true,
  });

  const result = await readQuotaSettlement(request(runtimeRoot));

  assert.equal((result.identity as any).result.failure.kind, "receipt_missing");
  assert.equal(result.monitor_phase, null);
  assert.equal(result.replay_phase, null);
  assert.equal(result.writeback_run, null);
  assert.equal(result.spend_run, null);
});

test("infers the latest typed turn and revalidates its guard", async () => {
  const runtimeRoot = await fixture({ writeback: true });

  const result = await readQuotaSettlement(request(runtimeRoot, {
    turn_instance_id: null,
    infer_turn_instance_id: true,
  }));

  assert.equal(result.found, true);
  assert.equal((result.identity as any).result.value.turn_instance_id, turnId);
});

test("returns not-found when compatibility inference has no typed run", async () => {
  const runtimeRoot = await fixture();

  const result = await readQuotaSettlement(request(runtimeRoot, {
    turn_instance_id: null,
    infer_turn_instance_id: true,
  }));

  assert.deepEqual(result, {
    schema_version: "loopx_quota_settlement_readback_result_v0",
    found: false,
  });
});

test("rejects malformed request authority at the runtime boundary", async () => {
  const runtimeRoot = await fixture();

  await assert.rejects(
    readQuotaSettlement(request(runtimeRoot, { agent_id: [agentId] })),
    /agent_id must be a string or null/,
  );
  await assert.rejects(
    readQuotaSettlement(request(runtimeRoot, { runtime_root: "relative" })),
    /runtime_root must be absolute/,
  );
  await assert.rejects(
    readQuotaSettlement(request(runtimeRoot, { schema_version: "future" })),
    /request schema mismatch/,
  );
  await assert.rejects(
    readQuotaSettlement(request(runtimeRoot, { infer_turn_instance_id: "yes" })),
    /infer_turn_instance_id must be a boolean/,
  );
  await assert.rejects(
    readQuotaSettlement(request(runtimeRoot, { allow_unbound_binding: "yes" })),
    /allow_unbound_binding must be a boolean/,
  );
});

test("fails closed on malformed settlement JSONL", async () => {
  const runtimeRoot = await fixture();
  await appendFile(
    join(runtimeRoot, "goals", goalId, "runs", "index.jsonl"),
    '{"classification":"quota_slot_spent"\n',
  );

  await assert.rejects(
    readQuotaSettlement(request(runtimeRoot)),
    /settlement readback line 2 is malformed/,
  );
});

test("fails closed on valid JSON with an invalid settlement record shape", async () => {
  const runtimeRoot = await fixture();
  await appendFile(
    join(runtimeRoot, "goals", goalId, "runs", "index.jsonl"),
    "[]\n",
  );

  await assert.rejects(
    readQuotaSettlement(request(runtimeRoot)),
    /settlement readback line 2 is malformed/,
  );
});

test("fails closed on a settlement event schema mismatch", async () => {
  const runtimeRoot = await fixture();
  await appendFile(
    join(runtimeRoot, "goals", goalId, "rollout-event-log.jsonl"),
    `${JSON.stringify({ schema_version: "future_rollout_event_v1" })}\n`,
  );

  await assert.rejects(
    readQuotaSettlement(request(runtimeRoot)),
    /settlement readback line 2 is malformed/,
  );
});
