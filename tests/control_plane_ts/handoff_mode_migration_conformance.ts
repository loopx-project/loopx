import assert from "node:assert/strict";
import {mkdtemp, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import {canonicalAuthoritySha256 as sha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {verifyAuthorityArchive, restoreAuthorityArchive} from "../../loopx/control_plane/coordination/authority_archive.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {planHandoffModeMigration, executeHandoffModeMigration} from "../../loopx/control_plane/coordination/handoff_mode_migration.ts";
import {productionScaleCoordinationFixture} from "./production_scale_coordination_fixture.ts";
import type {AuthorityStoreConformanceFactory} from "./authority_store_conformance.ts";

const goal = "mode-goal";
async function prepare(t: test.TestContext, factory: AuthorityStoreConformanceFactory, mode = "hard_lease") {
  const runtime = await mkdtemp(join(tmpdir(), "loopx-policy-migration-"));
  t.after(() => rm(runtime, {recursive: true, force: true}));
  const {store, contender} = await factory(t);
  const fixture = productionScaleCoordinationFixture(goal);
  const projection = structuredClone(fixture.projection) as JsonObject;
  projection.handoff_mode = "legacy";
  for (const lease of projection.leases as JsonObject[]) lease.status = "released";
  const seedReceipt = {schema_version: "synthetic_seed_v0", operation_id: "seed-policy", value: {untouched: [1, "界"]}};
  assert.equal((await store.commitAuthority({expected_provider_revision: null, operation_id: "seed-policy",
    next_projection: projection, events: [{kind: "seed"}], receipts: [seedReceipt]})).status, "applied");
  const input = {goal_id: goal, runtime_root: runtime, plan: join(runtime, "plan.json"),
    registered_agents: [...fixture.registered_agents], observed_at: "2026-10-01T00:00:00Z"};
  const preview = await planHandoffModeMigration(store, {...input, requested_mode: mode});
  assert.equal(preview.status, "planned", JSON.stringify(preview));
  return {store, contender, projection, input, preview, request: {...input, execute: true,
    plan_sha256: String(preview.plan_sha256)}};
}
function intercept(store: AuthorityStore, commit: AuthorityStore["commitAuthority"]): AuthorityStore {
  return {storeIdentity: () => store.storeIdentity(), loadAuthority: () => store.loadAuthority(),
    readReceipt: id => store.readReceipt(id), scanCommitted: (cursor, limit) => store.scanCommitted(cursor, limit), commitAuthority: commit};
}

export function registerHandoffModeMigrationConformance(provider: string, factory: AuthorityStoreConformanceFactory) {
  test(`${provider}: reviewed policy migration preserves all metadata, receipts, claims and backup history`, async t => {
    const f = await prepare(t, factory);
    const before = await f.store.loadAuthority();
    assert.equal((await executeHandoffModeMigration(f.store, {...f.request, execute: false})).status, "planned");
    await assert.rejects(readFile(`${f.input.plan}.backup.jsonl`), {code: "ENOENT"});
    assert.deepEqual(await f.store.loadAuthority(), before);
    const applied = await executeHandoffModeMigration(f.store, f.request);
    assert.equal(applied.status, "applied", JSON.stringify(applied));
    const current = await f.contender.loadAuthority();
    assert.equal(current.status, "loaded");
    if (current.status !== "loaded") throw new Error("fixture unavailable");
    assert.deepEqual(current.head, {...f.projection, handoff_mode: "hard_lease"});
    assert.equal(applied.execution_authority_granted, false);
    const backup = await verifyAuthorityArchive(String(applied.backup_path));
    assert.equal(backup.commits, "1");
    assert.equal(backup.projection_sha256, sha256(f.projection));
    const restored = new FileAuthorityStore(join(f.input.runtime_root, "restore"), goal);
    await restoreAuthorityArchive(String(applied.backup_path), restored, backup.archive_sha256);
    assert.equal((await restored.loadAuthority()).status, "loaded");
    const restoredHead = await restored.loadAuthority();
    if (restoredHead.status === "loaded") assert.deepEqual(restoredHead.head, f.projection);
    assert.deepEqual((await restored.readReceipt("seed-policy")).status, "found");
    const later = await f.store.commitAuthority({expected_provider_revision: current.provider_revision,
      operation_id: "later-work", next_projection: {...current.head, later: {metadata: true}}, events: [], receipts: []});
    assert.equal(later.status, "applied");
    const after = await f.store.loadAuthority();
    assert.equal((await executeHandoffModeMigration(f.contender, f.request)).status, "replayed");
    assert.deepEqual(await f.store.loadAuthority(), after);
  });

  test(`${provider}: explicit single-instance soft migration preserves assignment without leases`, async t => {
    const f = await prepare(t, factory, "soft_claim");
    const applied = await executeHandoffModeMigration(f.store, f.request);
    assert.equal(applied.status, "applied", JSON.stringify(applied));
    const current = await f.store.loadAuthority();
    if (current.status !== "loaded") throw new Error("fixture unavailable");
    assert.deepEqual(current.head, {...f.projection, handoff_mode: "soft_claim"});
  });

  test(`${provider}: reviewed upgrade retains an active standalone execution and its exact fencing proof`, async t => {
    const f = await prepare(t, factory);
    const loaded = await f.store.loadAuthority();
    if (loaded.status !== "loaded") throw new Error("missing head");
    const source = structuredClone(loaded.head);
    const todo = (source.todos as JsonObject[]).find(row => row.status === "open" && row.claimed_by)!;
    const lease = (source.leases as JsonObject[]).find(row => row.todo_id === todo.todo_id)!;
    todo.required_write_scopes = [];
    lease.owner = todo.claimed_by;
    lease.status = "active";
    lease.write_scopes = ["src/**", "tests/**"];
    lease.expires_at = "2027-01-01T00:00:00Z";
    (source.todo_read_model as JsonObject).records_sha256 = sha256(source.todos);
    assert.equal((await f.store.commitAuthority({operation_id: "active-execution", expected_provider_revision: loaded.provider_revision,
      next_projection: source, events: [], receipts: []})).status, "applied");
    const input = {...f.input, plan: join(f.input.runtime_root, "active.json")};
    const plan = await planHandoffModeMigration(f.store, {...input, requested_mode: "hard_lease"});
    assert.equal(plan.status, "planned", JSON.stringify(plan));
    const result = await executeHandoffModeMigration(f.store, {...input, execute: true, plan_sha256: String(plan.plan_sha256)});
    assert.equal(result.status, "applied", JSON.stringify(result));
    const after = await f.contender.loadAuthority();
    if (after.status !== "loaded") throw new Error("missing head");
    assert.deepEqual(after.head, {...source, handoff_mode: "hard_lease"});
    const backup = await verifyAuthorityArchive(String(result.backup_path));
    assert.equal(backup.commits, "2");
  });

  for (const drift of ["digest", "registration", "head", "corrupt_backup"] as const) {
    test(`${provider}: reviewed ${drift} drift is rejected without a policy write`, async t => {
      const f = await prepare(t, factory);
      let request = f.request;
      if (drift === "digest") request = {...request, plan_sha256: "0".repeat(64)};
      if (drift === "registration") request = {...request, registered_agents: [...request.registered_agents, "new-agent"]};
      if (drift === "head") {
        const loaded = await f.store.loadAuthority();
        if (loaded.status !== "loaded") throw new Error("missing head");
        await f.contender.commitAuthority({expected_provider_revision: loaded.provider_revision,
          operation_id: "external-work", next_projection: {...loaded.head, external: true}, events: [], receipts: []});
      }
      if (drift === "corrupt_backup") await writeFile(`${f.input.plan}.backup.jsonl`, "corrupt\n");
      const before = await f.store.loadAuthority();
      assert.equal((await executeHandoffModeMigration(f.store, request)).status, "failed");
      assert.deepEqual(await f.store.loadAuthority(), before);
    });
  }

  test(`${provider}: commit contention never overwrites newer work, and lost response recovers the original migration`, async t => {
    const f = await prepare(t, factory);
    const raced = intercept(f.store, async commit => {
      const loaded = await f.contender.loadAuthority();
      if (loaded.status !== "loaded") throw new Error("missing head");
      await f.contender.commitAuthority({expected_provider_revision: loaded.provider_revision,
        operation_id: "race", next_projection: {...loaded.head, later: true}, events: [], receipts: []});
      return f.store.commitAuthority(commit);
    });
    assert.equal((await executeHandoffModeMigration(raced, f.request)).status, "conflict");
    const loaded = await f.store.loadAuthority();
    if (loaded.status !== "loaded") throw new Error("missing head");
    assert.equal(loaded.head.handoff_mode, "legacy");
    const input = {...f.input, plan: join(f.input.runtime_root, "fresh.json")};
    const plan = await planHandoffModeMigration(f.store, {...input, requested_mode: "hard_lease"});
    const request = {...input, execute: true, plan_sha256: String(plan.plan_sha256)};
    let attempts = 0;
    const lost = intercept(f.store, async commit => {
      attempts++;
      await f.store.commitAuthority(commit);
      throw new Error("response lost after commit");
    });
    assert.equal((await executeHandoffModeMigration(lost, request)).status, "recovered");
    assert.equal((await executeHandoffModeMigration(lost, request)).status, "replayed");
    assert.equal(attempts, 1);
  });
}
