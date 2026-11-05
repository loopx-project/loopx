// Packaged reader over the real File/SQLite Chat/Inbox owners. Synthetic source
// data and a server-side gate only; no intercepted inventory or model execution.
import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {once} from "node:events";
import {mkdir, writeFile} from "node:fs/promises";
import {resolve} from "node:path";
import {createInterface} from "node:readline";
import type {Browser, Page} from "playwright";
import {testPythonCommand} from "../../../../scripts/test-python.mjs";
import {launchBrowser, loadPlaywright} from "../../../../examples/dashboard-browser-smoke-support.mjs";

const repo = resolve(import.meta.dirname, "../../../..");
const output = resolve(repo, "output/playwright/delegation-cancel");
await mkdir(output, {recursive: true});
const results: unknown[] = [];
for (const storage of ["file", "sqlite"]) {
  const path = resolve(repo, "examples/personal-workspace-browser/steward_requests_fixture.py");
  const command = testPythonCommand(`import runpy; runpy.run_path(${JSON.stringify(path)}, run_name="__main__")`, {repoRoot: repo});
  const child = spawn(command.command, [...command.args, "--status-file", resolve(repo, "examples/status.example.json"),
    "--requests", "61", "--long-history", "--canonical-storage", storage,
    ...(process.env.LOOPX_CANCEL_ASSETS ? ["--assets-dir", process.env.LOOPX_CANCEL_ASSETS] : [])],
  {cwd: repo, env: {...process.env, LOOPX_USAGE_PING: "0"}, stdio: ["pipe", "pipe", "pipe"]});
  const exited = once(child, "exit");
  let stderr = "";
  child.stderr.on("data", data => stderr += data);
  type Event = {event: string; origin: string; posts: number; source_unchanged: boolean; chat_unchanged: boolean};
  const queue: Event[] = [], waiting: {kind: string; receive: (event: Event) => void}[] = [];
  createInterface({input: child.stdout}).on("line", line => {
    try {
      const value: Event = JSON.parse(line), index = waiting.findIndex(item => item.kind === value.event);
      if (index >= 0) waiting.splice(index, 1)[0]!.receive(value); else queue.push(value);
    }
    catch { /* Owner startup output is not a fixture event. */ }
  });
  const next = (kind: string) => new Promise<Event>((accept, reject) => {
    const timer = setTimeout(() => reject(Error(`Fixture timeout: ${stderr}`)), 90000);
    const receive = (value: Event) => {clearTimeout(timer); accept(value);};
    const index = queue.findIndex(item => item.event === kind);
    if (index >= 0) receive(queue.splice(index, 1)[0]!); else waiting.push({kind, receive});
  });
  const fixture = async (action: string) => {
    child.stdin.write(action + "\n"); return next(action);
  };
  let browser: Browser | undefined, page: Page | undefined;
  try {
    const ready = await next("ready");
    browser = await launchBrowser(loadPlaywright().chromium);
    page = await browser!.newPage({viewport: {width: 1440, height: 1000}});
    await page.addInitScript(() => {if (!localStorage.getItem("loopx-pw-locale")) localStorage.setItem("loopx-pw-locale", "zh-CN");});
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(ready.origin + "/chat/");
    await page.getByRole("navigation", {name: "管家视图"}).getByRole("button", {name: /^(Chat|对话)$/}).click();
    await page.getByText("复核迁移说明中的示例", {exact: true}).filter({visible: true}).first().waitFor();
    const before = await fixture("inspect");
    const writes: string[] = [];
    page.on("request", request => {if (request.method() !== "GET") writes.push(request.url());});
    const trigger = page.getByRole("button", {name: /委派，.*条待跟进/});
    const dialog = page.getByRole("dialog", {name: "委派", exact: true});
    const settled = () => page!.waitForFunction(() => document.querySelector(".conversation-requests-body")?.getAttribute("aria-busy") === "false");
    const rows = async (count: number) => {
      await settled(); await page!.waitForFunction(expected => document.querySelectorAll(".conversation-request-row").length === expected, count);
    };
    const cancel = dialog.getByRole("button", {name: "取消读取委派", exact: true});
    const refresh = dialog.getByRole("button", {name: "刷新委派", exact: true});
    const held = () => next("inventory-held");
    const release = async () => {await fixture("release"); await next("inventory-finished");};
    const status = dialog.locator(".conversation-requests-body > [role=status]");
    const snapshot = () => dialog.locator(".conversation-request-row > summary").allTextContents();
    const search = async (text: string) => {
      await dialog.getByRole("searchbox", {name: "搜索委派"}).fill(text);
      await dialog.getByRole("button", {name: "搜索", exact: true}).click();
    };
    await fixture("hold"); await trigger.click(); await held();
    if (process.env.LOOPX_CANCEL_BEFORE === "1") {
      await page.screenshot({path: resolve(output, `before-${storage}-desktop.png`)});
      await page.setViewportSize({width: 390, height: 844});
      await page.screenshot({path: resolve(output, `before-${storage}-narrow.png`)});
      continue;
    }
    // No successful page exists yet. Cancellation is neither empty success nor
    // a source error; the same dialog and keyboard focus survive it.
    assert.equal(await cancel.count(), 1, "A stalled read needs an in-place cancellation control");
    await cancel.focus(); await cancel.press("Enter"); await settled();
    assert.equal(await dialog.isVisible(), true);
    assert.equal(await status.innerText(), "已取消读取。");
    assert.equal(await dialog.locator(".conversation-request-row").count(), 0);
    assert.equal(await dialog.getByRole("alert").count(), 0);
    assert.equal(await refresh.evaluate(node => node === document.activeElement), true);
    await fixture("continue"); await refresh.press("Enter"); await rows(12);
    const firstPage = await snapshot();
    await release();
    assert.deepEqual(await snapshot(), firstPage);

    // Switching views keeps the published source visible. The cancelled
    // requested filter remains directly retryable, even when it is unchanged.
    await fixture("hold"); await dialog.getByRole("button", {name: "全部记录", exact: true}).click(); await held();
    await page.screenshot({path: resolve(output, `loading-${storage}-desktop.png`)});
    await cancel.click(); await settled(); assert.deepEqual(await snapshot(), firstPage);
    assert.match(await status.innerText(), /显示上次记录/);
    await fixture("continue"); await dialog.getByRole("button", {name: "全部记录", exact: true}).click(); await rows(12);
    assert.equal(await dialog.getByRole("button", {name: "全部记录", exact: true}).getAttribute("aria-pressed"), "true");
    await release();

    // A late cancelled search must never overwrite a newer query's real page.
    await fixture("hold"); await search("核对账单导出的字段映射"); await held(); await cancel.click(); await settled();
    assert.equal(await dialog.locator(".conversation-request-row").count(), 0);
    await fixture("continue"); await dialog.getByRole("button", {name: "清空", exact: true}).click(); await rows(12);
    await release();
    assert.deepEqual(await snapshot(), firstPage);

    // More and a multi-window refresh retain all already published rows and
    // an expanded original, rather than publishing a half-read snapshot.
    const more = dialog.getByRole("button", {name: "加载下一页", exact: true});
    await fixture("hold-window"); await more.click(); await held(); await cancel.click(); await rows(12);
    await fixture("continue"); await more.click(); await rows(24); await release();
    for (const count of [36, 48, 60, 61]) {await more.click(); await rows(count);}
    const original = dialog.locator(".conversation-request-row").filter({has: page.getByText("核对账单导出的字段映射", {exact: true})}).first();
    await original.locator(":scope > summary").click();
    await original.getByText("原对话", {exact: true}).click();
    const articles = original.locator("article"); await articles.nth(1).waitFor();
    const body = "核对账单导出的字段映射\n\n" + "完整原始要求。Original evidence. ".repeat(600) + "完整正文结束。";
    assert.equal(await articles.first().locator(":scope > .personal-md").innerText(), body);
    const allRows = await snapshot();
    await fixture("hold-window"); await refresh.click(); await held();
    await cancel.focus(); await cancel.press("Enter"); await rows(61);
    assert.deepEqual(await snapshot(), allRows);
    assert.equal(await articles.first().locator(":scope > .personal-md").innerText(), body);
    assert.equal(await original.getByText("原对话", {exact: true}).evaluate(node => node.parentElement!.hasAttribute("open")), true);
    await page.screenshot({path: resolve(output, `cancelled-${storage}-desktop.png`)});
    await page.setViewportSize({width: 390, height: 844});
    assert.ok(await dialog.evaluate(node => node.scrollWidth <= node.clientWidth + 1));
    await page.screenshot({path: resolve(output, `cancelled-${storage}-narrow.png`)});
    await fixture("continue"); await refresh.press("Enter"); await rows(61); await release();
    assert.equal(await status.count(), 0);

    // Source loss still withholds content. Cancellation must not turn that
    // refusal into empty success or keep another source's previous records.
    await fixture("local-source-unavailable"); await refresh.click(); await dialog.getByRole("alert").waitFor();
    assert.equal(await dialog.locator(".conversation-request-row").count(), 0);
    await fixture("local-source-recover"); await dialog.getByRole("button", {name: "重试", exact: true}).click(); await rows(12);
    const after = await fixture("inspect");
    assert.equal(after.source_unchanged, true); assert.equal(after.chat_unchanged, true);
    assert.equal(after.posts, before.posts); assert.deepEqual(writes, []); assert.deepEqual(errors, []);
    await page.keyboard.press("Escape");
    assert.equal(await trigger.evaluate(node => node === document.activeElement), true);
    if (storage === "sqlite") {
      await page.evaluate(() => localStorage.setItem("loopx-pw-locale", "en")); await page.reload();
      await page.getByRole("button", {name: /Delegations,.*awaiting follow-up/}).click(); await rows(12);
      const english = page.getByRole("dialog", {name: "Delegations", exact: true});
      await fixture("hold"); await english.getByRole("button", {name: "All records", exact: true}).click(); await held();
      await page.screenshot({path: resolve(output, "loading-en-narrow.png")});
      await english.getByRole("button", {name: "Cancel delegation read", exact: true}).press("Enter"); await rows(12);
      assert.match(await english.locator(".conversation-requests-body > [role=status]").innerText(), /Read cancelled.*previous records/);
      await page.screenshot({path: resolve(output, "cancelled-en-narrow.png")});
      assert.ok(await english.evaluate(node => node.scrollWidth <= node.clientWidth + 1));
      await fixture("continue"); await english.getByRole("button", {name: "All records", exact: true}).click(); await rows(12); await release();
      assert.equal(await english.getByRole("button", {name: "All records", exact: true}).getAttribute("aria-pressed"), "true");
      await page.keyboard.press("Escape");
    }
    results.push({storage, requests: 61, originalBodyChars: [...body].length, sourceUnchanged: true,
      initial: true, filterRetry: true, staleSearch: true, more: true, multiWindow: true, sourceRecovery: true, writes: 0});
  } finally {
    // Release before shutdown: cancelled clients do not stop server work.
    child.stdin.write("release\nstop\n");
    await browser?.close();
    const [code] = await exited; assert.equal(code, 0, stderr);
  }
}
await writeFile(resolve(output, "result.json"), JSON.stringify({results}, null, 2));
console.log(JSON.stringify({ok: true, results}));
