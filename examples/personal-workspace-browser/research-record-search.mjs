import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import {mkdtemp, readFile, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {resolve} from "node:path";
import {resolveTestPython} from "../../scripts/test-python.mjs";
import {outputDir, packaged, repoRoot} from "./fixture.mjs";
import {startHistoryOwner} from "./research-history.mjs";
import {fetchWorkspaceBackend} from "./scenario-context.mjs";

async function readEmptyScope(page, results, message) {
  const search = results.getByRole('searchbox');
  const query = await search.inputValue();
  const trigger = results.getByRole('button',{name:'No matches in loaded records',exact:true});
  await trigger.focus();
  // Enter opens the help even when the trigger retained focus after an earlier
  // dismissal; focusing an already focused element does not fire onFocus.
  await trigger.press('Enter');
  await page.getByRole('tooltip').getByText(message,{exact:true}).waitFor();
  // Bind dismissal to the current trigger after rendering. A global Escape
  // can reach the search input and clear the filter instead of only the help.
  await trigger.press('Escape');
  await page.getByRole('tooltip').waitFor({state:'hidden'});
  assert.equal(await search.inputValue(), query, 'Dismissing scope help preserves the selected query');
}

export const researchRecordSearchScenario = {
  id: "research-record-search",
  requiresBackendReadback: true,
  async run({browser, collectCoverage, url}) {
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
negative=build_explore_finding_event(goal_id=other,node_id='other-question',finding_id='negative',title='Independent evidence scope',
    summary='The alternative missed the target.',status='refuted',evidence_refs=['synthetic:negative'],recorded_at='2026-01-01T00:00:00Z')
Path(sys.argv[2]).write_text(json.dumps({'goal_id':other,'display_name':'Other synthetic research','events':[
    build_explore_node_event(goal_id=other,node_id='other-question',title='Independent evidence scope',status='resolved',recorded_at='2026-01-01T00:00:00Z'),
    negative,
    build_explore_node_event(goal_id=other,node_id='separate-attempt',title='Independent evidence scope',recorded_at='2026-01-01T00:00:00Z'),
    build_explore_finding_event(goal_id=other,node_id='missing-anchor',finding_id='unlinked',title='Independent evidence scope',
        summary='This finding has an absent node.',status='tentative',recorded_at='2026-01-01T00:00:00Z'),
    negative]}))
`, fixture, otherFixture], {cwd: repoRoot});
      owner = await startHistoryOwner([fixture, otherFixture]);
      page = await browser.newPage({viewport: {width: 1440, height: 900}});
      if (collectCoverage) await page.coverage.startJSCoverage({resetOnNavigation: false});
      await page.addInitScript(() => {if (!localStorage.getItem("loopx-pw-locale")) localStorage.setItem("loopx-pw-locale", "en");});
      const entry = packaged ? new URL("/chat/", owner.base) : new URL(url);
      entry.searchParams.set("goalId", "synthetic-record-search");
      entry.searchParams.set("view", "research");
      entry.searchParams.set("statusUrl", "/status.json");
      if (!packaged) {
        // Keep Vite modules and their source maps while using the same history owner.
        await page.route(target => target.origin === entry.origin && (target.pathname === "/status.json"
          || target.pathname.startsWith("/api/") || target.pathname.startsWith("/extension-")), async route => {
          await route.fulfill({response: await fetchWorkspaceBackend(route, owner.base)});
        });
      }
      await page.goto(entry.href);
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
      const report = async () => {
        await results.getByRole("combobox", {name: "Download format"}).selectOption("md");
        const received = page.waitForEvent("download");
        await results.getByRole("button", {name: "Download reading report", exact: true}).click();
        const file = await received;
        assert.match(file.suggestedFilename(), /-readback\.md$/);
        const text = await readFile(await file.path(), "utf8");
        await results.getByRole("combobox", {name: "Download format"}).selectOption("json");
        return text;
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
      const fullReport = await report();
      assert.equal((fullReport.match(/^## \d+\./gm) ?? []).length, 79);
      assert.match(fullReport, /\*\*Value:\*\* Unknown/);
      assert.match(fullReport, /\*\*Value:\*\* 0/);
      assert.match(fullReport, /candidate:amber-v3/);
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
      const selectedReport = await report();
      assert.match(selectedReport, /质量下降 — original negative evidence/);
      assert.match(selectedReport, /\*\*Comparison scope:\*\* synthetic:fixed/);
      assert.doesNotMatch(selectedReport, /candidate:amber-v2|Later observation/);
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
      await results.getByRole("combobox", {name: "Download format"}).selectOption("md");
      await page.evaluate(() => {
        const original = URL.createObjectURL;
        URL.createObjectURL = () => {URL.createObjectURL = original; throw new Error("Synthetic report interruption");};
      });
      await results.getByRole("button", {name: "Download reading report", exact: true}).click();
      await results.getByRole("alert").waitFor();
      assert.match(await report(), /Earlier negative observation/);
      assert.equal(await results.getByRole("alert").count(), 0);
      await results.getByRole("combobox", {name: "Download format"}).selectOption("md");
      for (const width of [1440, 390]) {
        await page.setViewportSize({width, height: 900});
        await results.evaluate(element => element.scrollIntoView({block: "start"}));
        assert(await results.evaluate(element => element.scrollWidth <= element.clientWidth + 1), "Results fit the viewport");
        await page.screenshot({path: resolve(outputDir, `research-record-search-${width}.png`), animations: "disabled"});
      }
      // A failed refresh keeps the selected query and old evidence visibly stale.
      await results.getByRole("combobox", {name: "Download format"}).selectOption("json");
      await page.route("**/api/chat/explore-results?*", route => route.fulfill({status: 503, json: {error: "Synthetic interruption"}}));
      await history.getByRole("button", {name: "Refresh history", exact: true}).click();
      await results.getByRole("status", {name: /^1 matches in 79 loaded records/}).getByText(/Refresh failed/).waitFor();
      assert.equal(await search.inputValue(), "AMBER-V1 run:old");
      assert.equal(await results.locator("li").count(), 1);
      const stale = await download();
      assert.equal(stale.coverage.refresh_failed, true);
      assert.equal(await history.locator(".research-phase").count(), 3, "A failed refresh retains the last complete time range");
      assert.match(await report(), /Failed; previously read records retained/);
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
      await results.getByRole("button", {name: /Show more entries/}).click();
      assert.equal(await results.locator("li").count(), 79);
      await search.fill("no such evidence");
      await readEmptyScope(page, results, "No matching records. Try another term or clear the filters.");
      await results.getByRole("button", {name: "Clear filters"}).click();
      assert.equal(await results.locator("li").count(), 50, "A changed query resets display paging");
      // Fresh interrupted load searches only the explicitly partial snapshot.
      await page.route("**/api/chat/explore-results?*", route => new URL(route.request().url()).searchParams.has("cursor")
        ? route.fulfill({status: 503, json: {error: "Synthetic page interruption"}}) : route.fallback());
      const periodBookmark = new URL(page.url());
      periodBookmark.searchParams.set("research_goal", "synthetic-record-search");
      periodBookmark.searchParams.set("research_phase", "2");
      await page.goto(periodBookmark.href);
      const readError = history.locator(".research-history-read-error");
      const coverage = history.locator(".research-history-coverage");
      await readError.waitFor();
      await history.locator(".research-axis").waitFor();
      for (const width of [1440, 390]) {
        await page.setViewportSize({width, height: 900});
        await page.screenshot({path: resolve(outputDir, `research-history-partial-${width}.png`), animations: "disabled"});
      }
      assert.match(await coverage.innerText(), /Loaded records \(local time\).*40 \/ 79/);
      assert.match(await readError.locator(":scope > div > strong").innerText(), /Only part of the history loaded/);
      assert.equal(await history.locator(".research-phase, .research-phase-divider").count(), 0,
        "Incomplete pages cannot define the full history's periods");
      assert.equal(new URL(page.url()).searchParams.get("research_phase"), "2", "An incomplete read keeps the requested period");
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
      assert.match(await report(), /Incomplete; unloaded records are not included/);
      assert.equal(partial.records.length, partial.coverage.loaded_records);
      assert(partial.records.length > 0 && partial.records.length < 79);
      await search.fill("amber-v1");
      await page.unroute("**/api/chat/explore-results?*");
      await readError.getByRole("button", {name: "Retry", exact: true}).press("Enter");
      await results.getByRole("status", {name: /^1 matches in 79 loaded records/}).waitFor();
      assert(await coverage.evaluate(element => element === document.activeElement), "Keyboard retry leaves focus on the updated coverage");
      assert.match(await coverage.innerText(), /Recorded time \(local\).*79 \/ 79/);
      const phases = history.locator(".research-phase");
      assert.equal(await phases.count(), 3);
      assert.equal(await phases.nth(2).getAttribute("aria-pressed"), "true", "Recovery applies the original period bookmark");
      const otherGoal = new URL(entry);
      otherGoal.searchParams.set("goalId", "synthetic-other-research");
      await page.goto(otherGoal.href);
      const openOtherSearch = history.getByRole("button", {name: "Search research records", exact: true});
      await openOtherSearch.evaluate(button => {
        // Hold the frame from this one search activation until the reader moves focus.
        button.addEventListener("click", () => {
          const schedule = window.requestAnimationFrame;
          const frames = [];
          window.__loopxHeldSearchFrames = frames;
          window.requestAnimationFrame = callback => {
            frames.push(callback);
            return -frames.length;
          };
          setTimeout(() => {window.requestAnimationFrame = schedule;}, 0);
        }, {once: true});
      });
      await openOtherSearch.click();
      await results.getByRole("status", {name: "4 matches in 4 loaded records · 3 entries", exact: true}).waitFor();
      assert.equal(await search.inputValue(), "", "Another Goal starts with its own search scope");
      assert.doesNotMatch(await results.innerText(), /amber|Earlier negative/);
      const grouped = results.locator(".research-record-entry").first();
      const groupSummary = grouped.locator(":scope > summary");
      assert.match(await groupSummary.innerText(), /Refuted.*Resolved.*2 records/);
      assert.equal(await results.locator(".research-record-entry").count(), 3, "Identical titles and times leave independent attempts and absent links separate");
      await groupSummary.focus();
      await page.evaluate(() => {
        const frames = window.__loopxHeldSearchFrames ?? [];
        delete window.__loopxHeldSearchFrames;
        frames.forEach(callback => callback(performance.now()));
      });
      assert(await groupSummary.evaluate(element => element === document.activeElement),
        "Delayed search autofocus leaves the already focused record in control");
      await page.keyboard.press("Enter");
      const groupHistory = grouped.locator(".research-record-group-history");
      await groupHistory.waitFor();
      assert.match(await grouped.innerText(), /The alternative missed the target\./);
      assert.equal(await groupHistory.locator(":scope > li").count(), 2);
      await grouped.locator(".research-record-sources > summary").last().click();
      await grouped.getByText("synthetic:negative", {exact: true}).waitFor();
      await groupSummary.press("Escape");
      assert(await groupSummary.evaluate(element => element === document.activeElement && !element.parentElement.open));
      const other = await download();
      assert.equal(other.goal_id, "synthetic-other-research");
      assert.deepEqual(other.coverage, {loaded_records: 4, exported_records: 4, history_complete: true, refresh_failed: false});
      assert.equal(other.records.length, 4);
      assert.match(await history.locator(".research-history-coverage").innerText(), /5 \/ 5 source rows/);
      await page.screenshot({path: resolve(outputDir, "research-history-persisted-replay.png"), animations: "disabled"});
      assert.equal(await comparison.count(), 0, "A single-version Goal offers no inferred comparison");
      assert(other.records.every(record => record.goal_id === other.goal_id));
      await search.fill("amber-v1");
      await readEmptyScope(page, results, "No matching records. Try another term or clear the filters.");
      await page.goto(entry.href);
      await history.getByRole("button", {name: "Search research records", exact: true}).click();
      await search.fill("amber-v1");
      await results.getByRole("status", {name: /^1 matches in 79 loaded records/}).waitFor();
      let firstRecord, pageMode = "replay";
      await page.route("**/api/chat/explore-results?*", async route => {
        const response = await fetchWorkspaceBackend(route, owner.base);
        const historyPage = await response.json();
        if (new URL(route.request().url()).searchParams.has("cursor")) {
          if (pageMode === "short") historyPage.items.pop();
          else historyPage.items.unshift(pageMode === "conflict" ? {...firstRecord, summary: "Conflicting replay"} : firstRecord);
        } else [firstRecord] = historyPage.items;
        await route.fulfill({response, json: historyPage});
      });
      await history.getByRole("button", {name: "Refresh history", exact: true}).click();
      await page.waitForFunction(() => document.querySelector(".research-history")?.getAttribute("aria-busy") === "false");
      const replayed = await download();
      assert.equal(replayed.coverage.history_complete, true);
      assert.equal(replayed.coverage.refresh_failed, false);
      assert.deepEqual(replayed.records, selected.records, "Overlapping pages keep one copy per exact event ID");
      for (const invalid of ["conflict", "short"]) {
        pageMode = invalid;
        const tail = page.waitForResponse(response => response.url().includes("/api/chat/explore-results?")
          && new URL(response.url()).searchParams.has("cursor"));
        await history.getByRole("button", {name: "Refresh history", exact: true}).click();
        await tail;
        await results.getByRole("status", {name: /1 matches in 79 loaded records.*Refresh failed/}).waitFor();
        const stale = await download();
        assert.deepEqual(stale.records, selected.records, "Invalid pages preserve the previous complete snapshot");
        assert.equal(stale.coverage.history_complete, true, invalid);
        assert.equal(stale.coverage.refresh_failed, true, invalid);
      }
      await page.unroute("**/api/chat/explore-results?*");
      // A pending refresh cannot export an intermediate mixture of pages.
      let release, observed;
      const hold = new Promise(resolve => {release = resolve;});
      const seen = new Promise(resolve => {observed = resolve;});
      await page.route("**/api/chat/explore-results?*", async route => {observed(); await hold; await route.fallback();});
      await history.getByRole("button", {name: "Refresh history", exact: true}).click();
      await seen;
      await page.waitForFunction(() => [...document.querySelectorAll(".research-record-filters button")].some(button => button.textContent.includes("Download matches") && button.disabled));
      assert.equal(await downloadButton.isDisabled(), true);
      release();
      await page.waitForFunction(() => document.querySelector('.research-history')?.getAttribute('aria-busy') === 'false');
      await page.unroute("**/api/chat/explore-results?*");
      assert.equal(await downloadButton.isEnabled(), true);
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
      await results.getByRole("combobox", {name: "下载格式"}).selectOption("md");
      const chineseReport = page.waitForEvent("download");
      await results.getByRole("button", {name: "下载阅读报告", exact: true}).focus();
      await page.keyboard.press("Enter");
      const reportText = await readFile(await (await chineseReport).path(), "utf8");
      assert.match(reportText, /^# 研究记录报告/);
      assert.match(reportText, /\*\*数值:\*\* 未知/);
      assert.match(reportText, /\*\*数值:\*\* 0/);
      assert.match(reportText, /质量下降 — original negative evidence/);
      const readback = await (await page.request.get(`${owner.base}/fixture/readback`)).json();
      assert.ok(readback.ok && readback.history_reads >= 2, "Search uses the real paged history owner");
      assert.ok(readback.source_unchanged && readback.mutation_attempts === 0, "Search leaves the source untouched");
      const coverageEntries = collectCoverage ? await page.coverage.stopJSCoverage() : [];
      if (collectCoverage) {
        assert.ok(coverageEntries.some(entry => new URL(entry.url).pathname === "/src/features/personal-workspace/research-record-browser.tsx"
          && entry.source && entry.functions.some(fn => fn.ranges.some(range => range.count > 0))),
        "Research-record search must execute instrumented Dashboard source in the coverage lane");
      }
      return {coverageEntries, backendReadbackVerified: true, note: "evidence search/export through native paged history; full and filtered downloads, original revisions, coverage/staleness, original revision comparison, download/read recovery, Goal isolation and narrow viewport"};
    } catch (error) {
      if (page && !page.isClosed()) await page.screenshot({path: resolve(outputDir, "research-record-search-failed.png")});
      throw error;
    } finally {
      await page?.unrouteAll({behavior: "ignoreErrors"}).catch(() => {});
      await page?.close();
      await owner?.close();
      await rm(temporary, {recursive: true, force: true});
    }
  },
};
