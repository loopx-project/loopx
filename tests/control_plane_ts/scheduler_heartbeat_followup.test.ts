import assert from "node:assert/strict";
import {
  mkdir,
  mkdtemp,
  readFile,
  rename,
  rm,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { setTimeout as delay } from "node:timers/promises";

import {
  goalHeartbeatReceiptsFromSnapshot,
  readGoalRolloutEventSnapshot,
  strictGoalRolloutEvents,
} from "../../loopx/control_plane/rollout_receipt_log.ts";
import {
  acquireFileMutationLock,
  releaseFileMutationLock,
} from "../../loopx/control_plane/effect_runtime_io.ts";
import {
  evaluateSchedulerHeartbeatFollowup,
  SCHEDULER_HEARTBEAT_FOLLOWUP_REQUEST_SCHEMA,
  SCHEDULER_HEARTBEAT_FOLLOWUP_RESULT_SCHEMA,
} from "../../loopx/control_plane/scheduler/heartbeat_followup.ts";
import { schedulerStatePath } from "../../loopx/control_plane/scheduler/state_store.ts";

type CleanupContext = {
  after(callback: () => void | Promise<void>): void;
};

const scope = {
  goal_id: "goal-followup",
  agent_id: "agent-followup",
  surface: "codex_app",
  state_key: "scheduler_hint.app_automation.stateful_backoff",
};

const before = {
  should_run: true,
  normal_delivery_allowed: true,
  recovery_delivery_allowed: false,
  effective_action: "normal_run",
  self_repair_allowed: false,
  capability_repair_allowed: false,
  workspace_repair_allowed: false,
  state: "eligible",
  safe_bypass_allowed: false,
  safe_bypass_kind: null,
  blocked_action_scope: null,
  compute: 1,
  window_hours: 4,
  slot_minutes: 15,
  spent_slots: 0,
  allowed_slots: 16,
};

async function tempRuntime(t: CleanupContext): Promise<string> {
  const runtimeRoot = await mkdtemp(join(tmpdir(), "loopx-heartbeat-followup-"));
  t.after(() => rm(runtimeRoot, { recursive: true, force: true }));
  return runtimeRoot;
}

function receiptPath(runtimeRoot: string): string {
  return join(runtimeRoot, "goals", scope.goal_id, "rollout-event-log.jsonl");
}

async function appendReceipt(runtimeRoot: string, turnId: string): Promise<void> {
  const path = receiptPath(runtimeRoot);
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, `${JSON.stringify({
    schema_version: "loopx_rollout_event_v0",
    event_kind: "quota_should_run",
    goal_id: scope.goal_id,
    agent_id: scope.agent_id,
    run_id: turnId,
  })}\n`, { encoding: "utf8", flag: "a" });
}

function request(
  runtimeRoot: string,
  extra: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    schema_version: SCHEDULER_HEARTBEAT_FOLLOWUP_REQUEST_SCHEMA,
    runtime_root: runtimeRoot,
    turn_instance_id: "turn-followup-1",
    require_heartbeat_receipt: true,
    before,
    use_current_hint: true,
    host_facts: {
      schema_version: "loopx_scheduler_heartbeat_host_facts_v0",
      operation: "ack",
      ...scope,
      reset_token: "reset-followup",
      identity_signature: "identity-followup",
      progression_index: 0,
      progression_minutes: [15, 30, 60],
      expected_rrule: "FREQ=MINUTELY;INTERVAL=15",
      applied_rrule: "FREQ=MINUTELY;INTERVAL=15",
      cadence_class: "active_work",
      generated_at: "2026-08-27T06:30:00Z",
      execute: true,
      ack_needed: true,
      apply_needed: true,
      source: "quota_scheduler_ack",
      host_match_observed: true,
    },
    ...extra,
  };
}

test("receipt-bound ACK returns the legacy public projection from one native transaction", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  await appendReceipt(runtimeRoot, "turn-followup-1");

  const result = await evaluateSchedulerHeartbeatFollowup(request(runtimeRoot));

  assert.equal(result.schema_version, SCHEDULER_HEARTBEAT_FOLLOWUP_RESULT_SCHEMA);
  assert.equal(result.ok, true);
  assert.equal(result.mode, "scheduler-ack");
  assert.equal(result.scheduler_state_mutated, true);
  assert.equal(result.already_applied, false);
  assert.equal(result.used_current_hint, true);
  assert.equal(result.current_hint_source, "quota.should-run.scheduler_hint");
  assert.deepEqual(result.before, before);
  const commit = result.scheduler_commit as Record<string, unknown>;
  assert.equal(commit.status, "written");
  const event = result.scheduler_ack_event as Record<string, unknown>;
  const state = event.scheduler_state as Record<string, unknown>;
  assert.equal(state.last_applied_rrule, "FREQ=MINUTELY;INTERVAL=15");
  assert.equal("heartbeat_commit" in state, false);

  const replay = await evaluateSchedulerHeartbeatFollowup(request(runtimeRoot, {
    host_facts: {
      ...(request(runtimeRoot).host_facts as Record<string, unknown>),
      generated_at: "2026-08-27T06:31:00Z",
    },
  }));
  assert.equal(replay.ok, true);
  assert.equal((replay.scheduler_commit as Record<string, unknown>).status, "replayed");
});

test("host failure returns the legacy failure event and increments the native cache", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  await appendReceipt(runtimeRoot, "turn-followup-1");
  const base = request(runtimeRoot);
  const hostFacts = base.host_facts as Record<string, unknown>;

  const result = await evaluateSchedulerHeartbeatFollowup({
    ...base,
    host_facts: {
      ...hostFacts,
      operation: "host_failure",
      applied_rrule: "FREQ=MINUTELY;INTERVAL=3",
      observed_host_rrule: "FREQ=MINUTELY;INTERVAL=3",
      failure_kind: "timeout",
      source: "quota_scheduler_host_update_failure",
    },
  });

  assert.equal(result.ok, true);
  assert.equal(result.mode, "scheduler-fail-current");
  assert.equal(result.failed_rrule, "FREQ=MINUTELY;INTERVAL=15");
  assert.equal(result.observed_host_rrule, "FREQ=MINUTELY;INTERVAL=3");
  assert.equal(result.failure_kind, "timeout");
  assert.equal(result.failure_count, 1);
  const event = result.scheduler_failure_event as Record<string, unknown>;
  const failure = event.host_update_failure as Record<string, unknown>;
  assert.equal(failure.failure_count, 1);
});

test("missing or superseded heartbeat receipt rejects before scheduler mutation", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const path = schedulerStatePath(runtimeRoot, {
    goalId: scope.goal_id,
    agentId: scope.agent_id,
    surface: scope.surface,
    stateKey: scope.state_key,
  });

  const missing = await evaluateSchedulerHeartbeatFollowup(request(runtimeRoot));
  assert.equal(missing.ok, false);
  assert.equal(missing.status, "heartbeat_receipt_missing");
  assert.equal(
    missing.error_code,
    "SCHEDULER_FOLLOWUP_HEARTBEAT_RECEIPT_MISSING",
  );
  await assert.rejects(readFile(path, "utf8"), { code: "ENOENT" });

  await appendReceipt(runtimeRoot, "turn-followup-1");
  const seeded = await evaluateSchedulerHeartbeatFollowup(request(runtimeRoot));
  assert.equal(seeded.ok, true);
  const legacyPath = join(
    runtimeRoot,
    "goals",
    scope.goal_id,
    "scheduler-state",
    scope.agent_id,
    scope.surface,
    "8a41f410c67e7c0e.json",
  );
  await mkdir(dirname(legacyPath), { recursive: true });
  await rename(path, legacyPath);
  const held = await acquireFileMutationLock(path);
  let released = false;
  t.after(async () => {
    if (!released) await releaseFileMutationLock(held.targetPath, held.token);
  });
  const inFlight = evaluateSchedulerHeartbeatFollowup(request(runtimeRoot));
  await delay(250);
  await appendReceipt(runtimeRoot, "turn-followup-2");
  released = await releaseFileMutationLock(held.targetPath, held.token);
  const stale = await inFlight;
  assert.equal(stale.ok, false);
  assert.equal(stale.status, "heartbeat_receipt_stale");
  assert.equal(
    stale.error_code,
    "SCHEDULER_FOLLOWUP_HEARTBEAT_RECEIPT_STALE",
  );
  await assert.rejects(readFile(path, "utf8"), { code: "ENOENT" });
  assert.equal(JSON.parse(await readFile(legacyPath, "utf8")).goal_id, scope.goal_id);
});

test("receipt-bound ACK rechecks freshness after waiting for the scheduler lock", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  await appendReceipt(runtimeRoot, "turn-followup-1");
  const path = schedulerStatePath(runtimeRoot, {
    goalId: scope.goal_id,
    agentId: scope.agent_id,
    surface: scope.surface,
    stateKey: scope.state_key,
  });
  const held = await acquireFileMutationLock(path);
  let released = false;
  t.after(async () => {
    if (!released) await releaseFileMutationLock(held.targetPath, held.token);
  });

  const inFlight = evaluateSchedulerHeartbeatFollowup(request(runtimeRoot));
  await delay(250);
  await appendReceipt(runtimeRoot, "turn-followup-2");
  released = await releaseFileMutationLock(held.targetPath, held.token);

  const stale = await inFlight;
  assert.equal(stale.ok, false);
  assert.equal(stale.status, "heartbeat_receipt_stale");
  assert.equal(stale.scheduler_state_mutated, false);
  await assert.rejects(readFile(path, "utf8"), { code: "ENOENT" });
});

test("decision-free compatibility requests can omit a heartbeat receipt", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const result = await evaluateSchedulerHeartbeatFollowup(request(runtimeRoot, {
    turn_instance_id: null,
    require_heartbeat_receipt: false,
  }));

  assert.equal(result.ok, true);
  assert.equal((result.scheduler_commit as Record<string, unknown>).status, "written");
});

test("identity-less quota audits stay visible without superseding a host receipt", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  await appendReceipt(runtimeRoot, "turn-followup-1");
  for (const runId of [undefined, null, "", "   ", 7, {}]) {
    await writeFile(receiptPath(runtimeRoot), `${JSON.stringify({
      schema_version: "loopx_rollout_event_v0",
      event_kind: "quota_should_run",
      goal_id: scope.goal_id,
      agent_id: scope.agent_id,
      run_id: runId,
      status: "skip",
      details: { error_code: "QUOTA_VALIDATION_FAILED" },
    })}\n`, { flag: "a" });
  }
  const snapshot = await readGoalRolloutEventSnapshot(runtimeRoot, scope.goal_id);
  assert.equal(strictGoalRolloutEvents(snapshot).length, 7);
  assert.equal(goalHeartbeatReceiptsFromSnapshot(snapshot, scope.goal_id, scope.agent_id)?.length, 1);
  const first = await evaluateSchedulerHeartbeatFollowup(request(runtimeRoot));
  assert.equal(first.ok, true);
  const statePath = String(first.scheduler_state_path);
  const stateBytes = await readFile(statePath, "utf8");
  const retry = await evaluateSchedulerHeartbeatFollowup(request(runtimeRoot));
  assert.equal(retry.ok, true);
  assert.equal((retry.scheduler_commit as Record<string, unknown>).status, "replayed");
  assert.equal((retry.scheduler_commit as Record<string, unknown>).written, false);
  assert.equal(await readFile(statePath, "utf8"), stateBytes);

  // A real newer Turn remains authoritative even after replay of the old Turn.
  await appendReceipt(runtimeRoot, "turn-followup-2");
  await appendReceipt(runtimeRoot, "turn-followup-1");
  const stale = await evaluateSchedulerHeartbeatFollowup(request(runtimeRoot));
  assert.equal(stale.error_code, "SCHEDULER_FOLLOWUP_HEARTBEAT_RECEIPT_STALE");
  assert.equal(stale.scheduler_state_mutated, false);
});

test("another Agent receipt cannot supersede ACK or host-failure writeback", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  await appendReceipt(runtimeRoot, "turn-followup-1");
  await writeFile(receiptPath(runtimeRoot), `${JSON.stringify({
    schema_version: "loopx_rollout_event_v0",
    event_kind: "quota_should_run",
    goal_id: scope.goal_id,
    agent_id: "other-agent",
    run_id: "other-turn",
  })}\n`, { flag: "a" });
  const base = request(runtimeRoot);
  const acknowledged = await evaluateSchedulerHeartbeatFollowup(base);
  assert.equal(acknowledged.ok, true);
  const failed = await evaluateSchedulerHeartbeatFollowup({
    ...base,
    host_facts: {
      ...(base.host_facts as Record<string, unknown>),
      operation: "host_failure",
      failure_kind: "timeout",
      observed_host_rrule: "FREQ=MINUTELY;INTERVAL=3",
    },
  });
  assert.equal(failed.ok, true);
  assert.equal(failed.failure_count, 1);
});

test("a successful identity-less quota guard supersedes an older host receipt", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  await appendReceipt(runtimeRoot, "turn-followup-1");
  await writeFile(receiptPath(runtimeRoot), `${JSON.stringify({
    schema_version: "loopx_rollout_event_v0",
    event_kind: "quota_should_run",
    goal_id: scope.goal_id,
    agent_id: scope.agent_id,
    status: "normal_run",
    details: { ok: true, should_run: true },
  })}\n`, { flag: "a" });

  const stale = await evaluateSchedulerHeartbeatFollowup(request(runtimeRoot));
  assert.equal(stale.error_code, "SCHEDULER_FOLLOWUP_HEARTBEAT_RECEIPT_STALE");
  assert.equal(stale.scheduler_state_mutated, false);
  assert.equal(stale.write_performed, false);
  assert.equal(stale.appended, false);
});

test("an identity-less quota guard from another Agent does not supersede a receipt", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  await appendReceipt(runtimeRoot, "turn-followup-1");
  await writeFile(receiptPath(runtimeRoot), `${JSON.stringify({
    schema_version: "loopx_rollout_event_v0",
    event_kind: "quota_should_run",
    goal_id: scope.goal_id,
    agent_id: "other-agent",
    status: "normal_run",
    details: { ok: true, should_run: true },
  })}\n`, { flag: "a" });

  const acknowledged = await evaluateSchedulerHeartbeatFollowup(request(runtimeRoot));
  assert.equal(acknowledged.ok, true);
  assert.equal((acknowledged.scheduler_commit as Record<string, unknown>).status, "written");
});

test("a quota audit cannot manufacture a missing heartbeat receipt", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const path = receiptPath(runtimeRoot);
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, `${JSON.stringify({
    schema_version: "loopx_rollout_event_v0",
    event_kind: "quota_should_run",
    goal_id: scope.goal_id,
    agent_id: scope.agent_id,
    status: "skip",
  })}\n`);
  const result = await evaluateSchedulerHeartbeatFollowup(request(runtimeRoot));
  assert.equal(result.error_code, "SCHEDULER_FOLLOWUP_HEARTBEAT_RECEIPT_MISSING");
  assert.equal(result.scheduler_state_mutated, false);
  const statePath = schedulerStatePath(runtimeRoot, {
    goalId: scope.goal_id, agentId: scope.agent_id,
    surface: scope.surface, stateKey: scope.state_key,
  });
  await assert.rejects(readFile(statePath, "utf8"), { code: "ENOENT" });
});
