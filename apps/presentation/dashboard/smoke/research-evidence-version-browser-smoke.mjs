// Exercise a saved original and its newly available language-specific result
// through the packaged reader. Both snapshots and citation excerpts come from
// the native test backend; interception controls only when the result arrives.
import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {once} from "node:events";
import {mkdtemp, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {resolve} from "node:path";
import {createInterface} from "node:readline";
import {resolveTestPython} from "../../../../scripts/test-python.mjs";
import {launchBrowser, loadPlaywright} from "../../../../examples/dashboard-browser-smoke-support.mjs";

const repo = resolve(import.meta.dirname, "../../../..");
const root = await mkdtemp(resolve(tmpdir(), "loopx-evidence-version-"));
const assets = process.env.LOOPX_RESEARCH_EVIDENCE_ASSETS ?? resolve(repo, "loopx/web/chat");
const servers = [];
let browser;
let releaseOriginal;
try {
  const capture = JSON.parse(await readFile(resolve(import.meta.dirname, "../src/data/fixtures/research-native-readback.json"), "utf8"));
  const analysis = capture.analyses[0];
  analysis.config = {model: "fixture", effort: "max", service_tier: "fast", research_synthesis: false};
  const observation = (line, verdict, reason) => ({
    assessments: [{episode_id: "search-episode", metric: "benefit_test", verdict, reason,
      citations: [{source_id: "facts", first_line: line, last_line: line}]}],
    limitations: ["Synthetic reader fixture."],
  });
  analysis.outputs = {original: observation(1, "no", "The saved original examines the first observation.")};
  const initial = await start("original", capture);
  analysis.outputs.zh = {...observation(3, "yes", "当前分析判读后续观测。"),
    episode_questions: [{episode_id: "search-episode", question: "后续观测是否检验了任务进展？"}]};
  const ready = await start("localized", capture);
  const endpoints = new URLSearchParams({goal_id: capture.goal_id, output_language: "zh"});
  const analysisPath = "/api/chat/research-analysis";
  const getPack = async backend => (await (await fetch(new URL(analysisPath + "?" + endpoints, backend))).json()).pack;
  const [before, after] = await Promise.all([getPack(initial), getPack(ready)]);
  assert.equal(before.pack_id, after.pack_id);
  assert.equal(before.analysis_key, after.analysis_key, "Same requested analysis identity");
  assert.equal(before.result, null);
  assert.equal(after.result.output_language, "zh");

  browser = await launchBrowser(loadPlaywright().chromium);
  const page = await browser.newPage({locale: "zh-CN", viewport: {width: 1200, height: 900}});
  let localizedReady = false;
  let holdOriginal = false;
  await page.route(/\/api\/chat\/research-analysis(?:\/citation)?\?/, async route => {
    const url = new URL(route.request().url());
    if (url.searchParams.get("output_language") === "zh" && localizedReady) {
      return route.fulfill({response: await route.fetch({url: new URL(url.pathname + url.search, ready).href})});
    }
    if (url.pathname.endsWith("/citation") && holdOriginal) {
      holdOriginal = false;
      await new Promise(resolveOriginal => {releaseOriginal = resolveOriginal;});
    }
    return route.continue();
  });
  const citation = page.locator(".analysis-source");
  const citationButton = page.locator(".analysis-episode[open] .analysis-citations button");
  const research = new URL("/chat/", initial);
  research.search = new URLSearchParams({goalId: capture.goal_id, view: "research",
    research_analysis: capture.goal_id, research_pack: before.pack_id});
  const refresh = page.getByRole("button", {name: "刷新分析结果", exact: true});
  const showEpisode = () => page.locator(".analysis-episode > summary").click();
  async function openOriginal() {
    localizedReady = false;
    await page.goto(research.href);
    await page.getByText("显示已保存的原文分析", {exact: false}).waitFor();
    await showEpisode();
  }
  async function refreshToLocalized() {
    localizedReady = true;
    await refresh.click();
    await page.getByText("显示已保存的原文分析", {exact: false}).waitFor({state: "hidden"});
    await page.getByRole("button", {name: /收益检验 · 1/}).first().waitFor();
    assert.equal(await citation.count(), 0, "No prior version citation content or loading state in the new reading");
  }

  await openOriginal();
  await citationButton.click();
  await citation.getByText(/candidate-one/).waitFor();
  await refreshToLocalized();
  await showEpisode();
  await citationButton.click();
  await citation.getByText(/candidate-three/).waitFor();

  await openOriginal();
  holdOriginal = true;
  const requested = page.waitForRequest(request => request.url().includes("/citation?"));
  await citationButton.click();
  await requested;
  await citation.getByText("正在读取引用原文…", {exact: true}).waitFor();
  await refreshToLocalized();
  const late = page.waitForResponse(response => response.url().includes("/citation?") && response.ok());
  assert.equal(typeof releaseOriginal, "function");
  releaseOriginal();
  await (await late).finished();
  await page.evaluate(() => new Promise(resolvePaint => requestAnimationFrame(() => requestAnimationFrame(resolvePaint))));
  assert.equal(await citation.count(), 0, "Late original response cannot reopen the previous evidence");

  // Open a saved chapter through the same workspace router used by the tabs
  // and parent links. Browser Back must keep the previous visit's position.
  localizedReady = false;
  const child = JSON.parse(await readFile(resolve(import.meta.dirname, "../src/data/fixtures/case-study-child.json"), "utf8"));
  const parent = {goal_id: child.parent_goal_id, display_name: "Benchmark", events: [], analyses: []};
  const second = structuredClone(child.analyses[0]);
  second.title = "另一版解读";
  second.case_study.sections[1].citations[0].first_line = 2;
  child.analyses.push(second);
  const study = await start("study", child, parent);
  const studyRead = await (await fetch(new URL(analysisPath + "?" + new URLSearchParams({goal_id: child.goal_id}), study))).json();
  const studyPack = studyRead.pack;
  const otherPack = studyRead.catalog.packs.find(pack => pack.pack_id !== studyPack.pack_id);
  assert(otherPack, "Same Goal has two saved versions");
  const chapter = studyPack.case_study.sections[1];
  const address = new URL("/chat/", study);
  address.search = new URLSearchParams({goalId: child.goal_id, view: "research", research_goal: child.goal_id,
    research_reading: "case", research_analysis_goal: child.goal_id, research_pack: studyPack.pack_id});
  address.hash = "case-section-" + studyPack.pack_id + "-" + chapter.id;
  const atChapter = () => page.waitForFunction(id => document.activeElement?.id === id, address.hash.slice(1));
  await page.goto(address.href);
  await atChapter();
  const views = page.getByRole("group", {name: "研究视图", exact: true});
  const historyView = views.getByRole("button", {name: "研究历程", exact: true});
  const caseView = views.getByRole("button", {name: "案例解读", exact: true});
  assert.equal(await caseView.getAttribute("aria-pressed"), "true");
  await historyView.press("Enter");
  await page.locator(".research-history-heading").waitFor();
  assert.equal(await historyView.getAttribute("aria-pressed"), "true");
  assert.equal(await historyView.evaluate(element => document.activeElement === element), true);
  assert.equal(new URL(page.url()).searchParams.get("research_reading"), "history");
  await page.goBack();
  await atChapter();
  await page.goForward();
  await page.locator(".research-history-heading").waitFor();
  await caseView.press("Enter");
  await atChapter();
  assert.equal(new URL(page.url()).searchParams.get("research_pack"), studyPack.pack_id);

  const sourceRequests = [];
  const quoteRoute = url => url.origin === new URL(study).origin && url.pathname.endsWith("/citation");
  let failNextQuote = false;
  await page.route(quoteRoute, route => {
    sourceRequests.push(new URL(route.request().url()).search);
    if (failNextQuote) {
      failNextQuote = false;
      return route.fulfill({status: 503, json: {ok: false, error: "Synthetic source unavailable"}});
    }
    return route.continue();
  });
  const chapterControl = page.locator(".case-sections > section").nth(1);
  await chapterControl.locator("summary").click();
  const originalLink = chapterControl.locator(".case-citation-links").getByRole("link");
  const bookmark = new URL(await originalLink.getAttribute("href"));
  assert.equal(bookmark.searchParams.get("research_pack"), studyPack.pack_id);
  const cited = chapter.citations[0];
  const expectedSource = await (await fetch(new URL(analysisPath + "/citation?" + new URLSearchParams({
    goal_id: child.goal_id, pack_id: studyPack.pack_id, analysis_key: studyPack.analysis_key,
    source_id: cited.source_id, first_line: String(cited.first_line), last_line: String(cited.last_line),
  }), study))).json();
  assert.equal(expectedSource.source_id, cited.source_id);
  assert(expectedSource.text);
  await originalLink.press("Enter");
  await page.waitForURL(url => url.hash === bookmark.hash);
  await citation.locator("pre").first().waitFor();
  assert.equal(await citation.locator("pre").first().textContent(), expectedSource.text);
  const originalQuery = sourceRequests.at(-1);
  await page.setViewportSize({width: 390, height: 900});
  failNextQuote = true;
  await page.reload();
  await citation.getByRole("alert").waitFor();
  assert.equal(new URL(page.url()).hash, bookmark.hash);
  await citation.getByRole("button", {name: "重试读取原文"}).click();
  await citation.locator("pre").first().waitFor();
  assert.equal(await citation.locator("pre").first().textContent(), expectedSource.text);
  assert.deepEqual(sourceRequests.slice(-2), [originalQuery, originalQuery]);
  await citation.press("Escape");
  await originalLink.evaluate(element => {
    if (document.activeElement !== element) return new Promise(resolve => element.addEventListener("focus", resolve, {once: true}));
  });
  assert.equal(new URL(page.url()).hash, address.hash);
  assert.equal(await citation.count(), 0);
  // Paste the same permalink in the existing document. This exercises the
  // browser route, where neither the link's click handler nor reload runs.
  const documentStart = await page.evaluate(() => performance.timeOrigin);
  const readsBeforeReopen = sourceRequests.length;
  await page.evaluate(href => {window.location.assign(href);}, bookmark.href);
  await citation.locator("pre").first().waitFor({timeout: 6_000});
  assert.equal(await page.evaluate(() => performance.timeOrigin), documentStart);
  assert.equal(await citation.locator("pre").first().textContent(), expectedSource.text);
  assert.deepEqual(sourceRequests.slice(readsBeforeReopen), [originalQuery],
    "Reopening a closed permalink reads its original evidence exactly once");
  await citation.getByRole("button", {name: "关闭原文", exact: true}).click();
  assert.equal(new URL(page.url()).hash, address.hash);
  assert.equal(await citation.count(), 0);
  // A valid position from another pack must never fall through to this pack.
  const misplaced = new URL(bookmark);
  misplaced.searchParams.set("research_pack", otherPack.pack_id);
  const readsBeforeMismatch = sourceRequests.length;
  await page.goto(misplaced.href);
  await page.getByText("这条链接在所选解读中找不到原文。", {exact: false}).waitFor();
  assert.equal(sourceRequests.length, readsBeforeMismatch);
  assert.equal(await citation.count(), 0);
  await page.setViewportSize({width: 1200, height: 900});
  await page.goto(address.href);
  await atChapter();
  const tabs = page.locator(".personal-goal-tabs");
  await tabs.getByRole("button", {name: "任务", exact: true}).click();
  await page.waitForURL(url => url.searchParams.get("view") === "tasks");
  assert.equal(new URL(page.url()).hash, address.hash);
  await tabs.getByRole("button", {name: "研究", exact: true}).click();
  await atChapter();
  await page.locator("button.research-parent-goal").click();
  await page.waitForURL(url => url.searchParams.get("goalId") === parent.goal_id);
  function assertParentAddress() {
    const url = new URL(page.url());
    assert.equal(url.hash, "");
    assert.equal([...url.searchParams.keys()].some(key => key.startsWith("research_")), false,
      "The parent's address belongs to its own research");
  }
  assertParentAddress();
  await page.locator(".research-history-heading").waitFor();
  assert.equal(await views.count(), 0, "A Goal without a saved study keeps its ordinary history");
  await page.goBack();
  await atChapter();
  assert.equal(new URL(page.url()).searchParams.get("research_pack"), studyPack.pack_id);
  await page.reload();
  await atChapter();
  await page.goForward();
  await page.waitForURL(url => url.searchParams.get("goalId") === parent.goal_id);
  assertParentAddress();
  console.log("research evidence version: study and history navigation, saved excerpts and chapter position restored");
} finally {
  releaseOriginal?.();
  await browser?.close();
  await Promise.all(servers.map(async child => {
    if (child.exitCode !== null || child.signalCode !== null) return;
    const closed = once(child, "exit");
    child.kill("SIGTERM");
    await closed;
  }));
  await rm(root, {recursive: true, force: true});
}

async function start(name, ...captures) {
  const fixtures = await Promise.all(captures.map(async (capture, index) => {
    const path = resolve(root, name + "-" + index + ".json");
    await writeFile(path, JSON.stringify(capture));
    return path;
  }));
  const child = spawn(resolveTestPython({repoRoot: repo}), [
    "-u", "examples/personal-workspace-browser/research-history-backend.py",
    ...fixtures.flatMap(path => ["--fixture", path]), "--workspace-directory", "--assets-dir", assets,
  ], {cwd: repo, env: {...process.env, PYTHONPATH: repo}, stdio: ["ignore", "pipe", "pipe"]});
  servers.push(child);
  let error = "";
  child.stderr.on("data", chunk => {error = (error + chunk).slice(-3000);});
  const lines = createInterface({input: child.stdout});
  try {
    return await new Promise((accept, reject) => {
      const timer = setTimeout(() => reject(new Error("Research test server startup timed out: " + error)), 25_000);
      child.once("error", failure => {clearTimeout(timer); reject(failure);});
      child.once("exit", () => {clearTimeout(timer); reject(new Error("Research test server exited: " + error));});
      lines.once("line", line => {
        clearTimeout(timer);
        try {accept(JSON.parse(line).url);} catch (failure) {reject(failure);}
      });
    });
  } finally {lines.close();}
}
