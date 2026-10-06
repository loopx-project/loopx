import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {outputDir, dashboardDir} from "./fixture.mjs";
import {openWorkspacePage} from "./scenario-context.mjs";

export const researchResultsScenario = {
  id: "research-results",
  async run({browser, collectCoverage, url}) {
    let fixture = JSON.parse(await readFile(resolve(dashboardDir, "src/data/fixtures/presentation-projection.example.json")));
    fixture.projection.goal_id = "product-release";
    fixture.projection.view.metrics.push({id: "precise-supply", label: "Reported supply", value: "123456789.1234567891 SYN",
      detail: "Source height unknown; not a net flow measurement.", tone: "warning"});
    let mode = "ready";
    let reads = 0;
    let indexes = 0;
    let projection = fixture.projection;
    let surface = {
      extension_id: projection.extension_id, extension_revision: projection.extension_revision,
      surface_id: projection.surface_id, surface_kind: projection.surface_kind,
      title: "Investment Research", view_schema: projection.view_schema, visibility: "public-safe",
      state: "review_due", goal_id: projection.goal_id, generated_at: projection.generated_at,
      review_due_at: projection.review_due_at, diagnostic: null,
      empty_state_title: "No validated research yet", empty_state_detail: "Publish a validated projection.",
      detail_ref: {extension_id: projection.extension_id, extension_revision: projection.extension_revision,
        surface_id: projection.surface_id, payload_sha256: projection.payload_sha256},
    };
    const nativeUrl = process.env.LOOPX_RESEARCH_NATIVE_URL;
    if (nativeUrl) {
      const collection = await (await fetch(`${nativeUrl}/extension-presentation-surfaces`)).json();
      surface = collection.presentation_surfaces.items.find(item => item.goal_id === "product-release");
      assert(surface, "An explicitly supplied isolated native fixture must publish product-release research");
      fixture = await (await fetch(`${nativeUrl}/extension-projection?${new URLSearchParams(surface.detail_ref)}`)).json();
      projection = fixture.projection;
    }
    const context = await openWorkspacePage(browser, url, {collectCoverage, apiOptions: {presentationApi: nativeUrl ?? true},
      beforeGoto: async (_, page) => {
        await page.route("**/extension-presentation-surfaces", route => {
          indexes++;
          if (nativeUrl && mode === "ready") return route.continue();
          if (mode === "malformed") return route.fulfill({json: {ok: true}});
          const items = mode === "disabled" ? [] : [{...surface, visibility: mode === "owner-only" ? "owner-only" : "public-safe"}];
          return route.fulfill({json: {ok: true, presentation_surfaces: {
            schema_version: "extension_presentation_surfaces_v0", count: items.length, ready_count: 0,
            review_due_count: items.length, empty_count: 0, invalid_count: 0, items,
          }}});
        });
        await page.route("**/extension-projection?**", route => {
          reads++;
          const query = new URL(route.request().url()).searchParams;
          assert.equal(query.get("payload_sha256"), projection.payload_sha256);
          assert.equal(query.get("extension_revision"), projection.extension_revision);
          if (nativeUrl && mode === "ready") return route.continue();
          if (mode === "stale") return route.fulfill({status: 409, json: {error: "revision changed"}});
          return route.fulfill({json: {...fixture, projection: {...projection,
            goal_id: mode === "wrong-goal" ? "research-monitor" : projection.goal_id}}});
        });
      },
    });
    const {page, api} = context;
    try {
      await page.locator(".personal-goal-link", {hasText: "Product Release"}).click();
      assert.equal(indexes, 0, "Research must remain lazy outside the results tab");
      await page.getByRole("navigation", {name: "Goal 视图"}).getByRole("button", {name: "成果", exact: true}).click();
      const results = page.getByRole("region", {name: "研究成果", exact: true});
      await results.getByText("123456789.1234567891 SYN", {exact: true}).waitFor();
      assert.match(await results.textContent(), /Insufficient Evidence/);
      assert.match(await results.textContent(), /Counterevidence/);
      assert.match(await results.textContent(), /Source height unknown/);
      assert.match(await results.textContent(), /2026-01-15/);
      assert.match(await results.textContent(), /review_due/);
      assert.equal(reads, 1);
      assert.equal(await results.getByTestId("research-detail-ref-hash").textContent(), projection.payload_sha256);
      await page.screenshot({path: resolve(outputDir, "research-results-desktop.png"), animations: "disabled"});
      await page.setViewportSize({width: 390, height: 844});
      await results.scrollIntoViewIfNeeded();
      await page.screenshot({path: resolve(outputDir, "research-results-mobile.png"), animations: "disabled"});
      assert(await results.evaluate(el => el.scrollWidth <= el.clientWidth + 1), "Research must fit a phone: " + JSON.stringify(await results.evaluate(el => [...el.querySelectorAll("*")].filter(node => node.scrollWidth > node.clientWidth + 1).slice(0, 6).map(node => ({tag: node.tagName, css: node.className, text: node.textContent.slice(0,80), width: node.clientWidth, scroll: node.scrollWidth})))));
      const refresh = results.getByRole("button", {name: "刷新", exact: true});
      await refresh.focus();
      mode = "wrong-goal";
      await page.keyboard.press("Enter");
      await results.getByText(/does not match the requested detail_ref/).waitFor();
      assert.equal(await results.getByText("123456789.1234567891 SYN", {exact: true}).count(), 0);
      mode = "stale";
      await refresh.click();
      await results.getByText(/HTTP 409.*revision changed/).waitFor();
      mode = "disabled";
      await refresh.click();
      await results.getByText("本 Goal 暂无可读取的公开研究成果。", {exact: true}).waitFor();
      const priorReads = reads;
      mode = "owner-only";
      await refresh.click();
      await results.getByText("本 Goal 暂无可读取的公开研究成果。", {exact: true}).waitFor();
      assert.equal(reads, priorReads, "Owner-only details must never be fetched");
      mode = "malformed";
      await refresh.click();
      await results.getByRole("alert").waitFor();
      assert.equal(await results.getByText("123456789.1234567891 SYN", {exact: true}).count(), 0);
      mode = "ready";
      await refresh.click();
      await results.getByText("123456789.1234567891 SYN", {exact: true}).waitFor();
      await page.setViewportSize({width: 1512, height: 982});
      await page.locator(".personal-goal-link", {hasText: "Research Monitor"}).click();
      await page.getByRole("navigation", {name: "Goal 视图"}).getByRole("button", {name: "成果", exact: true}).click();
      await results.getByText("本 Goal 暂无可读取的公开研究成果。", {exact: true}).waitFor();
      assert.equal(await results.getByText("123456789.1234567891 SYN", {exact: true}).count(), 0);
      assert.equal(api.turnRequests.length, 0, "Reading research must not invoke a model");
      await page.evaluate(() => localStorage.setItem("loopx-pw-locale", "en"));
      await page.reload({waitUntil: "networkidle"});
      await page.locator(".personal-goal-link", {hasText: "Product Release"}).click();
      await page.getByRole("button", {name: "Files", exact: true}).click();
      const english = page.getByRole("region", {name: "Research results", exact: true});
      await english.getByText("123456789.1234567891 SYN", {exact: true}).waitFor();
      assert.match(await english.textContent(), /counterevidence and open questions/);
      return {note: `Packaged results consume Goal-scoped public research, preserve uncertainty, and clear stale/disabled/wrong-Goal contents; ${nativeUrl ? "real isolated Core publication/HTTP, scripted Goal directory and corruption cases" : "scripted API fixture"}`,
        projectionSha256: projection.payload_sha256, coverageEntries: await context.close()};
    } finally {if (!page.isClosed()) await context.close();}
  },
};
