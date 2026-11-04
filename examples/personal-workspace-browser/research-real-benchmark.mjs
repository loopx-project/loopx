import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {outputDir, repoRoot} from "./fixture.mjs";
import {startHistoryOwner} from "./research-history.mjs";

async function readNativeResults(page, history, capture, language) {
  const pack = capture.analysis_readbacks.original.response.pack;
  const native = pack.native_outcomes.projection;
  const progress = history.locator(".research-progress");
  await progress.locator(".research-native-results").waitFor();
  if (pack.scope.case_id === "ad_placement_optimization") {
    const best = await progress.locator('[data-native-point="best_valid"] strong').innerText();
    const final = await progress.locator('[data-native-point="final_valid"] strong').innerText();
    assert.notEqual(best, final, "The real best and final results must remain distinguishable before opening evidence");
  }
  for (const key of ["initial", "best_valid", "retained", "final_valid"]) {
    const row = progress.locator(`[data-native-point="${key}"]`);
    const point = native[key];
    if (!point) {
      assert.match(await row.innerText(), language === "zh" ? /未知/ : /Unknown/);
      assert.equal(await row.getByRole("button").count(), 0, "Missing retention is not a zero-valued result");
      continue;
    }
    if (point.value === 0) assert.equal(await row.locator("strong").innerText(), "0");
    await row.getByRole("button").click();
    const source = history.locator(".analysis-source");
    await source.locator("pre").waitFor();
    const citation = capture.analysis_citations.find(item => {
      const query = new URL(item.request_path, "http://fixture").searchParams;
      return query.get("source_id") === point.citation.source_id
        && query.get("first_line") === String(point.citation.first_line)
        && query.get("last_line") === String(point.citation.last_line);
    });
    assert(citation, "Each native value has its captured original citation");
    assert.equal(await source.locator("pre").innerText(), citation.response.text);
    assert.equal(JSON.parse(citation.response.text).value, point.value);
    await source.getByRole("button", {name: language === "zh" ? "关闭原文" : "Close source", exact: true}).click();
  }
  const retained = progress.locator(".research-retained-improvement");
  if (native.retained_improvement) {
    assert.equal(native.retained.candidate_ref, "agent-16");
    assert.equal(native.final_valid.candidate_ref, "agent-18");
    assert.equal(native.retained_improvement.baseline.candidate_ref, "agent-2");
    assert.match(await retained.locator("[data-retained-improvement]").innerText(), /\+0\.0323/);
    await retained.locator(":scope > details > summary").click();
    await retained.getByRole("button", {name: language === "zh" ? "查看原文" : "Read original evidence", exact: true}).click();
    const source = history.locator(".analysis-source");
    await source.locator("pre").waitFor();
    assert.match(await source.locator("pre").innerText(), /"best_round": "agent-16"/);
    await source.getByRole("button", {name: language === "zh" ? "关闭原文" : "Close source", exact: true}).click();
  } else {
    assert.equal(await retained.count(), 0, "A best/final result cannot supply missing adoption evidence");
  }
  await progress.locator(".research-progress-basis > summary").click();
  const cost = progress.locator(".research-analysis-cost");
  if (!pack.job) {
    assert.equal(pack.result, null);
    assert.match(await cost.innerText(), language === "zh" ? /未采集/ : /Not captured/);
    assert.equal(await cost.locator(".research-analysis-values").count(), 0);
    assert.equal(await history.locator("[data-analysis-judgment]").count(), 0);
  }
  await progress.locator(".research-progress-basis > summary").click();
  const name = pack.scope.case_id.split("_")[0];
  for (const width of [1440, 390]) {
    await page.setViewportSize({width, height: 900});
    await progress.scrollIntoViewIfNeeded();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({path: resolve(outputDir, `research-real-native-${name}-${language}-${width}.png`), animations: "disabled"});
  }
  await page.setViewportSize({width: 1440, height: 900});
}

async function readOriginalNativeSnapshot(page, history, capture, language) {
  const initial = capture.analysis_readbacks.initial_original.response.pack;
  const current = capture.analysis_readbacks.original.response.pack;
  const progress = history.locator(".research-progress");
  const scope = progress.getByRole("combobox", {name: language === "zh" ? "评估范围" : "Evaluation scope"});
  const selectOriginal = async () => {
    await scope.selectOption(initial.pack_id);
    await progress.locator(`[data-pack-id="${initial.pack_id}"]`).waitFor();
    await history.locator('.research-analysis [data-analysis-judgment]').first().waitFor({state: "attached"});
  };
  await selectOriginal();
  for (const key of ["initial", "retained"]) {
    const row = progress.locator(`[data-native-point="${key}"]`);
    assert.match(await row.innerText(), language === "zh" ? /未知/ : /Unknown/);
    assert.equal(await row.getByRole("button").count(), 0, "An absent native result is not borrowed from another snapshot");
  }
  assert.match(await progress.locator('[data-native-point="best_valid"] button').getAttribute("aria-label"), /0\.849544264387785/);
  assert.match(await progress.locator('[data-native-point="final_valid"] button').getAttribute("aria-label"), /0\.8485288027628158/);
  assert.equal(await progress.locator("[data-retained-improvement]").count(), 0, "A best measurement does not supply missing retained improvement");
  assert.equal(await history.locator("[data-analysis-judgment]").count(), 36, "The same original process judgments remain available");
  const missing = history.locator('[data-analysis-episode="native-feedback-1"]');
  await missing.locator(":scope > summary").click();
  for (const detail of await missing.locator(".analysis-judgments > details").all()) {
    if (!await detail.evaluate(element => element.open)) await detail.locator(":scope > summary").click();
  }
  await missing.getByRole("button", {name: /native-report-1/}).first().click();
  const source = history.locator(".analysis-source");
  await source.locator(".analysis-error").waitFor();
  assert.equal(await source.locator("pre").count(), 0, "An uncaptured source is not borrowed from a different snapshot");
  await source.getByRole("button", {name: language === "zh" ? "关闭原文" : "Close source", exact: true}).click();
  await progress.locator('[data-native-point="best_valid"] button').click();
  await source.locator("pre").waitFor();
  assert.equal(JSON.parse(await source.locator("pre").innerText()).value, 0.849544264387785);
  await source.getByRole("button", {name: language === "zh" ? "关闭原文" : "Close source", exact: true}).click();
  await page.goBack();
  await progress.locator(`[data-pack-id="${current.pack_id}"]`).waitFor();
  assert.match(await progress.locator("[data-retained-improvement]").innerText(), /\+0\.0323/);
  await selectOriginal();
  for (const width of [1440, 390]) {
    await page.setViewportSize({width, height: 900});
    await progress.scrollIntoViewIfNeeded();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({path: resolve(outputDir, `research-real-graph-retention-unknown-${language}-${width}.png`), animations: "disabled"});
  }
  await page.setViewportSize({width: 1440, height: 900});
}

async function readUnanalyzedNativeResults(browser, collectCoverage) {
  const coverageEntries = [];
  for (const name of ["portfolio", "ad"]) {
    const path = resolve(repoRoot, `apps/presentation/dashboard/src/data/fixtures/edgebench-${name}-native-results.json`);
    const capture = JSON.parse(await readFile(path, "utf8"));
    const owner = await startHistoryOwner(path);
    let page;
    try {
      page = await browser.newPage({viewport: {width: 1440, height: 900}});
      if (collectCoverage) await page.coverage.startJSCoverage({resetOnNavigation: false});
      for (const language of ["zh", "en"]) {
        await page.addInitScript(value => localStorage.setItem("loopx-pw-locale", value), language);
        await page.goto(`${owner.base}/chat/?goalId=${capture.goal_id}&view=research`);
        await readNativeResults(page, page.locator(".research-history"), capture, language);
      }
      const readback = await (await page.request.get(`${owner.base}/fixture/readback`)).json();
      assert.equal(readback.source_unchanged, true);
      assert.equal(readback.mutation_attempts, 0);
      if (collectCoverage) coverageEntries.push(...await page.coverage.stopJSCoverage());
    } finally {
      if (page) await page.close();
      await owner.close();
    }
  }
  return coverageEntries;
}

async function readSavedGraphAnalysis(browser, collectCoverage) {
  const path = resolve(repoRoot, "apps/presentation/dashboard/src/data/fixtures/edgebench-graph-saved-analysis.json");
  const capture = JSON.parse(await readFile(path, "utf8"));
  const saved = capture.analysis_readbacks.original.response.pack;
  const owner = await startHistoryOwner(path);
  let page;
  const coverageEntries = [];
  try {
    page = await browser.newPage({viewport: {width: 1440, height: 900}});
    if (collectCoverage) await page.coverage.startJSCoverage({resetOnNavigation: false});
    for (const language of ["zh", "en"]) {
      await page.addInitScript(value => localStorage.setItem("loopx-pw-locale", value), language);
      await page.goto(`${owner.base}/chat/?goalId=${capture.goal_id}&view=research`);
      const history = page.locator(".research-history");
      await readNativeResults(page, history, capture, language);
      await history.getByRole("button", {name: language === "zh" ? "查看分析" : "View analysis", exact: true}).click();
      const analysis = history.locator(".research-analysis[role=region]");
      await analysis.locator(".analysis-episode").first().waitFor();
      assert.match(await analysis.locator(".analysis-warning").first().innerText(), language === "zh" ? /已保存的原文/ : /saved original/);
      assert.match(await analysis.locator(".analysis-finding h4").innerText(), language === "zh"
        ? /5 \/ 6 个片段.*1 个证据不足/ : /5 \/ 6 episodes.*1 remains unknown/,
      "One unknown episode must not hide five recorded utility tests; testing still does not establish positive benefit");
      assert.equal(await analysis.locator(".analysis-episode").count(), 6);
      assert.equal(await analysis.locator("[data-analysis-judgment]").count(), 36, "All real judgments retain negative and unknown verdicts");
      for (const episode of saved.episodes) {
        const item = analysis.locator(`[data-analysis-episode="${episode.id}"]`);
        await item.locator(":scope > summary").click();
        for (const summary of await item.locator(".analysis-judgments > details > summary").all()) await summary.click();
        const text = await item.innerText();
        for (const assessment of saved.result.assessments.filter(row => row.episode_id === episode.id)) {
          assert(text.includes(assessment.reason), `${episode.id}/${assessment.metric}: complete original reasoning remains readable`);
        }
      }
      const missingEpisode = analysis.locator('[data-analysis-episode="native-feedback-1"]');
      await missingEpisode.locator(":scope > summary").click();
      for (const summary of await missingEpisode.locator(".analysis-judgments > details:not([open]) > summary").all()) await summary.click();
      await missingEpisode.getByRole("button", {name: /native-report-1/}).first().click();
      const unavailable = history.locator(".analysis-source");
      await unavailable.locator(".analysis-error").waitFor();
      assert.match(await unavailable.locator(".analysis-error").innerText(), /not captured/);
      assert.equal(await unavailable.locator("pre").count(), 0, "Uncaptured citations do not substitute unrelated evidence");
      await unavailable.getByRole("button", {name: language === "zh" ? "关闭原文" : "Close source", exact: true}).click();
      const citation = capture.analysis_citations[0].response;
      const cited = saved.result.assessments.find(row => row.citations.some(c => c.source_id === citation.source_id
        && c.first_line === citation.first_line && c.last_line === citation.last_line));
      assert(cited, "The portable excerpt is an actual judgment citation");
      const episode = analysis.locator(`[data-analysis-episode="${cited.episode_id}"]`);
      await episode.locator(":scope > summary").click();
      for (const summary of await episode.locator(".analysis-judgments > details:not([open]) > summary").all()) await summary.click();
      await episode.getByRole("button", {name: `${citation.source_id} · ${language === "zh" ? "行" : "lines"} ${citation.first_line}–${citation.last_line}`, exact: true}).first().click();
      const source = history.locator(".analysis-source");
      await source.locator("pre").waitFor();
      assert.equal(await source.locator("pre").innerText(), citation.text);
      await source.getByRole("button", {name: language === "zh" ? "关闭原文" : "Close source", exact: true}).click();
      const method = analysis.locator(".analysis-method");
      await method.locator(":scope > summary").click();
      assert.match(await method.innerText(), /447\.0s.*53,768.*19,278/);
      assert.match(await method.innerText(), /graph-official-native3600-integration-20261011-04/);
      assert.equal(await analysis.getByRole("button", {name: /用中文分析|Analyze selected scope/}).count(), 0, "A frozen readback never offers execution");
      for (const width of [1440, 390]) {
        await page.setViewportSize({width, height: 900});
        await analysis.scrollIntoViewIfNeeded();
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
        await page.screenshot({path: resolve(outputDir, `research-real-graph-analysis-${language}-${width}.png`), animations: "disabled"});
      }
      await page.setViewportSize({width: 1440, height: 900});
      await readOriginalNativeSnapshot(page, history, capture, language);
    }
    const readback = await (await page.request.get(`${owner.base}/fixture/readback`)).json();
    assert.equal(readback.source_unchanged, true);
    assert.equal(readback.mutation_attempts, 0);
    assert(readback.analysis_reads >= 4, "Both interfaces read their cache miss and the same saved original");
    if (collectCoverage) coverageEntries.push(...await page.coverage.stopJSCoverage());
    return coverageEntries;
  } finally {
    if (page) await page.close();
    await owner.close();
  }
}

// Core whole-page references: original history and independently scoped native outcomes.
// Synthetic scenarios remain separate boundary/race/overflow tests.
export const researchRealBenchmarkScenario = {
  id: "research-real-benchmark",
  async run({browser, collectCoverage}) {
    const coverageEntries = [];
    coverageEntries.push(...await readUnanalyzedNativeResults(browser, collectCoverage));
    const observations = [];
    for (const [arm, fixture] of [
      ["grid-on", "edgebench-grid-context-on-20261103-history.json"],
      ["grid-off", "edgebench-grid-context-off-20261103-history.json"],
      ["ad", "edgebench-ad-20261023-history.json"],
      ["schema", "edgebench-schema-20261025-history.json"],
    ]) {
      const path = resolve(repoRoot, "apps/presentation/dashboard/src/data/fixtures", fixture);
      const capture = JSON.parse(await readFile(path, "utf8"));
      const owner = await startHistoryOwner(path);
      let page;
      try {
        page = await browser.newPage({viewport: {width: 1440, height: 900}});
        if (collectCoverage) await page.coverage.startJSCoverage({resetOnNavigation: false});
        await page.addInitScript(() => localStorage.setItem("loopx-pw-locale", "en"));
        await page.goto(`${owner.base}/chat/?goalId=${capture.goal_id}&view=research`);
        const history = page.locator(".research-history");
        await page.waitForFunction(() => document.querySelector(".research-history")?.getAttribute("aria-busy") === "false");
        assert.match(await history.locator(".research-history-coverage").innerText(), new RegExp(`${capture.events.length} / ${capture.events.length}`));
        assert.equal(await history.locator(".research-history-edges > g > path").count(), 0, "No relation is inferred from matching questions or adjacent times");
        const absentDecisions = history.locator(".research-trail-empty");
        assert.equal(await absentDecisions.evaluate(element => element.open), false, "The real source's missing structured decisions use an on-demand disclosure");
        await absentDecisions.locator(":scope > summary").click();
        assert.match(await absentDecisions.innerText(), /No explicit decision recorded/);
        await absentDecisions.locator(":scope > summary").click();
        await page.screenshot({path: resolve(outputDir, `research-real-${arm}-desktop.png`), animations: "disabled"});

        await history.getByRole("button", {name: "Search research records", exact: true}).click();
        const records = history.locator(".research-record-search");
        const search = records.getByRole("searchbox", {name: "Search research records"});
        assert.equal(await records.locator(".research-record-entry").count(), Math.min(50, capture.events.length));
        while (await records.getByRole("button", {name: /Show more records/}).count()) {
          await records.getByRole("button", {name: /Show more records/}).click();
        }
        assert.equal(await records.locator(".research-record-entry").count(), capture.events.length, "Later real records stay reachable across display pages");
        const received = page.waitForEvent("download");
        await records.getByRole("button", {name: "Download matches (JSON)", exact: true}).click();
        const download = await received;
        const exported = JSON.parse(await readFile(await download.path(), "utf8"));
        assert.equal(exported.goal_id, capture.goal_id);
        assert.deepEqual(exported.coverage, {loaded_records: capture.events.length, exported_records: capture.events.length, history_complete: true, refresh_failed: false});
        assert.deepEqual(exported.records.map(event => event.event_id), capture.events.map(event => event.event_id), "The full append order survives export");
        for (const [index, original] of capture.events.entries()) {
          const actual = exported.records[index];
          for (const field of ["title", "summary", "status", "recorded_at", "result_id", "goal_id", "run_id"]) {
            assert.equal(actual[field], original[field] ?? "", `${arm}/${original.event_id}/${field} keeps the original content`);
          }
          assert.deepEqual(actual.measurements ?? [], original.measurements ?? []);
        }

        // A source-authored 'confirmed' record can report no gain. Never infer
        // positive benefit from that label or rewrite the original conclusion.
        const chosen = arm === "grid-on"
          ? capture.events.find(event => event.summary.includes("no measured gain from either tested family"))
          : arm === "ad" ? capture.events.find(event => event.summary.includes("Not adopted. This is a scoped negative result"))
          : capture.events.findLast(event => event.event_kind === "finding");
        assert(chosen);
        await search.fill(chosen.event_id);
        const entry = records.locator(".research-record-entry");
        assert.equal(await entry.count(), 1);
        await entry.locator(":scope > summary").click();
        for (const width of [1440, 390]) {
          await page.setViewportSize({width, height: 900});
          await entry.scrollIntoViewIfNeeded();
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
          assert.equal(await entry.locator(":scope > summary > strong").innerText(), chosen.title);
          await page.screenshot({path: resolve(outputDir, `research-real-${arm}-record-${width}.png`), animations: "disabled"});
        }
        if (arm === "grid-on") assert.match(await entry.locator(".research-finding-sections").first().innerText(), /no measured gain/);
        if (arm === "ad") assert.match(await entry.innerText(), /Not adopted/);
        const original = entry.locator(":scope > details").filter({has: page.locator("summary", {hasText: "Full original text"})});
        await original.locator(":scope > summary").click();
        assert.equal(await original.locator(":scope > p").first().innerText(), chosen.summary, "Long negative/unknown evidence is readable without abbreviation");
        await original.locator(":scope > summary").click();
        if (arm === "ad") {
          const first = capture.events[0];
          const versions = capture.events.filter(event => event.result_id === first.result_id);
          assert.equal(versions.length, 39, "The real repeated question has 39 original updates");
          await search.fill(first.event_id);
          await entry.locator(":scope > summary").click();
          const comparison = entry.locator(".research-record-comparison");
          await comparison.locator(":scope > summary").click();
          await comparison.getByRole("combobox", {name: "Compare with"}).selectOption(versions.at(-1).event_id);
          await comparison.getByRole("checkbox", {name: "Include unchanged fields"}).check();
          for (const width of [1440, 390]) {
            await page.setViewportSize({width, height: 900});
            await comparison.scrollIntoViewIfNeeded();
            await page.screenshot({path: resolve(outputDir, `research-real-ad-versions-${width}.png`), animations: "disabled"});
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, "Real long version labels fit the reader");
          }
          await comparison.getByRole("checkbox", {name: "Include unchanged fields"}).press("Escape");
          assert.equal(await comparison.evaluate(element => element.open), false);
          await entry.locator(":scope > summary").evaluate(element => new Promise(resolve => {
            requestAnimationFrame(() => resolve(element === document.activeElement));
          })).then(focused => assert.equal(focused, true));
        }
        await search.fill("");
        await history.getByRole("button", {name: "Refresh history", exact: true}).click();
        await page.waitForFunction(() => document.querySelector(".research-history")?.getAttribute("aria-busy") === "false");
        const readback = await (await page.request.get(`${owner.base}/fixture/readback`)).json();
        assert.equal(readback.source_unchanged, true);
        assert.equal(readback.mutation_attempts, 0, "Reading a real benchmark capture never executes a solver/model or writes source state");
        observations.push({arm, source_run_id: capture.provenance.source_run_id, records: capture.events.length, source_sha256: capture.source_sha256});
        if (collectCoverage) coverageEntries.push(...await page.coverage.stopJSCoverage());
      } finally {
        if (page) await page.close();
        await owner.close();
      }
    }
    assert.notEqual(observations[0].source_run_id, observations[1].source_run_id);
    coverageEntries.push(...await readSavedGraphAnalysis(browser, collectCoverage));
    return {coverageEntries, observations};
  },
};
