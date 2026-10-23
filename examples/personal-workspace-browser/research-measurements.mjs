import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import {mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {resolve} from "node:path";
import {resolveTestPython} from "../../scripts/test-python.mjs";
import {outputDir, repoRoot} from "./fixture.mjs";
import {startHistoryOwner} from "./research-history.mjs";

// The real history HTTP/measurement owner supplies the values and comparisons.
// The browser must keep zero, unknown and a regression readable, with complete
// source identities reachable through one disclosure rather than repeated prose.
export const researchMeasurementsScenario = {
  id: "research-measurements",
  async run({browser, collectCoverage}) {
    const temporary = await mkdtemp(resolve(tmpdir(), "loopx-measurement-reading-"));
    const fixture = resolve(temporary, "capture.json");
    const goal = "synthetic-measurement-reading";
    let owner, session;
    try {
      execFileSync(resolveTestPython(), ["-c", `
import json, sys
from pathlib import Path
from loopx.capabilities.explore.result_log import build_explore_node_event, build_explore_finding_event
goal='synthetic-measurement-reading'
context={'comparison_scope':'synthetic:fixed-window','input_revision':'input:'+('a'*64),
    'evaluation_ref':'method:'+('b'*64),'candidate_ref':'candidate:'+('c'*64),'evidence_ref':'result:synthetic'}
def metrics(values, index=0):
    return [dict(context, candidate_ref='candidate:'+('c' if index==0 else 'd')*64,
        evidence_ref=f'result:synthetic-{index}',evaluation_ref='method:'+method*64,
        metric=metric, unit=unit, higher_is_better=direction, value=value)
        for metric, unit, direction, value, method in zip(
            ['质量 quality','延迟 latency','重试次数 retries','尚未采集的完整成本 unmeasured complete cost'],
            ['points','seconds','count','USD'],[True,False,False,False],values,['b','b','b','e'])]
events=[build_explore_node_event(goal_id=goal,node_id='comparison',title='合成研究 · 比较候选的质量、延迟与完整成本',
    summary='Synthetic presentation data; no solver, model or native evaluation.',recorded_at='2026-01-01T00:00:00Z')]
for index, values in enumerate([[8,12,0,None],[7,10,0,None]]):
    events.append(build_explore_finding_event(goal_id=goal,node_id='comparison',finding_id=f'finding-{index}',
        title='合成比较基线' if index==0 else '候选质量下降，延迟改善；完整成本仍未采集',
        summary='保留负结果，不把局部延迟改善称为整体收益。The quality regression remains visible; complete cost is unknown.',
        status='confirmed' if index==0 else 'refuted',recorded_at=f'2026-01-01T0{index+1}:00:00Z',
        measurements=metrics(values,index),evidence_refs=[f'result:synthetic-{index}']))
events.append(build_explore_node_event(goal_id=goal,node_id='single',title='单项已记录测量',recorded_at='2026-01-01T03:00:00Z'))
events.append(build_explore_finding_event(goal_id=goal,node_id='single',finding_id='single-finding',title='单项测量保持完整来源',
    summary='A small record uses the same reading hierarchy.',status='tentative',recorded_at='2026-01-01T03:00:00Z',
    measurements=metrics([8,12,0,None])[:1],evidence_refs=['result:synthetic-0']))
Path(sys.argv[1]).write_text(json.dumps({'goal_id':goal,'display_name':'合成研究 · 量化结果阅读','events':events},ensure_ascii=False))
`, fixture], {cwd: repoRoot});
      owner = await startHistoryOwner(fixture);
      const page = await browser.newPage({viewport: {width: 1440, height: 900}, hasTouch: true});
      const errors = [];
      page.on("pageerror", error => errors.push(error.message));
      if (collectCoverage) await page.coverage.startJSCoverage({resetOnNavigation: false});
      session = {page, close: async () => {
        const coverage = collectCoverage ? await page.coverage.stopJSCoverage() : [];
        await page.close();
        return coverage;
      }};
      const url = `${owner.base}/chat/`;
      const open = async node => {
        await page.goto(new URL(`?goalId=${goal}&view=research&research_goal=${goal}&research_node=${node}&statusUrl=%2Fstatus.json`, url).href, {waitUntil: "domcontentloaded"});
        await page.locator(".research-history-coverage").filter({hasText: "5 / 5"}).waitFor();
        return page.locator(".research-history-detail");
      };
      let reader = await open("comparison");
      const finding = reader.locator(".research-recorded-finding").last();
      await finding.locator(":scope > summary").click();
      const measurements = finding.locator(".explore-measurements");
      assert.deepEqual(await measurements.locator(".explore-measurement-reading strong").allTextContents(), ["7", "10", "0", "未测量"]);
      assert.equal(await measurements.locator(":scope > details").count(), 1, "Related measurements share one source disclosure");
      const source = measurements.locator(":scope > details");
      assert.equal(await source.evaluate(node => node.open), false);
      const help = measurements.getByRole("button", {name: "指标说明", exact: true});
      await help.focus();
      await measurements.getByRole("tooltip").waitFor({state: "visible"});
      await page.keyboard.press("Escape");
      assert.equal(await finding.evaluate(node => node.open), true, "Help dismissal preserves the selected finding");
      await page.screenshot({path: resolve(outputDir, "research-measurements-desktop.png"), animations: "disabled"});

      for (const width of [1440, 390]) {
        await page.setViewportSize({width, height: 900});
        await source.locator(":scope > summary").click();
        assert.match(await source.innerText(), new RegExp("input:" + "a".repeat(64)), "The complete input identity stays readable");
        assert.match(await source.innerText(), new RegExp("candidate:" + "d".repeat(64)));
        assert.equal((await source.innerText()).split("method:" + "b".repeat(64)).length - 1, 1, "Identical provenance is shown once");
        assert.match(await source.innerText(), new RegExp("method:" + "e".repeat(64)), "A different evaluation identity is not folded into the shared provenance");
        const geometry = await measurements.evaluate(node => ({overflow: node.scrollWidth - node.clientWidth,
          sourceFont: parseFloat(getComputedStyle(node.querySelector("code")).fontSize)}));
        assert(geometry.overflow <= 1, `Measurement detail overflows at ${width}px`);
        assert(geometry.sourceFont >= 14, "Requested source detail uses body-sized text");
        await page.screenshot({path: resolve(outputDir, `research-measurements-source-${width}.png`), animations: "disabled"});
        await source.locator(":scope > summary").click();
      }
      await help.tap();
      await measurements.getByRole("tooltip").waitFor({state: "visible"});
      await source.locator(":scope > summary").tap();
      await measurements.getByRole("tooltip").waitFor({state: "hidden"});
      assert.equal(await finding.evaluate(node => node.open), true, "Touch help dismissal preserves the reading context");
      assert.equal(await source.evaluate(node => node.open), true, "The outside source action still runs");
      await source.locator(":scope > summary").tap();
      await page.screenshot({path: resolve(outputDir, "research-measurements-phone.png"), animations: "disabled"});
      const provenance = reader.locator(".research-node-provenance");
      await provenance.locator(":scope > summary").click();
      const comparison = metric => provenance.locator(".explore-measurement-row").filter({hasText: metric}).locator(".explore-measurement-comparison");
      assert.match(await comparison("质量 quality").innerText(), /8.*-1/s, "Quality regression keeps best and negative change distinct");
      assert.match(await comparison("延迟 latency").innerText(), /10.*\+2/s, "Lower latency is an improvement from the same owner");
      assert.match(await comparison("重试次数 retries").innerText(), /0.*0/s, "A measured zero is not missing data");
      assert.doesNotMatch(await comparison("完整成本").innerText(), /[+-]0|较先前最佳/, "Unknown cost does not invent a difference");
      const bestSources = provenance.locator(".explore-measurement-sources");
      await bestSources.locator(":scope > summary").click();
      const qualitySource = bestSources.locator("dl > div").filter({hasText: "最佳来源 · 质量 quality"});
      assert.deepEqual(await qualitySource.locator("code").allTextContents(), ["candidate:" + "c".repeat(64), "result:synthetic-0"], "A regression preserves the distinct historical best source");
      assert(await bestSources.evaluate(node => node.scrollWidth <= node.clientWidth + 1), "Historical source detail stays within the narrow reader");

      reader = await open("single");
      assert.match(await reader.locator(".explore-measurement-reading:visible").innerText(), /8\s*points/, "The small readout remains directly readable");
      await page.evaluate(() => localStorage.setItem("loopx-pw-locale", "en"));
      await page.reload({waitUntil: "domcontentloaded"});
      await reader.getByRole("heading", {name: "Measured results", exact: true}).waitFor();
      assert.equal(await reader.getByRole("button", {name: "About these measurements", exact: true}).count(), 1);
      const readback = await (await page.request.get(`${owner.base}/fixture/readback`)).json();
      assert(readback.source_unchanged && readback.mutation_attempts === 0, "Reading and help do not rewrite source or execute work");
      assert.deepEqual(errors, []);
      return {coverageEntries: await session.close(), note: "real-owner measured/unknown/zero/regression readback, one source disclosure, desktop/phone/English detail and effect-free help"};
    } catch (error) {
      if (session && !session.page.isClosed()) {
        await session.page.screenshot({path: resolve(outputDir, "research-measurements-failed.png")});
        console.error((await session.page.locator("body").innerText()).slice(0, 1600));
      }
      throw error;
    } finally {
      if (session && !session.page.isClosed()) await session.close();
      await owner?.close();
      await rm(temporary, {recursive: true, force: true});
    }
  },
};
