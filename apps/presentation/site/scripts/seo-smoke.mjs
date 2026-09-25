import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const directory = resolve(process.argv[2] ?? fileURLToPath(new URL("../dist", import.meta.url)));
const origin = "https://loopx-project.github.io/loopx/";
const titles = new Set();
for (const [path, subject] of [["", "long-running"], ["benchmarks/swe-marathon/", "SWE-Marathon"], ["benchmarks/lhtb/", "LHTB"]]) {
  const html = await readFile(resolve(directory, path, "index.html"), "utf8");
  const title = html.match(/<title>(.*?)<\/title>/s)?.[1];
  assert(title?.toLowerCase().includes(subject.toLowerCase()), `${path}: descriptive title`);
  assert(!titles.has(title), "page titles must be distinct");
  titles.add(title);
  assert.equal((html.match(/<h1(?:\s|>)/g) ?? []).length, 1, `${path}: one static primary heading`);
  assert((html.match(/<h2(?:\s|>)/g) ?? []).length >= 2, `${path}: real sections before JavaScript`);
  assert(html.includes('id="root">') && !html.includes('id="root"></div>'), `${path}: populated React root`);
  assert.equal((html.match(/rel="canonical"/g) ?? []).length, 1);
  assert(html.includes(`rel="canonical" href="${origin}${path}"`), `${path}: canonical matches route`);
  if (!path) {
    const structured = JSON.parse(html.match(/<script type="application\/ld\+json">(.*?)<\/script>/s)?.[1] ?? "null");
    assert.equal(structured?.["@type"], "SoftwareSourceCode");
    assert.equal(structured?.codeRepository, "https://github.com/loopx-project/loopx");
  }
  const description = html.match(/<meta name="description" content="([^"]+)"/)?.[1];
  assert(description?.length > 50 && description.length < 240, `${path}: useful snippet`);
  assert(html.includes(`property="og:description" content="${description}"`));
  assert(html.includes(`property="og:url" content="${origin}${path}"`));
  assert(html.includes(`name="twitter:title" content="${title}"`));
  assert(!/rel="[^"]*noreferrer/.test(html), `${path}: public referrals retained`);
  assert(!/file:\/\/|\/Users\/|node_modules\//.test(html), `${path}: no build paths`);
  for (const [, asset] of html.matchAll(/(?:src|href)="([^"?#]*site-assets\/[^"?#]+)"/g)) {
    await access(resolve(directory, "site-assets", asset.split("site-assets/")[1]));
  }
}
const sitemap = await readFile(resolve(directory, "sitemap-pages.xml"), "utf8");
const locations = [...sitemap.matchAll(/<loc>(.*?)<\/loc>/g)].map((m) => m[1]);
assert.equal(new Set(locations).size, locations.length, "no duplicate sitemap URLs");
assert(locations.includes(origin) && locations.includes(`${origin}blog/zh/`));
for (const url of locations) {
  assert(url.startsWith(origin) && !/[?#]/.test(url), `canonical sitemap URL: ${url}`);
  const path = url.slice(origin.length);
  const html = await readFile(resolve(directory, path, path.endsWith("/") || !path ? "index.html" : ""), "utf8");
  assert(html.includes(`rel="canonical" href="${url}"`), `self-canonical sitemap target: ${url}`);
}
if (process.argv.includes("--with-docs")) {
  const index = await readFile(resolve(directory, "sitemap.xml"), "utf8");
  for (const [, url] of index.matchAll(/<loc>(.*?)<\/loc>/g)) await access(resolve(directory, url.slice(origin.length)));
}
console.log(`SEO publication: three prerendered routes, ${locations.length} canonical sitemap targets, assets and metadata passed`);
