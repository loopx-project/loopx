import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {outputDir, repoRoot} from "./fixture.mjs";
import {startHistoryOwner} from "./research-history.mjs";

// Core whole-page reference: unmodified display state from two real terminal
// runs. Synthetic scenarios remain separate boundary/race/overflow tests.
export const researchRealBenchmarkScenario = {
  id: "research-real-benchmark",
  async run({browser, collectCoverage}) {
    const coverageEntries = [];
    const observations = [];
    for (const arm of ["on", "off"]) {
      const path = resolve(repoRoot, `apps/presentation/dashboard/src/data/fixtures/edgebench-grid-context-${arm}-20261103-history.json`);
      const capture = JSON.parse(await readFile(path, "utf8"));
      const owner = await startHistoryOwner(path);
      let page;
      try {
        page = await browser.newPage({viewport: {width: 1440, height: 900}});
        if (collectCoverage) await page.coverage.startJSCoverage({resetOnNavigation: false});
        await page.addInitScript(() => localStorage.setItem("loopx-pw-locale", "en"));
        await page.goto(`${owner.base}/chat/?goalId=benchmark-goal&view=research`);
        const history = page.locator(".research-history");
        await page.waitForFunction(() => document.querySelector(".research-history")?.getAttribute("aria-busy") === "false");
        assert.match(await history.locator(".research-history-coverage").innerText(), new RegExp(`${capture.events.length} / ${capture.events.length}`));
        assert.equal(await history.locator(".research-history-edges > g > path").count(), 0, "No relation is inferred from matching questions or adjacent times");
        const absentDecisions = history.locator(".research-trail-empty");
        assert.equal(await absentDecisions.evaluate(element => element.open), false, "The real source's missing structured decisions use an on-demand disclosure");
        await absentDecisions.locator(":scope > summary").click();
        assert.match(await absentDecisions.innerText(), /No explicit decision recorded/);
        await absentDecisions.locator(":scope > summary").click();
        await page.screenshot({path: resolve(outputDir, `research-real-grid-${arm}-desktop.png`), animations: "disabled"});

        await history.getByRole("button", {name: "Search research records", exact: true}).click();
        const records = history.locator(".research-record-search");
        const search = records.getByRole("searchbox", {name: "Search research records"});
        assert.equal(await records.locator(".research-record-entry").count(), capture.events.length);
        const received = page.waitForEvent("download");
        await records.getByRole("button", {name: "Download matches (JSON)", exact: true}).click();
        const download = await received;
        const exported = JSON.parse(await readFile(await download.path(), "utf8"));
        assert.equal(exported.goal_id, "benchmark-goal");
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
        const chosen = arm === "on"
          ? capture.events.find(event => event.summary.includes("no measured gain from either tested family"))
          : capture.events.at(-1);
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
          await page.screenshot({path: resolve(outputDir, `research-real-grid-${arm}-record-${width}.png`), animations: "disabled"});
        }
        if (arm === "on") assert.match(await entry.locator(".research-finding-sections").first().innerText(), /no measured gain/);
        const original = entry.locator(":scope > details").filter({has: page.locator("summary", {hasText: "Full original text"})});
        await original.locator(":scope > summary").click();
        assert.equal(await original.locator(":scope > p").first().innerText(), chosen.summary, "Long negative/unknown evidence is readable without abbreviation");
        await original.locator(":scope > summary").click();
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
    return {coverageEntries, observations};
  },
};
