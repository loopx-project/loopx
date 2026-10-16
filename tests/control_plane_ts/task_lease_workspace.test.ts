import assert from "node:assert/strict";
import test from "node:test";
import {execFileSync} from "node:child_process";
import {mkdtemp, rm, mkdir, writeFile, symlink, realpath} from "node:fs/promises";
import {platform, tmpdir} from "node:os";
import {join} from "node:path";
import {evaluateTaskLeaseAcquireDecision, evaluateTaskLeaseWriteScopesOverlap} from "../../loopx/control_plane/work_items/task_lease_acquire_decision.ts";
import {leaseWorkspace, sameLeaseCheckout, observeLeaseWorktree} from "../../loopx/control_plane/work_items/task_lease_workspace.ts";

const repo = "git:github.com/example/project";
const workspace = {host: "1".repeat(64), common_directory: "2".repeat(64), worktree: "3".repeat(64), repository: repo};
test("native observation never interprets a caller path relative to the worker", async () => {
  await assert.rejects(observeLeaseWorktree(".", () => false), /absolute/);
});

test("observed Git origins use the Todo repository identity contract", {
  skip: !["darwin", "linux", "win32"].includes(platform()),
}, async t => {
  const temporary = await mkdtemp(join(tmpdir(), "loopx-lease-origin-"));
  t.after(() => rm(temporary, {recursive: true, force: true}));
  const main = join(temporary, "main"), worktree = join(temporary, "worktree");
  const git = (...args: string[]) => execFileSync("git", args, {stdio: "pipe"});
  git("init", main);
  git("-C", main, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com",
    "commit", "--allow-empty", "-m", "fixture");
  git("-C", main, "worktree", "add", "-b", "fixture", worktree);
  for (const [origin, expected] of [
    ["https://GITHUB.com:443/example/project.git", repo],
    ["ssh://git@github.com:22/example/project.git", repo],
    ["git://github.com:9418/example/project.git", repo],
    ["git@github.com:example/project.git", repo],
    ["ssh://git@github.com/example//project.git", repo],
    ["ssh://git@github.com:2222/example/project.git", "git:github.com:2222/example/project"],
  ]) {
    await t.test(origin, async () => {
      git("-C", main, "config", "remote.origin.url", origin);
      const observed = await observeLeaseWorktree(worktree, () => false);
      assert.equal(observed.repository, expected);
      const input = request();
      const decision = evaluateTaskLeaseAcquireDecision({...input, other_leases: [],
        todo: {...input.todo, task_repository: expected}, command: {...input.command, write_workspace: observed}});
      assert.equal(decision.outcome, "apply");
    });
  }
  for (const origin of [
    "https://fixture:fixture@github.com/example/project.git",
    "https://github.com/example/../project.git",
    "https://github.com/example\\project.git",
    "https://github.com/example/project.git?query=fixture",
    "https://github.com/example/project.git#fragment",
    "file:///example/project.git",
    "git:github.com/example/project.git",
  ]) {
    await t.test(`reject ${origin}`, async () => {
      git("-C", main, "config", "remote.origin.url", origin);
      await assert.rejects(observeLeaseWorktree(worktree, () => false));
    });
  }
});

// Creating file symlinks needs Developer Mode or administrator rights on
// Windows; the machine-identity path is covered by the observation test above.
test("normal executable file links preserve the declared independent checkout", {
  skip: !["darwin", "linux"].includes(platform()),
}, async t => {
  const temporary = await mkdtemp(join(tmpdir(), "loopx-lease-links-"));
  t.after(() => rm(temporary, {recursive: true, force: true}));
  const main = join(temporary, "main"), checkout = join(temporary, "worktree");
  const git = (...args: string[]) => execFileSync("git", args, {stdio: "pipe"});
  git("init", main);
  git("-C", main, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com",
    "commit", "--allow-empty", "-m", "fixture");
  git("-C", main, "config", "remote.origin.url", "https://github.com/example/project.git");
  git("-C", main, "worktree", "add", "-b", "fixture", checkout);
  const worktree = await realpath(checkout);
  const overlaps = (path: string) => evaluateTaskLeaseWriteScopesOverlap({left: ["src/**"], right: [path]}).overlap === true;
  const bin = join(worktree, "src/node_modules/.bin/tool");
  const target = join(worktree, "src/node_modules/tool/bin.mjs");
  await mkdir(join(worktree, "src/node_modules/.bin"), {recursive: true});
  await mkdir(join(worktree, "src/node_modules/tool"), {recursive: true});
  await writeFile(join(worktree, ".gitignore"), "node_modules/\n");
  await writeFile(target, "export {};\n");
  const original = await observeLeaseWorktree(worktree, overlaps);
  for (const link of ["../tool/bin.mjs", target]) {
    await symlink(link, bin);
    assert.deepEqual(await observeLeaseWorktree(worktree, overlaps), original);
    await rm(bin);
  }

  // Ignored dependencies are still inspected, and the same path must recover
  // after a rejected redirection without changing scopes or checkout identity.
  const outside = join(temporary, "outside.mjs");
  await writeFile(outside, "export {};\n");
  await symlink(outside, bin);
  await assert.rejects(observeLeaseWorktree(worktree, overlaps), /file links/);
  await rm(bin);
  await symlink("../tool/bin.mjs", bin);
  assert.deepEqual(await observeLeaseWorktree(worktree, overlaps), original);

  await rm(bin);
  for (const [name, destination] of [
    ["directory", "../tool"], ["missing", "../tool/missing.mjs"],
    ["self cycle", "tool"], ["Git administration", "../../../.git"],
    ["out of scope", "../../../elsewhere.mjs"],
  ]) {
    await t.test(name, async () => {
      await writeFile(join(worktree, "elsewhere.mjs"), "export {};\n");
      await symlink(destination, bin);
      await assert.rejects(observeLeaseWorktree(worktree, overlaps), /file links/);
      await rm(bin);
    });
  }
  await symlink(target, join(worktree, "src/node_modules/tool/chain.mjs"));
  await symlink("../tool/chain.mjs", bin);
  await assert.rejects(observeLeaseWorktree(worktree, overlaps), /file links/);
  await rm(bin);
  await symlink(join(worktree, "src/node_modules/tool"), join(worktree, "src/alias"));
  await symlink(join(worktree, "src/alias/bin.mjs"), bin);
  await assert.rejects(observeLeaseWorktree(worktree, overlaps), /file links/);
  await rm(bin);
  await symlink(join(worktree, "src/node_modules/.bin"), join(worktree, "src/node_modules/alias"));
  await symlink("../alias/../tool/bin.mjs", bin);
  await assert.rejects(observeLeaseWorktree(worktree, overlaps), /file links/);
  await rm(bin);
  await mkdir(join(temporary, "outside-directory"));
  await symlink(`${worktree}/../outside-directory/../worktree/src/node_modules/tool/bin.mjs`, bin);
  await assert.rejects(observeLeaseWorktree(worktree, overlaps), /file links/);
  await rm(bin);

  // A redirected ancestor is rejected even before its requested leaf exists.
  await symlink(join(worktree, "src"), join(worktree, "future"));
  await assert.rejects(observeLeaseWorktree(worktree, path =>
    evaluateTaskLeaseWriteScopesOverlap({left: ["future/new/file.ts"], right: [path]}).overlap === true), /file links/);
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
