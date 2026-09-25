import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { cp, mkdtemp, readFile, rm, access } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { resolve, extname } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const require = createRequire(resolve(root, "../dashboard/package.json"));
const { chromium } = require("playwright");
const out = await mkdtemp(resolve(tmpdir(), "loopx-analytics-smoke-"));
const inject = (id) => spawnSync(process.execPath, [resolve(root, "scripts/add-analytics.mjs"), out], {
  env: { ...process.env, LOOPX_GA_MEASUREMENT_ID: id }, encoding: "utf8",
});
let browser;
try {
  await cp(resolve(process.argv[2] ?? resolve(root, "dist")), out, { recursive: true });
  assert.equal(inject("").status, 0);
  const initial = await readFile(resolve(out, "index.html"), "utf8");
  assert.equal(inject("").status, 0);
  assert.equal(await readFile(resolve(out, "index.html"), "utf8"), initial, "disabled mode leaves HTML unchanged");
  assert.notEqual(inject('invalid"><script>').status, 0, "malformed ID rejected");
  // This fixture ID never reaches Google: the browser intercepts every request.
  assert.equal(inject("G-TEST123456").status, 0);
  const enabled = await readFile(resolve(out, "index.html"), "utf8");
  assert.equal(inject("G-TEST123456").status, 0);
  assert.equal(await readFile(resolve(out, "index.html"), "utf8"), enabled, "injection is idempotent");
  browser = await chromium.launch({ headless: true });
  for (const scenario of ["enabled", "local", "dnt", "gpc"]) {
    const context = await browser.newContext();
    if (scenario === "dnt") await context.addInitScript(() => Object.defineProperty(navigator, "doNotTrack", { value: "1" }));
    if (scenario === "gpc") await context.addInitScript(() => Object.defineProperty(navigator, "globalPrivacyControl", { value: true }));
    const tagRequests = [];
    const collectionRequests = [];
    if (scenario !== "enabled") await context.addInitScript(() => localStorage.setItem("loopx.analytics-consent.v1", JSON.stringify({ choice: "granted" })));
    const origin = scenario === "local" ? "http://localhost" : "https://loopx-project.github.io";
    await context.route("**/*", async (route) => {
      const url = new URL(route.request().url());
      if (url.hostname === "www.googletagmanager.com") {
        tagRequests.push(url.href);
        return route.fulfill({ contentType: "text/javascript", body: `
          document.cookie = "_ga=fixture; path=/; domain=loopx-project.github.io";
          document.cookie = "_ga_TEST123456=fixture; path=/";
          const collect = (args) => { if (args[0] === "event" && !window["ga-disable-G-TEST123456"]) fetch("https://www.google-analytics.com/g/collect", {method:"POST",body:JSON.stringify([...args])}); };
          window.dataLayer.forEach(collect);
          const push = window.dataLayer.push.bind(window.dataLayer);
          window.dataLayer.push = (...rows) => { rows.forEach(collect); return push(...rows); };
        ` });
      }
      if (url.hostname === "www.google-analytics.com") {
        collectionRequests.push(route.request().postData());
        return route.fulfill({ status: 204, body: "" });
      }
      if (url.origin !== origin) return route.abort();
      const path = url.pathname.replace(/^\/(?:loopx\/)?/, "");
      try {
        const file = resolve(out, !path || path.endsWith("/") ? path + "index.html" : path);
        const contentType = ({ ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".png": "image/png" })[extname(file)] ?? "application/octet-stream";
        await route.fulfill({ contentType, body: await readFile(file) });
      } catch { await route.fulfill({ status: 404, body: "Not found" }); }
    });
    const page = await context.newPage();
    await page.goto(`${origin}/loopx/?secret=never-send-this#private-task`);
    await page.locator("h1").waitFor();
    if (scenario !== "enabled") {
      assert.equal(tagRequests.length, 0, `${scenario}: no Google request`);
      assert.equal(await page.evaluate(() => window.dataLayer), undefined);
      await context.close();
      continue;
    }
    await page.locator("#loopx-analytics-consent").waitFor();
    // A normal visitor's pre-choice interactions must not even initialize GA.
    await page.evaluate(() => window.dispatchEvent(new CustomEvent("loopx:setup-copy", { detail: "shell" })));
    assert.equal(tagRequests.length, 0, "no remote tag before choosing");
    assert.equal(collectionRequests.length, 0, "no collection before choosing");
    assert.equal(await page.evaluate(() => window.dataLayer), undefined);
    assert.equal((await context.cookies()).filter(c => c.name.startsWith("_ga")).length, 0);
    await page.locator('[data-consent="denied"]').click();
    await page.reload();
    await page.locator("h1").waitFor();
    assert.equal(await page.locator("#loopx-analytics-consent").isVisible(), false, "rejection remembered");
    assert.equal(tagRequests.length, 0, "rejected reload stays off");
    await page.locator("#loopx-analytics-settings").click();
    await page.locator('[data-consent="granted"]').click();
    await page.waitForFunction(() => window.dataLayer?.length >= 7);
    await page.waitForFunction(() => document.cookie.includes("_ga="));
    assert.equal(tagRequests.length, 1);
    await page.locator('.hero [data-analytics-event="setup_open"]').click();
    // Only the successful copy path in App dispatches this signal. Invalid
    // detail must never become an event or pass arbitrary data to analytics.
    await page.evaluate(() => {
      window.dispatchEvent(new CustomEvent("loopx:setup-copy", { detail: "shell" }));
      window.dispatchEvent(new CustomEvent("loopx:setup-copy", { detail: "never-send-this" }));
    });
    await page.getByRole("button", { name: "Close setup", exact: true }).click();
    await page.locator(".language-toggle").click();
    const rows = await page.evaluate(() => window.dataLayer.map((args) => [...args]));
    assert.equal(rows.filter((r) => r[0] === "event" && r[1] === "page_view").length, 1);
    assert.equal(rows.filter((r) => r[0] === "event" && r[1] === "setup_open").length, 1);
    assert.equal(rows.filter((r) => r[0] === "event" && r[1] === "setup_copy").length, 1);
    assert(!JSON.stringify(rows).includes("never-send-this") && !JSON.stringify(rows).includes("private-task"));
    assert.equal(rows.find((r) => r[0] === "config")[2].allow_google_signals, false);
    await page.evaluate(() => {
      document.querySelector('link[rel="canonical"]').href = "https://loopx-project.github.io/loopx/docs/";
    });
    await page.waitForFunction(() => window.dataLayer.filter((r) => r[0] === "event" && r[1] === "page_view").length === 2);
    assert.equal(collectionRequests.filter(row => JSON.parse(row)[1] === "page_view").length, 2);
    await page.reload();
    await page.waitForFunction(() => document.cookie.includes("_ga="));
    assert.equal(tagRequests.length, 2, "grant remembered across reloads");
    assert.equal(await page.locator("#loopx-analytics-consent").isVisible(), false);
    await page.locator("#loopx-analytics-settings").click();
    const beforeWithdrawal = collectionRequests.length;
    await Promise.all([page.waitForEvent("load"), page.locator('[data-consent="denied"]').click()]);
    await page.locator("h1").waitFor();
    assert.equal(tagRequests.length, 2, "withdrawal unloads tag without reloading it");
    assert.equal(collectionRequests.length, beforeWithdrawal, "withdrawal sends no events");
    assert.equal(await page.evaluate(() => window.dataLayer), undefined);
    assert.equal((await context.cookies()).filter(c => c.name.startsWith("_ga")).length, 0, "GA cookies cleared");
    // Replacing a footer (as in client rendering / instant navigation) retains
    // the visitor's settings entry without re-enabling measurement.
    await page.evaluate(() => document.querySelector(".site-footer").replaceChildren());
    await page.locator(".site-footer #loopx-analytics-settings").waitFor();
    if (await access(resolve(out, "docs/index.html")).then(() => true, () => false)) {
      await page.goto(`${origin}/loopx/docs/`);
      await page.locator("#loopx-analytics-settings").waitFor();
      assert.equal(tagRequests.length, 2, "rejection shared with documentation pages");
    }
    await context.close();
  }
  assert.equal(inject("").status, 0);
  assert.equal(await readFile(resolve(out, "index.html"), "utf8"), initial, "disable removes injection");
  await assert.rejects(access(resolve(out, "site-assets/analytics.js")));
  await assert.rejects(access(resolve(out, "site-assets/analytics-consent.css")));
  console.log("Analytics smoke: pre-choice/rejection isolation, grant, remembered choice, withdrawal/cookie cleanup, footer replacement, deployment off, DNT/GPC and event boundaries passed; Google requests intercepted");
} finally {
  await browser?.close();
  await rm(out, { recursive: true, force: true });
}
