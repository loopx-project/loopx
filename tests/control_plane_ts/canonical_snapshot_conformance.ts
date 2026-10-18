/** One snapshot contract on every real provider; no fake paging backend. */
import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import {coordinationTodoReadModel} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {CANONICAL_SNAPSHOT_PAGE_REQUEST, CANONICAL_SNAPSHOT_PAGE_BYTES, CANONICAL_SNAPSHOT_LOCAL_BYTES,
  readCanonicalSnapshotFromStore} from "../../loopx/control_plane/coordination/canonical_snapshot_page.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {normalizeGoalAcceptanceDocument} from "../../loopx/control_plane/goals/acceptance_contract.ts";
import {productionScaleCoordinationFixture} from "./production_scale_coordination_fixture.ts";
import type {AuthorityStoreConformanceFactory} from "./authority_store_conformance.ts";

/** Reuse the mixed production population and widen retained notes to cross the
 * actual RPC ceiling. This stresses bytes without inventing more domain rows. */
export function snapshotFixture(shape: "native" | "legacy" = "native") {
  const fixture = productionScaleCoordinationFixture("goal-a", shape);
  const rows = fixture.projection.todos as JsonObject[];
  for (const row of rows) row.note = `${row.note ?? ""} ${"Retained full record 文🙂 ".repeat(240)}`;
  fixture.projection.todo_read_model = coordinationTodoReadModel(rows,
    String((fixture.projection.todo_read_model as JsonObject).schema_version));
  assert.ok(Buffer.byteLength(JSON.stringify(fixture.projection)) > 2 * 1024 * 1024);
  return fixture;
}

export function snapshotRequest(overrides: JsonObject = {}): JsonObject {
  return {schema_version: CANONICAL_SNAPSHOT_PAGE_REQUEST, runtime_root: "/disposable",
    goal_id: "goal-a", include_leases: true, projection_readback: null, after: null, ...overrides};
}
export async function seedSnapshot(store: AuthorityStore, projection: JsonObject): Promise<void> {
  const result = await store.commitAuthority({expected_provider_revision: null, operation_id: "snapshot-seed",
    next_projection: projection, events: [], receipts: []});
  assert.equal(result.status, "applied", JSON.stringify(result));
}
export async function collectSnapshot(store: AuthorityStore, request = snapshotRequest()) {
  const pages: JsonObject[] = [], todos: JsonObject[] = [], leases: JsonObject[] = [];
  let after: unknown = null;
  do {
    const page = await readCanonicalSnapshotFromStore({...request, after}, store);
    assert.equal(page.status, "page", JSON.stringify(page));
    assert.ok(Buffer.byteLength(JSON.stringify(page), "utf8") <= CANONICAL_SNAPSHOT_PAGE_BYTES);
    if (pages.length) {
      assert.deepEqual(page.snapshot, pages[0].snapshot);
      assert.deepEqual(page.metadata, pages[0].metadata);
    }
    pages.push(page);
    todos.push(...page.todos as JsonObject[]);
    leases.push(...(page.leases ?? []) as JsonObject[]);
    after = page.next;
    assert.ok(pages.length <= 100, "small conformance fixture must progress");
  } while (after !== null);
  return {pages, todos, leases};
}

export function registerCanonicalSnapshotConformance(name: string, factory: AuthorityStoreConformanceFactory): void {
  for (const shape of ["native", "legacy"] as const) {
    test(`${name}: paged canonical ${shape} reads retain the complete complex population`, async t => {
      const {store} = await factory(t);
      const fixture = snapshotFixture(shape);
      await seedSnapshot(store, fixture.projection);
      const before = await store.loadAuthority();
      const result = await collectSnapshot(store);
      assert.ok(result.pages.length > 1, "fixture must cross a page boundary");
      assert.equal(result.todos.length, fixture.expected_initial_todo_count);
      assert.equal(result.leases.length, fixture.expected_current_lease_count);
      const byId = (rows: JsonObject[]) => new Map(rows.map(row => [row.todo_id, row]));
      assert.deepEqual(byId(result.todos), byId(fixture.projection.todos as JsonObject[]));
      assert.deepEqual(byId(result.leases), byId(fixture.projection.leases as JsonObject[]));
      const complete = await readCanonicalSnapshotFromStore(snapshotRequest(), store, CANONICAL_SNAPSHOT_LOCAL_BYTES);
      assert.equal(complete.next, null);
      assert.deepEqual(complete.snapshot, result.pages[0].snapshot);
      assert.deepEqual(complete.metadata, result.pages[0].metadata);
      assert.deepEqual(complete.todos, result.todos);
      assert.deepEqual(complete.leases, result.leases);
      assert.ok(Buffer.byteLength(JSON.stringify(complete)) > CANONICAL_SNAPSHOT_PAGE_BYTES);
      // Exercise capacity fallback on this real provider with a smaller private
      // response allowance. No caller/protocol constant or wire budget changes.
      const bounded = await readCanonicalSnapshotFromStore(snapshotRequest(), store, CANONICAL_SNAPSHOT_PAGE_BYTES);
      assert.deepEqual(bounded, result.pages[0]);
      assert.deepEqual(await store.loadAuthority(), before, "pagination is read-only");
      const todoOnly = await collectSnapshot(store, snapshotRequest({include_leases: false}));
      assert.deepEqual(todoOnly.todos, result.todos);
      assert.deepEqual(todoOnly.leases, []);
      assert.ok(todoOnly.pages.every(page => !("leases" in page)));
      const completeTodoOnly = await readCanonicalSnapshotFromStore(snapshotRequest({include_leases: false}),
        store, CANONICAL_SNAPSHOT_LOCAL_BYTES);
      assert.deepEqual(completeTodoOnly.todos, result.todos);
      assert.equal("leases" in completeTodoOnly, false);
    });
  }

  test(`${name}: an overlapping commit rejects continuation even when records did not change`, async t => {
    const {store, contender} = await factory(t);
    const fixture = snapshotFixture();
    await seedSnapshot(store, fixture.projection);
    const first = await readCanonicalSnapshotFromStore(snapshotRequest(), store);
    assert.notEqual(first.next, null);
    const head = await contender.loadAuthority();
    assert.equal(head.status, "loaded");
    if (head.status !== "loaded") throw new Error("missing seeded state");
    assert.equal((await contender.commitAuthority({expected_provider_revision: head.provider_revision,
      operation_id: "concurrent-commit", next_projection: head.head, events: [], receipts: []})).status, "applied");
    await assert.rejects(readCanonicalSnapshotFromStore(snapshotRequest({after: first.next}), store),
      {code: "canonical_snapshot_changed"});
    await assert.rejects(readCanonicalSnapshotFromStore(snapshotRequest({after: first.next}),
      store, CANONICAL_SNAPSHOT_LOCAL_BYTES), {code: "canonical_snapshot_changed"});
    const restarted = await collectSnapshot(store);
    assert.notDeepEqual(restarted.pages[0].snapshot, first.snapshot);
  });

  test(`${name}: continuation binds query, Goal, store incarnation and revision`, async t => {
    const {store} = await factory(t);
    await seedSnapshot(store, snapshotFixture().projection);
    const first = await readCanonicalSnapshotFromStore(snapshotRequest(), store);
    for (const key of ["goal_id", "store_identity", "provider_revision", "cursor", "query_sha256", "todo_count", "lease_count"]) {
      const next = structuredClone(first.next) as JsonObject;
      const identity = next.snapshot as JsonObject;
      identity[key] = key.endsWith("count") ? Number(identity[key]) + 1 : "foreign";
      await assert.rejects(readCanonicalSnapshotFromStore(snapshotRequest({after: next}), store),
        {code: "canonical_snapshot_changed"}, key);
      await assert.rejects(readCanonicalSnapshotFromStore(snapshotRequest({after: next}),
        store, CANONICAL_SNAPSHOT_LOCAL_BYTES), {code: "canonical_snapshot_changed"}, key);
    }
    for (const changed of [{include_leases: false},
      {projection_readback: {provider_revision: "other", changed: true, attempt: 1, target: "latest" as const}}]) {
      await assert.rejects(readCanonicalSnapshotFromStore(snapshotRequest({...changed, after: first.next}), store),
        {code: "canonical_snapshot_changed"});
    }
  });

  test(`${name}: an empty canonical snapshot is a complete answer`, async t => {
    const {store} = await factory(t);
    const fixture = snapshotFixture().projection;
    fixture.todos = []; fixture.leases = [];
    fixture.todo_read_model = coordinationTodoReadModel([], "loopx_todo_domain_read_record_v0");
    await seedSnapshot(store, fixture);
    const result = await collectSnapshot(store);
    assert.equal(result.pages.length, 1);
    assert.equal(result.pages[0].next, null);
    assert.deepEqual(result.todos, []);
    assert.deepEqual(result.leases, []);
    assert.deepEqual(await readCanonicalSnapshotFromStore(snapshotRequest(), store,
      CANONICAL_SNAPSHOT_LOCAL_BYTES), result.pages[0]);
  });

  test(`${name}: projection confirmation belongs to the same snapshot as every page`, async t => {
    const {store} = await factory(t);
    await seedSnapshot(store, snapshotFixture().projection);
    const head = await store.loadAuthority();
    if (head.status !== "loaded") throw new Error("missing seeded head");
    for (const [revision, changed, expected] of [[head.provider_revision, false, "current"],
      [head.provider_revision, true, "delivered"], ["stale", true, "pending"]] as const) {
      const result = await collectSnapshot(store, snapshotRequest({projection_readback:
        {provider_revision: revision, changed, attempt: 1, target: "latest" as const}}));
      assert.ok(result.pages.every(page => ((page.metadata as JsonObject).projection_readback as JsonObject).status === expected));
      const complete = await readCanonicalSnapshotFromStore(snapshotRequest({projection_readback:
        {provider_revision: revision, changed, attempt: 1, target: "latest" as const}}), store, CANONICAL_SNAPSHOT_LOCAL_BYTES);
      assert.deepEqual(complete.metadata, result.pages[0].metadata);
    }
  });
  test(`${name}: enabled acceptance guards stay attached to their own page`, async t => {
    const {store} = await factory(t);
    const projection = snapshotFixture().projection;
    const document = normalizeGoalAcceptanceDocument({objective: "Verify all required work", non_goals: [],
      criteria: [{id: "criterion-a", description: "Required validation succeeds", validation_argv: ["true"], validation_timeout_seconds: 10}],
      bindings: []});
    // Deliberately make every row applicable, so missing a page guard cannot hide
    // behind the complex fixture's unrelated maintenance/terminal work classes.
    projection.todos = (projection.todos as JsonObject[]).map(row => ({...row,
      role: "agent", task_class: "advancement_task", status: "open", done: false, archive_state: "active"}));
    projection.todo_read_model = coordinationTodoReadModel(projection.todos as JsonObject[], "loopx_todo_domain_read_record_v0");
    projection.goal_acceptance = {schema_version: "loopx_goal_acceptance_v0", enabled: true,
      revision: 1, digest: canonicalAuthoritySha256(document), document, verification: null, bindings: []};
    await seedSnapshot(store, projection);
    const result = await collectSnapshot(store);
    const seen = new Set<string>();
    for (const page of result.pages) {
      const guards = page.goal_acceptance_work_guards as JsonObject;
      const ids = (page.todos as JsonObject[]).map(row => String(row.todo_id));
      assert.deepEqual(Object.keys(guards).sort(), [...ids].sort());
      for (const id of ids) {
        assert.equal(seen.has(id), false);
        seen.add(id);
      }
      assert.equal(((page.metadata as JsonObject).goal_acceptance_contract as JsonObject).enabled, true);
    }
    assert.equal(seen.size, result.todos.length);
    const complete = await readCanonicalSnapshotFromStore(snapshotRequest(), store, CANONICAL_SNAPSHOT_LOCAL_BYTES);
    const allGuards = Object.assign({}, ...result.pages.map(page => page.goal_acceptance_work_guards));
    assert.deepEqual(complete.goal_acceptance_work_guards, allGuards);
    assert.deepEqual(complete.todos, result.todos);
  });

  test(`${name}: a complete read rejects store replacement during admission`, async t => {
    const {store} = await factory(t);
    await seedSnapshot(store, snapshotFixture().projection);
    const identity = store.storeIdentity.bind(store);
    let calls = 0;
    // Inject replacement at the snapshot admission boundary. Providers may
    // check identity internally during load; keep those calls bound to the
    // real store rather than changing the provider's own verification path.
    const observedStore = new Proxy(store, {get(target, key) {
      if (key === "storeIdentity") return async () => {
        const observed = await identity();
        return ++calls > 1 && observed.status === "available"
          ? {...observed, store_identity: `${observed.store_identity}:replaced`} : observed;
      };
      const value = Reflect.get(target, key, target);
      return typeof value === "function" ? value.bind(target) : value;
    }});
    await assert.rejects(readCanonicalSnapshotFromStore(snapshotRequest(), observedStore,
      CANONICAL_SNAPSHOT_LOCAL_BYTES), {code: "canonical_snapshot_changed"});
  });

}
