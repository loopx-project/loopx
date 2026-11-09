/** A real consumer process for the lagged-consumer cursor test.
 *
 * The process owns nothing but its durable cursor file and its stdout log.
 * A SIGKILL can land between the log append and the cursor persist, so
 * recovery must tolerate one page of redelivery; it may never lose or
 * reorder a transaction. Spawned only by
 * `sqlite_authority_consumer_lag.test.ts`, never imported. */
import {existsSync} from "node:fs";
import {readFile, rename, writeFile} from "node:fs/promises";
import {join} from "node:path";
import {setTimeout as sleep} from "node:timers/promises";
import { SqliteAuthorityStore } from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";

const CONSUMER_CURSOR_SCHEMA = "loopx_sqlite_consumer_lag_cursor_test_v0";

async function persistCursor(path: string, goalId: string, cursor: string): Promise<void> {
  const staging = `${path}.staging`;
  await writeFile(staging, JSON.stringify({
    schema_version: CONSUMER_CURSOR_SCHEMA, goal_id: goalId, last_consumed_cursor: cursor,
  }), {mode: 0o600});
  await rename(staging, path);
}

async function loadCursor(path: string, goalId: string): Promise<string | null> {
  if (!existsSync(path)) return null;
  const record = JSON.parse(await readFile(path, "utf8")) as {
    schema_version: string; goal_id: string; last_consumed_cursor: string};
  if (record.schema_version !== CONSUMER_CURSOR_SCHEMA || record.goal_id !== goalId) {
    throw new Error("consumer cursor file is invalid");
  }
  return record.last_consumed_cursor;
}

/** Publish the hold marker only with its cursor bytes complete.
 *
 * `writeFile` on `hold-ready` creates that name before the write lands, so a
 * parent polling the name can observe a zero-byte file and treat it as cursor
 * 0. The staging file is in the same directory; the rename is the publication.
 * The parent acknowledges `hold-staging-seen` only after it has observed that
 * staging file and the absence of `hold-ready`, which keeps the window open
 * until the observation is real rather than a race against rename. */
async function publishHoldReady(directory: string, cursor: string): Promise<void> {
  const ready = join(directory, "hold-ready");
  const staging = `${ready}.staging`;
  const seen = join(directory, "hold-staging-seen");
  await writeFile(staging, cursor, {mode: 0o600});
  const deadline = Date.now() + 30000;
  while (!existsSync(seen)) {
    if (Date.now() > deadline) {
      process.stdout.write(`${JSON.stringify({hold_publish_unacknowledged: true})}\n`);
      process.exit(1);
    }
    await sleep(5);
  }
  await rename(staging, ready);
}

const [directory, goalId, cursorPath, limitArg, holdDir, holdAfterArg] = process.argv.slice(2);
const store = new SqliteAuthorityStore(directory!, goalId!);
const limit = Number(limitArg);
// Rendezvous for a deterministic mid-stream kill: once the durable cursor
// has passed holdAfter and unconsumed pages remain, announce readiness and
// block until the parent releases (or kills) — so the SIGKILL always lands
// on a consumer that is provably stopped mid-journal, never one that has
// already finished and exited on its own.
const holdAfter = Number(holdAfterArg ?? "0");
const hold = async (cursor: string): Promise<void> => {
  if (!holdDir || Number(cursor) < holdAfter) return;
  await publishHoldReady(holdDir, cursor);
  const deadline = Date.now() + 30000;
  while (!existsSync(join(holdDir, "hold-release"))) {
    if (Date.now() > deadline) {
      process.stdout.write(`${JSON.stringify({hold_expired: true})}\n`);
      process.exit(1);
    }
    await sleep(25);
  }
};
await new Promise<void>(resolve => process.stdout.write("ready\n", () => resolve()));
for await (const _chunk of process.stdin) {
  for (;;) {
    // Every page resumes from the durable cursor file, never from memory.
    const after = await loadCursor(cursorPath!, goalId!);
    const page = await store.scanCommitted(after, limit);
    if (page.status !== "page") {
      process.stdout.write(`${JSON.stringify({scan_failed: page})}\n`);
      process.exit(1);
    }
    if (page.transactions.length === 0) {
      process.stdout.write(`${JSON.stringify({done: true, last_cursor: after})}\n`);
      process.exit(0);
    }
    // The log append precedes the cursor persist: a kill between them leaves
    // one logged page beyond the persisted cursor, which recovery must
    // redeliver byte-identically.
    for (const transaction of page.transactions) {
      process.stdout.write(`${JSON.stringify({cursor: transaction.cursor,
        operation_id: transaction.operation_id, events: transaction.events,
        receipts: transaction.receipts})}\n`);
    }
    const next = page.next_cursor;
    if (next === null) {
      process.stdout.write(`${JSON.stringify({done: false, cursor_missing: true})}\n`);
      process.exit(1);
    }
    await persistCursor(cursorPath!, goalId!, next);
    if (page.has_more) await hold(next);
    if (!page.has_more) {
      process.stdout.write(`${JSON.stringify({done: true, last_cursor: next})}\n`);
      process.exit(0);
    }
  }
}
