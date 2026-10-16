import assert from "node:assert/strict";
import {mkdtemp, mkdir, readFile, readdir, rm, writeFile} from "node:fs/promises";
import {join} from "node:path";
import {tmpdir} from "node:os";
import test, {type TestContext} from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {prepareAuthorityForPlannedTransfer as prepare} from "../../loopx/control_plane/coordination/planned_transfer_authority.ts";
import {selectLocalAuthorityTarget, openLocalAuthorityStoreHandle} from "../../loopx/control_plane/coordination/local_authority_provider.ts";
import {COLD_SOURCE_IMPORT_WRITER_FENCE_SCHEMA, legacyCoordinationWriterFencePath,
  loadLegacyCoordinationWriterFence, checkLegacyCoordinationWriteAllowed,
  LEGACY_COORDINATION_WRITE_CHECK_REQUEST_SCHEMA} from "../../loopx/control_plane/coordination/legacy_writer_fence.ts";
import {CanonicalWriteRouteError, withCanonicalWriter, type CanonicalBusinessWrite} from "../../loopx/control_plane/coordination/local_authority_write.ts";
import {projectCoordinationSource, SOURCE_PROJECTION_REQUEST_SCHEMA} from "../../loopx/control_plane/coordination/source_projection.ts";
import {shadowManagementDirectory} from "../../loopx/control_plane/coordination/shadow_management.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {manageLocalAuthorityArchive} from "../../loopx/control_plane/coordination/local_authority_archive.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {todo} from "./shadow_file_fixture.ts";

async function fixture(t: TestContext, provider: "file" | "sqlite", leaseStatus = "released") {
  const root = await mkdtemp(join(tmpdir(), "loopx-prepared-transfer-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  await selectLocalAuthorityTarget(root, "goal-a", provider, true);
  const opened = await openLocalAuthorityStoreHandle(root, "goal-a");
  const records = [{...todo("unfinished"), claimed_by: "agent-a", required_write_scopes: ["reports/**"],
    required_capabilities: ["filesystem_read", "filesystem_write"], successor_todo_ids: ["accepted"],
    text: "Read the required input and finish the report", note: "Original decision context"},
  {...todo("accepted", "done"), archive_state: "archive", source_section: "Agent Todo Archive",
    evidence: "Independent result acceptance", text: "Original accepted work"}];
  const lease = {schema_version: "task_lease_v0", goal_id: "goal-a", todo_id: "unfinished", owner: "agent-a",
    idempotency_key: "original-execution", status: leaseStatus, version: 3, lease_epoch: 2,
    expires_at: "2000-01-01T00:00:00Z"};
  const projection = projectCoordinationSource({schema_version: SOURCE_PROJECTION_REQUEST_SCHEMA, kind: "snapshot",
    goal_id: "goal-a", handoff_mode: "hard_lease", todos: records, leases: [lease],
    read_model_schema: "loopx_todo_canonical_read_record_v0"}).projection as JsonObject;
  let revision: string | null = null;
  for (let n = 1; n <= 3; n++) {
    const result = await opened.store.commitAuthority({expected_provider_revision: revision, operation_id: `original-${n}`,
      next_projection: projection, events: [{kind: "retained-fact", n}], receipts: [{operation_id: `original-${n}`, independent_fact: n}]});
    assert.equal(result.status, "applied"); if (result.status !== "applied") throw new Error("fixture commit failed");
    revision = result.provider_revision;
  }
  const fence = {schema_version: COLD_SOURCE_IMPORT_WRITER_FENCE_SCHEMA, state: "engaged", goal_id: "goal-a",
    fence_id: "original-import", import_operation_id: "original-import", import_plan_sha256: "a".repeat(64)};
  const fencePath = legacyCoordinationWriterFencePath(root, "goal-a");
  await mkdir(join(root, "authority-transition", "file-v0"), {recursive: true});
  await writeFile(fencePath, JSON.stringify(fence));
  const request = {runtime_root: root, goal_id: "goal-a", operation_id: "move-original", expected_provider_revision: revision,
    expected_writer_fence_sha256: canonicalAuthoritySha256(fence), execute: true};
  const directory = join(shadowManagementDirectory(root, "goal-a"), "planned-transfers", canonicalAuthoritySha256("move-original"));
  return {root, store: opened.store, projection, request, directory, fencePath, fence};
}

for (const provider of ["file", "sqlite"] as const) {
  test(`${provider}: preparation fences native/legacy writes and retains original history through the existing restore owner`, async t => {
    const f = await fixture(t, provider);
    for (const change of [{execute: false}, {unexpected: true}, {expected_writer_fence_sha256: "b".repeat(64)}]) {
      await assert.rejects(prepare({...f.request, ...change}), /explicit execution|complete reviewed|writer fence changed/);
      assert.deepEqual(JSON.parse(await readFile(f.fencePath, "utf8")), f.fence);
      await assert.rejects(readFile(join(f.directory, "preparation.json")), {code: "ENOENT"});
    }
    let calls = 0;
    assert.equal(await withCanonicalWriter(f.root, "goal-a", false, async () => ++calls, {businessWrite: "todo_update"}), 1);
    const before = await f.store.loadAuthority();
    const prepared = await prepare(f.request);
    assert.equal(prepared.authority_write_fenced, true);
    assert.equal(prepared.execution_authority_granted, false);
    assert.equal(prepared.host_drain_qualified, false);
    assert.equal(prepared.portable_artifact_qualified, false);
    assert.deepEqual(await f.store.loadAuthority(), before, "preparation must not change native history or original receipts");
    const originalFence = await readFile(f.fencePath);
    const originalReceipt = await readFile(join(f.directory, "receipt.json"));
    assert.deepEqual(await prepare(f.request), prepared);
    assert.deepEqual(await readFile(f.fencePath), originalFence);
    assert.deepEqual(await readFile(join(f.directory, "receipt.json")), originalReceipt);
    const writes: CanonicalBusinessWrite[] = ["todo_claim", "todo_create", "todo_update", "todo_terminal", "todo_archive",
      "monitor_poll", "team_plan", "handoff_mode_set", "task_lease_acquire", "task_lease_renew", "task_lease_transfer", "task_lease_release", "goal_create"];
    for (const businessWrite of writes) {
      await assert.rejects(withCanonicalWriter(f.root, "goal-a", false, async () => ++calls, {businessWrite}),
        (error: unknown) => error instanceof CanonicalWriteRouteError && error.code === "canonical_authority_transfer_prepared");
    }
    await assert.rejects(withCanonicalWriter(f.root, "goal-a", false, async () => ++calls), /prepared for transfer/);
    assert.equal(calls, 1, "no protected callback runs after preparation");
    const legacy = await checkLegacyCoordinationWriteAllowed({schema_version: LEGACY_COORDINATION_WRITE_CHECK_REQUEST_SCHEMA,
      runtime_root: f.root, goal_id: "goal-a"});
    assert.equal(legacy.status, "blocked");
    const archive = prepared.authority_archive as JsonObject;
    for (const targetProvider of ["file", "sqlite"] as const) {
      const destination = join(f.root, `inert-${targetProvider}`);
      const restored = await manageLocalAuthorityArchive({schema_version: "loopx_authority_archive_admin_request_v0",
        action: "restore", goal_id: "goal-a", provider: targetProvider, archive: prepared.archive,
        archive_sha256: archive.archive_sha256, destination, execute: true});
      assert.equal(restored.status, "restored", JSON.stringify(restored));
      assert.equal(restored.execution_authority_granted, false);
      const target = targetProvider === "file" ? new FileAuthorityStore(join(destination, "store"), "goal-a", {existingOnly: true})
        : new SqliteAuthorityStore(join(destination, "store"), "goal-a", {existingOnly: true});
      const after = await target.loadAuthority();
      assert.equal(after.status, "loaded"); if (after.status !== "loaded") throw new Error("missing target");
      assert.deepEqual(after.head, f.projection);
      assert.notEqual(after.provider_revision, f.request.expected_provider_revision);
      for (let n = 1; n <= 3; n++) {
        const receipt = await target.readReceipt(`original-${n}`);
        assert.equal(receipt.status, "found");
        if (receipt.status === "found") assert.deepEqual(receipt.receipts, [{operation_id: `original-${n}`, independent_fact: n}]);
      }
    }
    await assert.rejects(prepare({...f.request, operation_id: "another-move"}), /another transfer operation/);
    assert.deepEqual(await f.store.loadAuthority(), before);
  });

  test(`${provider}: expired active lease and changed preview reject before fencing`, async t => {
    const f = await fixture(t, provider, "active");
    const original = await readFile(f.fencePath), head = await f.store.loadAuthority();
    await assert.rejects(prepare({...f.request, expected_provider_revision: "stale:revision"}), /changed/);
    await assert.rejects(prepare(f.request), /unreleased lease/);
    assert.deepEqual(await readFile(f.fencePath), original);
    assert.deepEqual(await f.store.loadAuthority(), head);
    await assert.rejects(readFile(join(f.directory, "preparation.json")), {code: "ENOENT"});
  });

  test(`${provider}: interrupted capture stays fenced and same-operation retry recovers without reinitializing`, async t => {
    const f = await fixture(t, provider);
    // Actual filesystem publication failure after the fence, without replacing
    // an AuthorityStore or inventing a successful owner receipt.
    await mkdir(join(f.directory, "authority.ndjson"), {recursive: true});
    await assert.rejects(prepare(f.request));
    assert.equal((await loadLegacyCoordinationWriterFence(f.root, "goal-a")).status, "loaded");
    await assert.rejects(withCanonicalWriter(f.root, "goal-a", false, async () => assert.fail("writer ran"),
      {businessWrite: "todo_update"}), /prepared for transfer/);
    await assert.rejects(readFile(join(f.directory, "receipt.json")), {code: "ENOENT"});
    await rm(join(f.directory, "authority.ndjson"), {recursive: true});
    const recovered = await prepare(f.request);
    assert.equal(recovered.authority_write_fenced, true);
    assert.equal((recovered.authority_archive as JsonObject).commits, "3");
    const bytes = await readFile(String(recovered.archive));
    await writeFile(String(recovered.archive), Buffer.concat([bytes, Buffer.from("corrupt\n")]));
    await assert.rejects(prepare(f.request), /terminal seal/);
    await assert.rejects(withCanonicalWriter(f.root, "goal-a", false, async () => assert.fail("writer ran"),
      {businessWrite: "task_lease_acquire"}), /prepared for transfer/);
  });

  test(`${provider}: unknown Goal and lost provider never create a replacement authority`, async t => {
    const f = await fixture(t, provider);
    const before = await readdir(join(f.root, "authority"), {recursive: true});
    await assert.rejects(prepare({...f.request, goal_id: "unregistered"}), /original verified canonical writer fence/);
    assert.deepEqual(await readdir(join(f.root, "authority"), {recursive: true}), before);
    const source = f.store;
    assert.ok(source instanceof FileAuthorityStore || source instanceof SqliteAuthorityStore);
    await rm(source.path);
    await assert.rejects(prepare(f.request), /missing|unavailable|restore|open/i);
    await assert.rejects(readFile(source.path), {code: "ENOENT"});
    assert.deepEqual(JSON.parse(await readFile(f.fencePath, "utf8")), f.fence);
    await assert.rejects(readFile(join(f.directory, "preparation.json")), {code: "ENOENT"});
  });

  test(`${provider}: an admitted writer racing the preview wins before preparation, which requires a fresh review`, async t => {
    const f = await fixture(t, provider);
    let entered!: () => void, resume!: () => void;
    const inside = new Promise<void>(resolve => {entered = resolve;});
    const release = new Promise<void>(resolve => {resume = resolve;});
    const writer = withCanonicalWriter(f.root, "goal-a", false, async () => {
      entered();
      await release;
      return f.store.commitAuthority({expected_provider_revision: f.request.expected_provider_revision,
        operation_id: "admitted-before-transfer", next_projection: {...f.projection, progress: "later source progress"},
        events: [{kind: "later-source-fact"}], receipts: [{source_progress_preserved: true}]});
    }, {businessWrite: "todo_update"});
    await inside;
    const refused = assert.rejects(prepare(f.request), /Source authority changed/);
    resume();
    assert.equal((await writer).status, "applied");
    await refused;
    assert.deepEqual(JSON.parse(await readFile(f.fencePath, "utf8")), f.fence);
    assert.equal((await f.store.readReceipt("admitted-before-transfer")).status, "found");
    await assert.rejects(readFile(join(f.directory, "preparation.json")), {code: "ENOENT"});
  });
}
