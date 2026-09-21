/** Disposable SQLite qualification. No live goal or caller-supplied runtime. */
import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {spawn, spawnSync} from "node:child_process";
import {existsSync, mkdtempSync, readFileSync, readdirSync, rmSync,
  statfsSync, statSync, writeFileSync} from "node:fs";
import {cpus, platform, release, tmpdir, totalmem} from "node:os";
import {DatabaseSync} from "node:sqlite";
import {delimiter, dirname, join, relative, sep} from "node:path";
import {performance} from "node:perf_hooks";
import {fileURLToPath} from "node:url";
import {parseArgs} from "node:util";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import type {AuthorityStoreCommit} from "../../loopx/control_plane/coordination/authority_store.ts";
import {sqliteAuthorityRuntime} from "../../loopx/control_plane/coordination/sqlite_runtime.ts";
import {selectLocalSqliteAuthority} from "../../loopx/control_plane/coordination/local_authority_provider.ts";
import {engageLegacyCoordinationWriterFence} from "../../loopx/control_plane/coordination/legacy_writer_fence.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {authorityProjectionFixture} from "../../tests/control_plane_ts/authority_projection_fixture.ts";
import {capacityLedger, latency, type CapacityAxis, type QualificationRow} from "./sqlite-capacity-report.ts";

const {values: options} = parseArgs({options: {
  profile: {type: "string", default: "rehearsal"}, output: {type: "string"},
  python: {type: "string", default: "python3"}, cli: {type: "boolean", default: false},
}});
const goal = "sqlite-capacity", formal = options.profile === "matched-64k";
const script = fileURLToPath(import.meta.url), repository = fileURLToPath(new URL("../../", import.meta.url));
const sqlite = (() => {
  try { return sqliteAuthorityRuntime(); }
  catch (error) {
    const ledger: QualificationRow[] = [{id: "runtime_admission", status: "failed", scope: "runtime prerequisites; no qualification database opened"}];
    const report = {schema_version: "loopx_sqlite_capacity_report_v1", profile: options.profile,
      status: "failed", full_d2_qualified: false, axes: [], ledger,
      reason: error instanceof Error ? error.message : "SQLite runtime admission failed"};
    const json = JSON.stringify(report, null, 2) + "\n";
    if (options.output) writeFileSync(options.output, json);
    process.stdout.write(json); process.exit(1);
  }
})(); // Fail before creating a qualification database.

assert(["rehearsal", "matched-64k"].includes(options.profile), "profile must be rehearsal or matched-64k");
const report: Record<string, unknown> = {
  schema_version: "loopx_sqlite_capacity_report_v1", profile: options.profile,
  runtime: sqlite.info, source: sourceIdentity(),
  host: {platform: platform(), release: release(), arch: process.arch,
    logical_cpus: cpus().length, cpu_model: cpus()[0]?.model ?? "unknown", memory_bytes: totalmem(),
    storage_medium: "not_captured"},
  workload: {projection_json_bytes: 65536, event_receipt_max_bytes: 4096,
    fill_read_write_ratio: "5:1", read_mix: "three head, oldest receipt, deterministic middle receipt",
    records: "one native synthetic Todo; fixed padding isolates history growth; not the full domain profile",
    sampling: "last 1000 commits (or entire smaller rehearsal); nearest-rank quantiles",
    traffic_window: "8 warmup commits, then one bounded window (1000 formal / 100 rehearsal) with a held read mark that blocks WAL resets; exact frames from WAL file growth",
    lock_probe: "a probe process holds the write lock for 200 ms per sample (12 formal / 3 rehearsal); the end-to-end store commit wait is reported",
    cold_cli: options.cli ? "new Python process and newly started managed Effect runtime per sample; shutdown outside timing" : "not_requested",
    cold_node: "new Node process and import plus first load; OS file cache is not dropped",
    warm: "same process, actual provider opens and closes each connection"},
  durability: {journal_mode: "WAL", synchronous: "FULL", altered_for_measurement: false},
  budgets: {per_axis_fill_seconds: 2400, database_bytes: 16 * 1024 ** 3, minimum_free_bytes: 5 * 1024 ** 3},
  metric_limits: {
    logical_storage_writes: "measured per commit as serialized bytes handed to SQLite (commits row delta+events+receipts, full-projection head rewrite, amortized checkpoint row); page, index and compaction overhead excluded",
    cumulative_wal_traffic: "measured over one bounded commit window per axis with a held read mark that blocks WAL resets; whole-run WAL totals remain unmeasured",
    lock_wait: "app-observed end-to-end commit wait while a probe process holds the write lock; includes connection open, excludes busy-handler internals (not exposed by node:sqlite)",
    pure_busy_wait: "missing", physical_device_writes: "missing",
    rss_scope: "Node parent sampled per axis; resourceUsage peak is process-lifetime across both axes; CLI child RSS is not measured",
    application_byte_scope: "fixed-history fill requests only; excludes CLI requests and storage/checkpoint work"},
  full_d2_qualified: false,
};
const axes: CapacityAxis[] = [];
for (const count of formal ? [10000, 100000] : [100, 1000]) {
  const axis = await measureAxis(count); axes.push(axis);
  if (axis.status === "failed") break;
}
const ledger = capacityLedger(axes, formal);
const sourceStable = (report.source as Record<string, unknown>).source_tree_sha256 === sourceIdentity().source_tree_sha256;
if (!sourceStable) ledger.push({id: "source_stability", status: "failed", scope: "source changed while the profile was running"});
Object.assign(report, {axes, ledger, source_stable: sourceStable, status: axes.some(axis => axis.status === "failed") ||
  ledger.some(row => row.status === "failed") ? "failed" : "incomplete"});
const json = JSON.stringify(report, null, 2) + "\n";
if (options.output) writeFileSync(options.output, json);
process.stdout.write(json);
if (report.status === "failed") process.exitCode = 1;

function sourceIdentity(): Record<string, unknown> {
  const hash = createHash("sha256");
  const visit = (directory: string) => {
    for (const entry of readdirSync(directory, {withFileTypes: true}).sort((a, b) => Buffer.compare(Buffer.from(a.name), Buffer.from(b.name)))) {
      const path = join(directory, entry.name);
      if (entry.isDirectory() && entry.name !== "__pycache__") visit(path);
      else if (entry.isFile() && /\.(py|ts|json)$/.test(entry.name)) hash.update(relative(repository, path).split(sep).join("/")).update("\0").update(readFileSync(path));
    }
  };
  visit(join(repository, "loopx"));
  for (const path of [script, join(dirname(script), "sqlite-capacity-report.ts"),
    join(repository, "tests/control_plane_ts/authority_projection_fixture.ts")]) hash.update(relative(repository, path).split(sep).join("/")).update("\0").update(readFileSync(path));
  const git = spawnSync("git", ["rev-parse", "HEAD"], {cwd: repository, encoding: "utf8"});
  return {git_head: git.status === 0 ? git.stdout.trim() : null, source_tree_sha256: hash.digest("hex"),
    fingerprint_scope: "LoopX Python/TS/JSON source, capacity entrypoint/report and shared fixture; includes uncommitted source"};
}

async function measureAxis(count: number): Promise<CapacityAxis> {
  const projection = authorityProjectionFixture(goal, [{todo_id: "todo_capacity", role: "agent", status: "open",
    done: false, text: "Capacity 000000", archive_state: "active", claimed_by: "agent-a", task_class: "advancement_task"}],
  [], "native", {handoff_mode: "soft_claim", capacity_padding: ""});
  const padding = 65536 - Buffer.byteLength(JSON.stringify(projection));
  assert(padding >= 0); projection.capacity_padding = "p".repeat(padding);
  assert.equal(Buffer.byteLength(JSON.stringify(projection)), 65536);
  const root = mkdtempSync(join(tmpdir(), "loopx-sqlite-capacity-"));
  const runtime = join(root, "runtime"), state = join(root, "state.md"), registry = join(root, "registry.json");
  const directory = join(runtime, "authority", "sqlite-v0");
  const store = new SqliteAuthorityStore(directory, goal);
  const axis: CapacityAxis = {target_commits: count, completed_commits: 0, projection_json_bytes: 65536,
    sample_window: Math.min(1000, count), status: "failed", warm: null, cold_node: null, cold_cli: null,
    application_request_json_bytes: 0, files_at_target: null, sampled_peak_rss_bytes: process.memoryUsage().rss,
    bounded_profile: null, history_audit: null, wal_traffic_window: null, logical_writes: null, lock_wait: null,
    resource_peak_rss_bytes: 0, fill_seconds: 0, cli_commits: 0, cleanup_verified: false};
  const commits: number[] = [], heads: number[] = [], receipts: number[] = [];
  const timed = async <T>(fn: () => Promise<T>, samples?: number[]): Promise<T> => {
    const start = performance.now(), result = await fn(); samples?.push(performance.now() - start); return result;
  };
  // Instrumentation commits (traffic window, lock probe) extend the same
  // matched workload past the fill target; they are counted separately so the
  // ledger's target-state rows keep their meaning.
  let revision: string | null = null;
  let extraCommits = 0, instrumentOrdinal = count + 1;
  const commitProbe = async (operationId: string): Promise<number> => {
    const ordinal = instrumentOrdinal++;
    const input: AuthorityStoreCommit = {expected_provider_revision: revision, operation_id: operationId,
      next_projection: projection,
      events: [{kind: "synthetic", ordinal, data: "e".repeat(1800)}],
      receipts: [{operation_id: operationId, ordinal, data: "r".repeat(1800)}]};
    const started = performance.now();
    const result = await store.commitAuthority(input);
    if (result.status !== "applied") throw new Error(`instrumentation commit rejected: ${operationId}`);
    revision = result.provider_revision; extraCommits++;
    return performance.now() - started;
  };
  const environment = {...process.env, PATH: dirname(process.execPath) + delimiter + (process.env.PATH ?? ""),
    NODE_OPTIONS: "--experimental-sqlite", TMPDIR: root, TMP: root, TEMP: root};
  let phase = "setup", managedRuntimeUsed = false;
  const stopRuntime = () => {
    if (!managedRuntimeUsed) return;
    const child = spawnSync(options.python!, ["-c",
      "from loopx.control_plane.effect_runtime import effect_runtime_result; effect_runtime_result('runtime.shutdown', {}, retry_safe=False)"],
    {cwd: repository, env: environment, encoding: "utf8", timeout: 30000});
    assert.equal(child.status, 0, "isolated Effect runtime shutdown failed");
    managedRuntimeUsed = false;
  };
  const cli = (args: string[], samples: number[]) => {
    phase = `cold_cli_${args[0] === "todo" ? "mutation" : args[0]}`;
    const start = performance.now(); managedRuntimeUsed = true;
    const child = spawnSync(options.python!, ["-m", "loopx.cli", "--registry", registry, "--format", "json", ...args],
      {cwd: repository, env: environment, encoding: "utf8", timeout: 60000, maxBuffer: 4 * 1024 ** 2});
    const elapsed = performance.now() - start;
    if (child.status !== 0) process.stderr.write(`${phase}: process exited ${child.status ?? "without an exit code"}\n`);
    assert.equal(child.status, 0, `cold CLI ${args[0]} failed`);
    const result = JSON.parse(child.stdout) as Record<string, unknown>;
    assert.notEqual(result.ok, false, `cold CLI ${args[0]} rejected`);
    samples.push(elapsed); stopRuntime(); return result;
  };
  try {
    assert.equal((await selectLocalSqliteAuthority(runtime, goal, true)).ok, true);
    writeFileSync(state, "# Synthetic SQLite capacity goal\n\n## Objective\n\nValidate synthetic storage.\n\n## Agent Todo\n");
    writeFileSync(registry, JSON.stringify({schema_version: "0.1", common_runtime_root: runtime, goals: [{id: goal,
      repo: root, state_file: "state.md", status: "active", domain: "synthetic-storage-qualification",
      adapter: {kind: "read_only_project_map_v0", status: "connected-read-only"},
      coordination: {registered_agents: ["agent-a"]}}]}));
    phase = "matched_fill";
    const start = performance.now();
    for (let i = 1; i <= count; i++) {
      const input: AuthorityStoreCommit = {expected_provider_revision: revision, operation_id: `op-${i}`, next_projection: projection,
        events: [{kind: "synthetic", ordinal: i, data: "e".repeat(1800)}],
        receipts: [{operation_id: `op-${i}`, ordinal: i, data: "r".repeat(1800)}]};
      assert(Buffer.byteLength(JSON.stringify(input.events)) + Buffer.byteLength(JSON.stringify(input.receipts)) <= 4096);
      axis.application_request_json_bytes += Buffer.byteLength(JSON.stringify(input));
      const sample = i > count - axis.sample_window;
      const result = await timed(() => store.commitAuthority(input), sample ? commits : undefined);
      assert.equal(result.status, "applied"); if (result.status !== "applied") throw new Error("commit rejected");
      assert.equal(result.cursor, String(i)); revision = result.provider_revision; axis.completed_commits = i;
      for (let j = 0; j < 3; j++) {
        const loaded = await timed(() => store.loadAuthority(), sample ? heads : undefined);
        assert.equal(loaded.status, "loaded"); if (loaded.status !== "loaded") throw new Error("head unavailable");
        assert.equal(loaded.cursor, String(i)); assert.deepEqual(loaded.head, projection);
      }
      for (const operation of [1, Math.max(1, Math.floor(i / 2))]) {
        const read = await timed(() => store.readReceipt(`op-${operation}`), sample ? receipts : undefined);
        assert.equal(read.status, "found"); if (read.status !== "found") throw new Error("receipt unavailable");
        assert.equal(read.receipts[0]?.ordinal, operation);
      }
      if (i % 100 === 0) {
        axis.sampled_peak_rss_bytes = Math.max(axis.sampled_peak_rss_bytes, process.memoryUsage().rss);
        const fs = statfsSync(root);
        assert(performance.now() - start < 2400000, "axis wall budget exhausted");
        assert(statSync(store.path).size < 16 * 1024 ** 3, "database budget exhausted");
        assert(fs.bavail * fs.bsize > 5 * 1024 ** 3, "disk reserve exhausted");
      }
      if (i % 10000 === 0) process.stderr.write(`SQLite capacity: ${i}/${count} commits\n`);
    }
    axis.fill_seconds = (performance.now() - start) / 1000;
    phase = "scan";
    const scans: number[] = [];
    for (let i = 0; i < (count >= 10000 ? 200 : 10); i++) {
      const page = await timed(() => store.scanCommitted(count === 100 ? null : String(count - 100), 100), scans);
      assert.equal(page.status, "page"); if (page.status !== "page") throw new Error("scan unavailable");
      assert.deepEqual(page.transactions.map(row => row.operation_id), Array.from({length: 100}, (_, k) => `op-${count - 99 + k}`));
      for (const row of page.transactions) assert.deepEqual(row.projection, projection);
    }
    axis.warm = {commit: latency(commits), head: latency(heads), receipt: latency(receipts), scan_100: latency(scans)};
    const bytes = (path: string) => existsSync(path) ? statSync(path).size : 0;
    axis.files_at_target = {database_bytes: bytes(store.path), wal_bytes: bytes(store.path + "-wal"), shm_bytes: bytes(store.path + "-shm")};
    phase = "bounded_profile";
    // Retained state and recovery bound of the filled history. The linear
    // archive audit is only launched where its cost is affordable: it reads
    // every retained transaction, so the formal 100k axis leaves it to a
    // separately authorized run.
    const profile = await store.boundedProfile();
    if (profile.status !== "available") throw new Error("bounded profile unavailable");
    axis.bounded_profile = profile;
    if (!formal) {
      const audit = await store.verifyAuthorityHistory();
      axis.history_audit = audit.status === "verified"
        ? {status: audit.status, commits: audit.commits, checkpoints: audit.checkpoints}
        : {status: audit.status, commits: 0, checkpoints: 0};
      assert.equal(audit.status, "verified");
    }
    phase = "logical_writes";
    // Logical write volume comes from the filled database itself, so the
    // numbers stay separated from WAL traffic and final file size.
    {
      const db = new DatabaseSync(store.path);
      try {
        const head = db.prepare("SELECT length(CAST(projection AS BLOB)) AS bytes FROM head WHERE singleton = 1")
          .get() as {bytes: number};
        const commitRow = db.prepare(`SELECT count(*) AS n,
          avg(length(CAST(delta AS BLOB)) + length(CAST(events AS BLOB)) + length(CAST(receipts AS BLOB))) AS mean
          FROM (SELECT delta, events, receipts FROM commits ORDER BY cursor DESC LIMIT 1000)`).get() as
          {n: number; mean: number};
        const checkpointRow = db.prepare(
          "SELECT count(*) AS n, avg(length(CAST(projection AS BLOB))) AS mean FROM checkpoints").get() as
          {n: number; mean: number};
        assert(commitRow.n > 0 && head.bytes > 0 && Number.isFinite(commitRow.mean));
        const interval = checkpointRow.n > 0 ? axis.completed_commits / checkpointRow.n : Number.POSITIVE_INFINITY;
        const perCommit = commitRow.mean + head.bytes +
          (Number.isFinite(interval) ? checkpointRow.mean / interval : 0);
        axis.logical_writes = {commits_rows_sampled: commitRow.n,
          commits_row_bytes_mean: Math.round(commitRow.mean), checkpoints: checkpointRow.n,
          checkpoint_row_bytes_mean: Math.round(checkpointRow.mean), head_projection_bytes: head.bytes,
          per_commit_logical_bytes: Math.round(perCommit),
          cumulative_logical_bytes: Math.round(perCommit * axis.completed_commits),
          formula: "per commit = commits row (delta+events+receipts) + full-projection head rewrite + checkpoint row amortized over its interval"};
      } finally { db.close(); }
    }
    phase = "wal_traffic_window";
    // WAL traffic is measured over one bounded window with read marks pinned
    // so no checkpoint can reset the WAL: the file only appends and frame
    // growth is exact. Two observers are needed because a store connection
    // checkpoints and resets the WAL on every close: the first observer
    // blocks that reset while one align commit leaves frames behind, then the
    // second observer pins a read mark inside the now non-empty WAL, where a
    // reset is impossible. The window measures per-commit traffic at this
    // history depth; it is not a whole-run total.
    {
      const windowWarmup = 8, windowCommits = formal ? 1000 : 100;
      const pin = (observer: DatabaseSync) => {
        observer.exec("BEGIN");
        assert.equal((observer.prepare("SELECT count(*) AS n FROM commits").get() as {n: number}).n,
          axis.completed_commits + extraCommits);
      };
      const reset = (observer: DatabaseSync) => {
        try { observer.exec("ROLLBACK"); } catch { /* the transaction already ended */ }
        observer.close();
      };
      const blocker = new DatabaseSync(store.path);
      const marker = new DatabaseSync(store.path);
      try {
        blocker.exec("PRAGMA busy_timeout = 5000");
        for (let i = 1; i <= windowWarmup; i++) await commitProbe(`wal-warmup-${count}-${i}`);
        const pageSize = (blocker.prepare("PRAGMA page_size").get() as {page_size: number}).page_size;
        pin(blocker);
        await commitProbe(`wal-align-${count}`);
        pin(marker);
        const walPath = store.path + "-wal";
        const startSize = statSync(walPath).size, startHeader = Buffer.from(readFileSync(walPath).subarray(0, 32));
        assert(startSize > 32, "WAL must hold the align commit when the read mark is pinned");
        for (let i = 1; i <= windowCommits; i++) await commitProbe(`wal-window-${count}-${i}`);
        const endSize = statSync(walPath).size, endHeader = Buffer.from(readFileSync(walPath).subarray(0, 32));
        const frameBytes = 24 + pageSize, growth = endSize - startSize;
        if (!startHeader.equals(endHeader) || growth <= 0 || growth % frameBytes !== 0) {
          axis.wal_traffic_window = {status: "invalid", reason: startHeader.equals(endHeader)
            ? "WAL growth is empty, negative or not frame aligned" : "WAL header changed; a reset happened despite the held read marks"};
        } else {
          const frames = growth / frameBytes;
          axis.wal_traffic_window = {status: "measured", warmup_commits: windowWarmup,
            window_commits: windowCommits, page_size_bytes: pageSize, frame_bytes: frameBytes,
            wal_bytes: growth, frames, wal_bytes_per_commit: growth / windowCommits};
        }
      } finally {
        try { reset(blocker); } catch { /* cleanup only */ }
        try { reset(marker); } catch { /* cleanup only */ }
      }
    }
    phase = "lock_wait";
    // The node:sqlite driver exposes no busy-handler timing, so the probe
    // measures the application-observed wait: a separate process holds the
    // write lock for a controlled interval while one store commit runs.
    {
      const heldMs = 200, lockSamples = formal ? 12 : 3;
      const probe = `const {DatabaseSync} = require("node:sqlite");
        const db = new DatabaseSync(process.argv[1]);
        db.exec("PRAGMA busy_timeout = 3000");
        db.exec("BEGIN IMMEDIATE");
        process.stdout.write("READY\\n");
        setTimeout(() => { db.close(); process.exit(0); }, Number(process.argv[2]));`;
      const waits: number[] = [];
      for (let i = 1; i <= lockSamples; i++) {
        const child = spawn(process.execPath, ["--no-warnings", "--experimental-sqlite", "-e", probe,
          store.path, String(heldMs)], {stdio: ["ignore", "pipe", "inherit"]});
        const {stdout} = child;
        assert(stdout, "lock probe stdout must be piped");
        const ready = new Promise<void>((resolveReady, rejectReady) => {
          const guard = setTimeout(() => rejectReady(new Error("lock probe did not hold the write lock in time")), 10000);
          stdout.once("data", () => { clearTimeout(guard); resolveReady(); });
          child.once("exit", () => { clearTimeout(guard);
            rejectReady(new Error("lock probe process exited before holding the write lock")); });
        });
        await ready;
        waits.push(await commitProbe(`lock-probe-${count}-${i}`));
        await new Promise<void>(resolveExit => child.once("exit", () => resolveExit()));
      }
      assert(axis.warm?.commit, "lock wait needs the uncontended commit baseline");
      axis.lock_wait = {status: "measured", samples: lockSamples, held_write_lock_ms: heldMs,
        uncontended_commit_p50_ms: axis.warm.commit.p50_ms, observed_wait: latency(waits)};
    }
    phase = "cold_node";
    const cold: number[] = [], samples = formal ? 20 : 3;
    for (let i = 0; i < samples; i++) {
      const before = performance.now();
      const child = spawnSync(process.execPath, ["--no-warnings", "--experimental-sqlite", "--experimental-strip-types",
        "--input-type=module", "-e",
        `import {SqliteAuthorityStore} from ${JSON.stringify(new URL("../../loopx/control_plane/coordination/sqlite_authority_store.ts", import.meta.url).href)};
         const result=await new SqliteAuthorityStore(process.argv[1],${JSON.stringify(goal)},{existingOnly:true}).loadAuthority();
         if(result.status!=="loaded")process.exit(1);`, directory], {encoding: "utf8", timeout: 30000});
      assert.equal(child.status, 0, "cold store process failed"); cold.push(performance.now() - before);
    }
    axis.cold_node = latency(cold);
    if (options.cli) {
      phase = "cold_cli";
      const fence = await engageLegacyCoordinationWriterFence({schema_version: "loopx_legacy_coordination_writer_fence_engage_request_v0",
        runtime_root: runtime, goal_id: goal, state_path: state, fence: {schema_version: "loopx_legacy_coordination_writer_fence_v0",
          state: "engaged", goal_id: goal, fence_id: "capacity-fixture", source_version: "capacity-fixture",
          source_projection_sha256: canonicalAuthoritySha256(projection), expected_shadow_provider_revision: revision}});
      assert.equal(fence.status, "applied");
      const mutation: number[] = [], status: number[] = [], quota: number[] = [];
      for (let i = 0; i < samples; i++) {
        const statusResult = cli(["status", "--goal-id", goal], status);
        const index = statusResult.todo_index as {items?: {todo_id?: string}[]} | undefined;
        assert(index?.items?.some(row => row.todo_id === "todo_capacity"), "status lost the canonical Todo");
        const quotaResult = cli(["quota", "should-run", "--goal-id", goal, "--agent-id", "agent-a"], quota);
        const selected = quotaResult.selected_todo as {todo_id?: string} | undefined;
        const summary = quotaResult.agent_todo_summary as {first_executable_items?: {todo_id?: string}[]} | undefined;
        assert(selected?.todo_id === "todo_capacity" || summary?.first_executable_items?.some(row => row.todo_id === "todo_capacity"),
          "quota lost the canonical Todo");
        const result = cli(["todo", "update", "--goal-id", goal, "--agent-id", "agent-a", "--todo-id", "todo_capacity",
          "--text", `Capacity ${String(i + 1).padStart(6, "0")}`, "--update-operation-id", `cli-${i}`], mutation);
        assert.equal(result.source_authority, "sqlite_v0"); axis.cli_commits++;
      }
      axis.cold_cli = {mutation: latency(mutation), status: latency(status), quota: latency(quota)};
      const final = await store.loadAuthority(); assert.equal(final.status, "loaded");
      if (final.status === "loaded") assert.equal(final.cursor, String(count + extraCommits + samples));
    }
    axis.status = "passed";
  } catch (error) {
    axis.failure = `${phase}: ${error instanceof Error ? error.name : "unknown failure"}`;
  } finally {
    try { stopRuntime(); } catch { axis.status = "failed"; axis.failure = "isolated_runtime_cleanup_failed"; }
    axis.resource_peak_rss_bytes = process.resourceUsage().maxRSS * 1024;
    try { rmSync(root, {recursive: true, force: true}); axis.cleanup_verified = !existsSync(root); }
    catch { axis.status = "failed"; axis.failure = "temporary_database_cleanup_failed"; }
  }
  return axis;
}
