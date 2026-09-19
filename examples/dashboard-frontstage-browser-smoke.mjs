#!/usr/bin/env node
// Exercise replacement surfaces and old bookmarks through the real browser router.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir, readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { dirname, resolve, extname } from "node:path";
import { fileURLToPath } from "node:url";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dashboard = resolve(root, "apps/presentation/dashboard");
const require = createRequire(resolve(dashboard, "package.json"));
const { chromium } = require("playwright");
const exportSite = process.env.LOOPX_PUBLIC_SITE_DIR ?? "/tmp/loopx-frontstage-share-bundle-smoke/site";
const output = resolve(root, "output/playwright/home-navigation");
await mkdir(output, { recursive: true });
const child = spawn(process.execPath, [resolve(dashboard, "node_modules/vite/bin/vite.js"), "--host", "127.0.0.1", "--port", "5197", "--strictPort"], { cwd: dashboard, stdio: "pipe" });
let logs = "";
child.stdout.on("data", (v) => { logs += v; });
child.stderr.on("data", (v) => { logs += v; });
const staticServer = createServer(async (req, res) => {
  const path = new URL(req.url, "http://localhost").pathname;
  if (!path.startsWith("/loopx/")) { res.writeHead(404).end(); return; }
  const file = resolve(exportSite, path.slice(7) + (path.endsWith("/") ? "index.html" : ""));
  if (!file.startsWith(resolve(exportSite) + "/")) { res.writeHead(403).end(); return; }
  try {
    const body = await readFile(file);
    res.setHeader("Content-Type", ({ ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".png": "image/png", ".svg": "image/svg+xml" })[extname(file)] ?? "application/octet-stream");
    res.end(body);
  } catch { res.writeHead(404).end(); }
});
await new Promise((done) => staticServer.listen(0, "127.0.0.1", done));
const publicOrigin = `http://127.0.0.1:${staticServer.address().port}`;
async function assertAnchorInView(page, id) {
  await page.waitForFunction((id) => {
    const section = document.getElementById(id);
    if (!section) return false;
    const rect = section.getBoundingClientRect();
    const top = rect.top;
    const heading = section.querySelector("h1, h2, h3")?.getBoundingClientRect();
    // A short final section cannot align at the top. Font reflow can leave a
    // few pixels below the viewport; require the whole section, not scroll-end
    // equality, so the assertion matches what the reader actually sees.
    const finalSection = section.closest("main")?.lastElementChild;
    const finalSectionVisible = finalSection?.contains(section) && rect.bottom <= innerHeight;
    const header = document.querySelector(".bm-topbar")?.getBoundingClientRect();
    const headerBottom = header?.bottom ?? 0;
    const reveal = section.closest(".reveal-block");
    return (!header || Math.abs(header.top) < 2) && top >= -2 && (top < 80 || finalSectionVisible) &&
      (!heading || (heading.top >= Math.max(0, headerBottom) && heading.bottom < innerHeight)) &&
      (!reveal || getComputedStyle(reveal).opacity === "1");
  }, id, { timeout: 4000 }).catch(async (error) => {
    const viewport = await page.locator(`[id="${id}"]`).evaluate((section) => ({
      scrollY, top: section.getBoundingClientRect().top,
      headingTop: section.querySelector("h1, h2, h3")?.getBoundingClientRect().top,
    }));
    throw new Error(`Anchor ${id} at ${page.url()}: ${JSON.stringify(viewport)}`, { cause: error });
  });
}

let browser;
try {
  for (let i = 0; ; i++) {
    try { if ((await fetch("http://127.0.0.1:5197/")).ok) break; } catch {}
    if (i > 100 || child.exitCode !== null) throw new Error(`Vite did not start: ${logs}`);
    await new Promise((done) => setTimeout(done, 200));
  }
  browser = await chromium.launch({ headless: true });
  // Serve the real bundled font behind a controlled delay. Check both late
  // reflow correction and its cancellation when a reader scrolls away.
  const font = await readFile(resolve(dashboard, "node_modules/@fontsource-variable/geist/files/geist-latin-wght-normal.woff2"));
  for (const width of [1440, 390]) {
    for (const [path, id] of [["", "learn"], ["benchmarks/swe-marathon/", "mechanism"]]) {
      for (const userScrolls of [false, true]) {
        const fontContext = await browser.newContext({ reducedMotion: "reduce", viewport: { width, height: 900 } });
        const fontPage = await fontContext.newPage();
        let releaseFont;
        const fontGate = new Promise((resolve) => { releaseFont = resolve; });
        await fontPage.route("https://fonts.googleapis.com/**", (route) => route.fulfill({
          contentType: "text/css",
          body: '@font-face { font-family: Geist; src: url(https://fonts.gstatic.com/navigation-test.woff2) format("woff2"); font-weight: 100 900; font-display: swap; }',
        }));
        await fontPage.route("https://fonts.gstatic.com/navigation-test.woff2", async (route) => {
          await fontGate;
          await route.fulfill({ contentType: "font/woff2", body: font });
        });
        await fontPage.goto(`${publicOrigin}/loopx/${path}#${id}`, { waitUntil: "domcontentloaded" });
        await fontPage.waitForFunction(() => document.fonts.status === "loading");
        await assertAnchorInView(fontPage, id);
        if (userScrolls) {
          await fontPage.mouse.wheel(0, -20000);
          await fontPage.waitForFunction(() => scrollY === 0);
        }
        releaseFont();
        await fontPage.evaluate(async () => {
          await document.fonts.ready;
          await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        });
        if (userScrolls) assert.equal(await fontPage.evaluate(() => scrollY), 0, "late fonts must not undo reader scrolling");
        else await assertAnchorInView(fontPage, id);
        await fontContext.close();
      }
    }
  }
  console.log("Delayed font reflow and reader-scroll cancellation: ok");
  // A shared fragment URL must land on its section without any test-driven
  // scroll or focus. Use cold pages and delayed JS to cover pre-React parsing.
  for (const reducedMotion of ["no-preference", "reduce"]) {
    for (const width of [1440, 390]) {
      const entryContext = await browser.newContext({ reducedMotion, viewport: { width, height: 900 } });
      const entry = await entryContext.newPage();
      const entryErrors = [];
      entry.on("pageerror", (error) => entryErrors.push(error.message));
      await entry.route("**/site-assets/*.js", async (route) => {
        await new Promise((done) => setTimeout(done, 250));
        await route.continue();
      });
      for (const path of ["", "benchmarks/swe-marathon/"]) {
        const anchors = path
          ? ["top", "summary", "background", "mechanism", "zstd", "official-comparison", "boundary", "sources"]
          : ["top", "main", "product", "workflow", "showcases", "explore", "learn", "quickstart"];
        for (const lang of ["en", "zh"]) {
          for (const id of anchors) {
            await entry.goto("about:blank");
            await entry.goto(`${publicOrigin}/loopx/${path}?lang=${lang}#${id}`);
            await assertAnchorInView(entry, id);
            await entry.reload();
            await assertAnchorInView(entry, id);
          }
          await entry.goto(`${publicOrigin}/loopx/${path}index.html?lang=${lang}#${anchors[2]}`);
          await assertAnchorInView(entry, anchors[2]);
        }
        for (const fragment of ["", "missing-section", "%zz"]) {
          await entry.goto("about:blank");
          await entry.goto(`${publicOrigin}/loopx/${path}?lang=zh#${fragment}`);
          await entry.locator("h1").first().waitFor();
          assert.equal(await entry.evaluate(() => scrollY), 0, "missing fragments must preserve normal page entry");
        }
      }
      await entry.goto(`${publicOrigin}/loopx/?lang=zh#%65xplore`);
      await assertAnchorInView(entry, "explore");
      // Language changes must keep the section, and history must restore both
      // URL and rendered language. Clicks exercise real controls, not JS scroll.
      await entry.goto(`${publicOrigin}/loopx/`);
      const navigateHome = async (id) => {
        if (width === 390) {
          await entry.getByRole("button", { name: "Open navigation" }).click();
          await entry.locator(`.mobile-nav a[href="#${id}"]`).click();
          assert.equal(await entry.locator(".mobile-nav").count(), 0);
        } else await entry.locator(`.desktop-nav a[href="#${id}"]`).click();
        await assertAnchorInView(entry, id);
      };
      await navigateHome("product");
      await entry.locator(".language-toggle").click();
      await entry.waitForFunction(() => document.documentElement.lang === "zh-CN");
      await assertAnchorInView(entry, "product");
      await navigateHome("workflow");
      await navigateHome("explore");
      await navigateHome("explore"); // Clicking the current fragment still navigates.
      // Opening the header menu can scroll to the top before a click. History
      // restores that user position, so do not force it to the fragment again.
      await entry.goBack();
      assert.equal(new URL(entry.url()).hash, "#workflow");
      await entry.goBack();
      assert.equal(new URL(entry.url()).hash, "#product");
      await entry.goBack();
      await entry.waitForFunction(() => document.documentElement.lang === "en");
      await entry.goForward();
      await entry.waitForFunction(() => document.documentElement.lang === "zh-CN");
      assert.equal(new URL(entry.url()).hash, "#product");
      await entry.locator('.hero .button-secondary').click();
      await assertAnchorInView(entry, "showcases");
      await entry.goBack();
      assert.equal(new URL(entry.url()).hash, "#product", "section CTA must preserve previous history entry");
      await entry.goForward();
      assert.equal(new URL(entry.url()).hash, "#showcases");
      await entry.locator('.site-footer a[href="#top"]').click();
      await assertAnchorInView(entry, "top");
      await entry.getByRole('link', { name: 'LoopX home', exact: true }).click();
      assert.equal(new URL(entry.url()).searchParams.get("lang"), "zh", "home link must preserve language");
      await entry.goto(`${publicOrigin}/loopx/benchmarks/swe-marathon/#mechanism`);
      await entry.getByRole("button", { name: "中文", exact: true }).click();
      await assertAnchorInView(entry, "mechanism");
      await entry.locator('.bm-footer a[href="#top"]').click();
      await assertAnchorInView(entry, "top");
      await entry.goBack();
      assert.equal(new URL(entry.url()).hash, "#mechanism");
      await entry.locator('.bm-home-link').click();
      await entry.locator("#explore").waitFor();
      assert.equal(new URL(entry.url()).searchParams.get("lang"), "zh");
      assert.deepEqual(entryErrors, [], "fragment entry must not raise runtime errors");
      console.log(`Fragment/history matrix: ${width}px, ${reducedMotion}: ok`);
      await entryContext.close();
    }
  }
  const context = await browser.newContext({ reducedMotion: "reduce" });
  const page = await context.newPage();
  await page.route((url) => url.pathname === "/status.example.json", async (route) => route.fulfill({ contentType: "application/json", body: await readFile(resolve(root, "examples/status.example.json"), "utf8") }));
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const privateRequests = [];
  await page.route((url) => url.pathname === "/private-status.json", (route) => { privateRequests.push(route.request().url()); return route.abort(); });
  // Resolve the public destination to the actual exported case directory in this test.
  await page.route("https://loopx-project.github.io/loopx/**", (route) => {
    return route.fulfill({ status: 200, contentType: "text/html", body: "<h1>Public destination</h1>" });
  });
  for (const route of ["/frontstage?statusUrl=/private-status.json", "/frontstage?mode=showcase&statusUrl=https://example.com/private-status.json"]) {
    await page.goto(`http://127.0.0.1:5197${route}`);
    await page.waitForURL("https://loopx-project.github.io/loopx/docs/showcases/index.en.html");
  }
  for (const route of ["/frontstage?mode=developer", "/frontstage/developer"]) {
    await page.goto(`http://127.0.0.1:5197${route}`);
    await page.waitForURL("**/developers/projections");
    await page.locator('[data-testid="frontstage-developer-cockpit"]').waitFor();
  }
  for (const route of ["/frontstage?mode=ops&", "/deprecated/frontstage/ops?"]) {
    await page.goto(`http://127.0.0.1:5197${route}goalId=demo&statusUrl=https://example.com/private-status.json`);
    await page.getByRole("alert").filter({ hasText: "relative or loopback" }).waitFor();
    await page.goto(`http://127.0.0.1:5197${route}goalId=demo&statusUrl=/status.example.json`);
    await page.waitForURL((url) => url.pathname === "/" && url.searchParams.get("goalId") === "demo" && url.searchParams.get("statusUrl") === "/status.example.json");
    await page.locator(".personal-workspace-shell").waitFor();
  }
  assert.deepEqual(privateRequests, [], "retired/public URLs must not read rejected status sources");
  for (const [route, target] of [
    ["frontstage/?mode=ops&statusUrl=/private-status.json", "/loopx/docs/showcases/index.en.html"],
    ["frontstage/developer/", "/loopx/developers/projections/"],
  ]) {
    await page.goto(`${publicOrigin}/loopx/${route}`);
    await page.waitForURL(`${publicOrigin}${target}`);
  }
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    for (const lang of ["en", "zh"]) {
      await page.goto(`${publicOrigin}/loopx/?lang=${lang}`);
      await page.locator("#explore").waitFor();
      assert.equal(await page.locator("html").getAttribute("lang"), lang === "zh" ? "zh-CN" : "en");
      assert.equal(await page.locator('a[href*="deprecated"], a[href*="frontstage/"]').count(), 0);
      const expected = ["docs/guides/personal-workspace-user-guide/", `benchmarks/swe-marathon/${lang === "zh" ? "?lang=zh" : ""}`, `benchmarks/lhtb/${lang === "zh" ? "?lang=zh" : ""}`, "benchmarks/deepswe/behavior-discovery/", "benchmarks/deepswe-sol/", `docs/showcases/index${lang === "en" ? ".en" : ""}.html`];
      assert.deepEqual(await page.locator("#explore .resource-card").evaluateAll((links) => links.map((a) => a.getAttribute("href"))), expected.map((path) => `/loopx/${path}`));
      if (width === 390) {
        await page.getByRole("button", { name: "Open navigation" }).click();
        await page.locator('.mobile-nav a[href="#explore"]').click();
        assert.equal(await page.locator(".mobile-nav").count(), 0);
      } else {
        await page.screenshot({ path: resolve(output, `home-${lang}-desktop.png`) });
        await page.locator('.desktop-nav a[href="#explore"]').click();
      }
      await assertAnchorInView(page, "explore");
      await page.locator("#explore .resource-card").first().focus();
      assert.ok(await page.locator("#explore .resource-card").first().evaluate((a) => a === document.activeElement));
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), "horizontal overflow");
      await page.screenshot({ path: resolve(output, `explore-${lang}-${width}.png`) });
      // Follow the actual research and case links; the guide is built by MkDocs later.
      for (let i = process.env.LOOPX_PUBLIC_SITE_DIR ? 0 : 1; i < expected.length; i++) {
        await page.goto(`${publicOrigin}/loopx/${expected[i]}`);
        await page.locator("h1").first().waitFor();
      }
    }
  }
  if (process.env.LOOPX_PUBLIC_SITE_DIR) {
    // Check the assembled publication, including the separately built books.
    const checked = new Set();
    for (const path of ["", "?lang=zh", "benchmarks/swe-marathon/", "benchmarks/lhtb/", "benchmarks/lhtb/?lang=zh", "benchmarks/deepswe/behavior-discovery/", "benchmarks/deepswe-sol/", "docs/showcases/index.html", "docs/showcases/index.en.html", "docs/guides/personal-workspace-user-guide/", "docs/book/", "docs/book/en/", "blog/", "blog/zh/"]) {
      await page.goto(`${publicOrigin}/loopx/${path}`);
      await page.locator("h1").first().waitFor();
      const links = await page.locator("a[href]").evaluateAll((links) => links.map((link) => link.href));
      for (const href of links) {
        const url = new URL(href);
        if (![publicOrigin, "https://huangruiteng.github.io"].includes(url.origin) || !url.pathname.startsWith("/loopx/")) continue;
        const target = `${publicOrigin}${url.pathname}${url.search}`;
        if (checked.has(target)) continue;
        checked.add(target);
        const response = await page.request.get(target);
        assert.ok(response.ok(), `Broken publication link from ${path}: ${href}`);
      }
    }
    console.log(`Published entry links checked: ${checked.size}`);
  }
  assert.deepEqual(errors, [], "browser runtime errors");
  console.log("public navigation and Frontstage migration browser smoke: ok");
} finally {
  await browser?.close();
  child.kill("SIGTERM");
  staticServer.closeAllConnections();
  await new Promise((done) => staticServer.close(done));
}
