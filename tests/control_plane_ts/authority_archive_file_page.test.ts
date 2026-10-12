/** Real File archive IO: full logical parity, bounded publication and recovery. */
import assert from "node:assert/strict";
import {mkdtemp, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import type {AuthorityStore, AuthorityStoreCommittedTransaction} from "../../loopx/control_plane/coordination/authority_store.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {exportAuthorityArchive, restoreAuthorityArchive} from "../../loopx/control_plane/coordination/authority_archive.ts";
import {auditAuthorityArchive} from "../../loopx/control_plane/coordination/authority_archive_audit.ts";

class ObservedFile extends FileAuthorityStore {
  publications: number[] = [];
  fault: "before" | "after" | "readback" | null = null;
  private unreadable = false;
  protected override async replaceDurably(path: string, bytes: Uint8Array) {
    if (path !== this.path) return super.replaceDurably(path, bytes);
    if (this.fault === "before") { this.fault = null; throw new Error("before rename"); }
    await super.replaceDurably(path, bytes);
    this.publications.push(JSON.parse(Buffer.from(bytes).toString()).committed.length);
    if (this.fault === "after") { this.fault = null; throw new Error("after rename / directory sync"); }
    if (this.fault === "readback") { this.fault = null; this.unreadable = true; throw new Error("response lost"); }
  }
  override async scanCommitted(after: string | null, limit: number) {
    if (this.unreadable) return {status: "unavailable" as const, reason_code: "fixture_disconnected", reason: "readback unavailable"};
    return super.scanCommitted(after, limit);
  }
}

async function fixture(root: string, count: number) {
  const source = new SqliteAuthorityStore(join(root, "source"), "goal");
  let previous: string | null = null;
  for (let i = 1; i <= count; i++) {
    const row = await source.commitAuthority({expected_provider_revision: previous, operation_id: `step-${i}`,
      events: [{round: i}], receipts: [{changed: false, optional: null, epoch: i}],
      next_projection: {goal_id: "goal", todos: [{todo_id: "archived", archive_state: "archive", text: "完整原文",
        metadata: {enabled: false, maybe: null, empty: [], ...(i % 2 ? {removed: "preserved until removal"} : {})}}],
      leases: [{owner: "fixture-peer", lease_epoch: i, status: "released"}], round: i}});
    assert.equal(row.status, "applied");
    if (row.status === "applied") previous = row.provider_revision;
  }
  const path = join(root, "archive");
  return {source, path, summary: await exportAuthorityArchive(source, "goal", path)};
}
function ordinary(store: FileAuthorityStore): AuthorityStore {
  return {providerKind: "file", storeIdentity: () => store.storeIdentity(), loadAuthority: () => store.loadAuthority(),
    commitAuthority: input => store.commitAuthority(input), readReceipt: id => store.readReceipt(id),
    scanCommitted: (after, limit) => store.scanCommitted(after, limit)};
}
function semantic(rows: readonly AuthorityStoreCommittedTransaction[]) {
  return rows.map(({provider_revision: _, ...row}) => row);
}

test("File pages match ordinary commit history, every receipt and subsequent CAS across checkpoints", async () => {
  const root = await mkdtemp(join(tmpdir(), "file-archive-page-"));
  try {
    const {source, path, summary} = await fixture(root, 69);
    const original = await source.loadAuthority();
    const fast = new ObservedFile(join(root, "page"), "goal");
    const slow = new ObservedFile(join(root, "ordinary"), "goal");
    const restored = await restoreAuthorityArchive(path, fast, summary.archive_sha256);
    await restoreAuthorityArchive(path, ordinary(slow), summary.archive_sha256);
    assert.deepEqual(fast.publications, [16, 32, 48, 64, 69]);
    assert.equal(slow.publications.length, 69);
    const page = await fast.scanCommitted(null, 100), baseline = await slow.scanCommitted(null, 100);
    assert.equal(page.status, "page"); assert.equal(baseline.status, "page");
    if (page.status !== "page" || baseline.status !== "page") throw new Error("missing history");
    assert.deepEqual(semantic(page.transactions), semantic(baseline.transactions));
    for (const row of page.transactions) {
      const receipt = await fast.readReceipt(row.operation_id);
      assert.equal(receipt.status, "found");
      if (receipt.status === "found") {
        assert.deepEqual(receipt.receipts, row.receipts);
        assert.equal(receipt.provider_revision, row.provider_revision);
        assert.equal(receipt.cursor, row.cursor);
      }
      assert.equal((await fast.commitAuthority({expected_provider_revision: null,
        operation_id: row.operation_id, next_projection: row.projection, events: row.events, receipts: row.receipts})).status, "applied");
    }
    const last = page.transactions.at(-1)!;
    assert.equal((await fast.commitAuthority({expected_provider_revision: restored.target_provider_revision,
      operation_id: last.operation_id, events: last.events, receipts: last.receipts,
      next_projection: {...last.projection, unauthorized: true}})).status, "conflict");
    assert.equal((await fast.commitAuthority({expected_provider_revision: null, operation_id: "stale",
      events: [], receipts: [], next_projection: {goal_id: "goal"}})).status, "conflict");
    const reopened = new FileAuthorityStore(fast.directory, "goal", {existingOnly: true});
    assert.equal((await auditAuthorityArchive(path, reopened, summary.archive_sha256)).status, "matched");
    assert.equal((await reopened.commitAuthority({expected_provider_revision: restored.target_provider_revision,
      operation_id: "new-write", events: [], receipts: [{accepted: true}],
      next_projection: {...last.projection, round: 70}})).status, "applied");
    assert.equal((await auditAuthorityArchive(path, reopened, summary.archive_sha256, "retained_prefix")).status, "matched");
    await assert.rejects(restoreAuthorityArchive(path, reopened, summary.archive_sha256), /extra commits/);
    assert.deepEqual(await source.loadAuthority(), original);
  } finally { await rm(root, {recursive: true, force: true}); }
});

for (const fault of ["before", "after", "readback"] as const) {
  test(`File archive ${fault} publication failure never retries an unproven page`, async () => {
    const root = await mkdtemp(join(tmpdir(), "file-archive-fault-"));
    try {
      const {source, path, summary} = await fixture(root, 33);
      const original = await source.loadAuthority();
      const target = new ObservedFile(join(root, "target"), "goal"); target.fault = fault;
      if (fault === "after") {
        assert.equal((await restoreAuthorityArchive(path, target, summary.archive_sha256)).status, "restored");
        assert.deepEqual(target.publications, [16, 32, 33]);
      } else {
        await assert.rejects(restoreAuthorityArchive(path, target, summary.archive_sha256), /resume the same archive/);
        const observed = await target.loadAuthority();
        assert.equal(observed.status, fault === "before" ? "missing" : "loaded");
        if (observed.status === "loaded") assert.equal(observed.cursor, "16");
        const resumed = new ObservedFile(target.directory, "goal", {existingOnly: fault !== "before"});
        assert.equal((await restoreAuthorityArchive(path, resumed, summary.archive_sha256)).status, "restored");
        assert.deepEqual(resumed.publications, fault === "before" ? [16, 32, 33] : [32, 33]);
      }
      const reopened = new FileAuthorityStore(target.directory, "goal", {existingOnly: true});
      assert.equal((await auditAuthorityArchive(path, reopened, summary.archive_sha256)).status, "matched");
      assert.deepEqual(await source.loadAuthority(), original);
    } finally { await rm(root, {recursive: true, force: true}); }
  });
}

test("File page refuses foreign goal/lineage, old CAS, cursor gaps and duplicate operations without publication", async () => {
  const root = await mkdtemp(join(tmpdir(), "file-archive-refusal-"));
  try {
    const {source} = await fixture(root, 3);
    const scan = await source.scanCommitted(null, 3);
    if (scan.status !== "page") throw new Error("fixture missing");
    const target = new ObservedFile(join(root, "target"), "goal");
    const identity = await target.storeIdentity();
    if (identity.status !== "available") throw new Error("fixture identity missing");
    for (const kind of ["goal", "identity", "cas", "gap", "duplicate", "oversize"] as const) {
      const rows = structuredClone(scan.transactions);
      if (kind === "goal") rows[0].projection.goal_id = "foreign";
      if (kind === "gap") rows[1].cursor = "8";
      if (kind === "duplicate") rows[1].operation_id = rows[0].operation_id;
      if (kind === "oversize") while (rows.length <= 16) rows.push(rows[0]);
      const result = await target.appendArchivePage(kind === "cas" ? "old" : null,
        kind === "identity" ? "file:" + "f".repeat(32) : identity.store_identity, rows);
      assert.notEqual(result.status, "applied");
      assert.equal((await target.loadAuthority()).status, "missing");
      assert.deepEqual(target.publications, []);
    }
    // A changed identity is checked inside the write lock, independently of
    // the archive owner's earlier observation.
    await writeFile(target.identityPath, "file:" + "e".repeat(32));
    assert.equal((await target.appendArchivePage(null, identity.store_identity, scan.transactions)).status, "failed");
    await assert.rejects(readFile(target.path), {code: "ENOENT"});
  } finally { await rm(root, {recursive: true, force: true}); }
});
