import assert from "node:assert/strict";
import test from "node:test";
import {evaluateTaskLeaseAcquireDecision} from "../../loopx/control_plane/work_items/task_lease_acquire_decision.ts";
import {leaseWorkspace, sameLeaseCheckout, observeLeaseWorktree} from "../../loopx/control_plane/work_items/task_lease_workspace.ts";

const repo = "git:github.com/example/project";
const workspace = {host: "1".repeat(64), common_directory: "2".repeat(64), worktree: "3".repeat(64), repository: repo};
test("native observation never interprets a caller path relative to the worker", async () => {
  await assert.rejects(observeLeaseWorktree(".", () => false), /absolute/);
});

function request(other: unknown = {...workspace, worktree: "4".repeat(64)}) {
  return {handoff_mode: "hard_lease", registered_agents: ["agent-a", "agent-b"],
    todo: {todo_id: "todo_a", status: "open", claimed_by: "agent-a", excluded_agents: [], task_repository: repo}, lease: null,
    other_leases: [{todo_id: "todo_b", active: true, effective: true, write_repository: repo as string | null, write_scopes: ["src/**"], write_workspace: other}],
    command: {owner: "agent-a", idempotency_key: "work-a", ttl_seconds: 60, expected_version: 0, write_scopes: ["src/main.ts"], write_workspace: workspace}};
}
test("verified code-edit worktrees treat file overlaps as integration advisories", () => {
  const result = evaluateTaskLeaseAcquireDecision(request());
  assert.equal(result.outcome, "apply");
  assert.deepEqual(result.overlap_advisory_indexes, [0]);
  assert.deepEqual(result.next_lease?.write_workspace, workspace);
  for (const other of [null, {...workspace, host: "5".repeat(64)},
    {...workspace, common_directory: "6".repeat(64), worktree: "7".repeat(64)}]) {
    const input = request(other);
    if (other === null) input.other_leases[0].write_repository = null;
    const advisory = evaluateTaskLeaseAcquireDecision(input);
    assert.equal(advisory.outcome, "apply");
    assert.deepEqual(advisory.overlap_advisory_indexes, [0]);
    assert.deepEqual(input.other_leases[0].write_workspace, other);
  }
  for (const other of [workspace, {...workspace, common_directory: "6".repeat(64)}]) {
    assert.equal(evaluateTaskLeaseAcquireDecision(request(other)).code, "write_scope_conflict");
    assert.equal(sameLeaseCheckout(workspace, other), true);
  }
});
test("ordinary exclusive grants retain overlap rejection without worktree mode", () => {
  for (const other of [null, workspace]) {
    const input = request(other);
    const exclusive = {...input, command: {...input.command, write_workspace: null}};
    assert.equal(evaluateTaskLeaseAcquireDecision(exclusive).code, "write_scope_conflict");
  }
});
test("a known same-checkout collision wins over changed repository metadata", () => {
  const other = {...workspace, repository: "git:github.com/example/other"};
  const input = request(other);
  input.other_leases[0].write_repository = other.repository;
  assert.equal(evaluateTaskLeaseAcquireDecision(input).code, "write_scope_conflict");
});
test("worktree identity cannot weaken Todo ownership or repository identity", () => {
  const input = request();
  assert.equal(evaluateTaskLeaseAcquireDecision({...input, todo: {...input.todo, claimed_by: "agent-b"}}).outcome, "rejected");
  assert.equal(evaluateTaskLeaseAcquireDecision({...input, command: {...input.command,
    write_workspace: {...workspace, repository: "git:github.com/example/other"}}}).code, "lease_workspace_repository_mismatch");
  assert.throws(() => leaseWorkspace({...workspace, worktree: ""}));
  assert.throws(() => leaseWorkspace({...workspace, unchecked_path: "/tmp"}));
});

test("PostgreSQL retains worktree identity and overlap receipts across reload", {skip: !process.env.LOOPX_TEST_POSTGRES_URL}, async () => {
  const {Pool} = await import("pg");
  const {randomUUID} = await import("node:crypto");
  const {PostgreSqlAuthorityStore, installPostgreSqlAuthorityStoreSchema} = await import("../../loopx/control_plane/coordination/postgresql_authority_store.ts");
  const {executeCanonicalTaskLeaseAcquire} = await import("../../loopx/control_plane/coordination/task_lease_acquire.ts");
  const {authorityProjectionFixture} = await import("./authority_projection_fixture.ts");
  const pool = new Pool({connectionString: process.env.LOOPX_TEST_POSTGRES_URL});
  const database = {connect: async () => {
    const client = await pool.connect();
    return {query: async (text: string, values?: readonly unknown[]) => client.query(text, values ? [...values] : undefined),
      release: () => client.release()};
  }};
  const goal = `workspace-${randomUUID()}`, options = {goal_id: goal, tenant_id: `tenant-${randomUUID()}`};
  try {
    await installPostgreSqlAuthorityStoreSchema(database, `postgresql:${"b".repeat(32)}`);
    const store = new PostgreSqlAuthorityStore(database, options);
    const todos = ["alpha", "beta", "legacy"].map(key => ({todo_id: `todo_${key}`, role: "agent", status: "open", done: false,
      archive_state: "active", task_class: "advancement_task", text: "Independent code edits", claimed_by: "agent-a", task_repository: repo}));
    assert.equal((await store.commitAuthority({expected_provider_revision: null, operation_id: "seed", events: [], receipts: [],
      next_projection: authorityProjectionFixture(goal, todos, [], "native", {handoff_mode: "hard_lease"})})).status, "applied");
    const command = {goal_id: goal, todo_id: "todo_alpha", owner: "agent-a", idempotency_key: "alpha",
      expected_version: 0, ttl_seconds: 600, write_scopes: ["src/**"], registered_agents: ["agent-a"], now: new Date(), write_workspace: workspace};
    const legacyCommand = {...command, todo_id: "todo_legacy", idempotency_key: "legacy", write_workspace: null};
    const legacy = await executeCanonicalTaskLeaseAcquire(store, legacyCommand);
    assert.equal(legacy.status, "applied");
    const first = await executeCanonicalTaskLeaseAcquire(store, command);
    assert.equal(first.status, "applied");
    assert.equal((first.integration_overlap_advisories as unknown[]).length, 1);
    const reopened = new PostgreSqlAuthorityStore(database, options);
    const replay = await executeCanonicalTaskLeaseAcquire(reopened, command);
    assert.deepEqual(replay.original_receipt, first.original_receipt);
    assert.deepEqual(replay.lease, first.lease);
    const second = await executeCanonicalTaskLeaseAcquire(reopened, {...command, todo_id: "todo_beta", idempotency_key: "beta",
      write_workspace: {...workspace, worktree: "4".repeat(64)}});
    assert.equal(second.status, "applied");
    assert.equal((second.integration_overlap_advisories as unknown[]).length, 2);
    const retained = await executeCanonicalTaskLeaseAcquire(reopened, legacyCommand);
    assert.deepEqual(retained.original_receipt, legacy.original_receipt);
    assert.deepEqual(retained.lease, legacy.lease);
  } finally {
    for (const table of ["authority_receipts", "authority_events", "authority_commits", "authority_heads"]) {
      await pool.query(`DELETE FROM loopx_control_plane.${table} WHERE tenant_id=$1 AND goal_id=$2`, [options.tenant_id, goal]);
    }
    await pool.end();
  }
});
