import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";

import {
  evaluateSchedulerStateOperation,
  loadSchedulerState,
  normalizeSchedulerState,
  schedulerStatePath,
  SCHEDULER_STATE_OPERATION_REQUEST_SCHEMA,
  SCHEDULER_STATE_STORE_REQUEST_SCHEMA,
  writeSchedulerState,
} from "../../loopx/control_plane/scheduler/state_store.ts";

const fixture = JSON.parse(
  await readFile(
    new URL(
      "../fixtures/control_plane/scheduler_state_store_characterization_v0.json",
      import.meta.url,
    ),
    "utf8",
  ),
) as {
  schema_version: string;
  source_baseline: string;
  cases: Array<Record<string, unknown>>;
};

const scope = {
  goalId: "goal-a",
  agentId: "agent-a",
  surface: "codex_app",
  stateKey: "scheduler_hint.app_automation.stateful_backoff",
};

function state(lastAppliedRrule = "FREQ=MINUTELY;INTERVAL=3") {
  return {
    schema_version: "loopx_scheduler_state_v0",
    goal_id: scope.goalId,
    agent_id: scope.agentId,
    surface: scope.surface,
    state_key: scope.stateKey,
    reset_token: "reset",
    identity_signature: "identity",
    progression_index: 0,
    progression_minutes: [3, 15, 30],
    last_applied_rrule: lastAppliedRrule,
    updated_at: "2026-01-01T12:00:00Z",
  };
}

function storeRequest(runtimeRoot: string, extra: Record<string, unknown> = {}) {
  return {
    schema_version: SCHEDULER_STATE_STORE_REQUEST_SCHEMA,
    runtime_root: runtimeRoot,
    goal_id: scope.goalId,
    agent_id: scope.agentId,
    surface: scope.surface,
    state_key: scope.stateKey,
    ...extra,
  };
}

test("pinned Python scheduler state characterization remains exact", () => {
  assert.equal(
    fixture.schema_version,
    "loopx_scheduler_state_store_characterization_v0",
  );
  assert.equal(fixture.source_baseline, "8b255e1d1");
  assert.equal(fixture.cases.length, 9);
  for (const item of fixture.cases) {
    const result = evaluateSchedulerStateOperation({
      schema_version: SCHEDULER_STATE_OPERATION_REQUEST_SCHEMA,
      operation: item.operation,
      ...(item.params as Record<string, unknown>),
    }).value;
    if ("expected" in item) assert.deepEqual(result, item.expected, String(item.name));
    if ("expected_targets" in item) {
      assert.deepEqual(
        (result as Array<Record<string, unknown>>).map((entry) => entry.target_rrule),
        item.expected_targets,
        String(item.name),
      );
    }
    if ("expected_last_count" in item) {
      assert.equal(
        (result as Array<Record<string, unknown>>).at(-1)?.failure_count,
        item.expected_last_count,
        String(item.name),
      );
    }
    if ("expected_progression_index" in item) {
      const normalized = result as Record<string, unknown>;
      assert.equal(normalized.progression_index, item.expected_progression_index);
      assert.deepEqual(normalized.progression_minutes, item.expected_progression_minutes);
      assert.equal(normalized.last_applied_rrule, item.expected_last_applied_rrule);
      assert.equal(
        (normalized.host_update_failures as unknown[]).length,
        item.expected_failure_count,
      );
      assert.equal(normalized.extra, item.expected_extra);
    }
  }
});

test("typed boundary rejects malformed requests and illegal state", () => {
  assert.throws(
    () => evaluateSchedulerStateOperation({
      schema_version: "unsupported",
      operation: "normalize_rrule",
      value: "RRULE:FREQ=MINUTELY;INTERVAL=3",
    }),
    /request schema mismatch/,
  );
  assert.throws(
    () => evaluateSchedulerStateOperation({
      schema_version: SCHEDULER_STATE_OPERATION_REQUEST_SCHEMA,
      operation: "delete_state",
    }),
    /Scheduler state operation is unsupported/,
  );
  assert.throws(
    () => evaluateSchedulerStateOperation({
      schema_version: SCHEDULER_STATE_OPERATION_REQUEST_SCHEMA,
      operation: "build_state",
      goal_id: scope.goalId,
      agent_id: scope.agentId,
      surface: scope.surface,
      state_key: scope.stateKey,
      progression_index: -1,
      progression_minutes: [],
    }),
    /missing required persisted-state fields/,
  );
  assert.equal(
    normalizeSchedulerState(
      { ...state(), progression_minutes: [3, 0] },
      scope,
    ),
    null,
  );
});

test("failure retention accepts the ISO offsets persisted by Python", () => {
  const result = evaluateSchedulerStateOperation({
    schema_version: SCHEDULER_STATE_OPERATION_REQUEST_SCHEMA,
    operation: "retain_failures",
    value: [{
      schema_version: "scheduler_host_update_failure_v0",
      target_rrule: "FREQ=MINUTELY;INTERVAL=3",
      observed_host_rrule: "FREQ=MINUTELY;INTERVAL=30",
      failure_kind: "timeout",
      failure_count: 1,
      failed_at: "2026-01-01T12:00:00+0000",
    }],
    reference_time: "2026-01-02T12:00:00+00:00",
  }).value;
  assert.equal((result as unknown[]).length, 1);
});

test("pure scheduler state operations do not mutate caller input", () => {
  const params = fixture.cases[7].params as Record<string, unknown>;
  const before = structuredClone(params);
  evaluateSchedulerStateOperation({
    schema_version: SCHEDULER_STATE_OPERATION_REQUEST_SCHEMA,
    operation: "normalize_state",
    ...params,
  });
  assert.deepEqual(params, before);
});

test("state path is scoped, sanitized, and stable", () => {
  const path = schedulerStatePath("/runtime", {
    ...scope,
    goalId: "../goal with spaces",
    agentId: "agent/../../other",
  });
  assert.equal(
    path,
    join(
      "/runtime",
      "goals",
      "goal-with-spaces-116e9296329bcdc8",
      "scheduler-state",
      "agent-..-..-other-55571d8866830ec6",
      "codex_app-b32e6f37f5dad64e",
      "8a41f410c67e7c0e.json",
    ),
  );
});

test("sanitized-equivalent scheduler scopes have distinct bounded paths", () => {
  const goalPaths = ["a b", "a-b", "非 ASCII", "---"].map((goalId) =>
    schedulerStatePath("/runtime", { ...scope, goalId })
  );
  const agentPaths = ["agent/name", "agent-name"].map((agentId) =>
    schedulerStatePath("/runtime", { ...scope, agentId })
  );
  const surfacePaths = ["codex app", "codex-app"].map((surface) =>
    schedulerStatePath("/runtime", { ...scope, surface })
  );
  assert.equal(new Set(goalPaths).size, goalPaths.length);
  assert.equal(new Set(agentPaths).size, agentPaths.length);
  assert.equal(new Set(surfacePaths).size, surfacePaths.length);
  for (const path of [...goalPaths, ...agentPaths, ...surfacePaths]) {
    for (const segment of path.split(/[\\/]/)) assert.ok(segment.length <= 64);
  }
});

test("legacy scheduler state is migrated once into the collision-safe layout", async (t) => {
  const runtimeRoot = await mkdtemp(join(tmpdir(), "loopx-scheduler-state-"));
  t.after(() => rm(runtimeRoot, { recursive: true, force: true }));
  const legacyPath = join(
    runtimeRoot,
    "goals",
    scope.goalId,
    "scheduler-state",
    scope.agentId,
    scope.surface,
    "8a41f410c67e7c0e.json",
  );
  await writeSchedulerState(storeRequest(runtimeRoot, { state: state() }));
  const canonicalPath = schedulerStatePath(runtimeRoot, scope);
  const persisted = await readFile(canonicalPath, "utf8");
  await rm(canonicalPath);
  await mkdir(dirname(legacyPath), { recursive: true });
  await writeFile(legacyPath, persisted, "utf8");

  const migrated = await loadSchedulerState(storeRequest(runtimeRoot));

  assert.deepEqual(migrated.state, state());
  assert.equal(migrated.path, canonicalPath);
  assert.deepEqual(JSON.parse(await readFile(canonicalPath, "utf8")), state());
  await assert.rejects(readFile(legacyPath, "utf8"), { code: "ENOENT" });
});

test("overlong legacy scope loads as missing state, not an effect rejection", async (t) => {
  const runtimeRoot = await mkdtemp(join(tmpdir(), "loopx-scheduler-state-"));
  t.after(() => rm(runtimeRoot, { recursive: true, force: true }));
  // A legacy scope component beyond the filesystem path-component limit cannot
  // exist in the legacy layout; the canonical bounded path has no state yet,
  // so the first load must return missing state instead of ENAMETOOLONG.
  const overlongGoalId = "g".repeat(300);
  const request = storeRequest(runtimeRoot, { goal_id: overlongGoalId });

  const result = await loadSchedulerState(request);

  assert.equal(result.state, null);
  const canonical = schedulerStatePath(runtimeRoot, {
    ...scope,
    goalId: overlongGoalId,
  });
  assert.ok(
    !canonical.includes("g".repeat(256)),
    "canonical path components must stay bounded",
  );
  assert.ok(
    Math.max(...canonical.split(/[\\/]/).map((part) => part.length)) <= 64,
    "canonical path component must not exceed the scoped-segment bound",
  );
});

test("concurrent writes for sanitized-equivalent scopes stay isolated", async (t) => {
  const runtimeRoot = await mkdtemp(join(tmpdir(), "loopx-scheduler-state-"));
  t.after(() => rm(runtimeRoot, { recursive: true, force: true }));
  const firstGoalId = "a b";
  const secondGoalId = "a-b";
  const firstState = { ...state(), goal_id: firstGoalId };
  const secondState = { ...state(), goal_id: secondGoalId };

  await Promise.all([
    writeSchedulerState(storeRequest(runtimeRoot, {
      goal_id: firstGoalId,
      state: firstState,
    })),
    writeSchedulerState(storeRequest(runtimeRoot, {
      goal_id: secondGoalId,
      state: secondState,
    })),
  ]);

  assert.deepEqual(
    (await loadSchedulerState(storeRequest(runtimeRoot, { goal_id: firstGoalId }))).state,
    firstState,
  );
  assert.deepEqual(
    (await loadSchedulerState(storeRequest(runtimeRoot, { goal_id: secondGoalId }))).state,
    secondState,
  );
});

test("state store writes atomically and recognizes replay", async (t) => {
  const runtimeRoot = await mkdtemp(join(tmpdir(), "loopx-scheduler-state-"));
  t.after(() => rm(runtimeRoot, { recursive: true, force: true }));
  const first = await writeSchedulerState(
    storeRequest(runtimeRoot, { state: state() }),
  );
  const replay = await writeSchedulerState(
    storeRequest(runtimeRoot, { state: state() }),
  );
  assert.equal(first.written, true);
  assert.equal(first.replayed, false);
  assert.equal(replay.written, false);
  assert.equal(replay.replayed, true);
  assert.deepEqual(
    (await loadSchedulerState(storeRequest(runtimeRoot))).state,
    state(),
  );
});

test("malformed persisted JSON is a missing state, not partial truth", async (t) => {
  const runtimeRoot = await mkdtemp(join(tmpdir(), "loopx-scheduler-state-"));
  t.after(() => rm(runtimeRoot, { recursive: true, force: true }));
  const path = schedulerStatePath(runtimeRoot, scope);
  await writeSchedulerState(storeRequest(runtimeRoot, { state: state() }));
  await writeFile(path, "{truncated", "utf8");
  assert.equal((await loadSchedulerState(storeRequest(runtimeRoot))).state, null);
});

test("concurrent same-key writes serialize without partial state", async (t) => {
  const runtimeRoot = await mkdtemp(join(tmpdir(), "loopx-scheduler-state-"));
  t.after(() => rm(runtimeRoot, { recursive: true, force: true }));
  const first = state("FREQ=MINUTELY;INTERVAL=15");
  const second = state("FREQ=MINUTELY;INTERVAL=30");
  await Promise.all([
    writeSchedulerState(storeRequest(runtimeRoot, { state: first })),
    writeSchedulerState(storeRequest(runtimeRoot, { state: second })),
  ]);
  const path = schedulerStatePath(runtimeRoot, scope);
  const persisted = JSON.parse(await readFile(path, "utf8"));
  assert.ok(
    persisted.last_applied_rrule === first.last_applied_rrule ||
      persisted.last_applied_rrule === second.last_applied_rrule,
  );
  assert.deepEqual(
    (await loadSchedulerState(storeRequest(runtimeRoot))).state,
    persisted,
  );
  assert.deepEqual(
    (await readdir(join(path, ".."))).filter((name) =>
      name.includes(".tmp") || name.includes(".lock")
    ),
    [],
  );
});

test("a dead writer lock is reclaimed before retrying the same state key", async (t) => {
  const runtimeRoot = await mkdtemp(join(tmpdir(), "loopx-scheduler-state-"));
  t.after(() => rm(runtimeRoot, { recursive: true, force: true }));
  const path = schedulerStatePath(runtimeRoot, scope);
  await writeSchedulerState(storeRequest(runtimeRoot, { state: state() }));
  await writeFile(
    `${path}.ts-effect.lock`,
    JSON.stringify({ pid: 99_999_999, token: "dead-writer" }),
    "utf8",
  );

  const updated = state("FREQ=MINUTELY;INTERVAL=15");
  const result = await writeSchedulerState(
    storeRequest(runtimeRoot, { state: updated }),
  );

  assert.equal(result.written, true);
  assert.equal(result.replayed, false);
  assert.deepEqual(
    (await loadSchedulerState(storeRequest(runtimeRoot))).state,
    updated,
  );
});
