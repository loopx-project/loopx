// Packaged UI and real SQLite/Chat HTTP; only provider/model identity is synthetic.
import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {once} from "node:events";
import {createInterface} from "node:readline";
import {mkdir, readFile} from "node:fs/promises";
import {createHash} from "node:crypto";
import {resolve} from "node:path";
import {resolveTestPython} from "../../../../scripts/test-python.mjs";
import {launchBrowser, loadPlaywright} from "../../../../examples/dashboard-browser-smoke-support.mjs";

const repo = resolve(import.meta.dirname, "../../../..");
const returnedFiles = process.argv.includes("--returned-files");
const historyImages = process.argv.includes("--history-images");
const output = resolve(process.env.LOOPX_REPLY_OUTPUT ?? resolve(repo, "output/playwright/conversation-reply"));
await mkdir(output, {recursive: true});
const child = spawn(resolveTestPython({repoRoot: repo}), ["-u",
  "apps/presentation/dashboard/smoke/bound-conversation-http-fixture.py", returnedFiles ? "--returned-files" : "--reply-context",
  ...(historyImages ? ["--history-images"] : [])],
  {cwd: repo, env: {...process.env, LOOPX_USAGE_PING: "0"}, stdio: ["pipe", "pipe", "pipe"]});
const exited = once(child, "exit"), queue = [], waiting = [];
let stderr = "", browser;
child.stderr.on("data", chunk => stderr += chunk);
createInterface({input: child.stdout}).on("line", line => {
  try {const value = JSON.parse(line); if (waiting.length) waiting.shift()(value); else queue.push(value);} catch { /* startup */ }
});
function next() {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(Error(`Fixture timeout: ${stderr}`)), 60000);
    const receive = value => {clearTimeout(timer); resolve(value);};
    if (queue.length) receive(queue.shift()); else waiting.push(receive);
  });
}
async function openWhileCheckingOriginalConversation(ready, page) {
  const isOriginalSource = url => url.pathname === "/api/chat/sessions"
    && url.searchParams.get("conversation_binding_id") === ready.bindingId;
  const {promise: allowSourceRead, resolve: finishSourceRead} = Promise.withResolvers();
  const holdOriginalSource = async route => {
    await allowSourceRead;
    await route.continue();
  };
  await page.route(isOriginalSource, holdOriginalSource);
  try {
    await page.goto(ready.origin + "/chat/", {waitUntil: "domcontentloaded"});
    await page.getByText("正在核验原会话…", {exact: true}).waitFor({timeout: 4_000});
    assert.equal(await page.getByTestId("runtime-readiness").count(), 0,
      "Checking the original conversation must not show machine runtime setup");
  } finally {
    finishSourceRead();
    await page.unrouteAll({behavior: "wait"});
  }
}
async function checkReturnedFiles(ready, page, errors, writes) {
  const snapshot = await (await page.request.get(`${ready.origin}/api/chat/sessions/${ready.sessionId}`)).json();
  const answer = snapshot.messages.find(row => row.message_id === ready.fileMessageId);
  assert.ok(answer.text.includes("Verified result"));
  await page.goto(ready.origin + "/chat/");
  await page.getByRole("navigation", {name: "管家视图"}).getByRole("button", {name: /^(Chat|对话)$/}).click();
  const timeline = page.locator(".personal-channel-timeline");
  await timeline.getByText("结果已核验 / Verified result", {exact: true}).waitFor();
  await page.waitForFunction(() => document.body.innerText.includes("已连接"));
  const baseline = process.env.LOOPX_REPLY_BEFORE === "1";
  if (baseline) {
    assert.equal(answer.attachments?.length ?? 0, 0);
    assert.equal(await timeline.locator("a[download]").count(), 0);
  } else {
    assert.equal(answer.attachments.length, ready.files.length);
    assert.equal(await timeline.locator("a[download]").count(), ready.files.length);
    assert.equal(await timeline.locator("img[alt='source.bin']").count(), 0);
    for (const file of ready.files) {
      const link = timeline.getByRole("link", {name: `下载文件：${file.name}`, exact: false});
      await link.focus();
      const completed = page.waitForEvent("download");
      await page.keyboard.press("Enter");
      const download = await completed;
      assert.equal(download.suggestedFilename(), file.name);
      const raw = await readFile(await download.path());
      assert.equal(raw.length, file.size);
      assert.equal(createHash("sha256").update(raw).digest("hex"), file.sha256);
    }
  }
  await page.screenshot({path: resolve(output, `${baseline ? "before" : "after"}-files-desktop.png`), animations: "disabled"});
  const opened = page.context().waitForEvent("page");
  await timeline.getByRole("link", {name: "单独阅读完整答复"}).click();
  const report = await opened;
  report.on("pageerror", error => errors.push(error.message));
  await report.getByRole("heading", {name: "结果已核验 / Verified result", exact: true}).waitFor();
  assert.equal(await report.locator("a[download]").count(), baseline ? 0 : ready.files.length);
  await report.screenshot({path: resolve(output, `${baseline ? "before" : "after"}-files-reader.png`), animations: "disabled"});
  if (!baseline) {
    for (const file of ready.files) {
      const completed = report.waitForEvent("download");
      await report.getByRole("link", {name: `下载文件：${file.name}`, exact: false}).click();
      const download = await completed;
      assert.equal(download.suggestedFilename(), file.name);
      assert.equal(createHash("sha256").update(await readFile(await download.path())).digest("hex"), file.sha256);
    }
    await report.setViewportSize({width: 390, height: 844});
    assert.ok(await report.locator("body").evaluate(node => node.scrollWidth <= innerWidth + 1));
    await report.screenshot({path: resolve(output, "after-files-reader-narrow.png"), animations: "disabled"});
    await page.setViewportSize({width: 390, height: 844});
    await timeline.locator("a[download]").first().scrollIntoViewIfNeeded();
    assert.ok(await page.locator("body").evaluate(node => node.scrollWidth <= innerWidth + 1));
    await page.screenshot({path: resolve(output, "after-files-narrow.png"), animations: "disabled"});
    await report.evaluate(() => localStorage.setItem("loopx-pw-locale", "en"));
    await report.reload();
    await report.getByRole("link", {name: "Download file: source.bin", exact: false}).waitFor();
    await report.screenshot({path: resolve(output, "after-files-reader-english.png"), animations: "disabled"});
    const url = ready.origin + answer.attachments[0].data_url;
    assert.equal((await page.request.get(url.replace(ready.sessionId, "unrelated-session"))).status(), 404);
    assert.equal((await page.request.get(url, {headers: {Origin: "https://unrelated.example"}})).status(), 403);
  }
  child.stdin.write("inspect\n");
  const inspection = await next();
  assert.equal(inspection.model_turns, 0);
  assert.equal(inspection.provider_sends, 1);
  assert.equal(inspection.reply_evidence_unchanged, true);
  assert.deepEqual(errors, []);
  assert.ok(!writes.some(path => /\/turns(?:\/|$)/u.test(path)), "Reading cannot submit a Turn");
  console.log(baseline ? "returned-files-browser: baseline reproduced (published files omitted from history and full reader; zero model Turns or repeat sends)"
    : "returned-files-browser: passed (real publisher, bound source, original history/reader, byte-exact downloads, desktop/narrow, keyboard, English, zero model Turns or repeat sends)");
}

async function checkHistoryImages(ready, page, errors, writes) {
  const {old, current} = ready.historyImages;
  const requests = [];
  const responses = [];
  page.on("request", request => {
    const url = new URL(request.url());
    if (url.origin === ready.origin && url.pathname.includes("/images/")) requests.push(url.pathname);
  });
  page.on("response", response => {
    const url = new URL(response.url());
    if (url.origin === ready.origin && url.pathname.includes(`/messages/${old}/images/`))
      responses.push({path: url.pathname, status: response.status()});
  });
  const requested = id => requests.filter(path => path.includes(`/messages/${id}/images/`)).length;
  const timeline = page.locator(".personal-channel-timeline");
  const image = name => timeline.locator(`img[alt="${name}.png"]`);
  await page.goto(ready.origin + "/chat/");
  await page.getByRole("navigation", {name: "管家视图"}).getByRole("button", {name: /^(Chat|对话)$/}).click();
  await timeline.getByText("Latest original result after archived images.", {exact: true}).waitFor();
  await page.waitForFunction(() => document.querySelector('img[alt="latest-image.png"]')?.naturalWidth === 3);
  assert.equal(await image("archived-image").evaluate(node => node.naturalWidth), 0);
  assert.equal(requested(old), 0, "Opening the latest conversation must not request the offscreen image");
  assert.equal(requested(current), 1);
  await image("archived-image").scrollIntoViewIfNeeded();
  const oldMessage = timeline.locator(`article[data-message-id="${old}"]`);
  const retry = oldMessage.getByRole("button", {name: "重试图片"});
  await retry.waitFor();
  assert.equal(requested(old), 1, "Scrolling requests only the selected original image");
  assert.deepEqual(responses.map(response => response.status), [503]);
  await retry.click();
  await page.waitForFunction(() => document.querySelector('img[alt="archived-image.png"]')?.naturalWidth === 4);
  assert.equal(requested(old), 2, "An inline retry reads the same original image without leaving the conversation");
  assert.deepEqual(responses.map(response => response.status), [503, 200]);
  assert.equal(responses[0].path, responses[1].path, "Retry must preserve the original message and content digest");

  requests.length = 0;
  const focus = new URL(ready.origin + "/chat/");
  focus.search = new URLSearchParams({view: "conversation", conversationBinding: ready.bindingId,
    conversationSession: ready.sessionId, messageSession: ready.sessionId, message: old});
  await page.goto(focus.href);
  await page.waitForFunction(id => document.activeElement?.getAttribute("data-message-id") === id
    && document.querySelector('img[alt="archived-image.png"]')?.naturalWidth === 4, old);
  assert.equal(requested(old), 1, "Opening an original message link positions and loads that image");
  assert.equal(requested(current), 0, "An unrelated offscreen image stays deferred when reading a link");
  const oldUrl = await image("archived-image").getAttribute("src");
  child.stdin.write("history-image-refresh\n");
  assert.equal((await next()).history_image_refreshed, old);
  assert.equal((await page.request.get(ready.origin + oldUrl)).status(), 404);
  await page.getByRole("button", {name: "刷新状态", exact: true}).click();
  await page.waitForFunction(previous => {
    const image = document.querySelector('img[alt="archived-image.png"]');
    return Boolean(image?.getAttribute("src") && image.getAttribute("src") !== previous && image.naturalWidth === 3);
  }, oldUrl);
  assert.equal(await oldMessage.count(), 1, "The refreshed image remains in the original message");
  assert.deepEqual(errors, []);
  assert.ok(!writes.some(path => /\/turns(?:\/|$)/u.test(path)), "Reading history cannot submit a Turn");
  child.stdin.write("inspect\n");
  const inspection = await next();
  assert.equal(inspection.model_turns, 0);
  assert.equal(inspection.reply_evidence_unchanged, true);
  console.log("history-images-browser: passed (latest, scroll, inline retry and original message link; zero model Turns)");
}

try {
  const ready = await next();
  browser = await launchBrowser(loadPlaywright().chromium);
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  await page.addInitScript(() => {if (!localStorage.getItem("loopx-pw-locale")) localStorage.setItem("loopx-pw-locale", "zh-CN");});
  const errors = [], writes = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("request", request => {if (request.method() !== "GET") writes.push(new URL(request.url()).pathname);});
  if (historyImages) {
    await checkHistoryImages(ready, page, errors, writes);
  } else if (returnedFiles) {
    await checkReturnedFiles(ready, page, errors, writes);
  } else {
  const snapshot = await (await page.request.get(`${ready.origin}/api/chat/sessions/${ready.sessionId}`)).json();
  const source = snapshot.messages.find(row => row.role === "user" && row.turn_id);
  assert.ok(source.text.startsWith("Referenced message ("), "Canonical model input stays unchanged");
  if (process.env.LOOPX_REPLY_BEFORE === "1") {
    await page.goto(ready.origin + "/chat/");
  } else {
    await openWhileCheckingOriginalConversation(ready, page);
  }
  await page.getByRole("navigation", {name: "管家视图"}).getByRole("button", {name: /^(Chat|对话)$/}).click();
  const body = "请比较这次修改，并保留原话题中的限制。Compare this change against the original constraints.";
  const parent = "原要求：保留已有结果和失败记录。Original requirement: retain results and failed attempts.";
  if (process.env.LOOPX_REPLY_BEFORE === "1") {
    await page.getByText(source.text, {exact: true}).waitFor();
    await page.screenshot({path: resolve(output, "before-desktop.png"), animations: "disabled"});
    assert.equal(await page.locator(".conversation-reply-context").count(), 0);
    console.log("BASELINE: original request renders the model envelope without a reply reader.");
  } else {
    await page.locator(".personal-channel-timeline").getByText("已保留原要求，当前核验结果待进一步确认。", {exact: true}).waitFor();
    assert.equal(await page.locator(".personal-channel-timeline").getByText(body, {exact: true}).count(), 1,
      "The primary user message shows the original body without the model envelope");
    assert.equal(source.reply_context.source_text, body);
    assert.equal(await page.getByText(source.text, {exact: true}).count(), 0);
    const context = page.locator(".personal-channel-timeline .conversation-reply-context");
    assert.equal(await context.count(), 1);
    assert.equal(await context.getAttribute("open"), null);
    await page.screenshot({path: resolve(output, "default-desktop.png"), animations: "disabled"});
    await context.locator("summary").focus();
    await page.keyboard.press("Enter");
    await context.getByText(parent, {exact: true}).waitFor();
    assert.equal(await context.locator("blockquote").count(), 1);
    assert.equal(await context.locator("section").count(), 1, "The exact parent is not repeated in thread history");
    await context.getByText("话题上下文为节选", {exact: true}).waitFor();
    await page.waitForFunction(() => {
      const rect = document.querySelector(".conversation-reply-context summary").getBoundingClientRect();
      return rect.top >= 74 && rect.bottom < innerHeight - 100;
    });
    await page.screenshot({path: resolve(output, "expanded-desktop.png"), animations: "disabled"});
    await page.setViewportSize({width: 390, height: 844});
    await context.locator("summary").scrollIntoViewIfNeeded();
    assert.ok(await page.locator("body").evaluate(node => node.scrollWidth <= innerWidth + 1));
    await page.screenshot({path: resolve(output, "expanded-narrow.png"), animations: "disabled"});
    await context.locator("blockquote ol").nth(1).scrollIntoViewIfNeeded();
    await page.screenshot({path: resolve(output, "expanded-narrow-steps.png"), animations: "disabled"});
    await context.locator("summary").click();
    await page.screenshot({path: resolve(output, "default-narrow.png"), animations: "disabled"});
    await page.evaluate(() => localStorage.setItem("loopx-pw-locale", "en"));
    await page.reload();
    await page.getByRole("navigation", {name: "Manager view"}).getByRole("button", {name: /^(Chat|Conversation)$/}).click();
    await page.getByText("Reply and thread context", {exact: true}).waitFor();
    await page.screenshot({path: resolve(output, "english-narrow.png"), animations: "disabled"});
    const quote = context.locator("blockquote");
    assert.deepEqual(await quote.locator("ol").evaluateAll(lists => lists.map(list => list.start)),
      [1, 2, 3, 7, 0], "Paragraph-separated steps and independent lists retain their starting numbers");
    assert.equal(await quote.locator("ol").nth(3).locator("li").count(), 2,
      "Later source markers in one consecutive list do not split its sequence");
    assert.equal(await quote.locator("ul li").count(), 2, "Unordered lists stay unordered");
    assert.equal(await quote.locator("pre code").textContent(), "9. This is literal code, not another list.");
    assert.deepEqual(errors, []);
    assert.ok(!writes.some(path => /\/turns(?:\/|$)/u.test(path)), "Browsing cannot submit a Turn");
    child.stdin.write("inspect\n");
    const inspection = await next();
    assert.equal(inspection.model_turns, 0);
    assert.equal(inspection.reply_evidence_unchanged, true);
    console.log("conversation-reply-browser: passed (real bound source, original body, complete quote, bounded thread, desktop/narrow, keyboard, English and zero model Turns)");
  }
  }
} finally {
  await browser?.close();
  child.stdin.end("stop\n");
  if (child.exitCode === null) await exited;
}
