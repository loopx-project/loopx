import assert from "node:assert/strict";
import {mkdtemp, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {exportAuthorityArchive, restoreAuthorityArchive} from "../../loopx/control_plane/coordination/authority_archive.ts";
import {auditAuthorityArchive} from "../../loopx/control_plane/coordination/authority_archive_audit.ts";
import {manageLocalAuthorityArchive} from "../../loopx/control_plane/coordination/local_authority_archive.ts";
import {productionScaleCoordinationFixture} from "./production_scale_coordination_fixture.ts";

function observe(store: AuthorityStore, overrides: Partial<AuthorityStore>): AuthorityStore {
  return {providerKind: store.providerKind, storeIdentity: () => store.storeIdentity(),
    loadAuthority: () => store.loadAuthority(), commitAuthority: c => store.commitAuthority(c),
    readReceipt: id => store.readReceipt(id), scanCommitted: (cursor, limit) => store.scanCommitted(cursor, limit),
    ...overrides};
}
async function seed(store: AuthorityStore, count = 35, divergent = false) {
  let previous: string | null = null;
  for (let i = 1; i <= count; i++) {
    const committed = await store.commitAuthority({expected_provider_revision: previous,
      operation_id: `operation-${i}`, events: [{kind: "observed", round: i}],
      next_projection: {goal_id: "goal", value: i},
      // A different old no-change receipt is invisible in the final head.
      receipts: [{changed: false, decision: divergent && i === 2 ? "other" : "same"}]});
    assert.equal(committed.status, "applied");
    if (committed.status === "applied") previous = committed.provider_revision;
  }
  return previous;
}

for (const provider of ["file", "sqlite"] as const) {
  test(`${provider}: audit is read-only, paged, checks every receipt; resume reuses prefix`, async () => {
    const root = await mkdtemp(join(tmpdir(), "archive-audit-"));
    try {
      const source = new SqliteAuthorityStore(join(root, "source"), "goal");
      await seed(source);
      const path = join(root, "archive");
      const archive = await exportAuthorityArchive(source, "goal", path);
      const target = provider === "file" ? new FileAuthorityStore(join(root, "target"), "goal")
        : new SqliteAuthorityStore(join(root, "target"), "goal");
      await restoreAuthorityArchive(path, target, archive.archive_sha256);
      const before = await target.loadAuthority();
      let scans = 0, receipts = 0;
      const readOnly = observe(target, {
        commitAuthority: async () => { throw new Error("audit must never write"); },
        scanCommitted: (after, limit) => { scans++; assert.ok(limit <= 16); return target.scanCommitted(after, limit); },
        readReceipt: id => { receipts++; return target.readReceipt(id); },
      });
      const report = await auditAuthorityArchive(path, readOnly, archive.archive_sha256);
      assert.equal(report.status, "matched");
      assert.equal(scans, 3); assert.equal(receipts, 35);
      scans = 0; receipts = 0;
      assert.equal((await restoreAuthorityArchive(path, readOnly, archive.archive_sha256)).status, "restored");
      assert.equal(scans, 3); assert.equal(receipts, 35);
      assert.deepEqual(await target.loadAuthority(), before);
      // Missing original lookup is not redeemed by identical head/scan data.
      const broken = observe(target, {readReceipt: id => id === "operation-2"
        ? Promise.resolve({status: "missing"}) : target.readReceipt(id)});
      const rejected = await auditAuthorityArchive(path, broken, archive.archive_sha256);
      assert.equal(rejected.status, "mismatch");
      if (rejected.status !== "matched") {
        assert.equal(rejected.reason_code, "archive_receipt_mismatch"); assert.equal(rejected.cursor, "2");
      }
      assert.ok(!JSON.stringify(rejected).includes("operation-2"));
    } finally { await rm(root, {recursive: true, force: true}); }
  });
}

test("matching head cannot hide divergent retained decisions; no suffix is written", async () => {
  const root = await mkdtemp(join(tmpdir(), "archive-audit-"));
  try {
    const source = new FileAuthorityStore(join(root, "source"), "goal");
    const target = new SqliteAuthorityStore(join(root, "target"), "goal");
    await seed(source, 5); await seed(target, 3, true);
    const path = join(root, "archive");
    const archive = await exportAuthorityArchive(source, "goal", path);
    const before = await target.loadAuthority();
    await assert.rejects(restoreAuthorityArchive(path, target, archive.archive_sha256), /transaction_mismatch/);
    assert.deepEqual(await target.loadAuthority(), before);
    await seed(new FileAuthorityStore(join(root, "divergent"), "goal"), 5, true);
    const other = new FileAuthorityStore(join(root, "divergent"), "goal", {existingOnly: true});
    const mismatch = await auditAuthorityArchive(path, other, archive.archive_sha256);
    assert.equal(mismatch.status, "mismatch");
    if (mismatch.status !== "matched") assert.equal(mismatch.reason_code, "archive_transaction_mismatch");
  } finally { await rm(root, {recursive: true, force: true}); }
});

for (const scope of ["exact", "retained_prefix"] as const) {
  test(`${scope}: concurrent append has explicit audit semantics`, async () => {
    const root = await mkdtemp(join(tmpdir(), "archive-audit-"));
    try {
      const store = new SqliteAuthorityStore(join(root, "store"), "goal");
      const previous = await seed(store, 3);
      const path = join(root, "archive");
      const archive = await exportAuthorityArchive(store, "goal", path);
      let appended = false;
      const target = observe(store, {scanCommitted: async (after, limit) => {
        if (!appended) {
          appended = true;
          assert.equal((await store.commitAuthority({expected_provider_revision: previous, operation_id: "later",
            events: [], receipts: [], next_projection: {goal_id: "goal", value: 4}})).status, "applied");
        }
        return store.scanCommitted(after, limit);
      }});
      const report = await auditAuthorityArchive(path, target, archive.archive_sha256, scope);
      assert.equal(report.status, scope === "exact" ? "mismatch" : "matched");
      if (report.status === "matched") assert.equal(report.compared_commits, "3");
      const exact = await auditAuthorityArchive(path, store, archive.archive_sha256);
      assert.equal(exact.status, "mismatch");
      if (exact.status !== "matched") assert.equal(exact.reason_code, "archive_target_has_newer_commits");
    } finally { await rm(root, {recursive: true, force: true}); }
  });
}

for (const fault of ["identity", "history", "receipt", "gap"] as const) {
  test(`audit reports ${fault} failure without authority writes`, async () => {
    const root = await mkdtemp(join(tmpdir(), "archive-audit-"));
    try {
      const store = new FileAuthorityStore(join(root, "store"), "goal");
      await seed(store, 3);
      const path = join(root, "archive");
      const archive = await exportAuthorityArchive(store, "goal", path);
      let identityReads = 0;
      const broken = observe(store, {
        commitAuthority: async () => { assert.fail("audit invoked a writer"); },
        storeIdentity: () => fault === "identity" && identityReads++ > 0
          ? Promise.resolve({status: "available", store_identity: "file:" + "f".repeat(32)}) : store.storeIdentity(),
        scanCommitted: async (after, limit) => {
          if (fault === "history") return {status: "unavailable", reason_code: "offline", reason: "private cause"};
          const page = await store.scanCommitted(after, limit);
          if (page.status === "page" && fault === "gap") page.next_cursor = "99";
          return page;
        },
        readReceipt: id => fault === "receipt"
          ? Promise.resolve({status: "unavailable", reason_code: "offline", reason: "private cause"}) : store.readReceipt(id),
      });
      const report = await auditAuthorityArchive(path, broken, archive.archive_sha256);
      assert.equal(report.status, ["receipt", "history"].includes(fault) ? "unavailable" : "mismatch");
      assert.ok(!JSON.stringify(report).includes("private cause"));
    } finally { await rm(root, {recursive: true, force: true}); }
  });
}

test("missing stores remain absent; admin rejects mismatched destination binding and digest", async () => {
  const root = await mkdtemp(join(tmpdir(), "archive-audit-"));
  try {
    const store = new FileAuthorityStore(join(root, "source"), "goal");
    await seed(store, 1);
    const archive = join(root, "archive");
    const summary = await exportAuthorityArchive(store, "goal", archive);
    const request = {schema_version: "loopx_authority_archive_admin_request_v0", action: "audit", goal_id: "goal",
      archive, archive_sha256: summary.archive_sha256, runtime_root: join(root, "absent")};
    assert.equal((await manageLocalAuthorityArchive(request)).status, "failed");
    await assert.rejects(readFile(join(root, "absent", "authority", "file-v0", "store-identity")), {code: "ENOENT"});
    const destination = join(root, "restored");
    assert.equal((await manageLocalAuthorityArchive({...request, action: "restore", destination,
      provider: "sqlite", execute: true})).status, "restored");
    const {runtime_root: _root, ...isolated} = request;
    assert.equal((await manageLocalAuthorityArchive({...isolated, destination})).status, "audited");
    const binding = join(destination, "restore-binding.json");
    const body = JSON.parse(await readFile(binding, "utf8"));
    await writeFile(binding, JSON.stringify({...body, goal_id: "other"}));
    assert.equal((await manageLocalAuthorityArchive({...isolated, destination})).status, "failed");
    await assert.rejects(auditAuthorityArchive(archive, store, "0".repeat(64)), /reviewed digest/);
  } finally { await rm(root, {recursive: true, force: true}); }
});

test("native and imported complete graphs audit all history across SQLite checkpoint boundaries", async () => {
  const root = await mkdtemp(join(tmpdir(), "archive-audit-"));
  try {
    for (const mode of ["native", "legacy"] as const) {
      const source = new SqliteAuthorityStore(join(root, mode, "source"), "goal");
      const fixture = productionScaleCoordinationFixture("goal", mode);
      let previous: string | null = null;
      for (let i = 1; i <= 66; i++) {
        const committed = await source.commitAuthority({expected_provider_revision: previous,
          operation_id: `step-${i}`, events: [{kind: "checkpoint_observation", round: i}],
          next_projection: {...fixture.projection, round: i}, receipts: [{changed: false, round: i}]});
        assert.equal(committed.status, "applied");
        if (committed.status === "applied") previous = committed.provider_revision;
      }
      const archive = join(root, mode, "archive");
      const summary = await exportAuthorityArchive(source, "goal", archive);
      // Use a reopened real SQLite store, including its retained receipt index.
      const reopened = new SqliteAuthorityStore(join(root, mode, "source"), "goal", {existingOnly: true});
      const proof = await auditAuthorityArchive(archive, reopened, summary.archive_sha256);
      assert.equal(proof.status, "matched");
      if (proof.status === "matched") assert.equal(proof.compared_commits, "66");
    }
  } finally { await rm(root, {recursive: true, force: true}); }
});
