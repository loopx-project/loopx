import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {once} from "node:events";
import {mkdtemp, mkdir, readFile, writeFile, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {resolve} from "node:path";
import {createInterface} from "node:readline";
import {createElement} from "react";
import {renderToStaticMarkup} from "react-dom/server";
import {resolveTestPython} from "../../../../scripts/test-python.mjs";
import {readAnalysisSnapshot, ResearchAnalysisPanel} from "../src/features/personal-workspace/goal-research-analysis.tsx";
import {ResearchCaseStudy} from "../src/features/personal-workspace/research-case-study.tsx";
import {fetchExploreResults} from "../src/data/chat.ts";
import {GoalResearchWorkbench} from "../src/features/personal-workspace/goal-research-workbench.tsx";

// The persisted directory can organize explicitly linked physical sources.
// Navigation must not turn organization into shared evidence or execution.
const caseFixture = JSON.parse(await readFile(resolve('src/data/fixtures/case-study-child.json'), 'utf8'));
const study = caseFixture.analyses[0].case_study;
const renderedStudy = renderToStaticMarkup(createElement(ResearchCaseStudy, {
  study, zh: false, historical: true, scope: caseFixture.analyses[0].input.scope, onCitation: () => {throw Error('read executed a citation');},
}));
assert.ok(renderedStudy.includes(study.title));
assert.ok(renderedStudy.includes('Showing the saved Chinese original.'));
assert.ok(renderedStudy.includes('not current results'));
assert.match(renderedStudy, /候选 A<.*?>7<.*?候选 B<.*?>Not recorded<.*?候选 C<.*?>3</,
  'Saved chart observations stay in author order, with numbers and the unrecorded value visible together');
for (const section of study.sections) assert.ok(renderedStudy.includes(section.title));
const goals = [
  {goalId:'parent',title:'Benchmark'},
  {goalId:'child',parentGoalId:'parent',title:'Local case'},
  {goalId:'foreign',parentGoalId:'parent',title:'Foreign case',sourceAccess:{namespace:'other',label:'Other',readOnly:true}},
];
const navigate = goalId => renderToStaticMarkup(createElement(GoalResearchWorkbench, {
  goalId, goals, zh: false, active: true, refreshRevision: 0,
  statusNotice: createElement("p", null, "Loading status"), onSelectGoal: () => {throw Error('read navigated');},
}));
assert.ok(navigate('parent').includes('Local case'));
assert.ok(navigate('parent').includes('Foreign case'));
assert.ok(navigate('child').includes('Benchmark'));
assert.ok(navigate('foreign').includes('Benchmark'), 'Explicit directory links retain their local parent');

// Transport uses the workspace view; immutable findings still identify the native Goal.
const sourceFetch = globalThis.fetch, sourceWindow = globalThis.window;
try {
  globalThis.window = {location: {href: "http://127.0.0.1:12345/chat/"}};
  globalThis.fetch = async input => {
    assert.equal(new URL(input, window.location.href).searchParams.get("goal_id"), "view-a");
    return Response.json({ok: true, goal_id: "native", total: 0, items: [], next_cursor: null});
  };
  assert.equal((await fetchExploreResults("view-a", undefined, undefined, "native")).goal_id, "native");
  await assert.rejects(fetchExploreResults("view-a"), /mismatch/);
} finally { globalThis.fetch = sourceFetch; globalThis.window = sourceWindow; }

// A language-specific cache miss must not hide the same registered pack's
// older, unspecified-language result or turn a read into model execution.
const scope = {goal_id: "synthetic-research", case_id: "case", run_id: "run", revision: "fixed", window: "fixed"};
const config = {model: "fixture", effort: "max", service_tier: "fast"};
const pack = {pack_id: "a".repeat(64), analysis_key: "b".repeat(64), scope, config,
  method_version: "research-episodes-v2", result: null, job: null};
const original = {...pack, analysis_key: "c".repeat(64),
  job: {status: "succeeded", usage: {input_tokens: 10, output_tokens: 3}},
  result: {analysis_key: "c".repeat(64), scope, method_version: pack.method_version,
    metrics: [{metric: "benefit_test", yes: 0, no: 1, unknown: 2, eligible: 3}],
    limitations: ["No causal effect has been established."]}};
const response = p => ({ok: true, catalog: {packs: [p], default_pack_id: pack.pack_id}, pack: p});
const savedFetch = globalThis.fetch, savedWindow = globalThis.window;
globalThis.window = {location: {href: "http://127.0.0.1:12345/chat/"}};
let calls = [], localized, saved, failOriginal = false;
globalThis.fetch = async (input, init) => {
  assert.ok(!init.method || init.method === "GET", "Saved analysis discovery must not dispatch any write");
  const url = new URL(input, window.location.href);
  calls.push(url);
  if (url.searchParams.has("output_language")) return Response.json(response(localized));
  assert.equal(url.searchParams.get("pack_id"), pack.pack_id, "A changed default cannot redirect the original read");
  return failOriginal ? Response.json({error: "Original source unavailable"}, {status: 503}) : Response.json(response(saved));
};
const read = (zh = true, source, signal, current) => readAnalysisSnapshot(scope.goal_id, null, zh, source, signal, current);
const reset = language => {
  calls = []; failOriginal = false;
  localized = {...structuredClone(pack), config: {...config, output_language: language}};
  saved = structuredClone(original);
};
try {
  for (const [zh, language] of [[true, "zh"], [false, "en"]]) {
    reset(language);
    const value = await read(zh);
    assert.equal(calls.length, 2);
    assert.equal(value.pack.config.output_language, language, "Execution retains the requested language and key");
    assert.equal(value.pack.result, null, "Original output does not populate the language-specific result");
    assert.deepEqual(value.originalPack, original, "Original judgments, negative/unknown counts, cost and identity remain unchanged");
    assert.equal(value.originalPack.result.output_language, undefined, "The reader never invents a translation");
  }
  reset("zh");
  localized.result = {...original.result, analysis_key: localized.analysis_key, output_language: "zh"};
  let value = await read();
  assert.equal(calls.length, 1, "A current-language result needs no original lookup");
  assert.equal(value.originalPack, null);

  reset("zh");
  localized.result = {...original.result, output_language: "zh"};
  await assert.rejects(read(), /读回不一致/);
  assert.equal(calls.length, 1, "A mismatched current result is rejected before any original lookup");

  reset("en");
  saved.config.output_language = "zh";
  saved.result.output_language = "zh";
  value = await read(false);
  assert.equal(value.originalPack, null, "An explicit other-language artifact is not an unspecified-language original");

  for (const corrupt of [
    p => {p.pack_id = "d".repeat(64);},
    p => {p.scope.run_id = "other-run";},
    p => {p.method_version = "other-method"; p.result.method_version = "other-method";},
    p => {p.result.analysis_key = "d".repeat(64);},
    p => {p.result.method_version = "other-method";},
    p => {p.result.scope = {...scope, revision: "other-revision"};},
    p => {p.result.output_language = "en";},
  ]) {
    reset("zh"); corrupt(saved);
    value = await read();
    assert.equal(value.originalPack, null, "Mismatched saved evidence must never enter the reader");
    assert.ok(value.originalReadError, "A rejected original remains distinguishable from an absent result");
    assert.equal(value.pack.analysis_key, pack.analysis_key);
  }
  reset("zh"); failOriginal = true;
  value = await read();
  assert.ok(value.originalReadError);
  assert.equal(value.pack.result, null, "An original read failure does not erase the current-language state");
  failOriginal = false;
  assert.deepEqual((await read()).originalPack, original, "The ordinary read retries recovery without execution");

  // A failed/in-flight observation is unknown; only a successful empty read
  // supports the absence message. Keep the existing read retry as recovery.
  for (const zh of [true, false]) {
    const empty = {...pack, availability: "available", selection: {exhaustive: false}, episode_count: 0};
    const controller = {zh, pack: empty, requestPack: empty, location: {open: true}, panel: {current: null},
      readOnly: true, loading: false, snapshot: {catalog: {}}, read: async () => {}, close: () => {}};
    const noSaved = zh ? "此来源尚无已保存的分析。" : "This source has no saved analysis yet.";
    const render = state => renderToStaticMarkup(createElement(ResearchAnalysisPanel, {analysis: {...controller, ...state}}));
    assert.ok(render({}).includes(noSaved), "A successful empty source retains its ordinary absence state");
    for (const state of [{loading: true}, {readError: "Source unavailable"}, {snapshot: {catalog: {}, originalReadError: "Original unavailable"}}]) {
      const html = render(state);
      assert.ok(!html.includes(noSaved), "An incomplete source read must not claim that no saved analysis exists");
      if (!state.loading) {
        assert.ok(html.includes('role="alert"'));
        assert.ok(html.includes(zh ? 'aria-label="重试读取"' : 'aria-label="Retry reading"'));
      }
    }
  }

  for (const options of [{current: () => false}, {signal: AbortSignal.abort()}]) {
    reset("zh");
    value = await read(true, undefined, options.signal, options.current);
    assert.equal(calls.length, 1, "A stale or aborted read cannot start an original lookup");
    assert.equal(value.originalPack, null);
  }
  reset("zh");
  await read(true, `http://127.0.0.1:23456/status.json?source_namespace=${"e".repeat(64)}`);
  assert.ok(calls.every(url => url.origin === "http://127.0.0.1:23456" && url.searchParams.get("source_namespace") === "e".repeat(64)),
    "Both observations retain the selected physical source namespace");

  // Qualify the production consumer against the actual pack/job/HTTP owner.
  // The sole startup output is a synthetic process fixture, never a model.
  const repoRoot = resolve(process.cwd(), "../../..");
  const directory = await mkdtemp(resolve(tmpdir(), "loopx-original-analysis-read-"));
  let child, lines;
  try {
    const capture = JSON.parse(await readFile(resolve(repoRoot, "apps/presentation/dashboard/src/data/fixtures/research-native-readback.json"), "utf8"));
    const analysis = capture.analyses[0];
    analysis.config = {model: "fixture", effort: "max", service_tier: "fast", research_synthesis: false};
    analysis.outputs = {original: {assessments: [{episode_id: analysis.input.episodes[0].id, metric: "benefit_test",
      verdict: "no", reason: "The recorded observation does not establish task utility.",
      citations: [{source_id: "facts", first_line: 1, last_line: 1}]}], limitations: ["Synthetic reader fixture only."]}};
    const path = resolve(directory, "capture.json"), temporary = resolve(directory, "tmp");
    await mkdir(temporary); await writeFile(path, JSON.stringify(capture));
    child = spawn(resolveTestPython({repoRoot}), ["-u", "examples/personal-workspace-browser/research-history-backend.py", "--fixture", path],
      {cwd: repoRoot, env: {...process.env, PYTHONPATH: repoRoot, TMPDIR: temporary, TMP: temporary, TEMP: temporary}, stdio: ["ignore", "pipe", "pipe"]});
    let diagnostic = "";
    child.stderr.on("data", chunk => {diagnostic = (diagnostic + chunk).slice(-4000);});
    lines = createInterface({input: child.stdout});
    const ready = await new Promise((accept, reject) => {
      const timer = setTimeout(() => reject(new Error(`Reader startup timed out: ${diagnostic}`)), 25000);
      child.once("error", e => {clearTimeout(timer); reject(e);});
      child.once("exit", () => {clearTimeout(timer); reject(new Error(`Reader exited: ${diagnostic}`));});
      lines.once("line", line => {clearTimeout(timer); try {accept(JSON.parse(line));} catch (e) {reject(e);}});
    });
    const origin = `http://127.0.0.1:${ready.port}`;
    const requests = [];
    globalThis.fetch = (input, init) => {
      assert.ok(!init?.method || init.method === "GET");
      requests.push(String(input));
      return savedFetch(new URL(input, origin), init);
    };
    const actual = await readAnalysisSnapshot(capture.goal_id, null, true);
    assert.equal(requests.length, 2);
    assert.equal(actual.pack.result, null);
    assert.equal(actual.originalPack.result.metrics.find(row => row.metric === "benefit_test").no, 1);
    assert.equal(actual.originalPack.result.output_language, undefined);
    const p = actual.originalPack;
    const citation = await (await savedFetch(`${origin}/api/chat/research-analysis/citation?${new URLSearchParams({
      goal_id: capture.goal_id, pack_id: p.pack_id, analysis_key: p.analysis_key,
      source_id: "facts", first_line: "1", last_line: "1"})}`)).json();
    assert.equal(citation.text, analysis.input.sources[0].text.split("\n")[0]);
    const readback = await (await savedFetch(`${origin}/fixture/readback`)).json();
    assert.equal(readback.mutation_attempts, 0);
    assert.equal(readback.source_unchanged, true);
  } finally {
    lines?.close();
    if (child && child.exitCode === null && child.signalCode === null) {
      const exited = once(child, "exit"); child.kill("SIGTERM"); await exited;
    }
    await rm(directory, {recursive: true, force: true});
  }
  console.log("research original analysis read: passed");
} finally {
  globalThis.fetch = savedFetch;
  if (savedWindow === undefined) delete globalThis.window; else globalThis.window = savedWindow;
}
