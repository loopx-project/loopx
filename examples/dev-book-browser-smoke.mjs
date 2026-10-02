#!/usr/bin/env node
// Verify Mermaid rendering from the final assembled GitHub Pages artifact.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { dirname, extname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dashboard = resolve(root, "apps/presentation/dashboard");
const require = createRequire(resolve(dashboard, "package.json"));
const { chromium } = require("playwright");
const site = resolve(process.env.LOOPX_PUBLIC_SITE_DIR ?? resolve(root, "output/frontstage-pages/site"));

const server = createServer(async (request, response) => {
  const pathname = new URL(request.url, "http://localhost").pathname;
  if (!pathname.startsWith("/loopx/")) {
    response.writeHead(404).end();
    return;
  }
  const file = resolve(site, pathname.slice(7) + (pathname.endsWith("/") ? "index.html" : ""));
  if (!file.startsWith(`${site}/`)) {
    response.writeHead(403).end();
    return;
  }
  try {
    const body = await readFile(file);
    const contentType = ({
      ".css": "text/css",
      ".html": "text/html",
      ".js": "text/javascript",
      ".json": "application/json",
      ".png": "image/png",
      ".svg": "image/svg+xml",
    })[extname(file)] ?? "application/octet-stream";
    response.writeHead(200, { "Content-Type": contentType }).end(body);
  } catch {
    response.writeHead(404).end();
  }
});

await new Promise((resolveListen) => server.listen(0, "127.0.0.1", resolveListen));
const origin = `http://127.0.0.1:${server.address().port}`;
let browser;
try {
  browser = await chromium.launch({ headless: true });
  for (const [path, expectedDiagrams] of [
    ["docs/guides/personal-workspace-user-guide/", 1],
    ["docs/book/chapters/core-state-machines/", 10],
    ["docs/book/en/chapters/core-state-machines/", 10],
    ["docs/book/chapters/03-one-turn/", 1],
    ["docs/book/en/chapters/03-one-turn/", 1],
    ["docs/book/chapters/work-graph-and-authority/", 1],
    ["docs/book/en/chapters/work-graph-and-authority/", 1],
    ["docs/book/chapters/04-runtime-boundaries/", 1],
    ["docs/book/en/chapters/04-runtime-boundaries/", 1],
  ]) {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const failedScripts = [];
    const runtimeErrors = [];
    const mermaidRequests = [];
    page.on("pageerror", (error) => runtimeErrors.push(error.message));
    page.on("response", (response) => {
      const url = response.url();
      if (url.includes("mermaid")) mermaidRequests.push(url);
      if (
        response.request().resourceType() === "script" &&
        (response.status() < 200 || response.status() >= 400)
      ) {
        failedScripts.push(`${response.status()} ${url}`);
      }
    });

    await page.goto(`${origin}/loopx/${path}`, { waitUntil: "networkidle" });
    await page.locator("div.mermaid").first().waitFor();
    assert.equal(await page.locator("div.mermaid").count(), expectedDiagrams, `${path}: rendered Mermaid diagram count`);
    assert.equal(await page.locator("pre.mermaid").count(), 0, `${path}: Mermaid source container must be replaced`);
    assert.ok(await page.locator("div.mermaid").evaluateAll((nodes) => nodes.every((node) => node.clientWidth > 0 && node.clientHeight > 0)), `${path}: rendered diagrams must have visible dimensions`);
    assert.equal(mermaidRequests.filter((url) => url.includes("unpkg.com/mermaid@11/dist/mermaid.min.js")).length, 1, `${path}: one Material-owned Mermaid runtime request`);
    assert.equal(mermaidRequests.filter((url) => url.includes("/javascripts/mermaid.js")).length, 0, `${path}: no stale custom Mermaid owner`);
    assert.deepEqual(failedScripts, [], `${path}: failed script resources`);
    assert.deepEqual(runtimeErrors, [], `${path}: browser runtime errors`);
    await page.close();
  }
  console.log("dev-book-browser-smoke: ok");
} finally {
  await browser?.close();
  server.closeAllConnections();
  await new Promise((resolveClose) => server.close(resolveClose));
}
