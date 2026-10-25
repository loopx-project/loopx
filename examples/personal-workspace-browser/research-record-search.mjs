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
        title=f'Candidate {index}',summary='Original observation; complete costs remain unknown.',
        status='tentative',recorded_at='2026-01-01T01:00:00Z'))
events.append(build_explore_finding_event(goal_id=goal,node_id='question',finding_id='retained-finding',
    title='Earlier negative observation',summary='质量下降 — original negative evidence for candidate amber.',
    status='refuted',evidence_refs=['candidate:amber-v1'],run_id='run:old',recorded_at='2026-01-02T00:00:00Z'))
events.append(build_explore_finding_event(goal_id=goal,node_id='question',finding_id='retained-finding',
    title='Later observation',summary='A later scope does not rewrite the earlier result.',
    status='confirmed',evidence_refs=['candidate:amber-v2'],run_id='run:new',recorded_at='2026-01-03T00:00:00Z'))
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
      const all = await download();
      assert.equal(all.goal_id, "synthetic-record-search");
      assert.equal(all.records.length, 78, "Download includes matches beyond both HTTP and display paging");
      assert.deepEqual(all.coverage, {loaded_records: 78, exported_records: 78, history_complete: true, refresh_failed: false});
      await search.fill("AMBER-V1 run:old");
      await results.getByText("1 matches in 78 loaded records", {exact: true}).waitFor();
      assert.equal(await results.locator("li").count(), 1, "Search reaches beyond both the display limit and HTTP page boundary");
      await results.locator("li summary").click();
      assert.match(await results.locator("li").innerText(), /质量下降.*original negative evidence/);
      assert.match(await results.locator("li").innerText(), /candidate:amber-v1/);
      assert.doesNotMatch(await results.locator("li").innerText(), /amber-v2|Later observation/);
      const status = results.getByRole("combobox", {name: "Recorded status"});
      await status.selectOption("confirmed");
      await readEmptyScope(page, results, "No matching records. Try another term or clear the filters.");
      assert.equal(await downloadButton.isDisabled(), true);
      await status.selectOption("refuted");
      await results.getByText("1 matches in 78 loaded records", {exact: true}).waitFor();
      await results.locator("li summary").click();
      const selected = await download();
      assert.deepEqual(selected.filters, {query: "AMBER-V1 run:old", status: "refuted"});
      assert.equal(selected.records.length, 1);
      assert.equal(selected.records[0].summary, "质量下降 — original negative evidence for candidate amber.");
      assert.deepEqual(selected.records[0].evidence_refs, ["candidate:amber-v1"]);
      assert.equal(selected.records[0].run_id, "run:old");
      // A browser download interruption is recoverable without rereading or mutation.
      await page.evaluate(() => {
        const original = URL.createObjectURL;
        URL.createObjectURL = (...args) => {URL.createObjectURL = original; throw new Error("Synthetic download interruption");};
      });
      await downloadButton.click();
      await results.getByRole("alert").getByText(/Could not start the download/).waitFor();
      await results.scrollIntoViewIfNeeded();
      await page.screenshot({path: resolve(outputDir, "research-record-export-download-error.png")});
      assert.deepEqual((await download()).records, selected.records);
      assert.equal(await results.getByRole("alert").count(), 0);
      for (const width of [1440, 390]) {
        await page.setViewportSize({width, height: 900});
        await results.scrollIntoViewIfNeeded();
        assert(await results.evaluate(element => element.scrollWidth <= element.clientWidth + 1), "Results fit the viewport");
        await page.screenshot({path: resolve(outputDir, `research-record-search-${width}.png`), animations: "disabled"});
      }
      // A failed refresh keeps the selected query and old evidence visibly stale.
      await page.route("**/api/chat/explore-results?*", route => route.fulfill({status: 503, json: {error: "Synthetic interruption"}}));
      await history.getByRole("button", {name: "Refresh history", exact: true}).click();
      await results.getByText(/Refresh failed; showing previously read records/).waitFor();
      assert.equal(await search.inputValue(), "AMBER-V1 run:old");
      assert.equal(await results.locator("li").count(), 1);
      const stale = await download();
      assert.equal(stale.coverage.refresh_failed, true);
      assert.deepEqual(stale.records, selected.records);
      await results.scrollIntoViewIfNeeded();
      await page.screenshot({path: resolve(outputDir, "research-record-export-stale.png")});
      await page.unroute("**/api/chat/explore-results?*");
      await history.getByRole("button", {name: "Refresh history", exact: true}).click();
      await results.getByText("1 matches in 78 loaded records", {exact: true}).waitFor();
      assert.equal((await download()).coverage.refresh_failed, false);
      await results.getByRole("button", {name: "Clear filters"}).click();
      await results.getByRole("button", {name: /Show more records/}).click();
      assert.equal(await results.locator("li").count(), 78);
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
      await results.getByRole("button", {name: "Clear filters"}).click();
      const partial = await download();
      assert.equal(partial.coverage.history_complete, false);
      assert.equal(partial.coverage.refresh_failed, true);
      assert.equal(partial.records.length, partial.coverage.loaded_records);
      assert(partial.records.length > 0 && partial.records.length < 78);
      await search.fill("amber-v1");
      await page.unroute("**/api/chat/explore-results?*");
      await history.getByRole("button", {name: "Refresh history", exact: true}).click();
      await results.getByText("1 matches in 78 loaded records", {exact: true}).waitFor();
      await page.goto(url.replace("synthetic-record-search", "synthetic-other-research"));
      await history.getByRole("button", {name: "Search research records", exact: true}).click();
      await results.getByText("1 matches in 1 loaded records", {exact: true}).waitFor();
      assert.equal(await search.inputValue(), "", "Another Goal starts with its own search scope");
      assert.doesNotMatch(await results.innerText(), /amber|Earlier negative/);
      const other = await download();
      assert.equal(other.goal_id, "synthetic-other-research");
      assert.equal(other.records.length, 1);
      assert(other.records.every(record => record.goal_id === other.goal_id));
      await search.fill("amber-v1");
      await readEmptyScope(page, results, "No matching records. Try another term or clear the filters.");
      await page.goto(url);
      await history.getByRole("button", {name: "Search research records", exact: true}).click();
      await search.fill("amber-v1");
      await results.getByText("1 matches in 78 loaded records", {exact: true}).waitFor();
      // A pending refresh cannot export an intermediate mixture of pages.
      let release, observed;
      const hold = new Promise(resolve => {release = resolve;});
      const seen = new Promise(resolve => {observed = resolve;});
      await page.route("**/api/chat/explore-results?*", async route => {observed(); await hold; await route.continue();});
      await history.getByRole("button", {name: "Refresh history", exact: true}).click();
      await seen;
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
      const chineseDownload = results.getByRole("button", {name: "下载匹配记录（JSON）", exact: true});
      const received = page.waitForEvent("download");
      await chineseDownload.focus();
      await page.keyboard.press("Enter");
      assert.equal(JSON.parse(await readFile(await (await received).path(), "utf8")).records.length, 78);
      return {coverageEntries: collectCoverage ? await page.coverage.stopJSCoverage() : [], note: "packaged evidence search/export through native paged history; full and filtered downloads, original revisions, coverage/staleness, download/read recovery, Goal isolation and narrow viewport"};
    } catch (error) {
      if (page && !page.isClosed()) await page.screenshot({path: resolve(outputDir, "research-record-search-failed.png")});
      throw error;
    } finally {await page?.close(); await owner?.close(); await rm(temporary, {recursive: true, force: true});}
  },
};
