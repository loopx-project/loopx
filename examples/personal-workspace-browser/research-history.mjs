import assert from "node:assert/strict";
import {execFileSync, spawn} from "node:child_process";
import {mkdtemp, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {resolve} from "node:path";
import {createInterface} from "node:readline";
import {resolveTestPython} from "../../scripts/test-python.mjs";
import {outputDir, repoRoot} from "./fixture.mjs";
import {fetchWorkspaceBackend, openWorkspacePage} from "./scenario-context.mjs";

// Presentation-only race fixture; the HTTP/process authority is tested in
// tests/presentation/test_research_analysis_api.py. No model is dispatched here.
function analysisReadback(selected = "a") {
  const scope = {goal_id: "synthetic-solver", case_id: "synthetic-case", run_id: "synthetic-run", revision: "fixed", window: "fixed synthetic sample"};
  const selection = {rule: "Synthetic purpose sample", population_episodes: null, exhaustive: false};
  const config = {model: "fixture", effort: "max", service_tier: "unknown"};
  const packs = ["a", "b", "c", "d"].map(id => ({pack_id: id.repeat(64), title: `Synthetic analysis ${id.toUpperCase()}`, scope, selection, config,
    episode_count: id === "c" ? 1 : 2, source_count: 1, analysis_key: (id === "c" ? "e" : "c").repeat(64), method_version: "research-episodes-v2", availability: "available", reason_code: null}));
  const snapshot = {ok: true, catalog: {availability: "available", reason_code: null, default_pack_id: packs[0].pack_id, packs}, pack: {
    ...packs[selected === "d" ? 3 : selected === "c" ? 2 : selected === "b" ? 1 : 0], objective: "Measure actual utility", applicability: "current",
    ...(selected === "c" ? {} : {solver_usage: {artifact: "capture.jsonl", artifact_sha256: "d".repeat(64), applicability: selected === "b" ? "historical" : "current", reason_code: selected === "b" ? "source_changed" : null,
      projection: {session_id: "synthetic-session", method: "codex_complete_session_counters_v1", start: "2026-01-01T00:00:00Z", end: "2026-01-01T00:01:01Z", completed_turns: 1, counter_observations: 1,
        input_tokens: 1100, cached_input_tokens: 1000, uncached_input_tokens: 100, output_tokens: 20, elapsed_seconds: 61, turn_elapsed_seconds: 61,
        turns: [{turn_id: "synthetic-turn", start: "2026-01-01T00:00:00Z", end: "2026-01-01T00:01:01Z", first_line: 2, last_line: 3, elapsed_seconds: 61, usage: {input_tokens: 1100, cached_input_tokens: 1000, uncached_input_tokens: 100, output_tokens: 20}}], limitations: []}}}),
    episodes: selected === "c" ? [{id: "episode", question: "Is this experiment discriminative?", source_ids: ["source"], eligible_metrics: ["discrimination"]}]
      : [{id: "episode", question: "Does a useful diagnosis prove original-objective utility?", source_ids: ["source"], eligible_metrics: ["goal_alignment", "benefit_test", "discrimination", "information_gain", "evidence_adoption", "redundancy"]}, {id: "uncertain", question: "Was the later decision informed by this evidence?", source_ids: ["source"], eligible_metrics: ["evidence_adoption"]}],
    ...(["a", "b", "d"].includes(selected) ? {native_outcomes: {artifact: "native-outcomes.json", artifact_sha256: "9".repeat(64),
      applicability: selected === "b" ? "historical" : "current", reason_code: selected === "b" ? "source_changed" : null,
      projection: {method: "source_bound_native_outcomes_v1", scope, start: "2026-01-01T00:00:00Z", end: "2026-01-01T00:10:00Z",
        complete: selected !== "d", metric: {name: "error", unit: "points", higher_is_better: false}, sample_count: 3, valid_count: 2,
        ...Object.fromEntries(["initial", "best_valid", "retained", "final_valid"].map(key => [key, {submission_index: key === "initial" ? 1 : 3,
          result_id: key === "initial" ? "native-initial" : "native-final", candidate_ref: key === "initial" ? "candidate-first" : "candidate-retained",
          submitted_at: "2026-01-01T00:01:00Z", observed_at: "2026-01-01T00:02:00Z", value: key === "initial" ? 7 : 3,
          citation: {source_id: "source", first_line: 1, last_line: 2}}])), limitations: []}}} : {}),
    job: {status: "succeeded", attempt: 2, analysis_wall_seconds: 12, usage: {input_tokens: 30, output_tokens: 4}, config},
    result: {schema_version: "trajectory_assessment_v1", method_version: "research-episodes-v2", analysis_key: (selected === "c" ? "e" : "c").repeat(64), scope, selection,
      calibration: "uncalibrated_model", evidence_manifest: [{id: "source", artifact: "facts.txt", artifact_sha256: "f".repeat(64), first_line: 1}], limitations: ["Synthetic presentation fixture; not a scientific result"],
      metrics: [...(selected === "c" ? [{metric: "benefit_test", yes: 0, no: 0, unknown: 0, eligible: 0, assessed_fraction: null, missing_data_bounds: null}, {metric: "discrimination", yes: 0, no: 0, unknown: 1, eligible: 1, assessed_fraction: 0, missing_data_bounds: [0, 1]}]
        : [{metric: "benefit_test", yes: 0, no: 1, unknown: 0, eligible: 1, assessed_fraction: 1, missing_data_bounds: [0, 0]}, {metric: "evidence_adoption", yes: 1, no: 0, unknown: 1, eligible: 2, assessed_fraction: .5, missing_data_bounds: [.5, 1]}]),
        ...["goal_alignment", "information_gain", "redundancy", selected === "c" ? "evidence_adoption" : "discrimination"].map(metric => selected === "c" ? ({metric, yes: 0, no: 0, unknown: 0, eligible: 0, assessed_fraction: null, missing_data_bounds: null}) : ({metric, yes: metric === "redundancy" ? 0 : 1, no: metric === "redundancy" ? 1 : 0, unknown: 0, eligible: 1, assessed_fraction: 1, missing_data_bounds: metric === "redundancy" ? [0, 0] : [1, 1]}))],
      assessments: selected === "c" ? [{episode_id: "episode", metric: "discrimination", verdict: "unknown", reason: "Available evidence cannot separate the explanations.", citations: []}]
        : [{episode_id: "episode", metric: "benefit_test", verdict: "no", reason: "The diagnostic changed, but original-objective utility was not tested.", citations: [{source_id: "source", first_line: 1, last_line: 2}]}, {episode_id: "episode", metric: "evidence_adoption", verdict: "yes", reason: "A later decision explicitly used this evidence.", citations: [{source_id: "source", first_line: 1, last_line: 2}]}, {episode_id: "uncertain", metric: "evidence_adoption", verdict: "unknown", reason: "The later decision is not observed.", citations: []}, ...[
          {metric: 'goal_alignment', verdict: 'yes', reason: 'Synthetic evidence ties the intervention to the stated bottleneck.'},
          {metric: 'discrimination', verdict: 'yes', reason: 'Synthetic control separates the competing explanations.'},
          {metric: 'information_gain', verdict: 'yes', reason: 'Synthetic evidence resolves the declared uncertainty.'},
          {metric: 'redundancy', verdict: 'no', reason: 'Synthetic evidence records a new question rather than repeating a settled result.'}
        ].map(item => ({...item, episode_id: 'episode', citations: [{source_id: 'source', first_line: 1, last_line: 2}]}))]},
  }};
  if (selected === "b") {
    snapshot.pack.native_outcomes.projection.sample_count = 4;
    snapshot.pack.native_outcomes.projection.valid_count = 3;
    snapshot.pack.native_outcomes.projection.final_valid = {...snapshot.pack.native_outcomes.projection.final_valid,
      submission_index: 4, result_id: "native-later", candidate_ref: "candidate-later", value: 5};
  }
  if (["a", "b", "d"].includes(selected)) {
    const native = snapshot.pack.native_outcomes.projection;
    native.retained_improvement = {
      baseline: selected === "d" ? null : selected === "b" ? native.retained : native.initial,
      retention_citation: {source_id: "source", first_line: 1, last_line: 2},
      value: selected === "d" ? null : selected === "b" ? 0 : 4,
      final_candidate_delta: selected === "d" ? null : selected === "b" ? -2 : 4,
    };
  }
  if (selected === "d") {
    const usage = snapshot.pack.solver_usage.projection;
    usage.method = "codex_observed_session_counters_v1";
    usage.incomplete_turn = {turn_id: "unfinished", start: usage.end, observed_through: usage.end,
      first_line: 4, last_line: 5, counter_observations: 1,
      usage: {input_tokens: 400, cached_input_tokens: 300, uncached_input_tokens: 100, output_tokens: 8}};
    usage.input_tokens += 400; usage.cached_input_tokens += 300;
    usage.uncached_input_tokens += 100; usage.output_tokens += 8;
  }
  if (selected === "d") {snapshot.pack.result = null; snapshot.pack.job = null; snapshot.pack.native_outcomes.projection.initial = null;}
  return snapshot;
}

// Repeated question templates are valid source data. Presentation must keep
// their complete wording readable without repeating it in every collapsed row.
function comparisonReadback() {
  const snapshot = analysisReadback(), p = snapshot.pack;
  const names = ["variant-alpha", "variant-beta", "variant-gamma", "edge-aware"];
  p.episode_count = 4;
  p.episodes = names.map(id => ({id,
    question: `${id} 的实验是否检验原始目标，并被后续候选选择实际采用？负结果、控制差异与未知分别保留。`,
    source_ids: ["source"], eligible_metrics: p.result.metrics.map(row => row.metric)}));
  p.result.metrics = p.result.metrics.map(row => ({...row, yes: 1, no: 0, unknown: 3, eligible: 4, assessed_fraction: .25, missing_data_bounds: [.25, 1]}));
  p.result.assessments = p.result.metrics.flatMap(({metric}) => names.map((episode_id, index) => ({
    episode_id, metric, verdict: index === 3 ? "yes" : "unknown",
    reason: index === 3 ? "Synthetic evidence connects the intervention to the measured bottleneck."
      : "Synthetic evidence identifies the candidate, but does not establish the intervention or its relationship to the bottleneck.",
    citations: [{source_id: "source", first_line: 1, last_line: 2}],
  })));
  return snapshot;
}

export async function startHistoryOwner(fixture) {
  const fixtures = Array.isArray(fixture) ? fixture : fixture ? [fixture] : [];
  const child = spawn(resolveTestPython(), ["-u", "examples/personal-workspace-browser/research-history-backend.py", ...fixtures.flatMap(path => ["--fixture", path])],
    {cwd: repoRoot, env: {...process.env, LOOPX_USAGE_PING: "0"}, stdio: ["ignore", "pipe", "pipe"]});
  let diagnostic = "";
  child.stderr.on("data", chunk => {diagnostic = (diagnostic + chunk).slice(-4000);});
  const lines = createInterface({input: child.stdout});
  const close = async () => {
    lines.close();
    if (child.exitCode === null && child.signalCode === null) {
      const exited = new Promise(accept => child.once("exit", accept));
      child.kill("SIGTERM"); await exited;
    }
  };
  try {
    const ready = await new Promise((accept, reject) => {
      const timer = setTimeout(() => reject(new Error(`History owner startup timed out: ${diagnostic}`)), 20_000);
      child.once("error", error => {clearTimeout(timer); reject(error);});
      child.once("exit", () => {clearTimeout(timer); reject(new Error(`History owner exited: ${diagnostic}`));});
      lines.once("line", line => {
        clearTimeout(timer);
        try {accept(JSON.parse(line));} catch (failure) {reject(failure);}
      });
    });
    assert(Number.isSafeInteger(ready.port) && ready.port > 0);
    return {...ready, base: `http://127.0.0.1:${ready.port}`, close};
  } catch (failure) {await close(); throw failure;}
}

export const researchHistoryScenario = {
  id: "research-history",
  async run({browser, collectCoverage, url}) {
    const capture = JSON.parse(await readFile(resolve(repoRoot, "apps/presentation/dashboard/src/data/fixtures/edgebench-research-history.json")));
    let owner = await startHistoryOwner();
    let collisionOwner, emptyOwner, temporary;
    let context;
    const pages = [];
    const findingReads = [];
    let unavailable = false;
    let analysisFixture = false, holdOldAnalysis = false, slowStarted, releaseOld, oldDelivered;
    let holdCitation = false, failedCitation = false, citationStarted, releaseCitation;
    let unavailableAnalysis = false, episodeMetadataLimit = null, comparisonFixture = false;
    let analysisWrites = 0, localizedFixture = false, continuationFixture = false, equivalentContinuation = false;
    let unavailableTask = false;
    const taskReads = [];
    try {
      context = await openWorkspacePage(browser, url, {collectCoverage, beforeGoto: async (_, page) => {
        await page.route("**/api/chat/research-analysis**", async route => {
          if (route.request().method() !== "GET") {analysisWrites++; return route.fulfill({status: 403, json: {error: "Read-only presentation fixture"}});}
          if (!analysisFixture) return route.fulfill({json: {ok: true, catalog: {availability: "unavailable", reason_code: "no_available_pack", default_pack_id: null, packs: []}, pack: null}});
          const query = new URL(route.request().url()).searchParams;
          const language = query.get('output_language');
          assert(['zh', 'en'].includes(language), 'Every analysis and original-citation read binds the interface language');
          if (new URL(route.request().url()).pathname.endsWith('/citation')) {
            if (holdCitation) {citationStarted(); await new Promise(resolve => {releaseCitation = resolve;});}
            if (failedCitation) return route.fulfill({status: 503, json: {error: 'Synthetic citation transport unavailable'}});
            return route.fulfill({json: {
              source_id: 'source', first_line: 1, last_line: 2, artifact: 'facts.txt', applicability: 'current',
              text: 'Synthetic original evidence.\nDiagnostic measurements do not test native utility.'
            }});
          }

          const selected = ["b", "c", "d"].find(id => query.get("pack_id") === id.repeat(64)) ?? "a";
          if (holdOldAnalysis && selected === "a") {slowStarted(); await new Promise(resolve => {releaseOld = resolve;});}
          const readback = comparisonFixture ? comparisonReadback() : analysisReadback(selected);
          if (continuationFixture) {
            const p = readback.pack, base = selected === 'b';
            p.pack_id = (base ? 'b' : 'a').repeat(64);
            p.analysis_key = (base ? '8' : '9').repeat(64);
            p.scope = {...p.scope, revision: base ? 'original-capture' : 'later-evidence', window: base ? '09:00–12:00' : '09:00–14:00'};
            p.result.scope = p.scope; p.result.analysis_key = p.analysis_key;
            p.title = base ? 'Synthetic original analysis' : 'Synthetic analysis with later evidence';
            if (!base) p.continuation = {
              base_pack_id: 'b'.repeat(64), base_analysis_key: '8'.repeat(64), base_title: 'Synthetic original analysis',
              base_scope: {...p.scope, revision: 'original-capture', window: '09:00–12:00'}, current_scope: p.scope,
              sources: {added: ['follow-up'], revised: [], removed: []}, episodes: {added: [], revised: ['variant-alpha'], removed: []},
              can_reuse: false, configuration_changed: false, objective_changed: false, base_available: true, base_episode_count: 4,
              reassessed_episode_ids: p.episodes.map(e => e.id), new_pair_count: 0, removed_pair_count: 0,
              verdict_changes: [{episode_id: 'variant-alpha', metric: 'benefit_test', before: 'yes', after: 'unknown'}],
            };
          }
          for (const pack of [...readback.catalog.packs, readback.pack]) pack.config = {...pack.config, output_language: language};
          if (readback.pack.result) {
            readback.pack.result.output_language = language;
            if (localizedFixture) {
              readback.pack.result.episode_questions = readback.pack.episodes.map(episode => ({episode_id: episode.id,
                question: language === 'zh' ? `${episode.id} 是否检验原始目标？` : `Did ${episode.id} test the original objective?`}));
              for (const assessment of readback.pack.result.assessments) assessment.reason = language === 'zh'
                ? '来源只记录了实验结果，缺少干预与目标瓶颈的联系。' : 'The source records the result but not the intervention’s link to the objective bottleneck.';
              if (continuationFixture) for (const row of readback.pack.result.assessments) {
                if (row.metric === 'benefit_test') row.reason = language === 'zh' ? '后续对照未覆盖原始任务收益，判断保留未知。' : 'The later control does not cover original-task utility; the judgment remains unknown.';
                if (row.metric === 'evidence_adoption') row.reason = language === 'zh' ? '后续选择记录未标明采用版本，尚不能建立采用关系。' : 'The later selection does not identify an adopted revision, so adoption remains unestablished.';
              }
              readback.pack.result.limitations = [language === 'zh' ? '合成展示；不是科学校准结果。' : 'Synthetic presentation; not scientific calibration.'];
            }
          }
          if (episodeMetadataLimit === 0) delete readback.pack.episodes;
          else if (episodeMetadataLimit !== null) readback.pack.episodes = readback.pack.episodes.slice(-episodeMetadataLimit);
          if (equivalentContinuation && selected === 'a') {
            Object.assign(readback.pack.continuation, {can_reuse: true,
              sources: {added: [], revised: [], removed: []}, episodes: {added: [], revised: [], removed: []}, reassessed_episode_ids: [], verdict_changes: []});
            readback.pack.result = null; readback.pack.job = null;
          }
          await route.fulfill(unavailableAnalysis && selected === "a"
            ? {status: 503, json: {error: "Synthetic analysis read unavailable"}}
            : {json: readback});
          if (holdOldAnalysis && selected === "a") oldDelivered();
        });
        // The document URL also contains ?statusUrl=/status.json. Only proxy
        // the status endpoint; navigation must reach the frontend server.
        await page.route(url => url.pathname === "/status.json", async route => {
          const response = await fetchWorkspaceBackend(route, owner.base);
          assert.equal(response.status(), 200);
          await route.fulfill({response});
        });
        await page.route("**/api/chat/explore-results?*", async route => {
          const query = new URL(route.request().url()).searchParams;
          if (query.get("view") === "findings") findingReads.push({goal: query.get("goal_id"), cursor: query.get("cursor")});
          if (unavailable) return route.fulfill({status: 503, json: {error: "Isolated history transport unavailable"}});
          const response = await fetchWorkspaceBackend(route, owner.base);
          if (response.status() === 409) return route.fulfill({response});
          assert.equal(response.status(), 200);
          const value = await response.json();
          if (new URL(route.request().url()).searchParams.get("view") === "history") pages.push(value);
          await route.fulfill({response, json: value});
        });
        await page.route("**/api/chat/todo/detail?*", async route => {
          taskReads.push(new URL(route.request().url()).searchParams.get("goal_id"));
          if (unavailableTask) return route.fulfill({status: 503, json: {error: "Synthetic task source unavailable"}});
          await route.fulfill({response: await fetchWorkspaceBackend(route, owner.base)});
        });
      }});
      const {page, api} = context;
      const selectedNodes = () => {
        const raw = new URL(page.url()).searchParams.get('research_node');
        return raw?.startsWith('[') ? JSON.parse(raw) : raw ? [raw] : [];
      };
      await page.locator(".personal-goal-link", {hasText: capture.display_name}).click();
      const tabs = page.getByRole("navigation", {name: "Goal 视图"});
      assert.equal(pages.length, 0, "Overview must not fetch full research history");
      assert.equal(await page.locator(".research-history").count(), 0, "Research is not a second long section in Overview");
      await page.locator(".personal-goal-tabs").getByRole("button", {name: "研究", exact: true}).click();
      assert.equal(new URL(page.url()).searchParams.get("view"), "research");
      const history = page.getByRole("region", {name: "研究历程", exact: true});
      await history.getByRole("status").filter({hasText: "76 / 76"}).waitFor();
      assert.equal(pages.length, 2, "The complete Portfolio checkpoint requires every bounded HTTP page");
      assert.deepEqual(pages.flatMap(value => value.items).map(row => row.event_id), capture.events.map(row => row.event_id));
      await history.locator(".research-phases").waitFor();
      const early = history.locator(".research-phase").first();
      await early.click();
      assert.equal(new URL(page.url()).searchParams.get("research_phase"), "0");
      await history.getByRole("button", {name: "关系图", exact: true}).click();
      await page.goBack();
      assert.equal(await history.getByRole("button", {name: "时间线", exact: true}).getAttribute("aria-pressed"), "true");
      assert.equal(await early.getAttribute("aria-pressed"), "true");
      await page.goBack();
      assert.equal(await early.getAttribute("aria-pressed"), "false");
      await tabs.getByRole("button", {name: "概览", exact: true}).click();
      assert.equal(await history.isVisible(), false);
      await tabs.getByRole("button", {name: "研究", exact: true}).click();
      await history.waitFor();
      assert.equal(analysisWrites, 0, "Browsing and returning never starts analysis");
      assert.equal(await history.locator('.research-route').count(),0,'The solver recorded no routes or parent links');
      assert.equal(await history.locator('.research-decision-ticks span').count()>0,true,'Actual recorded decisions remain visible');
      await page.screenshot({path:resolve(outputDir,'research-history-portfolio-viewport.png'),animations:'disabled'});
      await history.evaluate(element=>element.scrollIntoView({block:'start'}));
      await page.screenshot({path:resolve(outputDir,'research-history-portfolio-timeline.png'),animations:'disabled'});
      const day = history.locator(".research-unlinked-days button").first();
      await day.click();
      const realDetail=history.locator('.research-history-detail');
      const questionName='Do the tested cost-gated one-session executions improve the frozen forecast strategy?';
      await realDetail.getByRole('button',{name:new RegExp(questionName.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'))}).waitFor();
      assert.equal(await realDetail.locator('.research-recorded-finding').count(),0,'A date group is a question index, not a stack of complete documents');
      await realDetail.getByRole('button',{name:new RegExp(questionName.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'))}).click();
      await realDetail.getByRole('heading',{name:'Do the tested cost-gated one-session executions improve the frozen forecast strategy?',exact:true}).waitFor();
      assert.match(await realDetail.textContent(),/daily trials1\.53-2\.99\/199-719%/);
      await realDetail.locator('.research-node-provenance > summary').click();
      assert.match(await realDetail.textContent(),/evidence:opportunity-cost-gates/);
      await realDetail.getByRole('button',{name:'返回问题列表',exact:true}).click();
      assert.equal(await realDetail.locator('.research-question-index button:focus').count(),1,'Closing a question restores its exact index control');
      const realDay=new URL(page.url()).searchParams.get('research_group');
      await page.reload({waitUntil:'networkidle'});
      await realDetail.waitFor();
      assert.equal(new URL(page.url()).searchParams.get('research_group'),realDay);
      assert(await realDetail.evaluate(e=>e===document.activeElement),'Reload restores the selected reader after its actual content mounts');
      await page.screenshot({path:resolve(outputDir,'research-history-portfolio-detail.png'),animations:'disabled'});
      await realDetail.press('Escape');
      await page.goBack();
      await realDetail.waitFor();
      await page.goForward();
      await realDetail.waitFor({state:'hidden'});
      await history.getByRole("button", {name: "关系图", exact: true}).click();
      assert.equal(await history.locator(".research-history-edges g").count(),0,'Do not manufacture source edges for a visually rich graph');
      assert.equal(await history.locator(".research-history-edges .is-grouping").count(),0);
      await page.screenshot({path:resolve(outputDir,'research-history-portfolio-graph.png'),animations:'disabled'});
      await history.getByRole('button',{name:'时间线',exact:true}).click();
      unavailable=true;
      await history.getByRole('button',{name:'刷新记录',exact:true}).click();
      await history.getByRole('alert').waitFor();
      assert.match(await history.textContent(),/76 \/ 76/,'A failed refresh retains the complete real capture');
      unavailable=false;
      await history.getByRole('button',{name:'刷新记录',exact:true}).click();
      await history.getByRole('alert').waitFor({state:'hidden'});
      await page.setViewportSize({width:390,height:844});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
      await page.screenshot({path:resolve(outputDir,'research-history-portfolio-mobile.png'),animations:'disabled'});
      await page.setViewportSize({width:1512,height:982});
      const checkpoints=[];
      for(const [file,label] of [['edgebench-graph-checkpoint-history.json','graph'],['edgebench-ad-checkpoint-history.json','ad']]) {
        const other=JSON.parse(await readFile(resolve(repoRoot,'apps/presentation/dashboard/src/data/fixtures',file)));
        const previousUrl = page.url();
        const previousSelection = selectedNodes();
        pages.length=0;
        await page.locator('.personal-goal-link',{hasText:other.display_name}).click();
        assert.equal(new URL(page.url()).searchParams.get('view'), 'research', 'Comparing Goals must keep the research reading context');
        await history.getByRole('status').filter({hasText:`${other.events.length} / ${other.events.length}`}).waitFor();
        assert.deepEqual(pages.flatMap(value=>value.items).map(row=>row.event_id),other.events.map(row=>row.event_id),`${label}: all original pages, without cross-Goal records`);
        assert.equal(await history.locator('.research-history-detail').count(),0, 'A different Goal must not adopt the prior evidence selection');
        assert.equal(await history.locator('.research-route').count(),0);
        await page.goBack();
        assert.equal(page.url(), previousUrl, 'Back restores the prior Goal and its own evidence URL');
        assert.deepEqual(selectedNodes(), previousSelection);
        await page.goForward();
        await history.getByRole('status').filter({hasText:`${other.events.length} / ${other.events.length}`}).waitFor();
        assert.equal(await history.locator('.research-history-detail').count(),0);
        await history.evaluate(element=>element.scrollIntoView({block:'start'}));
        await page.screenshot({path:resolve(outputDir,`research-history-${label}-timeline.png`),animations:'disabled'});
        await history.locator('.research-unlinked-days button').first().click();
        await realDetail.waitFor();
        assert.match(await realDetail.textContent(),new RegExp(other.events[0].title.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')));
        await page.screenshot({path:resolve(outputDir,`research-history-${label}-detail.png`),animations:'disabled'});
        checkpoints.push({goal_id:other.goal_id,event_count:other.events.length,history_pages:Math.ceil(other.events.length/40)});
      }
      // The saved-finding reader uses the same real backend and retains full
      // source content; metadata stays optional and failures cannot show stale facts.
      const saved = page.locator(".research-saved-evidence");
      await saved.locator(":scope > summary").click();
      const evidence = saved.locator(".goal-explore-results");
      await evidence.locator(".goal-team-result-reader").waitFor();
      const item = evidence.locator(".goal-team-result-list button").first();
      await item.focus(); await item.press("Enter");
      const reader = evidence.locator(".goal-team-result-reader");
      await page.waitForFunction(() => document.activeElement?.classList.contains("goal-team-result-reader"));
      assert(await reader.locator(".explore-finding-prose").count(), "Decode the existing writer format into readable sections");
      const bodyStyle = await reader.locator("p").first().evaluate(el => ({size: parseFloat(getComputedStyle(el).fontSize), color: getComputedStyle(el).color}));
      assert(bodyStyle.size >= 16, "Evidence is body text, not a wall of captions");
      await reader.getByText("来源与范围", {exact: true}).click();
      await reader.getByText("完整原文", {exact: true}).click();
      assert.match(await reader.locator(".explore-finding-raw").textContent(), /Input revision:/, "The complete original remains available");
      unavailable = true;
      await evidence.getByRole("button", {name: "刷新证据"}).click();
      await evidence.getByRole("alert").waitFor();
      assert.equal(await evidence.locator(".goal-team-result-reader").count(), 0);
      unavailable = false;
      await evidence.getByRole("button", {name: "刷新证据"}).click();
      await reader.waitFor();
      const selectedFinding = await reader.locator("h4").textContent();
      const readerScroll = await page.locator(".personal-channel-scroll").evaluate(el => el.scrollTop);
      await tabs.getByRole("button", {name: "概览", exact: true}).click();
      await tabs.getByRole("button", {name: "研究", exact: true}).click();
      await reader.waitFor();
      assert.equal(await reader.locator("h4").textContent(), selectedFinding);
      const returnedScroll = await page.locator(".personal-channel-scroll").evaluate(el => el.scrollTop);
      assert(Math.abs(returnedScroll - readerScroll) < 2, "Returning to Research preserves its reading position");
      // A populated mobile reader must expose every record without a long
      // horizontal card strip. The selected record remains complete below it.
      await page.setViewportSize({width:390, height:844});
      const picker = evidence.getByRole("combobox", {name:"选择结论"});
      await picker.waitFor();
      assert.equal(await evidence.getByRole("navigation", {name:"选择结论"}).isVisible(), false);
      const lastFinding = await picker.locator("option").last().getAttribute("value");
      await picker.selectOption(lastFinding);
      assert.equal(await reader.locator("h4").count(), 1);
      await reader.getByText("来源与范围", {exact:true}).click();
      assert((await reader.textContent()).includes(lastFinding), "Selected identity is available in full provenance");
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
      await page.setViewportSize({width:1512, height:982});
      await evidence.getByRole("button", {name:"下一页",exact:true}).click();
      await reader.waitFor();
      assert(findingReads.at(-1).cursor, "Exercise an actual later findings page");
      const findingGoal = findingReads.at(-1).goal;
      // Evict the saved snapshot through the production cache, without a timer
      // or a mocked expiry response. Refresh must offer a usable recovery path.
      for (let i = 0; i < 8; i++) {
        const response = await fetch(`${owner.base}/api/chat/explore-results?goal_id=${encodeURIComponent(findingGoal)}&view=findings`);
        assert.equal(response.status, 200);
        assert.equal((await response.json()).goal_id, findingGoal);
      }
      await evidence.getByRole("button", {name: "刷新证据"}).click();
      await evidence.getByRole("alert").waitFor();
      assert.equal(await reader.count(), 0, "Expired evidence cannot remain visible as current");
      await evidence.getByRole("button", {name: "刷新证据"}).click();
      await reader.waitFor();
      assert.equal(findingReads.at(-1).cursor, null, "Retry starts a new snapshot instead of replaying the expired cursor");
      assert.equal(await reader.locator("h4").textContent(), selectedFinding);
      await evidence.getByRole("button", {name: "下一页", exact: true}).click();
      await reader.waitFor();
      assert(findingReads.at(-1).cursor, "Recovered pagination still reaches later findings");
      const graphCapture = JSON.parse(await readFile(resolve(repoRoot,'apps/presentation/dashboard/src/data/fixtures/edgebench-graph-checkpoint-history.json')));
      const readsBeforeSwitch = findingReads.length;
      await page.locator('.personal-goal-link',{hasText:graphCapture.display_name}).click();
      await saved.locator(":scope > summary").click();
      await reader.waitFor();
      const newReads = findingReads.slice(readsBeforeSwitch).filter(read => read.goal === graphCapture.goal_id);
      assert(newReads.length > 0);
      assert(newReads.every(read => read.cursor === null), "A different Goal starts from its own first page, never the previous Goal's cursor");
      await saved.locator(":scope > summary").click();
      const readback=await(await fetch(`${owner.base}/fixture/readback`)).json();
      assert.equal(readback.source_unchanged,true);
      assert.equal(readback.mutation_attempts,0);
      await writeFile(resolve(outputDir,'research-history-readback.json'),JSON.stringify({
        source:'Three real research checkpoints; original text, timestamps, IDs and references',event_count:76,
        explicit_edges:0,parent_nodes:0,initial_pages:2,checkpoints,...readback,
      },null,2));

      // A separate, explicitly synthetic regression supplies the coincident
      // route absent from the real data. Never enrich the solver capture to
      // satisfy a test or make its graph appear better connected.
      temporary=await mkdtemp(resolve(tmpdir(),'loopx-history-targets-'));
      const goal='hit-target-fixture';
      const fixture=resolve(temporary,'capture.json');
      execFileSync(resolveTestPython(),['-c',`
import json, sys
from pathlib import Path
from loopx.capabilities.explore.result_log import build_explore_node_event, build_explore_finding_event
goal='hit-target-fixture'
events=[build_explore_node_event(goal_id=goal,node_id=f'hit-{index}',title=f'Synthetic hit target {index}',
    summary=('完整原始说明 retained without truncation. '*20) if index==7 else 'Pointer regression only',
    status='blocked' if index==5 else 'open',blocked_reason='Synthetic source unavailable' if index==5 else None,
    parent_id='hit-0' if index else None,
    recorded_at='2026-01-01T00:00:00Z' if index<9 else '2026-01-01T18:00:00Z') for index in range(10)]
events.append(build_explore_finding_event(goal_id=goal,finding_id='negative',node_id='hit-6',
    title='Synthetic negative',summary='Negative pointer regression',status='refuted',recorded_at='2026-01-01T00:00:00Z',
    evidence_refs=['probe:synthetic'], execution_observation={
        'schema_version':'explore_execution_observation_v0','candidate_sha256':'a'*64,
        'input_revision':'synthetic-v1','planned_method_id':'scheduler','executed_method_id':'mask-selection',
        'change_reason':'The control isolated a different first intervention.',
        'elapsed_seconds':0.2,'limit_seconds':30,'search_iterations':None,'exit_code':0,
        'stop_reason':'process_exit:0','evidence_refs':['probe:synthetic']}))
events.append(build_explore_finding_event(goal_id=goal,finding_id='unresolved',node_id='hit-6',
    title='Synthetic unresolved follow-up',summary='A later question remains unknown; the negative result is retained.',
    status='tentative',recorded_at='2026-01-01T18:00:00Z'))
Path(sys.argv[1]).write_text(json.dumps({'goal_id':goal,'display_name':'Synthetic pointer regression','events':events}))
`,fixture],{cwd:repoRoot});
      collisionOwner=await startHistoryOwner(fixture);
      const realOwner=owner; owner=collisionOwner;
      await realOwner.close();
      await page.setViewportSize({width:1280,height:900});
      await page.goto(new URL(`?goalId=${goal}&view=research&statusUrl=%2Fstatus.json`,url).href,{waitUntil:'networkidle'});
      await history.getByRole('status').filter({hasText:'12 / 12'}).waitFor();
      assert(await history.locator('.research-density .is-gap').count() > 0, 'The sparse capture includes recording gaps');
      const gapLegend = history.getByText('无记录', {exact: true});
      assert(await gapLegend.isVisible(), 'Recording-gap color must be explained without opening optional help');
      assert.equal(await gapLegend.locator('i').evaluate(e => getComputedStyle(e).backgroundColor),
        await history.locator('.research-density .is-gap').first().evaluate(e => getComputedStyle(e).backgroundColor),
        'The visible key identifies the actual recording-gap marks');
      const barLegend = history.getByText('柱高=事件数', {exact: true});
      assert(await barLegend.isVisible(), 'The bar-height meaning must be visible without opening optional help');
      assert.equal(await barLegend.locator('i').evaluate(e => getComputedStyle(e).borderTopColor),
        await history.locator('.research-density span:not(.is-gap)').first().evaluate(e => getComputedStyle(e).borderTopColor),
        'The visible bar key matches the real density bars');
      // The default reading must survive a locale switch, not only the zh copy.
      await page.evaluate(key => localStorage.setItem(key, 'en'), 'loopx-pw-locale');
      await page.reload({waitUntil: 'networkidle'});
      const englishHistory = page.getByRole('region', {name: 'Research history', exact: true});
      await englishHistory.getByText('Bar height = events', {exact: true}).waitFor()
        .catch(error => {throw new Error(`The English reading keeps the bar-height key: ${error.message}`);});
      await englishHistory.getByText('No records', {exact: true}).waitFor()
        .catch(error => {throw new Error(`The English reading keeps the recording-gap key: ${error.message}`);});
      await page.evaluate(key => localStorage.setItem(key, 'zh-CN'), 'loopx-pw-locale');
      await page.reload({waitUntil: 'networkidle'});
      await history.getByText('柱高=事件数', {exact: true}).waitFor();
      const target = await history.locator(".research-timeline-marker").first().evaluate(button => {
        const box = button.getBoundingClientRect(), dot = getComputedStyle(button, "::after");
        const count=button.querySelector('span');
        const text=document.createRange();
        if (count) text.selectNodeContents(count);
        const textBox=count ? text.getBoundingClientRect() : box;
        return {width: box.width, height: box.height, dotWidth: dot.width, dotHeight: dot.height, border: dot.borderTopStyle,
          countOffset: Math.abs(textBox.x+textBox.width/2-box.x-box.width/2)};
      });
      assert(target.width >= 44 && target.height >= 44, "A readable marker has a separate touch target");
      assert.equal(target.dotWidth, target.dotHeight, "A point is not stretched into a status bar by button minimums");
      assert.notEqual(target.border,'none','A grouped count has a visible circle, not a bare number');
      assert(target.countOffset<1,'The count is centred in its own marker, not beside an empty circle');
      for (const viewport of [{width: 1280, height: 900}, {width: 390, height: 844}]) {
        await page.setViewportSize(viewport);
        assert(await gapLegend.isVisible(), 'The recording-gap key remains visible on desktop and narrow layouts');
        assert(await barLegend.isVisible(), 'The bar-height key remains visible on desktop and narrow layouts');
        const target = history.locator('.research-route-track [data-research-nodes~="hit-6"]');
        await target.scrollIntoViewIfNeeded();
        await page.screenshot({path: resolve(outputDir, `research-history-targets-${viewport.width}.png`), animations:'disabled'});
        // Actual pointer targeting preserves every declared member in a compact
        // index. A chosen question opens its complete evidence, never a neighbour.
        const ids = (await target.getAttribute('data-research-nodes')).split(' ');
        const box = await target.boundingBox();
        await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
        const detail = history.locator('.research-history-detail');
        await detail.waitFor();
        assert.deepEqual(selectedNodes(),ids);
        assert(ids.includes('hit-6'));
        for (const id of ids) assert.equal(await detail.locator(`[data-research-node="${id}"]`).count(),1,'Every grouped member remains reachable');
        assert.equal(await detail.locator('.research-recorded-finding').count(),0,'Opening a large group does not expand all original evidence');
        assert.match(await detail.innerText(),/Synthetic source unavailable/,'A blocked exception remains visible in the compact index');
        const widthBefore = await history.locator('.research-history-canvas').evaluate(e=>e.getBoundingClientRect().width);
        await detail.locator('[data-research-node="hit-7"] button').click();
        assert.equal(await detail.locator('.research-reader-content > p').filter({hasText:'完整原始说明'}).innerText(),'完整原始说明 retained without truncation. '.repeat(20).trim(),'A question with no finding still leads with its complete recorded description');
        const reading = await detail.evaluate(e=>({font:parseFloat(getComputedStyle(e).fontSize),maxHeight:getComputedStyle(e).maxHeight}));
        assert(reading.font >= 16, 'Original prose is reading text, not a small metadata label');
        assert.equal(reading.maxHeight,'none','Complete evidence uses the page scroll, not a second fixed-height text trap');
        await detail.getByRole('button',{name:'返回问题列表',exact:true}).click();
        assert(await detail.locator('[data-research-node="hit-7"] button').evaluate(e=>e===document.activeElement));
        await page.screenshot({path:resolve(outputDir, `research-question-index-${viewport.width}.png`), animations:'disabled'});
        await detail.locator('[data-research-node="hit-6"] button').click();
        assert.equal(await history.locator('.research-history-canvas').evaluate(e=>e.getBoundingClientRect().width),widthBefore,'Reading never squeezes the whole-history graph');
        assert.equal(await detail.locator('.research-recorded-finding').count(),1,'A question reads one complete finding at a time');
        await detail.getByRole('combobox',{name:'选择问题结论',exact:true}).selectOption('negative');
        await detail.getByText('实际执行与资源观测', {exact:true}).click();
        assert(await detail.getByText('scheduler → mask-selection', {exact:true}).isVisible());
        assert(await detail.getByText('未测量', {exact:true}).isVisible(), 'Unknown iterations must not become zero');
        assert(await detail.getByText('0.2 / 30', {exact:true}).isVisible());
        await page.screenshot({path:resolve(outputDir, `research-execution-${viewport.width}.png`), animations:'disabled'});
        assert.deepEqual(selectedNodes(),['hit-6']);
        await detail.getByRole('heading',{name:'Synthetic hit target 6',exact:true}).waitFor();
        await detail.press('Escape');
        assert.deepEqual(selectedNodes(),ids,'Escape returns to the original group');
        assert(await detail.locator('[data-research-node="hit-6"] button').evaluate(e=>e===document.activeElement));
        await page.reload({waitUntil:'networkidle'});
        await detail.waitFor();
        assert.deepEqual(selectedNodes(),ids,'Grouped selection survives reload');
        await detail.press('Escape');
        await page.goBack();
        await detail.waitFor();
        assert.deepEqual(selectedNodes(),ids,'Browser Back restores the group');
        await page.goForward();
        await detail.waitFor({state:'hidden'});
        await target.focus();
        await target.press('Enter');
        await detail.waitFor();
        assert.deepEqual(selectedNodes(),ids,'Keyboard and pointer expose the same records');
        await detail.press('Escape');
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
        const collisions = await history.locator('.research-route-track, .research-swarm').evaluateAll(tracks => tracks.flatMap(track => {
          const boxes = [...track.querySelectorAll('button')].map(button => button.getBoundingClientRect()).sort((a,b) => a.x-b.x);
          return boxes.slice(1).filter((box,index) => boxes[index].right > box.left + .1);
        }));
        assert.equal(collisions.length,0,'No adjacent target can steal a pointer hit after resize');
      }
      await page.setViewportSize({width:1280,height:900});
      await history.evaluate(element => {for (let parent=element.parentElement;parent;parent=parent.parentElement) parent.scrollTop=0;});
      await page.screenshot({path: resolve(outputDir, 'research-history-grouped-viewport.png'), animations:'disabled'});
      await history.screenshot({path: resolve(outputDir, "research-history-recorded-timeline.png"), animations: "disabled"});
      await history.getByRole("button", {name: "关系图", exact: true}).click();
      assert.equal(await history.locator(".research-history-edges g").count(),0);
      assert.equal(await history.locator(".research-history-edges .is-grouping").count(),9);
      await page.screenshot({path: resolve(outputDir, "research-history-recorded-overview.png"), animations: "disabled"});
      await history.getByRole("button", {name: /^Synthetic hit target 0/}).click();
      await history.locator(".research-history-detail").waitFor();
      const selected = new URL(page.url()).searchParams.get("research_node");
      await page.reload({waitUntil: "networkidle"});
      await history.locator(".research-history-detail").waitFor();
      assert.equal(new URL(page.url()).searchParams.get("research_node"), selected);
      assert(await history.locator('.research-history-detail').evaluate(e=>e===document.activeElement),'Saved evidence focus is restored after reload');
      assert.match(await history.textContent(), /12 \/ 12/);
      await page.screenshot({path: resolve(outputDir, "research-history-recorded-desktop.png"), animations: "disabled"});
      await history.screenshot({path: resolve(outputDir, "research-history-recorded-detail.png"), animations: "disabled"});
      unavailable = true;
      await history.getByRole("button", {name: "刷新记录", exact: true}).click();
      await history.getByRole("alert").waitFor();
      assert.match(await history.textContent(), /12 \/ 12/, "A failed refresh cannot erase a verified complete capture");
      assert.equal(new URL(page.url()).searchParams.get("research_node"), selected);
      unavailable = false;
      await history.getByRole("button", {name: "刷新记录", exact: true}).click();
      await history.getByRole("alert").waitFor({state: "hidden"});
      await history.locator(".research-history-detail").press("Escape");
      assert.equal(await history.locator(".research-history-detail").count(), 0);
      await page.setViewportSize({width: 390, height: 844});
      await page.emulateMedia({reducedMotion: "reduce"});
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await page.screenshot({path: resolve(outputDir, "research-history-recorded-mobile.png"), animations: "disabled"});
      // Same Goal, different packs with the same analysis key: an old poll
      // must not replace the newly selected pack or its interpretation.
      analysisFixture = true;
      await page.reload({waitUntil: "networkidle"});
      const progress = history.getByRole("region", {name: "长程效果与用量", exact: true});
      await progress.locator(`.research-progress-scope[data-pack-id="${"a".repeat(64)}"]`).waitFor();
      const effects = progress.locator('.research-effect-metrics');
      await effects.waitFor();
      assert.equal(await effects.locator('.research-effect-metric').count(), 6, 'All six analyzer metrics are primary without opening analysis or cost details');
      assert.equal(await progress.locator('.research-progress-basis').evaluate(e => e.open), false, 'Sources and cost share one secondary disclosure by default');
      // Optional methodology must be reachable without crowding the results,
      // changing counts or invoking analysis. Exercise the same disclosure with
      // pointer, keyboard and touch-sized layout, including Escape in a panel.
      const metricHelp = progress.getByRole('button', {name: '指标说明', exact: true});
      const help = progress.getByRole('tooltip');
      assert.equal(await help.isVisible(), false);
      assert.match(await progress.innerText(), /未校准/);
      assert.doesNotMatch(await progress.innerText(), /范围外或未捕获/);
      await metricHelp.hover();
      await help.waitFor({state: 'visible'});
      await help.hover();
      assert.match(await help.innerText(), /未知仍在分母/);
      await page.keyboard.press('Escape');
      await help.waitFor({state: 'hidden'});
      await metricHelp.focus();
      await help.waitFor({state: 'visible'});
      await page.keyboard.press('Escape');
      assert.equal(await metricHelp.evaluate(e => e === document.activeElement), true);
      await page.setViewportSize({width: 390, height: 844});
      await metricHelp.click();
      await help.waitFor({state: 'visible'});
      const helpBounds = await help.boundingBox();
      assert(helpBounds.x >= 0 && helpBounds.x + helpBounds.width <= 390);
      await progress.getByRole('heading', {name: '研究效果判读', exact: true}).click();
      await help.waitFor({state: 'hidden'});
      assert.match(await effects.locator('[data-metric="evidence_adoption"]').innerText(), /1 \/ 2.*未知 1/s);
      assert.doesNotMatch(await effects.locator('[data-metric="evidence_adoption"]').innerText(), /不适用/,
        'An unknown eligible episode stays in the denominator, not in exclusions');
      await page.setViewportSize({width: 1280, height: 900});
      const benefitMetric = effects.locator('[data-metric="benefit_test"]');
      const metricDetail = progress.locator('.research-effect-evidence');
      assert.match(await benefitMetric.innerText(), /0 \/ 1/);
      await page.setViewportSize({width: 1440, height: 1000});
      await progress.evaluate(element => element.scrollIntoView({block: 'start'}));
      await page.screenshot({path: resolve(outputDir, 'research-effect-viewport.png'), animations: 'disabled'});
      const metricGeometry = await effects.locator('.research-effect-metric').evaluateAll(elements => elements.map(element => ({
        top: Math.round(element.getBoundingClientRect().top),
        labelSize: parseFloat(getComputedStyle(element.querySelector('.research-effect-title')).fontSize),
        numberSize: parseFloat(getComputedStyle(element.querySelector('.research-effect-number')).fontSize),
      })));
      assert.equal(new Set(metricGeometry.map(item => item.top)).size, 1,
        'A wide viewport gives the six primary effects one compact band, leaving room for the history');
      assert(metricGeometry.every(item => item.labelSize >= 14 && item.numberSize >= 24),
        'Primary metric labels and values use readable body/title typography, not metadata type');
      assert.doesNotMatch(await effects.innerText(), /否 0|未知 0/,
        'Routine zero-valued qualifiers do not repeat beneath every metric');
      await page.setViewportSize({width: 390, height: 844});
      await progress.evaluate(element => element.scrollIntoView({block: 'start'}));
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await page.screenshot({path: resolve(outputDir, 'research-effect-mobile-viewport.png'), animations: 'disabled'});
      await page.setViewportSize({width: 1280, height: 900});
      await page.screenshot({path: resolve(outputDir, 'research-eligibility-desktop.png'), animations: 'disabled'});
      assert.match(await benefitMetric.innerText(), /不适用 1/,
        'The sample includes a second episode explicitly excluded from this metric');
      const metricPositions = () => effects.locator('.research-effect-metric').evaluateAll(items => items.map(item => ({x: item.offsetLeft, y: item.offsetTop, width: item.offsetWidth})));
      const closedPositions = await metricPositions();
      await benefitMetric.focus(); await benefitMetric.press('Enter');
      assert.deepEqual(await metricPositions(), closedPositions, 'Reading evidence must not reorder or resize any of the six comparable metrics');
      await page.waitForFunction(() => document.activeElement?.classList.contains('research-effect-evidence'));
      assert.equal(await metricDetail.evaluate(element => element === document.activeElement), true, 'Keyboard opening reaches the evidence region');
      assert.equal(await effects.locator('[aria-expanded="true"]').count(), 1);
      await metricDetail.press('Escape');
      assert.equal(await benefitMetric.evaluate(element => element === document.activeElement), true, 'Escape returns to the same metric');
      await benefitMetric.press('Enter');
      await metricDetail.locator('.research-assessment > summary').click();
      assert.match(await metricDetail.innerText(), /original-objective utility was not tested/);
      assert.match(await metricDetail.locator('.research-effect-excluded').innerText(), /Was the later decision informed by this evidence\?/,
        'One expansion identifies the declared exclusion rather than requiring the selection-rule prose');
      await metricDetail.locator('.research-effect-excluded').scrollIntoViewIfNeeded();
      await page.screenshot({path: resolve(outputDir, 'research-eligibility-detail.png'), animations: 'disabled'});
      await page.setViewportSize({width: 390, height: 844});
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await metricDetail.locator('.research-effect-excluded').scrollIntoViewIfNeeded();
      await page.screenshot({path: resolve(outputDir, 'research-eligibility-mobile.png'), animations: 'disabled'});
      await page.setViewportSize({width: 1280, height: 900});
      assert.match(await effects.locator('[data-metric="evidence_adoption"]').innerText(), /未知 1/);

      assert.equal(await history.getByRole("region", {name: "轨迹深度分析", exact: true}).count(), 0, "Expanding existing effects never opens or executes model analysis");
      const sourceTrigger = metricDetail.getByRole('button', {name: /source.*行 1–2/});
      await sourceTrigger.click();
      const citedOriginal = history.getByRole('region', {name: '引用原文', exact: true});
      await citedOriginal.getByText(/Diagnostic measurements/).waitFor();
      assert.match(await citedOriginal.innerText(), /Diagnostic measurements do not test native utility/);
      const analysisClosed = async () => {
        assert.equal(await history.getByRole('region', {name: '轨迹深度分析', exact: true}).count(), 0,
          'Reading one original must not expand a second list of the same analysis episodes');
        assert.equal(new URL(page.url()).searchParams.has('research_analysis'), false,
          'Citation reads preserve the explicit analysis navigation state');
      };
      await analysisClosed();
      const sourceReached = () => page.waitForFunction(() => {
        const source = document.querySelector('[aria-label="引用原文"]');
        const rect = source?.getBoundingClientRect();
        return source === document.activeElement && rect.top >= 0 && rect.bottom <= innerHeight;
      });
      await sourceReached();
      await page.screenshot({path: resolve(outputDir, 'research-citation-desktop.png'), animations: 'disabled'});
      await citedOriginal.press('Escape');
      await page.waitForFunction(() => document.activeElement?.closest('.research-effect-evidence'));
      assert(await sourceTrigger.evaluate(element => element === document.activeElement), 'Closing the source returns to the exact citation trigger');
      await page.setViewportSize({width: 390, height: 844});
      holdCitation = true;
      const citationPending = new Promise(resolve => {citationStarted = resolve;});
      await sourceTrigger.click();
      await citationPending;
      await sourceReached();
      assert.match(await citedOriginal.innerText(), /正在读取引用原文/);
      releaseCitation();
      await citedOriginal.getByText(/Diagnostic measurements/).waitFor();
      await sourceReached();
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await analysisClosed();
      await page.screenshot({path: resolve(outputDir, 'research-citation-mobile.png'), animations: 'disabled'});
      await citedOriginal.press('Escape');
      // Closing just the original invalidates its pending response too.
      const lateCitation = new Promise(resolve => {citationStarted = resolve;});
      await sourceTrigger.click();
      await lateCitation;
      await citedOriginal.getByRole('button', {name: '关闭原文', exact: true}).click();
      const citationDelivered = page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/citation'));
      releaseCitation();
      await citationDelivered;
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      holdCitation = false;
      assert.equal(await citedOriginal.count(), 0);
      assert(await sourceTrigger.evaluate(element => element === document.activeElement));
      await analysisClosed();
      failedCitation = true;
      await sourceTrigger.click();
      await citedOriginal.getByRole('alert').waitFor();
      await sourceReached();
      assert.match(await citedOriginal.innerText(), /Synthetic citation transport unavailable/);
      await analysisClosed();
      await citedOriginal.press('Escape');
      failedCitation = false;
      await sourceTrigger.click();
      await citedOriginal.getByText(/Diagnostic measurements/).waitFor();
      await sourceReached();
      await citedOriginal.press('Escape');
      await analysisClosed();
      await page.setViewportSize({width: 1280, height: 900});
      await progress.locator('.research-progress-basis > summary').click();
      assert.equal(await progress.locator(".research-progress-values dd").filter({hasText: /^100 tokens$/}).count(), 1, "Uncached input is shown without charging cache twice");
      // Actual observations precede provenance; details and help remain operable.
      assert.equal(await progress.locator('.research-usage-detail').evaluate(e => e.open), false);
      assert.doesNotMatch(await progress.innerText(), /codex_complete_session_counters_v1/);
      await progress.locator('.research-usage-detail > summary').focus();
      await page.keyboard.press('Enter');
      assert.match(await progress.innerText(), /1,?100.*1,?000/s);
      assert.match(await progress.innerText(), /codex_complete_session_counters_v1/);
      await progress.locator('.research-usage-detail > summary').press('Enter');
      assert.match(await progress.innerText(), /30.*4.*tokens/s, "Analysis attempt cost is distinct from solver usage");
      await progress.getByRole('button', {name: '分析开销范围', exact: true}).focus();
      assert.match(await progress.getByRole('tooltip').innerText(), /更早失败.*恢复.*评测/s);
      await page.keyboard.press('Escape');
      await progress.locator('.research-progress-basis > summary').click();
      for (const limit of [1, 0]) {
        episodeMetadataLimit = limit;
        await page.reload({waitUntil: 'networkidle'});
        await progress.locator(`.research-progress-scope[data-pack-id="${"a".repeat(64)}"]`).waitFor();
        assert.match(await benefitMetric.innerText(), /0 \/ 1/);
        assert.doesNotMatch(await benefitMetric.innerText(), /不适用/,
          'Partial or absent metadata cannot establish exclusions from the arithmetic count gap');
        assert.match(await effects.locator('[data-metric="evidence_adoption"]').innerText(), /未知 1/);
      }
      episodeMetadataLimit = null;
      await page.evaluate(() => localStorage.setItem('loopx-pw-locale', 'en'));
      await page.reload({waitUntil: 'networkidle'});
      const englishBenefit = page.locator('.research-progress [data-metric="benefit_test"]');
      await englishBenefit.waitFor();
      assert.match(await englishBenefit.innerText(), /Ineligible 1/);
      await englishBenefit.focus();
      await englishBenefit.press('Enter');
      assert.match(await page.locator('.research-progress .research-effect-excluded').innerText(), /Was the later decision informed/);
      await page.evaluate(() => localStorage.setItem('loopx-pw-locale', 'zh-CN'));
      await page.reload({waitUntil: 'networkidle'});
      await progress.locator(`.research-progress-scope[data-pack-id="${"a".repeat(64)}"]`).waitFor();
      const basis = progress.locator('.research-progress-basis');
      assert.equal(await basis.evaluate(e => e.open), false);
      const improvement = progress.getByRole('region', {name: '已采用成果增量', exact: true});
      assert.equal(await improvement.locator('[data-retained-improvement]').textContent(), '+4');
      await improvement.getByRole('button', {name: '成果增量说明', exact: true}).focus();
      await improvement.getByRole('tooltip').waitFor({state: 'visible'});
      await page.keyboard.press('Escape');
      await improvement.locator('summary').click();
      await improvement.getByRole('button', {name: '查看原文', exact: true}).click();
      await citedOriginal.getByText(/Synthetic original evidence/).waitFor();
      await citedOriginal.press('Escape');
      await analysisClosed();
      await improvement.locator('summary').click();
      await basis.locator(':scope > summary').click();
      const nativeResults = progress.getByRole('region', {name: '原生测量', exact: true});
      assert.match(await nativeResults.innerText(), /首次提交.*7.*提交 1/s, 'Detailed native measurements use the existing source disclosure');
      assert.match(await nativeResults.innerText(), /原生保留结果.*3.*提交 3/s);
      assert.match(await nativeResults.innerText(), /越低越好/);
      await nativeResults.getByRole('button', {name: /首次提交.*查看原始证据/}).scrollIntoViewIfNeeded();
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Native details remain readable on a narrow screen');
      await page.screenshot({path: resolve(outputDir, "research-native-outcomes-mobile.png"), animations: "disabled"});
      await page.setViewportSize({width:1280,height:900});
      await page.screenshot({path: resolve(outputDir, "research-native-outcomes-desktop.png"), animations: "disabled"});
      await nativeResults.getByRole('button', {name: /首次提交.*查看原始证据/}).click();
      await citedOriginal.waitFor();
      await citedOriginal.getByText(/Synthetic original evidence/).waitFor();
      assert.match(await citedOriginal.innerText(), /Synthetic original evidence/);
      await citedOriginal.press('Escape');
      await analysisClosed();
      await basis.locator(':scope > summary').click();
      await benefitMetric.click();
      assert.match(await progress.innerText(), /0 \/ 1/);
      assert.match(await progress.innerText(), /1 \/ 2/);
      await page.setViewportSize({width:1280,height:900});
      await page.evaluate(() => {for (const e of document.querySelectorAll('*')) if(e.scrollTop) e.scrollTop=0;});
      await page.screenshot({path: resolve(outputDir, "research-analysis-first-screen.png"), animations: "disabled"});
      await progress.screenshot({path: resolve(outputDir, "research-analysis-summary.png"), animations: "disabled"});

      await history.getByRole("button", {name: "查看分析", exact: true}).click();
      const analysis = history.getByRole("region", {name: "轨迹深度分析", exact: true});
      await analysis.locator("header p").first().filter({hasText: /^Synthetic analysis A$/}).waitFor();
      assert.match(await analysis.innerText(), /原目标收益|original-objective/);
      assert.equal(await analysis.locator('.analysis-episode > summary').count(), 2, 'The reader starts with one comparable row per episode');
      const firstEpisode = analysis.locator('.analysis-episode').first();
      assert.equal(await analysis.locator('.analysis-episode[open]').count(), 0);
      assert.match(await firstEpisode.locator(':scope > summary').innerText(), /Does a useful diagnosis prove original-objective utility\?.*收益检验.*尚未检验收益.*证据采用.*有依据/s);
      assert.equal(await analysis.locator('.analysis-citations button:visible').count(), 0);
      await firstEpisode.locator(':scope > summary').focus();
      await firstEpisode.locator(':scope > summary').press('Enter');
      assert.match(await firstEpisode.innerText(), /utility was not tested/);
      assert.equal(await firstEpisode.getByText('The diagnostic changed, but original-objective utility was not tested.', {exact: true}).count(), 1,
        'A rationale appears once, not again in the other judgments');
      const analysisCitation = firstEpisode.locator('.analysis-citations button').first();
      await analysisCitation.click();
      await citedOriginal.getByText(/Diagnostic measurements/).waitFor();
      await sourceReached();
      assert.equal(await analysis.isVisible(), true, 'An explicitly opened analysis stays open when reading its original');
      await citedOriginal.press('Escape');
      await page.waitForFunction(() => document.activeElement?.closest('.analysis-citations'));
      assert(await analysisCitation.evaluate(element => element === document.activeElement));
      holdCitation = true;
      const analysisCitationPending = new Promise(resolve => {citationStarted = resolve;});
      await analysisCitation.click();
      await analysisCitationPending;
      await analysis.getByRole('button', {name: '关闭分析', exact: true}).click();
      const analysisCitationDelivered = page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/citation'));
      releaseCitation();
      await analysisCitationDelivered;
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      holdCitation = false;
      assert.equal(await citedOriginal.count(), 0, 'Closing the parent cancels a pending original without reopening either reader');
      await analysisClosed();
      await page.waitForFunction(() => document.activeElement?.getAttribute('aria-expanded') === 'false' && /查看分析/.test(document.activeElement.textContent));
      await history.getByRole('button', {name: '查看分析', exact: true}).click();
      await firstEpisode.locator(':scope > summary').click();
      await firstEpisode.locator(':scope > summary').press('Enter');
      await analysis.getByRole('button', {name: '分析范围', exact: true}).focus();
      await analysis.getByRole('tooltip').waitFor({state: 'visible'});
      await page.keyboard.press('Escape');
      await analysis.getByRole('tooltip').waitFor({state: 'hidden'});
      assert.equal(await analysis.isVisible(), true, 'Dismissing help must not close the analysis or lose context');
      await firstEpisode.locator(':scope > summary').click();
      holdCitation = true;
      const scopedCitationPending = new Promise(resolve => {citationStarted = resolve;});
      await analysisCitation.click();
      await scopedCitationPending;
      const started = new Promise(resolve => {slowStarted = resolve;});
      const delivered = new Promise(resolve => {oldDelivered = resolve;});
      holdOldAnalysis = true;
      await analysis.getByRole("button", {name: "刷新分析结果", exact: true}).click();
      await started;
      await progress.getByRole("combobox").focus();
      await progress.getByRole("combobox").selectOption("b".repeat(64));
      await analysis.locator("header p").first().filter({hasText: /^Synthetic analysis B$/}).waitFor();
      assert.equal(await progress.getByRole("combobox").evaluate(select => select === document.activeElement), true, "The source selector keeps keyboard focus across loading");
      const scopedCitationDelivered = page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/citation'));
      releaseCitation(); await scopedCitationDelivered; holdCitation = false;
      releaseOld(); await delivered; holdOldAnalysis = false;
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      assert.equal(await citedOriginal.count(), 0, 'A late original from a previous pack cannot appear in the new scope');
      assert.equal(await progress.getByRole('combobox').evaluate(select => select === document.activeElement), true);
      assert.equal(await analysis.locator("header p").first().textContent(), "Synthetic analysis B", "A delayed same-Goal read cannot undo a pack selection");
      assert.match(await progress.innerText(), /历史捕获|historical/i);
      assert.equal(await improvement.locator('[data-retained-improvement]').textContent(), '0', 'A rejected candidate does not create realized gain');
      await basis.locator(':scope > summary').click();
      assert.match(await nativeResults.innerText(), /历史原生测量.*当前结果未核验/s, 'Stale measurements never appear current');
      assert.match(await nativeResults.locator('[data-native-point="retained"]').innerText(), /3.*提交 3/s);
      assert.match(await nativeResults.locator('[data-native-point="final_valid"]').innerText(), /5.*提交 4/s, 'The last submission is not silently presented as retained or best');
      await page.reload({waitUntil: "networkidle"});
      await analysis.locator("header p").first().filter({hasText: /^Synthetic analysis B$/}).waitFor();
      await page.setViewportSize({width: 390, height: 844});
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await page.screenshot({path: resolve(outputDir, "research-analysis-mobile.png"), animations: "disabled"});
      // Browser history changes source identity too. While A is delayed or
      // unavailable, B's interpretation/cost must not be shown under A's URL.
      // Use the real selector-created history entry, rather than fabricating
      // pushState here: source selection itself must preserve Back/Forward.
      const backStarted = new Promise(resolve => {slowStarted = resolve;});
      const backDelivered = new Promise(resolve => {oldDelivered = resolve;});
      holdOldAnalysis = unavailableAnalysis = true;
      await page.goBack();
      await backStarted;
      try {
        assert.equal(await progress.getByRole("combobox").inputValue(), "a".repeat(64));
        assert.equal(await progress.locator(".research-effect-metrics").count(), 0,
          "A pending history read cannot borrow the previously displayed pack's judgments");
        assert.equal(await progress.locator(".research-progress-scope").count(), 0,
          "The previous pack's displayed scope is cleared, while source options remain available");
      } finally {releaseOld(); await backDelivered; holdOldAnalysis = false;}
      await analysis.getByRole("alert").waitFor();
      assert.equal(await progress.locator(".research-effect-metrics").count(), 0,
        "A failed history read cannot retain another pack's judgments");
      assert.equal(await progress.getByRole("status").count(), 0,
        "An unavailable source is not an indefinitely loading source");
      await progress.scrollIntoViewIfNeeded();
      await page.screenshot({path: resolve(outputDir, "research-analysis-history-unavailable-mobile.png"), animations: "disabled"});
      unavailableAnalysis = false;
      await analysis.getByRole("button", {name: "重试读取", exact: true}).click();
      await analysis.locator("header p").first().filter({hasText: /^Synthetic analysis A$/}).waitFor();
      await page.goForward();
      await analysis.locator("header p").first().filter({hasText: /^Synthetic analysis B$/}).waitFor();
      await analysis.press("Escape");
      await analysis.waitFor({state: "hidden"});
      assert.equal(await history.getByRole("button", {name: "查看分析", exact: true}).evaluate(button => button === document.activeElement), true);
      await progress.getByRole("combobox").focus();
      await progress.getByRole("combobox").selectOption("c".repeat(64));
      await progress.locator(`.research-progress-scope[data-pack-id="${"c".repeat(64)}"]`).waitFor();
      assert.match(await progress.locator('.research-progress-basis > summary').innerText(), /完整成本未知/);
      await progress.locator('.research-progress-basis > summary').click();
      assert.match(await progress.innerText(), /尚无用量捕获|No usage capture/);
      await progress.getByRole('button', {name: '用量范围与成本', exact: true}).focus();
      assert.doesNotMatch(await progress.getByRole('tooltip').innerText(), /本次完整捕获|This complete capture/);
      await page.keyboard.press('Escape');
      await progress.locator('.research-progress-basis > summary').click();
      assert.equal(await analysis.count(), 0, "Changing a factual scope preserves the closed analysis panel");
      assert.match(await progress.innerText(), /不适用|N\/A/);
      assert.match(await effects.locator('[data-metric="benefit_test"]').innerText(), /不适用 1/,
        'A wholly ineligible metric preserves its explicitly excluded sample count');
      assert.doesNotMatch(await effects.locator('[data-metric="discrimination"]').innerText(), /不适用/,
        'An all-unknown but eligible metric has no declared exclusion');
      assert.doesNotMatch(await progress.innerText(), /0%/);
      assert.equal(await nativeResults.count(), 0, 'A missing native capture cannot borrow the previous scope');
      assert.equal(await improvement.count(), 0, 'An absent comparison preserves the prior process-only surface');
      assert.match(await progress.innerText(), /原生测量.*未采集/);
      await history.getByRole("button", {name: "查看分析", exact: true}).click();
      await analysis.locator("header p").first().filter({hasText: /^Synthetic analysis C$/}).waitFor();
      await analysis.press('Escape');
      await progress.getByRole('combobox').selectOption('d'.repeat(64));
      await progress.locator(`.research-progress-scope[data-pack-id="${"d".repeat(64)}"]`).waitFor();
      await progress.locator('.research-progress-basis > summary').click();
      assert.match(await progress.innerText(), /末轮未完成|final Turn incomplete/);
      assert.match(await progress.innerText(), /400.*8.*tokens/s, "Persisted unfinished usage is visible without declaring total cost");
      await progress.locator('.research-progress-basis > summary').click();
      assert.match(await progress.innerText(), /所选范围尚未分析/);
      assert.equal(await progress.locator('.research-effect-metrics').count(), 0, 'An unanalysed cost pack never borrows another pack’s judgments');
      assert.equal(await analysis.count(), 0, 'Choosing an unanalysed source never requests analysis');
      assert.equal(await improvement.locator('[data-retained-improvement]').textContent(), '—', 'Missing comparison evidence is unknown, never zero');
      await basis.locator(':scope > summary').click();
      assert.match(await nativeResults.innerText(), /原生保留结果.*3.*提交 3/s, 'Native facts do not require an analyzer result');
      assert.match(await nativeResults.innerText(), /部分窗口，起点未知/);
      assert.match(await nativeResults.locator('[data-native-point="initial"]').innerText(), /— 未知/);
      const nativeCitation = nativeResults.getByRole('button', {name: /原生保留结果.*查看原始证据/});
      await nativeCitation.focus(); await nativeCitation.press('Enter');
      await citedOriginal.waitFor();
      await citedOriginal.press('Escape');
      await analysisClosed();
      // Analysis sources do not require an Explore log. The real read-only
      // backend must keep their measurements discoverable with zero events.
      const emptyFixture = resolve(temporary, "empty.json");
      await writeFile(emptyFixture, JSON.stringify({goal_id: goal, display_name: "Synthetic empty Explore history", events: []}));
      const linkedGoal = "linked-reading";
      const linkedEvents = JSON.parse(execFileSync(resolveTestPython(), ["-c", `
import json
from loopx.capabilities.explore.result_log import build_explore_node_event, build_explore_finding_event
print(json.dumps([
    build_explore_node_event(goal_id='linked-reading', node_id='question', title='Read complete evidence', node_kind='question', summary='Fixed synthetic input'),
    build_explore_finding_event(goal_id='linked-reading', node_id='question', finding_id='observation', title='A negative observation', status='refuted', summary='The change did not improve the original objective.', evidence_refs=['artifact:synthetic-input'], agent_id='researcher')]))
`], {cwd: repoRoot, encoding: "utf8"}));
      const completeTask = "Preserve every original requirement. " + "The input, negative evidence and unknown results stay distinct. ".repeat(15) + "FINAL_ACCEPTANCE: verify the complete stored source.";
      const taskFixture = resolve(temporary, "linked-tasks.json");
      await writeFile(taskFixture, JSON.stringify({goal_id: linkedGoal, display_name: "Synthetic linked-task reading", events: linkedEvents,
        todos: [{schema_version: "todo_item_v0", todo_id: "todo_linked_reading", index: 1, role: "agent", status: "open", done: false,
          archive_state: "active", source_section: "Agent Todo", claimed_by: "researcher", task_class: "advancement_task",
          text: completeTask, explore_result_node_refs: ["question"]}]}));
      emptyOwner = await startHistoryOwner([emptyFixture, taskFixture]);
      const previousOwner = owner; owner = emptyOwner; await previousOwner.close();
      await page.reload({waitUntil: "networkidle"});
      await history.getByRole("status").filter({hasText: "0 / 0"}).waitFor();
      await progress.locator(`.research-progress-scope[data-pack-id="${"d".repeat(64)}"]`).waitFor();

      comparisonFixture = true;
      await progress.locator('select').selectOption('a'.repeat(64));
      await progress.locator(`.research-progress-scope[data-pack-id="${"a".repeat(64)}"]`).waitFor();
      await page.reload({waitUntil: "networkidle"});
      const comparison = progress.locator('[data-metric="goal_alignment"]');
      await comparison.click();
      assert.match(await comparison.innerText(), /1 \/ 4.*未知 3/s);
      const rows = metricDetail.locator('.research-assessment');
      assert.equal(await rows.count(), 4);
      assert.deepEqual(await rows.locator('.research-assessment-name').allTextContents(),
        ['variant-alpha', 'variant-beta', 'variant-gamma', 'edge-aware'].map(id => `${id} 的实验是否检验原始目标，并被后续候选选择实际采用？负结果、控制差异与未知分别保留。`),
        'Without a localized interpretation, the original question remains readable instead of a protocol id');
      assert.deepEqual(await rows.locator('.research-assessment-verdict').allTextContents(), ['证据不足', '证据不足', '证据不足', '有依据']);
      assert.equal(await rows.locator('.research-assessment-body:visible').count(), 0);
      assert.equal(await metricDetail.locator('.research-effect-citations button:visible').count(), 0);
      const interpretation = metricDetail.getByRole('button', {name: '目标关联 · 判读口径', exact: true});
      await interpretation.focus();
      await metricDetail.getByRole('tooltip').waitFor({state: 'visible'});
      assert.match(await metricDetail.getByRole('tooltip').innerText(), /25%.*25%–100%/s);
      await page.keyboard.press('Escape');
      await page.setViewportSize({width: 1440, height: 1000});
      await progress.scrollIntoViewIfNeeded();
      await page.screenshot({path: resolve(outputDir, 'research-assessment-comparison.png'), animations: 'disabled'});
      const firstRow = rows.first().locator(':scope > summary');
      await firstRow.focus(); await firstRow.press('Enter');
      assert.match(await rows.first().innerText(), /variant-alpha 的实验是否检验原始目标/);
      assert.match(await rows.first().innerText(), /does not establish the intervention/);
      const comparisonCitation = rows.first().getByRole('button', {name: /source.*行 1–2/});
      await comparisonCitation.click();
      await citedOriginal.waitFor(); await citedOriginal.press('Escape');
      await page.waitForFunction(element => element === document.activeElement, await comparisonCitation.elementHandle());
      assert.equal(await comparisonCitation.evaluate(element => element === document.activeElement), true,
        'Returning from original evidence restores the exact expanded row');
      await firstRow.press('Enter');
      await page.setViewportSize({width: 390, height: 844});
      await comparison.scrollIntoViewIfNeeded();
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      const collapsedQuestion = rows.first().locator('.research-assessment-name');
      assert(await collapsedQuestion.evaluate(element => element.clientHeight <= 2 * parseFloat(getComputedStyle(element).lineHeight) + 1
        && element.scrollHeight > element.clientHeight), 'Long questions occupy at most two lines in the narrow comparison index');
      const narrowPositions = await metricPositions();
      await effects.locator('[data-metric="information_gain"]').click();
      assert.deepEqual(await metricPositions(), narrowPositions, 'Narrow metric navigation preserves the comparison band');
      assert.equal(await metricDetail.count(), 1, 'One metric evidence region replaces the previous selection');
      assert.equal(await comparison.getAttribute('aria-expanded'), 'false');
      await comparison.click();
      await metricDetail.evaluate(element => element.scrollIntoView({block: 'start'}));
      await page.screenshot({path: resolve(outputDir, 'research-assessment-comparison-mobile.png'), animations: 'disabled'});
      await rows.last().locator(':scope > summary').click();
      await rows.last().getByText(/Synthetic evidence connects/).waitFor();
      assert(await rows.last().locator('.research-assessment-name').evaluate(element => element.clientHeight >= element.scrollHeight),
        'Expanding the row restores the complete question without a second copy');
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await page.screenshot({path: resolve(outputDir, 'research-assessment-expanded-mobile.png'), animations: 'disabled'});
      await metricDetail.getByRole('button', {name: '收起指标详情', exact: true}).click();
      assert.equal(await comparison.evaluate(element => element === document.activeElement), true);
      localizedFixture = true;
      await page.reload({waitUntil: 'networkidle'});
      await progress.locator('[data-metric="goal_alignment"]').click();
      assert.equal(await metricDetail.locator('.research-assessment-name').first().innerText(), 'variant-alpha 是否检验原始目标？',
        'The metric reader uses the same localized question as deep analysis');
      await metricDetail.press('Escape');
      await history.getByRole('button', {name: '查看分析', exact: true}).click();
      const chineseAnalysis = history.getByRole('region', {name: '轨迹深度分析', exact: true});
      const episodeRows = chineseAnalysis.locator('.analysis-episode');
      await episodeRows.first().locator('.analysis-episode-name').filter({hasText: 'variant-alpha 是否检验原始目标？'}).waitFor();
      assert.equal(await episodeRows.count(), 4);
      assert.equal(await chineseAnalysis.locator('.analysis-episode[open]').count(), 0);
      assert.equal(await chineseAnalysis.locator('.analysis-episode-body:visible').count(), 0,
        'Opening deep analysis does not expand every question and rationale');
      assert.equal(await chineseAnalysis.locator('.analysis-citations button:visible').count(), 0);
      const episodeSummary = episodeRows.first().locator(':scope > summary');
      await episodeSummary.focus(); await episodeSummary.press('Enter');
      assert.equal(await episodeRows.first().locator('.analysis-episode-name').innerText(), 'variant-alpha 是否检验原始目标？');
      assert.equal(await episodeRows.first().getByText('variant-alpha 是否检验原始目标？', {exact: true}).count(), 1,
        'The localized question is the row title and is not repeated in its expanded body');
      assert.match(await episodeRows.first().innerText(), /来源只记录了实验结果/);
      const episodeCitation = episodeRows.first().getByRole('button', {name: /source.*行 1–2/}).first();
      await episodeCitation.click(); await citedOriginal.waitFor(); await citedOriginal.press('Escape');
      assert.equal(await episodeCitation.evaluate(element => element === document.activeElement), true);
      const otherJudgments = episodeRows.first().locator('.analysis-judgments details');
      assert.equal(await otherJudgments.count(), 4);
      await otherJudgments.first().locator('summary').click();
      await otherJudgments.first().getByText('来源只记录了实验结果，缺少干预与目标瓶颈的联系。', {exact: true}).waitFor();
      const originalQuestion = chineseAnalysis.locator('.analysis-episode').first().getByText('原始研究问题', {exact: true});
      await originalQuestion.click();
      assert.match(await chineseAnalysis.locator('.analysis-episode').first().innerText(), /variant-alpha 的实验是否检验原始目标/);
      await originalQuestion.click();
      await episodeSummary.click();
      await page.setViewportSize({width: 1440, height: 1000});
      await chineseAnalysis.evaluate(element => element.scrollIntoView({block: 'start'}));
      await page.screenshot({path: resolve(outputDir, 'research-analysis-reading-desktop.png'), animations: 'disabled'});
      await page.setViewportSize({width: 390, height: 844});
      await chineseAnalysis.evaluate(element => element.scrollIntoView({block: 'start'}));
      assert(await chineseAnalysis.evaluate(element => element.scrollWidth <= element.clientWidth + 1));
      assert(await episodeRows.first().evaluate(element => element.scrollWidth <= element.clientWidth + 1));
      await page.screenshot({path: resolve(outputDir, 'research-analysis-reading-mobile.png'), animations: 'disabled'});
      await episodeSummary.click();
      assert(await episodeRows.first().evaluate(element => element.scrollWidth <= element.clientWidth + 1), 'Expanded evidence wraps within a narrow row');
      await page.screenshot({path: resolve(outputDir, 'research-analysis-reading-expanded-mobile.png'), animations: 'disabled'});
      await page.evaluate(key => localStorage.setItem(key, 'en'), 'loopx-pw-locale');
      await page.reload({waitUntil: 'networkidle'});
      const englishAnalysis = page.getByRole('region', {name: 'Trajectory analysis', exact: true});
      await englishAnalysis.locator('.analysis-episode-name').first().filter({hasText: 'Did variant-alpha test the original objective?'}).waitFor();
      assert.equal(await englishAnalysis.locator('.analysis-episode[open]').count(), 0, 'Language changes reset optional episode details');
      await englishAnalysis.locator('.analysis-episode > summary').first().click();
      assert.equal(await englishAnalysis.locator('.analysis-episode-name').first().innerText(), 'Did variant-alpha test the original objective?');
      assert.doesNotMatch(await englishAnalysis.innerText(), /来源只记录了实验结果/);
      await page.evaluate(key => localStorage.setItem(key, 'zh-CN'), 'loopx-pw-locale');
      await page.reload({waitUntil: 'networkidle'});
      await chineseAnalysis.locator('.analysis-episode > summary').first().filter({hasText: 'variant-alpha'}).waitFor();
      assert.equal(await chineseAnalysis.locator('.analysis-episode[open]').count(), 0);
      continuationFixture = true;
      await page.reload({waitUntil: 'networkidle'});
      // The inline version summary is a semantic section, not another page.
      const version = chineseAnalysis.locator('.analysis-continuation');
      await version.getByText('接续上一版', {exact: true}).waitFor();
      await version.getByText(/已复核全部 4 片段/).waitFor();
      assert.equal(await version.locator('details').getAttribute('open'), null);
      await version.locator('summary').click();
      assert.match(await version.innerText(), /original-capture → later-evidence/);
      assert.match(await version.innerText(), /variant-alpha.*已做收益检验.*证据不足/);
      await version.locator('summary').click();
      await page.setViewportSize({width: 1440, height: 1000});
      await chineseAnalysis.evaluate(element => element.scrollIntoView({block: 'start'}));
      await page.screenshot({path: resolve(outputDir, 'research-analysis-continuation-desktop.png'), animations: 'disabled'});
      const previousVersion = version.getByRole('button', {name: '查看上一版', exact: true});
      await previousVersion.focus(); await previousVersion.press('Enter');
      await chineseAnalysis.getByText('Synthetic original analysis', {exact: true}).waitFor();
      assert.equal(await chineseAnalysis.locator('.analysis-continuation').count(), 0);
      await page.goBack({waitUntil: 'networkidle'});
      await version.getByText('接续上一版', {exact: true}).waitFor();
      await page.reload({waitUntil: 'networkidle'});
      await version.getByText('接续上一版', {exact: true}).waitFor();
      await page.setViewportSize({width: 390, height: 844});
      await chineseAnalysis.evaluate(element => element.scrollIntoView({block: 'start'}));
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await page.screenshot({path: resolve(outputDir, 'research-analysis-continuation-mobile.png'), animations: 'disabled'});
      await page.evaluate(key => localStorage.setItem(key, 'en'), 'loopx-pw-locale');
      await page.reload({waitUntil: 'networkidle'});
      await page.getByRole('button', {name: 'Previous version', exact: true}).waitFor();
      equivalentContinuation = true;
      await page.reload({waitUntil: 'networkidle'});
      await page.getByRole('button', {name: 'Reuse analysis', exact: true}).first().waitFor();
      await page.getByRole('button', {name: 'About deep analysis', exact: true}).click();
      await page.getByText('This action reuses existing judgments without a model call; original usage stays with the previous version.', {exact: true}).waitFor();
      await page.keyboard.press('Escape');
      // A source can expose complete saved findings without a complete Task
      // association endpoint. Unknown links must never be presented as zero.
      await page.route("**/api/chat/explore-results?*", route => {
        const query = new URL(route.request().url()).searchParams;
        if (query.get("goal_id") === linkedGoal) return route.fallback();
        if (query.get("view") === "history") return route.fallback();
        assert.equal(query.get("view"), "findings");
        return route.fulfill({json: {ok: true, goal_id: query.get("goal_id"), total: 2, next_cursor: null,
          items: [0, 1].map(index => ({finding_id: `saved-${index}`, node_id: "question", finding: `Saved observation ${index + 1}`,
            question: "Does this observation support the original objective?", scope: "Fixed synthetic input",
            summary: "The measured change did not improve the original objective. Keep the negative result and investigate the next unresolved question. " +
              "The observation applies to the fixed input only; comparison with a different input remains unverified.",
            status: "refuted", agent_id: "researcher", evidence_refs: ["artifact:fixed-input"],
            last_updated_at: "2026-01-01T12:00:00Z", linked_todos: [], linked_todos_available: false}))}});
      });
      await page.getByText("Read saved findings", {exact: true}).click();
      const sourceReader = page.getByRole("region", {name: "Explore evidence", exact: true});
      const sourceBody = sourceReader.locator(".goal-team-result-reader");
      await sourceBody.waitFor();
      await sourceBody.getByText("Linked tasks · —", {exact: true}).click();
      assert.match(await sourceBody.innerText(), /Complete task associations are not available/);
      assert.doesNotMatch(await sourceBody.innerText(), /No links found/);
      await page.setViewportSize({width: 1440, height: 1000});
      await sourceReader.evaluate(element => element.scrollIntoView({block: "start"}));
      await page.screenshot({path: resolve(outputDir, "source-saved-findings-desktop.png"), animations: "disabled"});
      await page.setViewportSize({width: 390, height: 844});
      await sourceBody.scrollIntoViewIfNeeded();
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await page.screenshot({path: resolve(outputDir, "source-saved-findings-mobile.png"), animations: "disabled"});
      // The association list is a bounded preview; expansion must read the
      // canonical Task, including requirements beyond the preview's limit.
      await page.setViewportSize({width: 1440, height: 1000});
      await page.locator('.personal-goal-link', {hasText: "Synthetic linked-task reading"}).click();
      await page.getByText("Read saved findings", {exact: true}).click();
      await sourceBody.waitFor();
      await sourceBody.getByText("Linked tasks · 1", {exact: true}).click();
      const linkedTask = sourceBody.locator('.explore-linked-tasks details');
      assert.equal(taskReads.length, 0, "Collapsed associations perform no Task reads");
      assert.doesNotMatch(await linkedTask.innerText(), /FINAL_ACCEPTANCE/);
      unavailableTask = true;
      await linkedTask.locator('summary').focus(); await linkedTask.locator('summary').press('Enter');
      await linkedTask.getByRole('alert').waitFor();
      assert.equal(await linkedTask.locator('.explore-finding-raw').count(), 0, "An unavailable source cannot label a preview as the complete request");
      unavailableTask = false;
      await linkedTask.getByRole('button', {name: "Retry", exact: true}).click();
      await linkedTask.locator('.explore-finding-raw').waitFor();
      assert.equal(await linkedTask.locator('.explore-finding-raw').textContent(), completeTask);
      assert(taskReads.every(value => value === linkedGoal), "Each detail read stays bound to the selected Goal");
      await page.setViewportSize({width: 390, height: 844});
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      assert.equal(analysisWrites, 0, "Cached, reload, switch and keyboard reading never request execution");
      assert.equal(api.turnRequests.length, 0, "Reading the graph cannot start an Agent");
      return {note: "Three unchanged real checkpoint histories through production HTTP + packaged UI; per-Goal complete pagination, genuine negative evidence, no invented edges, reload/back/forward, mobile and failed refresh. Separate synthetic coincident-target regression; no benchmark qualification or model dispatch.",
        coverageEntries: await context.close()};
    } finally {
      if (context && !context.page.isClosed()) await context.close();
      await owner.close();
      if(collisionOwner && collisionOwner!==owner) await collisionOwner.close();
      if(emptyOwner && emptyOwner!==owner) await emptyOwner.close();
      if(temporary) await rm(temporary,{recursive:true,force:true});
    }
  },
};
