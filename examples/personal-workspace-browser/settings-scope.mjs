// Existing settings UI + disposable production storage backend. Discovery and
// optional capability catalogs use the shared synthetic workspace fixture.
import assert from "node:assert/strict";
import {mkdir} from "node:fs/promises";
import {resolve} from "node:path";
import {launchBrowser, loadPlaywright, waitForHttp} from "../dashboard-browser-smoke-support.mjs";
import {outputDir, packaged, port, startServer} from "./fixture.mjs";
import {fetchWorkspaceBackend, openWorkspacePage} from "./scenario-context.mjs";
import {startAuthority} from "./goal-settings-backend.mjs";

await mkdir(outputDir, {recursive: true});
const server = await startServer();
const authority = await startAuthority();
let browser, context;
let storageReads = 0, writes = 0, responseMode = "real";
let releaseOldRead, observeOldRead;
try {
  const url = `http://127.0.0.1:${port}/${packaged ? "chat/" : ""}?statusUrl=/status.json`;
  await waitForHttp(url);
  browser = await launchBrowser(loadPlaywright().chromium);
  context = await openWorkspacePage({newPage: options => browser.newPage({locale: "zh-CN", ...options})}, url, {
    beforeGoto: async (_api, page) => {
      await page.route(/\/api\/chat\/goal-(storage|ownership)(?:[/?]|$)/, async route => {
        if (route.request().method() !== "GET") writes++;
        if (new URL(route.request().url()).pathname.endsWith("goal-storage")) {
          storageReads++;
          const response = await fetchWorkspaceBackend(route, authority.url);
          if (responseMode === "delayed") {
            const gate = new Promise(resolve => {releaseOldRead = resolve;});
            observeOldRead();
            await gate;
          }
          if (responseMode !== "real") {
            const value = await response.json();
            // Hostile/partial transport projections, not claimed backend facts.
            value.current = {goal_id: responseMode === "foreign" ? "another-goal" : "multi-agent-projection",
              canonical: true, provider: "sqlite", ...(responseMode === "zero" ? {todo_count: 0, unsettled_lease_count: 0} : {})};
            await route.fulfill({status: 200, json: value});
          } else await route.fulfill({response});
        } else await route.fulfill({response: await fetchWorkspaceBackend(route, authority.url)});
      });
    },
  });
  const {page} = context;
  const settings = page.locator(".personal-settings-page");
  await page.locator(".personal-goal-link", {hasText: "Multi Agent Projection"}).click();
  await page.getByRole("button", {name: "设置", exact: true}).click();
  assert.equal(await settings.getByRole("radio", {name: "本机设置", exact: true}).isChecked(), true);
  assert.equal(await settings.getByRole("combobox", {name: "目标 Goal", exact: true}).count(), 0);
  assert.equal(await settings.getByRole("button", {name: "任务所有权", exact: true}).count(), 0);
  assert.equal(storageReads, 0, "global settings must not read the background Goal's storage");
  await page.screenshot({path: resolve(outputDir, "settings-device-desktop.png"), animations: "disabled"});
  await settings.getByRole("radio", {name: "Goal 设置", exact: true}).check();
  const target = settings.getByRole("combobox", {name: "目标 Goal", exact: true});
  assert.equal(await target.inputValue(), "", "explicit Goal scope starts without an implicit Goal");
  await settings.getByRole("button", {name: "任务所有权", exact: true}).click();
  assert.equal(storageReads, 0);
  await target.focus();
  await target.selectOption("multi-agent-projection");
  assert.equal(await target.evaluate(node => document.activeElement === node), true, "choosing a target preserves keyboard focus");
  const panel = settings.getByRole("region", {name: "Goal 数据存储"});
  await panel.getByText("1 项当前任务 · 1 项归档任务 · 1 项未结算 lease", {exact: true}).waitFor();
  await page.reload({waitUntil: "networkidle"});
  await panel.getByText("1 项当前任务 · 1 项归档任务 · 1 项未结算 lease", {exact: true}).waitFor();
  assert.equal(await target.inputValue(), "multi-agent-projection", "reload restores the explicit target");
  await page.screenshot({path: resolve(outputDir, "settings-goal-desktop.png"), animations: "disabled"});
  await target.selectOption("");
  assert.equal(await panel.count(), 0, "clearing the target unmounts the editor and its pending intent");
  await page.goBack();
  await panel.getByText("1 项当前任务 · 1 项归档任务 · 1 项未结算 lease", {exact: true}).waitFor();
  assert.equal(await target.inputValue(), "multi-agent-projection");
  responseMode = "delayed";
  const oldReadStarted = new Promise(resolve => {observeOldRead = resolve;});
  await panel.getByRole("button", {name: "读回当前存储", exact: true}).click();
  await oldReadStarted;
  await target.selectOption("");
  releaseOldRead();
  assert.equal(await panel.count(), 0, "late source responses cannot restore an editor after target change");
  responseMode = "real";
  await target.selectOption("multi-agent-projection");
  await panel.getByText("1 项当前任务 · 1 项归档任务 · 1 项未结算 lease", {exact: true}).waitFor();
  responseMode = "missing";
  await panel.getByRole("button", {name: "读回当前存储", exact: true}).click();
  await panel.getByText("任务未读取 · 未结算 lease 未读取", {exact: true}).waitFor();
  responseMode = "zero";
  await panel.getByRole("button", {name: "读回当前存储", exact: true}).click();
  await panel.getByText("0 项任务 · 0 项未结算 lease", {exact: true}).waitFor();
  responseMode = "foreign";
  await panel.getByRole("button", {name: "读回当前存储", exact: true}).click();
  await panel.getByRole("alert").waitFor();
  assert.equal(await panel.getByRole("button", {name: "预览存储变更", exact: true}).count(), 0);
  responseMode = "real";
  await panel.getByRole("button", {name: "读回当前存储", exact: true}).click();
  await panel.getByText("1 项当前任务 · 1 项归档任务 · 1 项未结算 lease", {exact: true}).waitFor();
  await page.setViewportSize({width: 390, height: 844});
  await target.focus();
  await page.keyboard.press("Tab");
  await panel.scrollIntoViewIfNeeded();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
  assert.ok(await target.isVisible(), "the Goal context remains visible while scrolling settings");
  await page.screenshot({path: resolve(outputDir, "settings-goal-narrow.png"), animations: "disabled"});
  await settings.getByRole("radio", {name: "本机设置", exact: true}).check();
  const category = settings.locator(".personal-settings-mobile-category select");
  await category.selectOption("language");
  await settings.getByRole("radio", {name: "English", exact: true}).click();
  assert.equal(await settings.getByRole("radio", {name: "This device", exact: true}).isChecked(), true);
  await category.selectOption("capabilities");
  await page.screenshot({path: resolve(outputDir, "settings-device-narrow-en.png"), animations: "disabled"});
  assert.equal(writes, 0, "scope, target, history and failure recovery do not mutate Goal storage");
  assert.equal(context.errors.filter(error => !error.includes("503") && !error.includes("409")).length, 0, context.errors.join("; "));
  console.log(JSON.stringify({status: "PASS", storageReads, writes,
    note: "Packaged global/explicit Goal navigation, real HTTP cold-source counts, refresh/Back, missing vs zero, wrong-target rejection/retry, sticky narrow/keyboard context and English device scope."}));
} finally { await context?.close(); await browser?.close(); await authority.close(); server.kill("SIGTERM"); }
