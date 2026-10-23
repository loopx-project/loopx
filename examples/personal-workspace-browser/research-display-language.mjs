import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {execFileSync} from "node:child_process";
import {mkdtemp, readFile, writeFile, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {resolve} from "node:path";
import {prepareTrajectoryAssessment} from "../../loopx/control_plane/capabilities/trajectory_assessment.ts";
import {TRAJECTORY_METRICS} from "../../loopx/control_plane/capabilities/trajectory_assessment_types.ts";
import {resolveTestPython} from "../../scripts/test-python.mjs";
import {outputDir, repoRoot} from "./fixture.mjs";
import {startHistoryOwner} from "./research-history.mjs";

// Synthetic text, actual Explore builders/SQLite/history/analysis job/HTTP.
// No model, solver or user-session execution is enabled by this regression.
async function displayFixture(path) {
  execFileSync(resolveTestPython(), ["-c", `
import json,sys
from pathlib import Path
from loopx.capabilities.explore.result_log import build_explore_node_event,build_explore_finding_event
goal='synthetic-display-history'; events=[]
for i in range(20):
    node=f'question-{i}'
    events.append(build_explore_node_event(goal_id=goal,node_id=node,node_kind='question',title=f'Does V{i+18} improve native utility under the fixed budget?',
        summary='Not established; no native validation yet.',parent_id='question-0' if i else None,recorded_at=f'2026-01-01T{i:02}:00:00Z'))
    if i%2==0:
        events.append(build_explore_finding_event(goal_id=goal,node_id=node,finding_id=f'finding-{i}',title='Negative evidence, not acceptance',
            summary='Input revision: fixed-v1 Applicability: Fixed source only; the later attempt remains unverified. Observation: The measured change was -0.25ms and does not establish causal benefit. Trial4failed2checks; local-only evidence remains preliminary. Interpretation: Outcome: replan retained: - Keep V18 changed: - Check the original evidence stopped: - Never claim native success',
            status='refuted' if i%4==0 else 'tentative',recorded_at=f'2026-01-01T{i:02}:30:00Z'))
Path(sys.argv[1]).write_text(json.dumps({'goal_id':goal,'display_name':'合成研究 · 展示语言','events':events},ensure_ascii=False))
`, path], {cwd: repoRoot});
  const capture = JSON.parse(await readFile(path, "utf8"));
  const history = JSON.stringify({ok: true, goal_id: capture.goal_id, items: capture.events, total: capture.events.length, next_cursor: null});
  const evidence = "No native improvement was observed.\n";
  const source = (id, artifact, text) => ({id, artifact, first_line: 1, text, artifact_sha256: createHash("sha256").update(text).digest("hex")});
  const translate = row => row.text.replaceAll("Does ", "").replaceAll(" improve native utility under the fixed budget?", " 是否在固定预算内改善原生收益？")
    .replaceAll("Not established; no native validation yet.", "尚未确定；尚无原生验证。")
    .replaceAll("Negative evidence, not acceptance", "负向证据，尚未通过验收")
    .replaceAll("Fixed source only; the later attempt remains unverified.", "仅适用于固定来源；后续尝试仍未核验。")
    .replaceAll("The measured change was ", "实测变化为 ").replaceAll(" and does not establish causal benefit.", "，尚不能证明因果收益。")
    .replace(/(⟪LX\d+⟫); (⟪LX\d+⟫) evidence remains preliminary\./g, (original, trial, scope) =>
      row.literals?.includes("Trial4failed2checks") && row.literals?.includes("local-only")
        ? `本次尝试未通过检查（${trial}）；仅限本地（${scope}）的证据仍属初步。` : original)
    .replaceAll("Keep ", "保留 ").replaceAll("Check the original evidence", "核对原始证据").replaceAll("Never claim native success", "不得宣称原生成功");
  capture.analyses = ["partial", "other-source", "complete"].map(variant => {
    const input = {schema_version: "trajectory_assessment_input_v1", scope: {goal_id: capture.goal_id, case_id: "synthetic-case", run_id: "synthetic-run", revision: variant, window: "fixed twenty-hour record window"},
      objective: "Improve native utility", selection: {rule: "Synthetic episode, not a whole-run rate", population_episodes: 1, exhaustive: true},
      sources: [source("evidence", "evidence.txt", evidence), source("history", "history.json", history)],
      episodes: [{id: "episode", question: "Does V18 help?", source_ids: ["evidence"], eligible_metrics: [...TRAJECTORY_METRICS]}],
      display_snapshot: {schema_version: "research_display_snapshot_v1", source_id: "history", source_namespace: variant === "other-source" ? "other-source" : "local",
        ...(variant === "complete" ? {translation_encoding: "source_text_aliases_with_literals_v1"} : {})}};
    const outputs = Object.fromEntries(["zh", "en"].map(language => {
      const p = prepareTrajectoryAssessment({input, config: {model: "gpt-6.1-sol", effort: "max", service_tier: "fast", output_language: language}});
      let rows = JSON.parse(p.prompt.split("\n\ndisplay_fields:\n")[1]).map(row => ({field_id: row.field_id, text: language === "zh" ? translate(row) : row.text}));
      if (variant === "partial") rows = [{...rows[0], text: "999"}, ...rows.slice(2)];
      return [language, {assessments: TRAJECTORY_METRICS.map(metric => ({episode_id: "episode", metric, verdict: "unknown", reason: language === "zh" ? "合成证据，实际收益未核验。" : "Synthetic evidence; native benefit is unverified.", citations: []})),
        limitations: [language === "zh" ? "合成翻译仅用于交互验证。" : "Synthetic translation for interaction validation only."],
        episode_questions: [{episode_id: "episode", question: language === "zh" ? "V18 是否有帮助？" : "Does V18 help?"}], display_translations: rows}];
    }));
    return {title: {partial: "部分译文", "other-source": "其他来源", complete: "完整固定快照"}[variant], input, artifacts: {"evidence.txt": evidence, "history.json": history}, outputs};
  });
  await writeFile(path, JSON.stringify(capture));
  return capture;
}

export const researchDisplayLanguageScenario = {
  id: "research-display-language",
  async run({browser}) {
    const directory = await mkdtemp(resolve(tmpdir(), "loopx-display-language-"));
    let owner;
    try {
      const path = resolve(directory, "capture.json"), capture = await displayFixture(path);
      owner = await startHistoryOwner(path);
      for (const width of [1440, 390]) {
        const page = await browser.newPage({viewport: {width, height: 1000}, reducedMotion: "reduce"});
        const context = page.context();
        const url = `${owner.base}/chat/?goalId=${owner.goal_id}&view=research&statusUrl=%2Fstatus.json`;
        await page.goto(url, {waitUntil: "domcontentloaded"});
        await page.evaluate(() => localStorage.setItem("loopx-pw-locale", "zh-CN"));
        await page.reload({waitUntil: "domcontentloaded"});
        const history = page.locator(".research-history"), reader = history.locator(".research-history-detail");
        const original = history.getByRole("button", {name: "查看原文", exact: true});
        await original.waitFor(); await page.waitForFunction(() => !document.querySelector(".research-display-language > button")?.disabled);
        assert.match(await history.locator(".research-route-caption").first().innerText(), /是否在固定预算/);
        await page.locator(".personal-channel-scroll").evaluate(element => element.scrollTop = 0);
        await page.screenshot({path: resolve(outputDir, `research-display-language-first-${width}.png`), animations: "disabled"});
        await original.click();
        await page.screenshot({path: resolve(outputDir, `research-display-language-original-${width}.png`), animations: "disabled"});
        await history.getByRole("button", {name: "查看译文", exact: true}).click();
        const entry = history.locator(".research-route-caption button").first();
        const returnKey = await entry.getAttribute("data-research-return");
        await entry.focus(); await page.keyboard.press("Enter"); await reader.waitFor();
        assert.match(await reader.locator("h3").first().innerText(), /V18 是否在固定预算/);
        assert.match(await reader.innerText(), /实测变化为 -0.25ms/);
        assert.match(await reader.innerText(), /本次尝试未通过检查（Trial4failed2checks）；仅限本地（local-only）的证据仍属初步/);
        assert.match(await reader.innerText(), /已反驳/);
        assert(!await reader.locator(".research-finding-sections").first().innerText().then(text => text.includes("Input revision:")));
        await page.locator(".personal-channel-scroll").evaluate(element => element.scrollTop = 0);
        await page.screenshot({path: resolve(outputDir, `research-display-language-reader-${width}.png`), animations: "disabled"});
        await original.click();
        assert.equal(await reader.locator("h3").first().innerText(), capture.events[0].title);
        await page.screenshot({path: resolve(outputDir, `research-display-language-original-reader-${width}.png`), animations: "disabled"});
        await history.getByRole("button", {name: "查看译文", exact: true}).click();
        await reader.focus(); await page.keyboard.press("Escape");
        await page.waitForFunction(key => document.activeElement?.dataset.researchReturn === key, returnKey);
        const allRecords = history.locator(".research-history-all-records");
        await allRecords.locator(":scope > summary").click();
        assert(await allRecords.locator("li").first().evaluate(element => parseFloat(getComputedStyle(element).fontSize) >= 16), "Expanded history uses readable body typography");
        assert(!await allRecords.innerText().then(text => text.includes("Input revision:")), "Serialized source labels stay inside original view");
        const search = allRecords.getByRole("searchbox", {name: "搜索研究记录"});
        await search.fill(capture.events[0].title);
        assert.equal(await allRecords.locator(".research-history-records > li").count(), 1, "Search matches original text while translations are displayed");
        const record = allRecords.locator(".research-history-records > li").first();
        assert.match(await record.locator("strong").innerText(), /V18 是否在固定预算/);
        await record.locator(":scope > details > summary").click();
        await record.getByText("尚未确定；尚无原生验证。", {exact: true}).waitFor();
        await record.getByText("完整原文", {exact: true}).click();
        await record.getByText(capture.events[0].summary, {exact: true}).waitFor();
        await original.click();
        assert.equal(await record.locator("strong").innerText(), capture.events[0].title);
        assert.equal(await search.inputValue(), capture.events[0].title, "Language display toggles retain the original query");
        await history.getByRole("button", {name: "查看译文", exact: true}).click();
        await allRecords.getByRole("button", {name: "清除筛选", exact: true}).click();
        assert.equal(await allRecords.locator(".research-history-records > li").count(), capture.events.length);
        await allRecords.locator(":scope > summary").click();
        const scope = history.locator(".research-progress > header select").first();
        await scope.selectOption({label: "部分译文"});
        await history.locator(".research-display-language > span").filter({hasText: /译文 \d+ \/ \d+/}).waitFor();
        assert.match(await history.locator(".research-route-caption").first().innerText(), /Does V18/, "Missing/invalid title remains original");
        if (width === 1440) {
          const before = await fetch(`${owner.base}/fixture/readback`).then(response => response.json());
          assert.equal(before.mutation_attempts, 0, "Reading, language and original toggles never execute");
          await history.locator(".research-analysis-entry-actions > button").click();
          await history.getByRole("button", {name: "重新分析并补全译文", exact: true}).click();
          await history.getByRole("alert").filter({hasText: "分析请求未完成，已保存结果保留。"}).waitFor();
          assert.match(await history.locator(".research-display-language").innerText(), /译文 \d+ \/ \d+/);
          await history.getByRole("button", {name: "关闭分析", exact: true}).click();
        }
        await scope.selectOption({label: "其他来源"});
        await history.getByText("译文版本不适用，显示原文", {exact: true}).waitFor();
        assert.match(await history.locator(".research-route-caption").first().innerText(), /Does V18/);
        await scope.selectOption({label: "完整固定快照"});
        await original.waitFor();
        await page.reload({waitUntil: "domcontentloaded"}); await original.waitFor();
        await page.evaluate(() => localStorage.setItem("loopx-pw-locale", "en"));
        await page.reload({waitUntil: "domcontentloaded"});
        await history.getByRole("button", {name: "Show originals", exact: true}).waitFor();
        assert.match(await history.locator(".research-route-caption").first().innerText(), /Does V18/);
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "No page-level narrow overflow");
        await page.addInitScript(() => Object.defineProperty(globalThis.crypto, "subtle", {value: undefined}));
        await page.reload({waitUntil: "domcontentloaded"});
        await history.getByText("Translation cannot be checked; showing originals", {exact: true}).waitFor();
        assert.match(await history.locator(".research-route-caption").first().innerText(), /Does V18/);
        await context.close();
      }
      const readback = await fetch(`${owner.base}/fixture/readback`).then(response => response.json());
      assert.equal(readback.mutation_attempts, 1); assert.equal(readback.source_unchanged, true);
      return {coverageEntries: [], note: "Whole-history translation/original reading, partial/invalid/source/language isolation with actual SQLite/HTTP; browsing never writes, one explicit retry is refused without losing saved results."};
    } finally {await owner?.close(); await rm(directory, {recursive: true, force: true});}
  },
};
