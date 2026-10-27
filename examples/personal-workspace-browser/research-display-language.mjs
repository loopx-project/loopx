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
events.append(build_explore_finding_event(goal_id=goal,node_id='question-0',finding_id='finding-0',
    title='Later evidence remains unverified',summary=events[1]['summary'].replace('fixed-v1','fixed-v2').replace('-0.25ms','-0.50ms'),
    status='tentative',supersedes=events[1]['event_id'],recorded_at='2026-01-01T19:45:00Z'))
Path(sys.argv[1]).write_text(json.dumps({'goal_id':goal,'display_name':'合成研究 · 展示语言','events':events},ensure_ascii=False))
`, path], {cwd: repoRoot});
  const capture = JSON.parse(await readFile(path, "utf8"));
  const history = JSON.stringify({ok: true, goal_id: capture.goal_id, items: capture.events, total: capture.events.length, next_cursor: null});
  const evidence = "No native improvement was observed.\nThe initialization record does not identify the original bottleneck.\nThe radius comparison lacks a repair mechanism and complete controls.\nThe restart result does not identify the later adopted revision.\nTrial4failed2checks refers to trial 4 with 2 failed checks, not 4 independent experiments.\n";
  const names = ["variant-alpha", "variant-beta", "variant-gamma", "variant-delta", "variant-epsilon", "independent-control", "paired-alpha", "paired-beta"];
  const question = (id, language) => id === "independent-control"
    ? (language === "zh" ? "独立对照是否支持 V18 的收益？尚无依据。" : "Does the independent control support a benefit for V18? It is not established.")
    : id.startsWith("paired-") ? (language === "zh" ? `${id} 是否在相同预算内排除相反解释？不能把未测结果当作通过，完整保留比较条件与未知。`
      : `Did ${id} rule out the competing explanation under the same budget? Untested results are not passes; preserve the comparison conditions and unknowns.`)
    : (language === "zh" ? `${id} 的实验证据是否检验原始目标，并被后续选择实际采用？保留负面结果、控制差异与未知。`
      : `Does ${id} test the original objective and inform the subsequent selection? Preserve negative results, control differences and unknowns.`);
  const source = (id, artifact, text) => ({id, artifact, first_line: 1, text, artifact_sha256: createHash("sha256").update(text).digest("hex")});
  const reasons = {
    zh: ["缺少原目标瓶颈的记录。虽有候选初始化与完整求解结果，但没有对应关系，不能据此认定目标关联。",
      "来源未说明修复机制，也未交代与参考方案的控制差异；判断保留未知，不能用候选名称代替机制证据。",
      "后续选择没有标明采用版本。本地测试和原生结果之间缺少回读，不能从一次诊断推断持续收益或因果效果。"],
    en: ["The initialization and full-solver result are recorded, but the original bottleneck is not identified. The intervention’s relationship to that bottleneck remains unknown.",
      "The source does not describe the repair mechanism or complete controls against the reference. A candidate name cannot replace mechanism evidence; the judgment remains unknown.",
      "The later selection does not identify the adopted revision. Without readback connecting local tests and native outcomes, this diagnosis cannot establish sustained or causal benefit."],
  };
  const translate = row => row.text.replaceAll("Does ", "").replaceAll(" improve native utility under the fixed budget?", " 是否在固定预算内改善原生收益？")
    .replaceAll("Not established; no native validation yet.", "尚未确定；尚无原生验证。")
    .replaceAll("Negative evidence, not acceptance", "负向证据，尚未通过验收")
    .replaceAll("Later evidence remains unverified", "后续证据，仍未核验")
    .replaceAll("Fixed source only; the later attempt remains unverified.", "仅适用于固定来源；后续尝试仍未核验。")
    .replaceAll("The measured change was ", "实测变化为 ").replaceAll(" and does not establish causal benefit.", "，尚不能证明因果收益。")
    .replace(/(⟪LX\d+⟫); (⟪LX\d+⟫) evidence remains preliminary\./g, (original, trial, scope) =>
      row.literals?.includes("Trial4failed2checks") && row.literals?.includes("local-only")
        ? `第4次尝试有2项检查未通过（${trial}）；仅限本地（${scope}）的证据仍属初步。` : original)
    .replaceAll("Keep ", "保留 ").replaceAll("Check the original evidence", "核对原始证据").replaceAll("Never claim native success", "不得宣称原生成功");
  capture.analyses = ["partial", "other-source", "complete"].map(variant => {
    const input = {schema_version: "trajectory_assessment_input_v1", scope: {goal_id: capture.goal_id, case_id: "synthetic-case", run_id: "synthetic-run", revision: variant, window: "fixed twenty-hour record window"},
      objective: "Improve native utility", selection: {rule: "Synthetic episodes, not a whole-run rate", population_episodes: names.length, exhaustive: true},
      sources: [source("evidence", "evidence.txt", evidence), source("history", "history.json", history)],
      episodes: names.map(id => ({id, question: question(id, "en"), source_ids: ["evidence"], eligible_metrics: [...TRAJECTORY_METRICS]})),
      display_snapshot: {schema_version: "research_display_snapshot_v1", source_id: "history", source_namespace: variant === "other-source" ? "other-source" : "local",
        ...(variant === "complete" ? {translation_encoding: "source_text_aliases_with_literal_values_v1",
          contexts: capture.events.filter(event => event.event_kind === "finding").map(event => ({event_id: event.event_id, field: "observation",
            citations: [{source_id: "evidence", first_line: 5, last_line: 5}]}))} : {})}};
    const outputs = Object.fromEntries(["zh", "en"].map(language => {
      const p = prepareTrajectoryAssessment({input, config: {model: "gpt-6.1-sol", effort: "max", service_tier: "fast", output_language: language}});
      let rows = JSON.parse(p.prompt.split("\n\ndisplay_fields:\n")[1]).map(row => ({field_id: row.field_id, text: language === "zh" ? translate(row) : row.text}));
      if (variant === "partial") rows = [{...rows[0], text: "999"}, ...rows.slice(2, -1)];
      return [language, {assessments: TRAJECTORY_METRICS.flatMap(metric => names.map((episode_id, index) => ({episode_id, metric,
        verdict: index < 3 ? "unknown" : index === 3 ? "no" : "yes",
        reason: reasons[language][index] ?? (language === "zh" ? "合成证据；负面结果并未证明因果收益，原生效果仍未核验。" : "Synthetic evidence; a negative result does not establish causal benefit. Native effectiveness remains unverified."),
        citations: index < 3 ? [] : [{source_id: "evidence", first_line: 1, last_line: 1}]}))),
        limitations: [language === "zh" ? "合成翻译仅用于交互验证。" : "Synthetic translation for interaction validation only."],
        episode_questions: names.map(episode_id => ({episode_id, question: question(episode_id, language)})), display_translations: rows}];
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
        // Model an appended analysis version becoming visible in the same
        // real owner's catalog after this page has already read its snapshot.
        let newVersionVisible = false, rejectedSubmission = false;
        await page.route(url => url.pathname === "/api/chat/research-analysis", async route => {
          if (route.request().method() !== "GET") return route.continue();
          const response = await route.fetch();
          const value = await response.json();
          if (!newVersionVisible) value.catalog.packs = value.catalog.packs.filter(pack => pack.title !== "部分译文");
          // Fault-inject advertised analysis availability for the refusal
          // case only. The real fixture HTTP owner still rejects every POST.
          if (rejectedSubmission) value.execution_enabled = true;
          await route.fulfill({response, json: value});
        });
        const url = `${owner.base}/chat/?goalId=${owner.goal_id}&view=research&statusUrl=%2Fstatus.json`;
        await page.goto(url, {waitUntil: "domcontentloaded"});
        await page.evaluate(() => localStorage.setItem("loopx-pw-locale", "zh-CN"));
        await page.reload({waitUntil: "domcontentloaded"});
        const history = page.locator(".research-history"), reader = history.locator(".research-history-detail");
        const original = history.getByRole("button", {name: "查看原文", exact: true});
        await original.waitFor(); await page.waitForFunction(() => !document.querySelector(".research-display-language > button")?.disabled);
        assert.match(await history.locator(".research-route-caption").first().innerText(), /是否在固定预算/);
        const scope = history.locator(".research-progress > header select").first();
        assert.equal(await scope.locator("option").filter({hasText: "部分译文"}).count(), 0);
        const beforeRefresh = await fetch(`${owner.base}/fixture/readback`).then(response => response.json());
        const selectedScope = await scope.inputValue(), selectedUrl = page.url();
        newVersionVisible = true;
        await history.getByRole("button", {name: "刷新记录", exact: true}).click();
        await scope.locator("option").filter({hasText: "部分译文"}).waitFor({state: "attached", timeout: 5000});
        const afterRefresh = await fetch(`${owner.base}/fixture/readback`).then(response => response.json());
        assert.ok(afterRefresh.history_reads > beforeRefresh.history_reads, "One refresh reloads history as well as the analysis catalog");
        assert.equal(afterRefresh.mutation_attempts, beforeRefresh.mutation_attempts, "Refreshing never executes analysis");
        assert.equal(await scope.inputValue(), selectedScope, "Refreshing preserves the selected analysis version");
        assert.equal(page.url(), selectedUrl);
        assert.equal(await history.getByRole("region", {name: "轨迹深度分析", exact: true}).count(), 0, "Refreshing does not open analysis");
        await page.locator(".personal-channel-scroll").evaluate(element => element.scrollTop = 0);
        await page.screenshot({path: resolve(outputDir, `research-display-language-first-${width}.png`), animations: "disabled"});
        const metric = history.locator('[data-metric="goal_alignment"]');
        await metric.click();
        const judgments = history.locator('.research-effect-evidence');
        assert.equal(await judgments.locator('.research-assessment-shared-question').count(), 2, "Both a long repeated group and a pair share exact context once");
        assert.deepEqual(await judgments.locator('.research-assessment-name').allTextContents(),
          [...capture.analyses.at(-1).input.episodes.slice(0, 5).map(episode => episode.id), '独立对照是否支持 V18 的收益？尚无依据。', 'paired-alpha', 'paired-beta']);
        assert.deepEqual(await judgments.locator('.research-assessment-verdict').allTextContents(),
          ['证据不足', '证据不足', '证据不足', '未建立', '有依据', '有依据', '有依据', '有依据']);
        const firstJudgment = judgments.locator('.research-assessment').first();
        await firstJudgment.locator(':scope > summary').focus(); await page.keyboard.press('Enter');
        await page.waitForFunction(element => element.open, await firstJudgment.elementHandle());
        const peerJudgment = judgments.locator('.research-assessment').nth(1);
        await peerJudgment.locator(':scope > summary').click();
        await page.waitForFunction(element => element.open, await peerJudgment.elementHandle());
        assert.equal(await judgments.locator('.research-assessment[open]').count(), 2, "Independent row expansion is preserved");
        assert.equal(await judgments.locator('.research-assessment-question:visible').count(), 0, "Opening several rationales never repeats the shared question");
        assert.equal(await firstJudgment.locator('.research-assessment-reason').innerText(), capture.analyses.at(-1).outputs.zh.assessments[0].reason);
        assert.notEqual(await peerJudgment.locator('.research-assessment-reason').innerText(), await firstJudgment.locator('.research-assessment-reason').innerText(), "Distinct source rationales stay distinct");
        assert(await firstJudgment.locator('.research-assessment-body p').first().evaluate(element => parseFloat(getComputedStyle(element).fontSize) >= 16));
        await firstJudgment.locator('.research-assessment-body details > summary').click();
        assert.match(await firstJudgment.locator('.research-assessment-question').innerText(), /保留负面结果、控制差异与未知/);
        assert.match(await firstJudgment.innerText(), /Does variant-alpha test the original objective/);
        await judgments.press('Escape');
        assert.equal(await metric.evaluate(element => element === document.activeElement), true);
        await original.click();
        await page.screenshot({path: resolve(outputDir, `research-display-language-original-${width}.png`), animations: "disabled"});
        await history.getByRole("button", {name: "查看译文", exact: true}).click();
        const entry = history.locator(".research-route-caption button").first();
        const returnKey = await entry.getAttribute("data-research-return");
        await entry.focus(); await page.keyboard.press("Enter"); await reader.waitFor();
        const question = reader.locator(".research-reader-question");
        assert(await question.evaluate(element => element.open), "The route's complete question opens with its finding");
        assert.match(await question.innerText(), /V18 是否在固定预算/);
        assert.match(await reader.innerText(), /实测变化为 -0.50ms/);
        assert.match(await reader.innerText(), /第4次尝试有2项检查未通过（Trial4failed2checks）；仅限本地（local-only）的证据仍属初步/);
        assert.match(await reader.innerText(), /暂定结论/);
        assert(!await reader.locator(".research-finding-sections").first().innerText().then(text => text.includes("Input revision:")));
        const provenance = reader.locator('.research-node-provenance');
        await provenance.locator(':scope > summary').click();
        await provenance.getByRole('heading', {name: '译文依据', exact: true}).waitFor();
        assert.equal(await provenance.getByRole('heading', {name: '显式关联', exact: true}).count(), 0, 'An empty relationship section adds no heading');
        const contextCitation = provenance.getByRole('button', {name: 'evidence · 行 5–5', exact: true});
        await contextCitation.focus(); await page.keyboard.press('Enter');
        const sourceReader = history.getByRole('region', {name: '引用原文', exact: true});
        await sourceReader.getByText('Trial4failed2checks refers to trial 4 with 2 failed checks, not 4 independent experiments.', {exact: true}).waitFor();
        await sourceReader.press('Escape');
        await page.waitForFunction(element => element === document.activeElement, await contextCitation.elementHandle());
        assert.equal(await contextCitation.evaluate(element => element === document.activeElement), true);
        assert.equal(await history.getByRole('region', {name: '轨迹深度分析', exact: true}).count(), 0, 'A context citation preserves closed analysis');
        await page.screenshot({path: resolve(outputDir, `research-display-context-reader-${width}.png`), animations: 'disabled'});
        await provenance.locator(':scope > summary').click();
        await page.locator(".personal-channel-scroll").evaluate(element => element.scrollTop = 0);
        await page.screenshot({path: resolve(outputDir, `research-display-language-reader-${width}.png`), animations: "disabled"});
        await original.click();
        assert.equal(await question.locator(":scope > summary").innerText(), capture.events[0].title);
        await page.screenshot({path: resolve(outputDir, `research-display-language-original-reader-${width}.png`), animations: "disabled"});
        await history.getByRole("button", {name: "查看译文", exact: true}).click();
        await reader.focus(); await page.keyboard.press("Escape");
        await page.waitForFunction(key => document.activeElement?.dataset.researchReturn === key, returnKey);
        const allRecords = history.locator(".research-history-all-records");
        await allRecords.locator(":scope > summary").click();
        assert(await allRecords.locator("li").first().evaluate(element => parseFloat(getComputedStyle(element).fontSize) >= 16), "Expanded history uses readable body typography");
        assert(!await allRecords.innerText().then(text => text.includes("Input revision:")), "Serialized source labels stay inside original view");
        const search = allRecords.getByRole("searchbox", {name: "搜索研究记录"});
        const downloadRecords = async () => {
          const download = page.waitForEvent("download");
          await allRecords.getByRole("button", {name: "下载匹配记录（JSON）", exact: true}).click();
          return JSON.parse(await readFile(await (await download).path(), "utf8"));
        };
        const originalsExport = await downloadRecords();
        await search.fill("V18 是否在固定预算");
        await allRecords.evaluate(element => element.scrollIntoView({block: "start"}));
        await page.screenshot({path: resolve(outputDir, `research-translated-search-title-${width}.png`), animations: "disabled"});
        await allRecords.getByRole("status", {name: new RegExp(`^匹配 1 / ${capture.events.length} 条已读记录`)}).waitFor();
        assert.equal(await allRecords.locator(".research-history-records > li").count(), 1, "Displayed Chinese titles are searchable");
        const finding = capture.events.find(event => event.event_kind === "finding");
        await search.fill(`第4次尝试 ${finding.event_id}`);
        await allRecords.getByRole("status", {name: new RegExp(`^匹配 2 / ${capture.events.length} 条已读记录`)}).waitFor();
        const lineageIds = [finding.event_id, capture.events.at(-1).event_id];
        assert.deepEqual((await downloadRecords()).records, originalsExport.records.filter(event => lineageIds.includes(event.event_id)),
          "The original id also matches its explicit successor lineage; downloads retain both original records");
        await search.fill(`第4次尝试 ${finding.event_id} fixed-v1`);
        await allRecords.getByRole("status", {name: new RegExp(`^匹配 1 / ${capture.events.length} 条已读记录`)}).waitFor();
        const translatedFinding = allRecords.locator(".research-history-records > li").first();
        await translatedFinding.locator(":scope > details > summary").click();
        assert.match(await translatedFinding.innerText(), /实测变化为 -0.25ms/);
        const exported = await downloadRecords();
        assert.deepEqual(exported.records, originalsExport.records.filter(event => event.event_id === finding.event_id),
          "Chinese matching downloads the original event, not translated prose");
        await page.screenshot({path: resolve(outputDir, `research-translated-search-body-${width}.png`), animations: "disabled"});
        await search.fill(`核对原始证据 ${finding.event_id} fixed-v1`);
        await allRecords.getByRole("status", {name: new RegExp(`^匹配 1 / ${capture.events.length} 条已读记录`)}).waitFor();
        await original.click();
        await allRecords.getByRole("status", {name: new RegExp(`^匹配 0 / ${capture.events.length} 条已读记录`)}).waitFor();
        assert.equal(await search.inputValue(), `核对原始证据 ${finding.event_id} fixed-v1`, "Original view retains the query but removes translation-only matches");
        await history.getByRole("button", {name: "查看译文", exact: true}).click();
        await allRecords.getByRole("status", {name: new RegExp(`^匹配 1 / ${capture.events.length} 条已读记录`)}).waitFor();
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
        await search.fill("finding-0");
        assert.equal(await allRecords.locator(".research-history-records > li").count(), 2);
        const revision = allRecords.locator(".research-record-entry").last();
        await revision.locator(":scope > summary").click();
        const comparison = revision.locator(".research-record-comparison");
        const compareEntry = comparison.locator(":scope > summary");
        await compareEntry.focus(); await compareEntry.press("Enter");
        const comparisonText = comparison.locator(".research-comparison-field").nth(1);
        await comparisonText.waitFor();
        await comparisonText.evaluate(element => element.scrollIntoView({block: "center"}));
        await page.screenshot({path: resolve(outputDir, `research-display-version-comparison-${width}.png`), animations: "disabled"});
        assert.match(await comparisonText.innerText(), /实测变化为 -0\.50ms/,
          "Version comparison uses the same qualified snapshot translation as the history reader");
        assert.match(await comparisonText.innerText(), /实测变化为 -0\.25ms/);
        assert.match(await comparisonText.innerText(), /尚不能证明因果收益/);
        assert.match(await comparison.innerText(), /已反驳/);
        const comparisonIds = await comparison.getByRole("combobox").inputValue();
        const changedCount = await comparison.getByRole("status").innerText();
        const sourceTexts = capture.events.filter(event => event.result_id === "finding-0").map(event => event.summary);
        const originalTexts = comparisonText.locator("details > summary");
        assert.equal(await originalTexts.count(), 2);
        await originalTexts.first().click();
        await comparisonText.getByText(sourceTexts[1], {exact: true}).waitFor();
        await originalTexts.first().click();
        await original.click();
        assert.match(await comparisonText.innerText(), /The measured change was -0\.50ms/);
        assert.equal(await comparison.getByRole("combobox").inputValue(), comparisonIds,
          "Original/translation toggles retain the exact comparison pair");
        assert.equal(await comparison.getByRole("status").innerText(), changedCount,
          "Translation changes never alter the count of changed source fields");
        await history.getByRole("button", {name: "查看译文", exact: true}).click();
        await compareEntry.focus(); await compareEntry.press("Enter");
        assert(await compareEntry.evaluate(element => element === document.activeElement));
        await allRecords.getByRole("button", {name: "清除筛选", exact: true}).click();
        await allRecords.locator(":scope > summary").click();
        await history.getByRole("button", {name: "查看分析", exact: true}).click();
        const analysis = history.getByRole("region", {name: "轨迹深度分析", exact: true});
        const episodes = analysis.locator(".analysis-episode");
        await episodes.last().waitFor();
        assert.equal(await episodes.count(), 8, "The real analysis API retains all declared episodes, including the independent comparison");
        await analysis.evaluate(element => element.scrollIntoView({block: "start"}));
        await page.mouse.move(0, 0);
        await page.locator('.context-help-popover:popover-open').waitFor({state: 'hidden'});
        await page.screenshot({path: resolve(outputDir, `research-analysis-comparison-${width}.png`), animations: "disabled"});
        const firstEpisode = episodes.first(), firstSummary = firstEpisode.locator(":scope > summary");
        await firstSummary.focus(); await firstSummary.press("Enter");
        await firstEpisode.getByText(/缺少原目标瓶颈的记录/).first().waitFor();
        assert.equal(await firstEpisode.getByText(capture.analyses[2].outputs.zh.assessments[0].reason, {exact: true}).count(), 1,
          "The same exact rationale and evidence are not repeated for each judgment");
        assert(await firstEpisode.locator('.analysis-episode-context p').first().evaluate(element => parseFloat(getComputedStyle(element).fontSize) >= 16),
          "Expanded rationale is reading copy, not a metadata caption");
        await page.screenshot({path: resolve(outputDir, `research-analysis-comparison-expanded-${width}.png`), animations: "disabled"});
        await firstSummary.press("Enter");
        assert.deepEqual(await episodes.locator(".analysis-episode-name").allTextContents(), [
          "variant-alpha", "variant-beta", "variant-gamma", "variant-delta", "variant-epsilon",
          "独立对照是否支持 V18 的收益？尚无依据。", "paired-alpha", "paired-beta",
        ], "Only exact declared-identity substitutions share a question; the independent question stays complete");
        assert.equal(await analysis.locator(".analysis-shared-question").count(), 2, "Both a long sequence and a pair share their question once");
        assert.match(await firstSummary.getAttribute("aria-label"), /variant-alpha 的实验证据是否检验原始目标/);
        assert.match(await firstSummary.innerText(), /证据不足 6 项/, "Unknown judgments stay visible even before reading evidence");
        assert.match(await episodes.nth(4).locator(":scope > summary").innerText(), /发现无益重复/, "A yes for repetition is an exception, not a positive judgment");
        await firstSummary.press("Enter");
        const fullQuestion = firstEpisode.getByText("完整问题", {exact: true});
        await fullQuestion.click();
        await firstEpisode.getByText(capture.analyses[2].outputs.zh.episode_questions[0].question, {exact: true}).waitFor();
        assert.equal(await firstEpisode.evaluate(element => element.open), true, "Reading the full question does not toggle its parent episode");
        await fullQuestion.click();
        await episodes.nth(4).locator(":scope > summary").click();
        assert.equal(await analysis.locator(".analysis-episode[open]").count(), 1, "A second episode replaces the previous full document");
        const episodeCitation = episodes.nth(4).getByRole("button", {name: "evidence · 行 1–1", exact: true}).first();
        await episodeCitation.click();
        await history.getByRole("region", {name: "引用原文", exact: true}).press("Escape");
        await page.waitForFunction(element => element === document.activeElement, await episodeCitation.elementHandle());
        assert.equal(await episodeCitation.evaluate(element => element === document.activeElement), true);
        assert.equal(await analysis.locator(".analysis-episode[open]").count(), 1, "Citation return retains the selected episode");
        assert(await analysis.evaluate(element => element.scrollWidth <= element.clientWidth + 1));
        await history.getByRole("button", {name: "关闭分析", exact: true}).click();
        await scope.selectOption({label: "部分译文"});
        await history.locator(".research-display-language > span").filter({hasText: /译文 \d+ \/ \d+/}).waitFor();
        assert.match(await history.locator(".research-route-caption").first().innerText(), /Does V18/, "Missing/invalid title remains original");
        await allRecords.locator(":scope > summary").click();
        await allRecords.getByRole("searchbox").fill("finding-0");
        const partialComparison = allRecords.locator(".research-record-entry").last();
        await partialComparison.locator(":scope > summary").click();
        await partialComparison.locator(".research-record-comparison > summary").click();
        const partialText = partialComparison.locator(".research-comparison-field").nth(1);
        await partialText.waitFor();
        assert.match(await partialText.innerText(), /Keep V18/,
          "A missing translation retains the exact source item beside translated fields");
        assert.match(await partialText.innerText(), /保留 V18/);
        await partialText.evaluate(element => element.scrollIntoView({block: "center"}));
        await page.screenshot({path: resolve(outputDir, `research-display-version-partial-${width}.png`), animations: "disabled"});
        await allRecords.locator(":scope > summary").click();
        if (width === 1440) {
          const before = await fetch(`${owner.base}/fixture/readback`).then(response => response.json());
          assert.equal(before.mutation_attempts, 0, "Reading, language and original toggles never execute");
          rejectedSubmission = true;
          await history.getByRole("button", {name: "刷新记录", exact: true}).click();
          await history.locator(".research-analysis-entry-actions > button").click();
          await history.getByRole("button", {name: "重新分析并补全译文", exact: true}).click();
          await history.getByRole("alert").filter({hasText: "分析请求未完成，已保存结果保留。"}).waitFor();
          assert.match(await history.locator(".research-display-language").innerText(), /译文 \d+ \/ \d+/);
          await history.getByRole("button", {name: "关闭分析", exact: true}).click();
        }
        await scope.selectOption({label: "其他来源"});
        await history.getByText("译文版本不适用，显示原文", {exact: true}).waitFor();
        assert.match(await history.locator(".research-route-caption").first().innerText(), /Does V18/);
        await allRecords.locator(":scope > summary").click();
        await search.fill("V18 是否在固定预算");
        await allRecords.getByRole("status", {name: new RegExp(`^匹配 0 / ${capture.events.length} 条已读记录`)}).waitFor();
        await search.fill(capture.events[0].title);
        await allRecords.getByRole("status", {name: new RegExp(`^匹配 1 / ${capture.events.length} 条已读记录`)}).waitFor();
        assert.equal(await allRecords.locator(".research-history-records > li strong").first().innerText(), capture.events[0].title,
          "A mismatched source cannot retain the previous translation's search aliases");
        await allRecords.locator(":scope > summary").click();
        await scope.selectOption({label: "完整固定快照"});
        await original.waitFor();
        await page.reload({waitUntil: "domcontentloaded"}); await original.waitFor();
        await page.evaluate(() => localStorage.setItem("loopx-pw-locale", "en"));
        await page.reload({waitUntil: "domcontentloaded"});
        await history.getByRole("button", {name: "Show originals", exact: true}).waitFor();
        assert.match(await history.locator(".research-route-caption").first().innerText(), /Does V18/);
        await history.locator('.research-route-caption button').first().click(); await reader.waitFor();
        const englishProvenance = reader.locator('.research-node-provenance');
        await englishProvenance.locator(':scope > summary').click();
        await englishProvenance.getByRole('heading', {name: 'Translation context', exact: true}).waitFor();
        const englishCitation = englishProvenance.getByRole('button', {name: 'evidence · lines 5–5', exact: true});
        await englishCitation.click();
        const englishSource = history.getByRole('region', {name: 'Cited original', exact: true});
        await englishSource.getByText('Trial4failed2checks refers to trial 4 with 2 failed checks, not 4 independent experiments.', {exact: true}).waitFor();
        await englishSource.press('Escape');
        await page.waitForFunction(element => element === document.activeElement, await englishCitation.elementHandle());
        assert.equal(await englishCitation.evaluate(element => element === document.activeElement), true);
        await page.screenshot({path: resolve(outputDir, `research-display-context-reader-en-${width}.png`), animations: 'disabled'});
        await reader.focus(); await reader.press('Escape');
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
