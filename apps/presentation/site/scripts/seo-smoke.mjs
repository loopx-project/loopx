import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const directory = resolve(process.argv[2] ?? fileURLToPath(new URL("../dist", import.meta.url)));
const origin = "https://loopx-project.github.io/loopx/";
const repoRoot = fileURLToPath(new URL("../../../../", import.meta.url));
// Agent discovery must survive both Vite and the assembled Pages export as
// Markdown, with links to the existing workflow owners rather than copies.
const skill = await readFile(resolve(directory, "skill.md"), "utf8");
assert.equal(skill, await readFile(new URL("../public/skill.md", import.meta.url), "utf8"), "publish the skill unchanged");
assert.match(skill, /^---\nname: [a-z0-9-]+\ndescription: .+\n---\n/);
for (const [, href] of skill.matchAll(/\]\((https:\/\/[^)]+)\)/g)) {
  const url = new URL(href);
  if (url.hostname === "github.com" && url.pathname.startsWith("/loopx-project/loopx/")) {
    const sourcePath = url.pathname.replace(/^\/loopx-project\/loopx\/(?:blob|tree)\/main\//, "");
    if (sourcePath !== url.pathname) await access(resolve(repoRoot, sourcePath));
  }
  if (process.argv.includes("--with-docs") && href.startsWith(origin)) {
    const path = url.pathname.slice(new URL(origin).pathname.length);
    await access(resolve(directory, path, path.endsWith("/") || !path ? "index.html" : ""));
  }
}
const titles = new Set();
for (const [path, subject] of [["", "long-running"], ["benchmarks/swe-marathon/", "SWE-Marathon"], ["benchmarks/lhtb/", "LHTB"], ["lab/time-machine/", "Time Machine"]]) {
  const html = await readFile(resolve(directory, path, "index.html"), "utf8");
  const title = html.match(/<title>(.*?)<\/title>/s)?.[1];
  assert(title?.toLowerCase().includes(subject.toLowerCase()), `${path}: descriptive title`);
  assert(!titles.has(title), "page titles must be distinct");
  titles.add(title);
  assert.equal((html.match(/<h1(?:\s|>)/g) ?? []).length, 1, `${path}: one static primary heading`);
  assert((html.match(/<h2(?:\s|>)/g) ?? []).length >= (path === "lab/time-machine/" ? 1 : 2), `${path}: real sections before JavaScript`);
  assert(html.includes('id="root">') && !html.includes('id="root"></div>'), `${path}: populated React root`);
  assert.equal((html.match(/rel="canonical"/g) ?? []).length, 1);
  assert(html.includes(`rel="canonical" href="${origin}${path}"`), `${path}: canonical matches route`);
  if (!path) {
    const footer = html.match(/<footer class="site-footer">(.*?)<\/footer>/s)?.[1] ?? "";
    const basePath = html.match(/<script\b[^>]*\bsrc="([^"]*)site-assets\/[^"]+"/)?.[1];
    assert(basePath !== undefined, "homepage contains the built application script");
    assert(footer.includes(`href="${basePath}skill.md"`), "homepage footer discovers the skill under the built base");
    const structured = JSON.parse(html.match(/<script type="application\/ld\+json">(.*?)<\/script>/s)?.[1] ?? "null");
    assert.equal(structured?.["@type"], "SoftwareSourceCode");
    assert.equal(structured?.codeRepository, "https://github.com/loopx-project/loopx");
    assert(html.includes('lab/time-machine/'), "homepage discovers the interactive research experience");
  }
  if (path === "lab/time-machine/") {
    assert(html.includes("Local results improved."), "question exists before JavaScript");
    assert(html.includes("c858f51021dc1c1cf08263f9c04b692a5e29b40b"), "immutable archive remains accessible without JavaScript");
    const base = html.match(/<script\b[^>]*\bsrc="([^"]*)site-assets\/[^"]+"/)?.[1];
    assert(html.includes(`href="${base}"`), "return link preserves deployment base");
    const scenes = JSON.parse(await readFile(new URL("../src/time-machine/scenes.json", import.meta.url)));
    for (const scene of scenes) {
      assert.equal(await readFile(resolve(directory, path, "data", scene.file), "utf8"),
        await readFile(new URL(`../public/lab/time-machine/data/${scene.file}`, import.meta.url), "utf8"), "capture copied unchanged");
    }
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
console.log(`SEO publication: agent skill and owner links, four prerendered routes, ${locations.length} canonical sitemap targets, assets and metadata passed`);
