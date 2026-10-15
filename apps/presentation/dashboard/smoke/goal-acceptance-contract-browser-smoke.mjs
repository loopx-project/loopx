#!/usr/bin/env node
// Exercise owner contract readback and unavailable operation controls with synthetic sources.
import assert from "node:assert/strict";
import {mkdir} from "node:fs/promises";
import { execFileSync, spawn } from "node:child_process";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { cleanupBrowserSmoke, launchBrowser, startViteDashboardServer, waitForHttp } from "../../../../examples/dashboard-browser-smoke-support.mjs";
import { acceptance, contract, snapshot, verifiedContract } from "./goal-acceptance-contract-fixture.mjs";
import { resolveTestPython } from "../../../../scripts/test-python.mjs";

const dashboardDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const root = resolve(dashboardDir, "../../..");
const python = resolveTestPython();
const port = Number(process.env.LOOPX_ACCEPTANCE_CONTRACT_PORT ?? 5297);
const packaged = process.env.LOOPX_ACCEPTANCE_CONTRACT_PACKAGED === "1";
const payload = JSON.parse(execFileSync(python, ["-c", `
import runpy, tempfile, json
from pathlib import Path
from loopx.status import collect_status
fixture = runpy.run_path('tests/test_delivery_review.py')
with tempfile.TemporaryDirectory() as directory:
    registry, runtime = fixture['make_project'](Path(directory))
    print(json.dumps(collect_status(registry_path=registry, runtime_root_override=str(runtime), scan_roots=[], limit=20, goal_id='release-demo', include_public_boundary_scan=False)))
`], { cwd: root, encoding: "utf8" }));
payload.run_history.goals[0].acceptance_observation = acceptance;
const server = packaged
  ? spawn(python, ["-m", "http.server", String(port), "--bind", "127.0.0.1", "--directory", resolve(root, "loopx/web")], { stdio: "ignore" })
  : startViteDashboardServer({ dashboardDir, port });
let browser;
try {
  await waitForHttp(`http://127.0.0.1:${port}/`);
  browser = await launchBrowser(createRequire(import.meta.url)("playwright").chromium);
  for (const [locale, viewport] of [["en", { width: 1440, height: 1000 }], ["zh-CN", { width: 390, height: 844 }]]) {
    const page = await browser.newPage({ viewport });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    const writes = [];
    let response = structuredClone(snapshot);
    let responseStatus = 200;
    let authorityStatus = 200;
    await page.addInitScript(language => localStorage.setItem("loopx-pw-locale", language), locale);
    await page.route("**/api/**", route => {
      // Model-option discovery is an existing read-only POST. Every effect
      // endpoint, including the new acceptance operation, remains in this ledger.
      if (route.request().method() !== "GET" && new URL(route.request().url()).pathname !== "/api/chat/agent-model-options") writes.push(route.request().url());
      return route.fulfill({ status: 404, json: { error: "Unavailable in read-only fixture" } });
    });
    await page.route(url => url.pathname === "/status.json", route => route.fulfill({ json: payload }));
    await page.route("**/api/chat/delivery-review?*", route => {
      assert.equal(route.request().method(), "GET");
      assert.equal(new URL(route.request().url()).searchParams.get("goal_id"), "release-demo");
      return route.fulfill({ status: responseStatus, json: response });
    });
    await page.route("**/api/actions?*", route => route.fulfill({json: {ok: true, schema_version: "loopx_chat_action_list_v1", proposals: []}}));
    await page.route("**/api/chat/goal-acceptance?*", route => route.fulfill({status: authorityStatus, json: {
      ok: true, goal_id: response.acceptance.goal_id, provider_revision: "fixture:current",
      goal_acceptance_contract: response.acceptance.goal_acceptance_contract ?? {enabled: false},
    }}));
    await page.goto(`http://127.0.0.1:${port}/${packaged ? "chat/" : ""}?statusUrl=/status.json`, { waitUntil: "networkidle" });
    const navigation = page.getByRole("button", { name: locale === "en" ? "Open Goal navigation" : "打开 Goal 导航" });
    if (await navigation.isVisible()) await navigation.click();
    await page.locator(".personal-goal-link").first().click();
    await page.getByRole("button", { name: locale === "en" ? "Overview" : "概览", exact: true }).click();
    // The owner can invalidate work without changing the contract revision.
    const imageDir = resolve(root, "output/playwright/acceptance-currentness");
    await mkdir(imageDir, {recursive: true});
    for (const status of ["stale", "held", "partial", "accepted", "failed", "unverified"]) {
      const value = verifiedContract(status);
      if (value.verification) {
        value.verification.contract_revision = value.revision;
        value.verification.contract_digest = value.digest;
      }
      payload.run_history.goals[0].acceptance_observation = {...acceptance, goal_acceptance_contract: value};
      await page.reload({waitUntil: "networkidle"});
      const direction = page.locator(".goal-overview-direction");
      await direction.waitFor();
      const toggle = direction.locator(".goal-direction-toggle");
      if (await toggle.isVisible() && await toggle.getAttribute("aria-expanded") === "false") await toggle.click();
      await direction.getByText(contract.criteria[0].description, {exact: true}).waitFor();
      await page.screenshot({path: resolve(imageDir, `${locale}-${status}.png`), animations: "disabled"});
      const passing = direction.locator(".goal-overview-criteria .is-verified, .goal-overview-criteria .is-accepted");
      assert.equal(await passing.count(), status === "accepted" ? 1 : 0, `${status}: retained checks are not current whole-scope success`);
      assert.equal(await direction.locator(".goal-overview-criteria li").count(), value.criteria.length);
    }
    payload.run_history.goals[0].acceptance_observation = acceptance;
    await page.reload({waitUntil: "networkidle"});
    const review = page.locator(".delivery-review");
    const section = review.locator(".delivery-acceptance-contract");
    const refresh = review.getByRole("button", { name: locale === "en" ? "Refresh snapshot" : "刷新快照", exact: true });
    const exportButton = review.getByRole("button", { name: locale === "en" ? "Export delivery snapshot" : "导出交付快照", exact: true });
    const openAdvanced = async () => {
      const advanced = section.locator(".delivery-check-advanced");
      if (await advanced.isVisible() && await advanced.getAttribute("open") === null) {
        await advanced.locator(":scope > summary").focus();
        await page.keyboard.press("Enter");
        assert.notEqual(await advanced.getAttribute("open"), null, "Optional diagnostics are keyboard accessible");
      }
    };
    const refreshSnapshot = async value => {
      response = { ...snapshot, acceptance: { ...acceptance, goal_acceptance_contract: value } };
      const read = page.waitForResponse(url => url.url().includes("/api/chat/delivery-review?"));
      await refresh.click();
      await read;
      await page.waitForFunction(() => !document.querySelector(".delivery-review-toolbar button")?.disabled);
      // The native check reader is independent of the delivery-map snapshot.
      // Reload verifies both read owners; polling/recovery is covered by the
      // real SQLite/HTTP/CLI journey.
      if (responseStatus === 200) await page.reload({waitUntil: "networkidle"});
      await openAdvanced();
    };
    await review.locator(".delivery-chain").waitFor();
    const baseline = await review.innerText();
    assert.equal(await section.count(), 0);
    for (const value of [{ enabled: false }, { ...verifiedContract("accepted"), enabled: false }, undefined]) {
      await refreshSnapshot(value);
      assert.equal(await section.count(), 0);
      assert.equal(await review.innerText(), baseline, "Absent/off contract must preserve the existing view");
    }
    await refreshSnapshot(contract);
    const text = await section.innerText();
    for (const value of [contract.objective, contract.digest, "release-demo", "todo_confirmed", "todo_unbound", "todo_stale", "recovery"]) assert.ok(text.includes(value));
    for (const value of locale === "en" ? ["Task association confirmed", "Task association missing", "Task association stale", "Task associations require confirmation", "Outside the current task gate"] : ["任务关联已确认", "任务关联缺失", "任务关联已过期", "任务关联需要确认", "不属于当前任务门禁范围"]) assert.ok(text.includes(value), value);
    await section.getByText(locale === "en" ? /Import an existing JSON contract/ : /导入现有 JSON 合同/).waitFor();
    for (const [status, en, zh] of [["unverified", "Artifact checks not verified", "产物检查未验证"], ["failed", "Artifact checks failed", "产物检查失败"], ["stale", "Artifact checks stale", "产物检查已过期"], ["partial", "Task checks passed; Goal-wide verification unknown", "任务检查通过；Goal 整体验证未知"], ["accepted", "Artifact checks passed", "产物检查通过"]]) {
      await refreshSnapshot(verifiedContract(status));
      await section.getByText(locale === "en" ? en : zh, { exact: true }).first().waitFor();
      assert.ok((await section.innerText()).includes(locale === "en" ? "independent review" : "独立复核"));
    }
    await refreshSnapshot({ ...contract, scope: { kind: "selected_work", todo_ids: contract.tasks.map(task => task.todo_id) } });
    await section.getByText(locale === "en" ? "Only explicitly selected tasks" : "仅明确选定的任务", { exact: true }).waitFor();
    await section.locator(".delivery-check-advanced dl").scrollIntoViewIfNeeded();
    await page.screenshot({ path: process.env.LOOPX_ACCEPTANCE_SCOPE_SCREENSHOT_DIR
      ? `${process.env.LOOPX_ACCEPTANCE_SCOPE_SCREENSHOT_DIR}/${locale}.png` : `/tmp/loopx-acceptance-scope-${locale}.png` });
    const next = { ...verifiedContract("stale"), revision: 8, digest: "b".repeat(64) };
    await refreshSnapshot(next);
    await section.getByText(next.digest, { exact: true }).waitFor();
    assert.equal(await section.getByText(contract.digest, { exact: true }).count(), 0);
    assert.match(await section.locator(".delivery-check-advanced dl").innerText(), /8/);
    await section.locator("details > summary").first().click();
    await section.getByText("c".repeat(64), { exact: true }).waitFor();
    assert.match(await section.locator(".delivery-check-result dl").innerText(), /6/);
    assert.equal(await section.evaluate(element => element.scrollWidth > element.clientWidth + 2), false, "Long digests must wrap at narrow widths");
    const downloadPromise = page.waitForEvent("download");
    await exportButton.click();
    const download = await downloadPromise;
    const stream = await download.createReadStream();
    let markdown = "";
    for await (const chunk of stream) markdown += chunk.toString();
    assert.ok(markdown.includes(next.digest) && markdown.includes("8") && markdown.includes(contract.objective));
    responseStatus = 503;
    authorityStatus = 503;
    await refreshSnapshot(next);
    await section.getByText(locale === "en" ? /Retained snapshot/ : /当前保留旧快照/).first().waitFor();
    assert.equal(await section.getByRole("button", {name: locale === "en" ? "Check delivery" : "检查交付", exact: true}).count(), 0,
      "An unavailable current authority must not offer a command from retained state");
    assert.equal(await exportButton.isDisabled(), true, "Failed refresh must not export retained contract as current");
    responseStatus = 200;
    authorityStatus = 200;
    response = { ...snapshot, acceptance: { ...acceptance, goal_id: "other-goal", goal_acceptance_contract: next } };
    const mismatch = page.waitForResponse(url => url.url().includes("/api/chat/delivery-review?"));
    await refresh.click();
    await mismatch;
    await page.waitForFunction(() => !document.querySelector(".delivery-review-toolbar button")?.disabled);
    await section.getByText(locale === "en" ? /Retained snapshot/ : /当前保留旧快照/).first().waitFor();
    assert.equal(await section.getByText("other-goal", { exact: true }).count(), 0);
    await refreshSnapshot({ enabled: false });
    assert.equal(await section.count(), 0, "Disabling removes the previous enabled readback");
    assert.deepEqual(writes, [], "Reading and exporting must not write state");
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log(`Goal acceptance contract browser (${packaged ? "packaged" : "development"}): off parity, states, keyboard, locales, mobile, revision refresh and read-only export passed`);
} finally {
  await cleanupBrowserSmoke({ browser, server, fixturePaths: [] });
}
