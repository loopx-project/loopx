/** Matched, disposable local-provider experiment; never selects a live provider. */
import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {spawnSync} from "node:child_process";
import {mkdtempSync, readFileSync, readdirSync, rmSync, statfsSync, statSync, writeFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {performance} from "node:perf_hooks";
import {parseArgs} from "node:util";
import {fileURLToPath} from "node:url";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import type {AuthorityStore, AuthorityStoreCommit, AuthorityStoreCommitResult} from "../../loopx/control_plane/coordination/authority_store.ts";
import {canonicalAuthorityBytes} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {sqliteRuntimeIdentity} from "../../loopx/control_plane/coordination/sqlite_runtime.ts";
import {productionScaleCoordinationFixture, productionScaleHistoryProjection, productionScaleObservationStep} from
  "../../tests/control_plane_ts/production_scale_coordination_fixture.ts";
import {authorityProjectionFixture} from "../../tests/control_plane_ts/authority_projection_fixture.ts";
import {latency} from "./sqlite-capacity-report.ts";

const {values} = parseArgs({options: {provider: {type: "string"}, workload: {type: "string", default: "mixed"},
  commits: {type: "string", default: "128"}, samples: {type: "string", default: "20"},
  output: {type: "string"}, "cold-root": {type: "string"}}});
assert(values.provider === "file" || values.provider === "sqlite", "provider must be file or sqlite");
assert(["mixed", "full", "fixed-64k", "changing-1m"].includes(values.workload!), "unknown workload");
const count = Number(values.commits), samples = Number(values.samples);
assert(Number.isSafeInteger(count) && count >= 100 && count <= 2048, "commits must be 100..2048; use D2 runner for capacity");
assert(Number.isSafeInteger(samples) && samples >= 3 && samples <= 100, "samples must be 3..100");
const goal = "provider-comparison";
let fixtureBase: JsonObject | undefined;
const openStore = (root: string): AuthorityStore => values.provider === "file"
  ? new FileAuthorityStore(root, goal) : new SqliteAuthorityStore(root, goal);
if (values["cold-root"]) {
  const result = await openStore(values["cold-root"]).loadAuthority();
  assert.equal(result.status, "loaded");
  process.stdout.write(JSON.stringify(result));
} else {
  const space = statfsSync(tmpdir());
  assert(space.bavail * space.bsize > 512 * 1024 ** 2, "need 512 MiB free before bounded comparison");
  const root = mkdtempSync(join(tmpdir(), "loopx-provider-comparison-"));
  try {
    const report = await measure(root);
    const output = JSON.stringify(report, null, 2) + "\n";
    if (values.output) writeFileSync(values.output, output);
    process.stdout.write(output);
  } finally { rmSync(root, {recursive: true, force: true}); }
}

function projectionAt(index: number): JsonObject {
  const base = fixtureBase ??= (values.workload === "full" ? productionScaleCoordinationFixture(goal, "native")
    : productionScaleHistoryProjection(goal, "native")).projection as JsonObject;
  if (values.workload === "fixed-64k") {
    const padding = 65536 - Buffer.byteLength(JSON.stringify({...base, padding: ""}));
    assert(padding >= 0, "mixed fixture no longer fits fixed 64 KiB profile");
    return {...base, padding: "p".repeat(padding)};
  }
  const step = productionScaleObservationStep(base, index);
  const todos = (base.todos as JsonObject[]).map(todo => todo.todo_id === step.todo_id ? step.mutation.todo : todo);
  const extras = {...base};
  for (const key of ["todos", "leases", "todo_read_model", "goal_id"]) delete extras[key];
  const projection = authorityProjectionFixture(goal, todos, base.leases as JsonObject[], "native", extras);
  if (values.workload === "changing-1m") {
    const padding = 1024 ** 2 - Buffer.byteLength(JSON.stringify({...projection, padding: ""}));
    assert(padding >= 0); projection.padding = "p".repeat(padding);
  }
  return projection;
}

function sourceIdentity() {
  const git = (args: string[]) => {
    const result = spawnSync("git", args, {encoding: "utf8"});
    assert.equal(result.status, 0, result.stderr); return result.stdout;
  };
  return {revision: git(["rev-parse", "HEAD"]).trim(),
    tracked_diff_sha256: createHash("sha256").update(git(["diff", "HEAD", "--", "loopx", "tests/control_plane_ts"])).digest("hex"),
    runner_sha256: createHash("sha256").update(readFileSync(fileURLToPath(import.meta.url))).digest("hex")};
}

async function measure(root: string) {
  const source = sourceIdentity();
  const store = openStore(root), commits: number[] = [], loads: number[] = [], reads: number[] = [], scans: number[] = [];
  // Generate one input at a time outside timings; retaining N full expected
  // snapshots here would add artificial memory pressure to the provider test.
  const finalProjection = projectionAt(count - 1);
  let revision: string | null = null;
  let first: AuthorityStoreCommit | undefined;
  let firstResult: Extract<AuthorityStoreCommitResult, {status: "applied"}> | undefined;
  const receiptsAt = (index: number) => [{operation_id: `op-${index}`, index,
    metadata: {checked: true, labels: ["synthetic", "保留"]}}];
  let filePublicationBytes = 0;
  const fileBytes = (): number => readdirSync(root).reduce((sum, name) => sum + statSync(join(root, name)).size, 0);
  const timed = async <T>(action: () => Promise<T>, into: number[]): Promise<T> => {
    const start = performance.now(); const result = await action(); into.push(performance.now() - start); return result;
  };
  const start = performance.now();
  for (let index = 0; index < count; index++) {
    const input: AuthorityStoreCommit = {expected_provider_revision: revision, operation_id: `op-${index}`,
      next_projection: projectionAt(index), events: [{kind: "observation", index}],
      receipts: receiptsAt(index)};
    const result = await timed(() => store.commitAuthority(input), commits);
    assert.equal(result.status, "applied"); if (result.status !== "applied") throw new Error("commit rejected");
    revision = result.provider_revision;
    if (index === 0) { first = input; firstResult = result; }
    if (values.provider === "file") filePublicationBytes += statSync((store as FileAuthorityStore).path).size;
    if ((index + 1) % 128 === 0) process.stderr.write(`${values.provider} ${values.workload}: ${index + 1}/${count}\n`);
  }
  const fillMs = performance.now() - start;
  const postFillRss = process.memoryUsage().rss;
  for (let index = 0; index < samples; index++) {
    const head = await timed(() => store.loadAuthority(), loads);
    assert.equal(head.status, "loaded"); if (head.status === "loaded") assert.deepEqual(head.head, finalProjection);
    const receiptIndex = Math.floor(index * (count - 1) / (samples - 1));
    const receipt = await timed(() => store.readReceipt(`op-${receiptIndex}`), reads);
    assert.equal(receipt.status, "found");
    if (receipt.status === "found") {
      assert.equal(receipt.cursor, String(receiptIndex + 1));
      assert.deepEqual(receipt.receipts, receiptsAt(receiptIndex));
    }
    const page = await timed(() => store.scanCommitted(String(count - 100), 100), scans);
    assert.equal(page.status, "page");
    if (page.status === "page") {
      assert.equal(page.transactions.length, 100);
      for (const [offset, row] of page.transactions.entries()) {
        const ordinal = count - 100 + offset;
        assert.equal(row.operation_id, `op-${ordinal}`);
        assert.deepEqual(row.projection, projectionAt(ordinal));
        assert.deepEqual(row.events, [{kind: "observation", index: ordinal}]);
        assert.deepEqual(row.receipts, receiptsAt(ordinal));
      }
    }
  }
  // A separately opened process verifies the full head, not merely a successful exit code.
  const cold: number[] = [];
  for (let index = 0; index < Math.min(samples, 5); index++) {
    const started = performance.now();
    const child = spawnSync(process.execPath, ["--no-warnings", "--experimental-strip-types", "--experimental-sqlite",
      fileURLToPath(import.meta.url), "--provider", values.provider!, "--cold-root", root],
    {encoding: "utf8", timeout: 120000, maxBuffer: 4 * 1024 ** 2});
    cold.push(performance.now() - started); assert.equal(child.status, 0, child.stderr);
    assert.deepEqual(JSON.parse(child.stdout).head, finalProjection);
  }
  // Qualification is outside the timings. Walk bounded pages without retaining
  // N full projections, checking independently generated input and exact history.
  const reopened = openStore(root);
  const historyDigest = async () => {
    const digest = createHash("sha256");
    let cursor: string | null = null, checked = 0;
    for (;;) {
      const page = await reopened.scanCommitted(cursor, 100);
      assert.equal(page.status, "page"); if (page.status !== "page") throw new Error("history read rejected");
      assert(page.transactions.length > 0);
      for (const row of page.transactions) {
        assert(checked < count, "history includes an unexpected transaction");
        assert.equal(row.operation_id, `op-${checked}`);
        assert.equal(row.cursor, String(checked + 1));
        assert.deepEqual(row.projection, projectionAt(checked));
        assert.deepEqual(row.events, [{kind: "observation", index: checked}]);
        assert.deepEqual(row.receipts, receiptsAt(checked));
        digest.update(canonicalAuthorityBytes(row)); digest.update("\n"); checked++;
      }
      if (!page.has_more) break;
      assert.equal(page.next_cursor, String(checked));
      cursor = page.next_cursor;
    }
    assert.equal(checked, count);
    return digest.digest("hex");
  };
  assert(first && firstResult);
  const before = await reopened.loadAuthority(), original = await reopened.readReceipt(first.operation_id);
  assert.equal(before.status, "loaded");
  if (before.status === "loaded") {
    assert.equal(before.provider_revision, revision); assert.equal(before.cursor, String(count));
    assert.deepEqual(before.head, finalProjection);
  }
  assert.equal(original.status, "found");
  if (original.status === "found") {
    assert.equal(original.provider_revision, firstResult.provider_revision);
    assert.equal(original.cursor, firstResult.cursor); assert.deepEqual(original.receipts, first.receipts);
  }
  const retainedHistory = await historyDigest();
  // An identical historical intent returns its original result despite a stale
  // basis. Projection-, event- and receipt-only drift must conflict, not append.
  for (const change of ["none", "projection", "events", "receipts"] as const) {
    const input = structuredClone(first);
    if (change === "projection") input.next_projection.replay_marker = "different";
    if (change === "events") input.events = [{kind: "observation", index: -1}];
    if (change === "receipts") input.receipts = [{...receiptsAt(0)[0], replay_marker: "different"}];
    const replay = await reopened.commitAuthority(input);
    if (change === "none") assert.deepEqual(replay, firstResult);
    else {
      assert.equal(replay.status, "conflict", `${change}-only drift was accepted`);
      if (replay.status === "conflict") assert.equal(replay.conflict_kind, "operation_id_exists");
    }
    assert.deepEqual(await reopened.loadAuthority(), before, `${change} changed the later head`);
    assert.deepEqual(await reopened.readReceipt(first.operation_id), original, `${change} changed the original receipt`);
  }
  assert.equal(await historyDigest(), retainedHistory, "replay attempts changed retained history");
  assert.deepEqual(sourceIdentity(), source, "measurement source changed while running");
  return {schema_version: "loopx_local_provider_comparison_v0", provider: values.provider, workload: values.workload,
    source, node: process.version, sqlite: sqliteRuntimeIdentity(), platform: process.platform, arch: process.arch,
    commits: count, projection_json_bytes: Buffer.byteLength(JSON.stringify(finalProjection)), fill_ms: fillMs,
    commit_first_100: latency(commits.slice(0, 100)), commit_last_100: latency(commits.slice(-100)),
    warm_head: latency(loads), historical_receipt: latency(reads), scan_100: latency(scans), cold_process_head: latency(cold),
    post_fill_rss_bytes: postFillRss,
    final_store_bytes: fileBytes(), file_document_publication_bytes: values.provider === "file" ? filePublicationBytes : null,
    complete_record_and_receipt_checks: "passed", original_receipt_recovery_after_reopen: "passed",
    historical_replay_and_conflict_checks: "passed",
    limits: "bounded sequential store experiment; cold process includes module loading, not cold OS cache; RSS includes fixture and verification allocations, not a steady-state qualification; no CLI, concurrent writers, crash, soak or formal D2 qualification; File publication bytes are application bytes, not physical writes; SQLite WAL traffic is measured by the separate capacity runner"};
}
