import assert from "node:assert/strict";
import fs, { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test, {type TestContext} from "node:test";
import {syncBuiltinESMExports} from "node:module";
import { atomicWriteJson } from "../../loopx/control_plane/effect_runtime_io.ts";
import { withCanonicalWriter } from "../../loopx/control_plane/coordination/local_authority_write.ts";
import {
  shadowMaintenanceLockPath,
  shadowManagementStatePath,
} from "../../loopx/control_plane/coordination/shadow_management.ts";
import {
  archiveLocalCoordinationTodos,
  acknowledgeLocalCoordinationTodoArchive,
  createLocalCoordinationTodo, claimLocalCoordinationTodo,
  terminalLifecycleLocalCoordinationTodo,
  LOCAL_COORDINATION_TODO_ARCHIVE_REQUEST_SCHEMA,
  LOCAL_COORDINATION_TODO_ARCHIVE_ACK_REQUEST_SCHEMA,
  LOCAL_COORDINATION_TODO_CREATE_REQUEST_SCHEMA, LOCAL_COORDINATION_TODO_CLAIM_REQUEST_SCHEMA,
  LOCAL_COORDINATION_TODO_TERMINAL_LIFECYCLE_REQUEST_SCHEMA,
} from "../../loopx/control_plane/coordination/local_authority_runtime.ts";

for (const [name, invoke, schema, requestFields] of [
  ["create", createLocalCoordinationTodo, LOCAL_COORDINATION_TODO_CREATE_REQUEST_SCHEMA, {}],
  ["claim", claimLocalCoordinationTodo, LOCAL_COORDINATION_TODO_CLAIM_REQUEST_SCHEMA, {}],
  ["terminal", terminalLifecycleLocalCoordinationTodo,
    LOCAL_COORDINATION_TODO_TERMINAL_LIFECYCLE_REQUEST_SCHEMA, {
      registered_agents: [], lifecycle_grants: [], successor_intents: [],
      linked_successor_todo_ids: [], lease_expected_version: null,
      registry_source: {path: join(tmpdir(), "unused-registry.json"), sha256: "0".repeat(64)},
      command: "complete", operation_identity: {kind: "explicit", operation_id: "maintenance"},
    }],
  ["archive", archiveLocalCoordinationTodos,
    LOCAL_COORDINATION_TODO_ARCHIVE_REQUEST_SCHEMA, {
      max_active_done: 0, role: "agent", operation_id: "archive-maintenance",
      observed_at: "2026-01-01T00:00:00Z",
    }],
  ["archive acknowledgement", acknowledgeLocalCoordinationTodoArchive,
    LOCAL_COORDINATION_TODO_ARCHIVE_ACK_REQUEST_SCHEMA, {
      role: "agent", operation_id: "archive-maintenance",
    }],
] as const) {
  test(`promoted ${name} checks maintenance before opening a provider`, async (t) => {
    const root = await mkdtemp(join(tmpdir(), "loopx-native-maintenance-"));
    t.after(() => rm(root, {recursive: true, force: true}));
    await atomicWriteJson(shadowManagementStatePath(root, "goal-a"), {});
    let opened = 0;
    const result = await invoke({schema_version: schema, runtime_root: root, goal_id: "goal-a",
      dry_run: false, ...requestFields}, {
      createStore: () => { opened++; throw new Error("provider touched"); },
    });
    assert.equal(result.reason_code, "shadow_management_state_invalid");
    assert.equal(opened, 0);
  });
}

for (const [name, invoke, schema, requestFields] of [
  ["terminal", terminalLifecycleLocalCoordinationTodo,
    LOCAL_COORDINATION_TODO_TERMINAL_LIFECYCLE_REQUEST_SCHEMA, {
      registered_agents: [], lifecycle_grants: [], successor_intents: [],
      linked_successor_todo_ids: [], lease_expected_version: null,
      registry_source: {path: join(tmpdir(), "unused-registry.json"), sha256: "0".repeat(64)},
      command: "complete", operation_identity: {kind: "explicit", operation_id: "maintenance"},
    }],
  ["archive", archiveLocalCoordinationTodos,
    LOCAL_COORDINATION_TODO_ARCHIVE_REQUEST_SCHEMA, {
      max_active_done: 0, role: "agent", operation_id: "archive-maintenance",
      observed_at: "2026-01-01T00:00:00Z",
    }],
  ["archive acknowledgement", acknowledgeLocalCoordinationTodoArchive,
    LOCAL_COORDINATION_TODO_ARCHIVE_ACK_REQUEST_SCHEMA, {
      role: "agent", operation_id: "archive-maintenance",
    }],
] as const) {
  test(`promoted ${name} waits behind the bootstrap and rollback maintenance lock`, async (t) => {
    const root = await mkdtemp(join(tmpdir(), "loopx-native-maintenance-race-"));
    t.after(() => rm(root, {recursive: true, force: true}));
    await atomicWriteJson(legacyCoordinationWriterFencePath(root, "goal-a"), {
      schema_version: "loopx_legacy_coordination_writer_fence_v0",
      state: "engaged",
      goal_id: "goal-a",
      fence_id: "maintenance-race",
      source_version: "source-a",
      source_projection_sha256: "a".repeat(64),
      expected_shadow_provider_revision: "file:1:aaaaaaaaaaaaaaaaaaaaaaaa",
    });
    let opened = 0;
    let completed = false;
    let pending: Promise<Record<string, unknown>> | undefined;
    await withFileMutationLock(shadowMaintenanceLockPath(root, "goal-a"), async () => {
      pending = invoke({schema_version: schema, runtime_root: root, goal_id: "goal-a",
        dry_run: false, ...requestFields}, {
        createStore: () => {
          opened += 1;
          throw new Error("provider opened only after maintenance");
        },
      }).then((result) => {
        completed = true;
        return result;
      });
      await new Promise((resolve) => setTimeout(resolve, 100));
      assert.equal(completed, false);
      assert.equal(opened, 0, "provider access must not overlap bootstrap or rollback");
    });
    const result = await pending!;
    assert.equal(opened, 1);
    assert.equal(result.status, "failed");
    assert.match(String(result.reason), /provider opened only after maintenance/);
  });
}

import { engageLegacyCoordinationWriterFence, legacyCoordinationTodoLockPath, legacyCoordinationWriterFencePath } from "../../loopx/control_plane/coordination/legacy_writer_fence.ts";
import { taskLeaseLockPath } from "../../loopx/control_plane/work_items/task_lease_acquire.ts";
import { withFileMutationLock } from "../../loopx/control_plane/effect_runtime_io.ts";
import { access } from "node:fs/promises";

test("normal canonical writes reject unpromoted execute and dry-run before provider open", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "loopx-canonical-cutoff-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  for (const dry_run of [false, true]) {
    let opened = 0;
    const result = await createLocalCoordinationTodo({
      schema_version: LOCAL_COORDINATION_TODO_CREATE_REQUEST_SCHEMA,
      runtime_root: root,
      goal_id: "goal-a",
      registered_agents: [],
      dry_run,
    }, {
      createStore: () => {
        opened++;
        throw new Error("provider touched");
      },
    });
    assert.equal(result.reason_code, "canonical_authority_required");
    assert.equal(result.write_admitted, false);
    assert.equal(result.decision_read_from_provider, false);
    assert.equal(result.legacy_fallback_used, false);
    assert.deepEqual(result.recovery, {
      action: "migrate_goal_authority",
      backup_required: true,
    });
    assert.equal(opened, 0);
  }
});

test("a canonical writer waits for a slow file-v0 critical section without losing the fence", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "loopx-canonical-long-writer-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  let entered!: () => void;
  let release!: () => void;
  const firstEntered = new Promise<void>((resolve) => { entered = resolve; });
  const firstRelease = new Promise<void>((resolve) => { release = resolve; });
  const first = withCanonicalWriter(root, "goal-a", false, async () => {
    entered();
    await firstRelease;
    return "first";
  });
  await firstEntered;
  let secondState = "pending";
  const second = withCanonicalWriter(root, "goal-a", false, async () => "second")
    .then((value) => { secondState = "applied"; return value; }, (error: unknown) => {
      secondState = "failed";
      return error;
    });
  try {
    await new Promise((resolve) => setTimeout(resolve, 5_200));
    assert.equal(secondState, "pending", "the old five-second lock deadline must not reject a live writer");
  } finally {
    release();
  }
  assert.equal(await first, "first");
  assert.equal(await second, "second");
  assert.equal(secondState, "applied");
});

function fenceRequest(root: string) {
  return {schema_version: "loopx_legacy_coordination_writer_fence_engage_request_v0",
    runtime_root: root, goal_id: "goal-a", state_path: join(root, "ACTIVE_GOAL_STATE.md"), fence: {
      schema_version: "loopx_legacy_coordination_writer_fence_v0", state: "engaged", goal_id: "goal-a",
      fence_id: "fence-a", source_version: "source-a", source_projection_sha256: "a".repeat(64),
      expected_shadow_provider_revision: "file:1:aaaaaaaaaaaaaaaaaaaaaaaa",
    }};
}

test("fence engagement refuses malformed durable maintenance before publishing", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "loopx-fence-maintenance-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  await writeFile(join(root, "ACTIVE_GOAL_STATE.md"), "---\ngoal_id: goal-a\n---\n");
  await atomicWriteJson(shadowManagementStatePath(root, "goal-a"), {});
  const result = await engageLegacyCoordinationWriterFence(fenceRequest(root));
  assert.equal(result.reason_code, "shadow_management_state_invalid");
  await assert.rejects(access(legacyCoordinationWriterFencePath(root, "goal-a")));
});

// Observe a real failed exclusive open, rather than assuming an operation has
// reached its lock after a wall-clock sleep. Slow startup must not let a broken
// lock path pass before the test releases the primary writer.
async function whileWriterContended(
  t: TestContext, target: string, invoke: () => Promise<Record<string, unknown>>,
  checkBlocked: () => Promise<void>,
): Promise<Record<string, unknown>> {
  let pending: Promise<Record<string, unknown>> | undefined;
  try {
    await withFileMutationLock(target, async () => {
      const heldPath = await fs.realpath(`${target}.ts-effect.lock`);
      let observed!: () => void;
      const contention = new Promise<void>(resolve => { observed = resolve; });
      const originalOpen = fs.open;
      const probe = t.mock.method(fs, "open", async (...args: Parameters<typeof fs.open>) => {
        try {
          return await originalOpen(...args);
        } catch (error) {
          if ((error as NodeJS.ErrnoException).code === "EEXIST" &&
              await fs.realpath(String(args[0])) === heldPath) observed();
          throw error;
        }
      });
      syncBuiltinESMExports();
      try {
        pending = invoke();
        const first = await Promise.race([
          contention.then(() => "contended"),
          pending.then(() => "completed"),
        ]);
        assert.equal(first, "contended", "the fence must encounter the active primary writer lock");
        await checkBlocked();
      } finally {
        probe.mock.restore();
        syncBuiltinESMExports();
      }
    });
    return await pending!;
  } finally {
    // The real holder is released even after a failed assertion. Drain the
    // operation before fixture cleanup, preserving the original test failure.
    await pending?.catch(() => undefined);
  }
}

for (const kind of ["todo", "state", "lease"] as const) {
  for (const startupDelay of [0, 200]) {
    const suffix = startupDelay ? " after delayed startup" : "";
    test(`fence engagement waits for an existing ${kind} writer before publication${suffix}`, {timeout: 10_000}, async (t) => {
      const root = await mkdtemp(join(tmpdir(), "loopx-fence-lock-"));
      t.after(() => rm(root, {recursive: true, force: true}));
      const statePath = join(root, "ACTIVE_GOAL_STATE.md");
      await writeFile(statePath, "---\ngoal_id: goal-a\n---\n");
      const lock = kind === "todo" ? legacyCoordinationTodoLockPath(root, "goal-a") : kind === "state" ? statePath : taskLeaseLockPath({runtime_root: root, goal_id: "goal-a"});
      const result = await whileWriterContended(t, lock,
        async () => {
          if (startupDelay) await new Promise(resolve => setTimeout(resolve, startupDelay));
          return engageLegacyCoordinationWriterFence(fenceRequest(root));
        },
        async () => { await assert.rejects(access(legacyCoordinationWriterFencePath(root, "goal-a"))); });
      assert.equal(result.status, "applied");
      assert.equal(JSON.parse(await readFile(legacyCoordinationWriterFencePath(root, "goal-a"), "utf8")).fence_id, "fence-a");
    });
  }
}

test("fence engagement requires the actual source state path", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "loopx-fence-source-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const request = fenceRequest(root) as Record<string, unknown>;
  delete request.state_path;
  const missing = await engageLegacyCoordinationWriterFence(request);
  assert.equal(missing.status, "failed");
  assert.equal(missing.reason_code, "invalid_legacy_writer_fence_request");
  await assert.rejects(access(legacyCoordinationWriterFencePath(root, "goal-a")));
});

import { fixture as bootstrapFixture } from "./shadow_file_fixture.ts";
import { readFile, writeFile } from "node:fs/promises";

test("managed fence engagement rejects a different existing source file", async (t) => {
  const f = await bootstrapFixture(t);
  const other = join(f.root, "OTHER_GOAL_STATE.md");
  await writeFile(other, await readFile(f.statePath));
  const result = await engageLegacyCoordinationWriterFence({...fenceRequest(f.root), state_path: other});
  assert.equal(result.status, "failed");
  assert.equal(result.reason_code, "shadow_source_state_path_mismatch");
  await assert.rejects(access(legacyCoordinationWriterFencePath(f.root, "goal-a")));
});
