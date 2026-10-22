import assert from "node:assert/strict";
import test from "node:test";
import {mkdtemp, rm, readdir, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {sqliteRuntimeIdentity} from "../../loopx/control_plane/coordination/sqlite_runtime.ts";
import {coordinationTodoReadModel} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {readCanonicalSnapshotFromStore, readCanonicalSnapshotPage, readCanonicalSnapshot,
  CANONICAL_SNAPSHOT_PAGE_BYTES, CANONICAL_SNAPSHOT_LOCAL_BYTES} from "../../loopx/control_plane/coordination/canonical_snapshot_page.ts";
import {productionScaleCoordinationFixture} from "./production_scale_coordination_fixture.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {goalAcceptanceTodoDigest, normalizeGoalAcceptanceDocument} from "../../loopx/control_plane/goals/acceptance_contract.ts";
import {reviseGoalObjective} from "../../loopx/control_plane/goals/goal_objective.ts";
import {collectSnapshot, registerCanonicalSnapshotConformance, seedSnapshot, snapshotRequest, snapshotFixture} from "./canonical_snapshot_conformance.ts";
import {engageCanonicalRoute} from "./local_promotion_fixture.ts";
import {legacyCoordinationWriterFencePath} from "../../loopx/control_plane/coordination/legacy_writer_fence.ts";
import {listLocalCoordinationTodos, readLocalCoordinationTodo} from "../../loopx/control_plane/coordination/local_authority_read.ts";
import {observeLocalCoordinationOwnership} from "../../loopx/control_plane/coordination/local_authority_runtime.ts";

async function fixture(t: test.TestContext, kind: "file" | "sqlite" = "file") {
  const root = await mkdtemp(join(tmpdir(), "canonical-snapshot-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const Store = kind === "file" ? FileAuthorityStore : SqliteAuthorityStore;
  return {store: new Store(root, "goal-a"), contender: new Store(root, "goal-a")};
}
const qualifiedSqlite = sqliteRuntimeIdentity().sqlite_authority_qualified;
if (!qualifiedSqlite) test.skip("SQLite snapshot conformance requires a qualified SQLite runtime", () => {});
for (const kind of (qualifiedSqlite ? ["file", "sqlite"] : ["file"]) as ("file" | "sqlite")[]) {
  registerCanonicalSnapshotConformance(kind, t => fixture(t, kind));
  test(`${kind}: a normal complete collection keeps a single RPC response`, async t => {
    const {store} = await fixture(t, kind);
    const source = productionScaleCoordinationFixture("goal-a", "native");
    await seedSnapshot(store, source.projection);
    const result = await collectSnapshot(store);
    assert.equal(result.pages.length, 1);
    assert.equal(result.todos.length, source.expected_initial_todo_count);
    assert.equal(result.leases.length, source.expected_current_lease_count);
  });
  test(`${kind}: acceptance read reuse ends at the loaded provider head`, async t => {
    const {store} = await fixture(t, kind);
    const todo = {todo_id: "todo_work", role: "agent", status: "open", done: false,
      text: "Deliver the reviewed requirement", task_class: "advancement_task", archive_state: "active"};
    const document = normalizeGoalAcceptanceDocument({objective: "Verify the reviewed requirement", non_goals: [],
      criteria: [{id: "criterion-a", description: "The check passes", validation_argv: ["true"]}],
      bindings: [{todo_id: todo.todo_id, criterion_ids: ["criterion-a"]}]});
    const acceptance = {schema_version: "loopx_goal_acceptance_v0", enabled: true, revision: 1,
      digest: canonicalAuthoritySha256(document), document, verification: null,
      bindings: [{todo_id: todo.todo_id, todo_semantic_digest: goalAcceptanceTodoDigest(todo),
        revision: 1, criterion_ids: ["criterion-a"], confirmed_by: "owner"}]};
    await seedSnapshot(store, authorityProjectionFixture("goal-a", [todo], [], "native", {goal_acceptance: acceptance}));
    const before = await readCanonicalSnapshotFromStore(snapshotRequest(), store);
    assert.equal(((before.goal_acceptance_work_guards as JsonObject).todo_work as JsonObject).allowed, true);
    const current = await store.loadAuthority();
    assert.equal(current.status, "loaded");
    if (current.status !== "loaded") throw new Error("synthetic provider head missing");
    const changed = authorityProjectionFixture("goal-a", [{...todo, text: "A different requirement"}], [], "native", {goal_acceptance: acceptance});
    const commit = await store.commitAuthority({operation_id: "change-requirement",
      expected_provider_revision: current.provider_revision, next_projection: changed, events: [], receipts: []});
    assert.equal(commit.status, "applied");
    const after = await readCanonicalSnapshotFromStore(snapshotRequest(), store);
    const guard = (after.goal_acceptance_work_guards as JsonObject).todo_work as JsonObject;
    assert.equal(guard.allowed, false);
    assert.equal(guard.reason_code, "goal_acceptance_stale");
    assert.notDeepEqual(after.snapshot, before.snapshot);
  });
  test(`${kind}: objective-only snapshots retain the current Goal direction`, async t => {
    const {store} = await fixture(t, kind);
    const head = authorityProjectionFixture("goal-a", [], [], "native");
    const direction = {objective: "Maintain the service and its recovery path", reason: "Bootstrap is complete", source_ref: "message:owner"};
    head.goal_objective = reviseGoalObjective(head, null, "Bootstrap the service", ["agent-a"], direction);
    await seedSnapshot(store, head);
    const result = await readCanonicalSnapshotFromStore(snapshotRequest(), store);
    const contract = (result.metadata as JsonObject).goal_acceptance_contract as JsonObject;
    assert.equal((contract.goal_objective as JsonObject).objective, direction.objective);
    assert.equal(contract.criteria, undefined, "the Goal direction does not enable optional acceptance checks");
    assert.equal(result.goal_acceptance_work_guards, undefined);
  });
  test(`${kind}: UTF-8 byte pages retain records beyond the old 2 MiB RPC ceiling`, async t => {
    const {store} = await fixture(t, kind);
    const projection = snapshotFixture().projection;
    const todos = projection.todos as JsonObject[];
    assert.ok(Buffer.byteLength(JSON.stringify(projection)) > 2 * 1024 * 1024);
    await seedSnapshot(store, projection);
    const result = await collectSnapshot(store);
    assert.deepEqual(new Map(result.todos.map(row => [row.todo_id, row])), new Map(todos.map(row => [row.todo_id, row])));
    assert.ok(result.pages.some(page => (page.todos as unknown[]).length < 4096 && page.next !== null));
  });
}

test("oversized single records fail explicitly instead of truncating or looping", async t => {
  const {store} = await fixture(t);
  const projection = snapshotFixture().projection;
  const todos = projection.todos as JsonObject[];
  todos[0].note = "x".repeat(CANONICAL_SNAPSHOT_PAGE_BYTES);
  projection.todo_read_model = coordinationTodoReadModel(todos, "loopx_todo_domain_read_record_v0");
  await seedSnapshot(store, projection);
  const before = await store.loadAuthority();
  await assert.rejects(collectSnapshot(store), {code: "canonical_snapshot_record_too_large"});
  const complete = await readCanonicalSnapshotFromStore(snapshotRequest(), store, CANONICAL_SNAPSHOT_LOCAL_BYTES);
  assert.equal(complete.next, null);
  assert.deepEqual(complete.todos, todos, "the private transport keeps the whole oversized inline record");
  assert.deepEqual(await store.loadAuthority(), before);
});

for (const position of [null, -1, true, "128", 0.5]) {
  test(`invalid continuation offset ${JSON.stringify(position)} is rejected`, async t => {
    const {store} = await fixture(t);
    await seedSnapshot(store, snapshotFixture().projection);
    const first = await readCanonicalSnapshotFromStore(snapshotRequest(), store);
    const after = structuredClone(first.next) as JsonObject;
    after.todo_offset = position;
    await assert.rejects(readCanonicalSnapshotFromStore(snapshotRequest({after}), store), {code: "canonical_snapshot_request_invalid"});
  });
}


test("a missing runtime provider read does not initialize a replacement authority", async t => {
  const root = await mkdtemp(join(tmpdir(), "canonical-missing-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  await engageCanonicalRoute(root);
  const before = await readdir(root);
  const result = await readCanonicalSnapshotPage(snapshotRequest({runtime_root: root}));
  assert.equal(result.status, "missing");
  assert.equal(result.reason_code, undefined);
  assert.deepEqual(await readdir(root), before);
  assert.equal("todos" in result, false);
  assert.deepEqual(await readCanonicalSnapshot(snapshotRequest({runtime_root: root})), result);
  assert.deepEqual(await readdir(root), before);
});

for (const fence of ["missing", "corrupt"] as const) {
  test(`ordinary current reads reject a ${fence} cutover before opening any provider`, async t => {
    const root = await mkdtemp(join(tmpdir(), "unmigrated-read-"));
    t.after(() => rm(root, {recursive: true, force: true}));
    if (fence === "corrupt") {
      await engageCanonicalRoute(root);
      await writeFile(legacyCoordinationWriterFencePath(root, "goal-a"), "{");
    }
    const before = await readdir(root);
    const dependencies = {createStore: () => assert.fail("unadmitted read opened a provider")};
    const results = [
      await readCanonicalSnapshotPage(snapshotRequest({runtime_root: root}), dependencies),
      await listLocalCoordinationTodos({schema_version: "loopx_local_coordination_todo_list_request_v0",
        runtime_root: root, goal_id: "goal-a"}, dependencies),
      await readLocalCoordinationTodo({schema_version: "loopx_local_coordination_todo_read_request_v0",
        runtime_root: root, goal_id: "goal-a", todo_id: "todo_a"}, dependencies),
      await observeLocalCoordinationOwnership({schema_version: "loopx_local_ownership_observation_request_v0",
        runtime_root: root, goal_id: "goal-a", observed_at: "2026-09-29T00:00:00Z"}),
    ];
    for (const result of results) {
      assert.equal(result.reason_code, fence === "missing"
        ? "canonical_authority_required" : "canonical_authority_fence_invalid");
      assert.equal(result.decision_read_from_provider, false);
      assert.equal(result.legacy_fallback_used, false);
      assert.equal("todos" in result, false);
    }
    assert.deepEqual(await readdir(root), before);
  });
}

test("ordering uses Unicode code points rather than JavaScript default UTF-16 sorting", async t => {
  const {store} = await fixture(t);
  const projection = snapshotFixture().projection;
  projection.todos = ["z", "\ue000", "🙂"].map(todo_id => ({
    schema_version: "todo_domain_record_v0", todo_id, role: "agent", status: "open", done: false,
    text: "Preserve stable identity", task_class: "advancement_task", archive_state: "active"}));
  projection.leases = [];
  projection.todo_read_model = coordinationTodoReadModel(projection.todos as JsonObject[], "loopx_todo_domain_read_record_v0");
  await seedSnapshot(store, projection);
  assert.deepEqual((await collectSnapshot(store)).todos.map(row => row.todo_id), ["z", "\ue000", "🙂"]);
});

for (const kind of (qualifiedSqlite ? ["file", "sqlite"] : ["file"]) as ("file" | "sqlite")[]) {
  test(`${kind}: collection and ownership preserve their validation precedence on one retained head`, async t => {
    const {readCoordinationOwnership} = await import("../../loopx/control_plane/coordination/ownership_observation.ts");
    for (const variant of ["valid", "bad_digest", "orphan_lease", "both", "duplicate_todo"] as const) {
      const {store} = await fixture(t, kind);
      const projection = productionScaleCoordinationFixture("goal-a", "native").projection;
      if (variant === "bad_digest" || variant === "both") {
        (projection.todo_read_model as JsonObject).records_sha256 = "0".repeat(64);
      }
      if (variant === "orphan_lease" || variant === "both" || variant === "duplicate_todo") {
        (projection.leases as JsonObject[]).push({todo_id: "missing-todo"});
      }
      if (variant === "duplicate_todo") {
        (projection.todos as JsonObject[]).push((projection.todos as JsonObject[])[0]!);
      }
      await seedSnapshot(store, projection);
      const before = await store.loadAuthority();
      if (variant === "valid") {
        const full = await collectSnapshot(store);
        assert.deepEqual(full.todos, projection.todos);
        assert.deepEqual(full.leases, projection.leases);
        const observation = await readCoordinationOwnership(store, "goal-a", "2026-09-29T00:00:00Z");
        assert.equal(observation.todo_count, full.todos.length);
        assert.equal(observation.lease_count, full.leases.length);
        full.todos[0]!.note = "consumer-only edit";
        full.leases[0]!.owner = "consumer-only owner";
        const fresh = await collectSnapshot(store);
        assert.deepEqual(fresh.todos, projection.todos);
        assert.deepEqual(fresh.leases, projection.leases);
      } else {
        const collectionReason = variant === "duplicate_todo" ? /duplicate todo ids/
          : variant === "orphan_lease" ? /lease references an unknown todo/ : /digest mismatch/;
        const ownershipReason = variant === "duplicate_todo" ? /duplicate todo ids/
          : variant === "bad_digest" ? /digest mismatch/ : /lease references an unknown todo/;
        await assert.rejects(readCanonicalSnapshotFromStore(snapshotRequest(), store), collectionReason);
        await assert.rejects(readCanonicalSnapshotFromStore(snapshotRequest(), store,
          CANONICAL_SNAPSHOT_LOCAL_BYTES), collectionReason);
        await assert.rejects(readCoordinationOwnership(store, "goal-a", "2026-09-29T00:00:00Z"), ownershipReason);
        // Todo-only callers never acquired a lease-graph dependency. Keep that
        // acceptance boundary even while complete reads reuse the Todo index.
        if (variant === "orphan_lease") {
          const todoOnly = await collectSnapshot(store, snapshotRequest({include_leases: false}));
          assert.deepEqual(todoOnly.todos, projection.todos);
          assert.deepEqual(todoOnly.leases, []);
        }
      }
      assert.deepEqual(await store.loadAuthority(), before, "validation cannot rewrite the retained head");
    }
  });
}
