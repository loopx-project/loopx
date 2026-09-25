#!/usr/bin/env node
// Smoke-test the public-safe frontstage static export bundle.

import { spawnSync } from "node:child_process";
import { readFile, readdir, rm, stat } from "node:fs/promises";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { validateBilingualBlog } from "./blog-bilingual-index-smoke.mjs";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const outDir = resolve("/tmp", "loopx-frontstage-share-bundle-smoke");
const privateTrapFixturePath = resolve(repoRoot, "examples/fixtures/frontstage-private-status-trap.public.json");
const homepagePackagePath = resolve(repoRoot, "apps/presentation/site/package.json");
const homepageLockfilePath = resolve(repoRoot, "apps/presentation/site/package-lock.json");

function run(command, args, cwd = repoRoot) {
  const result = spawnSync(command, args, {
    cwd,
    encoding: "utf8",
    env: {
      ...process.env,
      PATH: ["/opt/homebrew/bin", "/usr/local/bin", process.env.PATH].filter(Boolean).join(":"),
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  if (result.status !== 0) {
    throw new Error(`Command failed: ${command} ${args.join(" ")}\n${result.stderr}\n${result.stdout}`);
  }
  return result.stdout;
}

function assertExists(path) {
  if (!existsSync(path)) {
    throw new Error(`Missing expected file: ${path}`);
  }
}

// Relative references in the exported editorial pages point at either another
// exported page directory or a shipped static asset. Page references must own
// an index.html; asset references must resolve to the asset file itself.
const relativeAssetReferencePattern =
  /\.(?:avif|css|csv|gif|ico|jpe?g|js|json|mjs|mp4|pdf|png|svg|txt|webm|webp|woff2?|xml|zip)$/i;
const nonBundleReferencePattern = /^(?:[a-z][a-z0-9+.-]*:|\/\/|#)/i;

function assertRelativeReferenceExists(pagePath, target) {
  if (relativeAssetReferencePattern.test(target)) {
    assertExists(resolve(dirname(pagePath), target));
    return;
  }
  assertExists(resolve(dirname(pagePath), target, "index.html"));
}

function assertNoLeak(text, label) {
  const forbidden = [
    /\/Users\//,
    /\/private\//,
    new RegExp("byte" + "dance", "i"),
    new RegExp("lark" + "office", "i"),
    new RegExp("\\.codex/goals|\\.goal-" + "harness"),
    new RegExp("raw_" + "internal_note"),
    /BEGIN (?:RSA |OPENSSH |EC |)PRIVATE KEY/,
    /\b(?:api[_-]?key|auth[_-]?token|access[_-]?token)\s*[:=]/i,
  ];
  const hit = forbidden.find((pattern) => pattern.test(text));
  if (hit) {
    throw new Error(`${label} leaked forbidden pattern: ${hit}`);
  }
}

function collectFakePrivateMarkers(text) {
  return Array.from(new Set(text.match(/GH_FAKE_[A-Z0-9_]+/g) ?? [])).sort();
}

async function collectGeneratedTextFiles(rootDir) {
  const files = [];
  async function visit(dir) {
    for (const entry of await readdir(dir, { withFileTypes: true })) {
      const path = resolve(dir, entry.name);
      if (entry.isDirectory()) {
        await visit(path);
      } else if (entry.isFile()) {
        const info = await stat(path);
        if (info.size <= 2_000_000 && /\.(css|html|js|json|md|sh|txt)$/i.test(path)) {
          files.push(path);
        }
      }
    }
  }
  await visit(rootDir);
  return files;
}

await rm(outDir, { force: true, recursive: true });
const homepagePackage = JSON.parse(await readFile(homepagePackagePath, "utf8"));
if (homepagePackage.dependencies?.["@vitejs/plugin-react"]) {
  throw new Error("@vitejs/plugin-react must stay in homepage devDependencies");
}
if (!homepagePackage.devDependencies?.["@vitejs/plugin-react"]) {
  throw new Error("homepage devDependencies must include @vitejs/plugin-react");
}
const homepageLockfile = JSON.parse(await readFile(homepageLockfilePath, "utf8"));
const nonPublicResolvedUrls = Object.values(homepageLockfile.packages ?? {})
  .map((entry) => entry?.resolved)
  .filter((resolved) => typeof resolved === "string" && !resolved.startsWith("https://registry.npmjs.org/"));
if (nonPublicResolvedUrls.length) {
  throw new Error(`homepage lockfile contains non-public resolved URLs: ${nonPublicResolvedUrls.join(", ")}`);
}
run(process.execPath, [
  resolve(repoRoot, "examples/export-frontstage-share-bundle.mjs"),
  "--out-dir",
  outDir,
  "--base",
  "/loopx/",
]);

const siteDir = resolve(outDir, "site");
assertExists(resolve(siteDir, "index.html"));
assertExists(resolve(siteDir, "frontstage/index.html"));
assertExists(resolve(siteDir, "benchmarks/swe-marathon/index.html"));
assertExists(resolve(siteDir, "benchmarks/lhtb/index.html"));
assertExists(resolve(siteDir, "benchmarks/deepswe/behavior-discovery/index.html"));
// Static research articles must remain readable and navigable in the shipped
// bundle without falling back to the homepage SPA.
for (const route of ["benchmarks/deepswe-sol/"]) {
  const pagePath = resolve(siteDir, route, "index.html");
  const html = await readFile(pagePath, "utf8");
  if (!/<h1\b/.test(html) || html.includes('<div id="root">') || html.includes("<script")) {
    throw new Error(`Research article must ship static content: ${route}`);
  }
  for (const match of html.matchAll(/(?:href|src)="([^"]+)"/g)) {
    const target = match[1].split(/[?#]/)[0];
    if (target && !nonBundleReferencePattern.test(target)) assertRelativeReferenceExists(pagePath, target);
  }
}
// Editorial pages must ship their text and locale navigation without an SPA
// fallback or client-side execution, including on repository-base hosting.
const blogDir = resolve(siteDir, "blog");
const { articleSlugs: englishBlogArticles } = await validateBilingualBlog(blogDir);
for (const locale of ["", "zh/"]) {
  const articles = ["", ...englishBlogArticles.map((slug) => `${slug}/`)];
  for (const article of articles) {
    const pagePath = resolve(siteDir, "blog", locale, article, "index.html");
    assertExists(pagePath);
    const html = await readFile(pagePath, "utf8");
    const language = locale ? "zh-CN" : "en";
    const interactive = article === "application-scenarios/";
    if (!html.includes(`<html lang="${language}">`) || !html.includes("<h1>") || (!interactive && html.includes("<script"))) {
      throw new Error(`Blog must provide static content in ${language}: ${pagePath}`);
    }
    if (interactive) {
      const scripts = [...html.matchAll(/<script\b[^>]*>[\s\S]*?<\/script>/g)].map((match) => match[0]);
      if (scripts.length !== 1 || scripts[0] !== '<script src="presentation.js" defer></script>') {
        throw new Error("Application article must keep enhancement in its local deferred script");
      }
      assertExists(resolve(dirname(pagePath), "presentation.js"));
    }
    const stylesheet = html.match(/<link rel="stylesheet" href="([^"]+)"/);
    if (!stylesheet) throw new Error("Blog stylesheet is missing");
    assertExists(resolve(dirname(pagePath), stylesheet[1]));
    for (const match of html.matchAll(/<a[^>]+href="([^"]+)"/g)) {
      const href = match[1];
      if (nonBundleReferencePattern.test(href)) continue;
      if (href.startsWith("/")) throw new Error("Blog navigation must preserve the hosting base");
      const target = href.split(/[?#]/)[0];
      // MkDocs pages are built later by the publication workflow.
      if (target.includes("docs/")) continue;
      assertRelativeReferenceExists(pagePath, target);
    }
    // Embedded diagrams are the primary content of these pages, so their local
    // sources must ship in the bundle too.
    for (const match of html.matchAll(/<img[^>]+src="([^"]+)"/g)) {
      const src = match[1];
      if (nonBundleReferencePattern.test(src)) continue;
      if (src.startsWith("/")) throw new Error("Blog images must preserve the hosting base");
      assertRelativeReferenceExists(pagePath, src.split(/[?#]/)[0]);
    }
  }
}
assertExists(resolve(siteDir, "install.sh"));
assertExists(resolve(siteDir, "status.frontstage-share.json"));
assertExists(resolve(outDir, "README.md"));
assertExists(resolve(outDir, "frontstage-share-manifest.json"));

// Reproduce MkDocs cleaning its output: restoring cases must leave the rest
// of the built site intact and include the catalog used by the case pages.
const originalHomepage = await readFile(resolve(siteDir, "index.html"), "utf8");
await rm(resolve(siteDir, "docs/showcases"), { recursive: true, force: true });
run(process.execPath, [resolve(repoRoot, "examples/export-frontstage-share-bundle.mjs"), "--restore-case-pages", "--out-dir", outDir]);
if (await readFile(resolve(siteDir, "index.html"), "utf8") !== originalHomepage) throw new Error("case restore replaced the homepage");
assertExists(resolve(siteDir, "docs/showcases/showcase-catalog.json"));
const restoredCase = await readFile(resolve(siteDir, "docs/showcases/cases/0619-loopx-self-iteration.en.html"), "utf8");
if (!restoredCase.includes("https://github.com/huangruiteng/loopx/blob/main/docs/showcases/cases/0619-loopx-self-iteration.md")) {
  throw new Error("case narrative source must not point to an unshipped Markdown URL");
}

const routerSource = await readFile(resolve(repoRoot, "apps/presentation/dashboard/src/router.tsx"), "utf8");
if (!routerSource.includes("basepath:") || !routerSource.includes("import.meta.env.BASE_URL")) {
  throw new Error("dashboard router must derive basepath from Vite BASE_URL for GitHub Pages");
}

const homepageHtml = await readFile(resolve(siteDir, "index.html"), "utf8");
if (!homepageHtml.includes('<div id="root">') || !homepageHtml.includes("<h1>")) {
  throw new Error("homepage must include prerendered content inside its React root");
}
if (!homepageHtml.includes('src="/loopx/site-assets/') || !homepageHtml.includes('href="/loopx/site-assets/')) {
  throw new Error("homepage compiled assets did not resolve against the GitHub Pages base");
}
if (homepageHtml.includes("__LOOPX_BASE__") || homepageHtml.includes("home.js") || homepageHtml.includes("home.css")) {
  throw new Error("homepage must not publish legacy static assets or unresolved base placeholders");
}
const homepageAssetNames = await readdir(resolve(siteDir, "site-assets"));
const homepageScriptName = homepageAssetNames.find((name) => /^index-.*\.js$/.test(name));
const homepageStyleName = homepageAssetNames.find((name) => /^index-.*\.css$/.test(name));
const issueEvidenceName = homepageAssetNames.find((name) => /^long-running-loop-openviking-trajectory-.*\.png$/.test(name));
const mlEvidenceName = homepageAssetNames.find((name) => /^long-running-loop-ml-experiment-trajectory-.*\.png$/.test(name));
for (const [label, name] of [
  ["homepage JavaScript", homepageScriptName],
  ["homepage CSS", homepageStyleName],
  ["issue evidence", issueEvidenceName],
  ["ML evidence", mlEvidenceName],
]) {
  if (!name) throw new Error(`missing compiled ${label} asset`);
  assertExists(resolve(siteDir, "site-assets", name));
}
const publishedInstaller = await readFile(resolve(siteDir, "install.sh"), "utf8");
const canonicalInstaller = await readFile(resolve(repoRoot, "scripts/install-from-github.sh"), "utf8");
if (publishedInstaller !== canonicalInstaller) {
  throw new Error("published installer must be byte-identical to scripts/install-from-github.sh");
}
const homepageSource = await readFile(resolve(repoRoot, "apps/presentation/site/src/App.tsx"), "utf8");
const homepageStyles = await readFile(resolve(repoRoot, "apps/presentation/site/src/styles.css"), "utf8");
const benchmarkHtml = await readFile(resolve(siteDir, "benchmarks/swe-marathon/index.html"), "utf8");
if (benchmarkHtml === homepageHtml || !benchmarkHtml.includes("<h1")) {
  throw new Error("SWE-Marathon must publish its own prerendered research content");
}
const lhtbHtml = await readFile(resolve(siteDir, "benchmarks/lhtb/index.html"), "utf8");
if (lhtbHtml === homepageHtml || !lhtbHtml.includes("<h1")) {
  throw new Error("LHTB must publish its own prerendered research content");
}
const deepSweBehaviorHtml = await readFile(
  resolve(siteDir, "benchmarks/deepswe/behavior-discovery/index.html"),
  "utf8",
);
const canonicalDeepSweBehaviorHtml = await readFile(
  resolve(repoRoot, "benchmark/deepswe/behavior-discovery/index.html"),
  "utf8",
);
if (deepSweBehaviorHtml !== canonicalDeepSweBehaviorHtml) {
  throw new Error("DeepSWE behavior article route must be byte-identical to the reviewed standalone source");
}
for (const sourceContract of [
  "Get started",
  "开始使用",
  "See in action",
  "查看实战",
  "showTerminalReplay",
  'href="#showcases"',
  'setActiveTerminal("issue")',
  "setTerminalReplayToken",
  "SetupDialog",
  "EvidenceViewer",
  "TerminalReplay",
  "200+ hours, still legible.",
  "跨越 200+ 小时，依然清晰可读。",
  "Prefer the shell? Install LoopX manually.",
  "Developer book",
  "benchmarks/swe-marathon/",
  "Pi",
]) {
  if (!homepageSource.includes(sourceContract) && !homepageStyles.includes(sourceContract)) {
    throw new Error(`React homepage is missing contract: ${sourceContract}`);
  }
}
for (const developerBookPath of [
  "docs/book/",
  "chapters/01-from-session-to-loop/",
  "chapters/05-connect-existing-project/",
  "chapters/source-protocol-map/",
]) {
  if (!homepageSource.includes(developerBookPath)) {
    throw new Error(`React homepage is missing Developer Book route: ${developerBookPath}`);
  }
}
for (const forbidden of [
  "cocolord.github.io/loopx-book",
  "https://github.com/huangruiteng/loopx/tree/main/docs",
]) {
  if (homepageSource.includes(forbidden)) {
    throw new Error(`React homepage retained forbidden external publication: ${forbidden}`);
  }
}
for (const promptContract of [
  "Connect the current project to LoopX",
  "Do not clone LoopX",
  "README Quick Start",
  "no-clone installer",
  "loopx doctor",
  "loopx connect/bootstrap",
  "project connection status, current user gate, top agent todo",
  "next safe action, and available commands",
  "/loopx <complex task>",
  "/loopx <goal text>",
  "concise ordered plan",
  "P0/P1/P2 todos",
  "把当前项目接入 LoopX",
  "下一步安全动作是什么",
]) {
  if (!homepageSource.includes(promptContract)) {
    throw new Error(`homepage agent setup prompt is missing contract: ${promptContract}`);
  }
}
const setupPromptMatch = homepageSource.match(/const setupPrompts: Record<Language, string> = \{\s+en: `([\s\S]*?)`,\s+zh: `([\s\S]*?)`,\s+\};/);
if (!setupPromptMatch) {
  throw new Error("homepage must expose concise English and Chinese setup prompts");
}
for (const [language, prompt] of [["English", setupPromptMatch[1]], ["Chinese", setupPromptMatch[2]]]) {
  if (prompt.length < 300 || prompt.length > 1_000) {
    throw new Error(`${language} homepage setup prompt must stay concise and complete; received ${prompt.length} characters`);
  }
}
if (!homepageSource.includes('document.createElement("textarea")') || !homepageSource.includes('document.execCommand("copy")')) {
  throw new Error("homepage setup prompt must keep a clipboard fallback for non-secure preview origins");
}
if (!homepageSource.includes("Math.min(200, zoom + 25)") || !homepageSource.includes("Math.max(50, zoom - 25)")) {
  throw new Error("homepage evidence viewer must retain bounded zoom controls");
}
if (!homepageStyles.includes("@media (prefers-reduced-motion: reduce)") || !homepageStyles.includes("animation-iteration-count: 1 !important")) {
  throw new Error("homepage motion must expose a static reduced-motion state");
}
if (!homepageStyles.includes("@keyframes terminal-line-enter") || !homepageStyles.includes(".terminal-paused") || !homepageStyles.includes(".terminal-playback")) {
  throw new Error("homepage evidence terminal must support finite replay, pause, and a static reduced-motion state");
}
const frontstageHtml = await readFile(resolve(siteDir, "frontstage/index.html"), "utf8");
if (!frontstageHtml.includes('content="0;url=../docs/showcases/index.en.html"') || frontstageHtml.includes('<script')) {
  throw new Error("retired Frontstage must redirect without loading the dashboard or forwarding status parameters");
}
const developerHtml = await readFile(resolve(siteDir, "developers/projections/index.html"), "utf8");
if (!developerHtml.includes('/loopx/assets/')) throw new Error("projection developer tools must retain compiled assets");
for (const [route, target] of [
  ["frontstage/developer", "../../developers/projections/"],
  ["deprecated/frontstage/ops", "../../../docs/guides/personal-workspace-user-guide/"],
]) {
  const html = await readFile(resolve(siteDir, route, "index.html"), "utf8");
  if (!html.includes(`content="0;url=${target}"`) || html.includes('<script')) throw new Error(`unsafe legacy redirect: ${route}`);
}

const status = JSON.parse(await readFile(resolve(siteDir, "status.frontstage-share.json"), "utf8"));
if (status.attention_queue?.items?.[0]?.goal_channel_projection?.schema_version !== "goal_channel_projection_v0") {
  throw new Error("share fixture did not include goal_channel_projection_v0");
}
if (status.attention_queue.items[0].goal_channel_projection.truth_contract.projection_is_writable !== false) {
  throw new Error("share fixture must stay read-only");
}

const manifest = JSON.parse(await readFile(resolve(outDir, "frontstage-share-manifest.json"), "utf8"));
if (manifest.base !== "/loopx/") {
  throw new Error(`manifest base mismatch: ${manifest.base}`);
}
if (
  manifest.homepage_entry !== "site/index.html" ||
  manifest.swe_marathon_brief_entry !== "site/benchmarks/swe-marathon/index.html" ||
  manifest.lhtb_brief_entry !== "site/benchmarks/lhtb/index.html" ||
  manifest.deepswe_behavior_article_entry !== "site/benchmarks/deepswe/behavior-discovery/index.html" ||
  manifest.frontstage_entry !== "site/frontstage/index.html" ||
  manifest.installer_entry !== "site/install.sh"
) {
  throw new Error(`manifest entries mismatch: ${JSON.stringify(manifest)}`);
}
if (manifest.content_sources?.public_homepage !== "apps/presentation/site") {
  throw new Error(`manifest homepage source mismatch: ${JSON.stringify(manifest.content_sources)}`);
}
if (manifest.content_sources?.swe_marathon_brief !== "benchmark/swe-marathon") {
  throw new Error(`manifest benchmark brief source mismatch: ${JSON.stringify(manifest.content_sources)}`);
}
if (manifest.content_sources?.lhtb_brief !== "benchmark/LHTB/studies/five-arm-gpt56sol-max") {
  throw new Error(`manifest LHTB brief source mismatch: ${JSON.stringify(manifest.content_sources)}`);
}
if (
  manifest.content_sources?.deepswe_behavior_article !==
  "benchmark/deepswe/behavior-discovery/index.html"
) {
  throw new Error(`manifest DeepSWE behavior source mismatch: ${JSON.stringify(manifest.content_sources)}`);
}
if (manifest.content_sources?.installer_script !== "scripts/install-from-github.sh") {
  throw new Error(`manifest installer source mismatch: ${JSON.stringify(manifest.content_sources)}`);
}
const homepageEvidenceAssets = manifest.content_sources?.homepage_evidence_assets ?? [];
for (const assetPath of [
  "docs/assets/long-running-loop-openviking-trajectory.png",
  "docs/assets/long-running-loop-ml-experiment-trajectory.png",
]) {
  if (!homepageEvidenceAssets.includes(assetPath)) {
    throw new Error(`manifest homepage evidence source mismatch: ${JSON.stringify(homepageEvidenceAssets)}`);
  }
}
if (manifest.public_boundary.write_api !== false || manifest.public_boundary.live_registry_state !== false) {
  throw new Error(`manifest public boundary is too permissive: ${JSON.stringify(manifest.public_boundary)}`);
}
if (manifest.public_boundary.primary_content_is_showcase_catalog !== true) {
  throw new Error("manifest must declare showcase catalog as the primary public content source");
}
if (manifest.content_sources?.primary_public_story !== "docs/showcases/showcase-catalog.json") {
  throw new Error(`manifest primary content source mismatch: ${JSON.stringify(manifest.content_sources)}`);
}
const interactivePages = manifest.content_sources?.interactive_case_pages ?? [];
if (!interactivePages.includes("docs/showcases/cases/0619-dynamic-workflow-hardware-agent.html")) {
  throw new Error(`share bundle did not include the hardware-agent interactive page: ${JSON.stringify(interactivePages)}`);
}
for (const pagePath of [
  "docs/showcases/index.html",
  "docs/showcases/index.en.html",
  "docs/showcases/cases/0624-pr-issue-auto-fix.html",
  "docs/showcases/cases/0624-pr-issue-auto-fix.en.html",
  "docs/showcases/cases/0619-dynamic-workflow-hardware-agent.en.html",
]) {
  if (!interactivePages.includes(pagePath)) {
    throw new Error(`share bundle did not include expected showcase page ${pagePath}: ${JSON.stringify(interactivePages)}`);
  }
  assertExists(resolve(siteDir, pagePath));
}
assertExists(resolve(siteDir, "docs/showcases/cases/0619-dynamic-workflow-hardware-agent.html"));
const hardwareCaseHtml = await readFile(resolve(siteDir, "docs/showcases/cases/0619-dynamic-workflow-hardware-agent.html"), "utf8");
if (!hardwareCaseHtml.includes("loopx 在芯片开发任务上的实践") || !hardwareCaseHtml.includes("VeeR EH1")) {
  throw new Error("copied hardware-agent interactive page is missing expected public case content");
}
if (manifest.content_sources?.live_status_feed !== false) {
  throw new Error("public share bundle must not declare a live status feed content source");
}

const fakePrivateTrapFixture = await readFile(privateTrapFixturePath, "utf8");
const fakePrivateTrapMarkers = collectFakePrivateMarkers(fakePrivateTrapFixture);
if (fakePrivateTrapMarkers.length < 6) {
  throw new Error("fake-private frontstage trap fixture is too weak");
}
for (const path of await collectGeneratedTextFiles(outDir)) {
  const text = await readFile(path, "utf8");
  assertNoLeak(text, path);
  const leakedTrapMarkers = fakePrivateTrapMarkers.filter((marker) => text.includes(marker));
  if (leakedTrapMarkers.length) {
    throw new Error(`${path} leaked fake-private frontstage trap markers: ${leakedTrapMarkers.join(", ")}`);
  }
}

const readmeText = await readFile(resolve(outDir, "README.md"), "utf8");
if (readmeText.includes("?statusUrl=")) {
  throw new Error("share bundle README must not publish a statusUrl-loaded frontstage link");
}
if (!readmeText.includes("docs/showcases/showcase-catalog.json")) {
  throw new Error("share bundle README must name the showcase catalog as the primary story source");
}
if (!readmeText.includes("frontstage/")) {
  throw new Error("share bundle README must publish the frontstage showcase entry");
}
if (!readmeText.includes("benchmarks/swe-marathon/")) {
  throw new Error("share bundle README must publish the SWE-Marathon research brief entry");
}
if (!readmeText.includes("benchmarks/lhtb/")) {
  throw new Error("share bundle README must publish the LHTB research brief entry");
}
if (!readmeText.includes("benchmarks/deepswe/behavior-discovery/")) {
  throw new Error("share bundle README must publish the DeepSWE behavior article entry");
}

console.log("frontstage-share-bundle-smoke: ok");
