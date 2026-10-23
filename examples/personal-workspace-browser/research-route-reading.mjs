import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import {mkdtemp, readFile, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {resolve} from "node:path";
import {resolveTestPython} from "../../scripts/test-python.mjs";
import {outputDir, repoRoot} from "./fixture.mjs";
import {analysisReadback, startHistoryOwner} from "./research-history.mjs";

// Complete endpoint reading and a stable action row are independent of label
// length, analysis lifecycle and model metadata. The history owner is real;
// only the analysis readbacks are synthetic, with every write refused.
export function writeRouteReadingFixture(path) {
  execFileSync(resolveTestPython(), ["-c", `
import json,sys
from pathlib import Path
from loopx.capabilities.explore.result_log import build_explore_node_event,build_explore_finding_event
goal='synthetic-route-reading'
events=[]
for route in range(3):
    first=f'route-{route}-start'
    for endpoint in ('start','end'):
        title=(('起始研究：如何在相同预算下比较质量、延迟与失败？ ' if endpoint=='start' else '末端研究：负结果、采用与未知成本还需要哪些证据？ ')*4
            if route!=1 else ('Does the fixed candidate improve the original objective while retaining failures and unknown costs? '*4))
        node=f'route-{route}-{endpoint}'
        events.append(build_explore_node_event(goal_id=goal,node_id=node,title=title,
            parent_id=first if endpoint=='end' else None,recorded_at=f'2026-01-0{route+1}T{0 if endpoint=="start" else 12:02}:00:00Z'))
        events.append(build_explore_finding_event(goal_id=goal,node_id=node,finding_id=f'finding-{node}',title='固定合成记录',
            summary='完整观测：局部质量下降，延迟改善；完整成本和独立验证仍未知。',status='refuted' if route==2 else 'tentative',
            recorded_at=f'2026-01-0{route+1}T{1 if endpoint=="start" else 13:02}:00:00Z'))
Path(sys.argv[1]).write_text(json.dumps({'goal_id':goal,'display_name':'合成研究 · 路线阅读','events':events},ensure_ascii=False))
`, path], {cwd: repoRoot});
}

async function assertPrimaryRow(history, minimumHeight) {
  const controls = history.locator(".research-history-controls");
  const boxes = await controls.locator(".research-lens, .research-analysis-entry-actions > button, .research-analysis-entry-actions .context-help-trigger, :scope > button").evaluateAll(elements =>
    elements.map(element => {const r = element.getBoundingClientRect(); return {name: element.textContent || element.getAttribute("aria-label"), top: r.top, bottom: r.bottom, height: r.height};}));
  assert.equal(boxes.length, 4);
  assert(Math.max(...boxes.map(b => b.top)) - Math.min(...boxes.map(b => b.top)) <= 1, JSON.stringify(boxes));
  assert(Math.max(...boxes.map(b => b.bottom)) - Math.min(...boxes.map(b => b.bottom)) <= 1, JSON.stringify(boxes));
  assert(boxes.every(b => b.height >= minimumHeight - .1), `All primary controls retain their usable target height: ${JSON.stringify(boxes)}`);
  const metadata = controls.locator(".research-analysis-entry > small");
  if (await metadata.count()) assert((await metadata.boundingBox()).y >= boxes[0].bottom, "Model and usage have their own row");
  assert(await controls.evaluate(element => element.scrollWidth <= element.clientWidth + 1), "The action row fits without horizontal scrolling");
}

export const researchRouteReadingScenario = {
  id: "research-route-reading",
  async run({browser, collectCoverage}) {
    const temporary = await mkdtemp(resolve(tmpdir(), "loopx-route-reading-"));
    let owner, page;
    try {
      const fixture = resolve(temporary, "capture.json");
      writeRouteReadingFixture(fixture);
      const capture = JSON.parse(await readFile(fixture));
      owner = await startHistoryOwner(fixture);
      page = await browser.newPage({viewport: {width: 1440, height: 900}, hasTouch: true});
      if (collectCoverage) await page.coverage.startJSCoverage({resetOnNavigation: false});
      const url = new URL("/chat/?goalId=synthetic-route-reading&view=research&statusUrl=%2Fstatus.json", owner.base).href;
      let state = "deep", analysisWrites = 0, releaseLoading;
      await page.route("**/api/chat/research-analysis?*", async route => {
        if (route.request().method() !== "GET") {analysisWrites++; return route.abort();}
        if (state === "loading") await new Promise(resolve => {releaseLoading = resolve;});
        const language = new URL(route.request().url()).searchParams.get("output_language");
        const snapshot = analysisReadback();
        snapshot.pack.config = {...snapshot.pack.config, output_language: language};
        if (state === "continue") snapshot.pack.config.model = "synthetic-model-with-a-long-display-identifier";
        if (state !== "view") snapshot.pack.result = null;
        else snapshot.pack.result.output_language = language;
        if (state !== "running") snapshot.pack.job = null;
        else snapshot.pack.job = {...snapshot.pack.job, status: "running"};
        if (["reuse", "continue"].includes(state)) snapshot.pack.continuation = {can_reuse: state === "reuse"};
        if (state === "unavailable") {snapshot.pack.availability = "unavailable"; snapshot.pack.reason_code = "source_unavailable";}
        return route.fulfill({json: snapshot});
      });
      const history = page.locator(".research-history");
      const caption = history.locator(".research-route-caption");
      const reader = history.locator(".research-history-detail");
      for (const language of ["zh-CN", "en"]) for (const width of [1440, 768, 390]) {
        await page.setViewportSize({width, height: 900});
        await page.goto(url, {waitUntil: "domcontentloaded"});
        await page.evaluate(locale => localStorage.setItem("loopx-pw-locale", locale), language);
        for (state of ["loading", "deep", "view", "reuse", "continue", "running", "unavailable"]) {
          await page.reload({waitUntil: "domcontentloaded"});
          await caption.first().waitFor();
          assert.equal(await page.locator("html").getAttribute("lang"), language);
          await history.locator(`.research-analysis-entry-actions > button${state === "loading" ? ":disabled" : ":not(:disabled)"}`).waitFor();
          await assertPrimaryRow(history, width <= 640 ? 44 : 36);
          if (state === "loading") {releaseLoading(); await history.locator(".research-analysis-entry-actions > button:not(:disabled)").waitFor();}
        }
        state = "deep";
        await page.reload({waitUntil: "domcontentloaded"});
        await caption.first().waitFor();
        assert.equal(await caption.count(), 3, "Every recorded route remains in the whole history");
        const titleLayout = await caption.first().locator("strong").first().evaluate(element => ({height: element.clientHeight, lineHeight: parseFloat(getComputedStyle(element).lineHeight), fontSize: parseFloat(getComputedStyle(element).fontSize)}));
        assert(titleLayout.height >= titleLayout.lineHeight * 1.9 && titleLayout.fontSize >= 16, "Long captions wrap into readable list entries");
        if (language === "zh-CN" && width !== 768) {
          await page.locator(".personal-channel-scroll").evaluate(element => element.scrollTop = 0);
          await page.screenshot({path: resolve(outputDir, `research-route-reading-first-${width}.png`), animations: "disabled"});
        }
        for (const endpoint of [0, 1]) {
          const control = caption.first().locator("button").nth(endpoint);
          await control.focus();
          const returnKey = await control.getAttribute("data-research-return");
          const fullTitle = capture.events.find(event => event.result_id === `route-0-${endpoint ? "end" : "start"}` && event.event_kind === "node").title;
          await page.keyboard.press("Enter");
          await reader.waitFor();
          assert.equal(await reader.locator("h3").first().innerText(), fullTitle, "One activation reveals the complete original title");
          assert(await reader.locator("h3").evaluate(element => element.scrollHeight <= element.clientHeight + 1), "Selected text is not clipped");
          if (endpoint && language === "zh-CN" && width !== 768) await page.screenshot({path: resolve(outputDir, `research-route-reading-detail-${width}.png`), animations: "disabled"});
          await page.keyboard.press("Escape");
          assert.equal(await reader.count(), 0);
          await page.waitForFunction(key => document.activeElement?.getAttribute("data-research-return") === key, returnKey);
        }
        if (language === "zh-CN" && width !== 768) {
          await caption.first().evaluate(element => element.scrollIntoView({block: "center"}));
          await page.screenshot({path: resolve(outputDir, `research-route-reading-routes-${width}.png`), animations: "disabled"});
        }
      }
      await caption.first().locator("button").last().tap();
      await reader.waitFor();
      await page.reload({waitUntil: "domcontentloaded"});
      await reader.waitFor();
      assert.match(new URL(page.url()).searchParams.get("research_node"), /route-0-end/);
      await reader.getByRole("button", {name: "Whole graph", exact: true}).click();
      await page.goBack();
      await reader.waitFor();
      assert.match(await reader.locator("h3").first().innerText(), /末端研究/);
      assert.equal(analysisWrites, 0, "Reading, language changes and every action state never dispatch a model");
      const readback = await (await page.request.get(`${owner.base}/fixture/readback`)).json();
      assert(readback.source_unchanged && readback.mutation_attempts === 0, "No source or execution state changes");
      return {coverageEntries: collectCoverage ? await page.coverage.stopJSCoverage() : [], note: "complete route endpoint reading, exact keyboard return, reload/Back, bilingual action states on desktop/narrow/390 with zero model effects"};
    } catch (error) {
      if (page && !page.isClosed()) await page.screenshot({path: resolve(outputDir, "research-route-reading-failed.png")});
      throw error;
    } finally {await page?.close(); await owner?.close(); await rm(temporary, {recursive: true, force: true});}
  },
};
