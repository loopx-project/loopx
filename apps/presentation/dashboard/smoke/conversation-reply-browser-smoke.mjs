// Packaged UI and real SQLite/Chat HTTP; only provider/model identity is synthetic.
import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {once} from "node:events";
import {createInterface} from "node:readline";
import {mkdir} from "node:fs/promises";
import {resolve} from "node:path";
import {resolveTestPython} from "../../../../scripts/test-python.mjs";
import {launchBrowser, loadPlaywright} from "../../../../examples/dashboard-browser-smoke-support.mjs";

const repo = resolve(import.meta.dirname, "../../../..");
const output = resolve(process.env.LOOPX_REPLY_OUTPUT ?? resolve(repo, "output/playwright/conversation-reply"));
await mkdir(output, {recursive: true});
const child = spawn(resolveTestPython({repoRoot: repo}), ["-u",
  "apps/presentation/dashboard/smoke/bound-conversation-http-fixture.py", "--reply-context"],
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
try {
  const ready = await next();
  browser = await launchBrowser(loadPlaywright().chromium);
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  await page.addInitScript(() => {if (!localStorage.getItem("loopx-pw-locale")) localStorage.setItem("loopx-pw-locale", "zh-CN");});
  const errors = [], writes = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("request", request => {if (request.method() !== "GET") writes.push(new URL(request.url()).pathname);});
  const snapshot = await (await page.request.get(`${ready.origin}/api/chat/sessions/${ready.sessionId}`)).json();
  const source = snapshot.messages.find(row => row.role === "user" && row.turn_id);
  assert.ok(source.text.startsWith("Referenced message ("), "Canonical model input stays unchanged");
  await page.goto(ready.origin + "/chat/");
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
    await context.locator("summary").click();
    await page.screenshot({path: resolve(output, "default-narrow.png"), animations: "disabled"});
    await page.evaluate(() => localStorage.setItem("loopx-pw-locale", "en"));
    await page.reload();
    await page.getByRole("navigation", {name: "Manager view"}).getByRole("button", {name: /^(Chat|Conversation)$/}).click();
    await page.getByText("Reply and thread context", {exact: true}).waitFor();
    await page.screenshot({path: resolve(output, "english-narrow.png"), animations: "disabled"});
    assert.deepEqual(errors, []);
    assert.ok(!writes.some(path => /\/turns(?:\/|$)/u.test(path)), "Browsing cannot submit a Turn");
    child.stdin.write("inspect\n");
    const inspection = await next();
    assert.equal(inspection.model_turns, 0);
    assert.equal(inspection.reply_evidence_unchanged, true);
    console.log("conversation-reply-browser: passed (real bound source, original body, complete quote, bounded thread, desktop/narrow, keyboard, English and zero model Turns)");
  }
} finally {
  await browser?.close();
  child.stdin.end("stop\n");
  if (child.exitCode === null) await exited;
}
