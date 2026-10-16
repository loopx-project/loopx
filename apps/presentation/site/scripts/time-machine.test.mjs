import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {readFile} from "node:fs/promises";
import test from "node:test";
import {readPinnedCapture} from "../src/time-machine/records.ts";

const scenes = JSON.parse(await readFile(new URL("../src/time-machine/scenes.json", import.meta.url)));
const buffer = bytes => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
const digest = bytes => createHash("sha256").update(bytes).digest("hex");
const bytesFor = scene => readFile(new URL(`../public/lab/time-machine/data/${scene.file}`, import.meta.url));

test("all three pinned captures preserve original records and explicit evidence anchors", async () => {
  assert.deepEqual(scenes.map(scene => scene.count), [76, 134, 105]);
  for (const scene of scenes) {
    const bytes = await bytesFor(scene);
    const source = JSON.parse(bytes);
    const rows = await readPinnedCapture(buffer(bytes), scene);
    assert.deepEqual(rows, source.events, "the renderer does not rewrite source text, status or time");
    assert.equal(source.provenance.trajectory_publication.revision, "c858f51021dc1c1cf08263f9c04b692a5e29b40b");
    assert.equal(source.provenance.claim_scope, "diagnostic_only");
    assert.equal(source.provenance.score_countable, false);
    assert.equal(source.source_counts.edge_count, 0);
    assert.equal(source.source_counts.parent_records, 0);
    const finding = rows.find(row => row.result_id === scene.evidenceId);
    assert.equal(finding.event_kind, "finding");
    assert.equal(scene.en.choices.length, 2);
    assert.equal(scene.zh.choices.length, 2);
    assert.equal(scene.en.metrics.length, scene.zh.metrics.length);
  }
});

test("different or edited bytes cannot inherit the curated scene's authority", async () => {
  const [portfolio, graph] = await Promise.all(scenes.slice(0, 2).map(bytesFor));
  await assert.rejects(readPinnedCapture(buffer(graph), scenes[0]), /digest/);
  const edited = Buffer.from(portfolio);
  edited[10] ^= 1;
  await assert.rejects(readPinnedCapture(buffer(edited), scenes[0]), /digest/);
  await assert.rejects(readPinnedCapture(new ArrayBuffer(0), scenes[0]), /digest/);
});

test("artifact-shape failures reject even if an editor updates the digest", async () => {
  const original = JSON.parse(await bytesFor(scenes[0]));
  for (const mutate of [
    data => {data.events[0].schema_version = "invented_schema";},
    data => {data.events[0].recorded_at = "not a date";},
    data => {data.events[0].goal_id = "another-capture";},
    data => {data.events[1].event_id = data.events[0].event_id;},
    data => {data.events.splice(data.events.findIndex(row => row.result_id === scenes[0].evidenceId), 1);},
    data => {data.source_sha256 = "0".repeat(64);},
  ]) {
    const data = structuredClone(original); mutate(data);
    const bytes = Buffer.from(JSON.stringify(data));
    await assert.rejects(readPinnedCapture(buffer(bytes), {...scenes[0], sha256: digest(bytes)}));
  }
});

test("a record reader preserves append order rather than inventing historical state", async () => {
  const data = JSON.parse(await bytesFor(scenes[0]));
  data.events[0].recorded_at = "2030-01-01T00:00:00Z";
  const bytes = Buffer.from(JSON.stringify(data));
  const rows = await readPinnedCapture(buffer(bytes), {...scenes[0], sha256: digest(bytes)});
  assert.deepEqual(rows, data.events);
  assert.equal(rows[0].recorded_at, "2030-01-01T00:00:00Z");
});
