import assert from "node:assert/strict";
import {mkdtemp, mkdir, readFile, readdir, realpath, rename, rm, writeFile} from "node:fs/promises";
import {createHash} from "node:crypto";
import {join} from "node:path";
import {tmpdir} from "node:os";
import test, {type TestContext} from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {prepareAuthorityForPlannedTransfer as prepare} from "../../loopx/control_plane/coordination/planned_transfer_authority.ts";
import {stageAuthorityForPlannedTransfer as stage} from "../../loopx/control_plane/coordination/planned_transfer_target.ts";
import {localAuthorityProviderPaths, selectLocalAuthorityTarget, openLocalAuthorityStoreHandle} from "../../loopx/control_plane/coordination/local_authority_provider.ts";
import {withFileMutationLock} from "../../loopx/control_plane/effect_runtime_io.ts";
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
import {resolveTestPython} from "../../scripts/test-python.mjs";

// Mirror the ordinary Python entrypoint's fixed daemon context. Requests cannot
// choose an interpreter; the real codec is used in every context-binding test.
process.env.LOOPX_EFFECT_RUNTIME_PYTHON = resolveTestPython();

async function fixture(t: TestContext, provider: "file" | "sqlite", leaseStatus = "released") {
  const root = await realpath(await mkdtemp(join(tmpdir(), "loopx-prepared-transfer-")));
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

async function destination(t: TestContext, f: Awaited<ReturnType<typeof fixture>>, provider: "file" | "sqlite") {
  const directory = await realpath(await mkdtemp(join(tmpdir(), "loopx-transfer-destination-")));
  t.after(() => rm(directory, {recursive: true, force: true}));
  const project = join(directory, "project"), root = join(directory, "runtime"), registry = join(project, ".loopx", "registry.json");
  const context = {goal: {id: "goal-a", repo: join(f.root, "project"), state_file: ".loopx/goals/goal-a/ACTIVE_GOAL_STATE.md",
    creation_operation_id: "original-creation-only", display_name: "Continue the original report",
    coordination: {registered_agents: ["agent-a"], agent_profiles: {"agent-a": {role: "report author", credential_ref: "source-only"}}},
    capabilities: {example: {credential_ref: "source-only"}},
    authority_sources: [{kind: "goal_doc", path: "requirements.md", role: "primary_goal_document"}]},
    state_text: "# Original Goal\n\nKeep every reporting constraint and the original Agent decision context.\n",
    runtime_root: f.root, registry_path: join(f.root, "project", ".loopx", "registry.json")};
  const sourceRegistry = context.registry_path;
  const sourceState = join(String(context.goal.repo), context.goal.state_file);
  await mkdir(join(String(context.goal.repo), ".loopx/goals/goal-a"), {recursive: true});
  await writeFile(sourceState, context.state_text);
  const sourceBytes = JSON.stringify({schema_version: "0.1", common_runtime_root: f.root, goals: [context.goal]});
  await writeFile(sourceRegistry, sourceBytes);
  const prepared = await prepare({...f.request, source_context: context,
    registry_witness: {path: sourceRegistry, sha256: createHash("sha256").update(sourceBytes).digest("hex")}});
  const target = {runtime_root: root, project, registry_path: registry, provider};
  return {root, project, registry, context, prepared,
    request: {source_preparation: prepared, source_context: context, archive: prepared.archive, target,
      destination_registry: {}, registry_witness: {path: registry, sha256: null}, execute: true} as JsonObject};
}

for (const sourceProvider of ["file", "sqlite"] as const) {
  test(`${sourceProvider}: staged destinations retain the original Goal/Task history without copying a fresh grant`, async t => {
    const f = await fixture(t, sourceProvider), original = await f.store.loadAuthority();
    for (const targetProvider of ["file", "sqlite"] as const) {
      const d = await destination(t, f, targetProvider);
      const result = await stage(d.request), registry = result.destination_registry as JsonObject;
      const goal = (registry.goals as JsonObject[])[0];
      assert.equal(result.target_is_active, false); assert.equal(result.execution_authority_granted, false);
      assert.equal(result.fresh_admission_required, true);
      assert.equal(goal.id, "goal-a"); assert.equal((goal.activation as JsonObject).state, "stopped");
      assert.deepEqual((goal.coordination as JsonObject).registered_agents, []);
      assert.equal(goal.creation_operation_id, undefined); assert.equal(goal.capabilities, undefined);
      assert.equal((goal.coordination as JsonObject).agent_profiles, undefined);
      assert.equal((goal.coordination as JsonObject).storage_target, undefined);
      assert.deepEqual(JSON.parse(await readFile(String(result.original_context_path), "utf8")), d.context);
      assert.equal(await readFile(join(d.project, String(goal.state_file)), "utf8"), d.context.state_text);
      const target = await openLocalAuthorityStoreHandle(d.root, "goal-a"), head = await target.store.loadAuthority();
      assert.equal(head.status, "loaded"); if (head.status !== "loaded") throw new Error("missing original Tasks");
      assert.deepEqual(head.head, f.projection);
      assert.notEqual(head.provider_revision, f.request.expected_provider_revision);
      for (let n = 1; n <= 3; n++) {
        const receipt = await target.store.readReceipt(`original-${n}`);
        assert.equal(receipt.status, "found");
        if (receipt.status === "found") assert.deepEqual(receipt.receipts, [{operation_id: `original-${n}`, independent_fact: n}]);
      }
      const fence = await loadLegacyCoordinationWriterFence(d.root, "goal-a");
      assert.equal(fence.status, "loaded"); if (fence.status === "loaded") assert.equal(fence.fence.role, "target");
      let calls = 0;
      for (const businessWrite of ["todo_update", "task_lease_acquire", "task_lease_renew", "authority_maintenance"] as const) {
        await assert.rejects(withCanonicalWriter(d.root, "goal-a", false, async () => ++calls, {businessWrite}), /prepared for transfer/);
      }
      assert.equal(calls, 0);
      // Model the codec adapter's exact publication, then retry with its fresh
      // byte witness. Unrelated registry revisions are not an operation identity.
      const registryBytes = JSON.stringify(registry);
      await writeFile(d.registry, registryBytes);
      const retry = {...d.request, destination_registry: registry,
        registry_witness: {path: d.registry, sha256: createHash("sha256").update(registryBytes).digest("hex")}};
      assert.deepEqual(await stage(retry), result);
      assert.deepEqual(await target.store.loadAuthority(), head);
      await assert.rejects(stage({...retry, source_context: {...d.context, state_text: "different requirements"}}), /context differ|occupied|original facts/);
      await assert.rejects(stage({...retry, registry_witness: {path: d.registry, sha256: "0".repeat(64)}}), /registry changed/);
    }
    assert.deepEqual(await f.store.loadAuthority(), original, "destination staging cannot mutate original history");
  });

  test(`${sourceProvider}: invalid or occupied destinations never receive imported history`, async t => {
    const f = await fixture(t, sourceProvider), d = await destination(t, f, "sqlite");
    for (const change of [{execute: false}, {unexpected: true}]) await assert.rejects(stage({...d.request, ...change}), /Explicit complete/);
    await assert.rejects(stage({...d.request, source_context: {...d.context, goal: {...d.context.goal, goal_instance_id: "not-an-admission"}}}), /owning lifecycle/);
    await assert.rejects(stage({...d.request, target: {...d.request.target as JsonObject, runtime_root: f.root}}), /overlap/);
    const damaged = join(d.project, "damaged.ndjson");
    await mkdir(d.project, {recursive: true}); await writeFile(damaged, "damaged original authority");
    await assert.rejects(stage({...d.request, archive: damaged}));
    await assert.rejects(readFile(legacyCoordinationWriterFencePath(d.root, "goal-a")), {code: "ENOENT"});
    await selectLocalAuthorityTarget(d.root, "goal-a", "sqlite", true);
    const occupied = await openLocalAuthorityStoreHandle(d.root, "goal-a");
    const committed = await occupied.store.commitAuthority({expected_provider_revision: null, operation_id: "destination-work",
      events: [], receipts: [], next_projection: f.projection});
    assert.equal(committed.status, "applied"); const before = await occupied.store.loadAuthority();
    await assert.rejects(stage(d.request), /already contains authority/);
    assert.deepEqual(await occupied.store.loadAuthority(), before);
    await assert.rejects(readFile(join(d.project, ".loopx/goals/goal-a/ACTIVE_GOAL_STATE.md")), {code: "ENOENT"});
  });

  test(`${sourceProvider}: source Goal context must match its real registry and state before fencing`, async t => {
    const f = await fixture(t, sourceProvider), project = join(f.root, "source-project");
    await mkdir(project);
    const goal = {id: "goal-a", repo: project, state_file: "GOAL.md", role: "controller"};
    const state = "# Original Goal\n\nPreserve the original accepted constraints.\n";
    const registry = join(project, "registry.json");
    const context = {goal, state_text: state, runtime_root: f.root, registry_path: registry};
    const bytes = JSON.stringify({common_runtime_root: f.root, goals: [goal]});
    await writeFile(registry, bytes); await writeFile(join(project, "GOAL.md"), state);
    const request = {...f.request, source_context: context,
      registry_witness: {path: registry, sha256: createHash("sha256").update(bytes).digest("hex")}};
    for (const change of [
      {source_context: {...context, state_text: "Different requirements"}},
      {source_context: {...context, goal: {...goal, role: "different original role"}}},
      {registry_witness: {path: registry, sha256: "0".repeat(64)}},
    ]) {
      await assert.rejects(prepare({...request, ...change}), /context changed|intent differs/);
      assert.deepEqual(JSON.parse(await readFile(f.fencePath, "utf8")), f.fence);
      await assert.rejects(readFile(join(f.directory, "preparation.json")), {code: "ENOENT"});
    }
    const interpreter = process.env.LOOPX_EFFECT_RUNTIME_PYTHON;
    delete process.env.LOOPX_EFFECT_RUNTIME_PYTHON;
    try { await assert.rejects(prepare(request), /source-selected Python codec adapter/); }
    finally { process.env.LOOPX_EFFECT_RUNTIME_PYTHON = interpreter; }
    assert.deepEqual(JSON.parse(await readFile(f.fencePath, "utf8")), f.fence);
    const lifetimeWire = JSON.stringify([{schema_version: "loopx_project_registry_envelope_v2", minimum_writer_protocol: "goal_instance_v2"},
      {profile_id: "source_session_v1", common_runtime_root: f.root, goals: [goal]}]);
    await writeFile(registry, lifetimeWire);
    await assert.rejects(prepare({...request, registry_witness: {
      path: registry, sha256: createHash("sha256").update(lifetimeWire).digest("hex")}}), /codec readback is unavailable|lifecycle-only adoption remains held/);
    assert.deepEqual(JSON.parse(await readFile(f.fencePath, "utf8")), f.fence);
    await writeFile(registry, bytes);
    const prepared = await prepare(request);
    assert.equal(prepared.source_context_sha256, canonicalAuthoritySha256(context));
    assert.deepEqual(await prepare(request), prepared);
    assert.deepEqual(JSON.parse(await readFile(join(f.directory, "preparation.json"), "utf8")).source_context, context);
  });

  test(`${sourceProvider}: completed staging never restores over missing or later destination work`, async t => {
    const f = await fixture(t, sourceProvider);
    for (const provider of ["file", "sqlite"] as const) {
      const d = await destination(t, f, provider);
      await stage(d.request);
      const opened = await openLocalAuthorityStoreHandle(d.root, "goal-a");
      const head = await opened.store.loadAuthority();
      if (head.status !== "loaded") throw new Error("fixture missing");
      // A synthetic provider-level append simulates a later accepted phase.
      // The stage cannot rewind it or rewrite its original receipt.
      assert.equal((await opened.store.commitAuthority({expected_provider_revision: head.provider_revision,
        operation_id: "later-original-work", events: [], receipts: [{result: "retain later result"}],
        next_projection: head.head})).status, "applied");
      const later = await opened.store.loadAuthority();
      await assert.rejects(stage(d.request), /Completed destination authority changed/);
      assert.deepEqual(await opened.store.loadAuthority(), later);
      const path = (opened.store as FileAuthorityStore | SqliteAuthorityStore).path;
      await rename(path, `${path}.retained-for-recovery`);
      await assert.rejects(stage(d.request), /lineage is missing|Completed destination authority changed/);
      await assert.rejects(readFile(path), {code: "ENOENT"});
    }
  });
}

test("target staging recovers an interrupted real File archive write under the original closed fence", async t => {
  const f = await fixture(t, "sqlite"), d = await destination(t, f, "file");
  const paths = localAuthorityProviderPaths(d.root, "goal-a");
  const target = new FileAuthorityStore(paths.file, "goal-a");
  await withFileMutationLock(target.path, async () => {
    await assert.rejects(stage(d.request), /archive_target_missing|archive_target_unavailable|archive_history/);
  });
  const held = await loadLegacyCoordinationWriterFence(d.root, "goal-a");
  assert.equal(held.status, "loaded"); if (held.status === "loaded") assert.equal(held.fence.role, "target");
  assert.equal((await target.loadAuthority()).status, "missing");
  const result = await stage(d.request);
  assert.equal(result.execution_authority_granted, false);
  assert.equal((await target.loadAuthority()).status, "loaded");
  assert.deepEqual(await stage(d.request), result);
});

test("staging a File Goal preserves unrelated Goals sharing that provider directory", async t => {
  const f = await fixture(t, "sqlite"), d = await destination(t, f, "file");
  const paths = localAuthorityProviderPaths(d.root, "other-goal");
  const other = new FileAuthorityStore(paths.file, "other-goal");
  assert.equal((await other.commitAuthority({expected_provider_revision: null, operation_id: "unrelated-original",
    events: [], receipts: [{result: "keep"}], next_projection: {...f.projection, goal_id: "other-goal"}})).status, "applied");
  const before = await other.loadAuthority();
  await stage(d.request);
  assert.deepEqual(await other.loadAuthority(), before);
});

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
