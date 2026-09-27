import {executeTaskLeaseAcquire} from "../../loopx/control_plane/work_items/task_lease_acquire.ts";
import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {mkdtemp, writeFile, rm, access} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {spawn, spawnSync} from "node:child_process";
import {fileURLToPath} from "node:url";
import test, {type TestContext} from "node:test";
import {executeTaskLeaseLifecycle, TASK_LEASE_LIFECYCLE_REQUEST_SCHEMA_VERSION} from "../../loopx/control_plane/work_items/task_lease_lifecycle.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {sqliteAuthorityRuntime} from "../../loopx/control_plane/coordination/sqlite_runtime.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {selectLocalSqliteAuthority} from "../../loopx/control_plane/coordination/local_authority_provider.ts";
import {engageLegacyCoordinationWriterFence} from "../../loopx/control_plane/coordination/legacy_writer_fence.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";
import {executeCanonicalTaskLeaseLifecycle} from "../../loopx/control_plane/coordination/task_lease_lifecycle.ts";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import {taskLeaseOperationIdentity, taskLeaseOperationRequestDigest} from "../../loopx/control_plane/work_items/task_lease_operation_identity.ts";
import {legacyCoordinationWriterFencePath} from "../../loopx/control_plane/coordination/legacy_writer_fence.ts";
import {TASK_LEASE_CANONICAL_ACQUIRE_REQUEST_SCHEMA, TASK_LEASE_ACQUIRE_REQUEST_SCHEMA, TASK_LEASE_CANONICAL_RENEW_REQUEST_SCHEMA, TASK_LEASE_CANONICAL_LIFECYCLE_REQUEST_SCHEMA,
  TASK_LEASE_CANONICAL_CLAIM_TRANSFER_REQUEST_SCHEMA} from "../../loopx/control_plane/coordination/coordination_state_contract.generated.ts";
import {shadowManagementStatePath} from "../../loopx/control_plane/coordination/shadow_management.ts";
import {atomicWriteJson} from "../../loopx/control_plane/effect_runtime_io.ts";

const NOW = new Date("2026-09-13T10:05:00Z");
const CHILD = fileURLToPath(new URL("./canonical_task_lease_renew_process.ts", import.meta.url));
test("lifecycle identity diagnostics follow the invalid field, not error wording", async () => {
  for (const [fields, code] of [
    [{goal_id: 42, todo_id: "todo_valid"}, "invalid_goal_id"],
    [{goal_id: "valid-goal", todo_id: 42}, "invalid_todo_id"],
    [{goal_id: "valid-goal"}, "invalid_todo_id"],
  ] as const) {
    const result = await executeTaskLeaseLifecycle({schema_version: TASK_LEASE_CANONICAL_LIFECYCLE_REQUEST_SCHEMA,
      operation: "release", ...fields});
    assert.equal(result.error_code, code);
  }
});
function sqliteSkipReason(): string | undefined {
  try { sqliteAuthorityRuntime(); return undefined; }
  catch { return "requires a WAL-fixed SQLite runtime with finalized statements"; }
}
async function fixture(t: TestContext, provider: "file" | "sqlite") {
  const root = await mkdtemp(join(tmpdir(), "loopx-canonical-renew-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const runtime = join(root, "runtime"), goal = "renew-goal", state = join(root, "state.md");
  const store = provider === "sqlite"
    ? new SqliteAuthorityStore(join(runtime, "authority/sqlite-v0"), goal)
    : new FileAuthorityStore(join(runtime, "authority/file-v0"), goal);
  if (provider === "sqlite") assert.equal((await selectLocalSqliteAuthority(runtime, goal, true)).ok, true);
  const todo = {todo_id: "todo_renew", role: "agent", status: "open", done: false,
    text: "Renew the canonical lease", archive_state: "active", claimed_by: "agent-a", task_class: "advancement_task"};
  const lease = {schema_version: "task_lease_v0", goal_id: goal, todo_id: "todo_renew", owner: "agent-a",
    idempotency_key: "execution-a", version: 1, lease_epoch: 7, status: "active", write_scopes: ["src/**"],
    acquire_ttl_seconds: 600, acquired_at: "2026-09-13T10:00:00Z", updated_at: "2026-09-13T10:00:00Z", expires_at: "2026-09-13T10:10:00Z"};
  const projection = authorityProjectionFixture(goal, [todo], [lease], "native", {handoff_mode: "hard_lease"});
  assert.equal((await store.commitAuthority({expected_provider_revision: null, operation_id: "seed",
    next_projection: projection, events: [], receipts: []})).status, "applied");
  const head = await store.loadAuthority(); assert.equal(head.status, "loaded");
  if (head.status !== "loaded") throw new Error("fixture head missing");
  const content = "# Synthetic renewal\n\n## Agent Todo\n";
  await writeFile(state, content);
  assert.equal((await engageLegacyCoordinationWriterFence({schema_version: "loopx_legacy_coordination_writer_fence_engage_request_v0",
    runtime_root: runtime, goal_id: goal, state_path: state, fence: {schema_version: "loopx_legacy_coordination_writer_fence_v0",
      state: "engaged", goal_id: goal, fence_id: "renew-fixture", source_version: "state:1",
      source_projection_sha256: canonicalAuthoritySha256(projection), expected_shadow_provider_revision: head.provider_revision}})).status, "applied");
  const request = {schema_version: TASK_LEASE_CANONICAL_RENEW_REQUEST_SCHEMA, operation: "renew" as const, runtime_root: runtime,
    goal_id: goal, todo_id: "todo_renew", owner: "agent-a", idempotency_key: "execution-a", expected_version: 1, ttl_seconds: 600,
    authority: {handoff_mode: "hard_lease", registered_agent_candidates: [["agent-a", "agent-b"]], todos: [todo],
      todo_projection_error: null, source_receipts: [{source_id: "state", path: state, state: "file", sha256: createHash("sha256").update(content).digest("hex")}]}};
  return {root, runtime, goal, state, store, request, lease, projection};
}

test("renew identity preserves the legacy hash and binds changed TTL as changed intent", () => {
  const request = {operation: "renew", goal_id: "renew-goal", todo_id: "todo_renew", owner: "agent-a",
    idempotency_key: "execution-a", expected_version: 1, ttl_seconds: 600, new_owner: null, new_idempotency_key: null};
  // Independent SHA-256 of the documented v0 sorted JSON field sets.
  assert.equal(taskLeaseOperationIdentity(request), "4faaad76883d5e7f260cee986f0331b3f004ced3b981d6d935ac3a657325d04c");
  assert.equal(taskLeaseOperationRequestDigest(request), "954e868545c3b78fbce5a1aea466d097ebeef5071c7913e95606b8ed04b33d06");
  assert.equal(taskLeaseOperationIdentity({...request, ttl_seconds: 900}), taskLeaseOperationIdentity(request));
  assert.notEqual(taskLeaseOperationRequestDigest({...request, ttl_seconds: 900}), taskLeaseOperationRequestDigest(request));
});

for (const provider of ["file", "sqlite"] as const) {
  const skip = provider === "sqlite" ? sqliteSkipReason() : undefined;
  const providerTest = (name: string, body: (t: TestContext) => Promise<void>) =>
    test(name, {skip}, body);
  function acquireRequest(request: Awaited<ReturnType<typeof fixture>>["request"]) {
    return {schema_version: TASK_LEASE_CANONICAL_ACQUIRE_REQUEST_SCHEMA, runtime_root: request.runtime_root,
      goal_id: request.goal_id, todo_id: request.todo_id, owner: "agent-a", idempotency_key: "acquire-new",
      expected_version: 1, ttl_seconds: 600, write_scopes: ["src/**"], authority: request.authority};
  }
  const acquireNow = new Date("2026-09-13T10:11:00Z");
  providerTest(`${provider} native acquisition ignores stale Todo/mode and preserves the legacy fence`, async t => {
    const {store, request, state} = await fixture(t, provider);
    const command = acquireRequest(request);
    const before = await store.loadAuthority();
    const legacy = await executeTaskLeaseAcquire({...command, schema_version: TASK_LEASE_ACQUIRE_REQUEST_SCHEMA}, {now: () => acquireNow});
    assert.equal(legacy.error_code, "legacy_coordination_writer_fenced");
    assert.deepEqual(await store.loadAuthority(), before);
    const result = await executeTaskLeaseAcquire({...command, authority: {...command.authority,
      handoff_mode: "soft_claim", todos: []}}, {now: () => acquireNow});
    assert.equal(result.ok, true, JSON.stringify(result)); assert.equal(result.source_authority, provider + "_v0");
    assert.equal(result.legacy_fallback_used, false); assert.equal(result.lease_path, undefined);
    assert.equal((result.lease as Record<string, unknown>).version, 2);
    assert.equal((result.lease as Record<string, unknown>).lease_epoch, 8);
    await access(state);
  });
  for (const fault of ["source", "fence", "field"] as const) {
    providerTest(`${provider} acquisition fails closed on ${fault} without legacy or canonical writes`, async t => {
      const {store, request, state, runtime, goal} = await fixture(t, provider);
      const command = acquireRequest(request), before = await store.loadAuthority();
      if (fault === "fence") await rm(legacyCoordinationWriterFencePath(runtime, goal));
      const result = await executeTaskLeaseAcquire(fault === "field" ? {...command, lock_token: "wrong-domain"} : command,
        {now: () => acquireNow, beforeWrite: fault === "source" ? async () => {await writeFile(state, "changed source");} : undefined});
      assert.equal(result.error_code, {source: "authority_source_changed", fence: "canonical_acquire_fence_missing",
        field: "invalid_canonical_acquire_request"}[fault]);
      assert.deepEqual(await store.loadAuthority(), before);
    });
  }
  for (const boundary of ["before", "after"] as const) {
    providerTest(`${provider} real process death ${boundary} acquire commit preserves one new generation`, async t => {
      const {root, store, request} = await fixture(t, provider);
      const command = acquireRequest(request), config = join(root, "acquire-child.json");
      await writeFile(config, JSON.stringify({...command, operation: "acquire", now: acquireNow.toISOString()}));
      const before = await store.loadAuthority();
      const child = spawnSync(process.execPath, ["--no-warnings", "--experimental-sqlite", "--experimental-strip-types", CHILD, config, boundary, "600"], {encoding: "utf8", timeout: 30000});
      assert.equal(child.signal, "SIGKILL", child.stderr);
      if (boundary === "before") assert.deepEqual(await store.loadAuthority(), before);
      const result = await executeTaskLeaseAcquire(command, {now: () => acquireNow});
      assert.equal(result.ok, true, JSON.stringify(result)); assert.equal(result.status, boundary === "before" ? "applied" : "replayed");
      const final = await store.loadAuthority(); if (final.status !== "loaded") throw new Error("missing head");
      assert.equal(final.cursor, "2"); assert.equal((result.lease as Record<string, unknown>).version, 2);
      assert.equal((result.lease as Record<string, unknown>).lease_epoch, 8);
      assert.equal((await executeTaskLeaseAcquire(command, {now: () => acquireNow})).status, "replayed");
      assert.deepEqual(await store.loadAuthority(), final);
    });
  }
  providerTest(`${provider} legacy wire requests remain fenced`, async t => {
    const {store, request} = await fixture(t, provider); const before = await store.loadAuthority();
    const result = await executeTaskLeaseLifecycle({...request, schema_version: TASK_LEASE_LIFECYCLE_REQUEST_SCHEMA_VERSION}, {now: () => NOW});
    assert.equal(result.error_code, "legacy_coordination_writer_fenced");
    assert.deepEqual(await store.loadAuthority(), before);
  });
  providerTest(`${provider} canonical renew uses the public lifecycle entrypoint without a legacy lease file`, async t => {
    const {runtime, goal, store, request, lease} = await fixture(t, provider);
    const result = await executeTaskLeaseLifecycle(request, {now: () => NOW});
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.equal(result.source_authority, `${provider}_v0`);
    assert.equal(result.legacy_fallback_used, false);
    assert.equal(result.lease_path, undefined);
    assert.deepEqual(result.lease, {...lease, version: 2, updated_at: NOW.toISOString().replace(".000Z", "Z"), expires_at: "2026-09-13T10:15:00Z"});
    const after = await store.loadAuthority(); assert.equal(after.status, "loaded");
    if (after.status === "loaded") assert.equal(after.cursor, "2");
    await assert.rejects(access(join(runtime, "goals", goal, "task-leases/todo_renew.json")));
  });
  providerTest(`${provider} old renewal receipt survives later renewals and expiry without extending again`, async t => {
    const {store, request, runtime, goal} = await fixture(t, provider);
    const first = await executeTaskLeaseLifecycle(request, {now: () => NOW}); assert.equal(first.ok, true);
    const second = await executeTaskLeaseLifecycle({...request, expected_version: 2}, {now: () => new Date("2026-09-13T10:06:00Z")});
    assert.equal(second.ok, true); assert.equal((second.lease as Record<string, unknown>).version, 3);
    const current = await store.loadAuthority();
    const replay = await executeTaskLeaseLifecycle(request, {now: () => new Date("2026-09-14T10:00:00Z")});
    assert.equal(replay.ok, true); assert.equal(replay.idempotent, true); assert.equal(replay.status, "replayed");
    assert.deepEqual(replay.lease, first.lease); assert.deepEqual(replay.original_receipt, first.original_receipt);
    assert.deepEqual(await store.loadAuthority(), current);
    const reuse = await executeTaskLeaseLifecycle({...request, ttl_seconds: 900}, {now: () => NOW});
    assert.equal(reuse.error_code, "coordination_operation_identity_mismatch");
    assert.deepEqual(await store.loadAuthority(), current);
    await assert.rejects(access(join(runtime, "goals", goal, "task-leases/.lifecycle-operations")));
  });
  providerTest(`${provider} canonical facts override stale caller Todo and mode facts`, async t => {
    const {store, request} = await fixture(t, provider);
    const stale = {...request, authority: {...request.authority, handoff_mode: "soft_claim",
      todos: [{...request.authority.todos[0]!, status: "done", claimed_by: "agent-b"}]}};
    const result = await executeTaskLeaseLifecycle(stale, {now: () => NOW});
    assert.equal(result.ok, true, JSON.stringify(result)); assert.equal(result.handoff_mode, "hard_lease");
    const head = await store.loadAuthority(); if (head.status !== "loaded") throw new Error("missing head");
    assert.equal((head.head.todos as Record<string, unknown>[])[0]!.status, "open");
  });
  for (const [label, changes, now, code] of [
    ["wrong owner", {owner: "agent-b"}, NOW, "owner_conflicts_with_claim"],
    ["wrong execution", {idempotency_key: "wrong"}, NOW, "lease_cas_mismatch"],
    ["stale version", {expected_version: 0}, NOW, "version_mismatch"],
    ["missing version", {expected_version: null}, NOW, "version_required"],
    ["expired", {}, new Date("2026-09-13T10:10:00Z"), "lease_not_active"],
  ] as const) {
    providerTest(`${provider} rejects ${label} without canonical or legacy writes`, async t => {
      const {store, request, runtime, goal} = await fixture(t, provider);
      const before = await store.loadAuthority();
      const result = await executeTaskLeaseLifecycle({...request, ...changes}, {now: () => now});
      assert.equal(result.ok, false); assert.equal(result.error_code, code, JSON.stringify(result));
      assert.deepEqual(await store.loadAuthority(), before);
      await assert.rejects(access(join(runtime, "goals", goal, "task-leases/todo_renew.json")));
      await assert.rejects(access(join(runtime, "goals", goal, "task-leases/.lifecycle-operations")));
    });
  }
  providerTest(`${provider} source revocation before commit is rejected with no write`, async t => {
    const {store, state, request} = await fixture(t, provider); const before = await store.loadAuthority();
    const result = await executeTaskLeaseLifecycle(request, {now: () => NOW, beforeWrite: async () => {await writeFile(state, "changed source");}});
    assert.equal(result.ok, false); assert.equal(result.error_code, "authority_source_changed");
    assert.deepEqual(await store.loadAuthority(), before);
  });
  providerTest(`${provider} maintenance guard and canonical mode remain authoritative`, async t => {
    const {store, request, runtime, goal, projection} = await fixture(t, provider);
    let head = await store.loadAuthority(); if (head.status !== "loaded") throw new Error("missing head");
    await store.commitAuthority({expected_provider_revision: head.provider_revision, operation_id: "mode-change",
      next_projection: {...projection, handoff_mode: "soft_claim"}, events: [], receipts: []});
    const before = await store.loadAuthority();
    const refused = await executeTaskLeaseLifecycle(request, {now: () => NOW});
    assert.equal(refused.error_code, "handoff_mode_forbids_lease"); assert.equal(refused.handoff_mode, "soft_claim");
    assert.deepEqual(await store.loadAuthority(), before);
    await atomicWriteJson(shadowManagementStatePath(runtime, goal), {});
    const blocked = await executeTaskLeaseLifecycle(request, {now: () => NOW});
    assert.equal(blocked.error_code, "shadow_management_state_invalid"); assert.deepEqual(await store.loadAuthority(), before);
  });
  providerTest(`${provider} fenced missing head never initializes a new lease`, async t => {
    const {store, request, runtime, goal} = await fixture(t, provider);
    await rm(store.path);
    const result = await executeTaskLeaseLifecycle(request, {now: () => NOW});
    assert.equal(result.ok, false); assert.equal(result.legacy_fallback_used, false);
    await assert.rejects(access(store.path));
    await assert.rejects(access(join(runtime, "goals", goal, "task-leases/todo_renew.json")));
    await assert.rejects(access(join(runtime, "goals", goal, "task-leases/.lifecycle-operations")));
  });
  providerTest(`${provider} invalid fence stays fail-closed`, async t => {
    const {store, request, runtime, goal} = await fixture(t, provider); const before = await store.loadAuthority();
    await writeFile(legacyCoordinationWriterFencePath(runtime, goal), "{");
    const result = await executeTaskLeaseLifecycle(request, {now: () => NOW});
    assert.equal(result.ok, false); assert.equal(result.error_code, "legacy_writer_fence_read_failed");
    assert.deepEqual(await store.loadAuthority(), before);
  });
  providerTest(`${provider} a canonical-only request cannot downgrade after losing its fence`, async t => {
    const {store, request, runtime, goal, lease} = await fixture(t, provider); const before = await store.loadAuthority();
    const legacyPath = join(runtime, "goals", goal, "task-leases/todo_renew.json");
    const legacyBytes = JSON.stringify(lease); await writeFile(legacyPath, legacyBytes);
    await rm(legacyCoordinationWriterFencePath(runtime, goal));
    const result = await executeTaskLeaseLifecycle({...request, schema_version: TASK_LEASE_CANONICAL_RENEW_REQUEST_SCHEMA}, {now: () => NOW});
    assert.equal(result.ok, false); assert.equal(result.error_code, "canonical_renew_fence_missing");
    assert.deepEqual(await store.loadAuthority(), before);
    assert.equal(await import("node:fs/promises").then(fs => fs.readFile(legacyPath, "utf8")), legacyBytes);
  });
  providerTest(`${provider} canonical renew rejects unsupported mutation controls`, async t => {
    const {store, request} = await fixture(t, provider); const before = await store.loadAuthority();
    for (const fields of [{dry_run: true}, {new_owner: "agent-b"}, {release_lease: true}]) {
      const result = await executeTaskLeaseLifecycle({...request, ...fields}, {now: () => NOW});
      assert.equal(result.error_code, "invalid_canonical_renew_request"); assert.deepEqual(await store.loadAuthority(), before);
    }
    const transfer = await executeTaskLeaseLifecycle({...request, operation: "transfer"}, {now: () => NOW});
    assert.equal(transfer.error_code, "invalid_operation"); assert.deepEqual(await store.loadAuthority(), before);
  });
  providerTest(`${provider} ambiguous response recovers the exact committed renewal`, async t => {
    const {store, request, lease} = await fixture(t, provider);
    const wrapper: AuthorityStore = {storeIdentity: () => store.storeIdentity(), loadAuthority: () => store.loadAuthority(),
      readReceipt: id => store.readReceipt(id), scanCommitted: (...args) => store.scanCommitted(...args),
      commitAuthority: async input => {assert.equal((await store.commitAuthority(input)).status, "applied"); throw new Error("response lost");}};
    const result = await executeCanonicalTaskLeaseLifecycle(wrapper, {...request, registered_agents: ["agent-a", "agent-b"], now: NOW});
    assert.equal(result.status, "recovered", JSON.stringify(result));
    assert.deepEqual(result.lease, {...lease, version: 2, updated_at: "2026-09-13T10:05:00Z", expires_at: "2026-09-13T10:15:00Z"});
    const head = await store.loadAuthority(); assert.equal(head.status, "loaded"); if (head.status === "loaded") assert.equal(head.cursor, "2");
  });
  for (const boundary of ["before", "after"] as const) {
    providerTest(`${provider} real process interruption ${boundary} provider commit preserves renewal identity`, async t => {
      const {root, store, request} = await fixture(t, provider);
      const config = join(root, "child.json"); await writeFile(config, JSON.stringify({...request, now: NOW.toISOString()}));
      const before = await store.loadAuthority();
      const child = spawnSync(process.execPath, ["--no-warnings", "--experimental-sqlite", "--experimental-strip-types", CHILD, config, boundary, "600"], {encoding: "utf8", timeout: 30000});
      assert.equal(child.signal, "SIGKILL", child.stderr);
      if (boundary === "before") assert.deepEqual(await store.loadAuthority(), before);
      const result = await executeTaskLeaseLifecycle(request, {now: () => NOW});
      assert.equal(result.ok, true); assert.equal(result.status, boundary === "before" ? "applied" : "replayed");
      const final = await store.loadAuthority(); if (final.status !== "loaded") throw new Error("missing head");
      assert.equal(final.cursor, "2"); assert.equal((result.lease as Record<string, unknown>).version, 2);
      assert.equal((await executeTaskLeaseLifecycle(request, {now: () => NOW})).status, "replayed");
      assert.deepEqual(await store.loadAuthority(), final);
    });
  }
  for (const operation of ["transfer", "release"] as const) {
    providerTest(`${provider} canonical ${operation} preserves source and maintenance fences`, async t => {
      const {store, request, state, runtime, goal} = await fixture(t, provider);
      const command = {...request, schema_version: TASK_LEASE_CANONICAL_LIFECYCLE_REQUEST_SCHEMA, operation,
        ttl_seconds: operation === "release" ? null : 600,
        ...(operation === "transfer" ? {new_owner: "agent-a", new_idempotency_key: "rotated-key"} : {authority: null})};
      const before = await store.loadAuthority();
      const foreign = await executeTaskLeaseLifecycle({...command, release_lease: true}, {now: () => NOW});
      assert.equal(foreign.error_code, "invalid_canonical_lifecycle_request");
      assert.deepEqual(await store.loadAuthority(), before);
      if (operation === "transfer") {
        const revoked = await executeTaskLeaseLifecycle(command, {now: () => NOW,
          beforeWrite: async () => {await writeFile(state, "registration revoked");}});
        assert.equal(revoked.error_code, "authority_source_changed");
        assert.deepEqual(await store.loadAuthority(), before);
      } else {
        const extra = await executeTaskLeaseLifecycle({...command, ttl_seconds: 600}, {now: () => NOW});
        assert.equal(extra.error_code, "invalid_canonical_lifecycle_request");
        assert.deepEqual(await store.loadAuthority(), before);
        const cleanup = await executeTaskLeaseLifecycle(command, {now: () => new Date("2030-01-01Z")});
        assert.equal(cleanup.ok, true); assert.equal(cleanup.released, true);
        assert.equal((cleanup.lease as Record<string, unknown>).status, "released");
      }
      const current = await store.loadAuthority();
      await rm(legacyCoordinationWriterFencePath(runtime, goal));
      const unfenced = await executeTaskLeaseLifecycle(command, {now: () => NOW});
      assert.equal(unfenced.error_code, `canonical_${operation}_fence_missing`);
      assert.deepEqual(await store.loadAuthority(), current);
    });
    for (const boundary of ["before", "after"] as const) {
      providerTest(`${provider} process death ${boundary} ${operation} commit cannot duplicate the transition`, async t => {
        const {root, store, request} = await fixture(t, provider);
        const command = {...request, schema_version: TASK_LEASE_CANONICAL_LIFECYCLE_REQUEST_SCHEMA, operation,
          ttl_seconds: operation === "release" ? null : 600,
          ...(operation === "transfer" ? {new_owner: "agent-a", new_idempotency_key: "rotated-key"} : {})};
        const config = join(root, "lifecycle-child.json"); await writeFile(config, JSON.stringify({...command, now: NOW.toISOString()}));
        const before = await store.loadAuthority();
        const child = spawnSync(process.execPath, ["--no-warnings", "--experimental-sqlite", "--experimental-strip-types", CHILD,
          config, boundary, "600"], {encoding: "utf8", timeout: 30000});
        assert.equal(child.signal, "SIGKILL", child.stderr);
        if (boundary === "before") assert.deepEqual(await store.loadAuthority(), before);
        const recovered = await executeTaskLeaseLifecycle(command, {now: () => NOW});
        assert.equal(recovered.ok, true, JSON.stringify(recovered));
        assert.equal(recovered.status, boundary === "before" ? "applied" : "replayed");
        const final = await store.loadAuthority(); assert.equal(final.status, "loaded");
        if (final.status === "loaded") assert.equal(final.cursor, "2");
        assert.equal((await executeTaskLeaseLifecycle(command, {now: () => NOW})).status, "replayed");
        assert.deepEqual(await store.loadAuthority(), final);
      });
    }
  }
  providerTest(`${provider} claim transfer wire cannot downgrade or import legacy fence fields`, async t => {
    const {store, request} = await fixture(t, provider);
    const command = {...request, schema_version: TASK_LEASE_CANONICAL_CLAIM_TRANSFER_REQUEST_SCHEMA,
      operation: "transfer", transfer_claim: true, new_owner: "agent-b", new_idempotency_key: "receiver-b"};
    const before = await store.loadAuthority();
    for (const patch of [
      {schema_version: TASK_LEASE_LIFECYCLE_REQUEST_SCHEMA_VERSION},
      {schema_version: TASK_LEASE_CANONICAL_LIFECYCLE_REQUEST_SCHEMA},
      {operation: "renew"}, {transfer_claim: false}, {transfer_claim: "true"},
      {release_lease: true}, {todo: {claimed_by: "agent-b"}},
    ]) {
      assert.equal((await executeTaskLeaseLifecycle({...command, ...patch}, {now: () => NOW})).ok, false);
      assert.deepEqual(await store.loadAuthority(), before);
    }
    const accepted = await executeTaskLeaseLifecycle(command, {now: () => NOW});
    assert.equal(accepted.ok, true, JSON.stringify(accepted));
    assert.equal(accepted.claimed_by, "agent-b");
    assert.equal(accepted.lease_path, undefined);
  });
  for (const boundary of ["before", "after"] as const) {
    providerTest(`${provider} process death ${boundary} joint claim transfer commits both records or neither`, async t => {
      const {root, store, request} = await fixture(t, provider);
      const command = {...request, schema_version: TASK_LEASE_CANONICAL_CLAIM_TRANSFER_REQUEST_SCHEMA,
        operation: "transfer", transfer_claim: true, new_owner: "agent-b", new_idempotency_key: "receiver-b"};
      const config = join(root, "claim-transfer-child.json"); await writeFile(config, JSON.stringify({...command, now: NOW.toISOString()}));
      const before = await store.loadAuthority();
      const child = spawnSync(process.execPath, ["--no-warnings", "--experimental-sqlite", "--experimental-strip-types", CHILD,
        config, boundary, "600"], {encoding: "utf8", timeout: 30000});
      assert.equal(child.signal, "SIGKILL", child.stderr);
      if (boundary === "before") assert.deepEqual(await store.loadAuthority(), before);
      const recovered = await executeTaskLeaseLifecycle(command, {now: () => NOW});
      assert.equal(recovered.ok, true, JSON.stringify(recovered));
      assert.equal(recovered.status, boundary === "before" ? "applied" : "replayed");
      const final = await store.loadAuthority(); if (final.status !== "loaded") throw new Error("missing head");
      assert.equal(final.cursor, "2");
      assert.equal((final.head.todos as Record<string, unknown>[])[0]!.claimed_by, "agent-b");
      assert.equal((final.head.leases as Record<string, unknown>[])[0]!.owner, "agent-b");
      assert.equal((await executeTaskLeaseLifecycle(command, {now: () => NOW})).status, "replayed");
      assert.deepEqual(await store.loadAuthority(), final);
    });
  }
  for (const differentIntent of [false, true]) {
    providerTest(`${provider} real processes arbitrate ${differentIntent ? "different" : "identical"} renew intent at one version`, async t => {
      const {root, store, request} = await fixture(t, provider);
      const config = join(root, "race.json"); await writeFile(config, JSON.stringify({...request, now: NOW.toISOString()}));
      const children: ReturnType<typeof spawn>[] = []; let readyCount = 0;
      t.after(() => {for (const child of children) child.kill();});
      const results = await Promise.all([600, differentIntent ? 900 : 600].map(ttl => new Promise<Record<string, unknown>>((resolve, reject) => {
        const child = spawn(process.execPath, ["--no-warnings", "--experimental-sqlite", "--experimental-strip-types", CHILD, config, "race", String(ttl)], {stdio: ["ignore", "pipe", "pipe", "ipc"]});
        children.push(child); let output = "", error = "";
        const timeout = setTimeout(() => {child.kill("SIGKILL"); reject(new Error("renew race timed out"));}, 30000);
        child.stdout!.on("data", data => {output += String(data);}); child.stderr!.on("data", data => {error += String(data);});
        child.on("message", () => {if (++readyCount === 2) for (const peer of children) peer.send!({go: true});});
        child.on("error", reject);
        child.on("close", code => {clearTimeout(timeout); if (code !== 0) reject(new Error(error)); else resolve(JSON.parse(output));});
      })));
      assert.deepEqual(results.map(r => r.status).sort(), differentIntent ? ["applied", "failed"] : ["applied", "applied"]);
      if (differentIntent) assert.equal(results.find(r => r.status === "failed")!.reason_code, "coordination_operation_identity_mismatch");
      const head = await store.loadAuthority(); if (head.status !== "loaded") throw new Error("missing head");
      assert.equal(head.cursor, "2"); assert.equal((head.head.leases as Record<string, unknown>[])[0]!.version, 2);
    });
  }
}
