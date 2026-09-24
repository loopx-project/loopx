import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm, unlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  evaluateQuotaMonitorPollCommit,
  QUOTA_MONITOR_POLL_COMMIT_REQUEST_SCHEMA,
  QUOTA_LEASED_MONITOR_POLL_COMMIT_REQUEST_SCHEMA,
} from "../../loopx/control_plane/quota/monitor_poll_commit.ts";
import { EffectRuntimeRequestError } from "../../loopx/control_plane/effect_runtime_errors.ts";

const goalId = "monitor-native-goal";

function decision(
  extra: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    goal_id: goalId,
    should_run: false,
    normal_delivery_allowed: false,
    recovery_delivery_allowed: false,
    effective_action: "monitor_quiet_skip",
    self_repair_allowed: false,
    capability_repair_allowed: false,
    workspace_repair_allowed: false,
    state: "waiting",
    safe_bypass_allowed: false,
    safe_bypass_kind: null,
    blocked_action_scope: null,
    compute: 1,
    window_hours: 24,
    slot_minutes: 1,
    spent_slots: 0,
    allowed_slots: 10,
    recommended_action: "Watch the public release queue.",
    reason: "No material transition yet.",
    requires_user_action: false,
    agent_id: "codex-main-control",
    heartbeat_recommendation: {
      recommended_mode: "monitor_quiet_until_material_transition",
      reason: "Wait for a public transition.",
    },
    work_lane_contract: {},
    external_evidence_observation: null,
    vision_wait_state: null,
    due_monitor_candidates: [],
    registry_due_monitor: null,
    ...extra,
  };
}

function observation(
  extra: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    actor_agent_id: "codex-main-control",
    reason_summary: null,
    todo_id: null,
    target_key: null,
    result_hash: null,
    material_change: false,
    cadence: null,
    next_due_at: null,
    next_agent_todo: null,
    next_action_kind: null,
    next_task_repository: null,
    next_required_capabilities: [],
    next_continuation_policy: null,
    next_target_key: null,
    next_user_todo: null,
    next_user_task_class: null,
    next_claimed_by: null,
    ...extra,
  };
}

function request(
  extra: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    schema_version: QUOTA_MONITOR_POLL_COMMIT_REQUEST_SCHEMA,
    phase: "event",
    effect_id: "quota-monitor-poll:event-preview",
    runtime_root: null,
    goal_id: goalId,
    source: "heartbeat",
    generated_at: "2026-08-27T12:00:00+08:00",
    execute: false,
    expected_index_digest: null,
    turn_instance_id: null,
    decision: decision(),
    observation: observation(),
    provider_receipt: null,
    status_reload_warning: null,
    ...extra,
  };
}

async function tempRuntime(t: test.TestContext): Promise<string> {
  const runtimeRoot = await mkdtemp(join(tmpdir(), "loopx-monitor-poll-commit-"));
  t.after(() => rm(runtimeRoot, { recursive: true, force: true }));
  return runtimeRoot;
}

test("event phase matches the legacy quiet monitor event", async () => {
  const result = await evaluateQuotaMonitorPollCommit(request());

  assert.equal(result.status, "preview");
  assert.equal(result.record?.classification, "quota_monitor_poll");
  assert.deepEqual(result.record?.monitor_target, {
    schema_version: "quota_monitor_target_v0",
    target_id: "b188e621a4490f64",
    monitor_mode: "monitor_quiet_until_material_transition",
    effective_action: "monitor_quiet_skip",
    action_summary: "Watch the public release queue.",
    agent_id: "codex-main-control",
  });
  const event = result.record?.monitor_event as Record<string, unknown>;
  assert.equal(event.reason_summary, "Wait for a public transition.");
  assert.equal(event.material_change, false);
  assert.equal(result.record?.material_change, false);
  assert.equal(result.record?.health_check, "monitor-only poll unchanged; no quota spend; no material transition");
  assert.equal(result.record?.delivery_outcome, "surface_only");
});

test("event phase preserves material change on the complete run record", async () => {
  const result = await evaluateQuotaMonitorPollCommit(request({
    observation: observation({
      todo_id: "todo_public_monitor",
      result_hash: "approved-42",
      material_change: true,
    }),
  }));

  assert.equal(result.record?.material_change, true);
  assert.equal(
    (result.record?.monitor_event as Record<string, unknown>).material_change,
    true,
  );
});

test("admission revalidates due, external, and exact blocked-wait modes", async () => {
  const due = await evaluateQuotaMonitorPollCommit(request({
    decision: decision({
      should_run: true,
      effective_action: "normal_run",
      work_lane_contract: {
        must_attempt_work: true,
        obligation: "attempt_due_monitor",
      },
      due_monitor_candidates: [{
        todo_id: "todo_public_monitor",
        target_key: "public-release:42",
        task_class: "continuous_monitor",
      }],
    }),
    observation: observation({
      todo_id: "todo_public_monitor",
      target_key: "public-release:42",
      result_hash: "unchanged-42",
    }),
  }));
  assert.equal(
    (due.record?.monitor_event as Record<string, unknown>).monitor_mode,
    "due_monitor_observed_without_material_transition",
  );

  const external = await evaluateQuotaMonitorPollCommit(request({
    decision: decision({
      should_run: true,
      effective_action: "external_evidence_observe",
      heartbeat_recommendation: {},
      work_lane_contract: {
        must_attempt_work: true,
        monitor_policy: "read_only_observation_then_no_spend_if_unchanged",
        reason_codes: ["external_evidence_poll_signal"],
      },
    }),
  }));
  assert.equal(
    (external.record?.monitor_event as Record<string, unknown>).monitor_mode,
    "external_monitor_observed_without_material_transition",
  );

  const blocked = await evaluateQuotaMonitorPollCommit(request({
    decision: decision({
      effective_action: "agent_scope_wait",
      heartbeat_recommendation: {},
      vision_wait_state: {
        schema_version: "goal_vision_wait_state_v0",
        state: "waiting",
        reason_code: "exact_blocked_successor",
        automatic_resume: true,
        agent_id: "codex-main-control",
        selected_todo_id: "todo_blocked_successor",
        resume_when: "todo_done:todo_dependency",
      },
    }),
  }));
  assert.equal(
    (blocked.record?.monitor_event as Record<string, unknown>).monitor_mode,
    "blocked_successor_wait_without_material_transition",
  );

  await assert.rejects(
    () => evaluateQuotaMonitorPollCommit(request({
      decision: decision({
        should_run: true,
        effective_action: "advance",
        heartbeat_recommendation: {},
      }),
    })),
    (error: unknown) => {
      assert.ok(error instanceof EffectRuntimeRequestError);
      assert.equal(error.code, "monitor_poll_admission_rejected");
      return true;
    },
  );
});

test("Todo preflight rejects invalid admission before journaling provider intent", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const effectId = "quota-monitor-poll:rejected-provider";
  await assert.rejects(
    () => evaluateQuotaMonitorPollCommit(request({
      phase: "preflight",
      runtime_root: runtimeRoot,
      execute: true,
      effect_id: effectId,
      decision: decision({
        should_run: true,
        effective_action: "advance",
        heartbeat_recommendation: {},
      }),
      observation: observation({
        todo_id: "todo_unselected_monitor",
        result_hash: "unchanged",
      }),
    })),
    /requires monitor_quiet_skip, due monitor todo, external monitor observation/,
  );

  const receiptPath = join(
    runtimeRoot,
    "goals",
    goalId,
    "runs",
    ".transactions",
    "quota-monitor-poll",
    `${createHash("sha256").update(effectId).digest("hex").slice(0, 24)}.json`,
  );
  await assert.rejects(() => readFile(receiptPath), { code: "ENOENT" });
});

test("commit owns the repairable run artifacts and exact-effect replay", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const params = request({
    phase: "commit",
    runtime_root: runtimeRoot,
    execute: true,
    effect_id: "quota-monitor-poll:quiet-turn",
    turn_instance_id: "quiet-turn",
  });

  const written = await evaluateQuotaMonitorPollCommit(params);
  assert.equal(written.status, "written");
  assert.equal(written.payload.appended, true);
  assert.deepEqual(written.payload.turn_continuation, {
    schema_version: "quota_turn_continuation_v0",
    settlement_binding_matches_observation: null,
    current_turn_settled: false,
    same_turn_independent_settlement_allowed: false,
    next_turn_required: false,
    next_action: "obtain a typed settlement binding before claiming this Turn settled",
    reason: "the committed monitor-poll has no exact Todo settlement binding",
  });
  const jsonPath = String(written.payload.json_path);
  const markdownPath = String(written.payload.markdown_path);
  const indexPath = String(written.payload.index_path);
  assert.equal(
    JSON.parse(await readFile(jsonPath, "utf8")).classification,
    "quota_monitor_poll",
  );
  assert.match(await readFile(markdownPath, "utf8"), /LoopX Quota Monitor Poll/);
  assert.equal((await readFile(indexPath, "utf8")).trim().split("\n").length, 1);
  const receiptPath = join(
    runtimeRoot,
    "goals",
    goalId,
    "runs",
    ".transactions",
    "quota-monitor-poll",
    `${createHash("sha256").update(String(params.effect_id)).digest("hex").slice(0, 24)}.json`,
  );
  const preparedReceipt = JSON.parse(await readFile(receiptPath, "utf8"));
  assert.equal(preparedReceipt.status, "prepared");
  await assert.rejects(() => readFile(`${receiptPath}.committed`), { code: "ENOENT" });

  const replayed = await evaluateQuotaMonitorPollCommit({
    ...params,
    decision: decision({
      should_run: true,
      effective_action: "normal_run",
      work_lane_contract: { must_attempt_work: false },
    }),
  });
  assert.equal(replayed.status, "replayed");
  assert.equal(replayed.payload.appended, false);
  assert.equal(JSON.parse(await readFile(receiptPath, "utf8")).status, "prepared");

  await Promise.all([unlink(jsonPath), unlink(markdownPath), unlink(indexPath)]);
  const repairedFromPrepared = await evaluateQuotaMonitorPollCommit(params);
  assert.equal(repairedFromPrepared.status, "repaired");
  assert.equal(repairedFromPrepared.payload.transaction_repaired, true);
  assert.equal(
    repairedFromPrepared.reason,
    "quota monitor-poll commit repaired its durable transaction artifacts",
  );
  assert.equal((await readFile(indexPath, "utf8")).trim().split("\n").length, 1);
  assert.equal(JSON.parse(await readFile(receiptPath, "utf8")).status, "prepared");
  await assert.rejects(() => readFile(`${receiptPath}.committed`), { code: "ENOENT" });

  const committedReceipt = { ...preparedReceipt, status: "committed" };
  await Promise.all([
    writeFile(receiptPath, `${JSON.stringify(preparedReceipt, null, 2)}\n`, "utf8"),
    writeFile(`${receiptPath}.committed`, JSON.stringify(committedReceipt), "utf8"),
  ]);
  await Promise.all([unlink(markdownPath), unlink(indexPath)]);
  const repaired = await evaluateQuotaMonitorPollCommit(params);
  assert.equal(repaired.status, "repaired");
  assert.equal(repaired.payload.transaction_repaired, true);
  assert.equal((await readFile(indexPath, "utf8")).trim().split("\n").length, 1);
  assert.equal(JSON.parse(await readFile(receiptPath, "utf8")).status, "prepared");
  await assert.rejects(() => readFile(`${receiptPath}.committed`), { code: "ENOENT" });

  await writeFile(receiptPath, `${JSON.stringify(committedReceipt)}\n`, "utf8");
  await Promise.all([unlink(jsonPath), unlink(markdownPath), unlink(indexPath)]);
  const repairedFromCommitted = await evaluateQuotaMonitorPollCommit(params);
  assert.equal(repairedFromCommitted.status, "repaired");
  assert.equal(repairedFromCommitted.payload.transaction_repaired, true);
  assert.equal((await readFile(indexPath, "utf8")).trim().split("\n").length, 1);
  assert.equal(JSON.parse(await readFile(receiptPath, "utf8")).status, "committed");

  const conflict = await evaluateQuotaMonitorPollCommit({ ...params, source: "controller" });
  assert.equal(conflict.status, "conflict");
  assert.equal(conflict.reason_code, "effect_id_conflict");

  const observationConflict = await evaluateQuotaMonitorPollCommit({
    ...params,
    observation: observation({ result_hash: "different-observation" }),
  });
  assert.equal(observationConflict.status, "conflict");
  assert.deepEqual(observationConflict.conflict_fields, ["result_hash"]);
});

test("Todo commit journals provider intent before writeback and resumes exactly once", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const params = request({
    phase: "preflight",
    runtime_root: runtimeRoot,
    execute: true,
    effect_id: "quota-monitor-poll:todo-turn",
    turn_instance_id: "todo-turn",
    observation: observation({
      todo_id: "todo_public_monitor",
      target_key: "public-release:42",
      result_hash: "unchanged-42",
      cadence: "30m",
      next_due_at: "2026-08-27T12:30:00+08:00",
    }),
  });

  const preflight = await evaluateQuotaMonitorPollCommit(params);
  assert.equal(preflight.status, "provider_required");
  assert.deepEqual(preflight.provider_plan, {
    schema_version: "monitor_poll_todo_provider_plan_v0",
    monitor_effect_id: "quota-monitor-poll:todo-turn",
    goal_id: goalId,
    generated_at: "2026-08-27T12:00:00+08:00",
    execute: true,
    todo_id: "todo_public_monitor",
    target_key: "public-release:42",
    result_hash: "unchanged-42",
    material_change: false,
    cadence: "30m",
    next_due_at: "2026-08-27T12:30:00+08:00",
    reason_summary: null,
    next_agent_todo: null,
    next_action_kind: null,
    next_task_repository: null,
    next_required_capabilities: [],
    next_continuation_policy: null,
    next_target_key: null,
    next_user_todo: null,
    next_user_task_class: null,
    next_claimed_by: null,
    agent_id: "codex-main-control",
  });

  const repeated = await evaluateQuotaMonitorPollCommit({
    ...params,
    generated_at: "2026-08-27T12:01:00+08:00",
  });
  assert.equal(repeated.status, "provider_required");
  assert.deepEqual(repeated.provider_plan, preflight.provider_plan);

  const providerReceipt = {
    schema_version: "monitor_poll_todo_writeback_v0",
    monitor_effect_id: "quota-monitor-poll:todo-turn",
    dry_run: false,
    goal_id: goalId,
    todo_id: "todo_public_monitor",
    target_key: "public-release:42",
    result_hash: "unchanged-42",
    material_change: false,
    material_change_generation: 3,
    consecutive_no_change: 2,
    last_checked_at: "2026-08-27T12:00:00+08:00",
    next_due_at: "2026-08-27T12:30:00+08:00",
    cadence: "30m",
    todo_update: { ok: true },
    next_todos: [],
    successor_receipts: [],
  };
  const written = await evaluateQuotaMonitorPollCommit({
    ...params,
    phase: "commit",
    generated_at: "2026-08-27T12:02:00+08:00",
    provider_receipt: providerReceipt,
  });
  assert.equal(written.status, "written");
  assert.equal(written.record?.generated_at, "2026-08-27T12:00:00+08:00");
  assert.equal(
    (written.payload.todo_writeback as Record<string, unknown>).monitor_effect_id,
    undefined,
  );
  assert.deepEqual(
    (written.record?.monitor_event as Record<string, unknown>).todo_writeback,
    {
      schema_version: "monitor_poll_todo_writeback_v0",
      dry_run: false,
      goal_id: goalId,
      todo_id: "todo_public_monitor",
      target_key: "public-release:42",
      result_hash: "unchanged-42",
      material_change: false,
      consecutive_no_change: 2,
      last_checked_at: "2026-08-27T12:00:00+08:00",
      next_due_at: "2026-08-27T12:30:00+08:00",
      cadence: "30m",
      successor_receipts: [],
    },
  );

  const replayed = await evaluateQuotaMonitorPollCommit(params);
  assert.equal(replayed.status, "replayed");
  assert.equal(replayed.provider_plan, null);
  assert.equal(replayed.payload.todo_writeback, null);

  await unlink(String(written.payload.markdown_path));
  const repaired = await evaluateQuotaMonitorPollCommit(params);
  assert.equal(repaired.status, "repaired");
  assert.equal(repaired.provider_plan, null);
});

test("Todo commit rejects a provider receipt from another effect", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const params = request({
    phase: "preflight",
    runtime_root: runtimeRoot,
    execute: true,
    effect_id: "quota-monitor-poll:receipt-binding",
    observation: observation({
      todo_id: "todo_public_monitor",
      result_hash: "unchanged-42",
      next_due_at: "2026-08-27T12:30:00+08:00",
    }),
  });
  await evaluateQuotaMonitorPollCommit(params);

  await assert.rejects(
    () => evaluateQuotaMonitorPollCommit({
      ...params,
      phase: "commit",
      provider_receipt: {
        schema_version: "monitor_poll_todo_writeback_v0",
        monitor_effect_id: "quota-monitor-poll:another-effect",
        dry_run: false,
        goal_id: goalId,
        todo_id: "todo_public_monitor",
        target_key: null,
        result_hash: "unchanged-42",
        material_change: false,
        material_change_generation: 0,
        consecutive_no_change: 1,
        last_checked_at: "2026-08-27T12:00:00+08:00",
        next_due_at: "2026-08-27T12:30:00+08:00",
        cadence: null,
        todo_update: {},
        next_todos: [],
        successor_receipts: [],
      },
    }),
    /monitor_effect_id must match provider plan/,
  );
});

test("Todo commit fences the full material successor receipt", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const params = request({
    phase: "preflight",
    runtime_root: runtimeRoot,
    execute: true,
    effect_id: "quota-monitor-poll:successor-binding",
    observation: observation({
      todo_id: "todo_public_monitor",
      target_key: "public-release:42",
      result_hash: "approved-42",
      material_change: true,
      next_agent_todo: "Advance the approved release.",
      next_action_kind: "ADVANCE_RELEASE",
      next_task_repository: "https://github.com/owner/repo.git",
      next_required_capabilities: ["filesystem-write"],
      next_continuation_policy: "SAME_AGENT_NON_DELIVERY",
      next_target_key: "public-release:42:advance",
      next_claimed_by: "Codex Main Control",
    }),
  });
  await evaluateQuotaMonitorPollCommit(params);

  const successor = {
    todo_id: "todo_successor001",
    role: "agent",
    task_class: "advancement_task",
    action_kind: "advance_release",
    task_repository: "git:github.com/owner/repo",
    continuation_policy: "same_agent_non_delivery",
    required_capabilities: ["filesystem_write"],
    claimed_by: "codex-main-control",
    unblocks_todo_id: "todo_public_monitor",
    target_key: "public-release:42:advance",
  };
  const providerReceipt = {
    schema_version: "monitor_poll_todo_writeback_v0",
    monitor_effect_id: "quota-monitor-poll:successor-binding",
    dry_run: false,
    goal_id: goalId,
    todo_id: "todo_public_monitor",
    target_key: "public-release:42",
    result_hash: "approved-42",
    material_change: true,
    material_change_generation: 4,
    consecutive_no_change: 0,
    last_checked_at: "2026-08-27T12:00:00+08:00",
    next_due_at: null,
    cadence: null,
    todo_update: { ok: true },
    next_todos: [{
      ...successor,
      todo: "Advance the approved release.",
    }],
    successor_receipts: [successor],
  };

  await assert.rejects(
    () => evaluateQuotaMonitorPollCommit({
      ...params,
      phase: "commit",
      provider_receipt: {
        ...providerReceipt,
        next_todos: [{
          ...successor,
          todo: "Advance a different release.",
        }],
      },
    }),
    /agent next_todo text must match provider plan/,
  );

  await assert.rejects(
    () => evaluateQuotaMonitorPollCommit({
      ...params,
      phase: "commit",
      provider_receipt: {
        ...providerReceipt,
        next_todos: [{
          ...successor,
          todo: "Advance the approved release.",
          target_key: "public-release:42:other",
        }],
      },
    }),
    /agent next_todo target_key must match provider plan/,
  );

  const written = await evaluateQuotaMonitorPollCommit({
    ...params,
    phase: "commit",
    provider_receipt: providerReceipt,
  });
  assert.equal(written.status, "written");
  assert.deepEqual(
    (written.payload.todo_writeback as Record<string, unknown>).successor_receipts,
    [successor],
  );
  assert.equal((await evaluateQuotaMonitorPollCommit({...params, phase: "commit", provider_receipt: providerReceipt})).status, "replayed");
});

test("successor normalization preserves the legacy pending observation fingerprint", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const params = request({phase: "preflight", runtime_root: runtimeRoot, execute: true,
    effect_id: "quota-monitor-poll:legacy-route-fingerprint",
    observation: observation({todo_id: "todo_public_monitor", result_hash: "revision-a",
      material_change: true, next_agent_todo: "Validate.", next_action_kind: "VALIDATE",
      next_task_repository: "git@github.com:example/repo.git", next_required_capabilities: ["file--write"],
      next_continuation_policy: "SAME_AGENT_NON_DELIVERY", next_claimed_by: "Agent A"})});
  // The shipped v0 identity recipe hashes wire observation, not its normalized route.
  const legacyEnvelope = Object.fromEntries(["schema_version", "effect_id", "runtime_root", "goal_id",
    "source", "turn_instance_id", "observation"].map(key => [key, params[key]]));
  const oracle = spawnSync("python", ["-c", "import hashlib,json,sys; print('sha256:'+hashlib.sha256(json.dumps(json.load(sys.stdin),ensure_ascii=False,sort_keys=True).encode()).hexdigest())"],
    {input: JSON.stringify(legacyEnvelope), encoding: "utf8"});
  assert.equal(oracle.status, 0, oracle.stderr);
  const first = await evaluateQuotaMonitorPollCommit(params);
  assert.equal(first.status, "provider_required");
  assert.equal(first.request_digest, oracle.stdout.trim());
  assert.equal(first.provider_plan?.next_action_kind, "VALIDATE");
  const retry = await evaluateQuotaMonitorPollCommit({...params, generated_at: "2026-09-10T12:00:00Z"});
  assert.equal(retry.status, "provider_required");
  assert.equal(retry.request_digest, first.request_digest);
  assert.deepEqual(retry.provider_plan, first.provider_plan);
  const conflict = await evaluateQuotaMonitorPollCommit({...params,
    observation: {...params.observation as object, next_task_repository: "git:github.com/example/other"}});
  assert.equal(conflict.status, "conflict");
  assert.equal(conflict.written, false);
});

test("invalid successor routes fail before a pending provider effect is saved", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const effectId = "quota-monitor-poll:invalid-route";
  for (const invalid of [{next_required_capabilities: ["file_write", "bad/token"]},
    {next_claimed_by: "bad/actor"}, {next_continuation_policy: "primary_review"},
    {next_task_repository: "https://example.invalid/project/../other"}]) {
    await assert.rejects(() => evaluateQuotaMonitorPollCommit(request({phase: "preflight",
      runtime_root: runtimeRoot, execute: true, effect_id: effectId,
      observation: observation({todo_id: "todo_public_monitor", result_hash: "revision-a",
        material_change: true, next_agent_todo: "Validate.", next_action_kind: "validate", ...invalid})})));
  }
  const path = join(runtimeRoot, "goals", goalId, "runs", ".transactions", "quota-monitor-poll",
    `${createHash("sha256").update(effectId).digest("hex").slice(0, 24)}.json`);
  await assert.rejects(() => readFile(path), {code: "ENOENT"});
});

test("Todo commit rejects injected defaults in the material successor route", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const params = request({
    phase: "preflight",
    runtime_root: runtimeRoot,
    execute: true,
    effect_id: "quota-monitor-poll:successor-default-binding",
    observation: observation({
      todo_id: "todo_public_monitor",
      result_hash: "approved-default",
      material_change: true,
      next_agent_todo: "Advance the approved release.",
      next_action_kind: "advance_release",
    }),
  });
  await evaluateQuotaMonitorPollCommit(params);
  const derivedTarget = `monitor-successor:todo_public_monitor:${createHash("sha256")
    .update("approved-default", "utf8").digest("hex").slice(0, 16)}`;
  const successor = {
    todo_id: "todo_successor002",
    role: "agent",
    task_class: "advancement_task",
    action_kind: "advance_release",
    continuation_policy: "independent_handoff",
    unblocks_todo_id: "todo_public_monitor",
    target_key: derivedTarget,
  };
  const providerReceipt = {
    schema_version: "monitor_poll_todo_writeback_v0",
    monitor_effect_id: "quota-monitor-poll:successor-default-binding",
    dry_run: false,
    goal_id: goalId,
    todo_id: "todo_public_monitor",
    target_key: null,
    result_hash: "approved-default",
    material_change: true,
    material_change_generation: 1,
    consecutive_no_change: 0,
    last_checked_at: "2026-08-27T12:00:00+08:00",
    next_due_at: null,
    cadence: null,
    todo_update: {},
    next_todos: [{ ...successor, todo: "Advance the approved release." }],
    successor_receipts: [successor],
  };

  for (const mutation of [
    { task_repository: "git:github.com/attacker/repo" },
    { required_capabilities: ["network_write"] },
    { continuation_policy: "same_agent_non_delivery" },
    { claimed_by: "unplanned-agent" },
    { target_key: "injected-target" },
  ]) {
    await assert.rejects(
      () => evaluateQuotaMonitorPollCommit({
        ...params,
        phase: "commit",
        provider_receipt: {
          ...providerReceipt,
          successor_receipts: [{ ...successor, ...mutation }],
          next_todos: [{
            ...successor,
            ...mutation,
            todo: "Advance the approved release.",
          }],
        },
      }),
      /must match provider plan/,
    );
  }
});

test("native commit serializes same-effect replay and distinct-effect CAS", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const same = request({
    phase: "commit",
    runtime_root: runtimeRoot,
    execute: true,
    effect_id: "quota-monitor-poll:concurrent-same",
  });
  const sameResults = await Promise.all([
    evaluateQuotaMonitorPollCommit(same),
    evaluateQuotaMonitorPollCommit(same),
  ]);
  assert.deepEqual(
    sameResults.map((result) => result.status).sort(),
    ["replayed", "written"],
  );

  const expectedDigest = sameResults[0].index_digest;
  const distinct = await Promise.all([
    evaluateQuotaMonitorPollCommit(request({
      phase: "commit",
      runtime_root: runtimeRoot,
      execute: true,
      expected_index_digest: expectedDigest,
      effect_id: "quota-monitor-poll:concurrent-left",
    })),
    evaluateQuotaMonitorPollCommit(request({
      phase: "commit",
      runtime_root: runtimeRoot,
      execute: true,
      expected_index_digest: expectedDigest,
      effect_id: "quota-monitor-poll:concurrent-right",
    })),
  ]);
  assert.deepEqual(
    distinct.map((result) => result.status).sort(),
    ["conflict", "written"],
  );
  assert.equal(
    distinct.find((result) => result.status === "conflict")?.reason_code,
    "index_digest_conflict",
  );
});

test("retry repairs a truncated owned index tail and rejects artifact drift", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const params = request({
    phase: "commit",
    runtime_root: runtimeRoot,
    execute: true,
    effect_id: "quota-monitor-poll:repair-tail",
  });
  const written = await evaluateQuotaMonitorPollCommit(params);
  const indexPath = String(written.payload.index_path);
  await Promise.all([
    unlink(String(written.payload.json_path)),
    unlink(String(written.payload.markdown_path)),
  ]);

  const repairedArtifacts = await evaluateQuotaMonitorPollCommit(params);
  assert.equal(repairedArtifacts.status, "repaired");
  assert.equal(
    JSON.parse(await readFile(String(written.payload.json_path), "utf8")).classification,
    "quota_monitor_poll",
  );
  assert.match(
    await readFile(String(written.payload.markdown_path), "utf8"),
    /LoopX Quota Monitor Poll/,
  );

  const index = await readFile(indexPath);
  await writeFile(indexPath, index.subarray(0, index.length - 7));

  const repaired = await evaluateQuotaMonitorPollCommit(params);
  assert.equal(repaired.status, "repaired");
  assert.equal((await readFile(indexPath, "utf8")).trim().split("\n").length, 1);

  await writeFile(String(written.payload.json_path), "{}\n", "utf8");
  await assert.rejects(
    () => evaluateQuotaMonitorPollCommit(params),
    /JSON artifact conflicts with its transaction receipt/,
  );
});

test("pending provider effect settles after an unrelated append-only index commit", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const pending = request({
    phase: "preflight",
    runtime_root: runtimeRoot,
    execute: true,
    effect_id: "quota-monitor-poll:pending-provider",
    observation: observation({
      todo_id: "todo_public_monitor",
      result_hash: "unchanged-42",
    }),
  });
  assert.equal(
    (await evaluateQuotaMonitorPollCommit(pending)).status,
    "provider_required",
  );
  assert.equal(
    (await evaluateQuotaMonitorPollCommit(request({
      phase: "commit",
      runtime_root: runtimeRoot,
      execute: true,
      effect_id: "quota-monitor-poll:newer-index-effect",
    }))).status,
    "written",
  );

  const retry = await evaluateQuotaMonitorPollCommit(pending);
  assert.equal(retry.status, "provider_required");
  const receipt = {
    schema_version: "monitor_poll_todo_writeback_v0",
    monitor_effect_id: "quota-monitor-poll:pending-provider",
    dry_run: false,
    goal_id: goalId,
    todo_id: "todo_public_monitor",
    target_key: null,
    result_hash: "unchanged-42",
    material_change: false,
    material_change_generation: 0,
    consecutive_no_change: 1,
    last_checked_at: pending.generated_at,
    next_due_at: null,
    cadence: null,
    todo_update: {ok: true},
    next_todos: [],
    successor_receipts: [],
  };
  const settled = await evaluateQuotaMonitorPollCommit({
    ...pending, phase: "commit", provider_receipt: receipt,
  });
  assert.equal(settled.status, "written");
  assert.equal((await evaluateQuotaMonitorPollCommit({
    ...pending, phase: "commit", provider_receipt: receipt,
  })).status, "replayed");
  const indexPath = join(runtimeRoot, "goals", goalId, "runs", "index.jsonl");
  assert.equal((await readFile(indexPath, "utf8")).trim().split("\n").length, 2);
});

test("pending provider effect still rejects changed preflight index history", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const prior = request({phase: "commit", runtime_root: runtimeRoot, execute: true,
    effect_id: "quota-monitor-poll:prior-history"});
  const initial = await evaluateQuotaMonitorPollCommit(prior);
  assert.equal(initial.status, "written");
  const pending = request({phase: "preflight", runtime_root: runtimeRoot, execute: true,
    expected_index_digest: initial.index_digest,
    effect_id: "quota-monitor-poll:pending-history",
    observation: observation({todo_id: "todo_public_monitor", result_hash: "unchanged-42"})});
  assert.equal((await evaluateQuotaMonitorPollCommit(pending)).status, "provider_required");
  const indexPath = join(runtimeRoot, "goals", goalId, "runs", "index.jsonl");
  const original = await readFile(indexPath, "utf8");
  assert.match(original, /quota-monitor-poll:prior-history/);
  await writeFile(indexPath, original.replaceAll("quota-monitor-poll:prior-history", "quota-monitor-poll:alter-history"));
  const retry = await evaluateQuotaMonitorPollCommit(pending);
  assert.equal(retry.status, "conflict");
  assert.equal(retry.reason_code, "index_digest_conflict");
});

test("durable replay rejects receipt path escape and lost pre-existing index history", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const effectId = "quota-monitor-poll:receipt-path-fence";
  const params = request({
    phase: "commit",
    runtime_root: runtimeRoot,
    execute: true,
    effect_id: effectId,
  });
  const indexPath = join(runtimeRoot, "goals", goalId, "runs", "index.jsonl");
  await mkdir(join(runtimeRoot, "goals", goalId, "runs"), { recursive: true });
  await writeFile(indexPath, '{"classification":"existing-run"}\n', "utf8");
  params.expected_index_digest = `sha256:${createHash("sha256")
    .update(await readFile(indexPath))
    .digest("hex")}`;
  const written = await evaluateQuotaMonitorPollCommit(params);
  await unlink(indexPath);
  await assert.rejects(
    () => evaluateQuotaMonitorPollCommit(params),
    /index history conflicts with its transaction receipt/,
  );

  const receiptPath = join(
    runtimeRoot,
    "goals",
    goalId,
    "runs",
    ".transactions",
    "quota-monitor-poll",
    `${createHash("sha256").update(effectId).digest("hex").slice(0, 24)}.json`,
  );
  const receipt = JSON.parse(await readFile(receiptPath, "utf8"));
  const escapedPath = join(runtimeRoot, "escaped-monitor-artifact.json");
  receipt.json_path = escapedPath;
  await writeFile(receiptPath, `${JSON.stringify(receipt)}\n`, "utf8");
  await assert.rejects(
    () => evaluateQuotaMonitorPollCommit(params),
    /artifact paths do not match the transaction scope/,
  );
});

test("leased Monitor pending settlement binds the original proof and rejects missing or substituted receipts", async t => {
  const runtime = await tempRuntime(t);
  const proof = {idempotency_key: "monitor-execution", expected_version: 3};
  const params = request({schema_version: QUOTA_LEASED_MONITOR_POLL_COMMIT_REQUEST_SCHEMA,
    phase: "preflight", runtime_root: runtime, execute: true, effect_id: "leased-poll",
    observation: observation({todo_id: "todo_public_monitor", result_hash: "observed-a", lease_proof: proof})});
  await assert.rejects(evaluateQuotaMonitorPollCommit({...params,
    schema_version: QUOTA_MONITOR_POLL_COMMIT_REQUEST_SCHEMA}), /requires request v1/);
  const preflight = await evaluateQuotaMonitorPollCommit(params);
  assert.equal(preflight.status, "provider_required");
  assert.deepEqual(preflight.provider_plan?.lease_proof, proof);
  assert.equal(preflight.provider_plan?.schema_version, "monitor_poll_todo_provider_plan_v1");
  const receipt = {schema_version: "monitor_poll_todo_writeback_v0", monitor_effect_id: "leased-poll",
    goal_id: goalId, todo_id: "todo_public_monitor", dry_run: false, target_key: null,
    result_hash: "observed-a", material_change: false, material_change_generation: 0,
    consecutive_no_change: 1, last_checked_at: params.generated_at, next_due_at: null, cadence: null,
    todo_update: {ok: true}, next_todos: [], successor_receipts: [], lease_proof: proof};
  for (const invalid of [undefined, {...proof, expected_version: 4}, {...proof, idempotency_key: "another"}]) {
    await assert.rejects(evaluateQuotaMonitorPollCommit({...params, phase: "commit",
      provider_receipt: {...receipt, lease_proof: invalid}}), /lease_proof/);
  }
  const changedDecision = decision({effective_action: "normal_delivery", heartbeat_recommendation: {},
    recommended_action: "Work on the new successor", reason: "Monitor is no longer due"});
  const retry = {...params, decision: changedDecision, generated_at: "2099-01-01T00:00:00Z"};
  const replay = await evaluateQuotaMonitorPollCommit(retry);
  assert.deepEqual(replay.provider_plan, preflight.provider_plan);
  const written = await evaluateQuotaMonitorPollCommit({...retry, phase: "commit", provider_receipt: receipt});
  assert.equal(written.status, "written");
  assert.equal(written.record?.recommended_action, "Watch the public release queue.");
  assert.equal(((written.record?.monitor_event as Record<string, unknown>).before as Record<string, unknown>).effective_action,
    "monitor_quiet_skip");
  assert.deepEqual((written.payload.todo_writeback as Record<string, unknown>).lease_proof, proof);
  assert.equal((await evaluateQuotaMonitorPollCommit({...params, phase: "commit", provider_receipt: receipt})).status, "replayed");
  const changed = await evaluateQuotaMonitorPollCommit({...params,
    observation: observation({todo_id: "todo_public_monitor", result_hash: "observed-a", lease_proof: {...proof, expected_version: 4}})});
  assert.equal(changed.status, "conflict");
});

test("pending admission is scoped, mandatory in v1, and preserves bounded v0 recovery", async t => {
  const runtime = await tempRuntime(t);
  const effect = "pending-admission-compatibility";
  const params = request({phase: "preflight", runtime_root: runtime, execute: true, effect_id: effect,
    observation: observation({todo_id: "todo_public_monitor", result_hash: "observed-a"})});
  const preflight = await evaluateQuotaMonitorPollCommit(params);
  const path = join(runtime, "goals", goalId, "runs", ".transactions", "quota-monitor-poll",
    `${createHash("sha256").update(effect).digest("hex").slice(0, 24)}.json`);
  const pending = JSON.parse(await readFile(path, "utf8"));
  for (const admitted of [null, {...pending.admitted_decision, goal_id: "another-goal"},
    {...pending.admitted_decision, agent_id: "another-agent"},
    {...pending.admitted_decision, effective_action: "normal_delivery", heartbeat_recommendation: {}}]) {
    const bytes = JSON.stringify({...pending, admitted_decision: admitted});
    await writeFile(path, bytes);
    await assert.rejects(evaluateQuotaMonitorPollCommit(params), EffectRuntimeRequestError);
    assert.equal(await readFile(path, "utf8"), bytes);
  }
  const legacy = {...pending, schema_version: "quota_monitor_poll_commit_receipt_v0"};
  delete legacy.admitted_decision;
  await writeFile(path, JSON.stringify(legacy));
  assert.deepEqual((await evaluateQuotaMonitorPollCommit(params)).provider_plan, preflight.provider_plan);
  await assert.rejects(evaluateQuotaMonitorPollCommit({...params,
    decision: decision({effective_action: "normal_delivery", heartbeat_recommendation: {}})}),
  (error: unknown) => error instanceof EffectRuntimeRequestError && error.code === "legacy_monitor_admission_unavailable");
  assert.deepEqual(JSON.parse(await readFile(path, "utf8")), legacy);
  const receipt = {schema_version: "monitor_poll_todo_writeback_v0", monitor_effect_id: effect,
    goal_id: goalId, todo_id: "todo_public_monitor", dry_run: false, target_key: null,
    result_hash: "observed-a", material_change: false, material_change_generation: 0,
    consecutive_no_change: 1, last_checked_at: params.generated_at, next_due_at: null, cadence: null,
    todo_update: {}, next_todos: [], successor_receipts: []};
  assert.equal((await evaluateQuotaMonitorPollCommit({...params, phase: "commit", provider_receipt: receipt})).status, "written");
});

test("lease proof and schema must agree before any provider intent is journaled", async t => {
  const runtime = await tempRuntime(t);
  const params = request({schema_version: QUOTA_LEASED_MONITOR_POLL_COMMIT_REQUEST_SCHEMA,
    phase: "preflight", runtime_root: runtime, execute: true, effect_id: "invalid-proof"});
  for (const proof of [undefined, {}, {idempotency_key: " x", expected_version: 1},
    {idempotency_key: "x", expected_version: 0}, {idempotency_key: "x", expected_version: 1.5},
    {idempotency_key: "x", expected_version: Number.MAX_SAFE_INTEGER + 1},
    {idempotency_key: "x", expected_version: 1, owner: "injected"}]) {
    await assert.rejects(evaluateQuotaMonitorPollCommit({...params,
      observation: observation({todo_id: "todo_public_monitor", result_hash: "a", lease_proof: proof})}));
  }
  await assert.rejects(readFile(join(runtime, "goals", goalId, "runs", "index.jsonl")), {code: "ENOENT"});
});
