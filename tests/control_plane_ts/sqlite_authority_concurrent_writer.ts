/** A real writer process for the concurrent CAS / lost-response matrix test.
 *
 * The process owns nothing durable: its plan (a JSON file authored by the
 * parent) names every commit it will send, the file barriers that make
 * competing writers start together, and the raw-connection hold points used
 * to occupy the SQLite write lock. Results are reported as one JSON stdout
 * line per step phase so the parent can parse them robustly after the child
 * exits. A `lost_response` commit that applies prints no result at all and
 * ends the process with exit code 23, which is how the matrix simulates a
 * writer whose acknowledged commit never reached it; the process then ends
 * naturally (no `process.exit`) so its final stdout line cannot be truncated.
 * Spawned only by `sqlite_authority_concurrent_cas.test.ts`, never imported. */
import {existsSync} from "node:fs";
import {readFile, rename, writeFile} from "node:fs/promises";
import {createRequire} from "node:module";
import {setTimeout as sleep} from "node:timers/promises";

import {SqliteAuthorityStore, sqliteAuthorityPath} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import type {AuthorityStoreCommit, AuthorityStoreCommitResult} from "../../loopx/control_plane/coordination/authority_store.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

const PLAN_SCHEMA = "loopx_sqlite_concurrent_cas_writer_plan_v0";
const POLL_MS = 2;
const STEP_DEADLINE_MS = 30000;

interface WaitBarrierStep {
  kind: "wait-barrier";
  path: string;
}
interface LoadHeadStep {
  kind: "load-head";
}
interface CommitStep {
  kind: "commit";
  request: {
    expected_provider_revision: string | null | "$head";
    operation_id: string;
    events: JsonObject[];
    next_projection: JsonObject;
    receipts: JsonObject[];
    /** "$observed-head" rebuilds the submitted projection on top of the
     * projection this writer last loaded, the way a real writer re-plans its
     * operation from the current head after a lost response. */
    base_projection?: "$observed-head";
  };
  lost_response?: boolean;
}
interface HoldLockStep {
  kind: "hold-lock";
  ready_marker: string;
  release_marker: string;
}
type WriterStep = WaitBarrierStep | LoadHeadStep | CommitStep | HoldLockStep;

interface WriterPlan {
  schema_version: string;
  steps: WriterStep[];
}

/** Every line is drained through the write callback before anything else
 * happens, so ordering with the parent is deterministic. */
function line(payload: Record<string, unknown>): Promise<void> {
  return new Promise(resolve => process.stdout.write(`${JSON.stringify(payload)}\n`, () => resolve()));
}

/** Same-directory staging write plus rename, so the parent only ever observes
 * a complete marker; an empty create-before-write file is not a signal. */
async function publishMarker(path: string, body: string): Promise<void> {
  const staging = `${path}.staging`;
  await writeFile(staging, body, {mode: 0o600});
  await rename(staging, path);
}

async function waitForFile(path: string, what: string): Promise<void> {
  const deadline = Date.now() + STEP_DEADLINE_MS;
  while (!existsSync(path)) {
    if (Date.now() > deadline) throw new Error(`${what} marker never appeared: ${path}`);
    await sleep(POLL_MS);
  }
}

const [directory, goalId, planPath] = process.argv.slice(2);
if (!directory || !goalId || !planPath) {
  process.stderr.write("usage: sqlite_authority_concurrent_writer.ts <directory> <goalId> <planPath>\n");
  process.exit(2);
}

let plan: WriterPlan;
try {
  plan = JSON.parse(await readFile(planPath, "utf8")) as WriterPlan;
  if (plan.schema_version !== PLAN_SCHEMA || !Array.isArray(plan.steps) || plan.steps.length < 1) {
    throw new Error("concurrent writer plan is invalid");
  }
} catch (error) {
  process.stderr.write(`concurrent writer plan could not be loaded: ${error instanceof Error ? error.message : String(error)}\n`);
  process.exit(2);
}

const store = new SqliteAuthorityStore(directory, goalId);
// `$head` and `$observed-head` resolve from the most recent load-head step in
// this process, so a rebuilt retry carries exactly what this writer observed,
// at observation time.
let observedHeadRevision: string | null = null;
let observedHeadProjection: JsonObject | null = null;

await new Promise<void>(resolve => process.stdout.write("ready\n", () => resolve()));

let outcome: 0 | 1 | 23 = 0;
writer:
for (const [index, step] of plan.steps.entries()) {
  try {
    if (step.kind === "wait-barrier") {
      await waitForFile(step.path, "barrier");
      await line({step: index, kind: step.kind, phase: "done"});
      continue;
    }
    if (step.kind === "load-head") {
      const loaded = await store.loadAuthority();
      if (loaded.status !== "loaded") throw new Error(`load-head returned ${loaded.status}`);
      observedHeadRevision = loaded.provider_revision;
      observedHeadProjection = loaded.head;
      await line({step: index, kind: step.kind, phase: "done",
        cursor: loaded.cursor, provider_revision: loaded.provider_revision});
      continue;
    }
    if (step.kind === "commit") {
      const expected = step.request.expected_provider_revision === "$head"
        ? (() => {
          if (observedHeadRevision === null) throw new Error("$head resolved before any load-head step");
          return observedHeadRevision;
        })()
        : step.request.expected_provider_revision;
      const nextProjection = step.request.base_projection === "$observed-head"
        ? (() => {
          if (observedHeadProjection === null) throw new Error("$observed-head resolved before any load-head step");
          return {...observedHeadProjection, ...step.request.next_projection};
        })()
        : step.request.next_projection;
      const commit: AuthorityStoreCommit = {expected_provider_revision: expected,
        operation_id: step.request.operation_id, events: step.request.events,
        next_projection: nextProjection, receipts: step.request.receipts};
      await line({step: index, kind: step.kind, phase: "enter", operation_id: commit.operation_id});
      const started = Date.now();
      const result: AuthorityStoreCommitResult = await store.commitAuthority(commit);
      const elapsedMs = Date.now() - started;
      if (step.lost_response === true && result.status === "applied") {
        // The write is durable; the acknowledgement never left this process.
        await line({step: index, kind: step.kind, phase: "lost-response", operation_id: commit.operation_id});
        outcome = 23;
        break writer;
      }
      await line({step: index, kind: step.kind, phase: "done", operation_id: commit.operation_id,
        elapsed_ms: elapsedMs, result});
      continue;
    }
    if (step.kind === "hold-lock") {
      // Occupy the database write lock through a real connection: BEGIN
      // IMMEDIATE takes the lock, the marker rendezvous lets the parent
      // schedule a second writer against the held lock, and ROLLBACK leaves
      // no write behind. This child never uses store write paths here.
      const {DatabaseSync} = createRequire(import.meta.url)("node:sqlite");
      const db = new DatabaseSync(sqliteAuthorityPath(directory, goalId));
      try {
        db.exec("PRAGMA busy_timeout = 1000;");
        db.exec("BEGIN IMMEDIATE");
        const heldSince = Date.now();
        await publishMarker(step.ready_marker, "held");
        await waitForFile(step.release_marker, "release");
        db.exec("ROLLBACK");
        await line({step: index, kind: step.kind, phase: "done", held_ms: Date.now() - heldSince});
      } finally {
        db.close();
      }
      continue;
    }
    throw new Error(`unknown writer step kind: ${(step as {kind: string}).kind}`);
  } catch (error) {
    await line({step: index, kind: step.kind, phase: "error",
      error: error instanceof Error ? error.message : String(error)});
    outcome = 1;
    break writer;
  }
}

if (outcome === 0) await line({done: true});
process.exitCode = outcome;
// Natural exit: no pending writes can be truncated on the way out.
