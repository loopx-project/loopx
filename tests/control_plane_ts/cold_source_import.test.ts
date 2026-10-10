import assert from "node:assert/strict";
import {spawn, spawnSync} from "node:child_process";
import {createHash} from "node:crypto";
import {once} from "node:events";
import {setTimeout as delay} from "node:timers/promises";
import {mkdir, mkdtemp, readFile, rm, symlink, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join, relative} from "node:path";
import test, {type TestContext} from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {COLD_SOURCE_IMPORT_REQUEST_SCHEMA, executeColdSourceImport} from "../../loopx/control_plane/coordination/cold_source_import.ts";
import {inspectColdCoordinationStorage, COLD_SOURCE_INSPECTION_REQUEST_SCHEMA} from "../../loopx/control_plane/coordination/cold_source_inspection.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {localAuthorityProviderPaths, openLocalAuthorityStoreHandle, selectLocalAuthorityTarget} from "../../loopx/control_plane/coordination/local_authority_provider.ts";
import {checkLegacyCoordinationWriteAllowed, legacyCoordinationWriterFencePath,
  LEGACY_COORDINATION_WRITE_CHECK_REQUEST_SCHEMA} from "../../loopx/control_plane/coordination/legacy_writer_fence.ts";
import {shadowManagementDirectory} from "../../loopx/control_plane/coordination/shadow_management.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {projectCoordinationSource, SOURCE_PROJECTION_REQUEST_SCHEMA} from "../../loopx/control_plane/coordination/source_projection.ts";
import {sourceRequest, todo, type ShadowFixture} from "./shadow_file_fixture.ts";
import {acquireFileMutationLock, releaseFileMutationLock} from "../../loopx/control_plane/effect_runtime_io.ts";

/** Contract fixture for the trusted tar IO adapter. Real archive parsing and
 * installed CLI coverage live in the Python production-entrypoint tests. */
async function backupWitness(f: ShadowFixture, source: JsonObject): Promise<JsonObject> {
  const archive = join(f.root, "saved.tar.gz"), manifest = join(f.root, "saved.manifest.json");
  await writeFile(archive, "Saved archive byte identity"); await writeFile(manifest, "Saved manifest byte identity");
  const hash = (bytes: Buffer) => createHash("sha256").update(bytes).digest("hex");
  const snapshot = source.source_snapshot as JsonObject;
  const files = [f.statePath, String((snapshot.registry_source as JsonObject).path),
    ...(snapshot.lease_inventory as JsonObject[]).map(item => join(f.root, "goals", "goal-a", "task-leases", String(item.name)))];
  const members = await Promise.all(files.map(async path => ({archive_path: `runtime-root/${relative(f.root, path).replaceAll("\\", "/")}`,
    sha256: hash(await readFile(path))})));
  return {schema_version: "loopx_state_backup_source_witness_v0", archive_path: archive, archive_sha256: hash(await readFile(archive)),
    manifest_path: manifest, manifest_sha256: hash(await readFile(manifest)),
    included: [{source_path: f.root, archive_path: "runtime-root"}], members};
}

async function fixture(t: TestContext, provider: "file" | "sqlite" = "file") {
  const root = await mkdtemp(join(tmpdir(), "loopx-cold-import-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const statePath = join(root, "state.md");
  await writeFile(statePath, "---\ngoal_id: goal-a\nhandoff_mode: legacy\n---\n\n## Agent Todo\n\n");
  const archived = {...todo("archived", "done"), archive_state: "archive", source_section: "Agent Todo Archive",
    text: "Retained complete archive body", evidence: "Original independent evidence"};
  const active = {...todo("active"), claimed_by: "agent-a", note: "Complete source metadata",
    required_write_scopes: ["src/**"], successor_todo_ids: ["archived"]};
  const projection = projectCoordinationSource({schema_version: SOURCE_PROJECTION_REQUEST_SCHEMA, kind: "snapshot",
    goal_id: "goal-a", handoff_mode: "legacy", todos: [active, archived], leases: [],
    read_model_schema: "loopx_todo_canonical_read_record_v0"}).projection as JsonObject;
  const f: ShadowFixture = {root, statePath, baseline: projection,
    store: new FileAuthorityStore(join(root, "authority-shadow", "file-v0"), "goal-a")};
  await selectLocalAuthorityTarget(root, "goal-a", provider, true);
  const source = await sourceRequest(f, projection);
  const prepare = {schema_version: COLD_SOURCE_IMPORT_REQUEST_SCHEMA, action: "prepare", ...source,
    operation_id: "cold-import:original", target_handoff_mode: "soft_claim", target_provider: provider,
    source_backup: await backupWitness(f, source)};
  const operation = (action: "apply" | "recover", digest: unknown, stopped: unknown = true) => ({
    schema_version: COLD_SOURCE_IMPORT_REQUEST_SCHEMA, action, runtime_root: root, goal_id: "goal-a",
    operation_id: prepare.operation_id, expected_plan_sha256: digest,
    ...(action === "apply" ? {writers_stopped: stopped} : {})});
  const carrier = join(shadowManagementDirectory(root, "goal-a"), "cold-imports",
    `${canonicalAuthoritySha256(prepare.operation_id)}.json`);
  return {f, source, prepare, operation, carrier, active, archived};
}

test("empty selected SQLite preview remains inspectable, without accepting a lost or committed target", async t => {
  const f = await fixture(t, "sqlite");
  const prepared = await executeColdSourceImport(f.prepare);
  assert.equal(prepared.status, "prepared");
  const inspect = {...f.source, schema_version: COLD_SOURCE_INSPECTION_REQUEST_SCHEMA};
  const observed = await inspectColdCoordinationStorage(inspect);
  assert.equal(observed.ok, true, JSON.stringify(observed));
  assert.equal((observed.current as JsonObject).canonical, false);
  assert.equal(observed.execution_authority_granted, false);
  const opened = await openLocalAuthorityStoreHandle(f.f.root, "goal-a");
  assert.equal((await opened.store.loadAuthority()).status, "missing");
  const sqlitePath = localAuthorityProviderPaths(f.f.root, "goal-a").sqlite;
  await rm(sqlitePath, {recursive: true});
  const lost = await inspectColdCoordinationStorage(inspect);
  assert.equal(lost.ok, false);
  assert.equal(lost.current, null);
  // Missing identity must not be recreated by a read.
  await assert.rejects(() => openLocalAuthorityStoreHandle(f.f.root, "goal-a"));
});

test("unfenced committed SQLite and malformed selectors still refuse old-source inspection", async t => {
  const f = await fixture(t, "sqlite");
  const inspect = {...f.source, schema_version: COLD_SOURCE_INSPECTION_REQUEST_SCHEMA};
  const opened = await openLocalAuthorityStoreHandle(f.f.root, "goal-a");
  const committed = await opened.store.commitAuthority({expected_provider_revision: null,
    operation_id: "unfenced-existing-head", next_projection: f.f.baseline, events: [], receipts: []});
  assert.equal(committed.status, "applied");
  assert.equal((await inspectColdCoordinationStorage(inspect)).reason_code, "cold_source_canonical_authority_present");
  await writeFile(localAuthorityProviderPaths(f.f.root, "goal-a").marker, "{unreadable original selector");
  assert.equal((await inspectColdCoordinationStorage(inspect)).ok, false);
});

for (const provider of ["file", "sqlite"] as const) {
  test(`${provider}: cold cutover retains records and original receipt after later writes`, async t => {
    const f = await fixture(t, provider);
    const before = await readFile(f.f.statePath);
    const prepared = await executeColdSourceImport(f.prepare);
    assert.equal(prepared.status, "prepared", JSON.stringify(prepared));
    assert.equal(prepared.complete_goal_backup_verified, false);
    const originalRead = await executeColdSourceImport({...f.operation("recover", prepared.plan_sha256), action: "readback"});
    assert.equal(originalRead.status, "prepared");
    assert.equal(originalRead.authority_changed, false);
    assert.equal(originalRead.legacy_writer_fenced, false);
    assert.equal((await f.f.store.loadAuthority()).status, "missing");
    assert.equal((await executeColdSourceImport(f.operation("recover", prepared.plan_sha256))).reason_code,
      "cold_import_recovery_requires_fence");
    assert.equal((await executeColdSourceImport(f.operation("apply", prepared.plan_sha256, false))).reason_code,
      "cold_import_operator_stop_confirmation_required");
    const result = await executeColdSourceImport(f.operation("apply", prepared.plan_sha256));
    assert.equal(result.status, "applied", JSON.stringify(result));
    assert.equal(result.stop_confirmation_source, "operator_attestation");
    assert.equal(result.execution_authority_granted, false);
    const opened = await openLocalAuthorityStoreHandle(f.f.root, "goal-a");
    const initial = await opened.store.loadAuthority();
    assert.equal(initial.status, "loaded");
    if (initial.status !== "loaded") throw new Error("import head absent");
    assert.equal(initial.cursor, "1");
    assert.equal(initial.head.handoff_mode, "soft_claim");
    assert.deepEqual(initial.head.todos, [f.active, f.archived]);
    assert.deepEqual(initial.head.leases, []);
    assert.deepEqual(await readFile(f.f.statePath), before);
    const blocked = await checkLegacyCoordinationWriteAllowed({schema_version: LEGACY_COORDINATION_WRITE_CHECK_REQUEST_SCHEMA,
      runtime_root: f.f.root, goal_id: "goal-a"});
    assert.equal(blocked.status, "blocked");
    const later = {...initial.head, canonical_followup: "Preserve this new write"};
    assert.equal((await opened.store.commitAuthority({operation_id: "canonical:later",
      expected_provider_revision: initial.provider_revision, events: [], receipts: [], next_projection: later})).status, "applied");
    await rm(f.f.statePath);
    // Restart in another process, with no source snapshot or source file.
    const module = new URL("../../loopx/control_plane/coordination/cold_source_import.ts", import.meta.url).href;
    const recovered = spawnSync(process.execPath, ["--no-warnings", "--experimental-strip-types", "--input-type=module", "-e",
      `import {executeColdSourceImport} from ${JSON.stringify(module)}; process.stdout.write(JSON.stringify(await executeColdSourceImport(${JSON.stringify(f.operation("recover", prepared.plan_sha256))})));`], {encoding: "utf8"});
    assert.equal(recovered.status, 0, recovered.stderr);
    assert.equal(JSON.parse(recovered.stdout).status, "replayed", recovered.stdout);
    const current = await opened.store.loadAuthority();
    assert.equal(current.status, "loaded");
    if (current.status !== "loaded") throw new Error("later head absent");
    assert.deepEqual(current.head, later);
    assert.equal(current.cursor, "2");
    assert.equal((await opened.store.readReceipt("cold-import:original")).status, "found");
    const readback = await executeColdSourceImport({...f.operation("recover", prepared.plan_sha256), action: "readback"});
    assert.equal(readback.status, "replayed");
    assert.equal(readback.authority_changed, false);
    assert.deepEqual((await opened.store.loadAuthority()), current);
  });
}

test("changed source or reviewed digest never engages the writer fence", async t => {
  const f = await fixture(t);
  const prepared = await executeColdSourceImport(f.prepare);
  assert.equal(prepared.status, "prepared");
  assert.equal((await executeColdSourceImport(f.operation("apply", "0".repeat(64)))).reason_code, "cold_import_reviewed_plan_changed");
  await writeFile(f.f.statePath, "Unreviewed source change\n");
  assert.equal((await executeColdSourceImport(f.operation("apply", prepared.plan_sha256))).reason_code, "source_changed_retry");
  await assert.rejects(readFile(legacyCoordinationWriterFencePath(f.f.root, "goal-a")), {code: "ENOENT"});
});

test("a reviewed backup is required and cannot change before the first import", async t => {
  const f = await fixture(t);
  const backup = f.prepare.source_backup;
  const omitted = {...f.prepare}; delete (omitted as JsonObject).source_backup;
  assert.equal((await executeColdSourceImport(omitted)).reason_code, "cold_import_request_invalid");
  const missingSource = {...backup, members: []};
  assert.equal((await executeColdSourceImport({...f.prepare, source_backup: missingSource})).reason_code,
    "cold_import_backup_source_missing_or_changed");
  const prepared = await executeColdSourceImport(f.prepare);
  assert.equal(prepared.status, "prepared");
  await writeFile(String(backup.archive_path), "Changed archive after review");
  const refused = await executeColdSourceImport(f.operation("apply", prepared.plan_sha256));
  assert.equal(refused.reason_code, "cold_import_backup_changed");
  assert.equal(refused.legacy_writer_fenced, false);
  await assert.rejects(readFile(legacyCoordinationWriterFencePath(f.f.root, "goal-a")), {code: "ENOENT"});
});

test("a committed original receipt remains readable after backup loss", async t => {
  const f = await fixture(t);
  const prepared = await executeColdSourceImport(f.prepare);
  assert.equal((await executeColdSourceImport(f.operation("apply", prepared.plan_sha256))).status, "applied");
  await rm(String(f.prepare.source_backup.archive_path));
  const recovered = await executeColdSourceImport(f.operation("recover", prepared.plan_sha256));
  assert.equal(recovered.status, "replayed");
  assert.equal(recovered.executed, false);
  assert.equal(recovered.cursor, "1");
  assert.equal(recovered.complete_goal_backup_verified, false);
});

for (const variant of ["expired_active", "orphan_active", "symlink", "unsupported_filename"] as const) {
  test(`cold import rejects ${variant} lease facts rather than deleting them`, async t => {
    const f = await fixture(t);
    const directory = join(f.f.root, "goals", "goal-a", "task-leases");
    await mkdir(directory, {recursive: true});
    const id = variant === "expired_active" ? "active" : "orphan";
    const record = {schema_version: "task_lease_v0", goal_id: "goal-a", todo_id: id, owner: "agent-a",
      idempotency_key: "old-execution", status: "active", version: 3, lease_epoch: 2,
      expires_at: "2000-01-01T00:00:00Z"};
    if (variant === "symlink") {
      const outside = join(f.f.root, "original.json");
      await writeFile(outside, JSON.stringify(record));
      await symlink(outside, join(directory, "orphan.json"));
    } else await writeFile(join(directory, variant === "unsupported_filename" ? "unsafe name.json" : `${id}.json`), JSON.stringify(record));
    const result = await executeColdSourceImport(f.prepare);
    assert.equal(result.reason_code, variant === "symlink" ? "cold_import_lease_source_unsafe"
      : variant === "unsupported_filename" ? "cold_import_lease_source_unsupported" : "cold_import_lease_requires_settlement", JSON.stringify(result));
    await assert.rejects(readFile(f.carrier), {code: "ENOENT"});
    await assert.rejects(readFile(legacyCoordinationWriterFencePath(f.f.root, "goal-a")), {code: "ENOENT"});
  });
}

test("unresolved original outbox is refused without its producer or shadow history", async t => {
  const f = await fixture(t);
  const directory = join(f.f.root, "authority-shadow", "outbox", "goal-a", "todos");
  await mkdir(directory, {recursive: true});
  const original = join(directory, "0000000001-original.prepared.json");
  await writeFile(original, "Original retained prepared bytes\n");
  assert.equal((await executeColdSourceImport(f.prepare)).reason_code, "cold_import_outbox_requires_disposition");
  assert.equal(await readFile(original, "utf8"), "Original retained prepared bytes\n");
});

test("corrupt carrier and completed authority loss are fail-closed", async t => {
  const f = await fixture(t);
  const prepared = await executeColdSourceImport(f.prepare);
  const original = await readFile(f.carrier, "utf8");
  const corrupted = JSON.parse(original);
  corrupted.plan.target_projection.todos = [];
  await writeFile(f.carrier, JSON.stringify(corrupted));
  assert.equal((await executeColdSourceImport(f.operation("apply", prepared.plan_sha256))).reason_code, "cold_import_reviewed_plan_changed");
  // Recomputing a self-checksum does not qualify a changed target projection.
  corrupted.plan_sha256 = canonicalAuthoritySha256(corrupted.plan);
  await writeFile(f.carrier, JSON.stringify(corrupted));
  assert.equal((await executeColdSourceImport(f.operation("apply", corrupted.plan_sha256))).reason_code, "cold_import_carrier_invalid");
  await writeFile(f.carrier, original);
  assert.equal((await executeColdSourceImport(f.operation("apply", prepared.plan_sha256))).status, "applied");
  const opened = await openLocalAuthorityStoreHandle(f.f.root, "goal-a");
  assert.ok(opened.store instanceof FileAuthorityStore);
  await rm((opened.store as FileAuthorityStore).path);
  assert.equal((await executeColdSourceImport({...f.operation("recover", prepared.plan_sha256), action: "readback"})).reason_code,
    "cold_import_completed_authority_missing");
  const result = await executeColdSourceImport(f.operation("recover", prepared.plan_sha256));
  assert.equal(result.reason_code, "cold_import_completed_authority_missing", JSON.stringify(result));
  await assert.rejects(readFile((opened.store as FileAuthorityStore).path), {code: "ENOENT"});
});

test("killed fenced process recovers the original operation without reading Markdown", async t => {
  const f = await fixture(t);
  const prepared = await executeColdSourceImport(f.prepare);
  assert.equal(prepared.status, "prepared");
  const opened = await openLocalAuthorityStoreHandle(f.f.root, "goal-a");
  const store = opened.store as FileAuthorityStore;
  const lock = await acquireFileMutationLock(store.path);
  const module = new URL("../../loopx/control_plane/coordination/cold_source_import.ts", import.meta.url).href;
  const child = spawn(process.execPath, ["--no-warnings", "--experimental-strip-types", "--input-type=module", "-e",
    `import {executeColdSourceImport} from ${JSON.stringify(module)}; process.stdout.write(JSON.stringify(await executeColdSourceImport(${JSON.stringify(f.operation("apply", prepared.plan_sha256))})));`], {stdio: "pipe"});
  let output = "";
  child.stdout.on("data", bytes => { output += bytes; });
  const ended = once(child, "exit");
  try {
    const deadline = Date.now() + 4000;
    let fenced = false;
    while (Date.now() < deadline) {
      try { await readFile(legacyCoordinationWriterFencePath(f.f.root, "goal-a")); fenced = true; break; }
      catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
      await delay(20);
    }
    assert.equal(fenced, true, "child must reach durable fence before the injected crash");
    assert.equal((await store.loadAuthority()).status, "missing");
    child.kill("SIGKILL");
    assert.deepEqual(await ended, [null, "SIGKILL"]);
    assert.equal(output, "");
  } finally {
    child.kill("SIGKILL");
    await releaseFileMutationLock(store.path, lock.token);
  }
  await rm(f.f.statePath);
  const observed = await executeColdSourceImport({...f.operation("recover", prepared.plan_sha256), action: "readback"});
  assert.equal(observed.status, "prepared");
  assert.equal(observed.legacy_writer_fenced, true);
  assert.equal(observed.authority_changed, false);
  assert.equal((await store.loadAuthority()).status, "missing");
  await assert.rejects(readFile(`${f.carrier}.completed.json`), {code: "ENOENT"});
  const recovered = await executeColdSourceImport(f.operation("recover", prepared.plan_sha256));
  assert.equal(recovered.status, "applied", JSON.stringify(recovered));
  assert.equal((await executeColdSourceImport(f.operation("recover", prepared.plan_sha256))).status, "replayed");
  const head = await store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status !== "loaded") throw new Error("cold recovery head missing");
  assert.equal(head.cursor, "1");
  assert.deepEqual(head.head.todos, [f.active, f.archived]);
});

test("readback rejects a changed completion marker without repairing it", async t => {
  const f = await fixture(t);
  const prepared = await executeColdSourceImport(f.prepare);
  assert.equal((await executeColdSourceImport(f.operation("apply", prepared.plan_sha256))).status, "applied");
  const path = `${f.carrier}.completed.json`;
  await writeFile(path, JSON.stringify({operation_id: "another-operation"}));
  const damaged = await readFile(path);
  assert.equal((await executeColdSourceImport({...f.operation("recover", prepared.plan_sha256), action: "readback"})).reason_code,
    "cold_import_completion_identity_changed");
  assert.deepEqual(await readFile(path), damaged);
});

test("released orphan lease bytes stay in the source witness without entering the execution graph", async t => {
  const f = await fixture(t);
  const directory = join(f.f.root, "goals", "goal-a", "task-leases");
  await mkdir(directory, {recursive: true});
  const old = JSON.stringify({schema_version: "task_lease_v0", goal_id: "goal-a", todo_id: "orphan", owner: "agent-a",
    idempotency_key: "settled-original", status: "released", version: 9, lease_epoch: 8});
  await writeFile(join(directory, "orphan.json"), old);
  const source = await sourceRequest(f.f, f.f.baseline);
  const prepared = await executeColdSourceImport({...f.prepare, ...source, source_backup: await backupWitness(f.f, source)});
  assert.equal(prepared.status, "prepared", JSON.stringify(prepared));
  const carrier = JSON.parse(await readFile(f.carrier, "utf8"));
  const witness = carrier.plan.source_witness.find((item: JsonObject) => item.path === join(directory, "orphan.json"));
  assert.equal(Buffer.from(witness.bytes_base64, "base64").toString("utf8"), old);
  assert.equal((await executeColdSourceImport(f.operation("apply", prepared.plan_sha256))).status, "applied");
  const store = (await openLocalAuthorityStoreHandle(f.f.root, "goal-a")).store;
  const head = await store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status !== "loaded") throw new Error("head missing");
  assert.deepEqual(head.head.leases, []);
  assert.equal(await readFile(join(directory, "orphan.json"), "utf8"), old);
});
