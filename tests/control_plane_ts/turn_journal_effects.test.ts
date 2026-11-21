import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";

import { EffectRuntimeConflictError } from "../../loopx/control_plane/effect_runtime_errors.ts";
import {
  acquireFileMutationLock,
  mutationLockOwner,
  releaseFileMutationLock,
} from "../../loopx/control_plane/effect_runtime_io.ts";
import { commitTurnJournal } from "../../loopx/control_plane/turn_driver/turn_journal_effects.ts";
import { interpretTurnJournal } from "../../loopx/control_plane/turn_driver/turn_journal.ts";

const turnKey = `sha256:${"a".repeat(64)}`;
const todoId = "todo_fixture0001";
const instanceA = "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const instanceB = "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const phases = [
  "host_execute",
  "typed_result",
  "validation",
  "durable_writeback",
  "quota_spend",
  "scheduler_apply",
  "scheduler_ack",
] as const;

function effectId(agentId = "fixture-agent", key = turnKey): string {
  return `fixture-goal:${agentId}:${todoId}:${key}`;
}

function journal(
  status = "in_progress",
  completedPhases: readonly string[] = [],
  agentId = "fixture-agent",
  key = turnKey,
): Record<string, unknown> {
  return {
    schema_version: "loopx_turn_journal_v0",
    goal_id: "fixture-goal",
    turn_key: key,
    status,
    completed_phases: [...completedPhases],
    plan: {
      turn_envelope: {
        goal_id: "fixture-goal",
        agent_id: agentId,
        action: { selected_todo: { todo_id: todoId } },
      },
      transaction: {
        turn_key: key,
        settlement_plan: {
          schema_version: "quota_settlement_plan_v1",
          identity: {
            schema_version: "quota_settlement_identity_v0",
            effect_id: effectId(agentId, key),
            goal_id: "fixture-goal",
            agent_id: agentId,
            todo_id: todoId,
            turn_instance_id: key,
          },
        },
      },
    },
  };
}

function sourceJournal(
  goalInstanceId: string,
  key: string,
  completedPhases: readonly string[] = [],
  agentId = "fixture-agent",
): Record<string, unknown> {
  const snapshot = journal("in_progress", completedPhases, agentId, key);
  const plan = snapshot.plan as Record<string, unknown>;
  const goalRef = {
    goal_id: "fixture-goal",
    goal_instance_id: goalInstanceId,
  };
  plan.goal_ref = goalRef;
  (plan.transaction as Record<string, unknown>).goal_ref = goalRef;
  return snapshot;
}

test("sparse journal numbers survive publication, replay and existing guards", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-journal-source-"));
  const path = join(root, "turn.json");
  try {
    const snapshot = {...journal(), source: [1, -0, 2 ** 60], literal: "1.0 and 1152921504606846977"};
    const source_number_tokens = [
      [["source", 0], "1.0"], [["source", 1], "-0.0"], [["source", 2], "1152921504606846977"],
    ];
    const written = await commitTurnJournal({path, journal: snapshot, source_number_tokens, expected_effect_id: effectId()});
    assert.equal(written.appended, true);
    const encoded = await readFile(path, "utf8");
    assert.match(encoded, /1\.0,\n\s+-0\.0,\n\s+1152921504606846977/);
    assert.equal(JSON.parse(encoded).literal, snapshot.literal);
    assert.ok(!encoded.includes("source_number_tokens"));
    const replay = await commitTurnJournal({path, journal: snapshot, source_number_tokens});
    assert.equal(replay.replayed, true);
    assert.equal(replay.operation_id, written.operation_id);
    await assert.rejects(commitTurnJournal({path, journal: journal("committed", phases)}),
      /skip transaction checkpoints/);
    await assert.rejects(commitTurnJournal({path: join(root, "scoped.json"),
      journal: sourceJournal(instanceA, turnKey), source_number_tokens: []}), /require source admission/);
    await assert.rejects(commitTurnJournal({path, journal: snapshot, source_number_tokens, expected_effect_id: "other-effect"}),
      /expected settlement effect/);
    assert.equal(await readFile(path, "utf8"), encoded);
  } finally {
    await rm(root, {recursive: true, force: true});
  }
});

for (const tokens of [null, {}, [[[], "1"]], [[["missing"], "1"]],
  [[["source"], "2"]], [[["source"], "NaN"]], [[["source"], "01"]],
  [[["source"], "1"], [["source"], "1.0"]], [[["literal"], "1"]],
  [[["rows", "0"], "1.0"]], [[["source"], "-0.0"]]]) {
  test(`journal rejects invalid numeric metadata without writing: ${JSON.stringify(tokens)}`, async () => {
    const root = await mkdtemp(join(tmpdir(), "loopx-journal-invalid-"));
    const path = join(root, "turn.json");
    try {
      await assert.rejects(commitTurnJournal({path,
        journal: {...journal(), source: 1, literal: "1", rows: [1]}, source_number_tokens: tokens}),
      /context source number token/);
      await assert.rejects(readFile(path), {code: "ENOENT"});
    } finally {
      await rm(root, {recursive: true, force: true});
    }
  });
}

test("ordinary object journals retain native bytes and operation identity with no numeric metadata", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-journal-native-"));
  try {
    const snapshot = journal(), path = join(root, "turn.json");
    const initial = await commitTurnJournal({path, journal: snapshot});
    const replay = await commitTurnJournal({path, journal: snapshot, source_number_tokens: []});
    assert.equal(await readFile(path, "utf8"), JSON.stringify(snapshot, null, 2) + "\n");
    assert.equal(replay.operation_id, initial.operation_id);
    assert.equal(replay.replayed, true);
  } finally {
    await rm(root, {recursive: true, force: true});
  }
});

test("numeric source differences are not mistaken for replay or immutable-plan equality", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-journal-number-identity-"));
  try {
    const snapshot = journal(), path = join(root, "turn.json");
    (snapshot.plan as Record<string, unknown>).measurement = 1;
    const initial = await commitTurnJournal({path, journal: snapshot,
      source_number_tokens: [[["plan", "measurement"], "1.0"]]});
    await assert.rejects(commitTurnJournal({path, journal: snapshot}), /plan is immutable/);
    const before = await readFile(path, "utf8");
    assert.match(before, /"measurement": 1\.0/);
    const changed = {...snapshot, measurement: 1};
    const next = await commitTurnJournal({path, journal: changed,
      source_number_tokens: [[["plan", "measurement"], "1.0"], [["measurement"], "1.0"]]});
    assert.equal(next.appended, true);
    assert.notEqual(next.operation_id, initial.operation_id);
  } finally {
    await rm(root, {recursive: true, force: true});
  }
});

test("missing raw-number support fails before writing and leaves ordinary objects available", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-journal-number-support-"));
  const descriptor = Object.getOwnPropertyDescriptor(JSON, "rawJSON");
  try {
    Object.defineProperty(JSON, "rawJSON", {value: undefined, configurable: true});
    const path = join(root, "source.json");
    await assert.rejects(commitTurnJournal({path, journal: {...journal(), value: 1},
      source_number_tokens: [[["value"], "1.0"]]}), /requires JSON.rawJSON/);
    await assert.rejects(readFile(path), {code: "ENOENT"});
    assert.equal((await commitTurnJournal({path: join(root, "native.json"), journal: journal()})).ok, true);
  } finally {
    if (descriptor) Object.defineProperty(JSON, "rawJSON", descriptor);
    else Reflect.deleteProperty(JSON, "rawJSON");
    await rm(root, {recursive: true, force: true});
  }
});

function sourceGuardPath(registryPath: string, goalId: string): string {
  const alias = createHash("sha256").update(goalId, "utf8").digest("hex");
  return join(
    dirname(registryPath),
    ".loopx",
    "lifecycle",
    "goal-instance",
    "guards",
    `${alias}.guard`,
  );
}

async function commitSource(
  path: string,
  snapshot: Record<string, unknown>,
  plannedInstanceId: string,
  currentInstanceId = plannedInstanceId,
) {
  const registryPath = join(dirname(path), "project", ".loopx", "registry.json");
  const target = sourceGuardPath(registryPath, "fixture-goal");
  const lock = await acquireFileMutationLock(target);
  try {
    return await commitTurnJournal({
      path,
      journal: snapshot,
      expected_effect_id: effectId(
        "fixture-agent",
        String(snapshot.turn_key),
      ),
      source_admission: {
        schema_version: "loopx_turn_journal_source_admission_v0",
        profile_id: "source_session_v1",
        registry_path: registryPath,
        planned_goal_ref: {
          goal_id: "fixture-goal",
          goal_instance_id: plannedInstanceId,
        },
        authority: {
          kind: "present",
          goal_ref: {
            goal_id: "fixture-goal",
            goal_instance_id: currentInstanceId,
          },
        },
        lock: {
          target,
          pid: process.pid,
          token: lock.token,
        },
      },
    });
  } finally {
    await releaseFileMutationLock(target, lock.token, null, true);
  }
}

async function withJournalPath(
  run: (path: string) => Promise<void>,
): Promise<void> {
  const directory = await mkdtemp(join(tmpdir(), "loopx-ts-journal-"));
  try {
    await run(join(directory, "turn.json"));
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

async function commit(path: string, snapshot: Record<string, unknown>) {
  return await commitTurnJournal({
    path,
    journal: snapshot,
    expected_effect_id: effectId(),
  });
}

test("journal checkpoint retry is idempotent and operation-scoped", async () => {
  await withJournalPath(async (path) => {
    const snapshot = journal();
    const first = await commit(path, snapshot);
    const replay = await commit(path, snapshot);

    assert.equal(first.appended, true);
    assert.equal(first.replayed, false);
    assert.equal(replay.appended, false);
    assert.equal(replay.replayed, true);
    assert.equal(first.operation_id, replay.operation_id);
  });
});

test("contradictory settlement binding cannot create or replace a durable journal", async () => {
  await withJournalPath(async (path) => {
    const valid = journal();
    const invalid = structuredClone(valid);
    const plan = invalid.plan as Record<string, unknown>;
    const transaction = plan.transaction as Record<string, unknown>;
    const settlement = transaction.settlement_plan as Record<string, unknown>;
    Object.assign(settlement.identity as object, {
      binding_kind: "autonomous_replan", binding_id: "other-work",
    });

    await assert.rejects(commit(path, invalid), /settlement|identity/i);
    await assert.rejects(readFile(path), { code: "ENOENT" });
    await commit(path, valid);
    const before = await readFile(path, "utf8");
    await assert.rejects(commit(path, invalid), /settlement|identity/i);
    assert.equal(await readFile(path, "utf8"), before);
    assert.equal((await commit(path, valid)).replayed, true);
  });
});

test("exact GoalRef journals require source admission", async () => {
  await withJournalPath(async (path) => {
    const snapshot = sourceJournal(instanceA, turnKey);
    await assert.rejects(
      commitTurnJournal({
        path,
        journal: snapshot,
        expected_effect_id: effectId(),
      }),
      /source admission/,
    );
  });
});

test("source admission must be well formed and hold a live guard", async () => {
  await withJournalPath(async (path) => {
    const snapshot = sourceJournal(instanceA, turnKey);
    await assert.rejects(
      commitTurnJournal({
        path,
        journal: snapshot,
        expected_effect_id: effectId(),
        source_admission: {},
      }),
      /source admission is malformed/,
    );

    const registryPath = join(
      dirname(path),
      "project",
      ".loopx",
      "registry.json",
    );
    const target = sourceGuardPath(registryPath, "fixture-goal");
    const lock = await acquireFileMutationLock(target);
    await releaseFileMutationLock(target, lock.token);
    await assert.rejects(
      commitTurnJournal({
        path,
        journal: snapshot,
        expected_effect_id: effectId(),
        source_admission: {
          schema_version: "loopx_turn_journal_source_admission_v0",
          profile_id: "source_session_v1",
          registry_path: registryPath,
          planned_goal_ref: {
            goal_id: "fixture-goal",
            goal_instance_id: instanceA,
          },
          authority: {
            kind: "present",
            goal_ref: {
              goal_id: "fixture-goal",
              goal_instance_id: instanceA,
            },
          },
          lock: {
            target,
            pid: process.pid,
            token: lock.token,
          },
        },
      }),
      (error: unknown) => {
        assert.ok(error instanceof EffectRuntimeConflictError);
        assert.equal(error.code, "journal_source_admission_expired");
        return true;
      },
    );
    await assert.rejects(readFile(path), { code: "ENOENT" });
  });
});

test("source admission rejects stale Goal A without mutating A or B", async () => {
  const directory = await mkdtemp(join(tmpdir(), "loopx-ts-source-journal-"));
  const aKey = `sha256:${"a".repeat(64)}`;
  const bKey = `sha256:${"b".repeat(64)}`;
  const aPath = join(
    directory,
    "runtime",
    "goals",
    "fixture-goal",
    "turns",
    `${aKey.slice("sha256:".length)}.json`,
  );
  const bPath = join(
    directory,
    "runtime",
    "goals",
    "fixture-goal",
    "turns",
    `${bKey.slice("sha256:".length)}.json`,
  );
  try {
    const initialA = sourceJournal(instanceA, aKey);
    const first = await commitSource(aPath, initialA, instanceA);
    const replay = await commitSource(aPath, initialA, instanceA);
    assert.equal(first.appended, true);
    assert.equal(replay.replayed, true);

    const beforeA = await readFile(aPath, "utf8");
    await assert.rejects(
      commitSource(
        aPath,
        sourceJournal(instanceA, aKey, phases.slice(0, 2)),
        instanceA,
        instanceB,
      ),
      (error: unknown) => {
        assert.ok(error instanceof EffectRuntimeConflictError);
        assert.equal(error.code, "stale_goal_instance");
        return true;
      },
    );
    assert.equal(await readFile(aPath, "utf8"), beforeA);

    const currentB = sourceJournal(instanceB, bKey);
    const committedB = await commitSource(bPath, currentB, instanceB);
    assert.equal(committedB.appended, true);
    assert.deepEqual(JSON.parse(await readFile(bPath, "utf8")), currentB);
    assert.equal(await readFile(aPath, "utf8"), beforeA);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("source journal plan GoalRef copies must agree", async () => {
  await withJournalPath(async (path) => {
    const snapshot = sourceJournal(instanceA, turnKey);
    const plan = snapshot.plan as Record<string, unknown>;
    (plan.transaction as Record<string, unknown>).goal_ref = {
      goal_id: "fixture-goal",
      goal_instance_id: instanceB,
    };
    const inspection = interpretTurnJournal({
      schema_version: "loopx_turn_journal_interpretation_request_v0",
      journal: snapshot,
      goal_id: "fixture-goal",
      agent_id: "fixture-agent",
      turn_key: turnKey,
    });
    assert.ok(
      inspection.violations.includes("goal_ref_binding_mismatch"),
    );
    assert.equal(inspection.journal_consistent, false);
    await assert.rejects(
      commitSource(path, snapshot, instanceA),
      /GoalRef/,
    );
  });
});

test("legacy journals reject source admission without changing their wire", async () => {
  await withJournalPath(async (path) => {
    const snapshot = journal();
    await assert.rejects(
      commitTurnJournal({
        path,
        journal: snapshot,
        expected_effect_id: effectId(),
        source_admission: {},
      }),
      /Legacy Turn journals cannot carry source admission/,
    );
    const committed = await commit(path, snapshot);
    assert.equal(committed.appended, true);
    assert.deepEqual(JSON.parse(await readFile(path, "utf8")), snapshot);
  });
});

interface NativeJournalFixture {
  runtimeRoot: string;
  path: string;
  registries: string[];
  admission: Record<string, unknown>;
  locks: Record<string, unknown>[];
}

async function withNativeJournal(
  run: (fixture: NativeJournalFixture) => Promise<void>,
  sourceRoute = false,
): Promise<void> {
  const directory = await mkdtemp(join(tmpdir(), "loopx-ts-native-journal-"));
  const runtimeRoot = join(directory, "runtime");
  const invoked = join(directory, "project", ".loopx", "registry.json");
  const registries = sourceRoute ? [join(directory, "source", ".loopx", "registry.json"), invoked] : [invoked];
  const targets = [join(runtimeRoot, "goals", "fixture-goal", "runs", "index.jsonl"), ...registries];
  const locks: Record<string, unknown>[] = [];
  try {
    for (const [index, path] of registries.entries()) {
      await mkdir(dirname(path), {recursive: true});
      await writeFile(path, JSON.stringify({schema_version: "0.1", registry_role: "project-local",
        common_runtime_root: runtimeRoot, goals: [{id: "fixture-goal", goal_instance_id: instanceA,
          status: "active", coordination: {registered_agents: ["fixture-agent", "other-registered-agent"]},
          ...(sourceRoute && index === 1 ? {source_registry: registries[0]} : {})}]}));
    }
    for (const [index, target] of targets.entries()) {
      const lock = await acquireFileMutationLock(target);
      locks.push({role: index === 0 ? "run_index" : "registry_source", target, pid: process.pid, token: lock.token});
    }
    await run({runtimeRoot, path: join(runtimeRoot, "goals", "fixture-goal", "turns", `${"a".repeat(64)}.json`),
      registries, locks, admission: {schema_version: "loopx_quota_native_admission_v0", registry_path: invoked,
        runtime_root: runtimeRoot, planned_goal_ref: {goal_id: "fixture-goal", goal_instance_id: instanceA},
        agent_id: "fixture-agent", registry_sources: await Promise.all(registries.map(async path => ({path,
          sha256: createHash("sha256").update(await readFile(path)).digest("hex")}))), locks}});
  } finally {
    for (const lock of [...locks].reverse()) {
      await releaseFileMutationLock(String(lock.target), String(lock.token), null, true);
    }
    await rm(directory, {recursive: true, force: true});
  }
}

function nativeJournalRequest(fixture: NativeJournalFixture, snapshot = sourceJournal(instanceA, turnKey)) {
  return {path: fixture.path, journal: snapshot, expected_effect_id: effectId(), source_admission: fixture.admission};
}

for (const sourceRoute of [false, true]) {
  test(`native exact journal ${sourceRoute ? "source and invoked registries" : "project registry"} preserves numbers and borrowed locks`, async () => {
    await withNativeJournal(async fixture => {
      // Only metadata resources are held: no Todo lease, prepared receipt,
      // maintenance fence or heartbeat publication grants this journal write.
      const snapshot: Record<string, unknown> = {...sourceJournal(instanceA, turnKey), values: [1, -0, 2 ** 60]};
      (snapshot.plan as Record<string, unknown>).measurement = 1;
      const request = {...nativeJournalRequest(fixture, snapshot), source_number_tokens: [
        [["values", 0], "1.0"], [["values", 1], "-0.0"], [["values", 2], "1152921504606846977"],
        [["plan", "measurement"], "1.0"],
      ]};
      const written = await commitTurnJournal(request);
      const encoded = await readFile(fixture.path, "utf8");
      assert.equal(written.appended, true);
      assert.match(encoded, /1\.0,\n\s+-0\.0,\n\s+1152921504606846977/);
      assert.match(encoded, /"measurement": 1\.0/);
      assert.ok(!encoded.includes("source_number_tokens"));
      const replay = await commitTurnJournal(request);
      assert.equal(replay.replayed, true);
      assert.equal(replay.operation_id, written.operation_id);
      await assert.rejects(commitTurnJournal(nativeJournalRequest(fixture, snapshot)), /plan is immutable/);
      assert.equal(await readFile(fixture.path, "utf8"), encoded);
      for (const witness of fixture.locks) {
        const owner = await mutationLockOwner(String(witness.target));
        assert.equal(owner?.pid, witness.pid);
        assert.equal(owner?.token, witness.token);
      }
    }, sourceRoute);
  });
}

test("native exact journals retain plan, checkpoint and terminal guards", async () => {
  await withNativeJournal(async fixture => {
    const initial = nativeJournalRequest(fixture);
    await commitTurnJournal(initial);
    const encoded = await readFile(fixture.path, "utf8");
    const changedPlan = sourceJournal(instanceA, turnKey);
    (changedPlan.plan as Record<string, unknown>).host = {kind: "changed-after-start"};
    await assert.rejects(commitTurnJournal(nativeJournalRequest(fixture, changedPlan)), /transaction plan is immutable/);
    await assert.rejects(commitTurnJournal(nativeJournalRequest(fixture,
      {...sourceJournal(instanceA, turnKey, phases), status: "committed"})), /skip transaction checkpoints/);
    assert.equal(await readFile(fixture.path, "utf8"), encoded);
    for (const count of [2, 3, 4, 5]) {
      await commitTurnJournal(nativeJournalRequest(fixture, sourceJournal(instanceA, turnKey, phases.slice(0, count))));
      if (count === 3) {
        const checkpoint = await readFile(fixture.path, "utf8");
        await assert.rejects(commitTurnJournal(nativeJournalRequest(fixture,
          sourceJournal(instanceA, turnKey, phases.slice(0, 2)))), /completed phases cannot regress or fork/);
        assert.equal(await readFile(fixture.path, "utf8"), checkpoint);
      }
    }
    const committed = {...sourceJournal(instanceA, turnKey, phases), status: "committed"};
    await commitTurnJournal(nativeJournalRequest(fixture, committed));
    const terminal = await readFile(fixture.path, "utf8");
    assert.equal((await commitTurnJournal(nativeJournalRequest(fixture, committed))).replayed, true);
    await assert.rejects(commitTurnJournal(nativeJournalRequest(fixture, {...committed, reason: "late mutation"})),
      /tombstones are immutable/);
    assert.equal(await readFile(fixture.path, "utf8"), terminal);
  });
});

const nativeJournalRejections: {
  name: string;
  request: (fixture: NativeJournalFixture) => Record<string, unknown>;
  code: string;
}[] = [
  {name: "foreign planned GoalRef", request: f => ({source_admission: {...f.admission,
    planned_goal_ref: {goal_id: "fixture-goal", goal_instance_id: instanceB}}}), code: "quota_source_admission_invalid"},
  {name: "foreign journal GoalRef", request: () => ({journal: sourceJournal(instanceB, turnKey)}), code: "quota_source_admission_invalid"},
  {name: "foreign registered admission Agent", request: f => ({source_admission: {...f.admission,
    agent_id: "other-registered-agent"}}), code: "quota_source_admission_invalid"},
  {name: "foreign registered journal Agent", request: () => ({expected_effect_id: effectId("other-registered-agent"),
    journal: sourceJournal(instanceA, turnKey, [], "other-registered-agent")}), code: "quota_source_admission_invalid"},
  {name: "missing admission Agent", request: f => ({source_admission: {...f.admission, agent_id: undefined}}), code: "quota_source_admission_invalid"},
  {name: "unregistered admission Agent", request: f => ({source_admission: {...f.admission, agent_id: "unregistered-agent"}}), code: "goal_instance_conflict"},
  {name: "foreign runtime", request: f => ({source_admission: {...f.admission, runtime_root: join(f.runtimeRoot, "foreign")}}), code: "journal_source_admission_invalid"},
  {name: "relative runtime", request: f => ({source_admission: {...f.admission, runtime_root: "runtime"}}), code: "journal_source_admission_invalid"},
  {name: "foreign physical Goal path", request: f => ({path: join(f.runtimeRoot, "goals", "other-goal", "turns", `${"a".repeat(64)}.json`)}), code: "journal_source_admission_invalid"},
  {name: "foreign Turn path", request: f => ({path: join(dirname(f.path), `${"b".repeat(64)}.json`)}), code: "journal_source_admission_invalid"},
  {name: "unstripped Turn filename", request: f => ({path: join(dirname(f.path), `${turnKey}.json`)}), code: "journal_source_admission_invalid"},
  {name: "malformed Turn key", request: () => ({expected_effect_id: effectId("fixture-agent", "ordinary-turn"),
    journal: sourceJournal(instanceA, "ordinary-turn")}), code: "journal_source_admission_invalid"},
  {name: "alias journal admission upgrade", request: () => ({journal: journal()}), code: "journal_source_admission_invalid"},
  {name: "foreign run-index lock", request: f => ({source_admission: {...f.admission,
    locks: f.locks.map((lock, index) => index === 0 ? {...lock, target: join(f.runtimeRoot, "other-index.jsonl")} : lock)}}), code: "quota_source_admission_invalid"},
  {name: "foreign registry lock token", request: f => ({source_admission: {...f.admission,
    locks: f.locks.map((lock, index) => index === 1 ? {...lock, token: "foreign-lock-token"} : lock)}}), code: "quota_source_admission_expired"},
  {name: "foreign lock host", request: f => ({source_admission: {...f.admission,
    locks: f.locks.map(lock => ({...lock, pid: process.pid + 1}))}}), code: "quota_source_admission_expired"},
  {name: "missing registry lock", request: f => ({source_admission: {...f.admission, locks: f.locks.slice(0, 1)}}), code: "quota_source_admission_invalid"},
  {name: "changed registry byte witness", request: f => ({source_admission: {...f.admission,
    registry_sources: [{path: f.registries[0], sha256: "0".repeat(64)}]}}), code: "goal_instance_conflict"},
];

for (const rejection of nativeJournalRejections) {
  test(`native exact journal rejects ${rejection.name} before creation and replacement`, async () => {
    for (const existing of [false, true]) {
      await withNativeJournal(async fixture => {
        if (existing) await commitTurnJournal(nativeJournalRequest(fixture));
        const before = existing ? await readFile(fixture.path, "utf8") : null;
        const request = {...nativeJournalRequest(fixture), ...rejection.request(fixture)};
        await assert.rejects(commitTurnJournal(request), {code: rejection.code});
        if (before === null) await assert.rejects(readFile(fixture.path), {code: "ENOENT"});
        else assert.equal(await readFile(fixture.path, "utf8"), before);
        if (request.path !== fixture.path && rejection.name !== "unstripped Turn filename") {
          await assert.rejects(readFile(String(request.path)), {code: "ENOENT"});
        }
      });
    }
  });
}

test("native exact journal rejects expired resources and changed persisted identity without replacing bytes", async () => {
  for (const changed of ["run_index", "registry_source", "registry_bytes", "GoalRef", "Source profile"]) {
    await withNativeJournal(async fixture => {
      await commitTurnJournal(nativeJournalRequest(fixture));
      const encoded = await readFile(fixture.path, "utf8");
      if (changed === "run_index" || changed === "registry_source") {
        const lock = fixture.locks.find(witness => witness.role === changed)!;
        await releaseFileMutationLock(String(lock.target), String(lock.token));
      } else {
        const registry = fixture.registries[0]!;
        const document = JSON.parse(await readFile(registry, "utf8"));
        if (changed === "GoalRef") document.goals[0].goal_instance_id = instanceB;
        else if (changed === "Source profile") document.profile_id = "source_session_v1";
        else document.description = "changed bytes";
        await writeFile(registry, JSON.stringify(document));
        if (changed !== "registry_bytes") fixture.admission.registry_sources = [{path: registry,
          sha256: createHash("sha256").update(await readFile(registry)).digest("hex")}];
      }
      await assert.rejects(commitTurnJournal(nativeJournalRequest(fixture)), {
        code: changed === "run_index" || changed === "registry_source" ? "quota_source_admission_expired" : "goal_instance_conflict",
      });
      assert.equal(await readFile(fixture.path, "utf8"), encoded);
    });
  }
});

test("TS journal owner accepts the complete monotonic transaction", async () => {
  await withJournalPath(async (path) => {
    await commit(path, journal());
    await commit(path, journal("in_progress", phases.slice(0, 2)));
    await commit(path, journal("in_progress", phases.slice(0, 3)));

    const preparedWriteback = journal("in_progress", phases.slice(0, 3));
    preparedWriteback.effect_attempts = {
      durable_writeback: {
        status: "prepared",
        effect_ref: `${effectId()}#durable_writeback`,
      },
    };
    await commit(path, preparedWriteback);
    await commit(path, journal("in_progress", phases.slice(0, 4)));

    const preparedSpend = journal("in_progress", phases.slice(0, 4));
    preparedSpend.effect_attempts = {
      quota_spend: {
        status: "prepared",
        effect_ref: `${effectId()}#quota_spend`,
      },
    };
    await commit(path, preparedSpend);
    await commit(path, journal("in_progress", phases.slice(0, 5)));
    await commit(path, journal("scheduler_action_required", phases.slice(0, 5)));
    await commit(path, journal("committed", phases));

    assert.equal(JSON.parse(await readFile(path, "utf8")).status, "committed");
  });
});

test("new journals cannot appear after side effects", async () => {
  await withJournalPath(async (path) => {
    await assert.rejects(
      commit(path, journal("in_progress", phases.slice(0, 3))),
      /must begin in progress with no completed phases/,
    );
  });
});

test("completed phases cannot regress", async () => {
  await withJournalPath(async (path) => {
    await commit(path, journal());
    await commit(path, journal("in_progress", phases.slice(0, 2)));
    await commit(path, journal("in_progress", phases.slice(0, 3)));
    await assert.rejects(
      commit(path, journal("in_progress", phases.slice(0, 2))),
      /completed phases cannot regress or fork/,
    );
  });
});

test("completed phases cannot skip transaction checkpoints", async () => {
  await withJournalPath(async (path) => {
    await commit(path, journal());
    await assert.rejects(
      commit(path, journal("committed", phases)),
      /cannot skip transaction checkpoints/,
    );
  });
});

test("failed validation may explicitly rewind for host reinvocation", async () => {
  await withJournalPath(async (path) => {
    await commit(path, journal());
    const failed = journal("failed", phases.slice(0, 2));
    failed.receipt = { turn_key: turnKey, failed_phase: "validation" };
    await commit(path, failed);
    await commit(path, journal("in_progress", []));

    assert.deepEqual(JSON.parse(await readFile(path, "utf8")).completed_phases, []);
  });
});

test("terminal journal tombstones are immutable", async () => {
  await withJournalPath(async (path) => {
    await commit(path, journal());
    await commit(path, journal("in_progress", phases.slice(0, 2)));
    await commit(path, journal("in_progress", phases.slice(0, 3)));
    await commit(path, journal("in_progress", phases.slice(0, 4)));
    await commit(path, journal("in_progress", phases.slice(0, 5)));
    await commit(path, journal("committed", phases));
    const changed = journal("committed", phases);
    changed.reason = "late mutation";
    await assert.rejects(commit(path, changed), /tombstones are immutable/);
  });
});

test("transaction plans are immutable within one effect", async () => {
  await withJournalPath(async (path) => {
    await commit(path, journal());
    const changed = journal("in_progress", phases.slice(0, 2));
    const plan = changed.plan as Record<string, unknown>;
    plan.host = { kind: "changed-after-start" };
    await assert.rejects(commit(path, changed), /transaction plan is immutable/);
  });
});

test("cross-effect overwrite remains fail-closed", async () => {
  await withJournalPath(async (path) => {
    await commit(path, journal());
    await assert.rejects(
      commitTurnJournal({ path, journal: journal("in_progress", [], "other-agent") }),
      /belongs to another settlement effect/,
    );
  });
});

test("prepared effects must use the settlement identity", async () => {
  await withJournalPath(async (path) => {
    const invalid = journal();
    invalid.effect_attempts = {
      durable_writeback: {
        status: "prepared",
        effect_ref: "another-effect#durable_writeback",
      },
    };
    await assert.rejects(
      commit(path, invalid),
      /prepared effect does not match settlement identity/,
    );
  });
});

test("inspector and writer share prepared-intent rejection and valid controls", async () => {
  const prepared = {status: "prepared", effect_ref: `${effectId()}#durable_writeback`};
  const cases = [
    {attempts: {unknown_provider_step: prepared}, code: "prepared_effect_step_unsupported"},
    {attempts: [], code: "prepared_effect_attempts_invalid"},
    {attempts: null, code: "prepared_effect_attempts_invalid"},
    {attempts: {}, code: "prepared_effect_count_invalid"},
    {attempts: {durable_writeback: prepared, quota_spend: prepared}, code: "prepared_effect_count_invalid"},
    {attempts: {durable_writeback: null}, code: "prepared_effect_identity_invalid"},
    {attempts: {durable_writeback: {...prepared, status: "committed"}}, code: "prepared_effect_identity_invalid"},
    {attempts: {durable_writeback: {...prepared, effect_ref: "foreign#durable_writeback"}}, code: "prepared_effect_identity_invalid"},
    {attempts: {quota_spend: {status: "prepared", effect_ref: `${effectId()}#quota_spend`}}, code: "prepared_effect_phase_invalid"},
  ];
  await withJournalPath(async (path) => {
    await commit(path, journal());
    await commit(path, journal("in_progress", phases.slice(0, 2)));
    await commit(path, journal("in_progress", phases.slice(0, 3)));
    const inspect = (snapshot: Record<string, unknown>) => interpretTurnJournal({
      schema_version: "loopx_turn_journal_interpretation_request_v0",
      journal: snapshot, goal_id: "fixture-goal", agent_id: "fixture-agent", turn_key: turnKey,
    });
    const before = await readFile(path, "utf8");
    for (const {attempts, code} of cases) {
      const invalid = {...journal("in_progress", phases.slice(0, 3)), effect_attempts: attempts};
      const result = inspect(invalid);
      assert.ok(result.violations.includes(code), code);
      assert.equal(result.journal_consistent, false, code);
      assert.equal(result.recovery_decision.can_continue, false, code);
      assert.equal(result.recovery_decision.reinvoke_host, false, code);
      assert.equal(result.recorded_effects.host_invoked, true, code);
      assert.equal(result.recorded_effects.state_written, null, code);
      assert.equal(result.recorded_effects.quota_spent, null, code);
      assert.deepEqual(result.effects, []);
      await assert.rejects(commit(path, invalid), /Turn journal/, code);
      assert.equal(await readFile(path, "utf8"), before, code);
    }
    assert.equal(inspect(journal("in_progress", phases.slice(0, 3))).journal_consistent, true);
    const valid = {...journal("in_progress", phases.slice(0, 3)), effect_attempts: {durable_writeback: prepared}};
    assert.equal(inspect(valid).recovery_decision.reason, "resolve_prepared_effect");
    await commit(path, valid);
    const validCloseout = {...journal("in_progress", phases.slice(0, 5)), effect_attempts: {
      terminal_closeout: {status: "prepared", effect_ref: `${effectId()}#terminal_closeout`},
    }};
    assert.equal(inspect(validCloseout).journal_consistent, true);
    const invalidTerminalStatus = {...validCloseout, status: "committed"};
    assert.ok(inspect(invalidTerminalStatus).violations.includes("prepared_effect_status_invalid"));
    await assert.rejects(commit(path, invalidTerminalStatus), /terminal state cannot retain/);
    const terminalIntent = {...journal("committed", phases), effect_attempts: {
      terminal_closeout: {status: "prepared", effect_ref: `${effectId()}#terminal_closeout`},
    }};
    assert.equal(inspect(terminalIntent).journal_consistent, false);
    await assert.rejects(commit(path, terminalIntent), /prepared effect/);
    assert.equal(inspect(journal("committed", phases)).recovery_decision.action, "return_existing");
  });
});

test("failed snapshots must name the next uncompleted phase", async () => {
  await withJournalPath(async (path) => {
    const invalid = journal("failed", phases.slice(0, 4));
    invalid.receipt = { turn_key: turnKey, failed_phase: "validation" };
    await assert.rejects(
      commit(path, invalid),
      /must name the next uncompleted phase/,
    );
  });
});
