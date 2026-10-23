import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import {mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {resolve} from "node:path";
import {resolveTestPython} from "../../scripts/test-python.mjs";
import {outputDir, repoRoot} from "./fixture.mjs";
import {startHistoryOwner} from "./research-history.mjs";

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
      await page.addInitScript(() => localStorage.setItem("loopx-pw-locale", "en"));
      const url = `${owner.base}/chat/?goalId=synthetic-record-search&view=research&statusUrl=%2Fstatus.json`;
      await page.goto(url);
      const history = page.locator(".research-history");
      await history.getByRole("button", {name: "Search research records", exact: true}).click();
      const search = history.getByRole("searchbox", {name: "Search research records"});
      const results = history.locator(".research-record-search");
      await search.waitFor();
      assert(await search.evaluate(element => element === document.activeElement), "The search entry moves keyboard focus to the input");
      await page.waitForFunction(() => document.querySelector('.research-history')?.getAttribute('aria-busy') === 'false');
      assert.equal(await results.locator("li").count(), 50);
      await search.fill("AMBER-V1 run:old");
      await results.getByText("1 matches in 78 loaded records", {exact: true}).waitFor();
      assert.equal(await results.locator("li").count(), 1, "Search reaches beyond both the display limit and HTTP page boundary");
      await results.locator("li summary").click();
      assert.match(await results.locator("li").innerText(), /质量下降.*original negative evidence/);
      assert.match(await results.locator("li").innerText(), /candidate:amber-v1/);
      assert.doesNotMatch(await results.locator("li").innerText(), /amber-v2|Later observation/);
      const status = results.getByRole("combobox", {name: "Recorded status"});
      await status.selectOption("confirmed");
      await results.getByText("No matching records. Try another term or clear the filters.", {exact: true}).waitFor();
      await status.selectOption("refuted");
      await results.getByText("1 matches in 78 loaded records", {exact: true}).waitFor();
      await results.locator("li summary").click();
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
      await page.unroute("**/api/chat/explore-results?*");
      await history.getByRole("button", {name: "Refresh history", exact: true}).click();
      await results.getByText("1 matches in 78 loaded records", {exact: true}).waitFor();
      await results.getByRole("button", {name: "Clear filters"}).click();
      await results.getByRole("button", {name: /Show more records/}).click();
      assert.equal(await results.locator("li").count(), 78);
      await search.fill("no such evidence");
      await results.getByText(/No matching records/).waitFor();
      await results.getByRole("button", {name: "Clear filters"}).click();
      assert.equal(await results.locator("li").count(), 50, "A changed query resets display paging");
      // Fresh interrupted load searches only the explicitly partial snapshot.
      await page.route("**/api/chat/explore-results?*", route => new URL(route.request().url()).searchParams.has("cursor")
        ? route.fulfill({status: 503, json: {error: "Synthetic page interruption"}}) : route.continue());
      await page.reload();
      await history.getByRole("button", {name: "Search research records", exact: true}).click();
      await search.fill("amber-v1");
      await results.getByText("No matches in the loaded records; the remaining history is not verified.", {exact: true}).waitFor();
      await page.unroute("**/api/chat/explore-results?*");
      await history.getByRole("button", {name: "Refresh history", exact: true}).click();
      await results.getByText("1 matches in 78 loaded records", {exact: true}).waitFor();
      await page.goto(url.replace("synthetic-record-search", "synthetic-other-research"));
      await history.getByRole("button", {name: "Search research records", exact: true}).click();
      await results.getByText("1 matches in 1 loaded records", {exact: true}).waitFor();
      assert.equal(await search.inputValue(), "", "Another Goal starts with its own search scope");
      assert.doesNotMatch(await results.innerText(), /amber|Earlier negative/);
      await search.fill("amber-v1");
      await results.getByText(/No matching records/).waitFor();
      await page.goto(url);
      await history.getByRole("button", {name: "Search research records", exact: true}).click();
      await search.fill("amber-v1");
      await results.getByText("1 matches in 78 loaded records", {exact: true}).waitFor();
      const readback = await (await page.request.get(`${owner.base}/fixture/readback`)).json();
      assert(readback.source_unchanged && readback.mutation_attempts === 0);
      return {coverageEntries: collectCoverage ? await page.coverage.stopJSCoverage() : [], note: "packaged evidence search through native paged history; exact old revision, literal query, filters, interrupted reads/retry and narrow viewport"};
    } catch (error) {
      if (page && !page.isClosed()) await page.screenshot({path: resolve(outputDir, "research-record-search-failed.png")});
      throw error;
    } finally {await page?.close(); await owner?.close(); await rm(temporary, {recursive: true, force: true});}
  },
};
