import assert from "node:assert/strict";
import {spawn, spawnSync} from "node:child_process";
import {mkdtemp, rm} from "node:fs/promises";
import {readdirSync, symlinkSync} from "node:fs";
import {homedir, tmpdir} from "node:os";
import {basename, join} from "node:path";
import test from "node:test";
import {fileURLToPath} from "node:url";

import {canonicalAuthorityBytes} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

/**
 * OS/runtime matrix qualification for the SQLite authority store — the
 * remaining batch-3 ledger row on issue #4224 ("supported runtimes/platforms"
 * in `docs/reference/sqlite-authority-store.md`'s own gap list, "OS/runtime
 * coverage" in its qualification holds). Every assertion binds to that
 * reference document; no row is invented beyond it:
 *
 * - Runtime floor: "Both current local providers require Node >=22.22.3" and
 *   "The public Node minimum is now 22.22.3 for both File and optional SQLite
 *   use; SQLite also checks the actual embedded driver." A below-floor
 *   runtime (a stock older Node 22 release) must fail closed through the
 *   embedded-driver probe — "This intentionally rejects SQLite runtimes
 *   previously accepted by the statement-only probe, including vulnerable
 *   drivers shipped with older Node 22 releases" — and "an explicitly
 *   selected SQLite runtime never falls back to File when rejected": typed
 *   failures on every operation, no authority state created, no File-shaped
 *   artifacts in the store directory.
 * - Qualified reference: "The SQLite qualification reference is Node 22.22.3
 *   with SQLite 3.51.3." The full store contract (commits across the
 *   64-commit checkpoint window, byte-identical retry replay, head/receipt/
 *   paged-scan readback, archive audit, per-goal databases, WAL journal,
 *   `user_version=2`) must hold under a qualified runtime. The precise
 *   reference environment is distinguished from a generic qualified runner:
 *   the row records which serving pair actually exercised it, and
 *   LOOPX_TEST_SQLITE_FLOOR_NODE can pin the reference binary explicitly.
 * - Newer runtimes inside the documented envelope: "SQLite 3.51.3 and later
 *   3.x releases contain the WAL-reset concurrency fix" — a newer Node major
 *   whose embedded driver satisfies the documented admission lines must hold
 *   the same contract; a driver outside them must fail closed ("Unknown
 *   version strings or unverified vendor backports fail closed"). Both
 *   branches are the documented contract; the branch taken is decided by an
 *   independent raw-driver observation, never by trusting the probe.
 * - runtime_identity reporting: the info fields `node_version`,
 *   `sqlite_version`, `sqlite_authority_qualified` (plus
 *   `sqlite_source_id()` — "Record both sqlite_version() and
 *   sqlite_source_id(); the Node version alone is insufficient") must report
 *   the actually serving pair. The read-only identity probe "opens no
 *   database file and creates no authority state".
 *
 * Runtime selection is identity-based, never path-based: a second runtime
 * only counts for "newer runtime" or cross-runtime rows when the spawned
 * child's actually reported serving pair (node_version plus the raw-driver
 * sqlite_version, cross-checked against the launcher's own `--version`)
 * differs from the runner's reported pair. Two launchers of one executable —
 * direct call, symlink chains, hardlinks — report the same pair and can
 * never fabricate distinct coverage; the rows skip with the reason ("no
 * distinct qualifying second runtime available...") instead of passing.
 *
 * OS axis: this suite runs where it runs; every row pins the observed
 * `platform`/`arch` through the raw oracle. macOS (arm64) is the only local
 * host available to this author — Linux adds coverage wherever CI pins the
 * qualified runtime, and other platforms remain disclosed evidence gaps: an
 * unavailable environment is a gap, never a pass, so rows whose binaries are
 * absent skip with the precise reason instead of narrowing silently.
 */
const GOAL_ID = "goal-runtime-matrix";
// Duplicated from sqlite_authority_runtime_matrix_process.ts by design: the
// worker is spawned, never imported, so the wire contract lives on both sides
// (same rule as the disk-full and concurrent-writer tests).
const MATRIX_SCHEMA = "loopx_sqlite_runtime_matrix_v0";
const AUDIT_SCHEMA = "loopx_sqlite_authority_history_audit_v0";
const PROCESS_PATH = new URL("./sqlite_authority_runtime_matrix_process.ts", import.meta.url);
const NODE_FLOOR = [22, 22, 3];
// The documented qualification reference pair.
const REFERENCE_NODE = "v22.22.3";
const REFERENCE_SQLITE = "3.51.3";
const LEDGER_COUNT = 65;
const LEDGER_PADDING_BYTES = 512;
const CHILD_DEADLINE_MS = 30000;

interface MatrixCommand {
  seq: number;
  kind: "identity" | "store-identity" | "run-ledger" | "commit" | "load" | "receipt" | "scan"
    | "audit" | "files" | "engine" | "sibling-goal" | "exit";
  first?: number;
  count?: number;
  padding_bytes?: number;
  padding_char?: string;
  operation_id?: string;
  marker?: string;
  sequence?: number;
  expected_provider_revision?: string | null;
  after?: string | null;
  limit?: number;
  suffix?: string;
}

/** A request/response session with the runtime-matrix worker. Replies are
 * matched by seq over complete newline-delimited JSON lines; an unsolicited
 * protocol error fails the session instead of hanging the test. */
interface ChildSession {
  next(command: Omit<MatrixCommand, "seq">): Promise<Record<string, unknown>>;
  finish(): Promise<{code: number | null; signal: NodeJS.Signals | null; stderr: string}>;
}

async function spawnMatrixChild(t: test.TestContext, binary: string, directory: string): Promise<ChildSession> {
  const env = {...process.env};
  // Pin the direct-CLI repair-guidance shape documented for non-reused
  // runtimes; the managed-runtime variant has its own admission tests.
  delete env.LOOPX_EFFECT_RUNTIME_TOKEN;
  const child = spawn(binary, ["--no-warnings", "--experimental-sqlite", "--experimental-strip-types",
    fileURLToPath(PROCESS_PATH), directory, GOAL_ID], {stdio: ["pipe", "pipe", "pipe"], env});
  t.after(() => child.kill());
  let buffer = "", stderr = "";
  const replies = new Map<number, Record<string, unknown>>();
  const waiting: {seq: number; resolve: (value: Record<string, unknown>) => void; reject: (error: Error) => void}[] = [];
  child.stdout.on("data", data => {
    buffer += String(data);
    let newline = buffer.indexOf("\n");
    while (newline >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (line !== "" && line !== "ready") {
        let parsed: Record<string, unknown>;
        try { parsed = JSON.parse(line) as Record<string, unknown>; }
        catch { assert.fail(`unparseable child line: ${line.slice(0, 200)}`); }
        if (parsed.kind === "protocol-error" || parsed.kind === "command-error") {
          const pending = waiting.shift();
          if (pending) pending.reject(new Error(JSON.stringify(parsed)));
          else replies.set(-1, parsed);
        } else {
          const waiter = waiting.find(entry => entry.seq === Number(parsed.seq));
          if (waiter) {waiting.splice(waiting.indexOf(waiter), 1); waiter.resolve(parsed);}
          else replies.set(Number(parsed.seq), parsed);
        }
      }
      newline = buffer.indexOf("\n");
    }
  });
  child.stderr.on("data", data => {stderr += String(data);});
  const closed = new Promise<{code: number | null; signal: NodeJS.Signals | null}>(resolve =>
    child.on("close", (code, signal) => resolve({code, signal})));
  let seq = 0;
  return {
    next: command => {
      const current = ++seq;
      const reply = replies.has(current)
        ? Promise.resolve(replies.get(current)!)
        : new Promise<Record<string, unknown>>((resolve, reject) => {
          const timer = setTimeout(() => reject(new Error(
            `child reply ${current} deadline exceeded; stderr: ${stderr}`)), CHILD_DEADLINE_MS);
          waiting.push({seq: current, resolve: value => {clearTimeout(timer); resolve(value);},
            reject: error => {clearTimeout(timer); reject(error);}});
        });
      child.stdin.write(`${JSON.stringify({...command, seq: current})}\n`);
      return reply;
    },
    finish: async () => {
      child.stdin.end();
      const done = await closed;
      return {...done, stderr};
    },
  };
}

/** One planned commit plus the exact proof this test expects to read back. */
interface LedgerEntry {
  operationId: string;
  projection: JsonObject;
  events: readonly JsonObject[];
  receipts: readonly JsonObject[];
}

function matrixEntry(sequence: number): LedgerEntry {
  const operationId = `matrix-${String(sequence).padStart(3, "0")}`;
  return {
    operationId,
    projection: {schema_version: MATRIX_SCHEMA, marker: "matrix", sequence,
      runtime_padding: "m".repeat(LEDGER_PADDING_BYTES)},
    events: [{kind: "runtime-matrix", operation_id: operationId, sequence}],
    receipts: [{operation_id: operationId, sequence}],
  };
}

function siblingEntry(): LedgerEntry {
  return {
    operationId: "sibling-001",
    projection: {schema_version: MATRIX_SCHEMA, marker: "sibling", sequence: 1},
    events: [{kind: "runtime-matrix-sibling", sequence: 1}],
    receipts: [{operation_id: "sibling-001", sequence: 1}],
  };
}

function ledgerBytes(entry: LedgerEntry): string {
  return canonicalAuthorityBytes({projection: entry.projection, events: entry.events,
    receipts: entry.receipts}).toString("utf8");
}

function nodeVersionNumbers(version: string): number[] {
  return version.trim().replace(/^v/, "").split(".").map(Number);
}

function nodeAtLeast(version: string, floor: readonly number[]): boolean {
  const parts = nodeVersionNumbers(version);
  for (const [index, bound] of floor.entries()) {
    const part = parts[index] ?? 0;
    if (part !== bound) return part > bound;
  }
  return true;
}

/** The doc's admitted SQLite release lines, restated here as an independent
 * comparator: "SQLite 3.51.3 and later 3.x releases contain the WAL-reset
 * concurrency fix. The fixed 3.44.x (3.44.6+) and 3.50.x (3.50.7+) backport
 * lines are also admitted." Deliberately not `hasSqliteWalResetFix`: the
 * oracle must not be the function under test. */
function docAdmittedSqlite(version: string): boolean {
  const match = /^(\d+)\.(\d+)\.(\d+)$/.exec(version);
  if (!match) return false;
  const [major, minor, patch] = match.slice(1).map(Number);
  if (major !== 3) return false;
  return minor > 51 || (minor === 51 && patch >= 3) ||
    (minor === 50 && patch >= 7) || (minor === 44 && patch >= 6);
}

function probeBinaryVersion(path: string): string | null {
  const probe = spawnSync(path, ["--version"], {encoding: "utf8", timeout: 10000});
  if (probe.error || probe.status !== 0) return null;
  const version = probe.stdout.trim();
  return /^v\d+\.\d+\.\d+$/.test(version) ? version : null;
}

/** The worker needs the repository's runtime flags; a candidate that rejects
 * them (for example a Node older than the flags themselves) is not a usable
 * matrix binary and is skipped loudly rather than hanging the session. */
function binaryAcceptsRuntimeFlags(path: string): boolean {
  const probe = spawnSync(path, ["--no-warnings", "--experimental-sqlite", "--experimental-strip-types",
    "--input-type=module", "-e", ""], {encoding: "utf8", timeout: 10000});
  return probe.status === 0;
}

/** What a runtime actually reports when it serves the suite: the spawned
 * child's own node_version and raw-driver SQLite observation, together with
 * the launcher's `--version` output. This — not any launcher path — is the
 * identity matrix rows are selected and attributed by. */
interface RuntimeProbe {
  path: string;
  binaryVersion: string;
  nodeVersion: string;
  sqliteVersion: string | null;
  sqliteSourceId: string | null;
  finalization: boolean | null;
  driverAvailable: boolean;
  platform: string;
  arch: string;
}

const runtimeProbeCache = new Map<string, Promise<RuntimeProbe>>();

async function probeRuntime(t: test.TestContext, path: string): Promise<RuntimeProbe> {
  const cached = runtimeProbeCache.get(path);
  if (cached) return cached;
  const probed = (async () => {
    const binaryVersion = probeBinaryVersion(path);
    if (binaryVersion === null) throw new Error(`cannot probe a Node version from ${path}`);
    const directory = await mkdtemp(join(tmpdir(), "sqlite-runtime-matrix-probe-"));
    try {
      const session = await spawnMatrixChild(t, path, directory);
      const reply = (await session.next({kind: "identity"})) as unknown as IdentityReply;
      await session.next({kind: "exit"});
      const closed = await session.finish();
      assert.equal(closed.code, 0, `identity probe of ${path} exited cleanly; stderr: ${closed.stderr}`);
      return {path, binaryVersion,
        nodeVersion: reply.oracle.node_version, sqliteVersion: reply.oracle.sqlite_version,
        sqliteSourceId: reply.oracle.sqlite_source_id, finalization: reply.oracle.synchronous_statement_finalization,
        driverAvailable: reply.oracle.driver_available, platform: reply.oracle.platform,
        arch: reply.oracle.arch};
    } finally { await rm(directory, {recursive: true, force: true}); }
  })();
  runtimeProbeCache.set(path, probed);
  return probed;
}

function probeIsQualified(probe: RuntimeProbe): boolean {
  return probe.driverAvailable && probe.finalization === true && docAdmittedSqlite(String(probe.sqliteVersion));
}

/** Two runtimes form a distinct pair exactly when their reported serving
 * pairs differ: node_version or the embedded sqlite_version. Launchers of
 * one executable (direct call, symlinks, hardlinks) report the same pair and
 * are never distinct, whatever their paths claim. */
function isDistinctPair(a: RuntimeProbe, b: RuntimeProbe): boolean {
  return a.nodeVersion !== b.nodeVersion || a.sqliteVersion !== b.sqliteVersion;
}

function pairsLabel(runner: RuntimeProbe, second: RuntimeProbe): string {
  return `${second.nodeVersion}/${String(second.sqliteVersion)} vs runner ` +
    `${runner.nodeVersion}/${String(runner.sqliteVersion)}`;
}

type SecondRuntimeSelection = {status: "selected"; probe: RuntimeProbe} | {status: "unavailable"; reason: string};

/** Select a second runtime for the newer-major and handoff rows by the
 * candidates' actually reported identities, never by launcher path: the
 * runner's probe is compared with each candidate's probe, and only a
 * distinct, consistent, (optionally) qualified serving pair may form the
 * row. When nothing distinct is available the selection returns the precise
 * reason so the row can skip honestly instead of fabricating coverage. */
async function selectSecondRuntime(t: test.TestContext, runner: RuntimeProbe,
  options: {newerMajorOnly: boolean; requireQualified: boolean}): Promise<SecondRuntimeSelection> {
  const override = process.env.LOOPX_TEST_SQLITE_NEWER_MAJOR_NODE;
  const candidates: string[] = override ? [override] : [];
  if (!override) {
    const nvmRoot = join(homedir(), ".nvm", "versions", "node");
    try { candidates.push(...readdirSync(nvmRoot).map(entry => join(nvmRoot, entry, "bin", "node"))); }
    catch { /* nvm is not installed; the row skips with a disclosed reason */ }
    candidates.push("/opt/homebrew/bin/node", "/usr/local/bin/node");
  }
  const rejections: string[] = [];
  for (const path of candidates) {
    const binaryVersion = probeBinaryVersion(path);
    if (binaryVersion === null) { rejections.push(`${path}: not a usable Node binary`); continue; }
    if (options.newerMajorOnly && (nodeVersionNumbers(binaryVersion)[0] ?? 0) <= NODE_FLOOR[0]!) {
      rejections.push(`${path}: ${binaryVersion} is not newer than the floor major`);
      continue;
    }
    if (!binaryAcceptsRuntimeFlags(path)) { rejections.push(`${path}: rejects the repository runtime flags`); continue; }
    const probe = await probeRuntime(t, path);
    if (probe.binaryVersion !== probe.nodeVersion) {
      rejections.push(`${path}: --version reports ${probe.binaryVersion} but the child reports ${probe.nodeVersion}`);
      continue;
    }
    if (!isDistinctPair(probe, runner)) {
      rejections.push(`${path}: reports the same serving pair as the runner ` +
        `(${probe.nodeVersion}/${String(probe.sqliteVersion)})`);
      continue;
    }
    if (options.requireQualified && !probeIsQualified(probe)) {
      rejections.push(`${path}: serving pair ${probe.nodeVersion}/${String(probe.sqliteVersion)} is not qualified`);
      continue;
    }
    return {status: "selected", probe};
  }
  return {status: "unavailable", reason: "no distinct qualifying second runtime available (runner reports " +
    `${runner.nodeVersion}/${String(runner.sqliteVersion)}${rejections.length > 0 ? `; ${rejections.join("; ")}` : ""})`};
}

/** A locally available Node below the documented 22.22.3 floor (stock older
 * Node 22 releases are exactly the "vulnerable drivers shipped with older
 * Node 22 releases" the reference calls out). Overridable via
 * LOOPX_TEST_SQLITE_BELOW_FLOOR_NODE; absent candidates are a disclosed gap,
 * never an invented row. */
function resolveBelowFloorBinary(): {path: string; version: string} | null {
  const override = process.env.LOOPX_TEST_SQLITE_BELOW_FLOOR_NODE;
  const candidates = override ? [override] : ["/usr/local/bin/node", "/opt/homebrew/bin/node"];
  for (const path of candidates) {
    const version = probeBinaryVersion(path);
    if (version !== null && !nodeAtLeast(version, NODE_FLOOR) && binaryAcceptsRuntimeFlags(path)) {
      return {path, version};
    }
  }
  return null;
}

interface RawDriverOracle {
  node_version: string;
  platform: string;
  arch: string;
  driver_available: boolean;
  sqlite_version: string | null;
  sqlite_source_id: string | null;
  synchronous_statement_finalization: boolean | null;
}

interface IdentityReply {
  identity: {schema_version: string; node_version: string; sqlite_available: boolean;
    sqlite_version: string | null; sqlite_source_id: string | null;
    synchronous_statement_finalization: boolean | null; sqlite_authority_qualified: boolean;
    unavailable_reason: string | null};
  oracle: RawDriverOracle;
}

/** The documented `runtime_identity` fields must report the actually serving
 * pair: node_version equals the spawned binary's own `--version` output,
 * sqlite_version/sqlite_source_id equal a direct raw-driver query in the
 * same process, and `sqlite_authority_qualified` equals the qualification
 * the doc derives from that observed driver (admitted release line plus
 * synchronous statement finalization) — the Node version alone never decides
 * it. When `expected` is given (the identity this row was selected by), the
 * session must report exactly that pair, so coverage is attributed to the
 * runtime that actually served it. */
function assertIdentityRow(reply: Record<string, unknown>, binaryVersion: string, label: string,
  expected?: RuntimeProbe): IdentityReply {
  const shaped = reply as unknown as IdentityReply;
  const {identity, oracle} = shaped;
  assert.equal(oracle.node_version, binaryVersion.trim(), `${label}: raw oracle node version`);
  assert.equal(identity.node_version, oracle.node_version, `${label}: identity node_version`);
  assert.equal(identity.sqlite_available, oracle.driver_available, `${label}: driver availability`);
  assert.equal(identity.sqlite_version, oracle.sqlite_version, `${label}: sqlite_version reports the embedded driver`);
  assert.equal(identity.sqlite_source_id, oracle.sqlite_source_id, `${label}: sqlite_source_id reports the embedded driver`);
  assert.equal(identity.synchronous_statement_finalization, oracle.synchronous_statement_finalization,
    `${label}: finalization observation`);
  if (expected) {
    assert.equal(identity.node_version, expected.nodeVersion,
      `${label}: session reports the selected runtime's node version`);
    assert.equal(identity.sqlite_version, expected.sqliteVersion,
      `${label}: session reports the selected runtime's sqlite version`);
  }
  if (oracle.driver_available) {
    assert.match(String(oracle.sqlite_version), /^\d+\.\d+\.\d+$/, `${label}: observed driver version shape`);
    assert.ok(typeof oracle.sqlite_source_id === "string" && oracle.sqlite_source_id.length > 0,
      `${label}: observed driver source id`);
    assert.equal(identity.unavailable_reason, null, `${label}: no unavailable reason with a live driver`);
  }
  const expectedQualified = oracle.driver_available === true &&
    oracle.synchronous_statement_finalization === true && docAdmittedSqlite(String(oracle.sqlite_version));
  assert.equal(identity.sqlite_authority_qualified, expectedQualified,
    `${label}: qualification follows the observed driver, not the Node version string`);
  // OS-axis evidence pin: every child of this suite runs on this host.
  assert.equal(oracle.platform, process.platform, `${label}: platform`);
  assert.equal(oracle.arch, process.arch, `${label}: arch`);
  return shaped;
}

/** Every store operation must fail closed with the typed protocol violation
 * that names the observed driver pair and the documented floor, and nothing
 * may be created — no database, and no File-provider artifacts either (the
 * documented "never falls back to File" is observable as an empty store
 * directory together with these failures). */
async function assertFailClosedRow(session: ChildSession, oracle: RawDriverOracle, label: string): Promise<void> {
  const driverPair = oracle.driver_available
    ? `SQLite ${oracle.sqlite_version}, synchronous statement finalization=${oracle.synchronous_statement_finalization}`
    : "node:sqlite is unavailable in this runtime";
  // The unqualified-driver refusal names the WAL-reset release lines; the
  // missing-driver refusal names the unavailable module instead. Both are the
  // documented probe branches.
  const driverRequirement = oracle.driver_available ? /WAL-reset fix/ : /node:sqlite is unavailable/;
  const probes: [Omit<MatrixCommand, "seq">, string][] = [
    [{kind: "store-identity"}, "storeIdentity"],
    [{kind: "load"}, "loadAuthority"],
    [{kind: "commit", operation_id: "must-not-apply", marker: "matrix", sequence: 1,
      padding_bytes: LEDGER_PADDING_BYTES, padding_char: "m", expected_provider_revision: null}, "commitAuthority"],
    [{kind: "receipt", operation_id: "must-not-apply"}, "readReceipt"],
    [{kind: "scan", after: null, limit: 100}, "scanCommitted"],
    [{kind: "audit"}, "verifyAuthorityHistory"],
  ];
  for (const [command, name] of probes) {
    const reply = await session.next(command);
    const result = (reply.result ?? reply) as {status?: string; reason_code?: string; reason?: string;
      provider_revision?: string; cursor?: string};
    assert.equal(result.status, "failed", `${label}: ${name} status`);
    assert.equal(result.reason_code, "provider_protocol_violation", `${label}: ${name} reason_code`);
    assert.ok(typeof result.reason === "string" && result.reason.length > 0, `${label}: ${name} reason`);
    // The refusal names the observed embedded driver and the documented floor.
    assert.match(result.reason!, new RegExp(driverPair.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")),
      `${label}: ${name} reason names the observed driver pair`);
    assert.match(result.reason!, /Node 22\.22\.3 or newer/, `${label}: ${name} reason names the floor`);
    assert.match(result.reason!, driverRequirement, `${label}: ${name} reason names the driver requirement`);
    // Never half-works: no success payload ever escapes the refused runtime.
    assert.ok(!("provider_revision" in result) && !("cursor" in result), `${label}: ${name} leaks no revision`);
  }
  const files = await session.next({kind: "files"});
  assert.equal(files.path_exists, false, `${label}: no authority database created`);
  assert.equal(files.directory_exists, true, `${label}: the parent temp directory still exists`);
  assert.deepEqual(files.entries, [], `${label}: store directory stays empty (no File fallback artifacts)`);
  const engine = await session.next({kind: "engine"});
  assert.equal(engine.exists, false, `${label}: no engine file to inspect`);
}

/** The full documented store contract under a qualified runtime: commits
 * across the 64-commit checkpoint window, byte-identical retry replay, exact
 * head/receipt/paged-scan readback against the authored ledger, archive
 * audit, per-goal databases, WAL journal and `user_version=2` through a raw
 * engine oracle. */
async function exerciseQualifiedContract(t: test.TestContext, session: ChildSession,
  binaryVersion: string, label: string, selected?: RuntimeProbe): Promise<IdentityReply> {
  const identityReply = assertIdentityRow(await session.next({kind: "identity"}), binaryVersion, label, selected);
  assert.equal(identityReply.identity.sqlite_authority_qualified, true,
    `${label}: the contract row requires a qualified runtime`);

  // The identity probe is read-only: it created no authority state.
  const filesAfterProbe = await session.next({kind: "files"});
  assert.equal(filesAfterProbe.path_exists, false, `${label}: probe created no database`);
  assert.deepEqual(filesAfterProbe.entries, [], `${label}: probe created no authority state`);

  const storeIdentity = (await session.next({kind: "store-identity"})).result as
    {status: string; store_identity?: string};
  assert.equal(storeIdentity.status, "available", `${label}: storeIdentity status`);
  assert.match(storeIdentity.store_identity ?? "", /^sqlite:[0-9a-f]{32}$/, `${label}: store identity shape`);

  // Ledger across the checkpoint window: cursor 1 and cursor 65 are both
  // checkpoint-bearing, so 65 commits retain two checkpoints.
  const ledger = Array.from({length: LEDGER_COUNT}, (_unused, index) => matrixEntry(index + 1));
  const run = await session.next({kind: "run-ledger", first: 1, count: LEDGER_COUNT,
    padding_bytes: LEDGER_PADDING_BYTES, padding_char: "m"});
  const results = run.results as {sequence: number; operation_id: string; status: string;
    provider_revision: string | null; cursor: string | null}[];
  assert.equal(results.length, LEDGER_COUNT, `${label}: ledger length`);
  const incarnation = new Set<string>();
  for (const [index, result] of results.entries()) {
    assert.equal(result.status, "applied", `${label}: commit ${index + 1} status`);
    assert.equal(result.cursor, String(index + 1), `${label}: commit ${index + 1} cursor`);
    // Provider revisions combine the database incarnation with the monotonic
    // cursor sequence; one incarnation serves the whole ledger.
    assert.ok(result.provider_revision !== null, `${label}: commit ${index + 1} revision`);
    incarnation.add(result.provider_revision!.split(":").slice(0, 2).join(":"));
    assert.ok(result.provider_revision!.endsWith(`:${index + 1}`), `${label}: commit ${index + 1} revision cursor`);
  }
  assert.equal(incarnation.size, 1, `${label}: one database incarnation`);

  // Byte-identical retry replays the original position without a new write.
  const retry = (await session.next({kind: "commit", operation_id: "matrix-040", marker: "matrix",
    sequence: 40, padding_bytes: LEDGER_PADDING_BYTES, padding_char: "m",
    expected_provider_revision: results[38]!.provider_revision})).result as
    {status: string; provider_revision?: string; cursor?: string};
  assert.equal(retry.status, "applied", `${label}: retry status`);
  assert.equal(retry.cursor, "40", `${label}: retry cursor`);
  assert.equal(retry.provider_revision, results[39]!.provider_revision, `${label}: retry revision`);

  const load = (await session.next({kind: "load"})).result as {status: string; cursor?: string;
    provider_revision?: string; head?: JsonObject};
  assert.equal(load.status, "loaded", `${label}: load status`);
  assert.equal(load.cursor, String(LEDGER_COUNT), `${label}: load cursor`);
  assert.equal(load.provider_revision, results[LEDGER_COUNT - 1]!.provider_revision, `${label}: load revision`);
  assert.equal(canonicalAuthorityBytes(load.head!).toString("utf8"),
    canonicalAuthorityBytes(ledger[LEDGER_COUNT - 1]!.projection).toString("utf8"), `${label}: head bytes`);

  const receipt = (await session.next({kind: "receipt", operation_id: "matrix-033"})).result as
    {status: string; receipts?: JsonObject[]};
  assert.equal(receipt.status, "found", `${label}: receipt status`);
  assert.equal(canonicalAuthorityBytes(receipt.receipts!).toString("utf8"),
    canonicalAuthorityBytes(ledger[32]!.receipts).toString("utf8"), `${label}: original receipt bytes`);

  // Full paged scan: page size 7 deliberately misaligns with the 64-commit
  // checkpoint window; every row must equal the authored ledger byte for byte.
  const read: {cursor: string; operationId: string; bytes: string}[] = [];
  let after: string | null = null;
  for (;;) {
    const page = (await session.next({kind: "scan", after, limit: 7})).result as {status: string;
      has_more?: boolean; transactions?: {cursor: string; operation_id: string; projection: JsonObject;
        events: JsonObject[]; receipts: JsonObject[]}[]};
    assert.equal(page.status, "page", `${label}: scan page status`);
    if (page.status === "page" && (page.transactions?.length ?? 0) === 0) break;
    for (const transaction of page.transactions!) {
      read.push({cursor: transaction.cursor, operationId: transaction.operation_id,
        bytes: canonicalAuthorityBytes({projection: transaction.projection, events: transaction.events,
          receipts: transaction.receipts}).toString("utf8")});
    }
    after = page.transactions![page.transactions!.length - 1]!.cursor;
  }
  assert.deepEqual(read.map(row => row.cursor), ledger.map((_entry, index) => String(index + 1)),
    `${label}: scan cursor order`);
  assert.deepEqual(read.map(row => row.operationId), ledger.map(entry => entry.operationId),
    `${label}: scan operation order`);
  for (const [index, row] of read.entries()) {
    assert.equal(row.bytes, ledgerBytes(ledger[index]!), `${label}: scan row ${index} bytes`);
  }

  const audit = (await session.next({kind: "audit"})).result as {schema_version: string; status: string;
    commits: number; checkpoints: number};
  assert.deepEqual(audit, {schema_version: AUDIT_SCHEMA, status: "verified",
    commits: LEDGER_COUNT, checkpoints: 2}, `${label}: archive audit`);

  // Raw engine oracle, read through node:sqlite directly.
  const engine = (await session.next({kind: "engine"})) as {exists: boolean; user_version?: number;
    journal_mode?: string; integrity?: string[]};
  assert.equal(engine.exists, true, `${label}: engine file exists`);
  assert.equal(engine.user_version, 2, `${label}: version-2 schema`);
  assert.equal(engine.journal_mode, "wal", `${label}: WAL journal mode`);
  assert.deepEqual(engine.integrity, ["ok"], `${label}: engine integrity`);

  // Per-goal separation: a second goal in the same directory gets its own
  // database with its own head.
  const sibling = await session.next({kind: "sibling-goal", suffix: "-sibling"});
  const siblingEntryValue = siblingEntry();
  assert.notEqual(sibling.sibling_path, sibling.main_path, `${label}: distinct per-goal databases`);
  const siblingCommit = sibling.commit as {status: string; cursor?: string};
  assert.equal(siblingCommit.status, "applied", `${label}: sibling commit`);
  assert.equal(siblingCommit.cursor, "1", `${label}: sibling cursor`);
  const siblingLoad = sibling.load as {status: string; head?: JsonObject};
  assert.equal(siblingLoad.status, "loaded", `${label}: sibling load`);
  assert.equal(canonicalAuthorityBytes(siblingLoad.head!).toString("utf8"),
    canonicalAuthorityBytes(siblingEntryValue.projection).toString("utf8"), `${label}: sibling head bytes`);
  // Exactly the two per-goal databases exist; anything else in the directory
  // is only the documented `-wal`/`-shm` companion of an open WAL database.
  const files = await session.next({kind: "files"});
  const databases = (files.entries as string[])
    .filter(entry => !entry.endsWith("-wal") && !entry.endsWith("-shm"));
  assert.deepEqual(databases.map(entry => basename(entry)).sort(),
    [basename(String(sibling.main_path)), basename(String(sibling.sibling_path))].sort(),
    `${label}: exactly the two per-goal databases exist`);
  const allowed = new Set([basename(String(sibling.main_path)), basename(String(sibling.sibling_path)),
    `${basename(String(sibling.main_path))}-wal`, `${basename(String(sibling.main_path))}-shm`,
    `${basename(String(sibling.sibling_path))}-wal`, `${basename(String(sibling.sibling_path))}-shm`]);
  for (const entry of files.entries as string[]) {
    assert.ok(allowed.has(entry), `${label}: unexpected store directory entry ${entry}`);
  }
  assert.equal(files.path_exists, true, `${label}: main database exists`);

  const exited = await session.next({kind: "exit"});
  assert.equal(exited.kind, "exited");
  const closed = await session.finish();
  assert.equal(closed.code, 0, `${label}: worker exit code; stderr: ${closed.stderr}`);
  assert.equal(closed.stderr, "", `${label}: worker stderr`);
  return identityReply;
}

test("OS/runtime matrix: a qualified runtime holds the full SQLite authority-store contract, with the reference floor distinguished from a generic runner",
  {timeout: 120000}, async t => {
    const directory = await mkdtemp(join(tmpdir(), "sqlite-runtime-matrix-floor-"));
    t.after(() => rm(directory, {recursive: true, force: true}));
    // The precise reference environment (the documented Node 22.22.3 floor
    // with an admitted driver) can be pinned explicitly; unpinned, the
    // suite's own runner serves the row and is attributed by what it
    // actually reports — never assumed to be the floor just because the
    // lane is qualified.
    const pinned = process.env.LOOPX_TEST_SQLITE_FLOOR_NODE;
    const target = pinned ?? process.execPath;
    const probe = await probeRuntime(t, target);
    if (pinned) {
      if (probe.nodeVersion !== REFERENCE_NODE || !probeIsQualified(probe)) {
        t.skip(`pinned floor binary did not report the documented reference identity (observed ` +
          `${probe.nodeVersion} / ${String(probe.sqliteVersion)} on ${probe.platform}/${probe.arch})`);
        return;
      }
    } else if (!probeIsQualified(probe)) {
      assert.fail(`this suite must run under the documented qualified runtime (Node >=22.22.3 with an ` +
        `admitted embedded SQLite driver) or pin LOOPX_TEST_SQLITE_FLOOR_NODE; observed ` +
        `${probe.nodeVersion} / ${String(probe.sqliteVersion)} on ${probe.platform}/${probe.arch}`);
    }
    const isReferencePair = probe.nodeVersion === REFERENCE_NODE && probe.sqliteVersion === REFERENCE_SQLITE;
    const label = isReferencePair
      ? `runner(reference floor ${probe.nodeVersion} / ${String(probe.sqliteVersion)})`
      : `runner(generic qualified ${probe.nodeVersion} / ${String(probe.sqliteVersion)}; the documented ` +
        `reference floor is Node ${REFERENCE_NODE} / SQLite ${REFERENCE_SQLITE})`;
    const session = await spawnMatrixChild(t, target, directory);
    await exerciseQualifiedContract(t, session, probe.binaryVersion, label, probe);
  });

test("OS/runtime matrix: a below-floor Node runtime fails closed through the embedded-driver probe",
  {timeout: 60000}, async t => {
    const belowFloor = resolveBelowFloorBinary();
    if (belowFloor === null) {
      // A disclosed evidence gap, never an invented row: this host exposes no
      // Node below the documented floor.
      t.skip("no locally available Node below the 22.22.3 floor (set LOOPX_TEST_SQLITE_BELOW_FLOOR_NODE)");
      return;
    }
    const directory = await mkdtemp(join(tmpdir(), "sqlite-runtime-matrix-below-"));
    t.after(() => rm(directory, {recursive: true, force: true}));
    const session = await spawnMatrixChild(t, belowFloor.path, directory);
    const identityReply = assertIdentityRow(await session.next({kind: "identity"}), belowFloor.version, "below-floor");
    const oracle = identityReply.oracle;
    if (oracle.driver_available && oracle.synchronous_statement_finalization === true &&
      docAdmittedSqlite(String(oracle.sqlite_version))) {
      // The documented below-floor rejection is the vulnerable driver shipped
      // with stock older Node 22 releases. A custom below-floor runtime that
      // embeds an admitted driver is outside the documented matrix; surface
      // it instead of silently passing or failing the row.
      t.skip(`below-floor binary ${belowFloor.version} embeds an admitted SQLite ` +
        `${String(oracle.sqlite_version)}; the stock older-Node driver rejection does not apply`);
      return;
    }
    await assertFailClosedRow(session, oracle, "below-floor");
    const exited = await session.next({kind: "exit"});
    assert.equal(exited.kind, "exited");
    const closed = await session.finish();
    assert.equal(closed.code, 0, closed.stderr);
  });

test("OS/runtime matrix: a distinct newer-major Node runtime inside the documented envelope holds the same contract",
  {timeout: 120000}, async t => {
    const runner = await probeRuntime(t, process.execPath);
    const selection = await selectSecondRuntime(t, runner, {newerMajorOnly: true, requireQualified: false});
    if (selection.status === "unavailable") {
      t.skip(selection.reason);
      return;
    }
    // Prove distinctness from the actually reported serving pairs before any
    // coverage may be asserted; an alias of the runner skips honestly here.
    if (!isDistinctPair(runner, selection.probe)) {
      t.skip(`both entrypoints report the same serving pair ${selection.probe.nodeVersion}/` +
        `${String(selection.probe.sqliteVersion)}; no distinct newer-major coverage formed`);
      return;
    }
    const label = `newer-major(${pairsLabel(runner, selection.probe)})`;
    const directory = await mkdtemp(join(tmpdir(), "sqlite-runtime-matrix-newer-"));
    t.after(() => rm(directory, {recursive: true, force: true}));
    const session = await spawnMatrixChild(t, selection.probe.path, directory);
    const identityReply = assertIdentityRow(await session.next({kind: "identity"}),
      selection.probe.binaryVersion, label, selection.probe);
    const oracle = identityReply.oracle;
    if (oracle.driver_available && oracle.synchronous_statement_finalization === true &&
      docAdmittedSqlite(String(oracle.sqlite_version))) {
      // Inside the documented envelope ("SQLite 3.51.3 and later 3.x"):
      // the full contract must hold on the newer runtime.
      await exerciseQualifiedContract(t, session, selection.probe.binaryVersion, label, selection.probe);
      return;
    }
    // Outside the admitted lines, the documented envelope is fail-closed
    // ("Unknown version strings or unverified vendor backports fail
    // closed") — also a contract-conformant row, asserted as such.
    await assertFailClosedRow(session, oracle, label);
    const exited = await session.next({kind: "exit"});
    assert.equal(exited.kind, "exited");
    const closed = await session.finish();
    assert.equal(closed.code, 0, closed.stderr);
  });

test("OS/runtime matrix: one authority database serves across two distinct qualified runtimes on this host",
  {timeout: 120000}, async t => {
    const runner = await probeRuntime(t, process.execPath);
    if (!probeIsQualified(runner)) {
      assert.fail(`the handoff row needs a qualified suite runner; observed ${runner.nodeVersion} / ` +
        `${String(runner.sqliteVersion)} on ${runner.platform}/${runner.arch}`);
    }
    const selection = await selectSecondRuntime(t, runner, {newerMajorOnly: false, requireQualified: true});
    if (selection.status === "unavailable") {
      t.skip(selection.reason);
      return;
    }
    const second = selection.probe;
    // Prove the two sides are distinct from their reported serving pairs
    // before the takeover flow may assert cross-runtime coverage.
    if (!isDistinctPair(runner, second)) {
      t.skip(`both entrypoints report the same serving pair ${second.nodeVersion}/` +
        `${String(second.sqliteVersion)}; no distinct cross-runtime coverage formed`);
      return;
    }
    const pairLabel = `handoff(${pairsLabel(runner, second)})`;
    const directory = await mkdtemp(join(tmpdir(), "sqlite-runtime-matrix-handoff-"));
    t.after(() => rm(directory, {recursive: true, force: true}));
    const ledger = Array.from({length: 33}, (_unused, index) => matrixEntry(index + 1));

    // The runner-side runtime authors the first 33 commits.
    const first = await spawnMatrixChild(t, process.execPath, directory);
    const firstIdentity = assertIdentityRow(await first.next({kind: "identity"}), runner.binaryVersion,
      `${pairLabel} first`, runner);
    assert.equal(firstIdentity.identity.sqlite_authority_qualified, true,
      `${pairLabel}: handoff starts from a qualified runtime`);
    const run = (await first.next({kind: "run-ledger", first: 1, count: 33,
      padding_bytes: LEDGER_PADDING_BYTES, padding_char: "m"})).results as
      {sequence: number; status: string; provider_revision: string | null; cursor: string | null}[];
    for (const [index, result] of run.entries()) {
      assert.equal(result.status, "applied", `${pairLabel} first-side commit ${index + 1}`);
      assert.equal(result.cursor, String(index + 1));
    }
    await first.next({kind: "exit"});
    const firstClosed = await first.finish();
    assert.equal(firstClosed.code, 0, firstClosed.stderr);

    // The distinct second runtime takes over the same database and continues it.
    const secondSession = await spawnMatrixChild(t, second.path, directory);
    const secondIdentity = assertIdentityRow(await secondSession.next({kind: "identity"}), second.binaryVersion,
      `${pairLabel} second`, second);
    assert.equal(secondIdentity.identity.sqlite_authority_qualified, true,
      `${pairLabel}: handoff continues on a qualified runtime`);
    const loadOnTakeover = (await secondSession.next({kind: "load"})).result as {status: string; cursor?: string;
      head?: JsonObject};
    assert.equal(loadOnTakeover.status, "loaded", `${pairLabel}: second runtime loads the first runtime's head`);
    assert.equal(loadOnTakeover.cursor, "33", `${pairLabel}: takeover cursor`);
    assert.equal(canonicalAuthorityBytes(loadOnTakeover.head!).toString("utf8"),
      canonicalAuthorityBytes(ledger[32]!.projection).toString("utf8"), `${pairLabel}: takeover head bytes`);
    const continuation = (await secondSession.next({kind: "run-ledger", first: 34, count: 3,
      padding_bytes: LEDGER_PADDING_BYTES, padding_char: "m"})).results as
      {sequence: number; status: string; cursor: string | null; provider_revision: string | null}[];
    for (const [offset, result] of continuation.entries()) {
      assert.equal(result.status, "applied", `${pairLabel} second-side commit ${34 + offset}`);
      assert.equal(result.cursor, String(34 + offset));
    }
    const newerAudit = (await secondSession.next({kind: "audit"})).result as {schema_version: string; status: string;
      commits: number; checkpoints: number};
    assert.deepEqual(newerAudit, {schema_version: AUDIT_SCHEMA, status: "verified", commits: 36, checkpoints: 1},
      `${pairLabel}: second runtime audits the combined history`);
    await secondSession.next({kind: "exit"});
    const secondClosed = await secondSession.finish();
    assert.equal(secondClosed.code, 0, secondClosed.stderr);

    // The runner-side runtime reads back what the second runtime committed.
    // This is same-host runtime-axis evidence only: the separate "supported
    // upgrades/rollback" qualification hold stays missing.
    const third = await spawnMatrixChild(t, process.execPath, directory);
    const finalLoad = (await third.next({kind: "load"})).result as {status: string; cursor?: string;
      head?: JsonObject; provider_revision?: string};
    assert.equal(finalLoad.status, "loaded", `${pairLabel}: first runtime reloads after the second runtime's commits`);
    assert.equal(finalLoad.cursor, "36", `${pairLabel}: final cursor`);
    assert.equal(canonicalAuthorityBytes(finalLoad.head!).toString("utf8"),
      canonicalAuthorityBytes(matrixEntry(36).projection).toString("utf8"), `${pairLabel}: final head bytes`);
    const floorAudit = (await third.next({kind: "audit"})).result as {status: string; commits: number};
    assert.equal(floorAudit.status, "verified", `${pairLabel}: final audit`);
    assert.equal(floorAudit.commits, 36, `${pairLabel}: final audit commits`);
    const floorReceipt = (await third.next({kind: "receipt", operation_id: "matrix-035"})).result as
      {status: string; receipts?: JsonObject[]};
    assert.equal(floorReceipt.status, "found", `${pairLabel}: cross-runtime receipt`);
    assert.equal(canonicalAuthorityBytes(floorReceipt.receipts!).toString("utf8"),
      canonicalAuthorityBytes(matrixEntry(35).receipts).toString("utf8"), `${pairLabel}: cross-runtime receipt bytes`);
    await third.next({kind: "exit"});
    const thirdClosed = await third.finish();
    assert.equal(thirdClosed.code, 0, thirdClosed.stderr);
    // Platform pin: both runtimes served on this host's OS/arch.
    assert.equal(secondIdentity.oracle.platform, firstIdentity.oracle.platform, `${pairLabel}: platform`);
    assert.equal(secondIdentity.oracle.arch, firstIdentity.oracle.arch, `${pairLabel}: arch`);
  });

test("OS/runtime matrix: launcher aliasing of one executable cannot fabricate distinct runtime coverage",
  {timeout: 120000}, async t => {
    const runner = await probeRuntime(t, process.execPath);
    // The reviewed counterexample: one Node reachable through several
    // entrypoints — a direct call and launcher symlinks (hardlinks behave
    // identically because no path heuristic is consulted) — must never be
    // selectable as a "distinct" second runtime. Only the child-reported
    // serving pair decides, and it is identical for every entrypoint of one
    // executable.
    const workspace = await mkdtemp(join(tmpdir(), "sqlite-runtime-matrix-alias-"));
    t.after(() => rm(workspace, {recursive: true, force: true}));
    const alias = join(workspace, "launcher-node");
    symlinkSync(process.execPath, alias);
    const chain = join(workspace, "chained-launcher-node");
    symlinkSync(alias, chain);
    const previousOverride = process.env.LOOPX_TEST_SQLITE_NEWER_MAJOR_NODE;
    process.env.LOOPX_TEST_SQLITE_NEWER_MAJOR_NODE = chain;
    try {
      const aliased = await selectSecondRuntime(t, runner, {newerMajorOnly: false, requireQualified: false});
      assert.equal(aliased.status, "unavailable",
        "a launcher alias of the runner must never form a distinct runtime row");
      if (aliased.status === "unavailable") {
        assert.match(aliased.reason, /no distinct qualifying second runtime available/,
          "the skip reason names the missing distinct runtime");
        assert.match(aliased.reason, /same serving pair/,
          "the skip reason names the identical serving pair, not a path heuristic");
        assert.ok(aliased.reason.includes(runner.nodeVersion) &&
          aliased.reason.includes(String(runner.sqliteVersion)),
          "the skip reason records the identical reported pair");
      }
    } finally {
      if (previousOverride === undefined) delete process.env.LOOPX_TEST_SQLITE_NEWER_MAJOR_NODE;
      else process.env.LOOPX_TEST_SQLITE_NEWER_MAJOR_NODE = previousOverride;
    }

    // Positive control on the same host: when a genuinely distinct second
    // runtime exists, the selection must return it with a reported pair that
    // differs from the runner's — the invariant the distinct rows assert
    // coverage by. Without one, the control skips with the same disclosed
    // reason (an unavailable environment is a gap, never a pass).
    const genuine = await selectSecondRuntime(t, runner, {newerMajorOnly: true, requireQualified: false});
    if (genuine.status === "unavailable") {
      t.skip(`aliasing guard passed; no genuinely distinct newer-major runtime for the positive control ` +
        `(${genuine.reason})`);
      return;
    }
    assert.ok(isDistinctPair(runner, genuine.probe),
      `selected second runtime must report a distinct pair (${pairsLabel(runner, genuine.probe)})`);
    assert.equal(genuine.probe.binaryVersion, genuine.probe.nodeVersion,
      "the selected launcher's --version matches the child's reported node version");
  });
