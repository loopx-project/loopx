import assert from "node:assert/strict";
import {test} from "node:test";
import {downloadReport, reportDownloadName} from "./report-download.ts";

const hash = "abcdef0123456789".repeat(4);
test("download filenames are bounded versioned leaf names with inert extensions", () => {
  assert.equal(reportDownloadName("reports/结论.md", hash), "report-结论-abcdef012345.md");
  assert.equal(reportDownloadName("C:\\private\\CON.exe", hash), "report-CON-abcdef012345.txt");
  for (const ref of ["../../.html", "x\u202Eexe.md", "a\r\n\"<>:|?.js", "".padEnd(1000, "x")]) {
    const name = reportDownloadName(ref, hash);
    assert.match(name, /^report-[\p{L}\p{N}_-]+-abcdef012345\.(txt|md)$/u);
    assert.ok(name.length < 110);
  }
  assert.notEqual(reportDownloadName("report.json", hash), reportDownloadName("report.json", "b".repeat(64)));
});

test("repeated exports preserve exact Unicode/CRLF bytes and release object URLs even on interruption", async () => {
  const original = {window: globalThis.window, document: globalThis.document,
    create: URL.createObjectURL, revoke: URL.revokeObjectURL};
  const blobs = [], names = [], removed = [], released = [], timers = [];
  let fail = false;
  globalThis.window = {setTimeout: callback => timers.push(callback)};
  globalThis.document = {body: {append() {}}, createElement: () => ({
    click() {names.push(this.download); if (fail) throw new Error("interrupted");},
    remove() {removed.push(true);},
  })};
  URL.createObjectURL = blob => {blobs.push(blob); return `blob:${blobs.length}`;};
  URL.revokeObjectURL = url => released.push(url);
  try {
    const text = "# 结论 🧪\r\n\r\n<raw> & unmodified\n";
    downloadReport("a.md", hash, text);
    fail = true;
    assert.throws(() => downloadReport("a.md", hash, text), /interrupted/);
    fail = false;
    downloadReport("new.json", "b".repeat(64), '{"revision":2}\n');
    assert.deepEqual(new Uint8Array(await blobs[0].arrayBuffer()), new TextEncoder().encode(text));
    assert.equal(await blobs[1].text(), text);
    assert.equal(await blobs[2].text(), '{"revision":2}\n');
    assert.equal(removed.length, 3);
    assert.equal(released.length, 0, "Browser must consume URLs before cleanup");
    timers.forEach(callback => callback());
    assert.deepEqual(released, ["blob:1", "blob:2", "blob:3"]);
    assert.notEqual(names[0], names[2]);
  } finally {
    globalThis.window = original.window; globalThis.document = original.document;
    URL.createObjectURL = original.create; URL.revokeObjectURL = original.revoke;
  }
});
