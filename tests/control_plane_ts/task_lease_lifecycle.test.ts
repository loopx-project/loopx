import assert from "node:assert/strict";
import {access, mkdtemp, readFile, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";

import {
  acquireFileMutationLock,
  atomicWriteJson,
} from "../../loopx/control_plane/effect_runtime_io.ts";
import {
  LEGACY_COORDINATION_WRITER_FENCE_SCHEMA,
} from "../../loopx/control_plane/coordination/coordination_state_contract.generated.ts";
import {
  legacyCoordinationWriterFencePath,
} from "../../loopx/control_plane/coordination/legacy_writer_fence.ts";
import {
  executeTaskLeaseLifecycle,
  TASK_LEASE_FENCE_RECEIPT_SCHEMA,
  TASK_LEASE_LIFECYCLE_REQUEST_SCHEMA_VERSION,
} from "../../loopx/control_plane/work_items/task_lease_lifecycle.ts";
import {evaluateTaskLeaseLifecycleDecision} from "../../loopx/control_plane/work_items/task_lease_lifecycle_decision.ts";
import {
  taskLeaseLockPath,
  taskLeasePath,
} from "../../loopx/control_plane/work_items/task_lease_acquire.ts";

const NOW = new Date("2026-10-12T00:00:00Z");

function authority(runtimeRoot: string) {
  return {
    handoff_mode: "hard_lease",
    registered_agent_candidates: [["agent-a", "agent-b"]],
    todos: [{
      todo_id: "todo_target",
      status: "open",
      claimed_by: null,
      excluded_agents: [],
    }],
    todo_projection_error: null,
    source_receipts: [{
      source_id: "state",
      path: join(runtimeRoot, "missing-state.md"),
      state: "missing",
      sha256: null,
    }],
  };
}

function request(runtimeRoot: string, operation: string, overrides = {}) {
  return {
    schema_version: TASK_LEASE_LIFECYCLE_REQUEST_SCHEMA_VERSION,
    operation,
    runtime_root: runtimeRoot,
    goal_id: "goal-a",
    todo_id: "todo_target",
    owner: "agent-a",
    idempotency_key: "lease-a",
    expected_version: 1,
    ttl_seconds: operation === "release" ? null : 600,
    new_owner: operation === "transfer" ? "agent-b" : null,
    new_idempotency_key: operation === "transfer" ? "lease-b" : null,
    authority: authority(runtimeRoot),
    ...overrides,
  };
}

function activeLease() {
  return {
    schema_version: "task_lease_v0",
    goal_id: "goal-a",
    todo_id: "todo_target",
    owner: "agent-a",
    idempotency_key: "lease-a",
    write_scopes: ["src/**"],
    acquire_ttl_seconds: 600,
    version: 1,
    lease_epoch: 1,
    status: "active",
    acquired_at: "2026-10-12T00:00:00Z",
    updated_at: "2026-10-12T00:00:00Z",
    expires_at: "2026-10-12T01:00:00Z",
  };
}

test("pure lifecycle decision keeps canonical generations explicit", () => {
  const input = {
    handoff_mode: "hard_lease",
    registered_agents: ["agent-a", "agent-b"],
    todo: {
      todo_id: "todo_target",
      status: "open",
      claimed_by: null,
      excluded_agents: [],
    },
    lease: {
      present: true,
      active: true,
      status: "active",
      owner: "agent-a",
      idempotency_key: "lease-a",
      version: 1,
      lease_epoch: 1,
      write_scopes: ["src/**"],
      acquire_ttl_seconds: 600,
    },
  };
  const renewed = evaluateTaskLeaseLifecycleDecision({
    ...input,
    command: {
      operation: "renew",
      owner: "agent-a",
      idempotency_key: "lease-a",
      expected_version: 1,
      ttl_seconds: 900,
      new_owner: null,
      new_idempotency_key: null,
    },
  });
  const transferred = evaluateTaskLeaseLifecycleDecision({
    ...input,
    command: {
      operation: "transfer",
      owner: "agent-a",
      idempotency_key: "lease-a",
      expected_version: 1,
      ttl_seconds: 900,
      new_owner: "agent-b",
      new_idempotency_key: "lease-b",
    },
  });
  assert.equal(renewed.outcome, "apply");
  assert.equal(renewed.next_lease?.version, 2);
  assert.equal(renewed.next_lease?.lease_epoch, 1);
  assert.equal(transferred.outcome, "apply");
  assert.equal(transferred.next_lease?.version, 2);
  assert.equal(transferred.next_lease?.lease_epoch, 2);
});

for (const operation of ["renew", "transfer"] as const) {
  test(`legacy ${operation} returns the authority migration cutoff`, async (t) => {
    const root = await mkdtemp(join(tmpdir(), "loopx-lease-lifecycle-cutoff-"));
    t.after(() => rm(root, {recursive: true, force: true}));
    const runtimeRoot = join(root, "runtime");

    const result = await executeTaskLeaseLifecycle(request(runtimeRoot, operation));

    assert.equal(result.ok, false);
    assert.equal(result.error_code, "canonical_authority_required");
    assert.equal(result.write_admitted, false);
    assert.equal(result.decision_read_from_provider, false);
    assert.equal(result.legacy_fallback_used, false);
    assert.deepEqual(result.recovery, {
      action: "migrate_goal_authority",
      backup_required: true,
    });
    await assert.rejects(access(join(runtimeRoot, "goals")));
  });
}

for (const operation of ["holder_verify", "terminal_verify"] as const) {
  test(`${operation} is retired without creating a fence receipt`, async (t) => {
    const root = await mkdtemp(join(tmpdir(), "loopx-lease-verify-cutoff-"));
    t.after(() => rm(root, {recursive: true, force: true}));
    const runtimeRoot = join(root, "runtime");

    const result = await executeTaskLeaseLifecycle(request(runtimeRoot, operation));

    assert.equal(result.ok, false);
    assert.equal(result.error_code, "task_lease_verification_retired");
    assert.equal(result.write_admitted, false);
    assert.equal(result.legacy_fallback_used, false);
    await assert.rejects(access(join(runtimeRoot, "goals")));
  });
}

test("exact legacy release remains available for an existing lease", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "loopx-lease-release-retained-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const runtimeRoot = join(root, "runtime");
  const path = taskLeasePath({
    runtime_root: runtimeRoot,
    goal_id: "goal-a",
    todo_id: "todo_target",
  });
  await atomicWriteJson(path, activeLease());

  const releaseRequest = request(runtimeRoot, "release", {authority: null});
  const result = await executeTaskLeaseLifecycle(
    releaseRequest,
    {now: () => NOW},
  );
  const replay = await executeTaskLeaseLifecycle(
    releaseRequest,
    {now: () => NOW},
  );

  assert.equal(result.ok, true);
  assert.equal(result.released, true);
  assert.equal(JSON.parse(await readFile(path, "utf8")).status, "released");
  assert.equal(replay.ok, true);
  assert.equal(replay.idempotent, true);
});

test("exact legacy release honors an engaged promotion fence without writes", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "loopx-lease-release-fenced-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const runtimeRoot = join(root, "runtime");
  const path = taskLeasePath({
    runtime_root: runtimeRoot,
    goal_id: "goal-a",
    todo_id: "todo_target",
  });
  await atomicWriteJson(path, activeLease());
  await atomicWriteJson(
    legacyCoordinationWriterFencePath(runtimeRoot, "goal-a"),
    {
      schema_version: LEGACY_COORDINATION_WRITER_FENCE_SCHEMA,
      state: "engaged",
      goal_id: "goal-a",
      fence_id: "legacy-writer-fence:goal-a:state-1",
      source_version: "state:1",
      source_projection_sha256: "a".repeat(64),
      expected_shadow_provider_revision: "file:1:aaaaaaaaaaaaaaaaaaaaaaaa",
    },
  );

  const result = await executeTaskLeaseLifecycle(
    request(runtimeRoot, "release", {authority: null}),
    {now: () => NOW},
  );

  assert.equal(result.ok, false);
  assert.equal(result.error_code, "legacy_coordination_writer_fenced");
  assert.equal(
    result.error,
    "legacy coordination writer is fenced; use the promoted canonical authority " +
      "(file_v0) for goal goal-a; fence legacy-writer-fence:goal-a:state-1; " +
      "the primary record was not changed",
  );
  assert.deepEqual(result.write_check, {
    schema_version: "loopx_legacy_coordination_write_check_result_v0",
    status: "blocked",
    reason_code: "legacy_coordination_writer_fenced",
    authority_mode: "file_v0",
    fence_id: "legacy-writer-fence:goal-a:state-1",
  });
  assert.equal(JSON.parse(await readFile(path, "utf8")).status, "active");
});

test("historical held receipt can still close and release its exact lease", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "loopx-lease-close-retained-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const runtimeRoot = join(root, "runtime");
  const identity = {
    runtime_root: runtimeRoot,
    goal_id: "goal-a",
    todo_id: "todo_target",
  };
  const lease = activeLease();
  const leasePath = taskLeasePath(identity);
  await atomicWriteJson(leasePath, lease);
  const lock = await acquireFileMutationLock(
    taskLeaseLockPath(identity),
    process.pid,
  );
  const operationId = "a".repeat(64);
  const verification = {
    schema_version: "task_lease_v0",
    required: true,
    active: true,
    owner: "agent-a",
    version: 1,
    lease_epoch: 1,
    execution_instance_verified: true,
    lock_token: lock.token,
    fence_operation_id: operationId,
  };
  await atomicWriteJson(join(
    runtimeRoot,
    "goals",
    "goal-a",
    "task-leases",
    ".lifecycle-fences",
    `${operationId}.json`,
  ), {
    schema_version: TASK_LEASE_FENCE_RECEIPT_SCHEMA,
    operation_id: operationId,
    request_digest: "b".repeat(64),
    close_request_digest: null,
    state: "held",
    goal_id: "goal-a",
    todo_id: "todo_target",
    owner_pid: process.pid,
    lock_token: lock.token,
    fence_owner: "agent-a",
    fence_idempotency_key: "lease-a",
    fence_expected_version: 1,
    fence_expected_lease_epoch: 1,
    lease,
    response: verification,
    verify_response: verification,
  });
  const closeRequest = request(runtimeRoot, "fence_close", {
    owner: null,
    idempotency_key: null,
    expected_version: null,
    ttl_seconds: null,
    new_owner: null,
    new_idempotency_key: null,
    authority: null,
    lock_token: lock.token,
    fence_operation_id: operationId,
    committed: true,
    release_lease: true,
    fence_owner: "agent-a",
    fence_idempotency_key: "lease-a",
    fence_expected_version: 1,
    fence_expected_lease_epoch: 1,
    owner_pid: process.pid,
  });

  const closed = await executeTaskLeaseLifecycle(
    closeRequest,
    {now: () => NOW},
  );
  const replay = await executeTaskLeaseLifecycle(
    closeRequest,
    {now: () => NOW},
  );

  assert.equal(closed.ok, true);
  assert.equal(closed.released, true);
  assert.equal(JSON.parse(await readFile(leasePath, "utf8")).status, "released");
  assert.equal(replay.ok, true);
  assert.equal(replay.idempotent, true);
});
