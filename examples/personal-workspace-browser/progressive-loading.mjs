import { openWorkspacePage } from "./scenario-context.mjs";
import {resolve} from "node:path";
import {outputDir} from "./fixture.mjs";

async function verifyLarkLoading(browser, url, collectCoverage) {
  let releaseCatalog;
  const catalog = new Promise(resolve => {releaseCatalog = resolve;});
  let releaseSlow;
  const slow = new Promise(resolve => {releaseSlow = resolve;});
  let revision = 1, catalogs = 0;
  const probes = [];
  const context = await openWorkspacePage(browser, url, {collectCoverage, beforeGoto: async (_api, page) => {
    await page.route("**/api/chat/lark/apps?*", async route => {
      const query = new URL(route.request().url()).searchParams;
      const current = revision;
      const refs = current === 3 ? [] : ["fast", "slow", "failed"];
      if (query.get("verify") === "false") {
        catalogs++;
        if (current === 1) await catalog;
        if (current === 4) {
          await route.fulfill({status: 502, json: {ok: false, error: "Synthetic catalog outage", error_code: "lark_configuration_unavailable"}}); return;
        }
      }
      else {
        probes.push(query.get("app_ref"));
        if (query.get("app_ref") === "slow" && current === 1) await slow;
        if (query.get("app_ref") === "failed" && current === 1) {
          await route.fulfill({status: 503, json: {ok: false, error: "Synthetic verification outage"}}); return;
        }
      }
      const verifying = query.get("verify") === "false";
      await route.fulfill({json: {ok: true, apps: refs.filter(ref => verifying || ref === query.get("app_ref")).map(ref => ({
        app_ref: ref, active: true, brand: "feishu", label: `${ref} ${current}`,
        ready: !verifying, reply_ready: !verifying,
        health_error_code: verifying ? "lark_app_unverified" : null,
      }))}});
    });
  }});
  const {page} = context;
  async function openSettings() {
    const menu = page.locator(".personal-mobile-menu");
    if (await menu.isVisible()) await menu.click();
    await page.getByRole("button", {name: "设置", exact: true}).click();
    await page.locator(".personal-settings-tabs").getByRole("button", {name: "Lark", exact: true}).click();
    await page.locator(".personal-lark-tabs").getByRole("button", {name: /^Lark Apps/u}).click();
  }
  try {
    await openSettings();
    const appTab = page.locator(".personal-lark-tabs").getByRole("button", {name: /^Lark Apps/u});
    // Hold the actual catalog response: an unknown count must not masquerade
    // as an empty directory, regardless of the component's state variable names.
    await appTab.getByText("…", {exact: true}).waitFor();
    await page.screenshot({path: resolve(outputDir, "lark-catalog-pending-desktop.png"), animations: "disabled"});
    if (await appTab.getByText("0", {exact: true}).count() || await page.locator(".personal-lark-empty").count()) throw new Error("Pending catalog displayed a false empty directory");
    if ((await page.locator(".personal-lark-app-toolbar > span").innerText()) !== "… 个可复用 App") throw new Error("Pending catalog toolbar displayed a false App count");
    await page.setViewportSize({width: 390, height: 844});
    if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error("Pending Lark catalog overflows at 390px");
    await page.screenshot({path: resolve(outputDir, "lark-catalog-pending-narrow.png"), animations: "disabled"});
    await page.setViewportSize({width: 1512, height: 982});
    releaseCatalog();
    await appTab.getByText("3", {exact: true}).waitFor();
    const fast = page.locator(".personal-lark-app-card").filter({hasText: "fast 1"});
    await fast.getByText("自动回复可用", {exact: true}).waitFor();
    await page.locator(".personal-lark-app-card").filter({hasText: "slow 1"}).getByText("正在核验…", {exact: true}).waitFor();
    await page.locator(".personal-lark-app-card").filter({hasText: "failed 1"}).getByText("尚未核验", {exact: true}).waitFor();
    if (catalogs !== 1 || probes.length !== 3) throw new Error("Settings panels duplicated the profile catalog or verification");
    await page.screenshot({path: resolve(outputDir, "lark-progressive-desktop.png"), animations: "disabled"});
    // Explicit refresh aborts the old generation; a late healthy result cannot
    // restore an old profile after the configuration changed.
    revision = 2;
    await page.getByRole("button", {name: "刷新配置并重试核验", exact: true}).click();
    await page.locator(".personal-lark-app-card").filter({hasText: "slow 2"}).getByText("自动回复可用", {exact: true}).waitFor();
    releaseSlow();
    await page.waitForTimeout(200);
    if (catalogs !== 2 || probes.length !== 6 || await page.getByText("slow 1", {exact: true}).count()) throw new Error("Late verification escaped its settings generation");
    // A confirmed empty catalog is different from loading. It clears the old
    // cards, schedules no health probes and can recover on the same page.
    revision = 3;
    await page.getByRole("button", {name: "刷新配置并重试核验", exact: true}).click();
    await appTab.getByText("0", {exact: true}).waitFor();
    await page.locator(".personal-lark-empty").waitFor();
    if (await page.locator(".personal-lark-app-card").count() || catalogs !== 3 || probes.length !== 6) throw new Error("Empty catalog retained stale profiles or started verification");
    // A failed read is unknown, never a confirmed zero; its visible error and
    // retry stay available without retaining the previous directory's cards.
    revision = 4;
    await page.getByRole("button", {name: "刷新配置并重试核验", exact: true}).click();
    await page.locator(".personal-notification-error").waitFor();
    await page.screenshot({path: resolve(outputDir, "lark-catalog-failed.png"), animations: "disabled"});
    if (await appTab.getByText("…", {exact: true}).count() !== 1 || (await page.locator(".personal-lark-app-toolbar > span").innerText()) !== "… 个可复用 App" || await page.locator(".personal-lark-empty, .personal-lark-app-card").count() || catalogs !== 4 || probes.length !== 6) throw new Error("Failed catalog masqueraded as an empty or stale directory");
    revision = 5;
    await page.getByRole("button", {name: "刷新配置并重试核验", exact: true}).click();
    await page.locator(".personal-lark-app-card").filter({hasText: "slow 5"}).getByText("自动回复可用", {exact: true}).waitFor();
    if (catalogs !== 5 || probes.length !== 9 || await page.locator(".personal-lark-empty, .personal-notification-error").count()) throw new Error("Failed catalog did not recover without duplicate reads");
    await page.setViewportSize({width: 390, height: 844});
    if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error("Lark configuration overflows at 390px");
    await page.screenshot({path: resolve(outputDir, "lark-progressive-narrow.png"), animations: "disabled"});
    if (context.errors.some(error => !error.startsWith("Failed to load resource:"))) throw new Error(context.errors.join(" | "));
    return await context.close();
  } catch (error) {releaseCatalog(); releaseSlow(); await context.close(); throw error;}
}

// One Goal's lifecycle action must not send the rest of the workspace back to
// the loading lane. The directory entry is the cheap signal for "this Goal did
// not move", so only the touched Goal is re-read.
export const progressiveLoadingScenario = {
  id: "progressive-loading",
  async run({ browser, collectCoverage, url }) {
    const context = await openWorkspacePage(browser, url, {
      apiOptions: { progressiveWorkspace: true },
      collectCoverage,
    });
    const { api, coverageEntries, errors: pageErrors, page } = context;
    // Only the product's own reads are asserted: the development server may
    // refuse an asset it considers outside its root, which is a harness path
    // question rather than a client failure.
    const failedResponses = [];
    page.on("response", (response) => {
      if (response.status() >= 400 && response.url().includes("/api/")) {
        failedResponses.push(`${response.status()} ${response.url()}`);
      }
    });
    try {
      await page.waitForFunction(
        () => document.querySelectorAll(".personal-home-lane[aria-live]").length === 0,
        null,
        { timeout: 60_000 },
      );
      if (api.workspaceDirectoryRequests === 0) throw new Error("Progressive loading never read the workspace directory");
      const cardTitles = () => page.locator(".personal-home-lanes .personal-home-goal-card strong").allInnerTexts();
      const peers = (await cardTitles()).filter((title) => title !== "Product Release");
      if (peers.length === 0) throw new Error("Progressive loading rendered no peer Goal cards");
      const loadingCards = () => page.locator('.personal-home-goal-card[data-goal-state="loading"]').count();
      if (await loadingCards()) throw new Error("The workspace settled with a Goal still in its loading state");

      api.goalStatusRequests.length = 0;
      await page.getByRole("button", { name: "停止 Product Release", exact: true }).click();
      await page.waitForTimeout(3_000);

      const rereadPeers = [...new Set(api.goalStatusRequests)].filter((goalId) => goalId !== "product-release");
      if (rereadPeers.length) {
        throw new Error(`Pausing one Goal re-read its peers: ${JSON.stringify(rereadPeers)}`);
      }
      if (await loadingCards()) throw new Error("Pausing one Goal sent the workspace back to its loading lane");
      const afterStop = await cardTitles();
      for (const title of peers) {
        if (!afterStop.includes(title)) throw new Error(`Pausing one Goal dropped the peer card ${title}`);
      }
      const stoppedDirectory = page.locator(".personal-stopped-goals");
      if (!await stoppedDirectory.evaluate((node) => node.open)) {
        await stoppedDirectory.locator("summary").click();
      }

      api.goalStatusRequests.length = 0;
      await page.getByRole("button", { name: "恢复 Product Release", exact: true }).click();
      await page.getByText("确认执行", { exact: true }).waitFor({ state: "visible" });
      await page.locator('[data-action-review="review"]').filter({ hasText: "恢复自动调度前需要确认" }).waitFor({ state: "visible" });
      await page.getByRole("button", { name: "恢复 Goal", exact: true }).click();
      await page.waitForTimeout(3_000);

      const rereadPeersAfterResume = [...new Set(api.goalStatusRequests)]
        .filter((goalId) => goalId !== "product-release");
      if (rereadPeersAfterResume.length) {
        throw new Error(`Resuming one Goal re-read its peers: ${JSON.stringify(rereadPeersAfterResume)}`);
      }
      if (await loadingCards()) throw new Error("Resuming one Goal sent the workspace back to its loading lane");
      const resumed = await cardTitles();
      for (const title of peers) {
        if (!resumed.includes(title)) throw new Error(`Resuming one Goal dropped the peer card ${title}`);
      }
      if (!resumed.includes("Product Release")) throw new Error("The resumed Goal did not return to the workspace board");

      if (failedResponses.length) throw new Error(`Progressive reconciliation failed requests: ${failedResponses.join(" | ")}`);
      // The harness collects uncaught page errors and console errors; a
      // dev-server resource status is not a client-side exception.
      const scriptErrors = pageErrors.filter((message) => !message.startsWith("Failed to load resource"));
      if (scriptErrors.length) throw new Error(`Progressive reconciliation raised page errors: ${scriptErrors.join(" | ")}`);
      coverageEntries.push(...await verifyLarkLoading(browser, url, collectCoverage));
      return {
        coverageEntries,
        note: "progressive loading reads the workspace directory once and then one Goal at a time; a single Goal's pause and resume re-read no peer, never re-enters the loading lane and keeps every peer card on the board",
      };
    } finally {
      coverageEntries.push(...await context.close());
    }
  },
};
