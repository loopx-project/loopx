import assert from "node:assert/strict";
import {spawnSync} from "node:child_process";
import {mkdtemp, readFile, rm} from "node:fs/promises";
import {join} from "node:path";
import {tmpdir} from "node:os";
import {fileURLToPath} from "node:url";
import test from "node:test";
import {capacityLedger, latency, type CapacityAxis} from "../../examples/coordination/sqlite-capacity-report.ts";

test("latency has explicit nearest-rank tails and rejects absent or invalid samples", () => {
  assert.deepEqual(latency([5, 1, 4, 2, 3]), {n: 5, p50_ms: 3, p95_ms: 5, p99_ms: 5});
  for (const samples of [[], [NaN], [Infinity], [-1]]) assert.throws(() => latency(samples));
});

function axis(count: number): CapacityAxis {
  const sample = (n: number) => ({n, p50_ms: 1, p95_ms: 2, p99_ms: 3});
  return {target_commits: count, completed_commits: count, projection_json_bytes: 65536,
    sample_window: 1000, status: "passed", cleanup_verified: true,
    warm: {commit: sample(1000), head: sample(3000), receipt: sample(2000), scan_100: sample(200)},
    cold_node: sample(20), cold_cli: {mutation: sample(20), status: sample(20), quota: sample(20)},
    bounded_profile: {schema_version: "loopx_sqlite_authority_bounded_profile_v0", status: "available",
      cursor: String(count), commits: count, checkpoints: Math.ceil(count / 64), checkpoint_interval: 64,
      replay_budget_commits: 63, recovery_tail_commits: 0, retained_projection_bytes: 1024,
      retained_delta_bytes: 1024, retained_payload_bytes: 0, database_bytes: 4096, wal_bytes: 0, shm_bytes: 0},
    history_audit: {status: "verified", commits: count, checkpoints: Math.ceil(count / 64)},
    wal_traffic_window: {status: "measured", warmup_commits: 8, window_commits: 1000,
      page_size_bytes: 4096, frame_bytes: 4120, wal_bytes: 41200000, frames: 10000,
      wal_bytes_per_commit: 41200},
    logical_writes: {commits_rows_sampled: 1000, commits_row_bytes_mean: 4096,
      checkpoints: Math.ceil(count / 64), checkpoint_row_bytes_mean: 65536, head_projection_bytes: 65536,
      per_commit_logical_bytes: 73728, cumulative_logical_bytes: 73728 * count, formula: "fixture"},
    lock_wait: {status: "measured", samples: 12, held_write_lock_ms: 200,
      uncontended_commit_p50_ms: 1, observed_wait: sample(12)},
    application_request_json_bytes: 0, files_at_target: {database_bytes: 0, wal_bytes: 0, shm_bytes: 0},
    sampled_peak_rss_bytes: 0, resource_peak_rss_bytes: 0, fill_seconds: 0, cli_commits: 20};
}

test("budget failure remains failed; small rehearsals and unavailable metrics stay missing", () => {
  const baseline = axis(10000), final = axis(100000);
  final.warm!.head = {n: 3000, p50_ms: 1, p95_ms: 5, p99_ms: 6};
  const rows = capacityLedger([baseline, final], true);
  assert.equal(rows.find(row => row.id === "head_history_growth")?.status, "failed");
  assert.equal(rows.find(row => row.id === "head_p95")?.status, "passed");
  assert.equal(rows.find(row => row.id === "logical_write_growth")?.status, "passed");
  assert.equal(rows.find(row => row.id === "wal_traffic_growth")?.status, "passed");
  assert.equal(rows.find(row => row.id === "lock_wait_observed")?.status, "passed");
  assert.equal(rows.find(row => row.id === "elapsed_soak")?.status, "missing");
  assert(capacityLedger([baseline, final], false).every(row => row.status === "missing"));
  final.status = "failed";
  assert.equal(capacityLedger([baseline, final], true)[0]?.status, "failed");
});

test("split storage-write rows cannot stand in for each other or hide per-commit growth", () => {
  const baseline = axis(10000), final = axis(100000);
  const measured = baseline.wal_traffic_window;
  assert(measured?.status === "measured");
  // Per-commit WAL traffic that doubles with history depth is a 10x2 = 20x
  // cumulative growth: beyond the 15x budget even though every latency row is
  // healthy and final file sizes stay plausible.
  final.wal_traffic_window = {...measured, wal_bytes_per_commit: 82400};
  const amplified = capacityLedger([baseline, final], true);
  assert.equal(amplified.find(row => row.id === "wal_traffic_growth")?.status, "failed");
  assert.equal(amplified.find(row => row.id === "logical_write_growth")?.status, "passed");
  // The same amplification hidden inside logical writes must fail there too.
  const logicalBaseline = axis(10000), logicalFinal = axis(100000);
  logicalFinal.logical_writes = {...logicalBaseline.logical_writes!, per_commit_logical_bytes: 147456};
  assert.equal(capacityLedger([logicalBaseline, logicalFinal], true)
    .find(row => row.id === "logical_write_growth")?.status, "failed");
  // An invalidated window, absent accounting or missing probe is missing
  // evidence, never a pass from the surviving columns.
  final.wal_traffic_window = {status: "invalid", reason: "fixture"};
  const lost = capacityLedger([baseline, final], true);
  assert.equal(lost.find(row => row.id === "wal_traffic_growth")?.status, "missing");
  assert.equal(lost.find(row => row.id === "logical_write_growth")?.status, "passed");
  final.logical_writes = null;
  final.lock_wait = null;
  const stripped = capacityLedger([baseline, final], true);
  assert.equal(stripped.find(row => row.id === "logical_write_growth")?.status, "missing");
  assert.equal(stripped.find(row => row.id === "lock_wait_observed")?.status, "missing");
  // A probe with the wrong sample count is not qualification evidence.
  const shortProbe = axis(100000);
  shortProbe.lock_wait = {status: "measured", samples: 11, held_write_lock_ms: 200,
    uncontended_commit_p50_ms: 1, observed_wait: {n: 11, p50_ms: 1, p95_ms: 2, p99_ms: 3}};
  assert.equal(capacityLedger([axis(10000), shortProbe], true)
    .find(row => row.id === "lock_wait_observed")?.status, "missing");
});

test("incomplete, wrong-size or malformed evidence cannot satisfy matched budgets", () => {
  for (const change of [
    (value: CapacityAxis) => {value.completed_commits--;},
    (value: CapacityAxis) => {value.projection_json_bytes = 4096;},
    (value: CapacityAxis) => {value.warm!.head.n = 0;},
    (value: CapacityAxis) => {value.warm!.head.p95_ms = NaN;},
    (value: CapacityAxis) => {value.warm!.head.p50_ms = 99;},
    (value: CapacityAxis) => {value.cleanup_verified = false;},
  ]) {
    const final = axis(100000); change(final);
    assert.equal(capacityLedger([axis(10000), final], true).find(row => row.id === "head_p95")?.status, "missing");
  }
  const final = axis(100000); final.cold_cli = null;
  const rows = capacityLedger([axis(10000), final], true);
  assert.equal(rows.find(row => row.id === "head_p95")?.status, "passed");
  assert.equal(rows.find(row => row.id === "cold_cli_status_p95")?.status, "missing");
});

test("CLI latency improvement is retained as a signed difference", () => {
  const baseline = axis(10000), final = axis(100000);
  baseline.cold_cli!.mutation = {n: 20, p50_ms: 1, p95_ms: 8, p99_ms: 9};
  const row = capacityLedger([baseline, final], true).find(item => item.id === "cold_cli_mutation_increment_p95");
  assert.equal(row?.observed, -6);
  assert.equal(row?.status, "passed");
});

// Budget: the bounded state log proves every encoded delta and audits the
// retained chain, so the rehearsal costs more than the version-1 layout did
// (about 27 s here, roughly twice that on a shared CI runner).
test("small capacity entrypoint exercises real SQLite and never claims a full qualification", {timeout: 180000}, async t => {
  const directory = await mkdtemp(join(tmpdir(), "sqlite-capacity-report-"));
  t.after(() => rm(directory, {recursive: true, force: true}));
  const output = join(directory, "report.json");
  const child = spawnSync(process.execPath, ["--no-warnings", "--experimental-sqlite", "--experimental-strip-types",
    fileURLToPath(new URL("../../examples/coordination/sqlite-capacity.ts", import.meta.url)),
    "--profile", "rehearsal", "--output", output], {encoding: "utf8", timeout: 150000});
  assert.equal(child.status, 0, child.stderr);
  const report = JSON.parse(await readFile(output, "utf8"));
  assert.equal(report.full_d2_qualified, false);
  assert.deepEqual(report.axes.map((row: CapacityAxis) => row.completed_commits), [100, 1000]);
  assert(report.axes.every((row: CapacityAxis) => row.cleanup_verified && row.status === "passed"));
  assert(report.ledger.every((row: {status: string}) => row.status === "missing"));
  assert(report.axes.every((row: CapacityAxis) => row.wal_traffic_window?.status === "measured" &&
    row.wal_traffic_window.window_commits === 100 &&
    row.wal_traffic_window.wal_bytes === row.wal_traffic_window.frames * row.wal_traffic_window.frame_bytes &&
    row.wal_traffic_window.wal_bytes_per_commit === row.wal_traffic_window.wal_bytes / 100));
  assert(report.axes.every((row: CapacityAxis) => row.logical_writes !== null &&
    row.logical_writes.per_commit_logical_bytes ===
    Math.round(row.logical_writes.commits_row_bytes_mean + row.logical_writes.head_projection_bytes +
      row.logical_writes.checkpoint_row_bytes_mean / (row.completed_commits / row.logical_writes.checkpoints))));
  assert(report.axes.every((row: CapacityAxis) => row.lock_wait?.status === "measured" &&
    row.lock_wait.samples === 3 && row.lock_wait.observed_wait.n === 3 &&
    row.lock_wait.observed_wait.p50_ms >= row.lock_wait.uncontended_commit_p50_ms));
  assert.match(report.metric_limits.cumulative_wal_traffic, /held read mark/);
  assert.match(report.metric_limits.lock_wait, /app-observed/);
  assert.equal(report.workload.cold_cli, "not_requested");
  assert.equal(report.runtime.sqlite_version.length > 0, true);
});
