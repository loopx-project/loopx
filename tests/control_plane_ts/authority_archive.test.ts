import type {AuthorityStore, AuthorityStoreCommit} from "../../loopx/control_plane/coordination/authority_store.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {productionScaleCoordinationFixture} from "./production_scale_coordination_fixture.ts";
import assert from "node:assert/strict";
import {mkdtemp, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {exportAuthorityArchive, verifyAuthorityArchive, restoreAuthorityArchive} from
  "../../loopx/control_plane/coordination/authority_archive.ts";

// Expectations come from the retained-journal contract: exact historical state,
// operation identities and receipts survive; physical revision tokens do not.
for (const sourceKind of ["file", "sqlite"] as const) {
  test(`${sourceKind}: complete archive roundtrip preserves every transaction`, async () => {
    const root = await mkdtemp(join(tmpdir(), "authority-archive-"));
    try {
      const source = sourceKind === "file" ? new FileAuthorityStore(join(root, "source"), "goal")
        : new SqliteAuthorityStore(join(root, "source"), "goal");
      let revision: string | null = null;
      for (let i = 1; i <= 7; i++) {
        const result = await source.commitAuthority({expected_provider_revision: revision,
          operation_id: `op-${i}`, events: [{kind: "change", i}],
          next_projection: {goal_id: "goal", i, archived: ["todo_old"], unicode: "复杂目标"},
          receipts: [{request_sha256: `request-${i}`, changed: i % 2 === 0}]});
        assert.equal(result.status, "applied");
        if (result.status === "applied") revision = result.provider_revision;
      }
      const archive = join(root, "backup.ndjson");
      const result = await exportAuthorityArchive(source, "goal", archive, {pageSize: 2});
      assert.equal(result.commits, "7");
      assert.deepEqual(await verifyAuthorityArchive(archive), result);
      for (const kind of ["file", "sqlite"] as const) {
        const target = kind === "file" ? new FileAuthorityStore(join(root, kind), "goal")
          : new SqliteAuthorityStore(join(root, kind), "goal");
        const restored = await restoreAuthorityArchive(archive, target, result.archive_sha256);
        assert.equal(restored.status, "restored");
        assert.equal(restored.commits, "7");
        const original = await source.scanCommitted(null, 10);
        const copy = await target.scanCommitted(null, 10);
        assert.equal(original.status, "page"); assert.equal(copy.status, "page");
        if (original.status !== "page" || copy.status !== "page") throw new Error("scan failed");
        assert.deepEqual(copy.transactions.map(({provider_revision, ...t}) => t),
          original.transactions.map(({provider_revision, ...t}) => t));
        assert.notEqual(copy.transactions[0].provider_revision, original.transactions[0].provider_revision);
        assert.equal((await restoreAuthorityArchive(archive, target, result.archive_sha256)).status, "restored");
      }
    } finally { await rm(root, {recursive: true, force: true}); }
  });
}

test("missing source, occupied output, truncation and changed payload fail closed", async () => {
  const root = await mkdtemp(join(tmpdir(), "authority-archive-"));
  try {
    const source = new FileAuthorityStore(join(root, "source"), "goal");
    const archive = join(root, "backup.ndjson");
    await assert.rejects(exportAuthorityArchive(source, "goal", archive));
    await source.commitAuthority({expected_provider_revision: null, operation_id: "op", events: [],
      next_projection: {goal_id: "goal", value: 1}, receipts: [{done: true}]});
    await exportAuthorityArchive(source, "goal", archive);
    const saved = await readFile(archive, "utf8");
    await assert.rejects(exportAuthorityArchive(source, "goal", archive));
    assert.equal(await readFile(archive, "utf8"), saved);
    for (const [name, content] of [["truncated", saved.slice(0, saved.lastIndexOf('\n', saved.length - 2) + 1)],
      ["changed", saved.replace('"done":true', '"done":false')], ["trailing", saved + '{}\n']]) {
      const path = join(root, name);
      await writeFile(path, content);
      await assert.rejects(verifyAuthorityArchive(path));
      const target = new FileAuthorityStore(join(root, name + "-target"), "goal");
      await assert.rejects(restoreAuthorityArchive(path, target, "0".repeat(64)));
      assert.equal((await target.loadAuthority()).status, "missing");
    }
  } finally { await rm(root, {recursive: true, force: true}); }
});

// These adapters interrupt actual durable stores at a named effect boundary.
// They do not replace persistence with an in-memory implementation.

function view(store: AuthorityStore, overrides: Partial<AuthorityStore>): AuthorityStore {
  return {providerKind: store.providerKind, storeIdentity: () => store.storeIdentity(),
    loadAuthority: () => store.loadAuthority(), commitAuthority: c => store.commitAuthority(c),
    readReceipt: id => store.readReceipt(id), scanCommitted: (cursor, limit) => store.scanCommitted(cursor, limit),
    ...overrides};
}
async function seed(store: AuthorityStore, count = 3) {
  let previous: string | null = null;
  for (let i = 1; i <= count; i++) {
    const row = await store.commitAuthority({expected_provider_revision: previous, operation_id: `op-${i}`,
      events: [{i}], receipts: [{decision: i}], next_projection: {goal_id: "goal", value: i}});
    assert.equal(row.status, "applied");
    if (row.status === "applied") previous = row.provider_revision;
  }
  return previous;
}

test("capture pins its original prefix while real source receives later commits", async () => {
  const root = await mkdtemp(join(tmpdir(), "authority-archive-"));
  try {
    const source = new SqliteAuthorityStore(join(root, "source"), "goal");
    const previous = await seed(source);
    let appended = false;
    const wrapped = view(source, {scanCommitted: async (after, limit) => {
      if (!appended) {
        appended = true;
        assert.equal((await source.commitAuthority({expected_provider_revision: previous, operation_id: "later",
          events: [], receipts: [], next_projection: {goal_id: "goal", value: 4}})).status, "applied");
      }
      return source.scanCommitted(after, limit);
    }});
    const report = await exportAuthorityArchive(wrapped, "goal", join(root, "archive"), {pageSize: 1});
    assert.equal(report.commits, "3");
    const head = await source.loadAuthority();
    assert.equal(head.status, "loaded"); if (head.status === "loaded") assert.equal(head.cursor, "4");
    assert.equal(report.projection_sha256, canonicalAuthoritySha256({goal_id: "goal", value: 3}));
  } finally { await rm(root, {recursive: true, force: true}); }
});

for (const fault of ["gap", "missing-page", "identity", "changed-head"] as const) {
  test(`capture rejects ${fault} without publishing an archive`, async () => {
    const root = await mkdtemp(join(tmpdir(), "authority-archive-"));
    try {
      const source = new FileAuthorityStore(join(root, "source"), "goal");
      await seed(source);
      let identityReads = 0;
      const wrapped = view(source, {
        storeIdentity: async () => fault === "identity" && identityReads++ > 0
          ? {status: "available", store_identity: "file:" + "f".repeat(32)} : source.storeIdentity(),
        scanCommitted: async (after, limit) => {
          const result = await source.scanCommitted(after, limit);
          if (result.status === "page") {
            if (fault === "gap") result.transactions[0].cursor = "9";
            if (fault === "missing-page") result.transactions = [];
            if (fault === "changed-head" && result.transactions.at(-1)?.cursor === "3") {
              result.transactions.at(-1)!.projection.value = 999;
            }
          }
          return result;
        },
      });
      const archive = join(root, "archive");
      await assert.rejects(exportAuthorityArchive(wrapped, "goal", archive, {pageSize: 1}));
      await assert.rejects(readFile(archive), {code: "ENOENT"});
      assert.equal((await source.readReceipt("op-3")).status, "found");
    } finally { await rm(root, {recursive: true, force: true}); }
  });
}

for (const kind of ["file", "sqlite"] as const) {
  test(`${kind}: interrupted restore resumes the exact retained prefix; lost ack recovers`, async () => {
    const root = await mkdtemp(join(tmpdir(), "authority-archive-"));
    try {
      const source = new FileAuthorityStore(join(root, "source"), "goal");
      await seed(source);
      const archive = join(root, "archive");
      const report = await exportAuthorityArchive(source, "goal", archive);
      const target = kind === "file" ? new FileAuthorityStore(join(root, "target"), "goal")
        : new SqliteAuthorityStore(join(root, "target"), "goal");
      let interrupted = false;
      const broken = view(target, {
        commitAuthority: async c => {
          const result = await target.commitAuthority(c);
          if (c.operation_id === "op-2") { interrupted = true; throw new Error("process response lost"); }
          return result;
        },
        scanCommitted: async (after, limit) => interrupted
          ? {status: "unavailable", reason_code: "stopped", reason: "stopped"} : target.scanCommitted(after, limit),
      });
      await assert.rejects(restoreAuthorityArchive(archive, broken, report.archive_sha256));
      const partial = await target.loadAuthority();
      assert.equal(partial.status, "loaded"); if (partial.status === "loaded") assert.equal(partial.cursor, "2");
      const resumed = view(target, {commitAuthority: async c => {
        await target.commitAuthority(c); throw new Error("lost ack with available readback");
      }});
      assert.equal((await restoreAuthorityArchive(archive, resumed, report.archive_sha256)).status, "restored");
      const end = await target.loadAuthority();
      assert.equal(end.status, "loaded"); if (end.status === "loaded") assert.equal(end.cursor, "3");
    } finally { await rm(root, {recursive: true, force: true}); }
  });
}

test("restore rejects occupied divergent or longer target and changed reviewed digest", async () => {
  const root = await mkdtemp(join(tmpdir(), "authority-archive-"));
  try {
    const source = new SqliteAuthorityStore(join(root, "source"), "goal");
    await seed(source);
    const archive = join(root, "archive");
    const report = await exportAuthorityArchive(source, "goal", archive);
    for (const mode of ["divergent", "longer", "digest", "source-itself"] as const) {
      const target = mode === "source-itself" ? source : new SqliteAuthorityStore(join(root, mode), "goal");
      if (mode === "longer") await seed(target, 4);
      if (mode === "divergent") await target.commitAuthority({expected_provider_revision: null, operation_id: "unrelated",
        events: [], receipts: [], next_projection: {goal_id: "goal"}});
      const before = await target.loadAuthority();
      await assert.rejects(restoreAuthorityArchive(archive, target, mode === "digest" ? "0".repeat(64) : report.archive_sha256));
      assert.deepEqual(await target.loadAuthority(), before);
    }
  } finally { await rm(root, {recursive: true, force: true}); }
});

// Re-sign corrupted records to prove semantic checks independently of checksums.
function resign(rows: Record<string, unknown>[]) {
  let previous: string | null = null;
  for (const row of rows) {
    delete row.sha256;
    if (row.kind !== "header") row.previous_sha256 = previous;
    row.sha256 = canonicalAuthoritySha256(row);
    previous = row.sha256 as string;
  }
  return rows.map(row => JSON.stringify(row)).join("\n") + "\n";
}
for (const corruption of ["order", "duplicate", "head", "goal", "unknown-field", "missing-transaction", "missing-seal"] as const) {
  test(`verified digests cannot hide ${corruption}`, async () => {
    const root = await mkdtemp(join(tmpdir(), "authority-archive-"));
    try {
      const source = new FileAuthorityStore(join(root, "source"), "goal");
      await seed(source);
      const archive = join(root, "archive");
      await exportAuthorityArchive(source, "goal", archive);
      const rows = (await readFile(archive, "utf8")).trim().split("\n").map(line => JSON.parse(line));
      if (corruption === "order") [rows[1], rows[2]] = [rows[2], rows[1]];
      if (corruption === "duplicate") rows[2].operation_id = rows[1].operation_id;
      if (corruption === "head") rows[0].provider_revision = "other";
      if (corruption === "goal") rows[0].goal_id = "other";
      if (corruption === "unknown-field") rows[1].ignored_payload = true;
      if (corruption === "missing-transaction") rows.splice(2, 1);
      if (corruption === "missing-seal") rows.pop();
      await writeFile(archive, resign(rows));
      await assert.rejects(verifyAuthorityArchive(archive));
    } finally { await rm(root, {recursive: true, force: true}); }
  });
}

for (const schema of ["native", "legacy"] as const) {
  test(`${schema}: mixed complete graph retains archived records, leases, ordering and unknown metadata`, async () => {
    const root = await mkdtemp(join(tmpdir(), "authority-archive-"));
    try {
      const source = new SqliteAuthorityStore(join(root, "source"), "goal");
      const fixture = productionScaleCoordinationFixture("goal", schema);
      const projection = {...fixture.projection, extra: JSON.parse('{"__proto__":{"keep":true},"":1}')};
      const first: AuthorityStoreCommit = {expected_provider_revision: null, operation_id: "import", events: [{kind: "import"}],
        next_projection: projection, receipts: [{kind: "reviewed-import", unchanged: false}]};
      const result = await source.commitAuthority(first);
      assert.equal(result.status, "applied");
      if (result.status !== "applied") throw new Error("seed failed");
      // A state-only copy would lose this first receipt and the exact older projection.
      assert.equal((await source.commitAuthority({...first, expected_provider_revision: result.provider_revision,
        operation_id: "observation", next_projection: {...projection, observed: true}, receipts: []})).status, "applied");
      const archive = join(root, "archive");
      const report = await exportAuthorityArchive(source, "goal", archive, {pageSize: 1});
      const target = new FileAuthorityStore(join(root, "target"), "goal");
      await restoreAuthorityArchive(archive, target, report.archive_sha256);
      const rows = await target.scanCommitted(null, 2);
      assert.equal(rows.status, "page");
      if (rows.status !== "page") throw new Error("readback failed");
      assert.deepEqual(rows.transactions[0].projection, projection);
      assert.deepEqual(rows.transactions[1].projection, {...projection, observed: true});
      assert.deepEqual(rows.transactions[0].receipts, first.receipts);
      // Delta archive should not retain the complete graph twice.
      assert.ok((await readFile(archive)).length < JSON.stringify(projection).length * 1.4);
    } finally { await rm(root, {recursive: true, force: true}); }
  });
}

test("SQLite retained history crosses checkpoint windows and preserves an old same-key receipt", async () => {
  const root = await mkdtemp(join(tmpdir(), "authority-archive-history-"));
  try {
    const source = new SqliteAuthorityStore(join(root, "source"), "goal");
    await seed(source, 70);
    const archive = join(root, "archive");
    const report = await exportAuthorityArchive(source, "goal", archive, {pageSize: 7});
    assert.equal(report.commits, "70");
    const target = new SqliteAuthorityStore(join(root, "target"), "goal");
    await restoreAuthorityArchive(archive, target, report.archive_sha256);
    const receipt = await target.readReceipt("op-1");
    assert.equal(receipt.status, "found");
    if (receipt.status === "found") assert.deepEqual(receipt.receipts, [{decision: 1}]);
    const page = await target.scanCommitted("63", 3);
    assert.equal(page.status, "page");
    if (page.status === "page") assert.deepEqual(page.transactions.map(row => row.projection.value), [64, 65, 66]);
  } finally { await rm(root, {recursive: true, force: true}); }
});

test("a changed archive during the second pass cannot claim a verified recovery", async () => {
  const root = await mkdtemp(join(tmpdir(), "authority-archive-change-"));
  try {
    const source = new FileAuthorityStore(join(root, "source"), "goal");
    await seed(source);
    const archive = join(root, "archive");
    const report = await exportAuthorityArchive(source, "goal", archive);
    const target = new SqliteAuthorityStore(join(root, "target"), "goal");
    const wrapped = view(target, {storeIdentity: async () => {
      // Change a syntactically valid archive after initial full verification.
      const rows = (await readFile(archive, "utf8")).trim().split("\n").map(line => JSON.parse(line));
      rows[1].receipts = [{changed: true}];
      await writeFile(archive, resign(rows));
      return target.storeIdentity();
    }});
    await assert.rejects(restoreAuthorityArchive(archive, wrapped, report.archive_sha256), /archive changed/);
    // It remains an isolated recovery store; no source transaction was rewritten.
    const receipt = await source.readReceipt("op-1");
    assert.equal(receipt.status, "found");
    if (receipt.status === "found") assert.deepEqual(receipt.receipts, [{decision: 1}]);
  } finally { await rm(root, {recursive: true, force: true}); }
});
