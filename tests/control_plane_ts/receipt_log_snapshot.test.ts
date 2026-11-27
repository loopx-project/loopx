import assert from "node:assert/strict";
import { appendFile, copyFile, mkdtemp, open, rename, rm, stat, utimes, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import fs from "node:fs/promises";
import { syncBuiltinESMExports } from "node:module";

import { readReceiptLogSnapshot as read } from "../../loopx/control_plane/runtime/receipt_log_snapshot.ts";

async function fixture(run: (path: string) => Promise<void>): Promise<void> {
  const root = await mkdtemp(join(tmpdir(), "loopx-receipt-history-"));
  try { await run(join(root, "history.jsonl")); }
  finally { await rm(root, {recursive: true, force: true}); }
}

test("overlapping reads share only identical bytes and decoding rules", { timeout: 15000 }, async (t) => {
  const originalRead = fs.readFile;
  for (const scenario of ["identical", "rewritten tail", "different schema"]) {
    await fixture(async (path) => {
      // A complete row large enough to yield during decoding, then a torn tail.
      const prefix = JSON.stringify({ schema_version: "v0", pad: "x".repeat(1024 * 1024) }) + "\n";
      const tail = (id: string) => JSON.stringify({ schema_version: "v0", id });
      await writeFile(path, prefix + tail("a"));
      const firstRead = Promise.withResolvers<void>();
      const bothRead = Promise.withResolvers<void>();
      let reads = 0;
      // Real file reads, released together so both snapshots have captured
      // their own bytes before either decode can install a completed prefix.
      const hook = t.mock.method(fs, "readFile", async (file: string) => {
        const current = await originalRead(file);
        if (++reads === 1) firstRead.resolve();
        else bothRead.resolve();
        await bothRead.promise;
        return current;
      });
      syncBuiltinESMExports();
      try {
        const a = read(path, "v0");
        await firstRead.promise;
        if (scenario === "rewritten tail") await writeFile(path, prefix + tail("b"));
        const b = read(path, scenario === "different schema" ? "v1" : "v0");
        const [first, second] = await Promise.all([a, b]);
        assert.equal(reads, 2);
        assert.equal(first!.records[1].id, "a");
        assert.equal(first!.unterminatedRecordLine, 2);
        if (scenario === "identical") {
          assert.equal(first!.records[1], second!.records[1]);
          const later = (await read(path, "v0"))!;
          assert.notEqual(later.records[1], first!.records[1]);
          assert.deepEqual(later.records[1], first!.records[1]);
        } else if (scenario === "rewritten tail") {
          assert.equal(second!.records[1].id, "b");
          assert.equal(second!.unterminatedRecordLine, 2);
        } else {
          assert.deepEqual(second!.records, []);
          assert.equal(second!.firstErrorLine, 1);
        }
      } finally {
        bothRead.resolve();
        hook.mock.restore();
        syncBuiltinESMExports();
      }
    });
  }
});

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

test("a damaged row still yields the named members its writer finished", async () => {
  await fixture(async (path) => {
    await writeFile(path, Buffer.concat([
      Buffer.from('{"agent_id":"a","details":{"todo_id":"'),
      Buffer.from([0xff]),
      Buffer.from('"},"event_id":"e","event_kind":"quota_should_run",' +
        '"goal_id":"g","run_id":"turn-2","schema_version":"v0"}\n'),
    ]));
    const snapshot = (await read(path, "v0", ["event_kind", "run_id", "goal_id"]))!;
    assert.deepEqual(snapshot.records, []);
    assert.equal(snapshot.firstErrorLine, 1);
    assert.deepEqual(
      Object.fromEntries(snapshot.malformedLines[0].members),
      { event_kind: "quota_should_run", goal_id: "g", run_id: "turn-2" },
    );
    assert.deepEqual(snapshot.malformedLines[0].unreadable, []);
    // A damaged value is unreadable, never absent and never guessed.
    await writeFile(path, Buffer.concat([
      Buffer.from('{"run_id":"turn-'),
      Buffer.from([0xff]),
      Buffer.from('","schema_version":"v0"}\n'),
    ]));
    const damaged = (await read(path, "v0", ["run_id"]))!;
    assert.deepEqual(damaged.malformedLines[0].members, []);
    assert.deepEqual(damaged.malformedLines[0].unreadable, ["run_id"]);
    // Members nobody asked for are not recovered at all.
    assert.deepEqual((await read(path, "v0"))!.malformedLines, []);
  });
});

test("uncommitted bytes the writer drops leave no trace in history", async () => {
  await fixture(async (path) => {
    // A torn write can hold a complete JSON object and still be missing its
    // committing newline.  The append owner drops those bytes before writing
    // its own row, so no reader ever sees the half-write.
    await writeFile(path, '{"run_id":"turn-lost","schema_version":"v0"}');
    await writeFile(path, '{"run_id":"kept","schema_version":"v0"}\n');
    const snapshot = (await read(path, "v0", ["run_id"]))!;
    assert.equal(snapshot.firstErrorLine, null);
    assert.deepEqual(snapshot.records.map((record) => record.run_id), ["kept"]);
    await appendFile(path, '{"run_id":"turn-2","schema_version":"v0"}\n');
    const grown = (await read(path, "v0", ["run_id"]))!;
    assert.deepEqual(grown.records.map((record) => record.run_id), ["kept", "turn-2"]);
  });
});

test("a readable row stays a record beside a blank history line", async () => {
  await fixture(async (path) => {
    await writeFile(path, '{"run_id":"kept","schema_version":"v0"}\n\n');
    const snapshot = (await read(path, "v0", ["run_id"]))!;
    assert.deepEqual(snapshot.records.map((record) => record.run_id), ["kept"]);
    await appendFile(path, '{"run_id":"later","schema_version":"v0"}\n');
    const grown = (await read(path, "v0", ["run_id"]))!;
    assert.deepEqual(grown.records.map((record) => record.run_id), ["kept", "later"]);
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

test("alternating large logs reuse verified records and still reread both sides of the cutoff", async () => {
  await fixture(async (path) => {
    const peer = path + ".peer";
    const marker = '{"id":"start"}\n';
    const paddingBlocks = 3;
    const padding = Buffer.alloc(1024 * 1024, 32);
    padding[padding.length - 1] = 10;
    const log = await open(path, "w");
    try {
      await log.writeFile(marker);
      for (let i = 0; i < paddingBlocks; i++) await log.writeFile(padding);
      await log.writeFile('{"id":"last"}\n');
    } finally {
      await log.close();
    }
    const opts = { maxRetainedBytes: 4 * 1024 * 1024 };
    await copyFile(path, peer);
    await read(path, undefined, [], opts);
    await read(peer, undefined, [], opts);
    const first = (await read(path, undefined, [], opts))!;
    const second = (await read(peer, undefined, [], opts))!;
    const updated = await open(path, "r+");
    try {
      await updated.write('{"id":"edit"}\n', marker.length + paddingBlocks * padding.length, "utf8");
    } finally {
      await updated.close();
    }
    const changedTail = (await read(path, undefined, [], opts))!;
    assert.equal(changedTail.records[0], first.records[0]);
    assert.deepEqual(changedTail.records.map(record => record.id), ["start", "edit"]);
    assert.equal((await read(peer, undefined, [], opts))!.records[0], second.records[0]);
    const rewritten = await open(path, "r+");
    try {
      await rewritten.write('{"id":"other"}\n', 0, "utf8");
    } finally {
      await rewritten.close();
    }
    assert.deepEqual((await read(path, undefined, [], opts))!.records.map(record => record.id), ["other", "edit"]);
    assert.deepEqual(first.records.map(record => record.id), ["start", "last"]);
  });
});

test("peer-preserving retention keeps alternating logs warm without thrashing or hiding rewrites", async () => {
  await fixture(async (path) => {
    for (let i = 0; i < 4; i++) {
      const clearPath = `${path}.clear-${i}`;
      await writeFile(clearPath, `{"clear":${i}}\n`);
      await read(clearPath);
    }
    const peer = path + ".peer";
    const row = Buffer.from(JSON.stringify({ pad: "x".repeat(512 * 1024 - 24) }) + "\n");
    const writeLog = async (target: string, count: number, tag: string) => {
      const handle = await open(target, "w");
      try {
        for (let i = 0; i < count; i++) await handle.writeFile(row);
        await handle.writeFile(JSON.stringify({ id: tag }) + "\n");
      } finally {
        await handle.close();
      }
    };
    await writeLog(path, 5, "primary-tail");
    await writeLog(peer, 5, "peer-tail");

    // 1) With sufficient total budget (> a + b), alternating logs keep 100% of
    // both snapshots resident (exact .records reference reuse).
    const comfy = { maxRetainedBytes: 6 * 1024 * 1024 };
    const comfyPrimary1 = (await read(path, undefined, [], comfy))!;
    const comfyPeer1 = (await read(peer, undefined, [], comfy))!;
    const comfyPrimary2 = (await read(path, undefined, [], comfy))!;
    const comfyPeer2 = (await read(peer, undefined, [], comfy))!;
    assert.equal(comfyPrimary2.records, comfyPrimary1.records);
    assert.equal(comfyPeer2.records, comfyPeer1.records);
    assert.equal(comfyPrimary1.records.length, 6);

    // 2) Under a tight 4 MiB budget where resident primary (~2.5 MiB) + new
    // peer (~2.5 MiB) exceed the cap, peer fits into the 1.5 MiB headroom
    // instead of evicting primary; appends remain immediately visible.
    const tight = { maxRetainedBytes: 4 * 1024 * 1024 };
    for (let i = 0; i < 4; i++) {
      const resetPath = `${path}.tight-reset-${i}`;
      await writeFile(resetPath, `{"reset":${i}}\n`);
      await read(resetPath, undefined, [], tight);
    }
    const tightPrimary1 = (await read(path, undefined, [], tight))!;
    const tightPeer1 = (await read(peer, undefined, [], tight))!;
    const tightPrimary2 = (await read(path, undefined, [], tight))!;
    const tightPeer2 = (await read(peer, undefined, [], tight))!;
    assert.equal(tightPrimary2.records, tightPrimary1.records);
    assert.equal(tightPeer2.records[0], tightPeer1.records[0]);
    assert.equal(tightPeer2.records.at(-1)?.id, "peer-tail");

    await appendFile(peer, JSON.stringify({ id: "peer-new" }) + "\n");
    const grownPeer = (await read(peer, undefined, [], tight))!;
    assert.equal(grownPeer.records[0], tightPeer1.records[0]);
    assert.equal(grownPeer.records.at(-1)?.id, "peer-new");
  });
});
