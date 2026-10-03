import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  appendFile,
  mkdir,
  mkdtemp,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";

import { settlementIdentity } from "../../loopx/control_plane/effect_program.ts";
import {
  acquireFileMutationLock,
  releaseFileMutationLock,
} from "../../loopx/control_plane/effect_runtime_io.ts";
import { BLOCKED_WAIT_REQUEST_SCHEMA, prepareBlockedWait } from "../../loopx/control_plane/quota/blocked_wait.ts";
import { evaluateTodoResumeConditions, TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION } from "../../loopx/control_plane/todos/resume_condition.ts";
import {
  projectSemanticReplanGuard,
  QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA,
  readQuotaSettlement,
  readQuotaSettlementSnapshot,
} from "../../loopx/control_plane/quota/settlement_readback.ts";
import { requireJsonObject } from "../../loopx/control_plane/runtime_decode.ts";

const goalId = "settlement-goal";
const agentId = "codex-settlement";
const todoId = "todo_settlement";
const turnId = "turn-settlement-1";
const instanceA = "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const instanceB = "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
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
  /**
   * Commit the guard's own deferred explicit selection for this Turn: the
   * receipt retains the chosen Todo but still carries no settlement binding,
   * because the guard bound the preemption only on argument-less reentry.
   */
  guardDeferred?: boolean;
  writeback?: boolean;
  spend?: boolean;
  completion?: boolean;
  noFollowup?: boolean;
  workspace?: boolean;
  monitor?: boolean;
  writebackOutcome?: string;
  progressObservation?: Record<string, unknown>;
  blockedRetry?: boolean | Record<string, unknown>;
  visionCheckpoint?: Record<string, unknown>;
  autonomousReplanAck?: Record<string, unknown>;
  goalRef?: Record<string, string>;
  turnId?: string;
} = {}) {
  const runtimeRoot = await mkdtemp(join(tmpdir(), "loopx-settlement-readback-"));
  const goalRoot = join(runtimeRoot, "goals", goalId);
  const runsRoot = join(goalRoot, "runs");
  const ownerProjection = options.goalRef ? { goal_ref: options.goalRef } : {};
  const fixtureTurnId = options.turnId ?? turnId;
  const fixtureIdentity = settlementIdentity({
    goal_id: goalId,
    agent_id: agentId,
    todo_id: todoId,
    turn_instance_id: fixtureTurnId,
  });
  await mkdir(runsRoot, { recursive: true });
  const events: Record<string, unknown>[] = options.guard === false
    ? []
    : [{
      schema_version: "loopx_rollout_event_v0",
      event_id: "event-guard",
      event_kind: "quota_should_run",
      goal_id: goalId,
      agent_id: agentId,
      run_id: fixtureTurnId,
      ...ownerProjection,
      details: {
        ...(options.guardUnbound
          ? {}
          : {
            todo_id: todoId,
            settlement_effect_id: fixtureIdentity.effect_id,
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
  if (options.guardDeferred) {
    events.push({
      schema_version: "loopx_rollout_event_v0",
      event_id: "event-guard-deferred",
      event_kind: "quota_should_run",
      goal_id: goalId,
      agent_id: agentId,
      run_id: fixtureTurnId,
      status: "action_selection_deferred",
      ...ownerProjection,
      details: {
        pending_action_selection_todo_id: todoId,
        pending_action_selection_state: "deferred",
        pending_action_selection_reason: "autonomous_replan",
        settlement_effect_id: "",
        todo_id: "",
        replan_obligation_id: "",
      },
    });
  }
  if (options.writeback) {
    events.push({
      schema_version: "loopx_rollout_event_v0",
      event_id: "event-writeback",
      event_kind: "refresh_state",
      goal_id: goalId,
      agent_id: agentId,
      run_id: fixtureTurnId,
      ...ownerProjection,
      details: { settlement_effect_id: fixtureIdentity.effect_id },
    });
    runs.push({
      classification: "state_refreshed",
      delivery_outcome: options.writebackOutcome ?? "outcome_progress",
      goal_id: goalId,
      agent_id: agentId,
      todo_id: todoId,
      turn_instance_id: fixtureTurnId,
      settlement_identity: fixtureIdentity,
      ...ownerProjection,
      ...(options.visionCheckpoint ? {vision_checkpoint: options.visionCheckpoint} : {}),
      ...(options.autonomousReplanAck ? {autonomous_replan_ack: options.autonomousReplanAck} : {}),
      ...(options.blockedRetry ? {blocked_retry: typeof options.blockedRetry === "object" ? options.blockedRetry : {
        schema_version: "quota_blocked_retry_v0",
        source: "todo",
        todo_id: todoId,
        resume_when: "resume_at:2026-09-24T10:05:00Z",
        observed_at: "2026-09-24T10:00:00Z",
        due_at: "2026-09-24T10:05:00Z",
      }} : {}),
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
      run_id: fixtureTurnId,
      ...ownerProjection,
      details: { settlement_effect_id: fixtureIdentity.effect_id },
    });
    runs.push({
      classification: "quota_slot_spent",
      goal_id: goalId,
      agent_id: agentId,
      todo_id: todoId,
      turn_instance_id: fixtureTurnId,
      settlement_identity: fixtureIdentity,
      ...ownerProjection,
    });
  }
  if (options.completion) {
    events.push({
      schema_version: "loopx_rollout_event_v0",
      event_id: "event-completion",
      event_kind: "todo_complete",
      goal_id: goalId,
      agent_id: agentId,
      run_id: fixtureTurnId,
      ...ownerProjection,
      details: {
        settlement_effect_id: fixtureIdentity.effect_id,
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
      turn_instance_id: fixtureTurnId,
      material_change: true,
      ...ownerProjection,
      quota_monitor_poll_commit: {
        schema_version: "quota_monitor_poll_commit_receipt_v0",
        effect_id: `quota-monitor-poll:${goalId}:${agentId}:${fixtureTurnId}:todo:${todoId}`,
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

async function appendHistory(targetRoot: string, sourceRoot: string): Promise<void> {
  const relativePaths = [
    join("goals", goalId, "rollout-event-log.jsonl"),
    join("goals", goalId, "runs", "index.jsonl"),
  ];
  for (const relativePath of relativePaths) {
    await appendFile(
      join(targetRoot, relativePath),
      await readFile(join(sourceRoot, relativePath), "utf8"),
    );
  }
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

function sourceGuardPath(registryPath: string): string {
  const digest = createHash("sha256").update(goalId, "utf8").digest("hex");
  return join(
    dirname(registryPath),
    ".loopx",
    "lifecycle",
    "goal-instance",
    "guards",
    `${digest}.guard`,
  );
}

async function withSourceAdmission<T>(
  runtimeRoot: string,
  plannedInstanceId: string,
  currentInstanceId: string,
  run: (binding: Record<string, unknown>) => Promise<T>,
): Promise<T> {
  const indexPath = join(runtimeRoot, "goals", goalId, "runs", "index.jsonl");
  const registryPath = join(runtimeRoot, "project", ".loopx", "registry.json");
  const guardPath = sourceGuardPath(registryPath);
  const indexLock = await acquireFileMutationLock(indexPath);
  const guardLock = await acquireFileMutationLock(guardPath);
  try {
    return await run({
      goal_ref: {
        goal_id: goalId,
        goal_instance_id: plannedInstanceId,
      },
      source_admission: {
        schema_version: "loopx_quota_source_admission_v0",
        profile_id: "source_session_v1",
        registry_path: registryPath,
        planned_goal_ref: {
          goal_id: goalId,
          goal_instance_id: plannedInstanceId,
        },
        authority: {
          kind: "present",
          goal_ref: {
            goal_id: goalId,
            goal_instance_id: currentInstanceId,
          },
        },
        locks: [
          {
            role: "run_index",
            target: indexPath,
            pid: process.pid,
            token: indexLock.token,
          },
          {
            role: "source_guard",
            target: guardPath,
            pid: process.pid,
            token: guardLock.token,
          },
        ],
      },
    });
  } finally {
    await releaseFileMutationLock(guardPath, guardLock.token, null, true);
    await releaseFileMutationLock(indexPath, indexLock.token, null, true);
  }
}

test("scoped supersede settles only its exact Turn and never terminal acceptance", async t => {
  const cases = [
    {name: "original tuple", expected: "settled", patch: {}},
    {name: "missing turn", expected: "open", patch: {run_id: null}},
    {name: "foreign turn", expected: "open", patch: {run_id: "other-turn"}},
    {name: "foreign goal", expected: "open", patch: {goal_id: "other-goal"}},
    {name: "foreign actor", expected: "open", patch: {agent_id: "other-agent"}},
    {name: "foreign Todo", expected: "open", patch: {todo_id: "todo_other"}},
    {name: "failed retirement", expected: "open", patch: {status: "failed"}},
    {name: "missing effect", expected: "open", patch: {details: {}}},
    {name: "foreign effect", expected: "open", patch: {details: {settlement_effect_id: "other-effect"}}},
  ];
  for (const entry of cases) await t.test(entry.name, async () => {
    const root = await fixture({writeback: true, spend: true});
    try {
      const path = join(root, "goals", goalId, "rollout-event-log.jsonl");
      await appendFile(path, JSON.stringify({
        schema_version: "loopx_rollout_event_v0", event_id: "supersede-receipt",
        event_kind: "todo_supersede", status: "done", goal_id: goalId,
        agent_id: agentId, todo_id: todoId, run_id: turnId,
        details: {settlement_effect_id: identity.effect_id}, ...entry.patch,
      }) + "\n");
      const result = await readQuotaSettlement(request(root));
      assert.equal(result.replay_phase, entry.expected);
      assert.equal(result.completion_event, null);
      assert.equal((result.terminal_closeout as {payload: {ok: boolean}}).payload.ok, false);
    } finally { await rm(root, {recursive: true, force: true}); }
  });
});

test("settlement progress requires both effects and exact receipts", async t => {
  const cases = [
    { guard: false, state: "identity_required", next: "validation" },
    { state: "writeback_required", next: "durable_writeback" },
    { writeback: true, remove: "refresh_state", state: "writeback_receipt_required", next: "durable_writeback" },
    { writeback: true, state: "spend_required", next: "quota_spend" },
    { writeback: true, spend: true, remove: "quota_spend", state: "spend_receipt_required", next: "quota_spend" },
    { writeback: true, spend: true, state: "settled", next: null },
  ];
  for (const entry of cases) await t.test(entry.state, async () => {
    const root = await fixture(entry);
    try {
      if (entry.remove) {
        const path = join(root, "goals", goalId, "rollout-event-log.jsonl");
        const events = (await readFile(path, "utf8")).trim().split("\n").map(line => JSON.parse(line));
        await writeFile(path, events.filter(event => event.event_kind !== entry.remove).map(event => JSON.stringify(event)).join("\n") + "\n");
      }
      const result = await readQuotaSettlement(request(root));
      assert.deepEqual(result.progress, {
        schema_version: "quota_settlement_progress_v0", state: entry.state,
        next_step: entry.next, quota_spend_source: "heartbeat",
      });
    } finally { await rm(root, { recursive: true, force: true }); }
  });
});

test("settlement readback enforces exact source ownership before identity inference", async () => {
  const goalRefA = {
    goal_id: goalId,
    goal_instance_id: instanceA,
  };
  const root = await fixture({
    writeback: true,
    spend: true,
    goalRef: goalRefA,
  });
  try {
    const unscoped = await readQuotaSettlement(request(root));
    assert.equal(unscoped.found, true);
    const unscopedIdentity = requireJsonObject(
      requireJsonObject(unscoped.identity, "unscoped identity").result,
      "unscoped identity result",
    );
    assert.equal(
      requireJsonObject(
        unscopedIdentity.failure,
        "unscoped identity failure",
      ).kind,
      "receipt_missing",
    );
    assert.equal(unscoped.spend_run, null);

    const inferred = await withSourceAdmission(
      root,
      instanceB,
      instanceB,
      async (binding) => await readQuotaSettlement(request(root, {
        ...binding,
        turn_instance_id: null,
        infer_turn_instance_id: true,
      })),
    );
    assert.deepEqual(inferred, {
      schema_version: "loopx_quota_settlement_readback_result_v0",
      found: false,
    });

    await assert.rejects(
      withSourceAdmission(
        root,
        instanceA,
        instanceB,
        async (binding) => await readQuotaSettlement(request(root, binding)),
      ),
      (error: unknown) => {
        assert.equal(
          requireJsonObject(error, "stale GoalRef error").code,
          "stale_goal_instance",
        );
        return true;
      },
    );

    const matching = await withSourceAdmission(
      root,
      instanceA,
      instanceA,
      async (binding) => await readQuotaSettlement(request(root, binding)),
    );
    const matchingSpend = requireJsonObject(matching.spend, "matching spend");
    const matchingPayload = requireJsonObject(
      matchingSpend.payload,
      "matching spend payload",
    );
    assert.equal(matchingPayload.ok, true);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("alias and exact histories remain independently readable", async () => {
  const legacy = await fixture({ writeback: true, spend: true });
  const exact = await fixture({
    writeback: true,
    spend: true,
    goalRef: {
      goal_id: goalId,
      goal_instance_id: instanceA,
    },
  });
  try {
    await appendHistory(legacy, exact);
    const aliasReadback = await readQuotaSettlement(request(legacy));
    assert.equal(
      requireJsonObject(
        requireJsonObject(aliasReadback.settlement, "alias settlement").payload,
        "alias settlement payload",
      ).ok,
      true,
    );
    const exactReadback = await withSourceAdmission(
      legacy,
      instanceA,
      instanceA,
      async (binding) => await readQuotaSettlement(request(legacy, binding)),
    );
    assert.equal(
      requireJsonObject(
        requireJsonObject(exactReadback.settlement, "exact settlement").payload,
        "exact settlement payload",
      ).ok,
      true,
    );
  } finally {
    await rm(legacy, { recursive: true, force: true });
    await rm(exact, { recursive: true, force: true });
  }
});

test("a delayed stale owner cannot replace the current owner's inferred Turn", async () => {
  const current = await fixture({
    writeback: true,
    spend: true,
    goalRef: {
      goal_id: goalId,
      goal_instance_id: instanceB,
    },
  });
  const delayed = await fixture({
    writeback: true,
    spend: true,
    turnId: "turn-stale-a",
    goalRef: {
      goal_id: goalId,
      goal_instance_id: instanceA,
    },
  });
  try {
    await appendHistory(current, delayed);
    const inferred = await withSourceAdmission(
      current,
      instanceB,
      instanceB,
      async (binding) => await readQuotaSettlement(request(current, {
        ...binding,
        turn_instance_id: null,
        infer_turn_instance_id: true,
      })),
    );
    assert.equal(inferred.found, true);
    assert.equal(
      requireJsonObject(
        requireJsonObject(inferred.settlement, "current settlement").payload,
        "current settlement payload",
      ).ok,
      true,
    );
  } finally {
    await rm(current, { recursive: true, force: true });
    await rm(delayed, { recursive: true, force: true });
  }
});

test("borrowed exact admission remains valid for an enclosing transaction", async () => {
  const root = await fixture({
    writeback: true,
    goalRef: {
      goal_id: goalId,
      goal_instance_id: instanceA,
    },
  });
  try {
    await withSourceAdmission(
      root,
      instanceA,
      instanceA,
      async (binding) => {
        const borrowed = {
          ...binding,
          borrow_source_admission: true,
        };
        assert.equal(
          (await readQuotaSettlement(request(root, borrowed))).found,
          true,
        );
        assert.equal(
          (await readQuotaSettlement(request(root, borrowed))).found,
          true,
        );
      },
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("legacy settlement still consumes legacy rows", async () => {
  const root = await fixture({ writeback: true, spend: true });
  try {
    const result = await readQuotaSettlement(request(root));
    assert.equal(result.found, true);
    assert.equal(
      requireJsonObject(
        requireJsonObject(result.settlement, "legacy settlement").payload,
        "legacy settlement payload",
      ).ok,
      true,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("settlement progress preserves typed source and rejects malformed sources", async () => {
  const root = await fixture({writeback: true});
  try {
    const path = join(root, "goals", goalId, "rollout-event-log.jsonl");
    const events = (await readFile(path, "utf8")).trim().split("\n").map(line => JSON.parse(line));
    for (const source of ["visible-goal", "unknown", 42]) {
      events[0].details.quota_spend_source = source;
      await writeFile(path, events.map(event => JSON.stringify(event)).join("\n") + "\n");
      if (source === "visible-goal") {
        const result = await readQuotaSettlement(request(root));
        assert.equal((result.progress as Record<string, unknown>).quota_spend_source, source);
      } else {
        await assert.rejects(readQuotaSettlement(request(root)), /settlement spend source is invalid/);
      }
    }
  } finally { await rm(root, {recursive: true, force: true}); }
});

test("accepted in-flight writeback closes only the exact Turn, not its Todo", async t => {
  const checkpoint = {
    schema_version: "vision_checkpoint_v0", agent_id: agentId, satisfied: true,
    delivery_boundary: "in_flight_continuation",
    triggers: [{kind: "in_flight_continuation", todo_id: todoId}],
  };
  const cases = [
    {name: "both effects", spend: true, expected: "settled"},
    {name: "spend still required", spend: false, expected: "settlement_pending"},
    {name: "writeback receipt missing", spend: true, remove: "refresh_state", expected: "open"},
    {name: "spend receipt missing", spend: true, remove: "quota_spend", expected: "settlement_pending"},
  ];
  for (const entry of cases) await t.test(entry.name, async () => {
    const root = await fixture({writeback: true, spend: entry.spend, visionCheckpoint: checkpoint});
    try {
      if (entry.remove) {
        const path = join(root, "goals", goalId, "rollout-event-log.jsonl");
        const events = (await readFile(path, "utf8")).trim().split("\n").map(line => JSON.parse(line));
        await writeFile(path, events.filter(event => event.event_kind !== entry.remove).map(event => JSON.stringify(event)).join("\n") + "\n");
      }
      const result = await readQuotaSettlement(request(root));
      assert.equal(result.replay_phase, entry.expected);
      assert.equal(result.completion_event, null);
      assert.equal((result.terminal_closeout as any).payload.ok, false);
    } finally { await rm(root, {recursive: true, force: true}); }
  });
});

test("qualified path replan closes its Turn without a preselected obligation or Todo completion", async t => {
  const accepted = {
    schema_version: "autonomous_replan_ack_v0", recorded: true,
    delta_contract: {schema_version: "repair_delta_contract_v0", delta_present: true},
  };
  const cases = [
    {name: "qualified repair delta", ack: accepted, spend: true, expected: "settled"},
    {name: "qualified semantic delta", ack: {...accepted, delta_contract: null,
      semantic_delta: {schema_version: "replan_semantic_delta_v0", accepted: true}}, spend: true, expected: "settled"},
    {name: "spend still required", ack: accepted, spend: false, expected: "settlement_pending"},
    {name: "missing writeback receipt", ack: accepted, spend: true, remove: "refresh_state", expected: "open"},
    {name: "missing spend receipt", ack: accepted, spend: true, remove: "quota_spend", expected: "settlement_pending"},
    {name: "unrecorded", ack: {...accepted, recorded: false}, spend: true, expected: "open"},
    {name: "truthy is not recorded", ack: {...accepted, recorded: "true"}, spend: true, expected: "open"},
    {name: "wrong ACK schema", ack: {...accepted, schema_version: "other"}, spend: true, expected: "open"},
    {name: "unqualified delta", ack: {...accepted, delta_contract: {schema_version: "repair_delta_contract_v0", delta_present: false}}, spend: true, expected: "open"},
    {name: "unqualified semantic delta", ack: {...accepted, delta_contract: null,
      semantic_delta: {schema_version: "replan_semantic_delta_v0", accepted: false}}, spend: true, expected: "open"},
    {name: "wrong delta schema", ack: {...accepted, delta_contract: {schema_version: "other", delta_present: true}}, spend: true, expected: "open"},
    ...["goal_id", "agent_id", "todo_id", "turn_instance_id"].map(field => ({
      name: `wrong ${field}`, ack: accepted, spend: true, patch: {[field]: "other"}, expected: "open",
    })),
    {name: "wrong effect", ack: accepted, spend: true,
      patch: {settlement_identity: {...identity, effect_id: "other"}}, expected: "open"},
  ];
  for (const entry of cases) await t.test(entry.name, async () => {
    const root = await fixture({writeback: true, spend: entry.spend, autonomousReplanAck: entry.ack});
    try {
      if ("remove" in entry) {
        const path = join(root, "goals", goalId, "rollout-event-log.jsonl");
        const rows = (await readFile(path, "utf8")).trim().split("\n").map(line => JSON.parse(line));
        await writeFile(path, rows.filter(row => row.event_kind !== entry.remove).map(row => JSON.stringify(row)).join("\n") + "\n");
      }
      if ("patch" in entry) {
        const path = join(root, "goals", goalId, "runs", "index.jsonl");
        const rows = (await readFile(path, "utf8")).trim().split("\n").map(line => JSON.parse(line));
        rows[0] = {...rows[0], ...entry.patch};
        await writeFile(path, rows.map(row => JSON.stringify(row)).join("\n") + "\n");
      }
      const result = await readQuotaSettlement(request(root));
      assert.equal(result.replay_phase, entry.expected);
      assert.equal(result.completion_event, null);
      assert.equal((result.terminal_closeout as any).payload.ok, false);
    } finally { await rm(root, {recursive: true, force: true}); }
  });
});

test("in-flight replay rejects unaccepted checkpoints and unrelated identities", async t => {
  const checkpoint = {
    schema_version: "vision_checkpoint_v0", agent_id: agentId, satisfied: true,
    delivery_boundary: "in_flight_continuation",
    triggers: [{kind: "in_flight_continuation", todo_id: todoId}],
  };
  const patches: [string, Record<string, unknown>][] = [
    ["checkpoint absent", {vision_checkpoint: null}],
    ["checkpoint malformed", {vision_checkpoint: []}],
    ["not accepted", {vision_checkpoint: {...checkpoint, satisfied: false}}],
    ["truthy is not acceptance", {vision_checkpoint: {...checkpoint, satisfied: "true"}}],
    ["wrong schema", {vision_checkpoint: {...checkpoint, schema_version: "other"}}],
    ["semantic closeout", {vision_checkpoint: {...checkpoint, delivery_boundary: "semantic_closeout"}}],
    ["checkpoint other agent", {vision_checkpoint: {...checkpoint, agent_id: "peer"}}],
    ["no trigger", {vision_checkpoint: {...checkpoint, triggers: []}}],
    ["trigger other Todo", {vision_checkpoint: {...checkpoint, triggers: [{kind: "in_flight_continuation", todo_id: "todo_other"}]}}],
    ["trigger wrong kind", {vision_checkpoint: {...checkpoint, triggers: [{kind: "vision_unchanged", todo_id: todoId}]}}],
    ...["surface_only", "outcome_gap", "primary_goal_outcome"].map(outcome => [outcome, {delivery_outcome: outcome}] as [string, Record<string, unknown>]),
    ...["goal_id", "agent_id", "todo_id", "turn_instance_id"].map(field => [field, {[field]: "other"}] as [string, Record<string, unknown>]),
    ["effect mismatch", {settlement_identity: {...identity, effect_id: "other"}}],
  ];
  for (const [name, patch] of patches) await t.test(name, async () => {
    const root = await fixture({writeback: true, spend: true, visionCheckpoint: checkpoint});
    try {
      const path = join(root, "goals", goalId, "runs", "index.jsonl");
      const runs = (await readFile(path, "utf8")).trim().split("\n").map(line => JSON.parse(line));
      runs[0] = {...runs[0], ...patch};
      await writeFile(path, runs.map(run => JSON.stringify(run)).join("\n") + "\n");
      const result = await readQuotaSettlement(request(root));
      assert.equal(result.replay_phase, "open");
      assert.equal(result.completion_event, null);
    } finally { await rm(root, {recursive: true, force: true}); }
  });
});

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
        assert.equal(result.replay_phase, expected === "settled" ? "settled" : "open");
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

test("committed monitor replay closes without manufacturing delivery or spend receipts", async () => {
  const runtimeRoot = await fixture({ writeback: true, monitor: true });

  const result = await readQuotaSettlement(request(runtimeRoot));

  assert.equal((result.writeback as any).payload.ok, true);
  assert.equal((result.spend as any).payload.ok, false);
  assert.equal((result.settlement as any).result.failure.kind, "receipt_missing");
  assert.equal(result.monitor_phase, "settled");
  assert.equal(result.replay_phase, "settled");
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
  // The receipt exists and is well-formed, so the missing binding has its own
  // kind instead of reading as a mismatch against a second record.
  assert.equal(failure.kind, "receipt_unbound");
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

test("names the argument-less guard reentry for a deferred explicit selection", async () => {
  // A deferred explicit selection is also identity-less, but its repair is not
  // "rebind with --todo-id": that re-enters the same preemption and defers
  // again, which is how a caller ends up looping instead of settling. The
  // retained selection tells the two unbound states apart, so the refusal can
  // name the reentry that actually binds the preemption.
  const runtimeRoot = await fixture({ guardUnbound: true, guardDeferred: true });

  const result = await readQuotaSettlement(request(runtimeRoot));

  const failure = (result.settlement as any).result.failure;
  // Both unbound states share the receipt's own failure kind; the deferred
  // selection is told apart by the repair text and the retained selection.
  assert.equal(failure.kind, "receipt_unbound");
  assert.match(failure.reason, /carries no settlement binding yet/);
  assert.match(
    failure.reason,
    new RegExp(
      `quota should-run --turn-instance-id ${turnId}(?! --todo-id)`,
    ),
  );
  assert.match(failure.reason, /without --todo-id/);
  assert.doesNotMatch(
    failure.reason,
    new RegExp(`--todo-id ${todoId}`),
  );
  assert.deepEqual(failure.details, {
    binding_kind: "unbound",
    requested_binding_kind: "todo",
    turn_instance_id: turnId,
    deferred_selection_todo_id: todoId,
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

test("causal no-spend closeout retains exact receipt identity and historical debits", async () => {
  const target = { todo_id: "todo_dependency", role: "agent", status: "open",
    task_class: "continuous_monitor", material_change_generation: 2 };
  const waiting = { todo_id: todoId, role: "agent", status: "open",
    task_class: "advancement_task", resume_when: `monitor_changed:${target.todo_id}`,
    resume_ready: false, resume_monitor_generation: 2 };
  const evaluated = evaluateTodoResumeConditions({
    schema_version: TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION,
    items: [waiting], source_items: [target],
  });
  const condition = (evaluated.conditions as Record<string, unknown>[])[0].condition;
  const proof = prepareBlockedWait({ schema_version: BLOCKED_WAIT_REQUEST_SCHEMA,
    todo_id: todoId, observed_at: "2026-09-24T10:00:00Z",
    todos: [{ ...waiting, resume_condition: condition }, target] });
  const options = { writeback: true, writebackOutcome: "outcome_gap", blockedRetry: proof,
    progressObservation: { schema_version: "typed_progress_observation_v0",
      result_class: "blocked", work_item_id: todoId,
      blocker_id: target.todo_id, evidence_ids: ["evidence:canonical-wait"] } };
  const runtime = await fixture(options);
  try {
    const result = await readQuotaSettlement(request(runtime));
    assert.equal((result.progress as any).state, "settled");
    assert.equal((result.progress as any).closeout_kind, "typed_blocked_writeback_no_spend");
    assert.equal(result.replay_phase, "settled");
    // Frozen historical facts stay closed after a later dependency observation.
    await appendFile(join(runtime, "goals", goalId, "runs", "index.jsonl"),
      `${JSON.stringify({ classification: "quota_monitor_poll", goal_id: goalId,
        agent_id: agentId, todo_id: target.todo_id, turn_instance_id: "later-turn",
        material_change_generation: 3 })}\n`);
    assert.equal((await readQuotaSettlement(request(runtime))).replay_phase, "settled");
  } finally {
    await rm(runtime, { recursive: true, force: true });
  }
  for (const field of ["goal_id", "agent_id", "todo_id", "turn_instance_id"]) {
    const mismatch = await fixture(options);
    try {
      const index = join(mismatch, "goals", goalId, "runs", "index.jsonl");
      const run = JSON.parse((await readFile(index, "utf8")).trim());
      await writeFile(index, `${JSON.stringify({ ...run, [field]: "another-identity" })}\n`);
      const result = await readQuotaSettlement(request(mismatch));
      assert.notEqual((result.progress as any).state, "settled", field);
      assert.notEqual(result.replay_phase, "settled", field);
    } finally {
      await rm(mismatch, { recursive: true, force: true });
    }
  }
  const missing = await fixture({ ...options, guard: false });
  const missingWriteback = await fixture(options);
  const spent = await fixture({ ...options, spend: true });
  const malformed = await fixture({ ...options, blockedRetry: { ...proof,
    waiting_todo: { ...waiting, resume_monitor_generation: 3 } } });
  try {
    assert.notEqual((await readQuotaSettlement(request(missing))).replay_phase, "settled");
    const log = join(missingWriteback, "goals", goalId, "rollout-event-log.jsonl");
    const events = (await readFile(log, "utf8")).trim().split("\n").map(line => JSON.parse(line));
    await writeFile(log, `${events.filter(event => event.event_kind !== "refresh_state").map(event => JSON.stringify(event)).join("\n")}\n`);
    assert.notEqual((await readQuotaSettlement(request(missingWriteback))).replay_phase, "settled");
    const debited = await readQuotaSettlement(request(spent));
    assert.equal((debited.spend as any).payload.ok, true);
    assert.equal((debited.progress as any).closeout_kind, undefined);
    assert.equal((await readQuotaSettlement(request(malformed))).replay_phase, "open");
  } finally {
    await Promise.all([missing, missingWriteback, spent, malformed].map(path =>
      rm(path, { recursive: true, force: true })));
  }
});

test("accepts only an attributable typed blocker as an outcome-gap writeback", async () => {
  const qualifiedRuntime = await fixture({
    writeback: true,
    writebackOutcome: "outcome_gap",
    blockedRetry: true,
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
  assert.equal((qualified.settlement as any).payload.ok, true);
  assert.equal((qualified.spend as any).payload.ok, false);
  assert.deepEqual((qualified.settlement as any).result.receipts.map(
    (receipt: any) => receipt.step_kind), ["validation", "durable_writeback"]);
  assert.equal((qualified.progress as any).state, "settled");
  assert.equal((qualified.progress as any).next_step, null);
  assert.equal((qualified.progress as any).closeout_kind,
    "typed_blocked_writeback_no_spend");
  assert.equal(qualified.replay_phase, "settled");

  const spentRuntime = await fixture({
    writeback: true,
    spend: true,
    writebackOutcome: "outcome_gap",
    blockedRetry: true,
    progressObservation: {
      schema_version: "typed_progress_observation_v0",
      result_class: "blocked",
      work_item_id: todoId,
      blocker_id: "blocker-runtime-boundary",
      evidence_ids: ["evidence-runtime-boundary"],
    },
  });
  const spent = await readQuotaSettlement(request(spentRuntime));
  assert.equal((spent.spend as any).payload.ok, true);
  assert.equal((spent.progress as any).closeout_kind, undefined);
  assert.deepEqual((spent.settlement as any).result.receipts.map(
    (receipt: any) => receipt.step_kind),
    ["validation", "durable_writeback", "quota_spend"]);

  const incompleteSpendRuntime = await fixture({
    writeback: true,
    writebackOutcome: "outcome_gap",
    blockedRetry: true,
    progressObservation: {
      schema_version: "typed_progress_observation_v0",
      result_class: "blocked",
      work_item_id: todoId,
      blocker_id: "blocker-runtime-boundary",
      evidence_ids: ["evidence-runtime-boundary"],
    },
  });
  await appendFile(join(incompleteSpendRuntime, "goals", goalId,
    "rollout-event-log.jsonl"), `${JSON.stringify({
      schema_version: "loopx_rollout_event_v0",
      event_id: "event-incomplete-spend",
      event_kind: "quota_spend",
      goal_id: goalId,
      agent_id: agentId,
      run_id: turnId,
      details: {settlement_effect_id: identity.effect_id},
    })}\n`);
  const incompleteSpend = await readQuotaSettlement(request(incompleteSpendRuntime));
  assert.equal((incompleteSpend.settlement as any).payload.ok, false);
  assert.equal((incompleteSpend.progress as any).closeout_kind, undefined);

  const unscheduledRuntime = await fixture({
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
  const unscheduled = await readQuotaSettlement(request(unscheduledRuntime));
  assert.equal((unscheduled.progress as any).state, "spend_required");
  assert.equal((unscheduled.progress as any).closeout_kind, undefined);

  const bareRuntime = await fixture({
    writeback: true,
    writebackOutcome: "outcome_gap",
  });
  const bare = await readQuotaSettlement(request(bareRuntime));
  assert.equal((bare.writeback as any).payload.ok, false);
  assert.equal((bare.writeback as any).result.failure.kind, "writeback_missing");
  assert.equal((bare.settlement as any).payload.ok, false);

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
  assert.equal((mismatched.settlement as any).payload.ok, false);

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
    readQuotaSettlementSnapshot("relative", goalId),
    /runtime_root must be absolute/,
  );
  await assert.rejects(
    readQuotaSettlementSnapshot(runtimeRoot, "../other-goal"),
    /goal_id must be a single path segment/,
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
