import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
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

for (const kind of ["todo", "state", "lease"] as const) {
  test(`fence engagement waits for an existing ${kind} writer before publication`, async (t) => {
    const root = await mkdtemp(join(tmpdir(), "loopx-fence-lock-"));
    t.after(() => rm(root, {recursive: true, force: true}));
    const statePath = join(root, "ACTIVE_GOAL_STATE.md");
    await writeFile(statePath, "---\ngoal_id: goal-a\n---\n");
    const lock = kind === "todo" ? legacyCoordinationTodoLockPath(root, "goal-a") : kind === "state" ? statePath : taskLeaseLockPath({runtime_root: root, goal_id: "goal-a"});
    let pending: Promise<Record<string, unknown>> | undefined;
    let completed = false;
    await withFileMutationLock(lock, async () => {
      pending = engageLegacyCoordinationWriterFence(fenceRequest(root)).then((result) => {completed = true; return result;});
      await new Promise((resolve) => setTimeout(resolve, 100));
      assert.equal(completed, false, "the fence must not pass an active primary writer");
      await assert.rejects(access(legacyCoordinationWriterFencePath(root, "goal-a")));
    });
    assert.equal((await pending)!.status, "applied");
  });
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
import { executeTaskLeaseAcquire, TASK_LEASE_ACQUIRE_REQUEST_SCHEMA_VERSION } from "../../loopx/control_plane/work_items/task_lease_acquire.ts";
import { executeTaskLeaseLifecycle, TASK_LEASE_LIFECYCLE_REQUEST_SCHEMA_VERSION } from "../../loopx/control_plane/work_items/task_lease_lifecycle.ts";
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

test("active native lease requires durable prepare even without a caller capture hint", async (t) => {
  const f = await bootstrapFixture(t);
  const part = join(f.root, "authority-shadow", "outbox", "goal-a", "leases");
  await writeFile(part, "prepare unavailable");
  const authority = {handoff_mode: "hard_lease", registered_agent_candidates: [["agent-a"]],
    todos: [{todo_id: "todo_one", status: "open", claimed_by: null, excluded_agents: []}],
    todo_projection_error: null, source_receipts: [{source_id: "state", path: f.statePath, state: "file",
      sha256: createHash("sha256").update(await readFile(f.statePath)).digest("hex")}]};
  const request = {schema_version: TASK_LEASE_ACQUIRE_REQUEST_SCHEMA_VERSION, runtime_root: f.root,
    goal_id: "goal-a", todo_id: "todo_one", owner: "agent-a", idempotency_key: "lease-a",
    ttl_seconds: 600, write_scopes: [], expected_version: null, authority};
  const held = await executeTaskLeaseAcquire(request);
  assert.equal(held.ok, false);
  assert.equal(held.error_code, "shadow_capture_prepare_failed", JSON.stringify(held));
  const leasePath = join(f.root, "goals", "goal-a", "task-leases", "todo_one.json");
  await assert.rejects(access(leasePath));
  await rm(part);
  const applied = await executeTaskLeaseAcquire(request);
  assert.equal(applied.ok, true, JSON.stringify(applied));
  assert.equal(typeof (applied.coordination_runtime_shadow_capture as Record<string, unknown>).entry_id, "string");
  const before = await readFile(leasePath, "utf8");
  await atomicWriteJson(join(part, "drain-cursor.json"), {});
  const released = await executeTaskLeaseLifecycle({schema_version: TASK_LEASE_LIFECYCLE_REQUEST_SCHEMA_VERSION,
    operation: "release", runtime_root: f.root, goal_id: "goal-a", todo_id: "todo_one",
    owner: "agent-a", idempotency_key: "lease-a", expected_version: 1, authority});
  assert.equal(released.ok, false);
  assert.equal(released.error_code, "shadow_capture_prepare_failed", JSON.stringify(released));
  assert.equal(await readFile(leasePath, "utf8"), before);
});
