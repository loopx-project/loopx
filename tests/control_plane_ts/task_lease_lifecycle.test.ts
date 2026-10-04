import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { atomicWriteJson } from "../../loopx/control_plane/effect_runtime_io.ts";
import { mkdtemp, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test, { type TestContext } from "node:test";

import {
  executeTaskLeaseAcquire,
  TASK_LEASE_ACQUIRE_REQUEST_SCHEMA_VERSION,
} from "../../loopx/control_plane/work_items/task_lease_acquire.ts";
import {
  executeTaskLeaseLifecycle,
  TASK_LEASE_LIFECYCLE_REQUEST_SCHEMA_VERSION,
} from "../../loopx/control_plane/work_items/task_lease_lifecycle.ts";
import { evaluateTaskLeaseLifecycleDecision } from "../../loopx/control_plane/work_items/task_lease_lifecycle_decision.ts";
import { shadowManagementStatePath } from "../../loopx/control_plane/coordination/shadow_management.ts";

const ACQUIRE_NOW = new Date("2026-09-01T03:00:00.000Z");

test("maintenance corruption blocks native acquire and release before lease mutation", async (t) => {
  const root = await workspace(t);
  const acquired = await executeTaskLeaseAcquire(await acquireRequest(root));
  assert.equal(acquired.ok, true);
  const path = join(root, "runtime", "goals", "goal-a", "task-leases", "todo_target.json");
  const before = await readFile(path, "utf8");
  await atomicWriteJson(shadowManagementStatePath(join(root, "runtime"), "goal-a"), {});
  const released = await executeTaskLeaseLifecycle(await lifecycleRequest(root, "release"));
  assert.equal(released.ok, false);
  assert.equal(released.error_code, "shadow_management_state_invalid");
  assert.equal(await readFile(path, "utf8"), before);
  const retry = await executeTaskLeaseAcquire(await acquireRequest(root, {idempotency_key: "new-key"}));
  assert.equal(retry.ok, false);
  assert.equal(retry.error_code, "shadow_management_state_invalid");
  assert.equal(await readFile(path, "utf8"), before);
});

function lifecycleDecision(
  operation: "renew" | "transfer" | "release",
  overrides: Record<string, unknown> = {},
) {
  return evaluateTaskLeaseLifecycleDecision({
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
      version: 3,
      lease_epoch: 7,
      write_scopes: ["docs/**"],
      acquire_ttl_seconds: 300,
    },
    command: {
      operation,
      owner: "agent-a",
      idempotency_key: "lease-a",
      expected_version: 3,
      ttl_seconds: operation === "release" ? null : 600,
      new_owner: operation === "transfer" ? "agent-b" : null,
      new_idempotency_key: operation === "transfer" ? "lease-b" : null,
    },
    ...overrides,
  });
}

test("pure lifecycle decision owns renew transfer and release generations", () => {
  const renewed = lifecycleDecision("renew");
  assert.equal(renewed.outcome, "apply");
  assert.equal(renewed.next_lease?.version, 4);
  assert.equal(renewed.next_lease?.lease_epoch, 7);

  const transferred = lifecycleDecision("transfer");
  assert.equal(transferred.outcome, "apply");
  assert.equal(transferred.next_lease?.owner, "agent-b");
  assert.equal(transferred.next_lease?.version, 4);
  assert.equal(transferred.next_lease?.lease_epoch, 8);

  const released = lifecycleDecision("release");
  assert.equal(released.outcome, "apply");
  assert.equal(released.next_lease?.active, false);
  assert.equal(released.next_lease?.status, "released");
});

test("pure lifecycle decision keeps provider executions behind the same gates", () => {
  const stale = lifecycleDecision("renew", {
    command: {
      operation: "renew",
      owner: "agent-a",
      idempotency_key: "lease-a",
      expected_version: 2,
      ttl_seconds: 600,
      new_owner: null,
      new_idempotency_key: null,
    },
  });
  assert.equal(stale.outcome, "conflict");
  assert.equal(stale.code, "version_mismatch");

  const softClaim = lifecycleDecision("transfer", {
    handoff_mode: "soft_claim",
  });
  assert.equal(softClaim.outcome, "rejected");
  assert.equal(softClaim.code, "handoff_mode_forbids_lease");
});

for (const operation of ["renew", "transfer", "release"] as const) {
  test(`native ${operation} rejects contradictory lease facts before mode and CAS`, () => {
    for (const contradiction of [{present: false}, {status: "released"}]) {
      const result = lifecycleDecision(operation, {
        handoff_mode: "soft_claim",
        lease: {present: true, active: true, status: "active", owner: "agent-a",
          idempotency_key: "lease-a", version: 3, lease_epoch: 7,
          write_scopes: [], acquire_ttl_seconds: 300, ...contradiction},
        command: {operation, owner: "agent-a", idempotency_key: "lease-a",
          expected_version: null, ttl_seconds: null, new_owner: null,
          new_idempotency_key: null},
      });
      assert.equal(result.outcome, "rejected");
      assert.equal(result.code, "invalid_lease_snapshot");
      assert.equal(result.next_lease, null);
    }
  });
}

test("soft-claim rejects renew and transfer before missing version; release keeps its fence", () => {
  for (const operation of ["renew", "transfer", "release"] as const) {
    const result = lifecycleDecision(operation, {
      handoff_mode: "soft_claim",
      command: {operation, owner: "agent-a", idempotency_key: "lease-a",
        expected_version: null, ttl_seconds: null, new_owner: null,
        new_idempotency_key: null},
    });
    assert.equal(result.outcome, "rejected");
    assert.equal(result.code, operation === "release" ? "version_required" : "handoff_mode_forbids_lease");
  }
  const cleanup = lifecycleDecision("release", {handoff_mode: "soft_claim"});
  assert.equal(cleanup.outcome, "apply");
  assert.equal(cleanup.next_lease?.status, "released");
  assert.equal(cleanup.next_lease?.version, 3);
  assert.equal(cleanup.next_lease?.lease_epoch, 7);
});

async function workspace(t: TestContext): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), "loopx-task-lease-lifecycle-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  return root;
}

async function authoritySource(root: string, content = "authority-v1") {
  const path = join(root, "authority-source.json");
  await writeFile(path, content, "utf8");
  return {
    source_id: "authority",
    path,
    state: "file",
    sha256: createHash("sha256").update(content).digest("hex"),
  };
}

type RequestOverrides = Record<string, unknown>;

async function authority(
  root: string,
  overrides: RequestOverrides = {},
): Promise<Record<string, unknown>> {
  return {
    handoff_mode: "hard_lease",
    registered_agent_candidates: [["agent-a", "agent-b"]],
    todos: [
      {
        todo_id: "todo_target",
        status: "open",
        claimed_by: null,
        excluded_agents: [],
      },
    ],
    todo_projection_error: null,
    source_receipts: [await authoritySource(root)],
    ...overrides,
  };
}

async function acquireRequest(
  root: string,
  overrides: RequestOverrides = {},
): Promise<Record<string, unknown>> {
  return {
    schema_version: TASK_LEASE_ACQUIRE_REQUEST_SCHEMA_VERSION,
    runtime_root: join(root, "runtime"),
    goal_id: "goal-a",
    todo_id: "todo_target",
    owner: "agent-a",
    idempotency_key: "lease-a",
    ttl_seconds: 600,
    write_scopes: [],
    expected_version: null,
    authority: await authority(root),
    ...overrides,
  };
}

async function lifecycleRequest(
  root: string,
  operation: string,
  overrides: RequestOverrides = {},
): Promise<Record<string, unknown>> {
  return {
    schema_version: TASK_LEASE_LIFECYCLE_REQUEST_SCHEMA_VERSION,
    operation,
    runtime_root: join(root, "runtime"),
    goal_id: "goal-a",
    todo_id: "todo_target",
    owner: "agent-a",
    idempotency_key: "lease-a",
    expected_version: 1,
    ttl_seconds: 600,
    new_owner: null,
    new_idempotency_key: null,
    lock_token: null,
    committed: false,
    release_lease: false,
    fence_owner: null,
    fence_idempotency_key: null,
    fence_expected_version: null,
    fence_expected_lease_epoch: 1,
    authority: await authority(root),
    ...overrides,
  };
}

async function lease(root: string): Promise<Record<string, unknown>> {
  return JSON.parse(
    await readFile(
      join(root, "runtime", "goals", "goal-a", "task-leases", "todo_target.json"),
      "utf8",
    ),
  ) as Record<string, unknown>;
}

test("native lifecycle renew, transfer, and release preserve CAS generations", async (t) => {
  const root = await workspace(t);
  const acquired = await executeTaskLeaseAcquire(await acquireRequest(root), {
    now: () => ACQUIRE_NOW,
  });
  assert.equal(acquired.ok, true);

  const renewed = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "renew"),
    { now: () => new Date("2026-09-01T03:01:00.000Z") },
  );
  assert.equal(renewed.ok, true);
  assert.equal(renewed.renewed, true);
  assert.equal((renewed.lease as Record<string, unknown>).version, 2);

  const transferred = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "transfer", {
      owner: "agent-a",
      idempotency_key: "lease-a",
      expected_version: 2,
      new_owner: "agent-b",
      new_idempotency_key: "lease-b",
    }),
    { now: () => new Date("2026-09-01T03:02:00.000Z") },
  );
  assert.equal(transferred.ok, true);
  assert.equal(transferred.transferred, true);
  assert.equal((transferred.lease as Record<string, unknown>).owner, "agent-b");
  assert.equal((transferred.lease as Record<string, unknown>).lease_epoch, 2);
  assert.equal((transferred.lease as Record<string, unknown>).version, 3);

  const released = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "release", {
      owner: "agent-b",
      idempotency_key: "lease-b",
      expected_version: 3,
      authority: null,
    }),
    { now: () => new Date("2026-09-01T03:03:00.000Z") },
  );
  assert.equal(released.ok, true);
  assert.equal(released.released, true);
  assert.equal((await lease(root)).status, "released");
});

for (const scenario of [
  {
    name: "unregistered",
    errorCode: "owner_not_registered",
    newOwner: "agent-c",
    authority: {},
  },
  {
    name: "excluded",
    errorCode: "owner_excluded_from_todo",
    newOwner: "agent-b",
    authority: {
      todos: [{
        todo_id: "todo_target",
        status: "open",
        claimed_by: null,
        excluded_agents: ["agent-b"],
      }],
    },
  },
  {
    name: "claim conflict",
    errorCode: "owner_conflicts_with_claim",
    newOwner: "agent-b",
    authority: {
      registered_agent_candidates: [["agent-a", "agent-b", "agent-c"]],
      todos: [{
        todo_id: "todo_target",
        status: "open",
        claimed_by: "agent-c",
        excluded_agents: [],
      }],
    },
  },
  {
    name: "closed todo",
    errorCode: "todo_not_open",
    newOwner: "agent-b",
    authority: {
      todos: [{
        todo_id: "todo_target",
        status: "done",
        claimed_by: null,
        excluded_agents: [],
      }],
    },
  },
  {
    name: "missing todo",
    errorCode: "todo_not_found",
    newOwner: "agent-b",
    authority: { todos: [] },
  },
] as const) {
  test(`transfer rejection identifies the requested new owner: ${scenario.name}`, async (t) => {
    const root = await workspace(t);
    await executeTaskLeaseAcquire(await acquireRequest(root), { now: () => ACQUIRE_NOW });
    const rejected = await executeTaskLeaseLifecycle(
      await lifecycleRequest(root, "transfer", {
        new_owner: scenario.newOwner,
        new_idempotency_key: "lease-target",
        authority: await authority(root, scenario.authority),
      }),
      { now: () => new Date("2026-09-01T03:01:00.000Z") },
    );
    assert.equal(rejected.ok, false);
    assert.equal(rejected.error_code, scenario.errorCode);
    assert.equal(rejected.owner, scenario.newOwner);
    assert.equal((await lease(root)).owner, "agent-a");
    assert.equal((await lease(root)).version, 1);
  });
}

test("lifecycle decode failures preserve the requested action", async (t) => {
  const root = await workspace(t);
  const rejected = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "renew", { expected_version: "1" }),
  );

  assert.equal(rejected.ok, false);
  assert.equal(rejected.action, "renew");
  assert.equal(rejected.error_code, "invalid_request");
});

test("lifecycle replay is idempotent and parameter reuse fails without mutation", async (t) => {
  const root = await workspace(t);
  await executeTaskLeaseAcquire(await acquireRequest(root), { now: () => ACQUIRE_NOW });
  const request = await lifecycleRequest(root, "renew");
  const first = await executeTaskLeaseLifecycle(request, {
    now: () => new Date("2026-09-01T03:01:00.000Z"),
  });
  const afterFirst = await lease(root);
  const replay = await executeTaskLeaseLifecycle(request, {
    now: () => new Date("2026-09-01T03:30:00.000Z"),
  });
  assert.equal(first.ok, true);
  assert.equal(replay.ok, true);
  assert.equal(replay.idempotent, true);
  assert.deepEqual(await lease(root), afterFirst);

  const reuse = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "renew", { ttl_seconds: 300 }),
    { now: () => new Date("2026-09-01T03:02:00.000Z") },
  );
  assert.equal(reuse.ok, false);
  assert.equal(reuse.error_code, "idempotency_key_reuse");
  assert.deepEqual(await lease(root), afterFirst);
});

test("lifecycle rejects stale versions and expired leases before writeback", async (t) => {
  const root = await workspace(t);
  await executeTaskLeaseAcquire(await acquireRequest(root, { ttl_seconds: 30 }), {
    now: () => ACQUIRE_NOW,
  });

  const stale = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "renew", { expected_version: 0 }),
    { now: () => new Date("2026-09-01T03:01:00.000Z") },
  );
  assert.equal(stale.ok, false);
  assert.equal(stale.error_code, "version_mismatch");
  assert.equal((await lease(root)).version, 1);

  const expired = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "renew", { expected_version: 1 }),
    { now: () => new Date("2026-09-01T03:01:00.001Z") },
  );
  assert.equal(expired.ok, false);
  assert.equal(expired.error_code, "lease_not_active");
  assert.equal((await lease(root)).version, 1);
});

test("lifecycle accepts active leases with high-precision fractional seconds", async (t) => {
  const root = await workspace(t);
  await executeTaskLeaseAcquire(await acquireRequest(root), { now: () => ACQUIRE_NOW });
  const existing = await lease(root);
  existing.expires_at = "2026-09-01T03:10:00.123456789Z";
  await writeFile(
    join(root, "runtime", "goals", "goal-a", "task-leases", "todo_target.json"),
    JSON.stringify(existing),
    "utf8",
  );

  const renewed = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "renew"),
    { now: () => new Date("2026-09-01T03:01:00.000Z") },
  );

  assert.equal(renewed.ok, true);
  assert.equal(renewed.renewed, true);
});

test("holder verification is hard-lease-only and fence close releases only its verified lease", async (t) => {
  const root = await workspace(t);
  const hardAuthority = await authority(root, {
    todos: [{
      todo_id: "todo_target",
      status: "open",
      claimed_by: "agent-a",
      excluded_agents: [],
    }],
  });
  await executeTaskLeaseAcquire(
    await acquireRequest(root, { authority: hardAuthority }),
    { now: () => ACQUIRE_NOW },
  );

  const checked = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "holder_verify", {
      authority: hardAuthority,
      idempotency_key: null,
      expected_version: null,
      owner: "agent-a",
    }),
    { now: () => new Date("2026-09-01T03:01:00.000Z") },
  );
  assert.equal(checked.ok, true);
  const fence = checked.fence as Record<string, unknown>;
  assert.equal(fence.owner, "agent-a");
  assert.equal(fence.version, 1);
  assert.equal(typeof fence.lock_token, "string");

  const closed = await executeTaskLeaseLifecycle({
    ...await lifecycleRequest(root, "fence_close", {
      authority: null,
      owner: null,
      idempotency_key: null,
      expected_version: null,
      lock_token: fence.lock_token,
      committed: true,
      release_lease: true,
      fence_owner: "agent-a",
      // Holder verification is actor/lease-tuple based; the close bridge can
      // recover the private execution key from its durable fence receipt.
      fence_idempotency_key: null,
      fence_expected_version: 1,
    }),
  }, { now: () => new Date("2026-09-01T03:02:00.000Z") });
  assert.equal(closed.ok, true);
  assert.equal(closed.released, true);
  assert.equal((await lease(root)).status, "released");

  const soft = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "holder_verify", {
      authority: await authority(root, { handoff_mode: "soft_claim" }),
      idempotency_key: null,
      expected_version: null,
    }),
    { now: () => new Date("2026-09-01T03:03:00.000Z") },
  );
  assert.equal(soft.ok, false);
  assert.equal(soft.error_code, "handoff_mode_forbids_lease");
});

test("holder verification adopts one explicit retry receipt and fresh gates mint new ids", async (t) => {
  const root = await workspace(t);
  const hardAuthority = await authority(root, {
    todos: [{
      todo_id: "todo_target",
      status: "open",
      claimed_by: "agent-a",
      excluded_agents: [],
    }],
  });
  await executeTaskLeaseAcquire(
    await acquireRequest(root, { authority: hardAuthority }),
    { now: () => ACQUIRE_NOW },
  );
  const request = await lifecycleRequest(root, "holder_verify", {
    authority: hardAuthority,
    idempotency_key: null,
    expected_version: null,
    owner: "agent-a",
    fence_operation_id: "a".repeat(64),
  });

  const first = await executeTaskLeaseLifecycle(request, {
    now: () => new Date("2026-09-01T03:01:00.000Z"),
  });
  const firstFence = first.fence as Record<string, unknown>;
  const retry = await executeTaskLeaseLifecycle(request, {
    now: () => new Date("2026-09-01T03:01:01.000Z"),
  });
  const retryFence = retry.fence as Record<string, unknown>;
  assert.equal(retry.ok, true);
  assert.equal(retryFence.idempotent, true);
  assert.equal(retryFence.lock_token, firstFence.lock_token);
  assert.equal(retryFence.fence_operation_id, firstFence.fence_operation_id);

  const firstClose = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "fence_close", {
      authority: null,
      owner: null,
      idempotency_key: null,
      expected_version: null,
      lock_token: firstFence.lock_token,
      committed: false,
      release_lease: false,
      fence_owner: "agent-a",
      fence_idempotency_key: null,
      fence_expected_version: 1,
      fence_expected_lease_epoch: 1,
      fence_operation_id: firstFence.fence_operation_id,
    }),
  );
  assert.equal(firstClose.ok, true);
  assert.equal(firstClose.released, false);

  const fresh = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "holder_verify", {
      authority: hardAuthority,
      idempotency_key: null,
      expected_version: null,
      owner: "agent-a",
    }),
    { now: () => new Date("2026-09-01T03:01:02.000Z") },
  );
  const freshFence = fresh.fence as Record<string, unknown>;
  assert.equal(fresh.ok, true);
  assert.notEqual(freshFence.fence_operation_id, firstFence.fence_operation_id);
  assert.notEqual(freshFence.lock_token, firstFence.lock_token);

  const freshClose = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "fence_close", {
      authority: null,
      owner: null,
      idempotency_key: null,
      expected_version: null,
      lock_token: freshFence.lock_token,
      committed: false,
      release_lease: false,
      fence_owner: "agent-a",
      fence_idempotency_key: null,
      fence_expected_version: 1,
      fence_expected_lease_epoch: 1,
      fence_operation_id: freshFence.fence_operation_id,
    }),
  );
  assert.equal(freshClose.ok, true);
});

test("fence close retires a lease that expires after verification", async (t) => {
  const root = await workspace(t);
  await executeTaskLeaseAcquire(
    await acquireRequest(root, { ttl_seconds: 1 }),
    { now: () => ACQUIRE_NOW },
  );

  const checked = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "terminal_verify"),
    { now: () => new Date("2026-09-01T03:00:00.500Z") },
  );
  assert.equal(checked.ok, true);
  const fence = checked.fence as Record<string, unknown>;

  const closed = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "fence_close", {
      authority: null,
      owner: null,
      idempotency_key: null,
      expected_version: null,
      lock_token: fence.lock_token,
      committed: true,
      release_lease: true,
      fence_owner: "agent-a",
      fence_idempotency_key: "lease-a",
      fence_expected_version: 1,
    }),
    { now: () => new Date("2026-09-01T03:00:02.000Z") },
  );

  assert.equal(closed.ok, true);
  assert.equal(closed.released, true);
  assert.equal((await lease(root)).status, "released");
  assert.equal((await lease(root)).version, 1);
  assert.equal((await lease(root)).lease_epoch, 1);
});

test("fence close write failure leaves the lease unchanged and unlocks", async (t) => {
  const root = await workspace(t);
  await executeTaskLeaseAcquire(await acquireRequest(root), { now: () => ACQUIRE_NOW });
  const checked = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "terminal_verify"),
    { now: () => new Date("2026-09-01T03:01:00.000Z") },
  );
  const fence = checked.fence as Record<string, unknown>;

  const failed = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "fence_close", {
      authority: null,
      owner: null,
      idempotency_key: null,
      expected_version: null,
      lock_token: fence.lock_token,
      committed: true,
      release_lease: true,
      fence_owner: "agent-a",
      fence_idempotency_key: "lease-a",
      fence_expected_version: 1,
    }),
    {
      now: () => new Date("2026-09-01T03:02:00.000Z"),
      beforeWrite: () => {
        throw new Error("simulated fence release write failure");
      },
    },
  );
  assert.equal(failed.ok, false);
  assert.equal(failed.error_code, "unexpected_handler_error");
  assert.equal((await lease(root)).status, "active");

  const cleanup = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "release", { authority: null }),
    { now: () => new Date("2026-09-01T03:03:00.000Z") },
  );
  assert.equal(cleanup.ok, true);
  assert.equal(cleanup.released, true);
});

test("user-gate auto-acquire returns a persistent fence that can be closed", async (t) => {
  const root = await workspace(t);
  const runtimeShadow = {
    schema_version: "loopx_coordination_runtime_shadow_binding_v0",
    provider: "file_v0",
  };
  const facts = await authority(root, {
    todos: [{
      todo_id: "todo_target",
      status: "open",
      claimed_by: null,
      excluded_agents: [],
      role: "user",
      task_class: "user_gate",
    }],
  });
  const checked = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "terminal_verify", {
      authority: facts,
      owner: "agent-a",
      idempotency_key: null,
      expected_version: null,
      allow_user_gate_auto_acquire: true,
      runtime_shadow: runtimeShadow,
    }),
    { now: () => ACQUIRE_NOW },
  );
  assert.equal(checked.ok, true);
  const autoCapture = checked.coordination_runtime_shadow_capture as Record<string, unknown>;
  assert.equal(autoCapture.entry_id, null);
  assert.equal(autoCapture.seq, null);
  assert.equal(autoCapture.skipped_reason, "bootstrap_required");
  assert.equal(autoCapture.failure, null);
  const fence = checked.fence as Record<string, unknown>;
  assert.equal(fence.auto_acquired, true);
  assert.equal((await lease(root)).status, "active");

  const closed = await executeTaskLeaseLifecycle({
    ...await lifecycleRequest(root, "fence_close", {
      authority: null,
      owner: null,
      idempotency_key: null,
      expected_version: null,
      lock_token: fence.lock_token,
      committed: true,
      release_lease: true,
      fence_owner: "agent-a",
      fence_idempotency_key: (await lease(root)).idempotency_key,
      fence_expected_version: (await lease(root)).version,
      runtime_shadow: runtimeShadow,
    }),
  }, { now: () => new Date("2026-09-01T03:01:00.000Z") });
  assert.equal(closed.ok, true);
  assert.equal(closed.released, true);
  const closeCapture = closed.coordination_runtime_shadow_capture as Record<string, unknown>;
  assert.equal(closeCapture.entry_id, null);
  assert.equal(closeCapture.seq, null);
  assert.equal(closeCapture.skipped_reason, "bootstrap_required");
  assert.equal(closeCapture.failure, null);
  assert.equal((await lease(root)).status, "released");
});

test("terminal fence retries an auto-acquire after the lease write window", async (t) => {
  const root = await workspace(t);
  const facts = await authority(root, {
    todos: [{
      todo_id: "todo_target",
      status: "open",
      claimed_by: null,
      excluded_agents: [],
      role: "user",
      task_class: "user_gate",
    }],
  });
  const request = await lifecycleRequest(root, "terminal_verify", {
    authority: facts,
    owner: "agent-a",
    idempotency_key: null,
    expected_version: null,
    allow_user_gate_auto_acquire: true,
  });
  let injected = false;
  const first = await executeTaskLeaseLifecycle(request, {
    now: () => ACQUIRE_NOW,
    beforeWrite: async (next) => {
      if (injected) return;
      injected = true;
      const leasePath = join(
        root,
        "runtime",
        "goals",
        "goal-a",
        "task-leases",
        "todo_target.json",
      );
      await atomicWriteJson(leasePath, next);
      throw new Error("simulated response loss after auto-acquire write");
    },
  });
  assert.equal(first.ok, false);
  assert.equal((await lease(root)).status, "active");

  const retry = await executeTaskLeaseLifecycle(request, {
    now: () => new Date("2026-09-01T03:00:01.000Z"),
  });
  assert.equal(retry.ok, true);
  const fence = retry.fence as Record<string, unknown>;
  assert.equal(fence.auto_acquired, true);
  assert.equal((await lease(root)).version, 1);

  const closed = await executeTaskLeaseLifecycle({
    ...await lifecycleRequest(root, "fence_close", {
      authority: null,
      owner: null,
      idempotency_key: null,
      expected_version: null,
      lock_token: fence.lock_token,
      fence_operation_id: fence.fence_operation_id,
      committed: true,
      release_lease: true,
      fence_owner: "agent-a",
      fence_idempotency_key: (await lease(root)).idempotency_key,
      fence_expected_version: 1,
      fence_expected_lease_epoch: 1,
    }),
  });
  assert.equal(closed.ok, true);
  assert.equal(closed.released, true);
});

test("auto-acquire keeps its token claim until the held receipt is durable", async (t) => {
  const root = await workspace(t);
  const facts = await authority(root, {
    todos: [{
      todo_id: "todo_target",
      status: "open",
      claimed_by: null,
      excluded_agents: [],
      role: "user",
      task_class: "user_gate",
    }],
  });
  const request = await lifecycleRequest(root, "terminal_verify", {
    authority: facts,
    owner: "agent-a",
    idempotency_key: null,
    expected_version: null,
    allow_user_gate_auto_acquire: true,
  });
  let resumeVerifier: () => void = () => {
    throw new Error("verifier gate was not initialized");
  };
  let verifierEntered: () => void = () => {
    throw new Error("verifier start gate was not initialized");
  };
  const verifierGate = new Promise<void>((resolve) => {
    resumeVerifier = resolve;
  });
  const verifierStarted = new Promise<void>((resolve) => {
    verifierEntered = resolve;
  });
  const leasePath = join(
    root,
    "runtime",
    "goals",
    "goal-a",
    "task-leases",
    "todo_target.json",
  );
  const verifier = executeTaskLeaseLifecycle(request, {
    now: () => ACQUIRE_NOW,
    beforeWrite: async (next) => {
      // Expose the exact response-loss window: the acquired receipt exists,
      // and the lease has been written, but the verifier has not published
      // its held receipt yet.
      await atomicWriteJson(leasePath, next);
      verifierEntered();
      await verifierGate;
    },
  });
  await verifierStarted;

  const receiptDirectory = join(
    root,
    "runtime",
    "goals",
    "goal-a",
    "task-leases",
    ".lifecycle-fences",
  );
  const [receiptName] = await readdir(receiptDirectory);
  const acquiredReceipt = JSON.parse(
    await readFile(join(receiptDirectory, receiptName), "utf8"),
  ) as Record<string, unknown>;
  assert.equal(acquiredReceipt.state, "acquired");
  assert.equal(typeof acquiredReceipt.lock_token, "string");

  let interruptedClose: Record<string, unknown>;
  try {
    interruptedClose = await executeTaskLeaseLifecycle(
      await lifecycleRequest(root, "fence_close", {
        authority: null,
        owner: null,
        idempotency_key: null,
        expected_version: null,
        lock_token: acquiredReceipt.lock_token,
        fence_operation_id: acquiredReceipt.operation_id,
        committed: false,
        release_lease: false,
        fence_owner: "agent-a",
        fence_idempotency_key: acquiredReceipt.fence_idempotency_key,
        fence_expected_version: acquiredReceipt.fence_expected_version,
        fence_expected_lease_epoch: acquiredReceipt.fence_expected_lease_epoch,
      }),
    );
  } finally {
    // Keep the test from hanging if the contention assertion fails.
    resumeVerifier();
  }
  assert.equal(interruptedClose.ok, false);
  assert.equal(interruptedClose.error_code, "fence_token_invalid");

  const checked = await verifier;
  assert.equal(checked.ok, true);
  const heldReceipt = JSON.parse(
    await readFile(join(receiptDirectory, receiptName), "utf8"),
  ) as Record<string, unknown>;
  assert.equal(heldReceipt.state, "held");

  const fence = checked.fence as Record<string, unknown>;
  const cleanup = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "fence_close", {
      authority: null,
      owner: null,
      idempotency_key: null,
      expected_version: null,
      lock_token: fence.lock_token,
      fence_operation_id: fence.fence_operation_id,
      committed: false,
      release_lease: false,
      fence_owner: "agent-a",
      fence_idempotency_key: "auto-todo_target",
      fence_expected_version: 1,
      fence_expected_lease_epoch: 1,
    }),
  );
  assert.equal(cleanup.ok, true);
});

test("a concurrent fence verifier cannot reopen a receipt while close owns its claim", async (t) => {
  const root = await workspace(t);
  await executeTaskLeaseAcquire(await acquireRequest(root), { now: () => ACQUIRE_NOW });
  const verifyRequest = await lifecycleRequest(root, "terminal_verify");
  const checked = await executeTaskLeaseLifecycle(verifyRequest, {
    now: () => new Date("2026-09-01T03:01:00.000Z"),
  });
  assert.equal(checked.ok, true);
  const fence = checked.fence as Record<string, unknown>;

  let releaseClose: () => void = () => {
    throw new Error("close gate was not initialized");
  };
  let closeEntered: () => void = () => {
    throw new Error("close start gate was not initialized");
  };
  const closeGate = new Promise<void>((resolve) => {
    releaseClose = resolve;
  });
  const closeHookEntered = new Promise<void>((resolve) => {
    closeEntered = resolve;
  });
  const closePromise = executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "fence_close", {
      authority: null,
      owner: null,
      idempotency_key: null,
      expected_version: null,
      lock_token: fence.lock_token,
      fence_operation_id: fence.fence_operation_id,
      committed: true,
      release_lease: true,
      fence_owner: "agent-a",
      fence_idempotency_key: "lease-a",
      fence_expected_version: 1,
      fence_expected_lease_epoch: 1,
    }),
    {
      beforeWrite: async () => {
        closeEntered();
        await closeGate;
      },
    },
  );
  await closeHookEntered;

  let concurrentRetry: Record<string, unknown>;
  try {
    concurrentRetry = await executeTaskLeaseLifecycle(verifyRequest, {
      now: () => new Date("2026-09-01T03:01:01.000Z"),
    });
  } finally {
    // Always unblock the close attempt so a failed assertion cannot leave a
    // pending promise holding the test process open.
    releaseClose();
  }
  assert.equal(concurrentRetry.ok, false);
  assert.equal(concurrentRetry.error_code, "fence_token_invalid");

  const closed = await closePromise;
  assert.equal(closed.ok, true);
  assert.equal(closed.released, true);
  const receiptPath = join(
    root,
    "runtime",
    "goals",
    "goal-a",
    "task-leases",
    ".lifecycle-fences",
    `${fence.fence_operation_id}.json`,
  );
  const receipt = JSON.parse(await readFile(receiptPath, "utf8")) as Record<string, unknown>;
  assert.equal(receipt.state, "closed");
  assert.equal((receipt.response as Record<string, unknown>).action, "fence_close");
});

test("held and closed fence receipts replay without changing their intent", async (t) => {
  const root = await workspace(t);
  await executeTaskLeaseAcquire(await acquireRequest(root), { now: () => ACQUIRE_NOW });
  const verifyRequest = await lifecycleRequest(root, "terminal_verify");
  const first = await executeTaskLeaseLifecycle(verifyRequest, {
    now: () => new Date("2026-09-01T03:01:00.000Z"),
  });
  assert.equal(first.ok, true);
  const firstFence = first.fence as Record<string, unknown>;
  const replay = await executeTaskLeaseLifecycle(verifyRequest, {
    now: () => new Date("2026-09-01T03:01:01.000Z"),
  });
  assert.equal(replay.ok, true);
  assert.equal(replay.fence?.lock_token, firstFence.lock_token);
  assert.equal(replay.fence?.idempotent, true);

  const closeRequest = {
    ...await lifecycleRequest(root, "fence_close", {
      authority: null,
      owner: null,
      idempotency_key: null,
      expected_version: null,
      lock_token: firstFence.lock_token,
      fence_operation_id: firstFence.fence_operation_id,
      committed: true,
      release_lease: true,
      fence_owner: "agent-a",
      fence_idempotency_key: "lease-a",
      fence_expected_version: 1,
      fence_expected_lease_epoch: 1,
    }),
  };
  const closed = await executeTaskLeaseLifecycle(closeRequest);
  assert.equal(closed.ok, true);
  assert.equal(closed.released, true);
  const staleCleanupLockPath = join(
    root,
    "runtime",
    "goals",
    "goal-a",
    "task-leases",
    ".task-leases.ts-effect.lock",
  );
  await writeFile(
    staleCleanupLockPath,
    JSON.stringify({ pid: process.pid, token: firstFence.lock_token }),
    "utf8",
  );
  const closeReplay = await executeTaskLeaseLifecycle(closeRequest);
  assert.equal(closeReplay.ok, true);
  assert.equal(closeReplay.idempotent, true);
  await assert.rejects(readFile(staleCleanupLockPath), { code: "ENOENT" });
  const changedClose = await executeTaskLeaseLifecycle({
    ...closeRequest,
    release_lease: false,
  });
  assert.equal(changedClose.ok, false);
  assert.equal(changedClose.error_code, "fence_operation_reuse");
});

test("closed non-required terminal fences replay without a retired lock token", async (t) => {
  const root = await workspace(t);
  const request = await lifecycleRequest(root, "terminal_verify", {
    authority: await authority(root, { handoff_mode: "legacy" }),
    idempotency_key: "completion-turn-a",
    expected_version: null,
    require_active_when_fence_supplied: false,
  });

  const first = await executeTaskLeaseLifecycle(request, {
    now: () => new Date("2026-09-01T03:01:00.000Z"),
  });
  assert.equal(first.ok, true);
  const firstFence = first.fence as Record<string, unknown>;
  assert.equal(firstFence.required, false);
  assert.equal(firstFence.active, false);
  assert.equal(firstFence.lock_token, undefined);

  const hardReplay = await executeTaskLeaseLifecycle({
    ...request,
    authority: await authority(root),
  });
  assert.equal(hardReplay.ok, false);
  assert.equal(hardReplay.error_code, "handoff_mode_requires_lease");

  const replay = await executeTaskLeaseLifecycle(request, {
    now: () => new Date("2026-09-01T03:02:00.000Z"),
  });
  assert.equal(replay.ok, true);
  const replayFence = replay.fence as Record<string, unknown>;
  assert.equal(replayFence.required, false);
  assert.equal(replayFence.active, false);
  assert.equal(replayFence.closed, true);
  assert.equal(replayFence.idempotent, true);
  assert.equal(replayFence.lock_token, undefined);

  await assert.rejects(
    readFile(join(
      root,
      "runtime",
      "goals",
      "goal-a",
      "task-leases",
      ".task-leases.ts-effect.lock",
    )),
    { code: "ENOENT" },
  );
});

test("aborted required fence close re-verifies the same lease generation", async (t) => {
  const root = await workspace(t);
  await executeTaskLeaseAcquire(await acquireRequest(root), { now: () => ACQUIRE_NOW });
  const request = await lifecycleRequest(root, "terminal_verify");
  const first = await executeTaskLeaseLifecycle(request, {
    now: () => new Date("2026-09-01T03:01:00.000Z"),
  });
  assert.equal(first.ok, true);
  const firstFence = first.fence as Record<string, unknown>;

  const aborted = await executeTaskLeaseLifecycle({
    ...await lifecycleRequest(root, "fence_close", {
      authority: null,
      owner: null,
      idempotency_key: null,
      expected_version: null,
      lock_token: firstFence.lock_token,
      fence_operation_id: firstFence.fence_operation_id,
      committed: false,
      release_lease: false,
      fence_owner: "agent-a",
      fence_idempotency_key: "lease-a",
      fence_expected_version: 1,
      fence_expected_lease_epoch: 1,
    }),
  });
  assert.equal(aborted.ok, true);
  assert.equal(aborted.released, false);
  assert.equal((await lease(root)).status, "active");

  const retry = await executeTaskLeaseLifecycle(request, {
    now: () => new Date("2026-09-01T03:01:01.000Z"),
  });
  assert.equal(retry.ok, true);
  const retryFence = retry.fence as Record<string, unknown>;
  assert.equal(retryFence.required, true);
  assert.equal(retryFence.active, true);
  assert.notEqual(retryFence.lock_token, firstFence.lock_token);

  const cleanup = await executeTaskLeaseLifecycle({
    ...await lifecycleRequest(root, "fence_close", {
      authority: null,
      owner: null,
      idempotency_key: null,
      expected_version: null,
      lock_token: retryFence.lock_token,
      fence_operation_id: retryFence.fence_operation_id,
      committed: false,
      release_lease: false,
      fence_owner: "agent-a",
      fence_idempotency_key: "lease-a",
      fence_expected_version: 1,
      fence_expected_lease_epoch: 1,
    }),
  });
  assert.equal(cleanup.ok, true);
});

test("closed non-required fence cannot bypass a lease acquired later", async (t) => {
  const root = await workspace(t);
  const legacyRequest = await lifecycleRequest(root, "terminal_verify", {
    authority: await authority(root, { handoff_mode: "legacy" }),
    idempotency_key: "completion-turn-a",
    expected_version: null,
    require_active_when_fence_supplied: false,
  });
  const first = await executeTaskLeaseLifecycle(legacyRequest, {
    now: () => new Date("2026-09-01T03:01:00.000Z"),
  });
  assert.equal(first.ok, true);
  assert.equal((first.fence as Record<string, unknown>).required, false);

  await executeTaskLeaseAcquire(await acquireRequest(root), {
    now: () => new Date("2026-09-01T03:01:01.000Z"),
  });
  const replay = await executeTaskLeaseLifecycle(legacyRequest, {
    now: () => new Date("2026-09-01T03:01:02.000Z"),
  });
  assert.equal(replay.ok, false);
  assert.equal(replay.error_code, "lease_cas_mismatch");
  assert.equal((await lease(root)).status, "active");
});

test("closed terminal receipt rejects an old generation after lease reacquire", async (t) => {
  const root = await workspace(t);
  await executeTaskLeaseAcquire(await acquireRequest(root), { now: () => ACQUIRE_NOW });
  const request = await lifecycleRequest(root, "terminal_verify");
  const checked = await executeTaskLeaseLifecycle(request, {
    now: () => new Date("2026-09-01T03:01:00.000Z"),
  });
  assert.equal(checked.ok, true);
  const fence = checked.fence as Record<string, unknown>;
  const closed = await executeTaskLeaseLifecycle({
    ...await lifecycleRequest(root, "fence_close", {
      authority: null,
      owner: null,
      idempotency_key: null,
      expected_version: null,
      lock_token: fence.lock_token,
      fence_operation_id: fence.fence_operation_id,
      committed: true,
      release_lease: true,
      fence_owner: "agent-a",
      fence_idempotency_key: "lease-a",
      fence_expected_version: 1,
      fence_expected_lease_epoch: 1,
    }),
  });
  assert.equal(closed.ok, true);
  assert.equal((await lease(root)).status, "released");

  await executeTaskLeaseAcquire(await acquireRequest(root, {
    idempotency_key: "lease-b",
  }), {
    now: () => new Date("2026-09-01T03:02:00.000Z"),
  });
  assert.equal((await lease(root)).version, 2);
  assert.equal((await lease(root)).lease_epoch, 2);

  const replay = await executeTaskLeaseLifecycle(request, {
    now: () => new Date("2026-09-01T03:02:01.000Z"),
  });
  assert.equal(replay.ok, false);
  assert.equal(replay.error_code, "fence_state_invalid");
  assert.equal((await lease(root)).status, "active");
});

test("held fence replay revalidates the canonical todo authority", async (t) => {
  const root = await workspace(t);
  await executeTaskLeaseAcquire(await acquireRequest(root), { now: () => ACQUIRE_NOW });
  const request = await lifecycleRequest(root, "terminal_verify");
  const first = await executeTaskLeaseLifecycle(request, {
    now: () => new Date("2026-09-01T03:01:00.000Z"),
  });
  assert.equal(first.ok, true);

  const changedAuthority = await authority(root, {
    todos: [{
      todo_id: "todo_target",
      status: "open",
      claimed_by: "agent-b",
      excluded_agents: [],
    }],
  });
  const replay = await executeTaskLeaseLifecycle(
    { ...request, authority: changedAuthority },
    { now: () => new Date("2026-09-01T03:01:01.000Z") },
  );
  assert.equal(replay.ok, false);
  assert.equal(replay.error_code, "authority_todo_mismatch");

  const cleanup = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "release", { authority: null }),
    { now: () => new Date("2026-09-01T03:02:00.000Z") },
  );
  assert.equal(cleanup.ok, true);
  assert.equal(cleanup.released, true);
});

test("partial caller Todo snapshots inherit derived canonical metadata", async (t) => {
  const root = await workspace(t);
  await executeTaskLeaseAcquire(await acquireRequest(root), { now: () => ACQUIRE_NOW });
  const request = await lifecycleRequest(root, "terminal_verify", {
    authority: await authority(root, {
      todos: [{
        todo_id: "todo_target",
        status: "open",
        claimed_by: "agent-a",
        excluded_agents: [],
        task_class: "advancement_task",
      }],
    }),
    todo: {
      todo_id: "todo_target",
      status: "open",
      claimed_by: "agent-a",
      excluded_agents: [],
    },
  });
  const checked = await executeTaskLeaseLifecycle(request, {
    now: () => new Date("2026-09-01T03:01:00.000Z"),
  });
  assert.equal(checked.ok, true);
  const fence = checked.fence as Record<string, unknown>;
  const closed = await executeTaskLeaseLifecycle({
    ...await lifecycleRequest(root, "fence_close", {
      authority: null,
      owner: null,
      idempotency_key: null,
      expected_version: null,
      lock_token: fence.lock_token,
      fence_operation_id: fence.fence_operation_id,
      committed: false,
      release_lease: false,
      fence_owner: "agent-a",
      fence_idempotency_key: "lease-a",
      fence_expected_version: 1,
      fence_expected_lease_epoch: 1,
    }),
  });
  assert.equal(closed.ok, true);

  const explicitMismatch = await executeTaskLeaseLifecycle({
    ...request,
    todo: {
      todo_id: "todo_target",
      status: "open",
      claimed_by: "agent-a",
      excluded_agents: [],
      task_class: "continuous_monitor",
    },
  });
  assert.equal(explicitMismatch.ok, false);
  assert.equal(explicitMismatch.error_code, "authority_todo_mismatch");
});

test("fence operation replay is independent of the caller process id", async (t) => {
  const root = await workspace(t);
  await executeTaskLeaseAcquire(await acquireRequest(root), { now: () => ACQUIRE_NOW });
  const firstRequest = await lifecycleRequest(root, "terminal_verify", {
    owner_pid: 999999,
  });
  const first = await executeTaskLeaseLifecycle(firstRequest, {
    now: () => new Date("2026-09-01T03:01:00.000Z"),
  });
  assert.equal(first.ok, true);
  const firstFence = first.fence as Record<string, unknown>;

  const retry = await executeTaskLeaseLifecycle(
    { ...firstRequest, owner_pid: process.pid },
    { now: () => new Date("2026-09-01T03:01:01.000Z") },
  );
  assert.equal(retry.ok, true);
  const retryFence = retry.fence as Record<string, unknown>;
  assert.equal(retryFence.fence_operation_id, firstFence.fence_operation_id);
  assert.notEqual(retryFence.lock_token, firstFence.lock_token);

  const closed = await executeTaskLeaseLifecycle({
    ...await lifecycleRequest(root, "fence_close", {
      authority: null,
      owner: null,
      idempotency_key: null,
      expected_version: null,
      lock_token: retryFence.lock_token,
      fence_operation_id: retryFence.fence_operation_id,
      committed: true,
      release_lease: true,
      fence_owner: "agent-a",
      fence_idempotency_key: "lease-a",
      fence_expected_version: 1,
      fence_expected_lease_epoch: 1,
    }),
  });
  assert.equal(closed.ok, true);
  assert.equal(closed.released, true);
});

test("legacy fence receipts default omitted optional fields to null", async (t) => {
  const root = await workspace(t);
  await executeTaskLeaseAcquire(await acquireRequest(root), { now: () => ACQUIRE_NOW });
  const request = await lifecycleRequest(root, "terminal_verify");
  const first = await executeTaskLeaseLifecycle(request, {
    now: () => new Date("2026-09-01T03:01:00.000Z"),
  });
  assert.equal(first.ok, true);
  const fence = first.fence as Record<string, unknown>;
  const receiptDirectory = join(
    root,
    "runtime",
    "goals",
    "goal-a",
    "task-leases",
    ".lifecycle-fences",
  );
  const [name] = await readdir(receiptDirectory);
  const receiptPath = join(receiptDirectory, name);
  const receipt = JSON.parse(await readFile(receiptPath, "utf8")) as Record<string, unknown>;
  delete receipt.close_request_digest;
  delete receipt.verify_response;
  await writeFile(receiptPath, JSON.stringify(receipt), "utf8");

  const replay = await executeTaskLeaseLifecycle(request, {
    now: () => new Date("2026-09-01T03:01:01.000Z"),
  });
  assert.equal(replay.ok, true);
  assert.equal(replay.fence?.idempotent, true);

  const closed = await executeTaskLeaseLifecycle({
    ...await lifecycleRequest(root, "fence_close", {
      authority: null,
      owner: null,
      idempotency_key: null,
      expected_version: null,
      lock_token: fence.lock_token,
      fence_operation_id: fence.fence_operation_id,
      committed: true,
      release_lease: true,
      fence_owner: "agent-a",
      fence_idempotency_key: "lease-a",
      fence_expected_version: 1,
      fence_expected_lease_epoch: 1,
    }),
  });
  assert.equal(closed.ok, true);

  const cleanup = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "release", { authority: null }),
    { now: () => new Date("2026-09-01T03:02:00.000Z") },
  );
  assert.equal(cleanup.ok, true);
  assert.equal(cleanup.released, true);
});

test("fence close rejects a stale lease epoch without releasing the lease", async (t) => {
  const root = await workspace(t);
  await executeTaskLeaseAcquire(await acquireRequest(root), { now: () => ACQUIRE_NOW });
  const checked = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "terminal_verify"),
    { now: () => new Date("2026-09-01T03:01:00.000Z") },
  );
  const fence = checked.fence as Record<string, unknown>;
  const staleClose = await executeTaskLeaseLifecycle({
    ...await lifecycleRequest(root, "fence_close", {
      authority: null,
      owner: null,
      idempotency_key: null,
      expected_version: null,
      lock_token: fence.lock_token,
      fence_operation_id: fence.fence_operation_id,
      committed: true,
      release_lease: true,
      fence_owner: "agent-a",
      fence_idempotency_key: "lease-a",
      fence_expected_version: 1,
      fence_expected_lease_epoch: 0,
    }),
  });
  assert.equal(staleClose.ok, false);
  assert.equal(staleClose.error_code, "fence_cas_mismatch");
  assert.equal((await lease(root)).status, "active");

  await assert.rejects(
    readFile(join(
      root,
      "runtime",
      "goals",
      "goal-a",
      "task-leases",
      ".task-leases.ts-effect.lock",
    )),
    { code: "ENOENT" },
  );
});

test("fence operation ids are strict and caller Todo snapshots cannot override authority", async (t) => {
  const root = await workspace(t);
  const invalid = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "terminal_verify", {
      fence_operation_id: "../../escape",
    }),
  );
  assert.equal(invalid.ok, false);
  assert.equal(invalid.error_code, "invalid_fence_operation_id");

  const mismatch = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "terminal_verify", {
      todo: {
        todo_id: "todo_target",
        status: "open",
        claimed_by: "agent-b",
        excluded_agents: [],
      },
    }),
  );
  assert.equal(mismatch.ok, false);
  assert.equal(mismatch.error_code, "authority_todo_mismatch");
});

test("prepared lifecycle receipts recover after a pre-write failure", async (t) => {
  const root = await workspace(t);
  await executeTaskLeaseAcquire(await acquireRequest(root), { now: () => ACQUIRE_NOW });
  const request = await lifecycleRequest(root, "renew");
  const failed = await executeTaskLeaseLifecycle(request, {
    now: () => new Date("2026-09-01T03:01:00.000Z"),
    beforeWrite: () => {
      throw new Error("simulated response loss");
    },
  });
  assert.equal(failed.ok, false);
  assert.equal((await lease(root)).version, 1);

  const retry = await executeTaskLeaseLifecycle(request, {
    now: () => new Date("2026-09-01T03:01:01.000Z"),
  });
  assert.equal(retry.ok, true);
  assert.equal(retry.renewed, true);
  assert.equal((await lease(root)).version, 2);
});

test("release reports missing leases and validates the version first", async (t) => {
  const root = await workspace(t);
  const missingVersion = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "release", {
      authority: null,
      expected_version: null,
    }),
  );
  assert.equal(missingVersion.ok, false);
  assert.equal(missingVersion.error_code, "version_required");

  const missing = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "release", {
      authority: null,
      expected_version: 0,
    }),
  );
  assert.equal(missing.ok, true);
  assert.equal(missing.released, false);
  assert.equal(missing.missing, true);
});

test("release accepts an expired active lease and replays its tombstone", async (t) => {
  const root = await workspace(t);
  await executeTaskLeaseAcquire(
    await acquireRequest(root, { ttl_seconds: 1 }),
    { now: () => ACQUIRE_NOW },
  );
  const request = await lifecycleRequest(root, "release", {
    authority: null,
    expected_version: 1,
  });
  const first = await executeTaskLeaseLifecycle(request, {
    now: () => new Date("2026-09-01T03:00:02.000Z"),
  });
  const afterFirst = await lease(root);
  const replay = await executeTaskLeaseLifecycle(request, {
    now: () => new Date("2026-09-01T04:00:00.000Z"),
  });
  assert.equal(first.ok, true);
  assert.equal(first.released, true);
  assert.equal(replay.ok, true);
  assert.equal(replay.idempotent, true);
  assert.deepEqual(await lease(root), afterFirst);
});

test("release rejects owner, key, and version drift without mutation", async (t) => {
  const root = await workspace(t);
  await executeTaskLeaseAcquire(await acquireRequest(root), { now: () => ACQUIRE_NOW });
  const ownerMismatch = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "release", {
      authority: null,
      owner: "agent-b",
      expected_version: 1,
    }),
  );
  assert.equal(ownerMismatch.ok, false);
  assert.equal(ownerMismatch.error_code, "lease_cas_mismatch");

  const keyMismatch = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "release", {
      authority: null,
      idempotency_key: "lease-other",
      expected_version: 1,
    }),
  );
  assert.equal(keyMismatch.ok, false);
  assert.equal(keyMismatch.error_code, "lease_cas_mismatch");

  const versionMismatch = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "release", {
      authority: null,
      expected_version: 0,
    }),
  );
  assert.equal(versionMismatch.ok, false);
  assert.equal(versionMismatch.error_code, "version_mismatch");
  assert.equal((await lease(root)).status, "active");
});

test("release retry after response loss commits one tombstone", async (t) => {
  const root = await workspace(t);
  await executeTaskLeaseAcquire(await acquireRequest(root), { now: () => ACQUIRE_NOW });
  const request = await lifecycleRequest(root, "release", { authority: null });
  const failed = await executeTaskLeaseLifecycle(request, {
    now: () => new Date("2026-09-01T03:01:00.000Z"),
    beforeWrite: () => {
      throw new Error("simulated response loss");
    },
  });
  assert.equal(failed.ok, false);
  assert.equal((await lease(root)).status, "active");

  const committed = await executeTaskLeaseLifecycle(request, {
    now: () => new Date("2026-09-01T03:02:00.000Z"),
  });
  assert.equal(committed.ok, true);
  assert.equal(committed.released, true);
  assert.equal((await lease(root)).status, "released");

  const replay = await executeTaskLeaseLifecycle(request, {
    now: () => new Date("2026-09-01T03:03:00.000Z"),
  });
  assert.equal(replay.ok, true);
  assert.equal(replay.idempotent, true);
  assert.equal((await lease(root)).status, "released");
});

test("malformed lifecycle receipts fail closed as receipt corruption", async (t) => {
  const root = await workspace(t);
  await executeTaskLeaseAcquire(await acquireRequest(root), { now: () => ACQUIRE_NOW });
  const request = await lifecycleRequest(root, "renew");
  await executeTaskLeaseLifecycle(request, {
    now: () => new Date("2026-09-01T03:01:00.000Z"),
    beforeWrite: () => {
      throw new Error("leave prepared receipt");
    },
  });
  const receiptDirectory = join(
    root,
    "runtime",
    "goals",
    "goal-a",
    "task-leases",
    ".lifecycle-operations",
  );
  const [receiptName] = await readdir(receiptDirectory);
  const receiptPath = join(receiptDirectory, receiptName);
  const receipt = JSON.parse(await readFile(receiptPath, "utf8")) as Record<string, unknown>;
  receipt.planned_lease = {
    ...(receipt.planned_lease as Record<string, unknown>),
    owner: 42,
  };
  await writeFile(receiptPath, JSON.stringify(receipt), "utf8");

  const rejected = await executeTaskLeaseLifecycle(request, {
    now: () => new Date("2026-09-01T03:02:00.000Z"),
  });
  assert.equal(rejected.ok, false);
  assert.equal(rejected.error_code, "corrupt_lifecycle_receipt");
  assert.equal((await lease(root)).version, 1);
});

test("lifecycle boundary rejects non-boolean flags and unsafe fence tokens", async (t) => {
  const root = await workspace(t);
  const invalidFlag = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "renew", {
      delegated_authority: "false",
    }),
  );
  assert.equal(invalidFlag.ok, false);
  assert.equal(invalidFlag.error_code, "invalid_request");

  const invalidToken = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "fence_close", {
      authority: null,
      owner: null,
      idempotency_key: null,
      expected_version: null,
      lock_token: "   ",
    }),
  );
  assert.equal(invalidToken.ok, false);
  assert.equal(invalidToken.error_code, "invalid_lock_token");
});

test("fence close keeps its held fence when the authority source changed", async (t) => {
  const root = await workspace(t);
  const runtimeShadow = {
    schema_version: "loopx_coordination_runtime_shadow_binding_v0",
    provider: "file_v0",
  };
  const hardAuthority = await authority(root, {
    todos: [{
      todo_id: "todo_target",
      status: "open",
      claimed_by: "agent-a",
      excluded_agents: [],
    }],
  });
  await executeTaskLeaseAcquire(
    await acquireRequest(root, { authority: hardAuthority }),
    { now: () => ACQUIRE_NOW },
  );
  const checked = await executeTaskLeaseLifecycle(
    await lifecycleRequest(root, "holder_verify", {
      authority: hardAuthority,
      idempotency_key: null,
      expected_version: null,
      owner: "agent-a",
    }),
    { now: () => new Date("2026-09-01T03:01:00.000Z") },
  );
  assert.equal(checked.ok, true);
  const fence = checked.fence as Record<string, unknown>;

  const closeRequest = await lifecycleRequest(root, "fence_close", {
    authority: hardAuthority,
    owner: null,
    idempotency_key: null,
    expected_version: null,
    lock_token: fence.lock_token,
    committed: true,
    release_lease: true,
    fence_owner: "agent-a",
    fence_idempotency_key: null,
    fence_expected_version: 1,
    runtime_shadow: runtimeShadow,
  });
  // An equivalent rewrite of the canonical registry after the close snapshot.
  // The decision facts are unchanged; only the source bytes moved.
  const rewritten = await authoritySource(root, "authority-v2");

  const stale = await executeTaskLeaseLifecycle(closeRequest, {
    now: () => new Date("2026-09-01T03:02:00.000Z"),
  });
  assert.equal(stale.ok, false);
  assert.equal(stale.error_code, "authority_source_changed");
  assert.equal((await lease(root)).status, "active");

  // The adapter answers a source mismatch by re-reading the graph and retrying
  // the same held fence.  A retryable precondition must not have consumed it.
  const retried = await executeTaskLeaseLifecycle({
    ...closeRequest,
    authority: { ...hardAuthority, source_receipts: [rewritten] },
  }, { now: () => new Date("2026-09-01T03:02:01.000Z") });
  assert.equal(retried.ok, true);
  assert.equal(retried.released, true);
  assert.equal((await lease(root)).status, "released");
});

for (const dimension of ["version", "epoch"] as const) {
  test(`legacy lifecycle rejects exhausted ${dimension} before persistent mutation`, async t => {
    const root = await workspace(t);
    const acquired = await executeTaskLeaseAcquire(await acquireRequest(root)); assert.equal(acquired.ok, true);
    const path = join(root, "runtime", "goals", "goal-a", "task-leases", "todo_target.json");
    const lease = JSON.parse(await readFile(path, "utf8"));
    lease[dimension === "version" ? "version" : "lease_epoch"] = Number.MAX_SAFE_INTEGER;
    await writeFile(path, JSON.stringify(lease)); const before = await readFile(path, "utf8");
    const result = await executeTaskLeaseLifecycle(await lifecycleRequest(root, dimension === "version" ? "renew" : "transfer", {
      expected_version: lease.version,
      ...(dimension === "epoch" ? {new_owner: "agent-b", new_idempotency_key: "lease-b"} : {}),
    }));
    assert.equal(result.error_code, "lease_generation_exhausted", JSON.stringify(result));
    assert.equal(await readFile(path, "utf8"), before);
  });
}
