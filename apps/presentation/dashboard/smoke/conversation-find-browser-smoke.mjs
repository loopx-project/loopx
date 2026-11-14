// Packaged reader, production HTTP/store, synthetic messages; no model calls.
import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {once} from "node:events";
import {createInterface} from "node:readline";
import {mkdir} from "node:fs/promises";
import {resolve} from "node:path";
import {resolveTestPython} from "../../../../scripts/test-python.mjs";
import {launchBrowser, loadPlaywright, waitForHttp} from "../../../../examples/dashboard-browser-smoke-support.mjs";

process.env.LOOPX_PERSONAL_WORKSPACE_PACKAGED = "1";
const {repoRoot, outputDir, port, startServer} = await import("../../../../examples/personal-workspace-browser/fixture.mjs");
const {openWorkspacePage} = await import("../../../../examples/personal-workspace-browser/scenario-context.mjs");
const child = spawn(resolveTestPython({repoRoot}), ["-u", "apps/presentation/dashboard/smoke/conversation-history-http-fixture.py", "--manager", "--find"],
  {cwd: repoRoot, stdio: ["pipe", "pipe", "pipe"]});
const exited = once(child, "exit"), lines = createInterface({input: child.stdout});
const iterator = lines[Symbol.asyncIterator]();
let stderr = "", browser, server, workspace;
child.stderr.on("data", data => {stderr += String(data);});
async function next() {const row = await iterator.next(); assert.equal(row.done, false, stderr); return JSON.parse(row.value);}
async function command(value) {child.stdin.write(`${value}\n`); return next();}
try {
  const {origin} = await next();
  await command("recover");
  server = await startServer();
  const url = `http://127.0.0.1:${port}/chat/?statusUrl=/status.json`;
  await waitForHttp(url);
  browser = await launchBrowser(loadPlaywright().chromium);
  for (const locale of ["en", "zh-CN"]) {
    const zh = locale === "zh-CN";
    await command("fault");
    workspace = await openWorkspacePage(browser, url, {beforeGoto(_api, page) {
      return Promise.all([page.addInitScript(value => localStorage.setItem("loopx-pw-locale", value), locale),
        page.route("**/api/chat/sessions**", async route => {
          const request = route.request(), target = new URL(request.url());
          if (!/^\/api\/chat\/sessions(?:\/(?:old|current|other-channel))?$/u.test(target.pathname)) return route.fallback();
          if (request.method() === "POST" && target.pathname === "/api/chat/sessions") {
            if (request.postDataJSON().context_kind !== "manager") return route.fallback();
            const response = await route.fetch({url: `${origin}/api/chat/sessions/current`, method: "GET", postData: undefined});
            const snapshot = await response.json();
            return route.fulfill({status: 201, json: {ok: true, resumed: true, session_id: "current", session: snapshot.session}});
          }
          assert.equal(request.method(), "GET", "Finding original messages cannot mutate the store");
          const response = await route.fetch({url: `${origin}${target.pathname}${target.search}`});
          return route.fulfill({response});
        })]);
    }});
    const {page, api} = workspace;
    await page.getByRole("navigation", {name: zh ? "管家视图" : "Manager view"}).getByRole("button", {name: /^(Chat|对话)$/}).click();
    const toggle = page.getByRole("button", {name: zh ? "查找对话" : "Find in conversation", exact: true});
    await toggle.click();
    const input = page.getByRole("searchbox", {name: zh ? "查找已加载消息" : "Find in loaded messages"});
    await input.fill("lunar");
    const bar = page.locator(".personal-conversation-find"), active = page.locator(".personal-message.is-find-match");
    await bar.getByRole("status").filter({hasText: "1 / 2"}).waitFor();
    await bar.getByText(zh ? "对话记录尚未完整读取，结果仅包含已加载消息。" : "History is incomplete; results include loaded messages only.").waitFor();
    const retained = await active.getAttribute("data-timeline-id");
    await command("recover");
    await page.getByRole("button", {name: zh ? "重试读取" : "Retry history", exact: true}).click();
    await bar.getByRole("status").filter({hasText: "2 / 3"}).waitFor();
    assert.equal(await active.getAttribute("data-timeline-id"), retained, "Backfill preserves the selected original message");
    // Reopen the timeline after the complete read to exercise folded history.
    await page.locator(".personal-goal-link").first().click();
    await page.locator(".personal-manager-link").click();
    await page.getByRole("navigation", {name: zh ? "管家视图" : "Manager view"}).getByRole("button", {name: /^(Chat|对话)$/}).click();
    await toggle.click(); await input.fill("lunar");
    await bar.getByRole("status").filter({hasText: "1 / 3"}).waitFor();
    await active.getByText("Keep the lunar evidence. 保留原始证据。", {exact: true}).waitFor();
    assert.equal(await active.getByRole("button", {name: zh ? "收起消息" : "Collapse message"}).getAttribute("aria-expanded"), "true");
    const first = await active.getAttribute("data-timeline-id");
    await input.dispatchEvent("keydown", {key: "Escape", isComposing: true});
    assert.equal(await input.count(), 1, "IME composition does not close the reader");
    await input.press("Enter");
    await bar.getByRole("status").filter({hasText: "2 / 3"}).waitFor();
    assert.notEqual(await active.getAttribute("data-timeline-id"), first);
    await input.press("Shift+Enter");
    await bar.getByRole("status").filter({hasText: "1 / 3"}).waitFor();
    await page.waitForFunction(key => document.querySelector(".is-find-match")?.getAttribute("data-timeline-id") === key, first);
    await page.getByRole("button", {name: zh ? "上一条匹配消息" : "Previous matching message"}).click();
    await bar.getByRole("status").filter({hasText: "3 / 3"}).waitFor();
    await input.fill("保留原始证据");
    await bar.getByRole("status").filter({hasText: "1 / 1"}).waitFor();
    for (const width of [1200, 390]) {
      await page.setViewportSize({width, height: 844});
      // Leave the long original expanded so the old short message can be scrolled away.
      await input.fill("Earlier public report");
      await bar.getByRole("status").filter({hasText: "1 / 1"}).waitFor();
      const inViewport = () => page.waitForFunction(() => {
        const row = document.querySelector(".is-find-match")?.getBoundingClientRect();
        const viewport = document.querySelector(".personal-channel-scroll")?.getBoundingClientRect();
        return row && viewport && row.top >= viewport.top && row.bottom <= viewport.bottom;
      }, undefined, {timeout: 5000});
      await inViewport();
      for (const key of ["Enter", "Shift+Enter", "arrow"]) {
        await page.locator(".personal-channel-scroll").evaluate(node => {node.scrollTop = node.scrollHeight;});
        assert(await active.evaluate(node => node.getBoundingClientRect().bottom < node.closest(".personal-channel-scroll").getBoundingClientRect().top), "The unique old message is outside the viewport before navigating again");
        if (key === "arrow") await bar.getByRole("button", {name: zh ? "上一条匹配消息" : "Previous matching message"}).click();
        else await input.press(key);
        await inViewport();
      }
      await input.fill("保留原始证据");
      for (const action of ["arrow", "Shift+Enter"]) {
        await active.getByRole("button", {name: zh ? "收起消息" : "Collapse message", exact: true}).click();
        await active.getByRole("button", {name: zh ? "展开完整消息" : "Read full message", exact: true}).waitFor();
        if (action === "arrow") await bar.getByRole("button", {name: zh ? "下一条匹配消息" : "Next matching message"}).click();
        else await input.press(action);
        await active.getByText("Keep the lunar evidence. 保留原始证据。", {exact: true}).waitFor({timeout: 5000});
        assert.equal(await active.getByRole("button", {name: zh ? "收起消息" : "Collapse message"}).getAttribute("aria-expanded"), "true");
      }
    }
    await page.setViewportSize({width: 1200, height: 844});
    await mkdir(outputDir, {recursive: true});
    // Short matching messages keep the useful reading context in the preview.
    await input.fill("final report");
    await bar.getByRole("status").filter({hasText: "1 / 1"}).waitFor();
    await page.getByRole("button", {name: zh ? "收起消息" : "Collapse message", exact: true}).click();
    await input.fill(""); await input.fill("final report");
    await active.getByText("Please retain LUNAR evidence in the final report.", {exact: true}).waitFor();
    await page.screenshot({path: resolve(outputDir, `conversation-find-${locale}-desktop.png`), animations: "disabled"});
    await page.setViewportSize({width: 390, height: 844});
    assert(await page.locator("body").evaluate(body => body.scrollWidth <= innerWidth + 1));
    await page.screenshot({path: resolve(outputDir, `conversation-find-${locale}-mobile.png`), animations: "disabled"});
    await input.fill("[no such literal message]");
    await bar.getByRole("status").filter({hasText: zh ? "没有匹配消息" : "No matching messages"}).waitFor();
    assert.equal(await active.count(), 0);
    assert(await page.getByRole("button", {name: zh ? "下一条匹配消息" : "Next matching message"}).isDisabled());
    await input.press("Escape");
    assert(await toggle.evaluate(button => button === document.activeElement));
    assert.equal(await input.count(), 0);
    await page.setViewportSize({width: 1512, height: 982});
    await toggle.click(); await input.fill("lunar");
    await page.locator(".personal-goal-link").first().click();
    await page.locator(".personal-manager-link").click();
    await page.getByRole("navigation", {name: zh ? "管家视图" : "Manager view"}).getByRole("button", {name: /^(Chat|对话)$/}).click();
    assert.equal(await input.count(), 0, "Changing the conversation discards its private search");
    assert.equal(api.turnRequests.length, 0);
    assert.deepEqual(await command("inspect"), {store_unchanged: true, turn_count: 0});
    await workspace.close(); workspace = null;
  }
  console.log("conversation-find: packaged HTTP/store, en/zh, full-text expansion, navigation/wrap, literal no-match, scope reset, narrow layout and zero writes passed");
} finally {
  await workspace?.close(); await browser?.close(); server?.kill("SIGTERM");
  child.stdin.end(); if (child.exitCode === null) await exited; lines.close();
}
