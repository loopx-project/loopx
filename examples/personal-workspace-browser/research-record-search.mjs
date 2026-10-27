import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import {mkdtemp, readFile, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {resolve} from "node:path";
import {resolveTestPython} from "../../scripts/test-python.mjs";
import {outputDir, repoRoot} from "./fixture.mjs";
import {startHistoryOwner} from "./research-history.mjs";

async function readEmptyScope(page, results, message) {
  await results.getByRole('button',{name:'No matches in loaded records',exact:true}).focus();
  await page.getByRole('tooltip').getByText(message,{exact:true}).waitFor();
  await page.keyboard.press('Escape');
}

export const researchRecordSearchScenario = {
  id: "research-record-search",
  async run({browser, collectCoverage}) {
    const temporary = await mkdtemp(resolve(tmpdir(), "loopx-record-search-"));
    let owner, page;
    try {
      const fixture = resolve(temporary, "capture.json");
      const otherFixture = resolve(temporary, "other.json");
      execFileSync(resolveTestPython(), ["-c", `
import json, sys
from pathlib import Path
from loopx.capabilities.explore.result_log import build_explore_node_event, build_explore_finding_event
goal='synthetic-record-search'
events=[build_explore_node_event(goal_id=goal,node_id='question',title='Synthetic candidate comparison',recorded_at='2026-01-01T00:00:00Z')]
for index in range(75):
    events.append(build_explore_finding_event(goal_id=goal,node_id='question',finding_id=f'finding-{index}',
        title=f'Candidate {index} — '+('研究问题与完整原始结论 / original question and retained evidence. '*3)+'END OF ORIGINAL TITLE',summary='Original observation; complete costs remain unknown.',
        status='tentative',recorded_at='2026-01-01T01:00:00Z'))
measurement=dict(metric='Synthetic score', unit='points', higher_is_better=True, comparison_scope='synthetic:fixed', input_revision='input:v1', evaluation_ref='method:v1', candidate_ref='candidate:amber', evidence_ref='result:amber')
events.append(build_explore_finding_event(goal_id=goal,node_id='question',finding_id='retained-finding',
    title='Earlier negative observation',summary='质量下降 — original negative evidence for candidate amber.',
    measurements=[dict(measurement,value=None,evidence_ref='candidate:amber-v1')],status='refuted',evidence_refs=['candidate:amber-v1'],run_id='run:old',recorded_at='2026-01-02T00:00:00Z'))
events.append(build_explore_finding_event(goal_id=goal,node_id='question',finding_id='retained-finding',
    title='Later observation',summary='A later scope does not rewrite the earlier result.',
    measurements=[dict(measurement,value=0,evidence_ref='candidate:amber-v2')],status='confirmed',evidence_refs=['candidate:amber-v2'],run_id='run:new',recorded_at='2026-01-03T00:00:00Z'))
events.append(build_explore_finding_event(goal_id=goal,node_id='question',finding_id='retained-finding',
    title='Unmeasured follow-up',summary='A new question remains open; earlier evidence is retained.',
    status='tentative',evidence_refs=['candidate:amber-v3'],run_id='run:follow-up',recorded_at='2026-01-04T00:00:00Z'))
Path(sys.argv[1]).write_text(json.dumps({'goal_id':goal,'display_name':'Synthetic evidence search','events':events},ensure_ascii=False))
other='synthetic-other-research'
Path(sys.argv[2]).write_text(json.dumps({'goal_id':other,'display_name':'Other synthetic research','events':[
    build_explore_node_event(goal_id=other,node_id='other-question',title='Independent evidence scope',recorded_at='2026-01-01T00:00:00Z')]}))
`, fixture, otherFixture], {cwd: repoRoot});
      owner = await startHistoryOwner([fixture, otherFixture]);
      page = await browser.newPage({viewport: {width: 1440, height: 900}});
      if (collectCoverage) await page.coverage.startJSCoverage({resetOnNavigation: false});
      await page.addInitScript(() => {if (!localStorage.getItem("loopx-pw-locale")) localStorage.setItem("loopx-pw-locale", "en");});
      const url = `${owner.base}/chat/?goalId=synthetic-record-search&view=research&statusUrl=%2Fstatus.json`;
      await page.goto(url);
      const history = page.locator(".research-history");
      await page.waitForFunction(() => document.querySelector('.research-history')?.getAttribute('aria-busy') === 'false');
      await page.screenshot({path: resolve(outputDir, "research-record-first-1440.png"), animations: "disabled"});
      await history.getByRole("button", {name: "Search research records", exact: true}).click();
      const search = history.getByRole("searchbox", {name: "Search research records"});
      const results = history.locator(".research-record-search");
      const downloadButton = results.getByRole("button", {name: "Download matches (JSON)", exact: true});
      const download = async () => {
        const received = page.waitForEvent("download");
        await downloadButton.click();
        const file = await received;
        assert.match(file.suggestedFilename(), /^report-research-synthetic-[a-z-]+-readback\.json$/);
        return JSON.parse(await readFile(await file.path(), "utf8"));
      };
      await search.waitFor();
      assert(await search.evaluate(element => element === document.activeElement), "The search entry moves keyboard focus to the input");
      await page.waitForFunction(() => document.querySelector('.research-history')?.getAttribute('aria-busy') === 'false');
      assert.equal(await results.locator("li").count(), 50);
      // Long mixed-language records remain scannable; one keyboard action restores
      // the complete title and body without exposing source diagnostics.
      for (const width of [1440, 390]) {
        await page.setViewportSize({width, height: 900});
        await results.evaluate(element => element.scrollIntoView({block: "start"}));
        await page.screenshot({path: resolve(outputDir, `research-record-list-${width}.png`), animations: "disabled"});
      }
      const longRecord = results.locator(".research-history-records > li > details").nth(1);
      const longTitle = longRecord.locator(":scope > summary > strong");
      assert(await longTitle.evaluate(element => element.clientHeight < element.scrollHeight), "Collapsed long titles leave room for other records");
      const longSummary = longRecord.locator(":scope > summary");
      await longSummary.focus();
      await page.keyboard.press("Enter");
      assert.match(await longRecord.innerText(), /END OF ORIGINAL TITLE/);
      assert(await longTitle.evaluate(element => element.clientHeight >= element.scrollHeight), "The opened title is complete at 390px");
      assert.doesNotMatch(await longRecord.innerText(), /Record ID|Result ID/);
      await page.screenshot({path: resolve(outputDir, "research-record-long-390.png"), animations: "disabled"});
      await page.keyboard.press("Enter");
      assert(await longSummary.evaluate(element => element === document.activeElement), "Closing preserves the exact record entry");
      const scopeHelp = results.getByRole("button", {name: /[Ss]earch scope/});
      await scopeHelp.focus();
      await page.getByRole("tooltip").getByText("Search covers the complete loaded history.", {exact: true}).waitFor();
      await page.keyboard.press("Escape");
      assert(await scopeHelp.evaluate(element => element === document.activeElement));
      await scopeHelp.click();
      await page.getByRole("tooltip").getByText("Search covers the complete loaded history.", {exact: true}).waitFor();
      await page.keyboard.press("Escape");
      await page.setViewportSize({width: 1440, height: 900});
      const all = await download();
      assert.equal(all.goal_id, "synthetic-record-search");
      assert.equal(all.records.length, 79, "Download includes matches beyond both HTTP and display paging");
      assert.deepEqual(all.coverage, {loaded_records: 79, exported_records: 79, history_complete: true, refresh_failed: false});
      await search.fill("AMBER-V1 run:old");
      await results.getByRole("status", {name: /^1 matches in 79 loaded records/}).waitFor();
      assert.equal(await results.locator("li").count(), 1, "Search reaches beyond both the display limit and HTTP page boundary");
      await results.locator(".research-history-records > li > details > summary").click();
      assert.match(await results.locator("li").innerText(), /质量下降.*original negative evidence/);
      const sources = results.locator(".research-record-sources");
      assert.doesNotMatch(await results.locator("li").innerText(), /run:old|candidate:amber-v1/, "Reading the observation does not expose protocol metadata by default");
      await sources.locator(":scope > summary").focus();
      await page.keyboard.press("Enter");
      assert.match(await sources.innerText(), /run:old|candidate:amber-v1/);
      await page.keyboard.press("Enter");
      assert.doesNotMatch(await results.locator("li").innerText(), /amber-v2|Later observation/);
      const status = results.getByRole("combobox", {name: "Recorded status"});
      await status.selectOption("confirmed");
      await readEmptyScope(page, results, "No matching records. Try another term or clear the filters.");
      assert.equal(await downloadButton.isDisabled(), true);
      await status.selectOption("refuted");
      await results.getByRole("status", {name: /^1 matches in 79 loaded records/}).waitFor();
      await results.locator(".research-history-records > li > details > summary").click();
      const selected = await download();
      assert.deepEqual(selected.filters, {query: "AMBER-V1 run:old", status: "refuted"});
      assert.equal(selected.records.length, 1);
      assert.equal(selected.records[0].summary, "质量下降 — original negative evidence for candidate amber.");
      assert.deepEqual(selected.records[0].evidence_refs, ["candidate:amber-v1"]);
      assert.equal(selected.records[0].run_id, "run:old");
      // Compare the original negative record with a revision excluded by both filters.
      const comparison = results.locator(".research-record-comparison");
      const comparisonSummary = comparison.locator(":scope > summary");
      await comparisonSummary.focus();
      await page.keyboard.press("Enter");
      await comparison.getByRole("combobox", {name: "Compare with"}).waitFor();
      const comparisonHelp = comparison.getByRole("button", {name: "Comparison scope and record identities", exact: true});
      await comparisonHelp.focus();
      const identities = page.getByRole("tooltip");
      assert.match(await identities.innerText(), /Compares original records/);
      for (const record of all.records.filter(record => record.result_id === "retained-finding").slice(0, 2)) {
        assert.match(await identities.innerText(), new RegExp(record.event_id));
      }
      await page.keyboard.press("Escape");
      assert.match(await comparison.innerText(), /Later observation/);
      assert.match(await comparison.innerText(), /质量下降/);
      assert.match(await comparison.innerText(), /candidate:amber-v1/);
      assert.match(await comparison.innerText(), /candidate:amber-v2/);
      assert.deepEqual(await comparison.locator(".explore-measurement-reading strong").allTextContents(), ["Not measured", "0"]);
      const versionSelect = comparison.getByRole("combobox", {name: "Compare with"});
      await versionSelect.selectOption({index: 1});
      await comparison.getByText("Unmeasured follow-up", {exact: true}).waitFor();
      assert.match(await comparison.innerText(), /candidate:amber-v3/);
      assert.doesNotMatch(await comparison.innerText(), /candidate:amber-v2/);
      await versionSelect.selectOption({index: 0});
      await comparison.getByText("Later observation", {exact: true}).waitFor();
      const includeUnchanged = comparison.getByRole("checkbox", {name: "Include unchanged fields"});
      await includeUnchanged.check();
      await comparison.getByRole("heading", {name: "Node ID", exact: true}).waitFor();
      await includeUnchanged.uncheck();
      assert.equal(await comparison.getByRole("heading", {name: "Node ID", exact: true}).count(), 0);
      for (const width of [1440, 390]) {
        await page.setViewportSize({width, height: 900});
        await comparison.scrollIntoViewIfNeeded();
        assert(await comparison.evaluate(element => element.scrollWidth <= element.clientWidth + 1), "Comparison fits without horizontal overflow");
        await page.screenshot({path: resolve(outputDir, `research-record-comparison-${width}.png`), animations: "disabled"});
      }
      await comparisonSummary.click();
      await comparisonSummary.click();
      await comparison.getByRole("combobox", {name: "Compare with"}).waitFor();
      assert.match(await comparison.innerText(), /candidate:amber-v2/, "Repeated opening preserves the original pair");
      await comparisonSummary.click();
      // A browser download interruption is recoverable without rereading or mutation.
      await page.evaluate(() => {
        const original = URL.createObjectURL;
        URL.createObjectURL = (...args) => {URL.createObjectURL = original; throw new Error("Synthetic download interruption");};
      });
      await downloadButton.click();
      await results.getByRole("alert").getByText(/Could not start the download/).waitFor();
      await results.evaluate(element => element.scrollIntoView({block: "start"}));
      await page.screenshot({path: resolve(outputDir, "research-record-export-download-error.png")});
      assert.deepEqual((await download()).records, selected.records);
      assert.equal(await results.getByRole("alert").count(), 0);
      for (const width of [1440, 390]) {
        await page.setViewportSize({width, height: 900});
        await results.evaluate(element => element.scrollIntoView({block: "start"}));
        assert(await results.evaluate(element => element.scrollWidth <= element.clientWidth + 1), "Results fit the viewport");
        await page.screenshot({path: resolve(outputDir, `research-record-search-${width}.png`), animations: "disabled"});
      }
      // A failed refresh keeps the selected query and old evidence visibly stale.
      await page.route("**/api/chat/explore-results?*", route => route.fulfill({status: 503, json: {error: "Synthetic interruption"}}));
      await history.getByRole("button", {name: "Refresh history", exact: true}).click();
      await results.getByRole("status", {name: /^1 matches in 79 loaded records/}).getByText(/Refresh failed/).waitFor();
      assert.equal(await search.inputValue(), "AMBER-V1 run:old");
      assert.equal(await results.locator("li").count(), 1);
      const stale = await download();
      assert.equal(stale.coverage.refresh_failed, true);
      await comparisonSummary.click();
      await comparison.getByRole("combobox", {name: "Compare with"}).waitFor();
      assert.match(await comparison.innerText(), /candidate:amber-v2/);
      await comparisonSummary.click();
      assert.deepEqual(stale.records, selected.records);
      await results.evaluate(element => element.scrollIntoView({block: "start"}));
      await page.screenshot({path: resolve(outputDir, "research-record-export-stale.png")});
      await page.unroute("**/api/chat/explore-results?*");
      await history.getByRole("button", {name: "Refresh history", exact: true}).click();
      await results.getByRole("status", {name: /^1 matches in 79 loaded records/}).waitFor();
      assert.equal((await download()).coverage.refresh_failed, false);
      await results.getByRole("button", {name: "Clear filters"}).click();
      await results.getByRole("button", {name: /Show more records/}).click();
      assert.equal(await results.locator("li").count(), 79);
      await search.fill("no such evidence");
      await readEmptyScope(page, results, "No matching records. Try another term or clear the filters.");
      await results.getByRole("button", {name: "Clear filters"}).click();
      assert.equal(await results.locator("li").count(), 50, "A changed query resets display paging");
      // Fresh interrupted load searches only the explicitly partial snapshot.
      await page.route("**/api/chat/explore-results?*", route => new URL(route.request().url()).searchParams.has("cursor")
        ? route.fulfill({status: 503, json: {error: "Synthetic page interruption"}}) : route.continue());
      await page.reload();
      await history.getByRole("button", {name: "Search research records", exact: true}).click();
      await search.fill("amber-v1");
      await readEmptyScope(page, results, "No matches in the loaded records; the remaining history is not verified.");
      assert.equal(await downloadButton.isDisabled(), true);
      await results.getByRole("button", {name: "No matches in loaded records", exact: true}).click();
      await page.getByRole("tooltip").getByText("History is incomplete; remaining records have not been verified.", {exact: true}).waitFor();
      await page.keyboard.press("Escape");
      await results.getByRole("button", {name: "Clear filters"}).click();
      const partial = await download();
      assert.equal(partial.coverage.history_complete, false);
      assert.equal(partial.coverage.refresh_failed, true);
      assert.equal(partial.records.length, partial.coverage.loaded_records);
      assert(partial.records.length > 0 && partial.records.length < 79);
      await search.fill("amber-v1");
      await page.unroute("**/api/chat/explore-results?*");
      await history.getByRole("button", {name: "Refresh history", exact: true}).click();
      await results.getByRole("status", {name: /^1 matches in 79 loaded records/}).waitFor();
      await page.goto(url.replace("synthetic-record-search", "synthetic-other-research"));
      await history.getByRole("button", {name: "Search research records", exact: true}).click();
      await results.getByRole("status", {name: "1 matches in 1 loaded records", exact: true}).waitFor();
      assert.equal(await search.inputValue(), "", "Another Goal starts with its own search scope");
      assert.doesNotMatch(await results.innerText(), /amber|Earlier negative/);
      const other = await download();
      assert.equal(other.goal_id, "synthetic-other-research");
      assert.equal(other.records.length, 1);
      assert.equal(await comparison.count(), 0, "A single-version Goal offers no inferred comparison");
      assert(other.records.every(record => record.goal_id === other.goal_id));
      await search.fill("amber-v1");
      await readEmptyScope(page, results, "No matching records. Try another term or clear the filters.");
      await page.goto(url);
      await history.getByRole("button", {name: "Search research records", exact: true}).click();
      await search.fill("amber-v1");
      await results.getByRole("status", {name: /^1 matches in 79 loaded records/}).waitFor();
      // A pending refresh cannot export an intermediate mixture of pages.
      let release, observed;
      const hold = new Promise(resolve => {release = resolve;});
      const seen = new Promise(resolve => {observed = resolve;});
      await page.route("**/api/chat/explore-results?*", async route => {observed(); await hold; await route.continue();});
      await history.getByRole("button", {name: "Refresh history", exact: true}).click();
      await seen;
      await page.waitForFunction(() => [...document.querySelectorAll(".research-record-filters button")].some(button => button.textContent.includes("Download matches") && button.disabled));
      assert.equal(await downloadButton.isDisabled(), true);
      release();
      await page.waitForFunction(() => document.querySelector('.research-history')?.getAttribute('aria-busy') === 'false');
      await page.unroute("**/api/chat/explore-results?*");
      assert.equal(await downloadButton.isEnabled(), true);
      const readback = await (await page.request.get(`${owner.base}/fixture/readback`)).json();
      assert(readback.source_unchanged && readback.mutation_attempts === 0);
      await page.evaluate(() => localStorage.setItem("loopx-pw-locale", "zh-CN"));
      await page.reload();
      await history.getByRole("button", {name: "搜索研究记录", exact: true}).click();
      await history.getByRole("searchbox", {name: "搜索研究记录"}).fill("amber-v1");
      await results.locator("li > details > summary").click();
      await results.getByText(/比较记录版本/).click();
      await results.getByRole("combobox", {name: "比较对象"}).waitFor();
      assert.match(await comparison.innerText(), /未测量/);
      await results.evaluate(element => element.scrollIntoView({block: "start"}));
      await page.screenshot({path: resolve(outputDir, "research-record-chinese-390.png"), animations: "disabled"});
      await comparison.locator(":scope > summary").click();
      await sources.locator(":scope > summary").click();
      assert.match(await sources.innerText(), /run:old|candidate:amber-v1/);
      await results.evaluate(element => element.scrollIntoView({block: "start"}));
      await page.screenshot({path: resolve(outputDir, "research-record-sources-390.png"), animations: "disabled"});
      await results.getByRole("button", {name: "清除筛选"}).click();
      const chineseDownload = results.getByRole("button", {name: "下载匹配记录（JSON）", exact: true});
      const received = page.waitForEvent("download");
      await chineseDownload.focus();
      await page.keyboard.press("Enter");
      assert.equal(JSON.parse(await readFile(await (await received).path(), "utf8")).records.length, 79);
      return {coverageEntries: collectCoverage ? await page.coverage.stopJSCoverage() : [], note: "packaged evidence search/export through native paged history; full and filtered downloads, original revisions, coverage/staleness, original revision comparison, download/read recovery, Goal isolation and narrow viewport"};
    } catch (error) {
      if (page && !page.isClosed()) await page.screenshot({path: resolve(outputDir, "research-record-search-failed.png")});
      throw error;
    } finally {await page?.close(); await owner?.close(); await rm(temporary, {recursive: true, force: true});}
  },
};
