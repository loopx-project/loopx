import assert from "node:assert/strict";
import {access, mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";

import {
  executeTaskLeaseAcquire,
  TASK_LEASE_ACQUIRE_REQUEST_SCHEMA_VERSION,
} from "../../loopx/control_plane/work_items/task_lease_acquire.ts";
import {TASK_LEASE_CANONICAL_ACQUIRE_REQUEST_SCHEMA} from "../../loopx/control_plane/coordination/coordination_state_contract.generated.ts";

function request(runtimeRoot: string, schemaVersion: string) {
  return {
    schema_version: schemaVersion,
    runtime_root: runtimeRoot,
    goal_id: "goal-a",
    todo_id: "todo_target",
    owner: "agent-a",
    idempotency_key: "lease-a",
    ttl_seconds: 600,
    write_scopes: ["src/**"],
    expected_version: null,
    authority: {
      handoff_mode: "hard_lease",
      registered_agent_candidates: [["agent-a"]],
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
    },
  };
}

test("legacy task lease acquire returns the migration cutoff without writes", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "loopx-lease-acquire-cutoff-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const runtimeRoot = join(root, "runtime");

  const result = await executeTaskLeaseAcquire(
    request(runtimeRoot, TASK_LEASE_ACQUIRE_REQUEST_SCHEMA_VERSION),
  );

  assert.equal(result.ok, false);
  assert.equal(result.error_code, "canonical_authority_required");
  assert.equal(result.write_admitted, false);
  assert.equal(result.decision_read_from_provider, false);
  assert.equal(result.legacy_fallback_used, false);
  assert.deepEqual(result.recovery, {
    action: "migrate_goal_authority",
    backup_required: true,
  });
  await assert.rejects(access(join(runtimeRoot, "authority")));
  await assert.rejects(access(join(
    runtimeRoot,
    "goals",
    "goal-a",
    "task-leases",
    "todo_target.json",
  )));
});

test("canonical acquire checks the durable fence before opening a provider", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "loopx-lease-acquire-fence-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const runtimeRoot = join(root, "runtime");
  let opened = 0;

  const result = await executeTaskLeaseAcquire(
    request(runtimeRoot, TASK_LEASE_CANONICAL_ACQUIRE_REQUEST_SCHEMA),
    {
      authorityProvider: {
        createStore: () => {
          opened += 1;
          throw new Error("provider must remain closed");
        },
      },
    },
  );

  assert.equal(result.ok, false);
  assert.equal(result.error_code, "canonical_authority_required");
  assert.equal(result.write_admitted, false);
  assert.equal(result.decision_read_from_provider, false);
  assert.equal(result.legacy_fallback_used, false);
  assert.equal(opened, 0);
});

test("canonical acquire rejects noncanonical write scopes before opening a provider", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "loopx-lease-acquire-scope-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const runtimeRoot = join(root, "runtime");
  let opened = 0;

  const result = await executeTaskLeaseAcquire(
    {...request(runtimeRoot, TASK_LEASE_CANONICAL_ACQUIRE_REQUEST_SCHEMA), write_scopes: ["src\\main.ts"]},
    {
      authorityProvider: {
        createStore: () => {
          opened += 1;
          throw new Error("provider must remain closed");
        },
      },
    },
  );

  assert.equal(result.ok, false);
  assert.equal(result.error_code, "invalid_write_scopes");
  assert.equal(opened, 0);
});
