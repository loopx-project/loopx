import assert from "node:assert/strict";
import {execFile} from "node:child_process";
import {promisify} from "node:util";
import {createHash} from "node:crypto";
import {mkdtemp, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import type {AuthorityStoreCommittedTransaction} from "../../loopx/control_plane/coordination/authority_store.ts";
import {migrateFileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_migration.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {canonicalAuthorityBytes} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {authorityStoreCommitFixture as commit} from "./authority_store_conformance.ts";
import {productionScaleHistoryProjection} from "./production_scale_coordination_fixture.ts";

const GOAL = "goal-a";
async function fixture(t: test.TestContext) {
  const directory = await mkdtemp(join(tmpdir(), "file-state-log-"));
  t.after(() => rm(directory, {recursive: true, force: true}));
  return new FileAuthorityStore(directory, GOAL);
}

/** Independent pre-upgrade wire writer: preserves the documented File revision
 * formula, without calling the candidate append, delta or reconstruction code. */
async function legacyHistory(store: FileAuthorityStore, projections: JsonObject[]) {
  const identity = await store.storeIdentity();
  assert.equal(identity.status, "available");
  if (identity.status !== "available") throw new Error("identity unavailable");
  let revision: string | null = null;
  const committed: AuthorityStoreCommittedTransaction[] = [];
  for (const [i, projection] of projections.entries()) {
    const transaction = {cursor: String(i + 1), operation_id: `operation-${i + 1}`,
      events: [{kind: "observed", sequence: i}], receipts: [{original: i}], projection};
    const digest = createHash("sha256").update(canonicalAuthorityBytes({goal_id: GOAL,
      store_identity: identity.store_identity, previous_provider_revision: revision, transaction})).digest("hex").slice(0, 24);
    revision = `file:${i + 1}:${digest}`;
    committed.push({...transaction, provider_revision: revision});
  }
  const document = {schema_version: "loopx_file_authority_store_v0", goal_id: GOAL,
    store_identity: identity.store_identity, head: projections.at(-1)!, cursor: String(projections.length),
    provider_revision: revision!, committed};
  const bytes = canonicalAuthorityBytes(document);
  await writeFile(store.path, bytes);
  return {document, bytes};
}

function projection(i: number): JsonObject {
  // Array insertion/removal/reorder, deleted object members and JSON keys that
  // ordinary property assignment mishandles must survive historical replay.
  const special = JSON.parse('{"__proto__":{"stored":true},"":42}');
  return {authority_revision: i, special, nested: i % 2 ? {retained: i} : {removed: true},
    values: i % 3 ? [i, "unchanged", {value: i}] : [{value: i}, "unchanged"],
    stable: "retained-".repeat(1000)};
}

test("v0 is rejected until explicit backed-up upgrade; all original revisions and receipts survive", async t => {
  const store = await fixture(t);
  const {document, bytes} = await legacyHistory(store, Array.from({length: 66}, (_, i) => projection(i)));
  for (const response of [await store.loadAuthority(), await store.scanCommitted(null, 100),
    await store.readReceipt("operation-1"), await store.commitAuthority(commit(document.provider_revision, "blocked", 67, 1))]) {
    assert.equal(response.status, "failed");
  }
  assert.deepEqual(await readFile(store.path), bytes);
  const preview = await migrateFileAuthorityStore(store.directory, GOAL);
  assert.equal(preview.status, "planned");
  assert.deepEqual(await readFile(store.path), bytes);
  const upgrade = await migrateFileAuthorityStore(store.directory, GOAL, true);
  assert.equal(upgrade.status, "migrated");
  assert.deepEqual(await readFile(join(String(upgrade.backup_directory), "source.json")), bytes);
  assert.deepEqual(await store.loadAuthority(), {status: "loaded", head: document.head,
    cursor: document.cursor, provider_revision: document.provider_revision});
  const next = commit(document.provider_revision, "after-upgrade", 67, 1);
  const result = await store.commitAuthority(next);
  assert.equal(result.status, "applied");
  const stored = JSON.parse(await readFile(store.path, "utf8"));
  assert.equal(stored.schema_version, "loopx_file_authority_store_v1");
  assert.equal(stored.store_identity, document.store_identity);
  assert.equal(stored.cursor, "67");
  assert.deepEqual(stored.committed.flatMap((row: any, i: number) => row.state.kind === "checkpoint" ? [i + 1] : []), [1, 65]);
  // Force cold verification instead of trusting the writer's verified cache.
  await writeFile(store.path, (await readFile(store.path, "utf8")) + "\n");
  const reopened = new FileAuthorityStore(store.directory, GOAL, {existingOnly: true});
  const all = await reopened.scanCommitted(null, 100);
  assert.equal(all.status, "page");
  if (all.status !== "page") return;
  assert.deepEqual(all.transactions.slice(0, 66), document.committed);
  assert.deepEqual(all.transactions[66]!.projection, next.next_projection);
  assert.equal(all.has_more, false);
  for (const row of document.committed) {
    assert.deepEqual(await reopened.readReceipt(row.operation_id), {status: "found", cursor: row.cursor,
      provider_revision: row.provider_revision, receipts: row.receipts});
  }
  for (const after of ["1", "62", "63", "64", "65", "66", "67"]) {
    const page = await reopened.scanCommitted(after, 2);
    assert.equal(page.status, "page");
    if (page.status === "page") assert.deepEqual(page.transactions, all.transactions.slice(Number(after), Number(after) + 2));
  }
  all.transactions[0]!.projection.stable = "mutated caller copy";
  const readAgain = await reopened.scanCommitted(null, 1);
  assert.equal(readAgain.status, "page");
  if (readAgain.status === "page") assert.deepEqual(readAgain.transactions[0], document.committed[0]);
});

test("cold compact reads reject broken historical state even when head and requested receipt are unchanged", async t => {
  const store = await fixture(t);
  let revision: string | null = null;
  for (let i = 0; i < 66; i++) {
    const result = await store.commitAuthority({...commit(revision, `operation-${i}`, i, 1), next_projection: projection(i)});
    assert.equal(result.status, "applied");
    if (result.status === "applied") revision = result.provider_revision;
  }
  const original = JSON.parse(await readFile(store.path, "utf8"));
  const mutations: [string, (value: any) => void][] = [
    ["old delta", d => {d.committed[1].state.delta.operations = [];}],
    ["old receipt", d => {d.committed[0].receipts = [{forged: true}];}],
    ["checkpoint", d => {d.committed[64].state.projection.stable = "forged";}],
    ["missing checkpoint", d => {d.committed[64].state = d.committed[63].state;}],
    ["unexpected state key", d => {d.committed[1].state.extra = true;}],
    ["missing middle", d => {d.committed.splice(30, 1);}],
    ["duplicate operation", d => {d.committed[1].operation_id = d.committed[0].operation_id;}],
    ["unsupported schema", d => {d.schema_version = "unknown";}],
  ];
  for (const [label, mutate] of mutations) {
    const broken = structuredClone(original); mutate(broken);
    const bytes = JSON.stringify(broken);
    await writeFile(store.path, bytes);
    for (const result of [await store.loadAuthority(), await store.readReceipt("operation-65"),
      await store.scanCommitted("64", 2), await store.commitAuthority(commit(revision, "after-corruption", 67, 1))]) {
      assert.equal(result.status, "failed", label);
      if (result.status === "failed") assert.equal(result.reason_code, "provider_protocol_violation", label);
    }
    assert.equal(await readFile(store.path, "utf8"), bytes);
  }
});

test("upgrade interruption before/after rename resumes without a new logical commit", async t => {
  const base = await fixture(t);
  const {document, bytes} = await legacyHistory(base, [projection(0), projection(1)]);
  await assert.rejects(migrateFileAuthorityStore(base.directory, GOAL, true, {
    beforePublish: async () => {throw new Error("before rename");},
  }), /before rename/);
  assert.deepEqual(await readFile(base.path), bytes);
  await assert.rejects(migrateFileAuthorityStore(base.directory, GOAL, true, {
    afterPublish: async () => {throw new Error("after rename");},
  }), /after rename/);
  assert.equal((await migrateFileAuthorityStore(base.directory, GOAL, true)).status, "already_current");
  assert.equal((await base.readReceipt("operation-1")).status, "found");
  const history = await base.scanCommitted(null, 10);
  assert.equal(history.status, "page");
  if (history.status === "page") assert.deepEqual(history.transactions, document.committed);
  const archived = await base.loadAuthority();
  assert.equal(archived.status, "loaded");
  if (archived.status !== "loaded") return;
  assert.equal((await base.archiveAuthorityDocument(archived.provider_revision, "archive")).status, "applied");
  assert.equal((await base.archiveAuthorityDocument(archived.provider_revision, "archive")).status, "replayed");
});

test("production-shaped retained history shrinks without changing mixed Todo/lease facts", async t => {
  const store = await fixture(t);
  const source = productionScaleHistoryProjection(GOAL, "native").projection;
  const expected = Array.from({length: 70}, (_, i) => ({...source, observation_sequence: i}));
  const {document, bytes} = await legacyHistory(store, expected);
  await migrateFileAuthorityStore(store.directory, GOAL, true);
  const result = await store.commitAuthority({...commit(document.provider_revision, "next-observation", 71, 1),
    next_projection: {...source, observation_sequence: 70}});
  assert.equal(result.status, "applied");
  const compactBytes = await readFile(store.path);
  // A structural budget: this workload changes one scalar in a large live
  // projection, so two checkpoints plus head must not retain seventy copies.
  assert.ok(compactBytes.length < bytes.length / 8, `${compactBytes.length} vs ${bytes.length}`);
  await writeFile(store.path, Buffer.concat([compactBytes, Buffer.from("\n")]));
  const history = await store.scanCommitted(null, 100);
  assert.equal(history.status, "page");
  if (history.status === "page") {
    assert.deepEqual(history.transactions.slice(0, 70), document.committed);
    assert.deepEqual(history.transactions[70]!.projection, {...source, observation_sequence: 70});
  }
});


test("competing upgrade processes preserve one lineage and one backup", async t => {
  const store = await fixture(t);
  const {document} = await legacyHistory(store, [projection(0), projection(1)]);
  const module = new URL("../../loopx/control_plane/coordination/file_authority_migration.ts", import.meta.url).href;
  const script = `import {migrateFileAuthorityStore} from ${JSON.stringify(module)};
    console.log(JSON.stringify(await migrateFileAuthorityStore(${JSON.stringify(store.directory)}, "goal-a", true)));`;
  const commands = [0, 1].map(() => promisify(execFile)(process.execPath,
    ["--no-warnings", "--experimental-strip-types", "--input-type=module", "-e", script], {timeout: 20000}));
  const results = (await Promise.all(commands)).map(result => JSON.parse(result.stdout));
  assert.deepEqual(results.map(result => result.status).sort(), ["already_current", "migrated"]);
  const page = await store.scanCommitted(null, 10);
  assert.equal(page.status, "page");
  if (page.status === "page") assert.deepEqual(page.transactions, document.committed);
});

test("a damaged backup blocks retry before source publication", async t => {
  const store = await fixture(t);
  const {bytes} = await legacyHistory(store, [projection(0)]);
  const plan = await migrateFileAuthorityStore(store.directory, GOAL);
  await assert.rejects(migrateFileAuthorityStore(store.directory, GOAL, true, {
    beforePublish: async () => {throw new Error("stop");},
  }), /stop/);
  await writeFile(join(String(plan.backup_directory), "source.json"), "damaged backup");
  await assert.rejects(migrateFileAuthorityStore(store.directory, GOAL, true), /backup is corrupt/);
  assert.deepEqual(await readFile(store.path), bytes);
});
