import assert from "node:assert/strict";
import { appendFile, mkdtemp, rename, rm, stat, utimes, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { readReceiptLogSnapshot as read } from "../../loopx/control_plane/runtime/receipt_log_snapshot.ts";

async function fixture(run: (path: string) => Promise<void>): Promise<void> {
  const root = await mkdtemp(join(tmpdir(), "loopx-receipt-history-"));
  try { await run(join(root, "history.jsonl")); }
  finally { await rm(root, {recursive: true, force: true}); }
}

test("fresh disk bytes own reuse: append retains old records without changing old snapshots", async () => {
  await fixture(async (path) => {
    await writeFile(path, '{"turn":"old","nested":{"value":1}}\n');
    const before = (await read(path))!;
    const again = (await read(path))!;
    assert.equal(again.records, before.records);
    assert.throws(() => { before.records[0].turn = "poison"; }, TypeError);
    assert.throws(() => { (before.records[0].nested as {value: number}).value = 2; }, TypeError);
    await appendFile(path, '{"turn":"new"}\n');
    const after = (await read(path))!;
    assert.deepEqual(after.records.map((r) => r.turn), ["old", "new"]);
    assert.equal(after.records[0], before.records[0]);
    assert.equal(before.records.length, 1);
    assert.equal(after.firstErrorLine, null);
  });
});

test("same-size rewrite with restored mtime cannot hide a changed old receipt", async () => {
  await fixture(async (path) => {
    await writeFile(path, '{"binding":"aaa"}\n');
    const metadata = await stat(path);
    await read(path);
    await writeFile(path, '{"binding":"bbb"}\n');
    await utimes(path, metadata.atime, metadata.mtime);
    assert.deepEqual((await read(path))!.records, [{binding: "bbb"}]);
    // Growth is not proof of append-only history either.
    await writeFile(path, '{"binding":"ccc"}\n{"later":true}\n');
    assert.deepEqual((await read(path))!.records, [{binding: "ccc"}, {later: true}]);
  });
});

test("truncation, replacement, deletion and recreation discard stale facts", async () => {
  await fixture(async (path) => {
    await writeFile(path, '{"old":true}\n{"old":2}\n');
    await read(path);
    await writeFile(path, "");
    assert.deepEqual((await read(path))!.records, []);
    await writeFile(path + ".next", '{"replacement":true}\n');
    await rename(path + ".next", path);
    assert.deepEqual((await read(path))!.records, [{replacement: true}]);
    await rm(path);
    assert.equal(await read(path), null);
    await writeFile(path, '{"recreated":true}\n');
    assert.deepEqual((await read(path))!.records, [{recreated: true}]);
  });
});

test("unfinished JSON and split UTF-8 tails are reparsed when completed", async () => {
  await fixture(async (path) => {
    const full = Buffer.from('{"label":"界"}\n');
    const split = full.indexOf(Buffer.from("界")) + 1;
    await writeFile(path, full.subarray(0, split));
    assert.equal((await read(path))!.firstErrorLine, 1);
    await appendFile(path, full.subarray(split));
    assert.deepEqual(await read(path).then((s) => s!.records), [{label: "界"}]);
    assert.equal((await read(path))!.firstErrorLine, null);
    await appendFile(path, '{"last":1}');
    const unterminated = (await read(path))!;
    assert.equal(unterminated.records.length, 2);
    await appendFile(path, 'broken\n');
    const malformed = (await read(path))!;
    assert.equal(malformed.records.length, 1);
    assert.equal(malformed.firstErrorLine, 2);
    assert.equal(unterminated.records.length, 2);
  });
});

test("schema, malformed line numbers and tolerant valid rows survive reuse", async () => {
  await fixture(async (path) => {
    await writeFile(path, '\r\n {"schema_version":"v1"}\r\n[]\n{"schema_version":"v2"}\n');
    const strict = (await read(path, "v1"))!;
    assert.deepEqual(strict.records, [{schema_version: "v1"}]);
    assert.equal(strict.firstErrorLine, 3);
    assert.equal((await read(path))!.records.length, 2);
    await appendFile(path, '{"schema_version":"v1","new":true}\n');
    const appended = (await read(path, "v1"))!;
    assert.equal(appended.firstErrorLine, 3);
    assert.equal(appended.records.length, 2);
    await writeFile(path, '{"schema_version":"v1","repaired":true}\n');
    assert.equal((await read(path, "v1"))!.firstErrorLine, null);
  });
});

test("invalid UTF-8 rows are malformed, never receipts", async () => {
  await fixture(async (path) => {
    const valid = Buffer.from('{"run_id":"turn-1"}\n');
    const bad = Buffer.concat([
      Buffer.from('{"run_id":"turn-2","summary":"'),
      Buffer.from([0xff]),
      Buffer.from('"}\n'),
    ]);
    await writeFile(path, Buffer.concat([valid, bad]));
    const snapshot = (await read(path))!;
    assert.deepEqual(snapshot.records.map((r) => r.run_id), ["turn-1"]);
    assert.equal(snapshot.firstErrorLine, 2);
    // Cached prefix reuse must not resurrect or forget the bad row.
    const reused = (await read(path))!;
    assert.equal(reused.firstErrorLine, 2);
    assert.deepEqual(reused.records.map((r) => r.run_id), ["turn-1"]);
    // An unterminated tail with invalid bytes is malformed, never a record.
    await writeFile(path, Buffer.concat([
      valid,
      Buffer.from('{"run_id":"turn-3","summary":"'),
      Buffer.from([0xff]),
    ]));
    const tail = (await read(path))!;
    assert.deepEqual(tail.records.map((r) => r.run_id), ["turn-1"]);
    assert.equal(tail.firstErrorLine, 2);
    // Terminating the damaged tail keeps it malformed but never hides a later row.
    await appendFile(path, Buffer.from('\n{"run_id":"turn-4"}\n'));
    const completed = (await read(path))!;
    assert.deepEqual(
      completed.records.map((r) => r.run_id),
      ["turn-1", "turn-4"],
    );
    assert.equal(completed.firstErrorLine, 2);
  });
});

test("eviction and concurrent cold readers affect cost, never evidence", async () => {
  await fixture(async (path) => {
    const row = JSON.stringify({payload: "x".repeat(2048)}) + "\n";
    await writeFile(path, row.repeat(1024));
    const [a, b] = await Promise.all([read(path), read(path)]);
    assert.deepEqual(a!.records, b!.records);
    for (let index = 0; index < 5; index++) {
      const other = path + index;
      await writeFile(other, '{"other":true}\n');
      await read(other);
    }
    const evicted = (await read(path))!;
    assert.deepEqual(evicted.records, a!.records);
    assert.notEqual(evicted.records, a!.records);
  });
});
