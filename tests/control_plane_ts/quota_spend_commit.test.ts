import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  readFile,
  mkdir,
  mkdtemp,
  readdir,
  rm,
  unlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";

import {
  acquireFileMutationLock,
  releaseFileMutationLock,
} from "../../loopx/control_plane/effect_runtime_io.ts";
import {
  EffectRuntimeConflictError,
  EffectRuntimeLockTimeoutError,
  EffectRuntimeRequestError,
} from "../../loopx/control_plane/effect_runtime_errors.ts";
import { requireJsonObject } from "../../loopx/control_plane/runtime_decode.ts";
import {
  parseQuotaAccountingOwner,
  withQuotaAccountingOwner,
} from "../../loopx/control_plane/quota/source_admission.ts";
import {
  evaluateQuotaSpendCommit,
  quotaSpendIndexDigest,
  QUOTA_SPEND_COMMIT_REQUEST_SCHEMA,
} from "../../loopx/control_plane/quota/spend_commit.ts";

const goalId = "quota-spend-transaction";
const instanceA = "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const instanceB = "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

interface TestSourceAdmission extends Record<string, unknown> {
  locks: [Record<string, unknown>, Record<string, unknown>];
}

function decision(
  spentSlots: number,
  extra: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    should_run: true,
    normal_delivery_allowed: true,
    recovery_delivery_allowed: false,
    effective_action: "advance",
    self_repair_allowed: false,
    capability_repair_allowed: false,
    workspace_repair_allowed: false,
    state: "eligible",
    safe_bypass_allowed: false,
    safe_bypass_kind: null,
    blocked_action_scope: null,
    compute: 1,
    window_hours: 24,
    slot_minutes: 1,
    spent_slots: spentSlots,
    allowed_slots: 1440,
    ...extra,
  };
}

function preview(extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    ok: true,
    mode: "spend-slot",
    dry_run: true,
    goal_id: goalId,
    slots: 1,
    agent_id: "codex-main-control",
    appended: false,
    registry_mutated: false,
    before: decision(0),
    after: decision(1),
    after_recommended_action: "inspect next quota should-run decision",
    would_throttle: false,
    delivery_completion_spend: false,
    safe_bypass_spend: false,
    delivery_workspace_validated: false,
    ...extra,
  };
}

function request(
  runtimeRoot: string | null,
  extra: Record<string, unknown> = {},
): Record<string, unknown> {
  const value = preview();
  return {
    schema_version: QUOTA_SPEND_COMMIT_REQUEST_SCHEMA,
    effect_id: "quota-spend-effect-1",
    runtime_root: runtimeRoot,
    goal_id: goalId,
    source: "heartbeat",
    generated_at: "2026-08-25T12:00:00+08:00",
    execute: runtimeRoot !== null,
    expected_index_digest: null,
    preview: value,
    before: value.before,
    after: value.after,
    resolved_agent_id: "codex-main-control",
    ...extra,
  };
}

async function tempRuntime(t: test.TestContext): Promise<string> {
  const runtimeRoot = await mkdtemp(join(tmpdir(), "loopx-quota-spend-commit-"));
  t.after(() => rm(runtimeRoot, { recursive: true, force: true }));
  return runtimeRoot;
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
  mutateAdmission?: (admission: TestSourceAdmission) => void,
  ownerPid = process.pid,
): Promise<T> {
  const indexPath = join(runtimeRoot, "goals", goalId, "runs", "index.jsonl");
  const registryPath = join(runtimeRoot, "project", ".loopx", "registry.json");
  const guardPath = sourceGuardPath(registryPath);
  const indexLock = await acquireFileMutationLock(indexPath, ownerPid);
  const guardLock = await acquireFileMutationLock(guardPath, ownerPid);
  const admission: TestSourceAdmission = {
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
        pid: ownerPid,
        token: indexLock.token,
      },
      {
        role: "source_guard",
        target: guardPath,
        pid: ownerPid,
        token: guardLock.token,
      },
    ],
  };
  mutateAdmission?.(admission);
  try {
    return await run({
      goal_ref: {
        goal_id: goalId,
        goal_instance_id: plannedInstanceId,
      },
      source_admission: admission,
    });
  } finally {
    await releaseFileMutationLock(guardPath, guardLock.token, null, true);
    await releaseFileMutationLock(indexPath, indexLock.token, null, true);
  }
}

function replayRequest(runtimeRoot: string, effectId: string) {
  return {
    schema_version: QUOTA_SPEND_COMMIT_REQUEST_SCHEMA,
    operation: "replay",
    runtime_root: runtimeRoot,
    goal_id: goalId,
    effect_id: effectId,
    resolved_agent_id: "codex-main-control",
  };
}

function assertConflictReplay(
  replay: Record<string, any>,
  indexPath: string,
  original: Record<string, unknown>,
) {
  assert.equal(replay.status, "conflict");
  assert.equal(replay.conflict, true);
  assert.equal(replay.replayed, false);
  assert.equal(replay.reason_code, "effect_id_conflict");
  return readFile(indexPath, "utf8").then((value) =>
    assert.equal(value, `${JSON.stringify(original)}\n`)
  );
}

async function previewForUnrelatedEffect(
  runtimeRoot: string,
  quotaSpendCommit: unknown,
  effectRef: string,
) {
  const runsDir = join(runtimeRoot, "goals", goalId, "runs");
  await mkdir(runsDir, { recursive: true });
  const indexPath = join(runsDir, "index.jsonl");
  await writeFile(
    indexPath,
    `${JSON.stringify({
      classification: "quota_slot_spent",
      goal_id: goalId,
      agent_id: "codex-main-control",
      quota_spend_commit: quotaSpendCommit,
      effect_ref: effectRef,
    })}\n`,
  );
  return evaluateQuotaSpendCommit(
    replayRequest(runtimeRoot, "requested-effect"),
  );
}

test("preview constructs the typed public-safe spend record without writing", async () => {
  const result = await evaluateQuotaSpendCommit(request(null));
  assert.equal(result.status, "preview");
  assert.equal(result.written, false);
  assert.equal(result.record?.classification, "quota_slot_spent");
  assert.equal(result.record?.agent_id, "codex-main-control");
  const event = result.record?.quota_event as Record<string, unknown>;
  assert.equal(event.source, "heartbeat");
  assert.equal(event.slots, 1);
  assert.equal(
    (event.after as Record<string, unknown>).spent_slots,
    1,
  );
});

test("illegal spend transitions fail before any durable effect", async () => {
  const value = preview({ safe_bypass_spend: false });
  const rejected = request(null, {
    preview: value,
    before: decision(0, {
      should_run: false,
      normal_delivery_allowed: false,
      state: "waiting",
    }),
    after: decision(1, {
      should_run: false,
      normal_delivery_allowed: false,
      state: "waiting",
    }),
  });
  await assert.rejects(
    () => evaluateQuotaSpendCommit(rejected),
    /requires an eligible, safe-bypass/,
  );
  await assert.rejects(
    () => evaluateQuotaSpendCommit(request(null, { after: decision(2) })),
    /after\.spent_slots must equal before\.spent_slots \+ slots/,
  );
  await assert.rejects(
    () => evaluateQuotaSpendCommit(request(null, { source: "unknown" })),
    /quota slot spend source must be one of/,
  );
});

test("delivery completion attribution survives later repair frontiers", async () => {
  const repairFrontiers = [
    {
      effective_action: "capability_bridge_repair",
      capability_repair_allowed: true,
    },
    {
      effective_action: "control_plane_projection_repair",
      self_repair_allowed: true,
    },
  ];
  for (const repairFrontier of repairFrontiers) {
    const before = decision(0, repairFrontier);
    const after = decision(1, repairFrontier);
    const value = preview({
      delivery_completion_spend: true,
      delivery_run_classification: "state_refreshed",
      delivery_run_recommended_action: "inspect the next delivery frontier",
    });
    const result = await evaluateQuotaSpendCommit(request(null, {
      source: "visible-goal",
      preview: value,
      before,
      after,
    }));

    assert.equal(
      result.record?.health_check,
      "quota validated delivery completion; quota slot spend event public-safe",
    );
    const event = result.record?.quota_event as Record<string, unknown>;
    assert.match(
      String(event.reason_summary),
      /accounted after validated delivery state_refreshed/,
    );
    assert.equal(
      result.record?.recommended_action,
      "inspect the next delivery frontier",
    );
  }
});

test("commit owns JSON, Markdown, index, and exact-effect replay", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const params = request(runtimeRoot);
  const written = await evaluateQuotaSpendCommit(params);
  assert.equal(written.status, "written");
  assert.equal(written.written, true);
  assert.equal(written.payload.appended, true);

  const jsonPath = String(written.payload.json_path);
  const markdownPath = String(written.payload.markdown_path);
  const indexPath = String(written.payload.index_path);
  const record = JSON.parse(await readFile(jsonPath, "utf8")) as Record<string, unknown>;
  assert.equal(record.classification, "quota_slot_spent");
  assert.match(await readFile(markdownPath, "utf8"), /LoopX Quota Slot Preview/);
  const rows = (await readFile(indexPath, "utf8")).trim().split("\n");
  assert.equal(rows.length, 1);

  const replayed = await evaluateQuotaSpendCommit(params);
  assert.equal(replayed.status, "replayed");
  assert.equal(replayed.replayed, true);
  assert.equal(replayed.payload.appended, false);
  assert.equal((await readFile(indexPath, "utf8")).trim().split("\n").length, 1);
});

test("source owner rejects stale Goal A before writes and stamps Goal B", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  await assert.rejects(
    withSourceAdmission(runtimeRoot, instanceA, instanceB, async (binding) =>
      await evaluateQuotaSpendCommit({
        ...request(runtimeRoot),
        ...binding,
      })
    ),
    (error: unknown) => {
      assert.ok(error instanceof EffectRuntimeConflictError);
      assert.equal(error.code, "stale_goal_instance");
      return true;
    },
  );
  const runsDir = join(runtimeRoot, "goals", goalId, "runs");
  await assert.rejects(readFile(join(runsDir, "index.jsonl")), { code: "ENOENT" });
  await assert.rejects(readdir(join(runsDir, ".transactions")), { code: "ENOENT" });

  const written = await withSourceAdmission(
    runtimeRoot,
    instanceB,
    instanceB,
    async (binding) => await evaluateQuotaSpendCommit({
      ...request(runtimeRoot),
      ...binding,
    }),
  );
  const expectedGoalRef = {
    goal_id: goalId,
    goal_instance_id: instanceB,
  };
  assert.deepEqual(written.payload.goal_ref, expectedGoalRef);
  const record = requireJsonObject(
    JSON.parse(await readFile(String(written.payload.json_path), "utf8")),
    "quota spend record",
  );
  const event = requireJsonObject(record.quota_event, "quota spend event");
  const row = requireJsonObject(
    JSON.parse(await readFile(String(written.payload.index_path), "utf8")),
    "quota spend index row",
  );
  const receiptDirectory = join(runsDir, ".transactions", "quota-spend");
  const [receiptName] = await readdir(receiptDirectory);
  const receipt = requireJsonObject(
    JSON.parse(await readFile(join(receiptDirectory, receiptName), "utf8")),
    "quota spend receipt",
  );
  assert.deepEqual(record.goal_ref, expectedGoalRef);
  assert.deepEqual(event.goal_ref, expectedGoalRef);
  assert.deepEqual(row.goal_ref, expectedGoalRef);
  assert.deepEqual(receipt.goal_ref, expectedGoalRef);
});

test("source owner validates every witness target before claiming locks", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  await assert.rejects(
    withSourceAdmission(
      runtimeRoot,
      instanceA,
      instanceA,
      async (binding) => await evaluateQuotaSpendCommit({
        ...request(runtimeRoot),
        ...binding,
      }),
      (admission) => {
        admission.locks[1].target = join(runtimeRoot, "wrong-source.guard");
      },
    ),
    (error: unknown) => {
      assert.ok(error instanceof EffectRuntimeRequestError);
      assert.equal(error.code, "quota_source_admission_invalid");
      return true;
    },
  );
  await assert.rejects(
    readFile(join(runtimeRoot, "goals", goalId, "runs", "index.jsonl")),
    { code: "ENOENT" },
  );
});

test("source owner rejects an expired lock handoff", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const indexPath = join(runtimeRoot, "goals", goalId, "runs", "index.jsonl");
  const registryPath = join(runtimeRoot, "project", ".loopx", "registry.json");
  const guardPath = sourceGuardPath(registryPath);
  const indexLock = await acquireFileMutationLock(indexPath);
  const guardLock = await acquireFileMutationLock(guardPath);
  await releaseFileMutationLock(guardPath, guardLock.token, null, true);
  await releaseFileMutationLock(indexPath, indexLock.token, null, true);

  await assert.rejects(
    evaluateQuotaSpendCommit({
      ...request(runtimeRoot),
      goal_ref: {
        goal_id: goalId,
        goal_instance_id: instanceA,
      },
      source_admission: {
        schema_version: "loopx_quota_source_admission_v0",
        profile_id: "source_session_v1",
        registry_path: registryPath,
        planned_goal_ref: {
          goal_id: goalId,
          goal_instance_id: instanceA,
        },
        authority: {
          kind: "present",
          goal_ref: {
            goal_id: goalId,
            goal_instance_id: instanceA,
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
    }),
    (error: unknown) => {
      assert.ok(error instanceof EffectRuntimeConflictError);
      assert.equal(error.code, "quota_source_admission_expired");
      return true;
    },
  );
});

test("source owner keeps both claims through durable completion after parent loss", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const indexPath = join(runtimeRoot, "goals", goalId, "runs", "index.jsonl");
  const registryPath = join(runtimeRoot, "project", ".loopx", "registry.json");
  const guardPath = sourceGuardPath(registryPath);
  const durableResult = join(runtimeRoot, "durable-result.json");
  const deadOwnerPid = 2_147_483_647;

  await withSourceAdmission(
    runtimeRoot,
    instanceA,
    instanceA,
    async (binding) => {
      const owner = parseQuotaAccountingOwner({
        goalRefValue: binding.goal_ref,
        sourceAdmissionValue: binding.source_admission,
        runtimeRoot,
        goalId,
      });
      await withQuotaAccountingOwner(owner, async (indexLockHeld) => {
        assert.equal(indexLockHeld, true);
        await writeFile(durableResult, "{\"status\":\"committed\"}\n", "utf8");
        for (const target of [indexPath, guardPath]) {
          await assert.rejects(
            acquireFileMutationLock(target, process.pid, 0),
            EffectRuntimeLockTimeoutError,
          );
        }
      });
    },
    undefined,
    deadOwnerPid,
  );

  assert.deepEqual(
    JSON.parse(await readFile(durableResult, "utf8")),
    { status: "committed" },
  );
  const reacquiredIndex = await acquireFileMutationLock(indexPath);
  const reacquiredGuard = await acquireFileMutationLock(guardPath);
  await releaseFileMutationLock(
    guardPath,
    reacquiredGuard.token,
    null,
    true,
  );
  await releaseFileMutationLock(
    indexPath,
    reacquiredIndex.token,
    null,
    true,
  );
});

test("unscoped replay cannot consume an exact source-owned spend", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const params = request(runtimeRoot);
  await withSourceAdmission(
    runtimeRoot,
    instanceA,
    instanceA,
    async (binding) => await evaluateQuotaSpendCommit({
      ...params,
      ...binding,
    }),
  );

  const replay = await evaluateQuotaSpendCommit(params);

  assert.equal(replay.status, "conflict");
  assert.equal(replay.reason_code, "goal_instance_conflict");
});

test("native replay requires the exact source GoalRef", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const effectId = "quota-spend-source-replay";
  await withSourceAdmission(
    runtimeRoot,
    instanceA,
    instanceA,
    async (binding) =>
      await evaluateQuotaSpendCommit({
        ...request(runtimeRoot, { effect_id: effectId }),
        ...binding,
      }),
  );

  const unscoped = await evaluateQuotaSpendCommit(
    replayRequest(runtimeRoot, effectId),
  );
  assert.equal(unscoped.status, "conflict");
  assert.equal(unscoped.reason_code, "goal_instance_conflict");

  const replayed = await withSourceAdmission(
    runtimeRoot,
    instanceA,
    instanceA,
    async (binding) =>
      await evaluateQuotaSpendCommit({
        ...replayRequest(runtimeRoot, effectId),
        ...binding,
      }),
  );
  assert.equal(replayed.status, "replayed");
  assert.equal(replayed.payload.replay_found, true);
});

test("legacy spend artifacts keep the alias-only wire shape", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const written = await evaluateQuotaSpendCommit(request(runtimeRoot));
  const record = requireJsonObject(
    JSON.parse(await readFile(String(written.payload.json_path), "utf8")),
    "quota spend record",
  );
  const event = requireJsonObject(record.quota_event, "quota spend event");
  const row = requireJsonObject(
    JSON.parse(await readFile(String(written.payload.index_path), "utf8")),
    "quota spend index row",
  );
  const receiptDirectory = join(
    runtimeRoot,
    "goals",
    goalId,
    "runs",
    ".transactions",
    "quota-spend",
  );
  const [receiptName] = await readdir(receiptDirectory);
  const receipt = requireJsonObject(
    JSON.parse(await readFile(join(receiptDirectory, receiptName), "utf8")),
    "quota spend receipt",
  );

  for (const value of [
    written.payload,
    record,
    event,
    row,
    receipt,
  ]) {
    assert.equal(Object.hasOwn(value, "goal_ref"), false);
  }
});

test("native replay validates legacy rows by goal and agent", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const runsDir = join(runtimeRoot, "goals", goalId, "runs");
  await mkdir(runsDir, { recursive: true });
  await writeFile(
    join(runsDir, "index.jsonl"),
    `${JSON.stringify({
      classification: "quota_slot_spent",
      goal_id: goalId,
      agent_id: "codex-main-control",
      effect_ref: "legacy-effect-1",
    })}\n`,
  );

  const replay = await evaluateQuotaSpendCommit({
    schema_version: QUOTA_SPEND_COMMIT_REQUEST_SCHEMA,
    operation: "replay",
    runtime_root: runtimeRoot,
    goal_id: goalId,
    effect_id: "legacy-effect-1",
    resolved_agent_id: "codex-main-control",
  });
  assert.equal(replay.status, "replayed");
  assert.equal(replay.payload.idempotent_replay, true);

  for (const resolvedAgentId of [null, "codex-other-control"]) {
    const rejected = await evaluateQuotaSpendCommit({
      schema_version: QUOTA_SPEND_COMMIT_REQUEST_SCHEMA,
      operation: "replay",
      runtime_root: runtimeRoot,
      goal_id: goalId,
      effect_id: "legacy-effect-1",
      resolved_agent_id: resolvedAgentId,
    });
    assert.equal(rejected.payload.ok, false);
    assert.equal(rejected.payload.replay_found, true);
    assert.match(rejected.reason, /same valid agent identity/);
  }
});

test("native replay rejects incomplete transaction metadata", async (t) => {
  for (const quotaSpendCommit of [{}, { effect_id: "" }, { effect_id: null }]) {
    const runtimeRoot = await tempRuntime(t);
    const runsDir = join(runtimeRoot, "goals", goalId, "runs");
    await mkdir(runsDir, { recursive: true });
    const indexPath = join(runsDir, "index.jsonl");
    const original = {
      classification: "quota_slot_spent",
      goal_id: goalId,
      agent_id: "codex-main-control",
      quota_spend_commit: quotaSpendCommit,
      effect_ref: "incomplete-metadata-effect",
    };
    await writeFile(indexPath, `${JSON.stringify(original)}\n`);

    const replay = await evaluateQuotaSpendCommit({
      schema_version: QUOTA_SPEND_COMMIT_REQUEST_SCHEMA,
      operation: "replay",
      runtime_root: runtimeRoot,
      goal_id: goalId,
      effect_id: "incomplete-metadata-effect",
      resolved_agent_id: "codex-main-control",
    });

    await assertConflictReplay(replay, indexPath, original);
  }
});

test("native replay ignores non-quota rows that reuse an effect identity", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const runsDir = join(runtimeRoot, "goals", goalId, "runs");
  await mkdir(runsDir, { recursive: true });
  await writeFile(
    join(runsDir, "index.jsonl"),
    `${JSON.stringify({
      classification: "state_refreshed",
      goal_id: goalId,
      agent_id: "codex-main-control",
      effect_ref: "cross-classification-effect",
      quota_spend_commit: { effect_id: "cross-classification-effect" },
    })}\n`,
  );

  const replay = await evaluateQuotaSpendCommit({
    schema_version: QUOTA_SPEND_COMMIT_REQUEST_SCHEMA,
    operation: "replay",
    runtime_root: runtimeRoot,
    goal_id: goalId,
    effect_id: "cross-classification-effect",
    resolved_agent_id: "codex-main-control",
  });

  assert.equal(replay.status, "preview");
  assert.equal(replay.replayed, false);
  assert.equal(replay.payload.replay_found, false);
  assert.match(replay.reason, /replay was not found/);
});

test("read-only replay misses do not create a runtime directory", async (t) => {
  const runtimeRoot = await tempRuntime(t);

  const replay = await evaluateQuotaSpendCommit({
    ...replayRequest(runtimeRoot, "missing-effect"),
    read_only: true,
  });

  assert.equal(replay.status, "preview");
  assert.deepEqual(await readdir(runtimeRoot), []);
});

test("native replay rejects either conflicting effect identity direction", async (t) => {
  for (const [label, row] of [
    ["metadata-first", {
      quota_spend_commit: { effect_id: "replay-conflict-effect" },
      effect_ref: "different-effect",
    }],
    ["ref-first", {
      quota_spend_commit: { effect_id: "different-effect" },
      effect_ref: "replay-conflict-effect",
    }],
  ] as const) {
    const runtimeRoot = await tempRuntime(t);
    const runsDir = join(runtimeRoot, "goals", goalId, "runs");
    await mkdir(runsDir, { recursive: true });
    const indexPath = join(runsDir, "index.jsonl");
    const original = {
      classification: "quota_slot_spent",
      goal_id: goalId,
      agent_id: "codex-main-control",
      ...row,
    };
    await writeFile(indexPath, `${JSON.stringify(original)}\n`);

    const replay = await evaluateQuotaSpendCommit(
      replayRequest(runtimeRoot, "replay-conflict-effect"),
    );

    assert.equal(label.length > 0, true);
    await assertConflictReplay(replay, indexPath, original);
  }
});

test("native replay ignores conflicting effect identities unrelated to its effect", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const replay = await previewForUnrelatedEffect(
    runtimeRoot,
    { effect_id: "unrelated-first" },
    "unrelated-second",
  );

  assert.equal(replay.status, "preview");
  assert.equal(replay.conflict, false);
  assert.equal(replay.payload.replay_found, false);
});

test("native replay ignores malformed metadata unrelated to its effect", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const replay = await previewForUnrelatedEffect(
    runtimeRoot,
    [],
    "unrelated-effect",
  );

  assert.equal(replay.status, "preview");
  assert.equal(replay.conflict, false);
  assert.equal(replay.payload.replay_found, false);
});

test("prepared transaction repairs partial artifacts exactly once", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const params = request(runtimeRoot);
  const written = await evaluateQuotaSpendCommit(params);
  const markdownPath = String(written.payload.markdown_path);
  const indexPath = String(written.payload.index_path);
  await unlink(markdownPath);
  await unlink(indexPath);

  const repaired = await evaluateQuotaSpendCommit(params);
  assert.equal(repaired.status, "repaired");
  assert.equal(repaired.repaired, true);
  assert.equal(repaired.payload.transaction_repaired, true);
  assert.match(await readFile(markdownPath, "utf8"), /quota_slot_spent/);
  assert.equal((await readFile(indexPath, "utf8")).trim().split("\n").length, 1);

  const replayed = await evaluateQuotaSpendCommit(params);
  assert.equal(replayed.status, "replayed");
});

test("prepared source transaction repairs only for its current GoalRef", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const params = request(runtimeRoot);
  const written = await withSourceAdmission(
    runtimeRoot,
    instanceA,
    instanceA,
    async (binding) =>
      await evaluateQuotaSpendCommit({
        ...params,
        ...binding,
      }),
  );
  const indexPath = String(written.payload.index_path);
  const markdownPath = String(written.payload.markdown_path);
  const transactionDir = join(
    dirname(indexPath),
    ".transactions",
    "quota-spend",
  );
  const [receiptName] = await readdir(transactionDir);
  assert.ok(receiptName);
  const receiptPath = join(transactionDir, receiptName);
  const receipt = requireJsonObject(
    JSON.parse(await readFile(receiptPath, "utf8")),
    "prepared quota spend receipt",
  );
  receipt.status = "prepared";
  await Promise.all([
    writeFile(receiptPath, `${JSON.stringify(receipt, null, 2)}\n`, "utf8"),
    unlink(markdownPath),
  ]);

  const repaired = await withSourceAdmission(
    runtimeRoot,
    instanceA,
    instanceA,
    async (binding) =>
      await evaluateQuotaSpendCommit({
        ...params,
        ...binding,
      }),
  );
  assert.equal(repaired.status, "repaired");
  assert.match(await readFile(markdownPath, "utf8"), /quota_slot_spent/);

  receipt.status = "prepared";
  await Promise.all([
    writeFile(receiptPath, `${JSON.stringify(receipt, null, 2)}\n`, "utf8"),
    unlink(markdownPath),
  ]);
  await assert.rejects(
    withSourceAdmission(
      runtimeRoot,
      instanceA,
      instanceB,
      async (binding) =>
        await evaluateQuotaSpendCommit({
          ...params,
          ...binding,
        }),
    ),
    (error: unknown) => {
      assert.ok(error instanceof EffectRuntimeConflictError);
      assert.equal(error.code, "stale_goal_instance");
      return true;
    },
  );
  await assert.rejects(readFile(markdownPath), { code: "ENOENT" });

  const foreign = await withSourceAdmission(
    runtimeRoot,
    instanceB,
    instanceB,
    async (binding) =>
      await evaluateQuotaSpendCommit({
        ...params,
        ...binding,
      }),
  );
  assert.equal(foreign.status, "conflict");
  assert.equal(foreign.reason_code, "goal_instance_conflict");
  await assert.rejects(readFile(markdownPath), { code: "ENOENT" });
  const foreignReceipt = requireJsonObject(
    JSON.parse(await readFile(receiptPath, "utf8")),
    "foreign quota spend receipt",
  );
  assert.equal(foreignReceipt.status, "prepared");
});

test("prepared transactions keep artifact paths reserved across later spends", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const firstParams = request(runtimeRoot);
  const first = await evaluateQuotaSpendCommit(firstParams);
  const firstJsonPath = String(first.payload.json_path);
  const firstMarkdownPath = String(first.payload.markdown_path);
  const indexPath = String(first.payload.index_path);
  const transactionDir = join(dirname(indexPath), ".transactions", "quota-spend");
  const receiptPaths = (await readdir(transactionDir)).map((name) =>
    join(transactionDir, name)
  );
  const firstReceiptPath = receiptPaths[0];
  assert.ok(firstReceiptPath);
  const firstReceipt = JSON.parse(
    await readFile(firstReceiptPath, "utf8"),
  ) as Record<string, unknown>;
  firstReceipt.status = "prepared";
  await writeFile(firstReceiptPath, `${JSON.stringify(firstReceipt, null, 2)}\n`, "utf8");
  await Promise.all([
    unlink(firstJsonPath),
    unlink(firstMarkdownPath),
    unlink(indexPath),
  ]);

  const second = await evaluateQuotaSpendCommit(request(runtimeRoot, {
    effect_id: "quota-spend-effect-2",
  }));
  assert.equal(second.status, "written");
  assert.notEqual(second.payload.json_path, firstJsonPath);
  assert.notEqual(second.payload.markdown_path, firstMarkdownPath);

  const repaired = await evaluateQuotaSpendCommit(firstParams);
  assert.equal(repaired.status, "repaired");
  assert.equal(repaired.payload.transaction_repaired, true);
});

test("prepared transaction repairs its own truncated final index row", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const firstParams = request(runtimeRoot);
  const first = await evaluateQuotaSpendCommit(firstParams);
  const indexPath = String(first.payload.index_path);
  const firstRow = (await readFile(indexPath, "utf8")).trim();
  const params = request(runtimeRoot, {
    effect_id: "quota-spend-effect-2",
    generated_at: "2026-08-25T12:02:00+08:00",
    expected_index_digest: await quotaSpendIndexDigest(indexPath),
  });
  await evaluateQuotaSpendCommit(params);
  const transactionDir = join(dirname(indexPath), ".transactions", "quota-spend");
  const receiptPaths = (await readdir(transactionDir)).map((name) =>
    join(transactionDir, name)
  );
  const receipts = await Promise.all(receiptPaths.map(async (path) => ({
    path,
    value: JSON.parse(
      await readFile(path, "utf8"),
    ) as Record<string, unknown>,
  })));
  const selected = receipts.find(({ value }) => value.effect_id === params.effect_id);
  assert.ok(selected);
  const receipt = selected.value;
  receipt.status = "prepared";
  await writeFile(selected.path, `${JSON.stringify(receipt, null, 2)}\n`, "utf8");

  const expectedLine = JSON.stringify(receipt.index_record);
  await writeFile(
    indexPath,
    `${firstRow}\n${
      expectedLine.slice(0, Math.floor(expectedLine.length / 2))
    }`,
    "utf8",
  );

  const repaired = await evaluateQuotaSpendCommit(params);
  assert.equal(repaired.status, "repaired");
  assert.equal(repaired.payload.transaction_repaired, true);
  const rows = (await readFile(indexPath, "utf8")).trim().split("\n");
  assert.equal(rows.length, 2);
  assert.equal(rows[0], firstRow);
  assert.equal(
    (JSON.parse(rows[1]).quota_spend_commit as Record<string, unknown>).effect_id,
    params.effect_id,
  );

  const replayed = await evaluateQuotaSpendCommit(params);
  assert.equal(replayed.status, "replayed");

  await writeFile(indexPath, `${firstRow}\n{\"different\":`, "utf8");
  await assert.rejects(
    () => evaluateQuotaSpendCommit(params),
    /quota run index line 2 is malformed/,
  );
});

test("shared receipt replay rejects a replaced spend index prefix", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const first = await evaluateQuotaSpendCommit(request(runtimeRoot));
  const indexPath = String(first.payload.index_path);
  const params = request(runtimeRoot, {
    effect_id: "quota-spend-effect-prefix-fence",
    generated_at: "2026-08-25T12:02:00+08:00",
    expected_index_digest: await quotaSpendIndexDigest(indexPath),
  });
  await evaluateQuotaSpendCommit(params);
  const replacement = `${JSON.stringify({
    generated_at: "2026-08-25T12:03:00+08:00",
    goal_id: "quota-spend-commit",
    classification: "unrelated",
  })}\n`;
  await writeFile(indexPath, replacement, "utf8");

  await assert.rejects(
    () => evaluateQuotaSpendCommit(params),
    /quota spend run index no longer retains its transaction prefix/,
  );
  assert.equal(await readFile(indexPath, "utf8"), replacement);
});

test("shared receipt replay tolerates a legal prefix rewrite that preserves its row", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const first = await evaluateQuotaSpendCommit(request(runtimeRoot));
  const indexPath = String(first.payload.index_path);
  const params = request(runtimeRoot, {
    effect_id: "quota-spend-effect-preserved-after-repair",
    generated_at: "2026-08-25T12:02:00+08:00",
    expected_index_digest: await quotaSpendIndexDigest(indexPath),
  });
  await evaluateQuotaSpendCommit(params);
  const rows = (await readFile(indexPath, "utf8")).trim().split("\n");
  assert.equal(rows.length, 2);

  await writeFile(indexPath, `${rows[1]}\n`, "utf8");

  const replayed = await evaluateQuotaSpendCommit(params);
  assert.equal(replayed.status, "replayed");
  assert.equal(await readFile(indexPath, "utf8"), `${rows[1]}\n`);
});

test("shared receipt replay rejects a mutated matching spend index row", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const params = request(runtimeRoot);
  const written = await evaluateQuotaSpendCommit(params);
  const indexPath = String(written.payload.index_path);
  const row = JSON.parse(await readFile(indexPath, "utf8")) as Record<string, unknown>;
  const mutated = `${JSON.stringify({
    ...row,
    goal_id: "other-goal",
    json_path: "bogus.json",
  })}\n`;
  await writeFile(indexPath, mutated, "utf8");

  await assert.rejects(
    () => evaluateQuotaSpendCommit(params),
    /quota spend index record conflicts with its transaction receipt/,
  );
  assert.equal(await readFile(indexPath, "utf8"), mutated);
});

test("effect identity and index CAS reject drift and racing writers", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const params = request(runtimeRoot);
  const first = await evaluateQuotaSpendCommit(params);
  const changed = await evaluateQuotaSpendCommit({
    ...params,
    source: "controller",
  });
  assert.equal(changed.status, "conflict");
  assert.equal(changed.reason_code, "effect_id_conflict");

  const stale = await evaluateQuotaSpendCommit(request(runtimeRoot, {
    effect_id: "quota-spend-effect-stale",
  }));
  assert.equal(stale.status, "conflict");
  assert.equal(stale.reason_code, "index_digest_conflict");

  const indexPath = String(first.payload.index_path);
  const currentDigest = await quotaSpendIndexDigest(indexPath);
  const fresh = await evaluateQuotaSpendCommit(request(runtimeRoot, {
    effect_id: "quota-spend-effect-2",
    generated_at: "2026-08-25T12:02:00+08:00",
    expected_index_digest: currentDigest,
  }));
  assert.equal(fresh.status, "written");
});

test("concurrent exact-effect retries serialize to one spend", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const params = request(runtimeRoot);
  const results = await Promise.all([
    evaluateQuotaSpendCommit(params),
    evaluateQuotaSpendCommit(params),
  ]);
  assert.deepEqual(
    results.map((item) => item.status).sort(),
    ["replayed", "written"],
  );
  const indexPath = String(results[0].payload.index_path);
  assert.equal((await readFile(indexPath, "utf8")).trim().split("\n").length, 1);
});
