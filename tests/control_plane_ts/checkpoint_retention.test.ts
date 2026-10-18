import assert from "node:assert/strict";
import test from "node:test";
import {checkpointArtifacts} from "../../loopx/control_plane/runtime/checkpoint_artifacts.ts";
import {checkpointRetentionPlan} from "../../loopx/control_plane/runtime/checkpoint_retention.ts";

const points = Array.from({length: 21}, (_, n) => ({id: `p${String(n).padStart(2, "0")}`,
  sha256: n.toString(16).padStart(64, "0"), sealed_at: n * 100, bytes: 10}));
const policy = {strategy: "tiered", keep_latest: 2, keep_first: true, minimum_age_seconds: 0,
  pins: [], protect_since: null, bucket_seconds: 100};
const plan = (overrides = {}, rows = points) => checkpointRetentionPlan({policy: {...policy, ...overrides}, points: rows, now: 3000});
const ids = (rows: unknown) => (rows as {id: string}[]).map(p => p.id);

test("default retention keeps only the latest complete publication without origin or sampled history", () => {
  const result = checkpointRetentionPlan({policy: null, points, now: 3000});
  assert.deepEqual(ids(result.retained), ["p20"]);
  assert.equal((result.removed as unknown[]).length, 20);
  assert.deepEqual(result.policy, {strategy: "uniform", keep_latest: 1, keep_first: false,
    minimum_age_seconds: 0, history_slots: 0, protect_since: null, pins: []});
  assert.equal(result.execution_authority_granted, false);
  assert.deepEqual(checkpointRetentionPlan({policy: null, points: [], now: 3000}).removed, []);
});

test("tiered retention preserves latest, origin and newest representative per exponentially wider age band", () => {
  const result = plan();
  assert.deepEqual(ids(result.retained), ["p00", "p04", "p12", "p16", "p18", "p19", "p20"]);
  assert.equal(result.retained_bytes, 70);
  assert.equal(result.removable_bytes, 140);
  assert.deepEqual(plan({}, points.slice().reverse()), result);
  assert.equal(result.execution_authority_granted, false);
});

test("uniform history samples elapsed time, including irregular capture spacing", () => {
  const {bucket_seconds: _, ...common} = policy;
  const input = {policy: {...common, strategy: "uniform", history_slots: 2}, now: 3000, points};
  assert.deepEqual(ids(checkpointRetentionPlan(input).retained), ["p00", "p09", "p18", "p19", "p20"]);
  const irregular = [0, 1, 2, 3, 4, 50, 98, 99, 100].map((at, n) => ({...points[n], sealed_at: at}));
  const uneven = checkpointRetentionPlan({...input, points: irregular});
  assert.deepEqual(ids(uneven.retained), ["p00", "p05", "p06", "p07", "p08"]);
});

test("pins, grace and pending-selection cutoff add hard protections without evicting history", () => {
  const baseline = new Set(ids(plan().retained));
  const protectedResult = plan({pins: [{id: points[5].id, sha256: points[5].sha256}], protect_since: 1700});
  const kept = new Set(ids(protectedResult.retained));
  for (const id of baseline) assert(kept.has(id));
  assert(kept.has("p05") && kept.has("p17"));
  const grace = plan({minimum_age_seconds: 1500});
  assert.equal(grace.grace_expires_at, 3100);
  for (let n = 16; n <= 20; n++) assert(ids(grace.retained).includes(points[n].id));
  assert(ids(plan({minimum_age_seconds: 1}, [...points, {...points[0], id: "future", sealed_at: 9000}]).retained).includes("future"));
});

test("both sampling strategies are idempotent without a new publication or expired protection", () => {
  for (const selected of [policy, {strategy: "uniform", keep_latest: 2, keep_first: true, minimum_age_seconds: 0,
    pins: [], protect_since: null, history_slots: 3}]) {
    const first = checkpointRetentionPlan({policy: selected, points, now: 3000});
    const retainedIds = new Set(ids(first.retained));
    const again = checkpointRetentionPlan({policy: selected, points: points.filter(p => retainedIds.has(p.id)), now: 3000});
    assert.deepEqual(again.removed, []);
  }
});

test("no policy can disable newest protection; malformed, missing and changed pins fail closed", () => {
  for (const overrides of [{keep_latest: 0}, {keep_latest: 1.5}, {bucket_seconds: 0}, {minimum_age_seconds: -1},
    {pins: [{id: "missing", sha256: points[0].sha256}]}, {pins: [{id: points[0].id, sha256: points[1].sha256}]},
    {pins: [{id: "../escape", sha256: points[0].sha256}]}, {strategy: "other"}, {unknown: true}]) {
    assert.throws(() => plan(overrides));
  }
  assert.throws(() => plan({}, [...points, points[0]]), /duplicate/);
  assert.throws(() => plan({}, [{...points[0], bytes: NaN}]), /bytes/);
  assert.equal((plan({}, []).removed as unknown[]).length, 0);
});

test("existing artifact Effect entry delegates to the sole typed retention selection owner", async () => {
  assert.deepEqual(await checkpointArtifacts({action: "retention_plan", input: {policy, points, now: 3000}}), plan());
  await assert.rejects(checkpointArtifacts({action: "retention_plan", input: {}, execute: true}), /request/);
});

test("retention points and pins use bare lowercase SHA-256 identities", () => {
  const sha256 = "a".repeat(64);
  const point = {...points[0], sha256};
  assert.deepEqual(ids(plan({pins: [{id: point.id, sha256}]}, [point]).retained), [point.id]);
  for (const invalid of ["", "a".repeat(63), "a".repeat(65), "A".repeat(64),
    "g".repeat(64), `sha256:${sha256}`]) {
    assert.throws(() => plan({}, [{...point, sha256: invalid}]), /invalid retention point identity/);
    assert.throws(() => plan({pins: [{id: point.id, sha256: invalid}]}, [point]),
      /invalid retention point identity/);
  }
});
