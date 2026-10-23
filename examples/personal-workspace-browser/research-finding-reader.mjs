import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import {mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {resolve} from "node:path";
import {resolveTestPython} from "../../scripts/test-python.mjs";
import {outputDir, repoRoot} from "./fixture.mjs";
import {analysisReadback, startHistoryOwner} from "./research-history.mjs";

// Public synthetic documents through the production history HTTP/SQLite owner.
// No model, solver, scientific score or live Goal is involved.
export function writeFindingReaderFixture(path) {
  execFileSync(resolveTestPython(), ["-c", `
import json, sys
from pathlib import Path
from loopx.capabilities.explore.result_log import build_explore_node_event, build_explore_finding_event
goal='synthetic-finding-reading'
events=[build_explore_node_event(goal_id=goal,node_id='question',title='合成研究：质量、延迟和未知成本应如何一起判断？',recorded_at='2026-01-01T00:00:00Z')]
for index in range(20):
    status='refuted' if index%7==0 else 'tentative' if index%4==0 else 'confirmed'
    observation=f'观测 {index+1}：质量下降而延迟改善，仍不能据此断言完整收益。'
    observation+=' 保留失败候选与完整成本未知；下一步需要在相同条件下核对。'*3
    summary=f'Input revision: input:{"a"*64} Applicability: 固定合成样本；不是生产评测。 Observation: {observation} Interpretation: Outcome: continue changed: - 继续核对完整成本 stopped: - 不把局部改善当整体收益 retained: - 保留负结果与原始版本'
    events.append(build_explore_finding_event(goal_id=goal,node_id='question',finding_id=f'finding-{index}',
        title=f'比较 {index+1}：质量、延迟与采用版本的核对 · quality, latency and retained candidate',
        status=status,summary=summary,evidence_refs=['result:synthetic-'+str(index)],recorded_at=f'2026-01-01T{index+1:02}:00:00Z'))
events.append(build_explore_node_event(goal_id=goal,node_id='blocked',title='来源暂不可用的研究问题',status='blocked',blocked_reason='无法读取评测来源，当前结果未知。',recorded_at='2026-01-02T00:00:00Z'))
events.append(build_explore_node_event(goal_id=goal,node_id='single',title='仅有一条结论的问题',recorded_at='2026-01-02T01:00:00Z'))
events.append(build_explore_finding_event(goal_id=goal,node_id='single',finding_id='single-finding',title='原始观测',summary='一条完整的原始结论。',status='tentative',recorded_at='2026-01-02T01:00:00Z'))
Path(sys.argv[1]).write_text(json.dumps({'goal_id':goal,'display_name':'合成研究 · 结论阅读','events':events},ensure_ascii=False))
`, path], {cwd: repoRoot});
}

export const researchFindingReaderScenario = {
  id: "research-finding-reader",
  async run({browser, collectCoverage}) {
    const temporary = await mkdtemp(resolve(tmpdir(), "loopx-finding-reading-"));
    let owner, page;
    try {
      const fixture = resolve(temporary, "capture.json");
      writeFindingReaderFixture(fixture);
      owner = await startHistoryOwner(fixture);
      page = await browser.newPage({viewport: {width: 1440, height: 900}, hasTouch: true});
      if (collectCoverage) await page.coverage.startJSCoverage({resetOnNavigation: false});
      const url = new URL("/chat/?goalId=synthetic-finding-reading&view=research&research_goal=synthetic-finding-reading&research_node=question&statusUrl=%2Fstatus.json", owner.base).href;
      await page.goto(url, {waitUntil: "domcontentloaded"});
      const history = page.locator(".research-history");
      const reader = history.locator(".research-history-detail");
      const article = reader.locator(".research-recorded-finding");
      const picker = reader.getByRole("combobox", {name: "选择问题结论", exact: true});
      await picker.waitFor();
      await page.getByText("开启研究模式", {exact: true}).waitFor();
      assert.equal(await picker.locator("option").count(), 20, "Every finding remains directly selectable");
      assert.equal(await article.count(), 1, "A dense question presents one full document, not twenty stacked documents");
      assert.equal(await picker.inputValue(), "finding-19");
      await page.waitForURL(/research_finding=finding-19/);
      assert.match(await reader.locator(".research-finding-exceptions").innerText(), /已反驳 3.*待检查 4/);
      assert.equal(await history.locator(".research-history-canvas").isVisible(), false, "The selected text leads; history is available on demand");
      await page.screenshot({path: resolve(outputDir, "research-finding-reader-desktop.png"), animations: "disabled"});
      await page.locator(".personal-channel-scroll").evaluate(element => element.scrollTop = 0);
      await page.screenshot({path: resolve(outputDir, "research-finding-reader-first-viewport.png"), animations: "disabled"});

      await picker.selectOption("finding-0");
      assert.match(await article.innerText(), /已反驳/);
      assert.match(await article.innerText(), /观测 1：质量下降而延迟改善/);
      const completeObservation = await article.locator(".research-finding-sections dd").first().innerText();
      assert.equal(completeObservation.split("保留失败候选").length - 1, 3, "Selected long prose is complete");
      await page.reload({waitUntil: "domcontentloaded"});
      await picker.waitFor();
      assert.equal(await picker.inputValue(), "finding-0", "Reload keeps the exact finding, not the latest one");
      await reader.getByRole("button", {name: "下一条结论", exact: true}).click();
      assert.equal(await picker.inputValue(), "finding-1");
      await page.goBack();
      await page.waitForFunction(() => new URL(location.href).searchParams.get("research_finding") === "finding-0");
      assert.equal(await picker.inputValue(), "finding-0", "Back restores the prior finding");

      for (const width of [1440, 390]) {
        await page.setViewportSize({width, height: 900});
        const sources = article.locator(".research-finding-sources");
        await sources.locator(":scope > summary").click();
        const original = sources.locator(":scope > details");
        if (!(await original.evaluate(element => element.open))) await original.locator(":scope > summary").click();
        assert.match(await sources.innerText(), /Input revision: input:a{64}/, "The writer's complete original remains reachable");
        assert.match(await sources.innerText(), /result:synthetic-0/);
        const geometry = await reader.evaluate(element => ({font: parseFloat(getComputedStyle(element.querySelector(".research-finding-sections dd")).fontSize),
          sourceFont: parseFloat(getComputedStyle(element.querySelector(".research-finding-sources code")).fontSize), overflow: element.scrollWidth - element.clientWidth, maxHeight: getComputedStyle(element).maxHeight}));
        assert(geometry.font >= 16 && geometry.sourceFont >= 14 && geometry.overflow <= 1 && geometry.maxHeight === "none", "Expanded content is normal reading text without clipping or a fixed-height text wall");
        await page.screenshot({path: resolve(outputDir, `research-finding-reader-detail-${width}.png`), animations: "disabled"});
        await sources.locator(":scope > summary").click();
      }
      await reader.getByRole("button", {name: "下一条结论", exact: true}).focus();
      await page.keyboard.press("Enter");
      assert.equal(await picker.inputValue(), "finding-1", "The complete collection works by keyboard");
      await reader.getByRole("button", {name: "上一条结论", exact: true}).tap();
      assert.equal(await picker.inputValue(), "finding-0");
      await reader.getByRole("button", {name: "下一条结论", exact: true}).tap();
      assert.equal(await picker.inputValue(), "finding-1", "Narrow touch navigation keeps the selected record identity");
      await page.screenshot({path: resolve(outputDir, "research-finding-reader-phone.png"), animations: "disabled"});
      await history.locator(".research-history-context > summary").click();
      assert.equal(await history.locator(".research-history-canvas").isVisible(), true);
      await reader.getByRole("button", {name: "回到全图", exact: true}).click();
      assert.equal(await reader.count(), 0, "Returning restores the original full history");
      assert.equal(new URL(page.url()).searchParams.has("research_finding"), false);

      await page.goto(url.replace("research_node=question", "research_node=blocked"));
      await reader.waitFor();
      assert.match(await reader.innerText(), /无法读取评测来源，当前结果未知/);
      assert.equal(await article.count(), 0, "No finding is invented for an unavailable source");
      await page.goto(url + "&research_finding=absent");
      await picker.waitFor();
      assert.equal(await article.count(), 0, "An unknown explicit finding does not silently open a different record");
      assert.match(await reader.innerText(), /找不到所选结论/);
      await picker.selectOption("finding-4");
      assert.match(await article.innerText(), /待检查/);
      await page.evaluate(() => localStorage.setItem("loopx-pw-locale", "en"));
      await page.reload({waitUntil: "domcontentloaded"});
      await reader.getByRole("combobox", {name: "Choose a question finding", exact: true}).waitFor();
      assert.match(await article.innerText(), /Tentative/);
      assert.match(await article.innerText(), /观测 5/, "Changing interface language reads the original; it does not rewrite it");
      await page.goto(url.replace("research_node=question", "research_node=single"));
      await reader.waitFor();
      assert.equal(await article.count(), 1);
      assert.equal(await reader.getByRole("combobox").count(), 0, "A single finding needs no selector or disabled navigation");
      assert.match(await article.innerText(), /一条完整的原始结论/);
      // Analysis remains scoped by its existing pack owner, not the open node.
      // This presentation-only read fixture must expose the actual chosen scope
      // before its action, even while whole-history metrics are collapsed.
      let analysisWrites = 0;
      await page.route("**/api/chat/research-analysis?*", route => {
        if (route.request().method() !== "GET") {analysisWrites++; return route.abort();}
        const selected = new URL(route.request().url()).searchParams.get("pack_id")?.[0] ?? "a";
        const snapshot = analysisReadback(selected); snapshot.pack.result = null; snapshot.pack.job = null;
        snapshot.pack.config.output_language = new URL(route.request().url()).searchParams.get("output_language");
        return route.fulfill({json: snapshot});
      });
      await page.goto(url);
      const scope = history.locator(".research-analysis-scope");
      await scope.filter({hasText: /^Analysis scope · Synthetic analysis A$/}).waitFor();
      assert.equal(await history.locator(".research-history-context").evaluate(element => element.open), false);
      await history.locator(".research-history-context > summary").click();
      await history.getByRole("combobox", {name: "Evaluation scope", exact: true}).selectOption("b".repeat(64));
      await scope.filter({hasText: /^Analysis scope · Synthetic analysis B$/}).waitFor();
      assert(await scope.evaluate(element => element.scrollWidth <= element.clientWidth + 1), "The chosen analysis scope remains readable in a narrow focused view");
      await history.locator(".research-history-context > summary").click();
      for (const width of [1440, 390]) {
        await page.setViewportSize({width, height: 900});
        await page.locator(".personal-channel-scroll").evaluate(element => element.scrollTop = 0);
        await page.screenshot({path: resolve(outputDir, `research-finding-reader-scope-${width}.png`), animations: "disabled"});
      }
      assert.equal(analysisWrites, 0, "Reading and choosing the displayed scope cannot dispatch analysis");
      const readback = await (await page.request.get(`${owner.base}/fixture/readback`)).json();
      assert(readback.source_unchanged && readback.mutation_attempts === 0, "Reading, switching and recovery have no execution or source-write effect");
      return {coverageEntries: collectCoverage ? await page.coverage.stopJSCoverage() : [], note: "real-owner dense/small/unavailable finding selection, complete original, negative states and exact reload/Back focus"};
    } catch (error) {
      if (page && !page.isClosed()) await page.screenshot({path: resolve(outputDir, "research-finding-reader-failed.png")});
      throw error;
    } finally {
      await page?.close();
      await owner?.close();
      await rm(temporary, {recursive: true, force: true});
    }
  },
};
