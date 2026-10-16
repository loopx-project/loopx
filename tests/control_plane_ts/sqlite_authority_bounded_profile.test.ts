import assert from "node:assert/strict";
import {mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {DatabaseSync} from "node:sqlite";
import test from "node:test";

import {AUTHORITY_STATE_CHECKPOINT_INTERVAL} from
  "../../loopx/control_plane/coordination/authority_state_log.ts";
import {canonicalAuthorityBytes,
  canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {prepareCoordinationProjectionCommit} from
  "../../loopx/control_plane/coordination/coordination_projection.ts";
import {SqliteAuthorityStore} from
  "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {PRODUCTION_SCALE_HISTORY, productionScaleHistoryProjection,
  productionScaleObservationStep} from "./production_scale_coordination_fixture.ts";

const GOAL_ID = "bounded-profile-goal";

async function fixture(t: test.TestContext): Promise<SqliteAuthorityStore> {
  const directory = await mkdtemp(join(tmpdir(), "sqlite-bounded-"));
  t.after(() => rm(directory, {recursive: true, force: true}));
  return new SqliteAuthorityStore(directory, GOAL_ID);
}

for (const text of ["plain ASCII", "中文", "😀🛰️"]) {
  test(`SQLite retained byte counts measure UTF-8 for ${JSON.stringify(text)}`, async t => {
    const store = await fixture(t);
    let revision: string | null = null;
    for (let index = 1; index <= AUTHORITY_STATE_CHECKPOINT_INTERVAL + 1; index += 1) {
      const applied = await store.commitAuthority({expected_provider_revision: revision,
        operation_id: `byte-profile-${index}`, next_projection: {text, index},
        events: [{text, index}], receipts: [{text, index}]});
      assert.equal(applied.status, "applied", JSON.stringify(applied));
      if (applied.status !== "applied") return;
      revision = applied.provider_revision;
    }
    const before = await store.loadAuthority();
    assert.equal(before.status, "loaded");
    const original = await store.readReceipt("byte-profile-1");
    assert.equal(original.status, "found");
    const profile = await store.boundedProfile();
    assert.equal(profile.status, "available", JSON.stringify(profile));
    if (profile.status !== "available") return;
    assert.equal(profile.checkpoints, 2);
    // Measure the exact retained JSON independently of the profile's SQL
    // aggregates. UTF-8 bytes differ from SQLite TEXT character counts.
    const utf8Bytes = (value: unknown): number => {
      assert.equal(typeof value, "string");
      return Buffer.byteLength(value as string, "utf8");
    };
    const db = new DatabaseSync(store.path, {readOnly: true});
    try {
      const checkpoints = db.prepare("SELECT projection FROM checkpoints").all();
      const commits = db.prepare("SELECT delta, events, receipts FROM commits").all();
      assert.equal(profile.retained_projection_bytes,
        checkpoints.reduce((total, row) => total + utf8Bytes(row.projection), 0));
      assert.equal(profile.retained_delta_bytes,
        commits.reduce((total, row) => total + utf8Bytes(row.delta), 0));
      assert.equal(profile.retained_payload_bytes,
        commits.reduce((total, row) => total + utf8Bytes(row.events) + utf8Bytes(row.receipts), 0));
    } finally { db.close(); }
    assert.deepEqual(await store.loadAuthority(), before);
    assert.deepEqual(await store.readReceipt("byte-profile-1"), original);
    assert.equal((await store.verifyAuthorityHistory()).status, "verified");
  });
}

interface ReplayedHistory {
  readonly heads: string[];
  readonly operations: string[];
  readonly revision: string;
}

/**
 * Drive the fixture's full retained history through one provider.
 *
 * The expected projections are derived from the fixture alone, so a provider
 * that stores or resumes from a lossy encoding diverges from this chain rather
 * than from its own output.
 */
async function replayFixtureHistory(store: SqliteAuthorityStore): Promise<ReplayedHistory> {
  const fixtureValue = productionScaleHistoryProjection(GOAL_ID, "legacy");
  const heads: string[] = [canonicalAuthoritySha256(fixtureValue.projection)];
  const operations: string[] = [];
  let projection = fixtureValue.projection;
  const seeded = await store.commitAuthority({operation_id: "history-seed", expected_provider_revision: null,
    events: [], receipts: [], next_projection: projection});
  assert.equal(seeded.status, "applied", JSON.stringify(seeded));
  if (seeded.status !== "applied") throw new Error("bounded profile seed did not apply");
  let revision = seeded.provider_revision;
  for (let index = 0; index < PRODUCTION_SCALE_HISTORY.commit_count; index += 1) {
    const step = productionScaleObservationStep(projection, index);
    const prepared = prepareCoordinationProjectionCommit({
      goal_id: GOAL_ID, operation_id: step.operation_id,
      expected_provider_revision: revision, projection, mutations: [step.mutation],
    });
    const applied = await store.commitAuthority(prepared);
    assert.equal(applied.status, "applied", JSON.stringify(applied));
    if (applied.status !== "applied") throw new Error("bounded profile history did not apply");
    revision = applied.provider_revision;
    projection = prepared.next_projection;
    heads.push(canonicalAuthoritySha256(projection));
    operations.push(step.operation_id);
  }
  return {heads, operations, revision};
}

test("SQLite retains one bounded window per checkpoint instead of one copy per commit", {timeout: 300000}, async t => {
  const store = await fixture(t);
  assert.equal((await store.storeIdentity()).status, "available");
  const history = await replayFixtureHistory(store);
  const commits = history.heads.length;
  const projectionBytes = canonicalAuthorityBytes(
    productionScaleHistoryProjection(GOAL_ID, "legacy").projection).byteLength;
  const profile = await store.boundedProfile();
  assert.equal(profile.status, "available", JSON.stringify(profile));
  if (profile.status !== "available") return;
  assert.equal(profile.cursor, String(commits));
  assert.equal(profile.commits, commits);
  assert.equal(profile.checkpoint_interval, AUTHORITY_STATE_CHECKPOINT_INTERVAL);
  assert.equal(profile.checkpoints, Math.ceil(commits / AUTHORITY_STATE_CHECKPOINT_INTERVAL));
  assert.equal(profile.replay_budget_commits, AUTHORITY_STATE_CHECKPOINT_INTERVAL - 1);
  assert.equal(profile.recovery_tail_commits, 0);
  // The removed redundancy is the point of the slice: one exact delta per
  // commit plus one full copy per checkpoint window, not one copy per commit.
  const perCommitCopyBytes = projectionBytes * commits;
  assert.ok(profile.checkpoints * 8 < profile.commits,
    `${profile.checkpoints} checkpoints retained ${profile.commits} commits`);
  const retainedStateBytes = profile.retained_projection_bytes + profile.retained_delta_bytes;
  assert.ok(retainedStateBytes < perCommitCopyBytes / 8,
    `retained state ${retainedStateBytes} left ${Math.round(perCommitCopyBytes / 8)} bytes of budget`);
  assert.ok(profile.database_bytes < perCommitCopyBytes / 4,
    `database bytes ${profile.database_bytes} left ${Math.round(perCommitCopyBytes / 4)} bytes of budget`);
  const audited = await store.verifyAuthorityHistory();
  assert.equal(audited.status, "verified", JSON.stringify(audited));
  if (audited.status === "verified") {
    assert.equal(audited.commits, commits);
    assert.equal(audited.checkpoints, profile.checkpoints);
  }
});

test("SQLite replays every retained projection across checkpoint windows", {timeout: 300000}, async t => {
  const store = await fixture(t);
  const history = await replayFixtureHistory(store);
  const expected = history.heads;
  const operations = history.operations;
  const pageSize = PRODUCTION_SCALE_HISTORY.scan_page_size;
  const observed: string[] = [];
  const observedOperations: string[] = [];
  const pageBounds: string[][] = [];
  let after: string | null = null;
  for (;;) {
    const page = await store.scanCommitted(after, pageSize);
    assert.equal(page.status, "page", JSON.stringify(page));
    if (page.status !== "page") return;
    if (page.transactions.length === 0) break;
    pageBounds.push([page.transactions[0]!.cursor, page.transactions[page.transactions.length - 1]!.cursor]);
    for (const transaction of page.transactions) {
      observed.push(canonicalAuthoritySha256(transaction.projection));
      observedOperations.push(transaction.operation_id);
    }
    after = page.transactions[page.transactions.length - 1]!.cursor;
  }
  assert.equal(observed.length, expected.length);
  assert.deepEqual(observed, expected);
  assert.deepEqual(observedOperations, ["history-seed", ...operations]);
  // One page starts below the second checkpoint and ends above it, so a page
  // is proven to span a window boundary instead of being resumed by row count.
  assert.ok(pageBounds.some(([first, last]) =>
    BigInt(first!) <= BigInt(AUTHORITY_STATE_CHECKPOINT_INTERVAL + 1) &&
    BigInt(last!) >= BigInt(AUTHORITY_STATE_CHECKPOINT_INTERVAL + 1)),
    `no page spanned the checkpoint boundary: ${JSON.stringify(pageBounds)}`);
  const head = await store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status === "loaded") {
    assert.equal(head.cursor, String(expected.length));
    assert.equal(head.provider_revision, history.revision);
    assert.equal(canonicalAuthoritySha256(head.head), expected[expected.length - 1]!);
  }
});
