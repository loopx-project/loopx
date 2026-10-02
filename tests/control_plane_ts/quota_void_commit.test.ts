import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  mkdtemp,
  mkdir,
  readFile,
  readdir,
  rm,
  symlink,
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
import { EffectRuntimeConflictError } from "../../loopx/control_plane/effect_runtime_errors.ts";
import { requireJsonObject } from "../../loopx/control_plane/runtime_decode.ts";
import {
  evaluateQuotaVoidCommit,
  quotaVoidIndexDigest,
  QUOTA_VOID_COMMIT_REQUEST_SCHEMA,
} from "../../loopx/control_plane/quota/void_commit.ts";

const goalId = "quota-void-transaction";
const instanceA = "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const instanceB = "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const targetGeneratedAt = "2026-08-25T11:59:00+08:00";
const voidGeneratedAt = "2026-08-25T12:00:00+08:00";

interface GoalRef {
  goal_id: string;
  goal_instance_id: string;
}

interface TargetFixture {
  runtimeRoot: string;
  runsDir: string;
  indexPath: string;
  indexContent: string;
  targetJsonPath: string;
}

function beforeDecision(spentSlots: unknown = 5): Record<string, unknown> {
  return {
    should_run: false,
    normal_delivery_allowed: false,
    recovery_delivery_allowed: false,
    effective_action: "wait_for_quota",
    self_repair_allowed: false,
    capability_repair_allowed: false,
    workspace_repair_allowed: false,
    state: "throttled",
    safe_bypass_allowed: false,
    safe_bypass_kind: null,
    blocked_action_scope: "normal_delivery",
    agent_identity: { agent_id: "codex-main-control" },
    quota: {
      compute: 1,
      window_hours: 24,
      slot_minutes: 1,
      spent_slots: spentSlots,
      allowed_slots: 5,
    },
  };
}

function quotaSpendEvent(
  slots: unknown,
  goalRef?: GoalRef,
): Record<string, unknown> {
  const event: Record<string, unknown> = {
    event_type: "quota_slot_spent",
    source: "heartbeat",
    slots,
    reason_summary: "accounted completed delivery",
    before: { spent_slots: 3 },
    after: { spent_slots: 5 },
  };
  if (goalRef) event.goal_ref = goalRef;
  return event;
}

async function tempRuntime(t: test.TestContext): Promise<string> {
  const runtimeRoot = await mkdtemp(join(tmpdir(), "loopx-quota-void-commit-"));
  t.after(() => rm(runtimeRoot, { recursive: true, force: true }));
  return runtimeRoot;
}

async function targetFixture(
  t: test.TestContext,
  options: {
    inline?: boolean;
    slots?: unknown;
    generatedAt?: string;
    jsonPath?: string;
    goalRef?: GoalRef;
  } = {},
): Promise<TargetFixture> {
  const runtimeRoot = await tempRuntime(t);
  const runsDir = join(runtimeRoot, "goals", goalId, "runs");
  await mkdir(runsDir, { recursive: true });
  const indexPath = join(runsDir, "index.jsonl");
  const generatedAt = options.generatedAt ?? targetGeneratedAt;
  const targetJsonPath = options.jsonPath ?? join(
    runsDir,
    "20260825-115900-quota-slot-spent.json",
  );
  const event = quotaSpendEvent(options.slots ?? 2, options.goalRef);
  const indexRecord: Record<string, unknown> = {
    generated_at: generatedAt,
    goal_id: goalId,
    classification: "quota_slot_spent",
    json_path: targetJsonPath,
  };
  if (options.goalRef) indexRecord.goal_ref = options.goalRef;
  if (options.inline !== false) {
    indexRecord.quota_event = event;
  } else {
    await writeFile(
      targetJsonPath,
      `${JSON.stringify({
        generated_at: generatedAt,
        goal_id: goalId,
        classification: "quota_slot_spent",
        quota_event: event,
        ...(options.goalRef ? { goal_ref: options.goalRef } : {}),
      }, null, 2)}\n`,
      "utf8",
    );
  }
  const indexContent = `${JSON.stringify(indexRecord)}\n`;
  await writeFile(indexPath, indexContent, "utf8");
  return { runtimeRoot, runsDir, indexPath, indexContent, targetJsonPath };
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
  fixture: TargetFixture,
  plannedInstanceId: string,
  currentInstanceId: string,
  run: (binding: Record<string, unknown>) => Promise<T>,
): Promise<T> {
  const registryPath = join(
    fixture.runtimeRoot,
    "project",
    ".loopx",
    "registry.json",
  );
  const guardPath = sourceGuardPath(registryPath);
  const indexLock = await acquireFileMutationLock(fixture.indexPath);
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
            target: fixture.indexPath,
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
    await releaseFileMutationLock(
      fixture.indexPath,
      indexLock.token,
      null,
      true,
    );
  }
}

async function rawIndexDigest(indexPath: string): Promise<string> {
  const value = await readFile(indexPath);
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}

async function request(
  fixture: TargetFixture,
  extra: Record<string, unknown> = {},
): Promise<Record<string, unknown>> {
  return {
    schema_version: QUOTA_VOID_COMMIT_REQUEST_SCHEMA,
    effect_id: "quota-void-effect-1",
    runtime_root: fixture.runtimeRoot,
    goal_id: goalId,
    voided_run_generated_at: targetGeneratedAt,
    source: "heartbeat",
    reason_summary: "duplicate heartbeat spend",
    generated_at: voidGeneratedAt,
    execute: true,
    expected_index_digest: await rawIndexDigest(fixture.indexPath),
    before: beforeDecision(),
    ...extra,
  };
}

async function transactionReceipt(
  runsDir: string,
  effectId: string,
): Promise<{ path: string; value: Record<string, unknown> }> {
  const candidates: string[] = [];
  async function collect(directory: string): Promise<void> {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) {
        await collect(path);
      } else if (entry.isFile() && entry.name.endsWith(".json")) {
        candidates.push(path);
      }
    }
  }
  await collect(join(runsDir, ".transactions"));
  for (const path of candidates) {
    const value = JSON.parse(await readFile(path, "utf8")) as Record<string, unknown>;
    if (value.effect_id === effectId) return { path, value };
  }
  throw new Error(`transaction receipt was not found for ${effectId}`);
}

test("preview finds an inline spend event and preserves the legacy payload without writes", async (t) => {
  const fixture = await targetFixture(t);
  const filesBefore = (await readdir(fixture.runsDir)).sort();
  const params = await request(fixture, { execute: false });

  assert.equal(
    await quotaVoidIndexDigest(fixture.indexPath),
    await rawIndexDigest(fixture.indexPath),
  );
  const result = await evaluateQuotaVoidCommit(params);

  assert.equal(result.status, "preview");
  assert.equal(result.payload.ok, true);
  assert.equal(result.payload.mode, "void-slot");
  assert.equal(result.payload.dry_run, true);
  assert.equal(result.payload.appended, false);
  assert.equal(result.payload.registry_mutated, false);
  assert.equal(result.payload.slots, 2);
  assert.equal(result.payload.voided_run_generated_at, targetGeneratedAt);
  assert.equal(
    ((result.payload.after as Record<string, unknown>).quota as Record<string, unknown>)
      .spent_slots,
    3,
  );
  assert.equal(result.record?.classification, "quota_slot_voided");
  assert.deepEqual((await readdir(fixture.runsDir)).sort(), filesBefore);
  assert.equal(await readFile(fixture.indexPath, "utf8"), fixture.indexContent);
});

test("target lookup falls back to the bounded legacy JSON artifact", async (t) => {
  const fixture = await targetFixture(t, { inline: false, slots: 4 });
  const params = await request(fixture, { execute: false });

  const result = await evaluateQuotaVoidCommit(params);

  assert.equal(result.status, "preview");
  assert.equal(result.payload.voided_run_json_path, fixture.targetJsonPath);
  const event = result.record?.quota_event as Record<string, unknown>;
  assert.equal(event.event_type, "quota_slot_voided");
  assert.equal(event.slots, 4);
  assert.equal((event.after as Record<string, unknown>).spent_slots, 1);
});

test("legacy index rows without goal identity use the requested goal", async (t) => {
  const fixture = await targetFixture(t, { inline: false });
  const indexRecord = JSON.parse(fixture.indexContent) as Record<string, unknown>;
  delete indexRecord.goal_id;
  await writeFile(fixture.indexPath, `${JSON.stringify(indexRecord)}\n`, "utf8");

  const result = await evaluateQuotaVoidCommit(await request(fixture, {
    execute: false,
    expected_index_digest: await rawIndexDigest(fixture.indexPath),
  }));

  assert.equal(result.status, "preview");
  assert.equal(result.payload.goal_id, goalId);
});

test("a missing spend target returns the legacy not-found payload and writes nothing", async (t) => {
  const fixture = await targetFixture(t, {
    generatedAt: "2026-08-25T11:58:00+08:00",
  });
  const filesBefore = (await readdir(fixture.runsDir)).sort();
  const params = await request(fixture);

  const result = await evaluateQuotaVoidCommit(params);

  assert.equal(result.status, "not_found");
  assert.equal(result.payload.ok, false);
  assert.equal(result.payload.appended, false);
  assert.match(String(result.payload.reason), /target quota_slot_spent run was not found/);
  assert.equal(result.record, null);
  assert.deepEqual((await readdir(fixture.runsDir)).sort(), filesBefore);
  assert.equal(await readFile(fixture.indexPath, "utf8"), fixture.indexContent);
});

test("invalid source and filesystem paths fail before mutation", async (t) => {
  const fixture = await targetFixture(t);
  const originalFiles = (await readdir(fixture.runsDir)).sort();
  const originalIndex = await readFile(fixture.indexPath, "utf8");

  await assert.rejects(
    async () => evaluateQuotaVoidCommit(await request(fixture, { source: "unknown" })),
    /quota slot (spend|void) source must be one of/,
  );
  await assert.rejects(
    async () => evaluateQuotaVoidCommit(await request(fixture, {
      operation: "preview",
      execute: true,
    })),
    /preview operation cannot execute durable effects/,
  );
  await assert.rejects(
    async () => evaluateQuotaVoidCommit(await request(fixture, {
      before: { ...beforeDecision(), should_run: "true" },
    })),
    /before\.should_run must be a boolean/,
  );
  await assert.rejects(
    async () => evaluateQuotaVoidCommit(await request(fixture, { goal_id: "../escape" })),
    /goal_id must be a single path segment|goal id must be a single path segment/,
  );
  await assert.rejects(
    async () => evaluateQuotaVoidCommit(await request(fixture, {
      runtime_root: "relative/runtime",
    })),
    /runtime_root must be absolute/,
  );

  const outsidePath = join(fixture.runtimeRoot, "outside-spend.json");
  await writeFile(
    outsidePath,
    `${JSON.stringify({
      classification: "quota_slot_spent",
      quota_event: quotaSpendEvent(1),
    })}\n`,
    "utf8",
  );
  const escapedTarget = {
    generated_at: targetGeneratedAt,
    goal_id: goalId,
    classification: "quota_slot_spent",
    json_path: outsidePath,
  };
  await writeFile(fixture.indexPath, `${JSON.stringify(escapedTarget)}\n`, "utf8");
  await assert.rejects(
    async () => evaluateQuotaVoidCommit(await request(fixture, {
      execute: false,
      expected_index_digest: await rawIndexDigest(fixture.indexPath),
    })),
    /json_path|artifact path|runs directory/,
  );

  await writeFile(fixture.indexPath, originalIndex, "utf8");
  assert.deepEqual((await readdir(fixture.runsDir)).sort(), originalFiles);
});

test("commit atomically owns the JSON, Markdown, and index artifacts", async (t) => {
  const fixture = await targetFixture(t);
  const result = await evaluateQuotaVoidCommit(await request(fixture));

  assert.equal(result.status, "written");
  assert.equal(result.payload.dry_run, false);
  assert.equal(result.payload.appended, true);
  assert.equal(result.payload.registry_mutated, false);
  assert.equal(result.payload.classification, "quota_slot_voided");
  const jsonPath = String(result.payload.json_path);
  const markdownPath = String(result.payload.markdown_path);
  const persisted = JSON.parse(await readFile(jsonPath, "utf8")) as Record<string, unknown>;
  assert.equal(persisted.classification, "quota_slot_voided");
  assert.equal(
    (persisted.quota_event as Record<string, unknown>).voided_run_generated_at,
    targetGeneratedAt,
  );
  assert.match(await readFile(markdownPath, "utf8"), /LoopX Quota Slot Preview/);
  assert.match(await readFile(markdownPath, "utf8"), /quota_slot_voided/);
  const rows = (await readFile(fixture.indexPath, "utf8")).trim().split("\n");
  assert.deepEqual(
    rows.map((line) => (JSON.parse(line) as Record<string, unknown>).classification),
    ["quota_slot_spent", "quota_slot_voided"],
  );
});

test("source Goal B voids only a spend owned by B", async (t) => {
  const goalRefA = { goal_id: goalId, goal_instance_id: instanceA };
  const goalRefB = { goal_id: goalId, goal_instance_id: instanceB };
  for (const targetOwner of [goalRefA, undefined]) {
    const fixture = await targetFixture(t, { goalRef: targetOwner });
    await assert.rejects(
      withSourceAdmission(
        fixture,
        instanceB,
        instanceB,
        async (binding) =>
          await evaluateQuotaVoidCommit({
            ...await request(fixture),
            ...binding,
          }),
      ),
      (error: unknown) => {
        assert.ok(error instanceof EffectRuntimeConflictError);
        assert.equal(error.code, "goal_instance_conflict");
        return true;
      },
    );
    assert.equal(await readFile(fixture.indexPath, "utf8"), fixture.indexContent);
    await assert.rejects(
      readdir(join(fixture.runsDir, ".transactions")),
      { code: "ENOENT" },
    );
  }

  const fixture = await targetFixture(t, { goalRef: goalRefB });
  const written = await withSourceAdmission(
    fixture,
    instanceB,
    instanceB,
    async (binding) =>
      await evaluateQuotaVoidCommit({
        ...await request(fixture),
        ...binding,
      }),
  );

  assert.equal(written.status, "written");
  assert.deepEqual(written.payload.goal_ref, goalRefB);
  const record = requireJsonObject(
    JSON.parse(await readFile(String(written.payload.json_path), "utf8")),
    "quota void record",
  );
  const event = requireJsonObject(record.quota_event, "quota void event");
  const rows = (await readFile(fixture.indexPath, "utf8"))
    .trim()
    .split("\n")
    .map((line) => requireJsonObject(
      JSON.parse(line),
      "quota void index row",
    ));
  const receipt = await transactionReceipt(
    fixture.runsDir,
    String(written.effect_id),
  );
  assert.deepEqual(record.goal_ref, goalRefB);
  assert.deepEqual(event.goal_ref, goalRefB);
  assert.deepEqual(rows[1]?.goal_ref, goalRefB);
  assert.deepEqual(receipt.value.goal_ref, goalRefB);
});

test("the same effect replays without appending a second void", async (t) => {
  const fixture = await targetFixture(t);
  const params = await request(fixture);
  const written = await evaluateQuotaVoidCommit(params);
  const indexAfterWrite = await readFile(fixture.indexPath, "utf8");

  const replayed = await evaluateQuotaVoidCommit(params);

  assert.equal(written.status, "written");
  assert.equal(replayed.status, "replayed");
  assert.equal(replayed.replayed, true);
  assert.equal(replayed.payload.appended, false);
  assert.equal(replayed.payload.json_path, written.payload.json_path);
  assert.equal(await readFile(fixture.indexPath, "utf8"), indexAfterWrite);
});

test("the same effect identity rejects semantic request drift", async (t) => {
  const fixture = await targetFixture(t);
  const params = await request(fixture);
  await evaluateQuotaVoidCommit(params);
  const indexAfterWrite = await readFile(fixture.indexPath, "utf8");

  const conflict = await evaluateQuotaVoidCommit({
    ...params,
    reason_summary: "a different accounting correction",
  });

  assert.equal(conflict.status, "conflict");
  assert.equal(conflict.reason_code, "effect_id_conflict");
  assert.equal(conflict.payload.goal_id, goalId);
  assert.equal(conflict.payload.effect_id, params.effect_id);
  assert.equal(await readFile(fixture.indexPath, "utf8"), indexAfterWrite);
});

test("distinct effects may append independent voids for the same spend target", async (t) => {
  const fixture = await targetFixture(t);
  const first = await evaluateQuotaVoidCommit(await request(fixture));
  const second = await evaluateQuotaVoidCommit(await request(fixture, {
    effect_id: "quota-void-effect-2",
    expected_index_digest: await quotaVoidIndexDigest(fixture.indexPath),
  }));

  assert.equal(first.status, "written");
  assert.equal(second.status, "written");
  assert.notEqual(first.payload.json_path, second.payload.json_path);
  assert.notEqual(first.payload.markdown_path, second.payload.markdown_path);
  const rows = (await readFile(fixture.indexPath, "utf8")).trim().split("\n");
  assert.equal(rows.length, 3);
  assert.deepEqual(
    rows.map((line) => (JSON.parse(line) as Record<string, unknown>).classification),
    ["quota_slot_spent", "quota_slot_voided", "quota_slot_voided"],
  );
});

test("index CAS serializes distinct racing void effects", async (t) => {
  const fixture = await targetFixture(t);
  const [left, right] = await Promise.all([
    evaluateQuotaVoidCommit(await request(fixture, { effect_id: "quota-void-race-a" })),
    evaluateQuotaVoidCommit(await request(fixture, { effect_id: "quota-void-race-b" })),
  ]);

  assert.deepEqual(
    [left.status, right.status].sort(),
    ["conflict", "written"],
  );
  const conflict = [left, right].find((result) => result.status === "conflict");
  assert.equal(conflict?.reason_code, "index_digest_conflict");
  assert.equal((await readFile(fixture.indexPath, "utf8")).trim().split("\n").length, 2);
});

test("a prepared transaction repairs missing artifacts and its absent index row", async (t) => {
  const fixture = await targetFixture(t);
  const params = await request(fixture);
  const written = await evaluateQuotaVoidCommit(params);
  const receipt = await transactionReceipt(fixture.runsDir, String(params.effect_id));
  receipt.value.status = "prepared";
  await writeFile(receipt.path, `${JSON.stringify(receipt.value, null, 2)}\n`, "utf8");
  await Promise.all([
    unlink(String(written.payload.json_path)),
    unlink(String(written.payload.markdown_path)),
    writeFile(fixture.indexPath, fixture.indexContent, "utf8"),
  ]);

  const repaired = await evaluateQuotaVoidCommit(params);

  assert.equal(repaired.status, "repaired");
  assert.equal(repaired.repaired, true);
  assert.equal(repaired.payload.transaction_repaired, true);
  assert.equal(
    (JSON.parse(await readFile(String(written.payload.json_path), "utf8")) as Record<string, unknown>)
      .classification,
    "quota_slot_voided",
  );
  assert.match(await readFile(String(written.payload.markdown_path), "utf8"), /quota_slot_voided/);
  assert.equal((await readFile(fixture.indexPath, "utf8")).trim().split("\n").length, 2);
  assert.equal((await evaluateQuotaVoidCommit(params)).status, "replayed");
});

test("a prepared transaction repairs only its own truncated final index row", async (t) => {
  const fixture = await targetFixture(t);
  await evaluateQuotaVoidCommit(await request(fixture));
  const indexBeforeSecond = await readFile(fixture.indexPath, "utf8");
  const secondParams = await request(fixture, {
    effect_id: "quota-void-effect-2",
    generated_at: "2026-08-25T12:02:00+08:00",
    expected_index_digest: await quotaVoidIndexDigest(fixture.indexPath),
  });
  await evaluateQuotaVoidCommit(secondParams);
  const receipt = await transactionReceipt(fixture.runsDir, String(secondParams.effect_id));
  receipt.value.status = "prepared";
  await writeFile(receipt.path, `${JSON.stringify(receipt.value, null, 2)}\n`, "utf8");
  const expectedLine = JSON.stringify(receipt.value.index_record);
  assert.notEqual(expectedLine, undefined);
  await writeFile(
    fixture.indexPath,
    `${indexBeforeSecond}${expectedLine.slice(0, Math.floor(expectedLine.length / 2))}`,
    "utf8",
  );

  const repaired = await evaluateQuotaVoidCommit(secondParams);

  assert.equal(repaired.status, "repaired");
  const repairedIndex = await readFile(fixture.indexPath, "utf8");
  assert.equal(repairedIndex.startsWith(indexBeforeSecond), true);
  const rows = repairedIndex.trim().split("\n");
  assert.equal(rows.length, 3);
  assert.equal(
    (JSON.parse(rows[2]) as Record<string, unknown>).classification,
    "quota_slot_voided",
  );
  assert.equal((await evaluateQuotaVoidCommit(secondParams)).status, "replayed");
});

test("receipt replay fails closed when its committed index prefix drifts", async (t) => {
  const fixture = await targetFixture(t);
  const params = await request(fixture);
  await evaluateQuotaVoidCommit(params);
  const replacement = `${JSON.stringify({
    generated_at: "2026-08-25T12:01:00+08:00",
    goal_id: goalId,
    classification: "unrelated",
  })}\n`;
  await writeFile(fixture.indexPath, replacement, "utf8");

  await assert.rejects(
    () => evaluateQuotaVoidCommit(params),
    /quota void run index no longer retains its transaction prefix/,
  );
  assert.equal(await readFile(fixture.indexPath, "utf8"), replacement);
});

test("receipt replay tolerates duplicate repair when its exact void row remains", async (t) => {
  const fixture = await targetFixture(t);
  await writeFile(
    fixture.indexPath,
    `${fixture.indexContent}${fixture.indexContent}`,
    "utf8",
  );
  const params = await request(fixture, {
    expected_index_digest: await quotaVoidIndexDigest(fixture.indexPath),
  });
  await evaluateQuotaVoidCommit(params);
  const rows = (await readFile(fixture.indexPath, "utf8")).trim().split("\n");
  assert.equal(rows.length, 3);

  const repairedIndex = `${rows[1]}\n${rows[2]}\n`;
  await writeFile(fixture.indexPath, repairedIndex, "utf8");

  const replayed = await evaluateQuotaVoidCommit(params);
  assert.equal(replayed.status, "replayed");
  assert.equal(await readFile(fixture.indexPath, "utf8"), repairedIndex);
});

test("receipt replay rejects a mutated matching void index row", async (t) => {
  const fixture = await targetFixture(t);
  const params = await request(fixture);
  await evaluateQuotaVoidCommit(params);
  const rows = (await readFile(fixture.indexPath, "utf8"))
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line) as Record<string, unknown>);
  rows[1] = { ...rows[1], goal_id: "other-goal", json_path: "bogus.json" };
  const mutated = `${rows.map((row) => JSON.stringify(row)).join("\n")}\n`;
  await writeFile(fixture.indexPath, mutated, "utf8");

  await assert.rejects(
    () => evaluateQuotaVoidCommit(params),
    /quota void index record conflicts with its transaction receipt/,
  );
  assert.equal(await readFile(fixture.indexPath, "utf8"), mutated);
});

test("a committed receipt refuses to overwrite a conflicting artifact", async (t) => {
  const fixture = await targetFixture(t);
  const params = await request(fixture);
  const written = await evaluateQuotaVoidCommit(params);
  await writeFile(String(written.payload.json_path), "{}\n", "utf8");

  await assert.rejects(
    () => evaluateQuotaVoidCommit(params),
    /JSON artifact conflicts with its transaction receipt/,
  );
});

test("receipt repair rejects paths outside the run directory and symbolic links", async (t) => {
  const fixture = await targetFixture(t);
  const params = await request(fixture);
  const written = await evaluateQuotaVoidCommit(params);
  const receipt = await transactionReceipt(fixture.runsDir, String(params.effect_id));
  const outsidePath = join(fixture.runtimeRoot, "outside-quota-void.json");
  const escapedReceipt = {
    ...receipt.value,
    status: "prepared",
    json_path: outsidePath,
    index_record: {
      ...(receipt.value.index_record as Record<string, unknown>),
      json_path: outsidePath,
    },
    payload: {
      ...(receipt.value.payload as Record<string, unknown>),
      json_path: outsidePath,
    },
  };
  await writeFile(receipt.path, `${JSON.stringify(escapedReceipt, null, 2)}\n`, "utf8");

  await assert.rejects(
    () => evaluateQuotaVoidCommit(params),
    /receipt JSON path is outside its run directory/,
  );
  await assert.rejects(() => readFile(outsidePath), /ENOENT/);

  await writeFile(receipt.path, `${JSON.stringify({
    ...receipt.value,
    status: "prepared",
  }, null, 2)}\n`, "utf8");
  await unlink(String(written.payload.json_path));
  await symlink(outsidePath, String(written.payload.json_path));
  await assert.rejects(
    () => evaluateQuotaVoidCommit(params),
    /JSON artifact must not be a symbolic link/,
  );
  await assert.rejects(() => readFile(outsidePath), /ENOENT/);
});

test("legacy slot coercion truncates numeric strings and clamps both slot floors", async (t) => {
  for (const testCase of [
    { slots: "3.9", spentSlots: "2.9", expectedSlots: 3, expectedAfter: 0 },
    { slots: 0, spentSlots: 4, expectedSlots: 1, expectedAfter: 3 },
    { slots: -4, spentSlots: "invalid", expectedSlots: 1, expectedAfter: 0 },
    { slots: "0x10", spentSlots: 4, expectedSlots: 1, expectedAfter: 3 },
  ]) {
    const fixture = await targetFixture(t, { slots: testCase.slots });
    const result = await evaluateQuotaVoidCommit(await request(fixture, {
      execute: false,
      before: beforeDecision(testCase.spentSlots),
    }));
    const event = result.record?.quota_event as Record<string, unknown>;

    assert.equal(result.status, "preview");
    assert.equal(event.slots, testCase.expectedSlots);
    assert.equal(
      (event.after as Record<string, unknown>).spent_slots,
      testCase.expectedAfter,
    );
  }
});
