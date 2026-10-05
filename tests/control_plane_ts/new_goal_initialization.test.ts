import assert from "node:assert/strict";
import {test} from "node:test";
import {createHash} from "node:crypto";
import {mkdtemp, writeFile, readFile, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {manageNewGoalStorage} from "../../loopx/control_plane/coordination/local_authority_defaults.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {openLocalAuthorityStore, localAuthorityProviderPaths} from "../../loopx/control_plane/coordination/local_authority_provider.ts";
import {loadLegacyCoordinationWriterFence, checkLegacyCoordinationWriteAllowed, LEGACY_COORDINATION_WRITE_CHECK_REQUEST_SCHEMA} from "../../loopx/control_plane/coordination/legacy_writer_fence.ts";
import {projectCoordinationSource, SOURCE_PROJECTION_REQUEST_SCHEMA} from "../../loopx/control_plane/coordination/source_projection.ts";
import {TODO_DOMAIN_READ_RECORD_SCHEMA} from "../../loopx/control_plane/coordination/coordination_state_contract.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";

async function fixture(provider: "file" | "sqlite") {
  const root = await mkdtemp(join(tmpdir(), "loopx-new-authority-"));
  const state = join(root, "state.md"), registryPath = join(root, "registry.json");
  const bytes = "---\ngoal_id: new-goal\nhandoff_mode: hard_lease\n---\n# Goal\n\n## Agent Todo\n";
  const target = {schema_version: "loopx_new_goal_storage_target_v1", provider, handoff_mode: "hard_lease"};
  const registry = {common_runtime_root: root, goals: [{id: "new-goal", repo: root, state_file: "state.md",
    creation_operation_id: "create-native", coordination: {storage_target: target}}]};
  const registryBytes = JSON.stringify(registry);
  await writeFile(state, bytes); await writeFile(registryPath, registryBytes);
  const projection = projectCoordinationSource({schema_version: SOURCE_PROJECTION_REQUEST_SCHEMA, kind: "snapshot",
    goal_id: "new-goal", handoff_mode: "hard_lease", todos: [], leases: [], read_model_schema: TODO_DOMAIN_READ_RECORD_SCHEMA}).projection as JsonObject;
  const sha = (value: string) => createHash("sha256").update(value).digest("hex");
  const request: JsonObject = {action: "initialize", runtime_root: root, goal_id: "new-goal", target,
    creation_operation_id: "create-native", projection, source_snapshot: {state_path: state, registered_state_path: state,
      registered_runtime_root: root, state_bytes_sha256: `sha256:${sha(bytes)}`, lease_inventory: [],
      projection_sha256: canonicalAuthoritySha256(projection), evidence_files: [],
      registry_source: {path: registryPath, sha256: sha(registryBytes), registered_agents: []}}};
  return {root, state, registryPath, request};
}

for (const provider of ["file", "sqlite"] as const) {
  test(`${provider}: interrupted creation fences old writers and retries the original native receipt`, async () => {
    const f = await fixture(provider), Store = provider === "file" ? FileAuthorityStore : SqliteAuthorityStore;
    const commit = Store.prototype.commitAuthority;
    try {
      Store.prototype.commitAuthority = async () => {throw new Error("lost before commit");};
      await assert.rejects(manageNewGoalStorage(f.request), /interrupted/);
      const blocked = await checkLegacyCoordinationWriteAllowed({schema_version: LEGACY_COORDINATION_WRITE_CHECK_REQUEST_SCHEMA,
        runtime_root: f.root, goal_id: "new-goal"});
      assert.equal(blocked.status, "blocked");
      Store.prototype.commitAuthority = commit;
      const recovered = await manageNewGoalStorage(f.request);
      assert.equal(recovered.authority_initialized, true);
      const fence = await loadLegacyCoordinationWriterFence(f.root, "new-goal");
      assert.equal(fence.status, "loaded"); if (fence.status === "loaded") assert.equal(fence.fence.creation_completed, true);
      const store = await openLocalAuthorityStore(f.root, "new-goal"), loaded = await store.loadAuthority();
      assert.equal(loaded.status, "loaded"); if (loaded.status !== "loaded") return;
      await store.commitAuthority({expected_provider_revision: loaded.provider_revision, operation_id: "later-write",
        next_projection: {...loaded.head, progress: "later native write"}, events: [], receipts: []});
      const replay = await manageNewGoalStorage(f.request);
      assert.equal(replay.cursor, "1", "read the original creation receipt after later commits");
      const after = await store.loadAuthority();
      assert.equal(after.status, "loaded"); if (after.status === "loaded") assert.equal(after.head.progress, "later native write");
      const {projection: _projection, ...receiptRequest} = f.request;
      await rm(f.state);
      assert.equal((await manageNewGoalStorage(receiptRequest)).authority_initialized, true,
        "completed native receipt must not depend on Markdown source parsing or availability");
      assert.equal((await store.scanCommitted(null, 10)).status, "page");
    } finally {Store.prototype.commitAuthority = commit; await rm(f.root, {recursive: true, force: true});}
  });
  test(`${provider}: foreign operation and nonempty source do not grant creation or change selection`, async () => {
    const f = await fixture(provider);
    try {
      await assert.rejects(manageNewGoalStorage({...f.request, creation_operation_id: "foreign"}), /original operation/);
      const snapshot = f.request.source_snapshot as JsonObject;
      await assert.rejects(manageNewGoalStorage({...f.request, source_snapshot: {...snapshot,
        lease_inventory: [{name: "retained.json", bytes_sha256: "sha256:retained"}]}}), /lease history/);
      assert.equal((await loadLegacyCoordinationWriterFence(f.root, "new-goal")).status, "missing");
      await assert.rejects(readFile(localAuthorityProviderPaths(f.root, "new-goal").marker), {code: "ENOENT"});
      await writeFile(f.state, "changed source");
      await assert.rejects(manageNewGoalStorage(f.request), /source_changed_retry/);
      assert.equal((await loadLegacyCoordinationWriterFence(f.root, "new-goal")).status, "missing");
    } finally {await rm(f.root, {recursive: true, force: true});}
  });
}

test("creation opt-in reuses execution policy vocabulary; legacy is only a compatibility target", async () => {
  await assert.rejects(manageNewGoalStorage({action: "resolve", configuration: {schema_version: "loopx_goal_storage_defaults_v1",
    new_goal_provider: "sqlite", canonical_creation: true, new_goal_handoff_mode: "legacy"}}), /soft_claim or hard_lease/);
  assert.deepEqual(await manageNewGoalStorage({action: "resolve", configuration: {schema_version: "loopx_goal_storage_defaults_v1",
    new_goal_provider: "sqlite", canonical_creation: false, new_goal_handoff_mode: "hard_lease"}}),
    {schema_version: "loopx_new_goal_storage_target_v0", provider: "sqlite"});
});

test("completed File creation cannot recreate a lost authority document", async () => {
  const f = await fixture("file");
  try {
    await manageNewGoalStorage(f.request);
    const store = await openLocalAuthorityStore(f.root, "new-goal");
    assert.ok(store instanceof FileAuthorityStore);
    await rm(store.path);
    await assert.rejects(manageNewGoalStorage(f.request), /restore its complete backup/);
    await assert.rejects(readFile(store.path), {code: "ENOENT"});
  } finally {await rm(f.root, {recursive: true, force: true});}
});

test("unfinished creation preflight requests source capture without selecting a provider or fencing writers", async () => {
  const f = await fixture("sqlite");
  try {
    const {projection: _projection, ...request} = f.request;
    assert.deepEqual(await manageNewGoalStorage(request), {source_capture_required: true});
    assert.equal((await loadLegacyCoordinationWriterFence(f.root, "new-goal")).status, "missing");
    await assert.rejects(readFile(localAuthorityProviderPaths(f.root, "new-goal").marker), {code: "ENOENT"});
  } finally {await rm(f.root, {recursive: true, force: true});}
});
