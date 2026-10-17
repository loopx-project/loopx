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
        complete: true, metric: {name: "error", unit: "points", higher_is_better: false}, sample_count: 3, valid_count: 2,
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
  if (selected === "d") {snapshot.pack.result = null; snapshot.pack.job = null;}
  return snapshot;
}

async function startHistoryOwner(fixture) {
  const child = spawn(resolveTestPython(), ["-u", "examples/personal-workspace-browser/research-history-backend.py", ...(fixture ? ["--fixture", fixture] : [])],
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
    let unavailable = false;
    let analysisFixture = false, holdOldAnalysis = false, slowStarted, releaseOld, oldDelivered;
    let unavailableAnalysis = false;
    let analysisWrites = 0;
    try {
      context = await openWorkspacePage(browser, url, {collectCoverage, beforeGoto: async (_, page) => {
        await page.route("**/api/chat/research-analysis**", async route => {
          if (route.request().method() !== "GET") {analysisWrites++; return route.fulfill({status: 403, json: {error: "Read-only presentation fixture"}});}
          if (!analysisFixture) return route.fulfill({json: {ok: true, catalog: {availability: "unavailable", reason_code: "no_available_pack", default_pack_id: null, packs: []}, pack: null}});
          const query = new URL(route.request().url()).searchParams;
          if (new URL(route.request().url()).pathname.endsWith('/citation')) return route.fulfill({json: {
            source_id: 'source', first_line: 1, last_line: 2, artifact: 'facts.txt', applicability: 'current',
            text: 'Synthetic original evidence.\nDiagnostic measurements do not test native utility.'
          }});

          const selected = ["b", "c", "d"].find(id => query.get("pack_id") === id.repeat(64)) ?? "a";
          if (holdOldAnalysis && selected === "a") {slowStarted(); await new Promise(resolve => {releaseOld = resolve;});}
          await route.fulfill(unavailableAnalysis && selected === "a"
            ? {status: 503, json: {error: "Synthetic analysis read unavailable"}}
            : {json: analysisReadback(selected)});
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
          if (unavailable) return route.fulfill({status: 503, json: {error: "Isolated history transport unavailable"}});
          const response = await fetchWorkspaceBackend(route, owner.base);
          assert.equal(response.status(), 200);
          const value = await response.json();
          if (new URL(route.request().url()).searchParams.get("view") === "history") pages.push(value);
          await route.fulfill({response, json: value});
        });
      }});
      const {page, api} = context;
      const selectedNodes = () => {
        const raw = new URL(page.url()).searchParams.get('research_node');
        return raw?.startsWith('[') ? JSON.parse(raw) : raw ? [raw] : [];
      };
      await page.locator(".personal-goal-link", {hasText: capture.display_name}).click();
      const history = page.getByRole("region", {name: "研究历程", exact: true});
      await history.getByRole("status").filter({hasText: "76 / 76"}).waitFor();
      assert.equal(pages.length, 2, "The complete Portfolio checkpoint requires every bounded HTTP page");
      assert.deepEqual(pages.flatMap(value => value.items).map(row => row.event_id), capture.events.map(row => row.event_id));
      await history.locator(".research-phases").waitFor();
      assert.equal(await history.locator('.research-route').count(),0,'The solver recorded no routes or parent links');
      assert.equal(await history.locator('.research-decision-ticks span').count()>0,true,'Actual recorded decisions remain visible');
      await page.screenshot({path:resolve(outputDir,'research-history-portfolio-viewport.png'),animations:'disabled'});
      await history.evaluate(element=>element.scrollIntoView({block:'start'}));
      await page.screenshot({path:resolve(outputDir,'research-history-portfolio-timeline.png'),animations:'disabled'});
      const day = history.locator(".research-unlinked-days button").first();
      await day.click();
      const realDetail=history.locator('.research-history-detail');
      await realDetail.getByRole('heading',{name:'Do the tested cost-gated one-session executions improve the frozen forecast strategy?',exact:true}).waitFor();
      assert.match(await realDetail.textContent(),/daily trials1\.53-2\.99\/199-719%/);
      assert.match(await realDetail.textContent(),/evidence:opportunity-cost-gates/);
      const realDay=new URL(page.url()).searchParams.get('research_group');
      await page.reload({waitUntil:'networkidle'});
      await realDetail.waitFor();
      assert.equal(new URL(page.url()).searchParams.get('research_group'),realDay);
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
        pages.length=0;
        await page.locator('.personal-goal-link',{hasText:other.display_name}).click();
        await history.getByRole('status').filter({hasText:`${other.events.length} / ${other.events.length}`}).waitFor();
        assert.deepEqual(pages.flatMap(value=>value.items).map(row=>row.event_id),other.events.map(row=>row.event_id),`${label}: all original pages, without cross-Goal records`);
        assert.equal(await history.locator('.research-route').count(),0);
        await history.evaluate(element=>element.scrollIntoView({block:'start'}));
        await page.screenshot({path:resolve(outputDir,`research-history-${label}-timeline.png`),animations:'disabled'});
        await history.locator('.research-unlinked-days button').first().click();
        await realDetail.waitFor();
        assert.match(await realDetail.textContent(),new RegExp(other.events[0].title.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')));
        await page.screenshot({path:resolve(outputDir,`research-history-${label}-detail.png`),animations:'disabled'});
        checkpoints.push({goal_id:other.goal_id,event_count:other.events.length,history_pages:Math.ceil(other.events.length/40)});
      }
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
    summary='Pointer regression only',parent_id='hit-0' if index else None,
    recorded_at='2026-01-01T00:00:00Z' if index<9 else '2026-01-01T18:00:00Z') for index in range(10)]
events.append(build_explore_finding_event(goal_id=goal,finding_id='negative',node_id='hit-6',
    title='Synthetic negative',summary='Negative pointer regression',status='refuted',recorded_at='2026-01-01T00:00:00Z'))
Path(sys.argv[1]).write_text(json.dumps({'goal_id':goal,'display_name':'Synthetic pointer regression','events':events}))
`,fixture],{cwd:repoRoot});
      collisionOwner=await startHistoryOwner(fixture);
      const realOwner=owner; owner=collisionOwner;
      await realOwner.close();
      await page.setViewportSize({width:1280,height:900});
      await page.goto(new URL(`?goalId=${goal}&view=overview&statusUrl=%2Fstatus.json`,url).href,{waitUntil:'networkidle'});
      await history.getByRole('status').filter({hasText:'11 / 11'}).waitFor();
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
        const target = history.locator('.research-route-track [data-research-nodes~="hit-6"]');
        await target.scrollIntoViewIfNeeded();
        await page.screenshot({path: resolve(outputDir, `research-history-targets-${viewport.width}.png`), animations:'disabled'});
        // Actual pointer targeting: the old overlapping 44px buttons opened
        // a neighbour instead of the requested node. A group must expose every
        // original member immediately, without a second selection or Graph.
        const ids = (await target.getAttribute('data-research-nodes')).split(' ');
        const box = await target.boundingBox();
        await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
        const detail = history.locator('.research-history-detail');
        await detail.waitFor();
        assert.deepEqual(selectedNodes(),ids);
        assert(ids.includes('hit-6'));
        await detail.getByRole('heading',{name:'Synthetic hit target 6',exact:true}).waitFor();
        for (const id of ids) assert.equal(await detail.locator(`[data-research-node="${id}"]`).count(),1,'Every grouped member is immediately readable');
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
      assert.match(await history.textContent(), /11 \/ 11/);
      await page.screenshot({path: resolve(outputDir, "research-history-recorded-desktop.png"), animations: "disabled"});
      await history.screenshot({path: resolve(outputDir, "research-history-recorded-detail.png"), animations: "disabled"});
      unavailable = true;
      await history.getByRole("button", {name: "刷新记录", exact: true}).click();
      await history.getByRole("alert").waitFor();
      assert.match(await history.textContent(), /11 \/ 11/, "A failed refresh cannot erase a verified complete capture");
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
      await progress.locator(".research-progress-scope").filter({hasText: "Synthetic analysis A"}).waitFor();
      const effects = progress.locator('.research-effect-metrics');
      await effects.waitFor();
      assert.equal(await effects.locator('details').count(), 6, 'All six analyzer metrics are primary without opening analysis or cost details');
      assert.equal(await progress.locator('.research-usage').evaluate(e => e.open), false, 'Cost is secondary by default');
      const benefitMetric = effects.locator('[data-metric="benefit_test"]');
      assert.match(await benefitMetric.innerText(), /0 \/ 1/);
      assert.match(await benefitMetric.innerText(), /尚未检验收益/);
      await benefitMetric.locator('summary').click();
      assert.match(await benefitMetric.innerText(), /original-objective utility was not tested/);
      assert.match(await effects.locator('[data-metric="evidence_adoption"]').innerText(), /未知 1/);

      assert.equal(await history.getByRole("region", {name: "轨迹深度分析", exact: true}).count(), 0, "Expanding existing effects never opens or executes model analysis");
      await benefitMetric.getByRole('button', {name: /source.*行 1–2/}).click();
      const citedOriginal = history.getByRole('region', {name: '引用原文', exact: true});
      await citedOriginal.waitFor();
      assert.match(await citedOriginal.innerText(), /Diagnostic measurements do not test native utility/);
      await citedOriginal.press('Escape');
      await history.getByRole('button', {name: '关闭分析', exact: true}).click();
      await progress.locator('.research-usage > summary').click();
      assert.equal(await progress.getByText("100", {exact: true}).count(), 1, "Uncached input is shown without charging cache twice");
      assert.match(await progress.innerText(), /1,?100.*1,?000/s);
      assert.match(await progress.innerText(), /30.*4.*tokens/s, "Analysis attempt cost is distinct from solver usage");
      assert.match(await progress.innerText(), /更早失败.*恢复.*评测/s);
      await progress.locator('.research-usage > summary').click();
      const basis = progress.locator('.research-progress-basis');
      await basis.locator('summary').click();
      assert.match(await basis.innerText(), /首次提交.*7.*candidate-first/s);
      assert.match(await basis.innerText(), /原生保留结果.*3.*candidate-retained/s);
      await basis.getByRole('button', {name: '查看原始证据', exact: true}).first().scrollIntoViewIfNeeded();
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Native details remain readable on a narrow screen');
      await page.screenshot({path: resolve(outputDir, "research-native-outcomes-mobile.png"), animations: "disabled"});
      await page.setViewportSize({width:1280,height:900});
      await page.screenshot({path: resolve(outputDir, "research-native-outcomes-desktop.png"), animations: "disabled"});
      await basis.getByRole('button', {name: '查看原始证据', exact: true}).first().click();
      await citedOriginal.waitFor();
      assert.match(await citedOriginal.innerText(), /Synthetic original evidence/);
      await citedOriginal.press('Escape');
      await history.getByRole('button', {name: '关闭分析', exact: true}).click();
      await basis.locator('summary').click();
      await benefitMetric.locator('summary').click();
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
      assert.match(await analysis.innerText(), /utility was not tested/);
      const started = new Promise(resolve => {slowStarted = resolve;});
      const delivered = new Promise(resolve => {oldDelivered = resolve;});
      holdOldAnalysis = true;
      await analysis.getByRole("button", {name: "刷新分析结果", exact: true}).click();
      await started;
      await progress.getByRole("combobox").focus();
      await progress.getByRole("combobox").selectOption("b".repeat(64));
      await analysis.locator("header p").first().filter({hasText: /^Synthetic analysis B$/}).waitFor();
      assert.equal(await progress.getByRole("combobox").evaluate(select => select === document.activeElement), true, "The source selector keeps keyboard focus across loading");
      releaseOld(); await delivered; holdOldAnalysis = false;
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      assert.equal(await analysis.locator("header p").first().textContent(), "Synthetic analysis B", "A delayed same-Goal read cannot undo a pack selection");
      assert.match(await progress.innerText(), /历史捕获|historical/i);
      await page.reload({waitUntil: "networkidle"});
      await analysis.locator("header p").first().filter({hasText: /^Synthetic analysis B$/}).waitFor();
      await page.setViewportSize({width: 390, height: 844});
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await page.screenshot({path: resolve(outputDir, "research-analysis-mobile.png"), animations: "disabled"});
      // Browser history changes source identity too. While A is delayed or
      // unavailable, B's interpretation/cost must not be shown under A's URL.
      await page.evaluate(({goal, a, b}) => {
        const url = new URL(location.href);
        url.searchParams.set("research_analysis_goal", goal);
        url.searchParams.set("research_pack", a);
        history.replaceState(history.state, "", url);
        url.searchParams.set("research_pack", b);
        history.pushState(history.state, "", url);
      }, {goal, a: "a".repeat(64), b: "b".repeat(64)});
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
      await progress.locator(".research-progress-scope").filter({hasText: "Synthetic analysis C"}).waitFor();
      assert.match(await progress.innerText(), /尚无完整用量捕获|No complete usage/);
      assert.equal(await analysis.count(), 0, "Changing a factual scope preserves the closed analysis panel");
      assert.match(await progress.innerText(), /不适用|N\/A/);
      assert.doesNotMatch(await progress.innerText(), /0%/);
      await history.getByRole("button", {name: "查看分析", exact: true}).click();
      await analysis.locator("header p").first().filter({hasText: /^Synthetic analysis C$/}).waitFor();
      await analysis.press('Escape');
      await progress.getByRole('combobox').selectOption('d'.repeat(64));
      await progress.locator('.research-progress-scope').filter({hasText:'Synthetic analysis D'}).waitFor();
      assert.match(await progress.innerText(), /此范围尚无分析器判读/);
      assert.equal(await progress.locator('.research-effect-metrics').count(), 0, 'An unanalysed cost pack never borrows another pack’s judgments');
      assert.equal(await analysis.count(), 0, 'Choosing an unanalysed source never requests analysis');
      await basis.locator('summary').click();
      assert.match(await basis.innerText(), /原生保留结果.*candidate-retained/s, 'Native facts do not require an analyzer result');
      const nativeCitation = basis.getByRole('button', {name: '查看原始证据', exact: true}).first();
      await nativeCitation.focus(); await nativeCitation.press('Enter');
      await citedOriginal.waitFor();
      await citedOriginal.press('Escape');
      await history.getByRole('button', {name: '关闭分析', exact: true}).click();
      await basis.locator('summary').click();
      // Analysis sources do not require an Explore log. The real read-only
      // backend must keep their measurements discoverable with zero events.
      const emptyFixture = resolve(temporary, "empty.json");
      await writeFile(emptyFixture, JSON.stringify({goal_id: goal, display_name: "Synthetic empty Explore history", events: []}));
      emptyOwner = await startHistoryOwner(emptyFixture);
      const previousOwner = owner; owner = emptyOwner; await previousOwner.close();
      await page.reload({waitUntil: "networkidle"});
      await history.getByRole("status").filter({hasText: "0 / 0"}).waitFor();
      await progress.locator(".research-progress-scope").filter({hasText: "Synthetic analysis D"}).waitFor();
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
