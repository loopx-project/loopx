import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {spawn} from "node:child_process";
import {once} from "node:events";
import {mkdtemp, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import {registryAuthoritySourceCheck} from "../../loopx/control_plane/coordination/authority_source.ts";
import {withRegistryAuthoritySources} from "../../loopx/control_plane/coordination/shadow_registry_source.ts";
import {mutationLockOwner, withFileMutationLock} from "../../loopx/control_plane/effect_runtime_io.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {coordinationTodoReadModel, TODO_CANONICAL_READ_RECORD_SCHEMA} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {TODO_ITEM_SCHEMA} from "../../loopx/control_plane/coordination/coordination_state_contract.ts";
import type {AuthorityStore, AuthorityStoreCommit} from "../../loopx/control_plane/coordination/authority_store.ts";
import {resolveTestPython} from "../../scripts/test-python.mjs";
import {engageFence, promotionRequest} from "./local_promotion_fixture.ts";
import {
  claimLocalCoordinationTodo, createLocalCoordinationTodo, updateLocalCoordinationTodo,
  terminalLifecycleLocalCoordinationTodo, pollLocalCoordinationMonitor,
  LOCAL_COORDINATION_TODO_TERMINAL_LIFECYCLE_REQUEST_SCHEMA,
  LOCAL_COORDINATION_TODO_CLAIM_GUARDED_REQUEST_SCHEMA,
} from "../../loopx/control_plane/coordination/local_authority_runtime.ts";

test("registry witness captures primitive values across awaited request mutation", async t => {
  const root = await mkdtemp(join(tmpdir(), "loopx-source-wire-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const path = join(root, "registry.json"), body = "original registration";
  await writeFile(path, body);
  const source = {path, sha256: createHash("sha256").update(body).digest("hex")};
  const check = registryAuthoritySourceCheck({registry_source: source}, true);
  const conflictingReview = registryAuthoritySourceCheck({registry_source: source}, true, "0".repeat(64));
  source.path = join(root, "missing.json"); source.sha256 = "0".repeat(64);
  assert.equal(await check(), true, "mutable decoded input cannot redirect the witness");
  assert.equal(await conflictingReview(), false);
  await writeFile(path, "revoked");
  assert.equal(await check(), false);
  await rm(path);
  assert.equal(await check(), false);
});

test("claim source guards reject missing, duplicate, omitted primary and legacy wire intent before provider IO", async t => {
  const root = await mkdtemp(join(tmpdir(), "loopx-claim-wire-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const path = join(root, "registry.json"), body = "{}";
  await writeFile(path, body);
  const primary = {path, sha256: createHash("sha256").update(body).digest("hex")};
  let opens = 0;
  const dependencies = {createStore: (): AuthorityStore => {opens++; throw new Error("unexpected provider IO");}};
  for (const registry_sources of [undefined, null, [], [null], [primary, primary],
    [{...primary, path: join(root, "broker.json")}], [{...primary, path: "relative"}],
    [{...primary, sha256: "invalid"}], [primary, primary, primary]]) {
    const result = await claimLocalCoordinationTodo({
      schema_version: LOCAL_COORDINATION_TODO_CLAIM_GUARDED_REQUEST_SCHEMA,
      registry_source: primary, registry_sources,
    }, dependencies);
    assert.equal(result.status, "failed");
    assert.equal(result.decision_read_from_provider, false);
    assert.equal(result.reason_code, "invalid_local_coordination_todo_claim_request");
  }
  for (const version of [0, 1]) for (const registry_sources of [null, [primary]]) {
    const result = await claimLocalCoordinationTodo({
      schema_version: `loopx_local_coordination_todo_claim_request_v${version}`, registry_sources,
    }, dependencies);
    assert.equal(result.failure_kind, "protocol_failure");
    assert.equal(result.decision_read_from_provider, false);
  }
  assert.equal(opens, 0);
});

test("source guards copy original witnesses, fail closed on drift or contention and release partial guards", async t => {
  const root = await mkdtemp(join(tmpdir(), "loopx-source-guards-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const a = {path: join(root, "a.json"), sha256: createHash("sha256").update("{}").digest("hex")};
  const b = {...a, path: join(root, "b.json")};
  await writeFile(a.path, "{}"); await writeFile(b.path, "{}");
  await withFileMutationLock(b.path, async () => {
    await assert.rejects(withRegistryAuthoritySources([a, b], a, async () => assert.fail("busy source reached effect")),
      {reason_code: "source_registry_busy_retry"});
    assert.equal(await mutationLockOwner(a.path), null, "earlier acquired guard is released on contention");
  });
  const sources = [a, b];
  const result = withRegistryAuthoritySources(sources, a, async () => {
    assert.equal((await mutationLockOwner(b.path))?.pid, process.pid);
    return "held original";
  });
  sources.length = 0; a.path = join(root, "redirect.json"); b.sha256 = "0".repeat(64);
  assert.equal(await result, "held original");
  const original = {...b, sha256: createHash("sha256").update("{}").digest("hex")};
  await writeFile(original.path, "changed");
  await assert.rejects(withRegistryAuthoritySources([original], original, async () => assert.fail("stale source reached effect")),
    {reason_code: "authority_source_changed"});
  await rm(original.path);
  await assert.rejects(withRegistryAuthoritySources([original], original, async () => assert.fail("missing source reached effect")),
    {reason_code: "authority_source_changed"});
});

for (const provider of ["file", "sqlite"] as const) test(`${provider}: native claim retains broker/source guards through real commit and Python writer`, {timeout: 20_000}, async t => {
  const root = await mkdtemp(join(tmpdir(), "loopx-claim-serialization-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const sources = await Promise.all(["broker.json", "source.json"].map(async name => {
    const path = join(root, name), body = JSON.stringify({goals: [], revision: 1});
    await writeFile(path, body);
    return {path, sha256: createHash("sha256").update(body).digest("hex")};
  }));
  const store: AuthorityStore = provider === "file" ? new FileAuthorityStore(join(root, "authority", "file-v0"), "goal-a")
    : new SqliteAuthorityStore(join(root, "authority", "sqlite-v0"), "goal-a");
  const todos = [{schema_version: TODO_ITEM_SCHEMA, todo_id: "todo_a", role: "agent", status: "open",
    done: false, text: "Claim the registered work", archive_state: "active", source_section: "Agent Todo", index: 1}];
  assert.equal((await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
    events: [], receipts: [], next_projection: {goal_id: "goal-a", handoff_mode: "soft_claim", todos, leases: [],
      todo_read_model: coordinationTodoReadModel(todos, TODO_CANONICAL_READ_RECORD_SCHEMA)}})).status, "applied");
  const request = {schema_version: LOCAL_COORDINATION_TODO_CLAIM_GUARDED_REQUEST_SCHEMA,
    registry_source: sources[1], registry_sources: sources, runtime_root: root, goal_id: "goal-a", todo_id: "todo_a",
    role: "agent", claimed_by: "agent-a", actor_agent_id: "agent-a", registered_agents: ["agent-a"],
    operation_id: "claim-a", observed_at: "2026-09-05T04:30:00Z", dry_run: false};
  const initial = await store.loadAuthority();
  assert.equal(initial.status, "loaded");
  if (initial.status !== "loaded") throw new Error("fixture authority missing");
  let unpromotedOpens = 0;
  const unpromoted = await claimLocalCoordinationTodo(request, {createStore: () => {
    unpromotedOpens++; return store;
  }});
  assert.equal(unpromoted.reason_code, "canonical_authority_required");
  assert.equal(unpromoted.write_admitted, false);
  assert.equal(unpromoted.decision_read_from_provider, false);
  assert.equal(unpromotedOpens, 0, "guarded claim cannot bypass canonical admission");
  assert.deepEqual(await store.loadAuthority(), initial);
  assert.deepEqual(await store.readReceipt("claim-a"), {status: "missing"});
  // A seeded store alone is not promoted authority. Reuse the explicit fixture
  // fence before exercising the commit, without weakening production admission.
  await writeFile(join(root, "ACTIVE_GOAL_STATE.md"), "## Agent Todo\n");
  await engageFence(promotionRequest(root, initial.head, initial.provider_revision,
    provider === "file" ? "file_v0" : "sqlite_v0"));
  let entered!: () => void, release!: () => void;
  const atCommit = new Promise<void>(resolve => {entered = resolve;});
  const resume = new Promise<void>(resolve => {release = resolve;});
  t.after(release);
  const guarded = new Proxy(store, {get(target, key) {
    if (key === "commitAuthority") return async (commit: AuthorityStoreCommit) => {
      entered(); await resume; return target.commitAuthority(commit);
    };
    const value = Reflect.get(target, key);
    return typeof value === "function" ? value.bind(target) : value;
  }});
  const claim = claimLocalCoordinationTodo(request, {createStore: () => guarded});
  await Promise.race([atCommit, claim.then(result => assert.fail(JSON.stringify(result)))]);
  for (const source of sources) assert.equal((await mutationLockOwner(source.path))?.pid, process.pid);
  // Use the real administration writer, not an uncoordinated writeFile probe.
  const writer = spawn(resolveTestPython(), ["-u", "-c", `
import sys
from pathlib import Path
from loopx.control_plane.projects.registry_codec import project_registry_transaction
print("attempting", flush=True)
for path in sys.argv[1:]:
    with project_registry_transaction(Path(path), operation="source-serialization-test") as tx:
        payload = tx.payload_copy()
        payload["revision"] = 2
        tx.commit(payload)
print("committed", flush=True)
`, ...sources.map(source => source.path)], {stdio: ["ignore", "pipe", "pipe"]});
  t.after(() => {writer.kill();});
  let output = "", error = "";
  writer.stdout.on("data", chunk => {output += String(chunk);});
  writer.stderr.on("data", chunk => {error += String(chunk);});
  const exit = once(writer, "exit");
  await once(writer.stdout, "data");
  assert.match(output, /attempting/u);
  assert.equal(await Promise.race([exit.then(() => true), new Promise<boolean>(resolve => setTimeout(() => resolve(false), 150))]), false);
  for (const source of sources) assert.equal(JSON.parse(await readFile(source.path, "utf8")).revision, 1);
  assert.deepEqual(await store.readReceipt("claim-a"), {status: "missing"});
  release();
  assert.equal((await claim).status, "applied");
  assert.equal((await store.readReceipt("claim-a")).status, "found");
  assert.equal((await exit)[0], 0, error);
  assert.match(output, /committed/u);
  for (const source of sources) {
    assert.equal(JSON.parse(await readFile(source.path, "utf8")).revision, 2);
    assert.equal(await mutationLockOwner(source.path), null);
  }
  const before = await store.loadAuthority();
  assert.equal((await claimLocalCoordinationTodo(request, {createStore: () => store})).reason_code, "authority_source_changed");
  assert.deepEqual(await store.loadAuthority(), before, "a stale replay cannot cross the retained source contract");
});

const wires = [
  {invoke: createLocalCoordinationTodo, prefix: "loopx_local_coordination_todo_create_request", legacy: [0], current: 1},
  {invoke: claimLocalCoordinationTodo, prefix: "loopx_local_coordination_todo_claim_request", legacy: [0], current: 1},
  {invoke: updateLocalCoordinationTodo, prefix: "loopx_local_coordination_todo_update_request", legacy: [0, 1], current: 2},
  {invoke: pollLocalCoordinationMonitor, prefix: "loopx_coordination_monitor_poll_request", legacy: [0, 1], current: 2},
];
for (const wire of wires) test(`${wire.prefix}: source obligation cannot silently cross wire versions`, async () => {
  for (const version of wire.legacy) for (const source of [null, {path: "/unused", sha256: "0".repeat(64)}]) {
    const result = await wire.invoke({schema_version: `${wire.prefix}_v${version}`, registry_source: source});
    assert.equal(result.status, "failed");
    assert.match(String(result.reason), /registry_source|admission and revision fields/u);
  }
  for (const source of [undefined, {}, {path: "relative", sha256: "0".repeat(64)}, {path: "/unused", sha256: "invalid"}]) {
    const result = await wire.invoke({schema_version: `${wire.prefix}_v${wire.current}`, registry_source: source,
      lifecycle_grants: []});
    assert.equal(result.status, "failed");
    assert.match(String(result.reason), /registry_source/u);
  }
});

test("terminal lifecycle v3 rejects old wires and requires a registry witness", async () => {
  const prefix = "loopx_local_coordination_todo_terminal_lifecycle_request";
  for (const version of [0, 1, 2]) {
    const result = await terminalLifecycleLocalCoordinationTodo({
      schema_version: `${prefix}_v${version}`, registry_source: null,
    });
    assert.equal(result.status, "failed");
    assert.match(String(result.reason), /schema mismatch/u);
  }
  for (const source of [undefined, {}, {path: "relative", sha256: "0".repeat(64)},
    {path: "/unused", sha256: "invalid"}]) {
    const result = await terminalLifecycleLocalCoordinationTodo({
      schema_version: LOCAL_COORDINATION_TODO_TERMINAL_LIFECYCLE_REQUEST_SCHEMA,
      command: "complete", operation_identity: {kind: "explicit", operation_id: "source-wire"},
      registry_source: source,
    });
    assert.equal(result.status, "failed");
    assert.match(String(result.reason), /registry_source/u);
  }
});
