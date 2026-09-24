import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { performance } from "node:perf_hooks";
import test from "node:test";

import {
  settlementIdentity,
  type JsonObject,
} from "../../loopx/control_plane/effect_program.ts";
import {
  ACCEPTED_CLOSEOUTS,
  PRIOR_HOST_TURN_CLOSEOUT_PREFLIGHT_REQUEST_SCHEMA,
  UNSETTLED_HOST_TURN_RECOVERY_REQUEST_SCHEMA,
  preflightPriorHostTurnCloseout,
  reduceUnsettledHostTurnRecovery,
} from "../../loopx/control_plane/quota/unsettled_host_turn_recovery.ts";

const GOAL = "goal-a";
const AGENT = "agent-a";

interface Runtime {
  root: string;
  close(): Promise<void>;
}

/** A disposable runtime whose only persisted state is the receipts under test. */
async function runtimeWith(events: JsonObject[]): Promise<Runtime> {
  const root = await mkdtemp(join(tmpdir(), "loopx-closeout-"));
  const goalRoot = join(root, "goals", GOAL);
  await mkdir(goalRoot, {recursive: true});
  await writeFile(
    join(goalRoot, "rollout-event-log.jsonl"),
    events.map((event) => JSON.stringify(event)).join("\n") + "\n",
    "utf8",
  );
  return {root, close: () => rm(root, {recursive: true, force: true})};
}

async function runtimeWithSettledTurns(count: number): Promise<Runtime> {
  const root = await mkdtemp(join(tmpdir(), "loopx-closeout-scale-"));
  const goalRoot = join(root, "goals", GOAL);
  const runsRoot = join(goalRoot, "runs");
  await mkdir(runsRoot, {recursive: true});
  const events: JsonObject[] = [];
  const runs: JsonObject[] = [];
  for (let index = 0; index < count; index += 1) {
    const suffix = String(index).padStart(4, "0");
    const turn = `turn-${suffix}`;
    const todoId = `todo_${suffix}`;
    const identity = settlementIdentity({
      goal_id: GOAL,
      agent_id: AGENT,
      todo_id: todoId,
      turn_instance_id: turn,
    });
    events.push(
      receipt(turn, {
        todo_id: todoId,
        settlement_effect_id: identity.effect_id,
        closeout_required: true,
      }, {event_id: `guard-${suffix}`}),
      {
        schema_version: "loopx_rollout_event_v0",
        event_id: `writeback-${suffix}`,
        event_kind: "refresh_state",
        goal_id: GOAL,
        agent_id: AGENT,
        run_id: turn,
        details: {settlement_effect_id: identity.effect_id},
      },
      {
        schema_version: "loopx_rollout_event_v0",
        event_id: `spend-${suffix}`,
        event_kind: "quota_spend",
        goal_id: GOAL,
        agent_id: AGENT,
        run_id: turn,
        details: {settlement_effect_id: identity.effect_id},
      },
    );
    runs.push(
      {
        classification: "state_refreshed",
        delivery_outcome: "outcome_progress",
        goal_id: GOAL,
        agent_id: AGENT,
        todo_id: todoId,
        turn_instance_id: turn,
        settlement_identity: identity,
      },
      {
        classification: "quota_slot_spent",
        goal_id: GOAL,
        agent_id: AGENT,
        todo_id: todoId,
        turn_instance_id: turn,
        settlement_identity: identity,
      },
    );
  }
  await writeFile(
    join(goalRoot, "rollout-event-log.jsonl"),
    `${events.map((event) => JSON.stringify(event)).join("\n")}\n`,
    "utf8",
  );
  await writeFile(
    join(runsRoot, "index.jsonl"),
    `${runs.map((run) => JSON.stringify(run)).join("\n")}\n`,
    "utf8",
  );
  return {root, close: () => rm(root, {recursive: true, force: true})};
}

function receipt(
  turn: string,
  details: JsonObject,
  overrides: JsonObject = {},
): JsonObject {
  return {
    schema_version: "loopx_rollout_event_v0",
    event_id: `event-${turn}`,
    event_kind: "quota_should_run",
    goal_id: GOAL,
    agent_id: AGENT,
    run_id: turn,
    details,
    ...overrides,
  };
}

function closeoutRequired(
  turn: string,
  todoId: string | null,
  replanId: string | null = null,
): JsonObject {
  return {
    ...(todoId ? {todo_id: todoId} : {}),
    ...(replanId ? {replan_obligation_id: replanId} : {}),
    ...(todoId ? {settlement_effect_id: `${GOAL}:${AGENT}:${todoId}:${turn}`} : {}),
    closeout_required: true,
  };
}

function preflight(runtimeRoot: string, exclude: string | null = "current-turn") {
  return preflightPriorHostTurnCloseout({
    schema_version: PRIOR_HOST_TURN_CLOSEOUT_PREFLIGHT_REQUEST_SCHEMA,
    runtime_root: runtimeRoot,
    goal_id: GOAL,
    agent_id: AGENT,
    exclude_turn_instance_id: exclude,
  });
}

function candidateFrom(result: JsonObject): JsonObject {
  assert.equal(result.status, "candidate");
  return result.candidate as JsonObject;
}

function reduce(
  candidate: JsonObject,
  missingReceipts: string[] = [
    "durable_writeback_receipt",
    "quota_spend_receipt",
  ],
  bindingFacts: JsonObject = {status: "not_read"},
) {
  return reduceUnsettledHostTurnRecovery({
    schema_version: UNSETTLED_HOST_TURN_RECOVERY_REQUEST_SCHEMA,
    goal_id: GOAL,
    agent_id: AGENT,
    candidate,
    missing_receipts: missingReceipts,
    binding_facts: bindingFacts,
  });
}

const READ_TODO = (todo: JsonObject | null, committed: JsonObject | null = null) => ({
  status: "read",
  todo,
  committed_monitor_poll: committed,
});

const ADVANCEMENT_OPEN = {
  task_class: "advancement_task",
  status: "open",
  has_resume_when: false,
  has_successor_todo_ids: false,
  target_key: null,
  cadence: null,
};

test("the preflight reads the persisted receipts and names the newest required closeout", async () => {
  const runtime = await runtimeWith([
    receipt("turn-old", closeoutRequired("turn-old", "todo_older")),
    receipt("turn-new", closeoutRequired("turn-new", "todo_newer")),
    // A Turn that repeats later is the more recent one, exactly as the
    // persisted log's append order defines "newest".
    receipt("turn-old", closeoutRequired("turn-old", "todo_older")),
  ]);
  try {
    const result = await preflight(runtime.root);
    assert.equal(result.schema_version, "loopx_prior_host_turn_closeout_preflight_result_v0");
    assert.equal(result.turns_validated, 2);
    const candidate = candidateFrom(result);
    assert.equal(candidate.prior_turn_instance_id, "turn-old");
    assert.equal(candidate.binding_kind, "todo");
    assert.equal(candidate.binding_id, "todo_older");
    assert.equal(candidate.event_id, "event-turn-old");
    // The receipt exists but its writeback and spend receipts do not.
    assert.deepEqual(result.missing_receipts, [
      "durable_writeback_receipt",
      "quota_spend_receipt",
    ]);
  } finally {
    await runtime.close();
  }
});

test("a receipt that does not opt in never becomes a recovery obligation", async () => {
  const runtime = await runtimeWith([
    receipt("turn-a", {todo_id: "todo_alpha", settlement_effect_id: "e"}),
    receipt("turn-b", {
      todo_id: "todo_beta",
      settlement_effect_id: `${GOAL}:${AGENT}:todo_beta:turn-b`,
      closeout_required: false,
    }),
  ]);
  try {
    const result = await preflight(runtime.root);
    assert.equal(result.status, "none");
    assert.equal(result.reason, "no_prior_turn_requires_closeout");
    assert.equal(result.turns_validated, 2);
  } finally {
    await runtime.close();
  }
});

test("the current Turn, other goals, other agents, and other event kinds are ignored", async () => {
  const runtime = await runtimeWith([
    receipt("current-turn", closeoutRequired("current-turn", "todo_current")),
    receipt("turn-other-goal", closeoutRequired("turn-other-goal", "todo_other"), {
      goal_id: "goal-b",
    }),
    receipt("turn-other-agent", closeoutRequired("turn-other-agent", "todo_other"), {
      agent_id: "agent-b",
    }),
    receipt("turn-other-kind", closeoutRequired("turn-other-kind", "todo_other"), {
      event_kind: "quota_settled",
    }),
    receipt("", closeoutRequired("", "todo_unbound_turn")),
  ]);
  try {
    const result = await preflight(runtime.root);
    assert.equal(result.status, "none");
    assert.equal(result.turns_validated, 0);
  } finally {
    await runtime.close();
  }
});

test("one receipt may bind only one settlement identity", async () => {
  const runtime = await runtimeWith([
    receipt("turn-conflict", {
      todo_id: "todo_alpha",
      replan_obligation_id: `replan-${"a".repeat(16)}`,
      closeout_required: true,
    }),
  ]);
  try {
    await assert.rejects(
      () => preflight(runtime.root),
      /conflicting Todo and autonomous replan bindings/,
    );
  } finally {
    await runtime.close();
  }
});

test("a receipt cannot claim an effect identity it does not bind", async () => {
  const runtime = await runtimeWith([
    receipt("turn-a", {
      settlement_effect_id: "effect-without-binding",
      closeout_required: true,
    }),
  ]);
  try {
    await assert.rejects(
      () => preflight(runtime.root),
      /refuse to infer or upgrade it/,
    );
  } finally {
    await runtime.close();
  }
});

test("a Todo binding the settlement authority cannot address fails the read", async () => {
  const runtime = await runtimeWith([
    receipt("turn-a", {
      todo_id: "TODO_ALPHA",
      closeout_required: true,
    }),
  ]);
  try {
    await assert.rejects(
      () => preflight(runtime.root),
      /not a legal Todo id/,
    );
  } finally {
    await runtime.close();
  }
});

test("receipts of one Turn that disagree on the binding are a conflict, not a preference", async () => {
  const runtime = await runtimeWith([
    receipt("turn-a", closeoutRequired("turn-a", "todo_alpha")),
    receipt("turn-a", closeoutRequired("turn-a", "todo_beta")),
  ]);
  try {
    await assert.rejects(
      () => preflight(runtime.root),
      /conflicting settlement identities/,
    );
  } finally {
    await runtime.close();
  }
});

test("a Turn with many receipts resolves the one that binds its settlement", async () => {
  const crowded: JsonObject[] = [];
  for (let index = 0; index < 40; index += 1) {
    crowded.push(
      receipt(`turn-crowded`, {
        stall_observation: "not_applicable",
        revision: index,
      }),
    );
  }
  crowded.push(receipt("turn-crowded", closeoutRequired("turn-crowded", "todo_crowded")));
  crowded.push(receipt("turn-newer", closeoutRequired("turn-newer", "todo_newer")));
  const runtime = await runtimeWith(crowded);
  try {
    const result = await preflight(runtime.root);
    assert.equal(result.turns_validated, 2);
    assert.equal(candidateFrom(result).binding_id, "todo_newer");
  } finally {
    await runtime.close();
  }
});

test("a prior Turn that already validates its settlement needs no bound facts", async () => {
  // The receipt alone is not a settlement: the readback resolves the identity
  // and reports the missing writeback and spend receipts instead...
  const unsettled = await runtimeWith([
    receipt("turn-a", closeoutRequired("turn-a", "todo_alpha")),
  ]);
  try {
    assert.equal((await preflight(unsettled.root)).status, "candidate");
  } finally {
    await unsettled.close();
  }
  // ...so a caller can never observe "validated" without the persisted
  // writeback and spend receipts, which the Python CLI fixtures cover
  // end-to-end through the real entrypoint.
});

test("a prior typed blocked writeback closes without a quota debit", async () => {
  const turn = "turn-blocked";
  const todoId = "todo_blocked";
  const identity = settlementIdentity({
    goal_id: GOAL, agent_id: AGENT, todo_id: todoId, turn_instance_id: turn,
  });
  const runtime = await runtimeWith([
    receipt(turn, {
      todo_id: todoId,
      settlement_effect_id: identity.effect_id,
      closeout_required: true,
    }),
    {
      schema_version: "loopx_rollout_event_v0",
      event_id: "event-blocked-writeback",
      event_kind: "refresh_state",
      goal_id: GOAL,
      agent_id: AGENT,
      run_id: turn,
      details: {settlement_effect_id: identity.effect_id},
    },
  ]);
  try {
    const runsRoot = join(runtime.root, "goals", GOAL, "runs");
    await mkdir(runsRoot, {recursive: true});
    await writeFile(join(runsRoot, "index.jsonl"), `${JSON.stringify({
      classification: "state_refreshed",
      delivery_outcome: "outcome_gap",
      goal_id: GOAL,
      agent_id: AGENT,
      todo_id: todoId,
      turn_instance_id: turn,
      settlement_identity: identity,
      blocked_retry: {
        schema_version: "quota_blocked_retry_v0",
        source: "turn_settlement",
        todo_id: todoId,
        resume_when: "resume_at:2026-09-24T10:05:00Z",
        observed_at: "2026-09-24T10:00:00Z",
        due_at: "2026-09-24T10:05:00Z",
      },
      progress_observation: {
        schema_version: "typed_progress_observation_v0",
        result_class: "blocked",
        work_item_id: todoId,
        blocker_id: "blocker-lease",
        evidence_ids: ["evidence-lease"],
      },
    })}\n`);
    const result = await preflight(runtime.root);
    assert.equal(result.status, "none");
    assert.equal(result.accepted_closeout, "typed_blocked_writeback_no_spend");
    assert.equal(result.prior_turn_instance_id, turn);
  } finally {
    await runtime.close();
  }
});

test("a malformed or foreign log line fails the read instead of erasing a closeout", async () => {
  const runtime = await runtimeWith([
    receipt("turn-a", closeoutRequired("turn-a", "todo_alpha")),
  ]);
  try {
    await writeFile(
      join(runtime.root, "goals", GOAL, "rollout-event-log.jsonl"),
      `${JSON.stringify(receipt("turn-a", closeoutRequired("turn-a", "todo_alpha")))}\nnot json\n`,
      "utf8",
    );
    await assert.rejects(
      () => preflight(runtime.root),
      /malformed/,
    );
  } finally {
    await runtime.close();
  }
});

test("an unrelated malformed line remains non-strict when no closeout exists", async () => {
  const runtime = await runtimeWith([
    receipt("turn-a", {closeout_required: false}),
  ]);
  try {
    await writeFile(
      join(runtime.root, "goals", GOAL, "rollout-event-log.jsonl"),
      `${JSON.stringify(receipt("turn-a", {closeout_required: false}))}\nnot json\n`,
      "utf8",
    );
    const result = await preflight(runtime.root);
    assert.equal(result.status, "none");
    assert.equal(result.reason, "no_prior_turn_requires_closeout");
  } finally {
    await runtime.close();
  }
});

test("settled history is read and indexed once instead of rescanned per Turn", async () => {
  const runtime = await runtimeWithSettledTurns(1000);
  try {
    const started = performance.now();
    const result = await preflight(runtime.root);
    const elapsedMs = performance.now() - started;
    assert.equal(result.status, "none");
    assert.equal(result.reason, "prior_turn_settlement_validated");
    assert.equal(result.turns_validated, 1000);
    assert.ok(
      elapsedMs < 1500,
      `indexed closeout preflight took ${elapsedMs.toFixed(1)}ms`,
    );
  } finally {
    await runtime.close();
  }
});

test("an unsettled Turn cannot be decided without its bound Todo facts", async () => {
  const runtime = await runtimeWith([
    receipt("turn-a", closeoutRequired("turn-a", "todo_alpha")),
  ]);
  try {
    const candidate = candidateFrom(await preflight(runtime.root));
    assert.throws(
      () => reduce(candidate),
      /requires its bound Todo facts/,
    );
  } finally {
    await runtime.close();
  }
});

test("the missing-receipt checkpoint is the closeout policy's own vocabulary", async () => {
  const runtime = await runtimeWith([
    receipt("turn-a", closeoutRequired("turn-a", "todo_alpha")),
  ]);
  try {
    const candidate = candidateFrom(await preflight(runtime.root));
    assert.throws(
      () => reduce(
        candidate,
        ["quota_spend_receipt", "durable_writeback_receipt"],
        READ_TODO(ADVANCEMENT_OPEN),
      ),
      /must be the closeout receipts/,
    );
    assert.throws(
      () => reduce(candidate, ["unknown_receipt"], READ_TODO(ADVANCEMENT_OPEN)),
      /must be the closeout receipts/,
    );
    assert.throws(
      () => reduce(candidate, [], READ_TODO(ADVANCEMENT_OPEN)),
      /must be the closeout receipts/,
    );
  } finally {
    await runtime.close();
  }
});

test("an exactly committed monitor-poll closeout is accepted", async () => {
  const todoId = "todo_monitor";
  const runtime = await runtimeWith([
    receipt("turn-a", closeoutRequired("turn-a", todoId)),
  ]);
  try {
    const candidate = candidateFrom(await preflight(runtime.root));
    const verdict = reduce(
      candidate,
      ["durable_writeback_receipt", "quota_spend_receipt"],
      READ_TODO(
        {task_class: "continuous_monitor", status: "open", has_resume_when: false,
          has_successor_todo_ids: false, target_key: "target", cadence: "1h"},
        {effect_id: `quota-monitor-poll:${GOAL}:${AGENT}:turn-a:todo:${todoId}`},
      ),
    );
    assert.equal(verdict.status, "none");
    assert.equal(verdict.accepted_closeout, "exact_committed_quota_monitor_poll");
  } finally {
    await runtime.close();
  }
});

test("a monitor-poll receipt for another Turn or effect is not an accepted closeout", async () => {
  const todoId = "todo_monitor";
  const runtime = await runtimeWith([
    receipt("turn-a", closeoutRequired("turn-a", todoId)),
  ]);
  try {
    const candidate = candidateFrom(await preflight(runtime.root));
    for (const effectId of [
      `quota-monitor-poll:${GOAL}:${AGENT}:turn-other:todo:${todoId}`,
      `quota-monitor-poll:${GOAL}:${AGENT}:turn-a:todo:todo_other`,
      "",
    ]) {
      const verdict = reduce(
        candidate,
        ["durable_writeback_receipt", "quota_spend_receipt"],
        READ_TODO(
          {task_class: "continuous_monitor", status: "open", has_resume_when: false,
            has_successor_todo_ids: false, target_key: null, cadence: null},
          {effect_id: effectId},
        ),
      );
      assert.equal(verdict.status, "recovery_required");
    }
  } finally {
    await runtime.close();
  }
});

test("a committed monitor-poll closeout is not accepted for a non-monitor Turn", async () => {
  const runtime = await runtimeWith([
    receipt("turn-a", closeoutRequired("turn-a", "todo_alpha")),
  ]);
  try {
    const candidate = candidateFrom(await preflight(runtime.root));
    const verdict = reduce(
      candidate,
      ["durable_writeback_receipt", "quota_spend_receipt"],
      READ_TODO(
        {...ADVANCEMENT_OPEN, target_key: null, cadence: null},
        {effect_id: `quota-monitor-poll:${GOAL}:${AGENT}:turn-a:todo:todo_alpha`},
      ),
    );
    assert.equal(verdict.status, "recovery_required");
    assert.equal((verdict.obligation as JsonObject).repair, "resume_prior_turn");
  } finally {
    await runtime.close();
  }
});

test("typed lifecycle closeouts are accepted and other open Todos are not", async () => {
  const runtime = await runtimeWith([
    receipt("turn-a", closeoutRequired("turn-a", "todo_alpha")),
  ]);
  try {
    const candidate = candidateFrom(await preflight(runtime.root));
    for (const [todo, accepted] of [
      [{...ADVANCEMENT_OPEN, has_resume_when: true, has_successor_todo_ids: true},
        "typed_external_wait_with_runnable_successor"],
      [{...ADVANCEMENT_OPEN, status: "done"},
        "typed_blocker_or_lifecycle_transition"],
      [{...ADVANCEMENT_OPEN, status: "blocked"},
        "typed_blocker_or_lifecycle_transition"],
      [{...ADVANCEMENT_OPEN, status: "deferred"},
        "typed_blocker_or_lifecycle_transition"],
      // A retained wait without a runnable successor is not a closeout.
      [{...ADVANCEMENT_OPEN, has_resume_when: true, has_successor_todo_ids: false}, null],
      // The persisted status is read verbatim; padding is not a transition.
      [{...ADVANCEMENT_OPEN, status: "done "}, null],
    ] as const) {
      const verdict = reduce(
        candidate,
        ["durable_writeback_receipt", "quota_spend_receipt"],
        READ_TODO(todo),
      );
      if (accepted === null) {
        assert.equal(verdict.status, "recovery_required");
      } else {
        assert.equal(verdict.status, "none");
        assert.equal(verdict.accepted_closeout, accepted);
      }
    }
  } finally {
    await runtime.close();
  }
});

test("an unsettled Turn keeps the exact public recovery payload", async () => {
  const runtime = await runtimeWith([
    receipt("turn-a", closeoutRequired("turn-a", "todo_alpha")),
  ]);
  try {
    const candidate = candidateFrom(await preflight(runtime.root));
    const verdict = reduce(
      candidate,
      ["durable_writeback_receipt", "quota_spend_receipt"],
      READ_TODO({...ADVANCEMENT_OPEN, target_key: "key-1", cadence: "1h"}),
    );
    assert.equal(verdict.status, "recovery_required");
    assert.deepEqual(verdict.recovery, {
      schema_version: "unsettled_host_turn_recovery_v0",
      state: "recovery_required",
      reason_code: "required_closeout_receipt_missing",
      prior_turn_instance_id: "turn-a",
      prior_event_id: "event-turn-a",
      binding_kind: "todo",
      binding_id: "todo_alpha",
      settlement_effect_id: `${GOAL}:${AGENT}:todo_alpha:turn-a`,
      missing_receipts: ["durable_writeback_receipt", "quota_spend_receipt"],
      accepted_closeouts: [...ACCEPTED_CLOSEOUTS],
      external_state_policy: "typed_host_observation_only",
      quota_policy: "no_spend_for_recovery_transition",
      binding_task_class: "advancement_task",
      binding_target_key: "key-1",
      binding_cadence: "1h",
      repair: "resume_prior_turn",
    });
    const obligation = verdict.obligation as JsonObject;
    assert.equal(obligation.lane, "control_plane_recovery");
    assert.equal(obligation.must_attempt_work, true);
    assert.equal(obligation.delivery_allowed, false);
    assert.equal(obligation.notify, "DONT_NOTIFY");
    assert.equal(obligation.repair, "resume_prior_turn");
  } finally {
    await runtime.close();
  }
});

test("continuation is a guarded route, not a closeout or an inferred wait", async () => {
  const runtime = await runtimeWith([
    receipt("turn-a", closeoutRequired("turn-a", "todo_alpha")),
  ]);
  try {
    const candidate = candidateFrom(await preflight(runtime.root));
    for (const missing of [
      ["durable_writeback_receipt", "quota_spend_receipt"],
      ["quota_spend_receipt"],
    ]) {
      for (const [todo, repair] of [
        [ADVANCEMENT_OPEN, "resume_prior_turn"],
        [{...ADVANCEMENT_OPEN, has_successor_todo_ids: true}, "resume_prior_turn"],
        [{...ADVANCEMENT_OPEN, has_resume_when: true}, "lifecycle"],
        [{...ADVANCEMENT_OPEN, task_class: "user_gate"}, "lifecycle"],
        [{...ADVANCEMENT_OPEN, status: "open "}, "lifecycle"],
        [null, "lifecycle"],
      ] as const) {
        const verdict = reduce(candidate, missing, READ_TODO(todo));
        assert.equal(verdict.status, "recovery_required");
        assert.equal((verdict.recovery as JsonObject).repair, repair);
        assert.equal((verdict.obligation as JsonObject).delivery_allowed, false);
        assert.equal(verdict.accepted_closeout, undefined);
        assert.deepEqual((verdict.recovery as JsonObject).missing_receipts, missing);
      }
    }
  } finally {
    await runtime.close();
  }
});

test("a monitor-bound recovery names only the receipts that are actually missing", async () => {
  const runtime = await runtimeWith([
    receipt("turn-a", closeoutRequired("turn-a", "todo_monitor")),
  ]);
  try {
    const candidate = candidateFrom(await preflight(runtime.root));
    const verdict = reduce(
      candidate,
      ["quota_spend_receipt"],
      READ_TODO({
        task_class: "continuous_monitor", status: "open", has_resume_when: false,
        has_successor_todo_ids: false, target_key: "target", cadence: "30m",
      }),
    );
    const recovery = verdict.recovery as JsonObject;
    assert.equal(verdict.status, "recovery_required");
    assert.equal(recovery.repair, "monitor_poll");
    assert.equal(recovery.binding_target_key, "target");
    assert.deepEqual(recovery.missing_receipts, ["quota_spend_receipt"]);
    assert.equal((verdict.obligation as JsonObject).repair, "monitor_poll");
  } finally {
    await runtime.close();
  }
});

test("an autonomous replan binding recovers without a Todo binding", async () => {
  const replanId = `replan-${"b".repeat(16)}`;
  const runtime = await runtimeWith([
    receipt("turn-a", closeoutRequired("turn-a", null, replanId)),
  ]);
  try {
    const candidate = candidateFrom(await preflight(runtime.root));
    assert.equal(candidate.binding_kind, "autonomous_replan");
    const verdict = reduce(
      candidate,
      ["durable_writeback_receipt", "quota_spend_receipt"],
      READ_TODO(null),
    );
    const recovery = verdict.recovery as JsonObject;
    assert.equal(recovery.binding_kind, "autonomous_replan");
    assert.equal(recovery.binding_id, replanId);
    assert.equal((verdict.obligation as JsonObject).repair, "lifecycle");
    assert.equal("binding_task_class" in recovery, false);
  } finally {
    await runtime.close();
  }
});

test("candidate bindings are revalidated before any verdict", async () => {
  assert.throws(
    () => reduce({
      prior_turn_instance_id: "turn-a",
      event_id: "event-turn-a",
      binding_kind: "todo",
      binding_id: "TODO_ALPHA",
      settlement_effect_id: null,
    }, ["durable_writeback_receipt", "quota_spend_receipt"], READ_TODO(null)),
    /not a legal Todo id/,
  );
  assert.throws(
    () => reduce({
      prior_turn_instance_id: "turn-a",
      event_id: null,
      binding_kind: "autonomous_replan",
      binding_id: "replan-not-an-obligation",
      settlement_effect_id: null,
    }, ["durable_writeback_receipt", "quota_spend_receipt"], READ_TODO(null)),
    /autonomous replan binding is malformed/,
  );
});
