import assert from "node:assert/strict";
import {execFileSync, spawn} from "node:child_process";
import {mkdtemp, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {resolve} from "node:path";
import {createInterface} from "node:readline";
import {resolveTestPython} from "../../scripts/test-python.mjs";
import {outputDir, repoRoot} from "./fixture.mjs";
import {fetchWorkspaceBackend, openWorkspacePage} from "./scenario-context.mjs";

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
    let collisionOwner, temporary;
    let context;
    const pages = [];
    let unavailable = false;
    try {
      context = await openWorkspacePage(browser, url, {collectCoverage, beforeGoto: async (_, page) => {
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
      assert.equal(api.turnRequests.length, 0, "Reading the graph cannot start an Agent");
      return {note: "Three unchanged real checkpoint histories through production HTTP + packaged UI; per-Goal complete pagination, genuine negative evidence, no invented edges, reload/back/forward, mobile and failed refresh. Separate synthetic coincident-target regression; no benchmark qualification or model dispatch.",
        coverageEntries: await context.close()};
    } finally {
      if (context && !context.page.isClosed()) await context.close();
      await owner.close();
      if(collisionOwner && collisionOwner!==owner) await collisionOwner.close();
      if(temporary) await rm(temporary,{recursive:true,force:true});
    }
  },
};
