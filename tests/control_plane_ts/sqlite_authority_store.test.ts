import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { dirname, join } from "node:path";
import { tmpdir } from "node:os";
import test from "node:test";
import { createRequire } from "node:module";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { SqliteAuthorityStore } from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import { AUTHORITY_STATE_CHECKPOINT_INTERVAL } from "../../loopx/control_plane/coordination/authority_state_log.ts";
import { canonicalAuthorityBytes } from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import { authorityStoreCommitFixture, registerAuthorityStoreConformance } from "./authority_store_conformance.ts";

async function fixture(t: test.TestContext) {
  const directory = await mkdtemp(join(tmpdir(), "sqlite-authority-"));
  t.after(() => rm(directory, {recursive: true, force: true}));
  return {store: new SqliteAuthorityStore(directory, "goal"), contender: new SqliteAuthorityStore(directory, "goal")};
}
registerAuthorityStoreConformance("SQLite", fixture);

test("SQLite commits and reads back every JSON object key", {timeout: 30000}, async t => {
  const {store} = await fixture(t);
  // The live writer must accept the same key space the migration has to carry:
  // a projection may key an object with `""` or `__proto__`, and both must
  // survive the stored delta, the head row and the retained history.
  const projections: Record<string, unknown>[] = [
    {},
    JSON.parse('{"": {"marker": "empty"}, "__proto__": {"marker": "proto"}}') as Record<string, unknown>,
    JSON.parse('{"nested": {"": [{"__proto__": "leaf"}]}}') as Record<string, unknown>,
    JSON.parse('{}') as Record<string, unknown>,
  ];
  let revision: string | null = null;
  for (const [index, projection] of projections.entries()) {
    const receipt = await store.commitAuthority({expected_provider_revision: revision,
      operation_id: `key-op-${String(index).padStart(3, "0")}`, next_projection: projection,
      events: [], receipts: []});
    assert.equal(receipt.status, "applied", JSON.stringify(receipt));
    revision = receipt.status === "applied" ? receipt.provider_revision : null;
  }
  const head = await store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status === "loaded") {
    assert.equal(canonicalAuthorityBytes(head.head).toString("utf8"),
      canonicalAuthorityBytes(projections[projections.length - 1]!).toString("utf8"));
  }
  assert.equal((await store.verifyAuthorityHistory()).status, "verified");
  const read: Record<string, unknown>[] = [];
  let after: string | null = null;
  for (;;) {
    const page = await store.scanCommitted(after, 4);
    assert.equal(page.status, "page", JSON.stringify(page));
    if (page.status !== "page" || page.transactions.length === 0) break;
    for (const transaction of page.transactions) {
      read.push(transaction.projection as Record<string, unknown>);
    }
    after = page.transactions[page.transactions.length - 1]!.cursor;
  }
  for (const [index, projection] of projections.entries()) {
    assert.equal(canonicalAuthorityBytes(read[index]!).toString("utf8"),
      canonicalAuthorityBytes(projection).toString("utf8"), `projection ${index}`);
  }
  assert.equal(({} as Record<string, unknown>).marker, undefined);
});

test("SQLite large unchanged projections retain independent receipts and reject a forged digest chain", async t => {
  const {store} = await fixture(t);
  const projection = {capacity_padding: "p".repeat(1024 * 1024), marker: "constant"};
  let revision: string | null = null;
  for (let index = 1; index <= 3; index++) {
    const result = await store.commitAuthority({expected_provider_revision: revision,
      operation_id: `large-${index}`, next_projection: projection,
      events: [{index}], receipts: [{operation_id: `large-${index}`, index}]});
    assert.equal(result.status, "applied");
    if (result.status !== "applied") return;
    revision = result.provider_revision;
  }
  const found = await store.readReceipt("large-3");
  assert.equal(found.status, "found");
  if (found.status === "found") assert.equal(found.receipts[0]?.index, 3);
  const page = await store.scanCommitted(null, 3);
  assert.equal(page.status, "page");
  if (page.status === "page") {
    assert.deepEqual(page.transactions.map(row => row.operation_id), ["large-1", "large-2", "large-3"]);
    (page.transactions[0]!.projection as {marker: string}).marker = "edited only in returned data";
    assert.equal((page.transactions[1]!.projection as {marker: string}).marker, "constant");
  }
  const {DatabaseSync} = createRequire(import.meta.url)("node:sqlite");
  const db = new DatabaseSync(store.path);
  try {
    const forged = "0".repeat(64);
    db.prepare("UPDATE commits SET state_digest=? WHERE cursor=2").run(forged);
    db.prepare("UPDATE commits SET parent_state_digest=? WHERE cursor=3").run(forged);
  } finally { db.close(); }
  // The current head can still load; a historical read must prove the empty
  // delta's claimed state digest rather than trusting the forged chain.
  assert.equal((await store.loadAuthority()).status, "loaded");
  for (const result of [await store.readReceipt("large-3"), await store.scanCommitted(null, 3)]) {
    assert.equal(result.status, "failed");
    if (result.status === "failed") assert.equal(result.reason_code, "provider_protocol_violation");
  }
});

test("SQLite first empty projection still derives its root digest", async t => {
  const {store} = await fixture(t);
  const committed = await store.commitAuthority({expected_provider_revision: null,
    operation_id: "empty-root", next_projection: {}, events: [], receipts: [{operation_id: "empty-root"}]});
  assert.equal(committed.status, "applied");
  assert.equal((await store.readReceipt("empty-root")).status, "found");
});

test("SQLite head continuity is independent of retained history", {timeout: 30000}, async t => {
  const {store} = await fixture(t);
  assert.equal((await store.storeIdentity()).status, "available");
  const {DatabaseSync} = createRequire(import.meta.url)("node:sqlite");
  const prepare = DatabaseSync.prototype.prepare;
  let retainedRowsRead = 0;
  // Count every retained transaction row the hot path materializes, so the
  // bound is measured on the production entrypoint instead of asserted from
  // an EXPLAIN plan that a later rewrite could satisfy by another route.
  DatabaseSync.prototype.prepare = function(this: import("node:sqlite").DatabaseSync, sql: string) {
    const statement = prepare.call(this, sql);
    if (!/FROM commits\b/i.test(sql)) return statement;
    return new Proxy(statement, {
      get(target, property) {
        const value = Reflect.get(target, property);
        if (typeof value !== "function") return value;
        if (property !== "all" && property !== "get") return value.bind(target);
        return (...args: unknown[]) => {
          const result = value.apply(target, args);
          retainedRowsRead += Array.isArray(result) ? result.length : result === undefined ? 0 : 1;
          return result;
        };
      },
    });
  };
  let revision: string | null = null;
  try {
    for (let i = 1; i <= AUTHORITY_STATE_CHECKPOINT_INTERVAL * 2; i++) {
      const result = await store.commitAuthority(authorityStoreCommitFixture(revision, `count-${i}`, i, i));
      assert.equal(result.status, "applied"); if (result.status !== "applied") return;
      revision = result.provider_revision;
    }
    retainedRowsRead = 0;
    const loaded = await store.loadAuthority();
    assert.equal(loaded.status, "loaded");
    if (loaded.status === "loaded") assert.equal(loaded.cursor, String(AUTHORITY_STATE_CHECKPOINT_INTERVAL * 2));
    // The live head is proven from its own row, its retained transaction and
    // the cursor bounds, so a window replay is not part of a head read.
    assert.ok(retainedRowsRead <= 3, `head continuity read ${retainedRowsRead} retained rows`);
  } finally { DatabaseSync.prototype.prepare = prepare; }
  const db = new DatabaseSync(store.path);
  try {
    const head = db.prepare("SELECT CAST(cursor AS TEXT) AS cursor FROM head WHERE singleton = 1").get() as {cursor: string};
    // The schema already refuses a head that names no retained row, so the
    // forged head is written with foreign keys suspended to prove the read
    // path rejects it on its own evidence.
    db.exec("PRAGMA foreign_keys=OFF; UPDATE head SET cursor=9223372036854775807");
    const forged = await store.loadAuthority();
    assert.equal(forged.status, "failed");
    if (forged.status === "failed") assert.equal(forged.reason_code, "provider_protocol_violation");
    db.prepare("UPDATE head SET cursor=? WHERE singleton = 1").run(head.cursor);
    db.exec("PRAGMA foreign_keys=ON");
    assert.equal((await store.loadAuthority()).status, "loaded");
    const emptyDelta = '{"schema_version":"authority_state_delta_v0","operations":[]}';
    const storedDelta = (cursor: number) =>
      (db.prepare("SELECT delta FROM commits WHERE cursor = ?").get(cursor) as {delta: string}).delta;
    const rewriteDelta = (cursor: number, delta: string) =>
      db.prepare("UPDATE commits SET delta=? WHERE cursor=?").run(delta, cursor);
    // One delta inside the verified span is proved by every read that returns
    // a later row, because the chain resumes from the window's checkpoint.
    const third = storedDelta(3);
    rewriteDelta(3, emptyDelta);
    const brokenChain = await store.readReceipt("count-4");
    assert.equal(brokenChain.status, "failed");
    rewriteDelta(3, third);
    // A window anchor resumes from its own checkpoint, so that one row's delta
    // is proven by the explicit linear archive audit instead of the hot path.
    const anchor = AUTHORITY_STATE_CHECKPOINT_INTERVAL + 1;
    const anchorDelta = storedDelta(anchor);
    rewriteDelta(anchor, emptyDelta);
    assert.equal((await store.loadAuthority()).status, "loaded");
    assert.equal((await store.readReceipt("count-1")).status, "found");
    const tampered = await store.verifyAuthorityHistory();
    assert.equal(tampered.status, "failed");
    if (tampered.status === "failed") assert.equal(tampered.reason_code, "provider_protocol_violation");
    rewriteDelta(anchor, anchorDelta);
    const audited = await store.verifyAuthorityHistory();
    assert.equal(audited.status, "verified");
    if (audited.status === "verified") {
      assert.equal(audited.commits, AUTHORITY_STATE_CHECKPOINT_INTERVAL * 2);
      assert.equal(audited.checkpoints, 2);
    }
    // A gap inside the live window fails closed on the next read, and the
    // linear audit cannot skip the missing parent either.
    db.exec("DELETE FROM commits WHERE cursor=66");
    const gapped = await store.loadAuthority();
    assert.equal(gapped.status, "failed");
    if (gapped.status === "failed") assert.equal(gapped.reason_code, "provider_protocol_violation");
    const gapAudit = await store.verifyAuthorityHistory();
    assert.equal(gapAudit.status, "failed");
  } finally { db.close(); }
});

for (const fault of ["crash-before", "crash-after", "capacity-full"]) {
  test(`SQLite real-process ${fault} preserves the exact committed state and proof`, {timeout: 30000}, async t => {
    const {store} = await fixture(t);
    const initial = authorityStoreCommitFixture(null, "seed", 1, 1);
    const seeded = await store.commitAuthority(initial);
    assert.equal(seeded.status, "applied"); if (seeded.status !== "applied") return;
    const child = spawnSync(process.execPath, ["--no-warnings", "--experimental-sqlite", "--experimental-strip-types",
      fileURLToPath(new URL("./sqlite_authority_process.ts", import.meta.url)), dirname(store.path), fault, seeded.provider_revision],
    {input: "go", encoding: "utf8", timeout: 15000});
    if (fault.startsWith("crash-")) {
      assert.equal(child.stdout.trim(), "ready");
      assert.notEqual(child.status, 0);
      if (process.platform !== "win32") assert.equal(child.signal, "SIGKILL");
    } else {
      assert.equal(child.status, 0, child.stderr);
      assert.equal(JSON.parse(child.stdout.split("\n")[1]!).status, "failed");
    }
    const applied = fault === "crash-after";
    const attempted = authorityStoreCommitFixture(seeded.provider_revision, fault, 1, 1);
    const expected = applied ? [initial, attempted] : [initial];
    const head = await store.loadAuthority(); assert.equal(head.status, "loaded");
    if (head.status !== "loaded") return;
    assert.equal(head.cursor, String(expected.length));
    assert.deepEqual(head.head, expected.at(-1)!.next_projection);
    const receipt = await store.readReceipt(fault);
    assert.equal(receipt.status, applied ? "found" : "missing");
    if (receipt.status === "found") assert.deepEqual(receipt.receipts, attempted.receipts);
    const scan = await store.scanCommitted(null, 10); assert.equal(scan.status, "page");
    if (scan.status !== "page") return;
    assert.deepEqual(scan.transactions.map(row => ({operation_id: row.operation_id, projection: row.projection,
      events: row.events, receipts: row.receipts})), expected.map(input => ({operation_id: input.operation_id,
      projection: input.next_projection, events: input.events, receipts: input.receipts})));
  });
}

for (const changedCursor of [1, 2]) {
  test(`SQLite validates historical receipt and scan lookahead at cursor ${changedCursor}`, async t => {
    const {store} = await fixture(t);
    let revision: string | null = null;
    for (let i = 1; i <= 3; i++) {
      const result = await store.commitAuthority(authorityStoreCommitFixture(revision, `op-${i}`, i, i));
      assert.equal(result.status, "applied"); if (result.status !== "applied") return;
      revision = result.provider_revision;
    }
    const {DatabaseSync} = createRequire(import.meta.url)("node:sqlite");
    const db = new DatabaseSync(store.path);
    db.prepare("UPDATE commits SET receipts=? WHERE cursor=?").run(JSON.stringify([{operation_id: "forged"}]), changedCursor);
    db.close();
    // The current row remains valid. Both returned historical rows and the
    // extra row used to decide has_more must independently verify their digest.
    for (const result of [await store.readReceipt(`op-${changedCursor}`), await store.scanCommitted(null, 1)]) {
      assert.equal(result.status, "failed");
      if (result.status === "failed") assert.equal(result.reason_code, "provider_protocol_violation");
    }
  });
}

for (const corruption of ["state_digest", "parent_state_digest", "head_rollback", "gap",
  "receipt", "event", "missing_head", "invalid_json"] as const) {
  test(`SQLite rejects readable ${corruption} corruption on the live path without side effects`, async t => {
    const {store} = await fixture(t);
    let revision: string | null = null;
    for (let i = 1; i <= 3; i++) {
      const result = await store.commitAuthority(authorityStoreCommitFixture(revision, `op-${i}`, i, i));
      assert.equal(result.status, "applied"); if (result.status !== "applied") return;
      revision = result.provider_revision;
    }
    const {DatabaseSync} = createRequire(import.meta.url)("node:sqlite");
    const db = new DatabaseSync(store.path);
    // Retained transactions no longer carry a second copy of the projection.
    // Their proof is the exact delta, its state digest, and the parent lineage.
    if (corruption === "state_digest") db.prepare("UPDATE commits SET state_digest=? WHERE cursor=3").run("0".repeat(64));
    if (corruption === "parent_state_digest") db.prepare("UPDATE commits SET parent_state_digest=? WHERE cursor=3").run("1".repeat(64));
    if (corruption === "receipt") db.exec("UPDATE commits SET receipts='[{\"operation_id\":\"forged\"}]' WHERE cursor=3");
    if (corruption === "event") db.exec("UPDATE commits SET events='[{\"type\":\"forged\"}]' WHERE cursor=3");
    if (corruption === "head_rollback") db.exec("UPDATE head SET cursor=1");
    if (corruption === "gap") db.exec("DELETE FROM commits WHERE cursor=2");
    if (corruption === "missing_head") db.exec("DELETE FROM head");
    if (corruption === "invalid_json") db.exec("UPDATE commits SET delta='{' WHERE cursor=3");
    db.close();
    const snapshot = () => {
      const reader = new DatabaseSync(store.path, {readOnly: true});
      try { return {head: reader.prepare("SELECT * FROM head").all(), commits: reader.prepare("SELECT * FROM commits ORDER BY cursor").all()}; }
      finally { reader.close(); }
    };
    const before = snapshot();
    for (const result of [await store.loadAuthority(), await store.readReceipt("op-1"),
      await store.scanCommitted(null, 1),
      await store.commitAuthority(authorityStoreCommitFixture(revision, "after-corruption", 4, 4))]) {
      assert.equal(result.status, "failed", JSON.stringify(result));
      if (result.status === "failed") assert.equal(result.reason_code, "provider_protocol_violation");
    }
    assert.deepEqual(snapshot(), before);
  });
}

/**
 * Storage that the live head never reads.
 *
 * A window anchor resumes from its own checkpoint and the live head is proven
 * from its own evidence, so these two shapes are not part of the live read
 * path. They must still be refused by every read that materializes the
 * corrupted span and by the linear archive audit, and the store must never
 * return a projection that the corrupted storage did not produce.
 */
for (const corruption of ["delta", "checkpoint_projection"] as const) {
  test(`SQLite refuses ${corruption} corruption when retained history is materialized`, async t => {
    const {store} = await fixture(t);
    let revision: string | null = null;
    for (let i = 1; i <= 3; i++) {
      const result = await store.commitAuthority(authorityStoreCommitFixture(revision, `op-${i}`, i, i));
      assert.equal(result.status, "applied"); if (result.status !== "applied") return;
      revision = result.provider_revision;
    }
    const {DatabaseSync} = createRequire(import.meta.url)("node:sqlite");
    const db = new DatabaseSync(store.path);
    if (corruption === "delta") db.prepare("UPDATE commits SET delta=? WHERE cursor=3")
      .run('{"schema_version":"authority_state_delta_v0","operations":[]}');
    if (corruption === "checkpoint_projection") db.prepare("UPDATE checkpoints SET projection=? WHERE cursor=1")
      .run(JSON.stringify({authority_revision: 999}));
    db.close();
    const snapshot = () => {
      const reader = new DatabaseSync(store.path, {readOnly: true});
      try { return {head: reader.prepare("SELECT * FROM head").all(), commits: reader.prepare("SELECT * FROM commits ORDER BY cursor").all()}; }
      finally { reader.close(); }
    };
    const before = snapshot();
    const head = await store.loadAuthority();
    assert.equal(head.status, "loaded", JSON.stringify(head));
    if (head.status === "loaded") {
      assert.equal(head.cursor, "3");
      assert.deepEqual(head.head, authorityStoreCommitFixture(revision, "op-3", 3, 3).next_projection);
    }
    for (const result of [await store.readReceipt("op-3"), await store.scanCommitted(null, 10),
      await store.verifyAuthorityHistory()]) {
      assert.equal(result.status, "failed", JSON.stringify(result));
      if (result.status === "failed") assert.equal(result.reason_code, "provider_protocol_violation");
    }
    assert.deepEqual(snapshot(), before);
  });
}

test("SQLite reopens with stable identity and rejects unknown schema", async t => {
  const {store, contender} = await fixture(t);
  assert.deepEqual(await store.storeIdentity(), await contender.storeIdentity());
  const first = await store.commitAuthority(authorityStoreCommitFixture(null, "first", 1, 1));
  assert.equal(first.status, "applied");
  assert.deepEqual(await store.loadAuthority(), await contender.loadAuthority());
  const {DatabaseSync} = createRequire(import.meta.url)("node:sqlite");
  const db = new DatabaseSync(store.path);
  db.exec("PRAGMA user_version=99"); db.close();
  assert.equal((await contender.loadAuthority()).status, "failed");
  assert.equal((await contender.commitAuthority(authorityStoreCommitFixture(null, "bad", 2, 2))).status, "failed");
});

test("SQLite interrupted head publication rolls back receipt and outbox", async t => {
  const {store} = await fixture(t);
  const first = await store.commitAuthority(authorityStoreCommitFixture(null, "first", 1, 1));
  assert.equal(first.status, "applied"); if (first.status !== "applied") return;
  const before = await store.loadAuthority();
  const {DatabaseSync} = createRequire(import.meta.url)("node:sqlite");
  const db = new DatabaseSync(store.path);
  db.exec("CREATE TRIGGER interrupt_head BEFORE UPDATE ON head BEGIN SELECT RAISE(ABORT, 'interrupted'); END");
  db.close();
  assert.equal((await store.commitAuthority(authorityStoreCommitFixture(first.provider_revision, "second", 2, 2))).status, "failed");
  assert.deepEqual(await store.loadAuthority(), before);
  assert.deepEqual(await store.readReceipt("second"), {status: "missing"});
  const page = await store.scanCommitted(null, 10);
  assert.equal(page.status, "page");
  if (page.status === "page") assert.deepEqual(page.transactions.map(row => row.operation_id), ["first"]);
});

test("SQLite real processes serialize CAS and preserve a lost-response receipt", {timeout: 30000}, async t => {
  const {store} = await fixture(t);
  await store.storeIdentity();
  const directory = dirname(store.path);
  const start = (operation: string, revision = "null") => {
    const child = spawn(process.execPath, ["--no-warnings", "--experimental-sqlite", "--experimental-strip-types",
      fileURLToPath(new URL("./sqlite_authority_process.ts", import.meta.url)), directory, operation, revision],
    {stdio: ["pipe", "pipe", "pipe"]});
    let output = "", error = "";
    let ready: () => void = () => {};
    const readyPromise = new Promise<void>(resolve => {ready = resolve;});
    child.stdout.on("data", data => {output += String(data); if (output.includes("ready\n")) ready();});
    child.stderr.on("data", data => {error += String(data);});
    const done = new Promise<{code: number | null; output: string}>(resolve => child.on("close", code => {
      assert.equal(error, ""); resolve({code, output});
    }));
    t.after(() => child.kill());
    return {child, readyPromise, done};
  };
  const a = start("process-a"), b = start("process-b");
  await Promise.all([a.readyPromise, b.readyPromise]);
  a.child.stdin.end("go"); b.child.stdin.end("go");
  const outcomes = await Promise.all([a.done, b.done]);
  assert.deepEqual(outcomes.map(result => JSON.parse(result.output.split("\n")[1]!).status).sort(), ["applied", "conflict"]);
  const head = await store.loadAuthority(); assert.equal(head.status, "loaded");
  if (head.status !== "loaded") return;
  const lost = start("lost-response", head.provider_revision);
  await lost.readyPromise; lost.child.stdin.end("go");
  assert.equal((await lost.done).code, 23);
  assert.equal((await store.readReceipt("lost-response")).status, "found");
  const reopened = await store.loadAuthority();
  assert.equal(reopened.status, "loaded");
  if (reopened.status === "loaded") assert.equal(reopened.cursor, "2");
});
