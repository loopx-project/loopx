import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {spawnSync} from "node:child_process";
import {readFile} from "node:fs/promises";
import test from "node:test";
import {readPinnedCapture} from "../src/time-machine/records.ts";
import {LOCAL_LOG_MAX_BYTES, LOCAL_LOG_MAX_RECORDS, LocalLogError, readLocalExploreLog, selectLocalLogEntries} from "../src/time-machine/local-log.ts";

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

// Public synthetic v0 records: the oracle is the original event/append contract,
// independent of the file adapter. Real native-builder bytes also enter the
// packaged browser qualification; this fixture is not a research result.
const localEvent = (extra = {}) => ({schema_version: "loopx_explore_result_event_v0", goal_id: "local-reader-example",
  event_id: "question-open", result_id: "question", event_kind: "node", node_kind: "question", status: "open",
  title: "合成问题 / Synthetic question", summary: "A question, not an experiment count.", recorded_at: "2026-01-01T00:00:00Z", ...extra});
const localRows = () => [localEvent(),
  localEvent({event_id: "early-negative", result_id: "finding", event_kind: "finding", node_id: "question", status: "refuted", title: "Counterevidence",
    summary: "原始反例 <b>literal source text</b>", evidence_refs: ["artifact:input-v1"], recorded_at: "2026-01-01T04:00:00Z"}),
  localEvent({event_id: "late-revision", result_id: "finding", event_kind: "finding", node_id: "question", status: "tentative", title: "Later revision",
    summary: "The later input is still uncertain.", evidence_refs: ["artifact:input-v2"], recorded_at: "2026-01-01T18:00:00Z"}),
  localEvent({event_id: "backfill", status: "exploring", recorded_at: "2026-01-01T03:00:00Z"}),
  localEvent({event_id: "unattached", event_kind: "finding", result_id: "unattached", node_id: "missing", title: "Unattached finding", status: "refuted", recorded_at: "unknown"}),
];
const encoded = text => new TextEncoder().encode(text).buffer;
const jsonl = rows => encoded(rows.map(row => JSON.stringify(row)).join("\n"));
const issue = (code, line = null) => error => error instanceof LocalLogError && error.issue === code && error.line === line;

test("local native-shaped JSONL retains the entire span, append corrections, negatives and unattached records", () => {
  const rows = localRows();
  rows[1].source_extra = {note: "Uninterpreted metadata remains inspectable"};
  const log = readLocalExploreLog(encoded(`\n${rows.map(row => JSON.stringify(row)).join("\r\n\n")}\n`));
  assert.deepEqual(log.entries.map(entry => entry.source), rows);
  assert.deepEqual(log.entries.map(entry => entry.line), [2, 4, 6, 8, 10]);
  assert.deepEqual(log.entries.map(entry => entry.event.summary), rows.map(row => row.summary));
  assert.equal(log.timeline.end - log.timeline.start, 18 * 3_600_000);
  assert.equal(log.history.nodes[0].latest.status, "exploring", "append order owns the latest node label");
  assert.equal(log.history.nodes[0].revisions.some(row => row.status === "refuted"), true);
  assert.equal(log.history.findings.some(row => row.result_id === "unattached"), true);
  assert.equal(log.history.unknownTimeCount, 1);
  assert.equal(log.history.edges.length, 0, "record-time adjacency is not a relationship");
});

test("local search and time/label filters preserve source order, exact labels and original text", () => {
  const log = readLocalExploreLog(jsonl(localRows()));
  assert.deepEqual(selectLocalLogEntries(log, " ARTIFACT:INPUT ", null, null).map(row => row.line), [2, 3]);
  assert.equal(selectLocalLogEntries(log, "原始反例", "refuted", null)[0].event.summary, "原始反例 <b>literal source text</b>");
  assert.deepEqual(selectLocalLogEntries(log, "", "refuted", null).map(row => row.line), [2, 5]);
  assert.deepEqual(selectLocalLogEntries(log, "", null, 0).map(row => row.line), [1, 2, 4]);
  assert.deepEqual(selectLocalLogEntries(log, "", null, 2).map(row => row.line), [3]);
  assert.deepEqual(selectLocalLogEntries(log, "", null, "unknown").map(row => row.line), [5]);
  assert.equal(selectLocalLogEntries(log, "absent text", null, null).length, 0);
  const futureLabel = readLocalExploreLog(jsonl([localEvent({status: "all"})]));
  assert.equal(selectLocalLogEntries(futureLabel, "", "all", null).length, 1, "a source label is data, not a reserved UI option");
});

// Independently declared Explore writeback semantics: only a finding with the
// owner's complete section envelope and an explicit leading Outcome is a
// decision. Status labels and incidental words do not confer that meaning.
const decisionSummary = interpretation => `Input revision: input-v2\nApplicability: Synthetic source only.\nObservation: The earlier route missed counterevidence.\nInterpretation: ${interpretation}`;
const decisionRows = () => {
  const replan = localEvent({event_id: "decision-replan", event_kind: "finding", node_id: "question", result_id: "decision-1", status: "tentative",
    summary: decisionSummary("Outcome: replan changed: - Read the early counterexample. stopped: - Retry the same input. retained: - Keep source references."), recorded_at: "2026-01-01T18:00:00Z"});
  return [localEvent({summary: decisionSummary("Outcome: stop"), status: "replan"}), replan,
    localEvent({event_id: "plain-mention", event_kind: "finding", result_id: "plain", summary: "Discuss Outcome: replan, changed: and retained: without a structured decision."}),
    localEvent({event_id: "decision-continue", event_kind: "finding", result_id: "decision-2", summary: decisionSummary("Outcome: continue retained: - Keep the negative result."), recorded_at: "2026-01-01T03:00:00Z"}),
    localEvent({event_id: "unknown-outcome", event_kind: "finding", result_id: "decision-3", summary: decisionSummary("Outcome: __proto__")}), replan,
    localEvent({event_id: "unknown-time-decision", event_kind: "finding", result_id: "decision-4", recorded_at: "unknown", summary: decisionSummary("Outcome: all")})];
};

test("decision reading uses explicit source outcomes, keeps repeated rows and never invents chronology or links", () => {
  const rows = decisionRows();
  const log = readLocalExploreLog(jsonl(rows));
  assert.deepEqual(selectLocalLogEntries(log, "", null, null).map(row => row.line), [1, 2, 3, 4, 5, 6, 7]);
  assert.deepEqual(selectLocalLogEntries(log, "", null, null, null).map(row => row.line), [2, 4, 5, 6, 7]);
  assert.deepEqual(selectLocalLogEntries(log, "", null, null, "replan").map(row => row.line), [2, 6]);
  assert.deepEqual(selectLocalLogEntries(log, "", null, null, "continue").map(row => row.line), [4]);
  assert.deepEqual(selectLocalLogEntries(log, "", null, null, "__proto__").map(row => row.line), [5]);
  assert.deepEqual(selectLocalLogEntries(log, "", null, null, "all").map(row => row.line), [7]);
  assert.equal(selectLocalLogEntries(log, "", null, null, "stop").length, 0, "a node is not a finding decision");
  assert.equal(log.timeline.decisions.length, 4, "the shared owner deduplicates its projection, not source rows");
  assert.equal(log.timeline.routes.length, 0);
  assert.equal(log.history.edges.length, 0);
  const sections = log.findingsByEventId.get("decision-replan").sections;
  assert.deepEqual(sections.decision, {outcome: "replan", changed: ["Read the early counterexample."], stopped: ["Retry the same input."], retained: ["Keep source references."]});
  assert.equal(sections.inputRevision, "input-v2");
  assert.equal(selectLocalLogEntries(log, "", null, null, "replan")[0].source.summary, rows[1].summary, "the complete original is never rewritten");
});

test("decision filters compose with original search, source labels and unknown record times", () => {
  const log = readLocalExploreLog(jsonl(decisionRows()));
  assert.deepEqual(selectLocalLogEntries(log, "early counterexample", "tentative", 2, "replan").map(row => row.line), [2, 6]);
  assert.equal(selectLocalLogEntries(log, "", "replan", null, null).length, 0, "a source status is separate from an outcome");
  assert.deepEqual(selectLocalLogEntries(log, "", null, "unknown", null).map(row => row.line), [7]);
  assert.equal(selectLocalLogEntries(log, "absent", null, null, "replan").length, 0);
  assert.equal(selectLocalLogEntries(readLocalExploreLog(jsonl(localRows())), "", null, null, null).length, 0, "a file without decisions stays empty in this view");
});

test("bad local files are rejected as a whole, with a line when the fault belongs to one record", () => {
  assert.throws(() => readLocalExploreLog(encoded("\n \r\n")), issue("empty"));
  assert.throws(() => readLocalExploreLog(new Uint8Array([0xff]).buffer), issue("encoding"));
  assert.throws(() => readLocalExploreLog(encoded(`${JSON.stringify(localEvent())}\n{broken`)), issue("json", 2));
  for (const value of [null, [], 1, {}, localEvent({goal_id: ""}), localEvent({schema_version: "not-v0"}), localEvent({confidence: 2}), localEvent({evidence_refs: [123]})]) {
    assert.throws(() => readLocalExploreLog(jsonl([value])), issue("record", 1));
  }
  assert.throws(() => readLocalExploreLog(jsonl([localEvent(), localEvent({goal_id: "another-goal"})])), issue("record", 2));
  assert.throws(() => readLocalExploreLog(jsonl([localEvent(), localEvent({summary: "Conflicting event identity"})])), issue("identity"));
  const repeated = readLocalExploreLog(jsonl([localEvent(), localEvent()]));
  assert.equal(repeated.entries.length, 2, "identical source rows remain readable");
  assert.equal(repeated.history.events.length, 1, "the existing common owner deduplicates only its display projection");
});

test("local browser bounds admit the full supported log and reject overflow rather than returning a prefix", () => {
  assert.throws(() => readLocalExploreLog(new ArrayBuffer(LOCAL_LOG_MAX_BYTES + 1)), issue("size"));
  const rows = Array.from({length: LOCAL_LOG_MAX_RECORDS}, (_, index) => localEvent({event_id: `event-${index}`, result_id: `node-${index}`}));
  assert.equal(readLocalExploreLog(jsonl(rows)).entries.length, LOCAL_LOG_MAX_RECORDS);
  assert.throws(() => readLocalExploreLog(jsonl([...rows, localEvent({event_id: "overflow"})])), issue("size"));
});

test("a byte-limit file of blank lines reports empty without a line-array memory explosion", () => {
  const source = JSON.stringify(new URL("../src/time-machine/local-log.ts", import.meta.url).href);
  const code = `import {readLocalExploreLog, LOCAL_LOG_MAX_BYTES, LocalLogError} from ${source};
    const bytes = new Uint8Array(LOCAL_LOG_MAX_BYTES).fill(10);
    try { readLocalExploreLog(bytes.buffer); process.exit(2); }
    catch(error) { if (!(error instanceof LocalLogError) || error.issue !== "empty") throw error; }`;
  // The child protects the test host; this is an intake regression, not a
  // general browser-memory or latency promise for all supported records.
  const result = spawnSync(process.execPath, ["--max-old-space-size=96", "--input-type=module", "-e", code], {encoding: "utf8", timeout: 5000});
  assert.equal(result.status, 0, result.stderr || String(result.error ?? result.signal));
});
