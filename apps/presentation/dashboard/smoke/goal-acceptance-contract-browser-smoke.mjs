#!/usr/bin/env node
// Exercise owner contract readback and unavailable operation controls with synthetic sources.
// --write-authority-only runs the React/Vite cancellation race without native fixtures.
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
const writeAuthorityOnly = process.argv.includes("--write-authority-only");
const python = writeAuthorityOnly ? null : resolveTestPython();
const port = Number(process.env.LOOPX_ACCEPTANCE_CONTRACT_PORT ?? 5297);
const packaged = process.env.LOOPX_ACCEPTANCE_CONTRACT_PACKAGED === "1";
if (writeAuthorityOnly && packaged) throw new Error("--write-authority-only requires the Vite fixture");
const payload = writeAuthorityOnly ? null : JSON.parse(execFileSync(python, ["-c", `
import runpy, tempfile, json
from pathlib import Path
from loopx.status import collect_status
fixture = runpy.run_path('tests/test_delivery_review.py')
with tempfile.TemporaryDirectory() as directory:
    registry, runtime = fixture['make_project'](Path(directory))
    print(json.dumps(collect_status(registry_path=registry, runtime_root_override=str(runtime), limit=20, goal_id='release-demo')))
`], { cwd: root, encoding: "utf8" }));
if (payload) payload.run_history.goals[0].acceptance_observation = acceptance;
const server = packaged
  ? spawn(python, ["-m", "http.server", String(port), "--bind", "127.0.0.1", "--directory", resolve(root, "loopx/web")], { stdio: "ignore" })
  : startViteDashboardServer({ dashboardDir, port });
async function checkLiveWriteAuthority(browser, origin) {
  const page = await browser.newPage({viewport: {width: 1024, height: 768}});
  const writes = [], errors = [];
  let readsFail = false;
  const native = {ok: true, goal_id: "release-demo", provider_revision: "fixture:current",
    goal_acceptance_contract: {...contract, lifecycle_state: "active",
      goal_ref: {goal_id: "release-demo", goal_instance_id: "write-authority-instance"}}};
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(() => localStorage.setItem("loopx-pw-locale", "en"));
  await page.route("**/api/**", route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (request.method() !== "GET") writes.push({path, body: request.postDataJSON()});
    if (path === "/api/chat/goal-acceptance") return route.fulfill(
      request.method() === "GET" && readsFail
        ? {status: 503, json: {error: "Synthetic native read failure"}}
        : {json: native});
    if (path === "/api/actions") return route.fulfill({json: {
      ok: true, schema_version: "loopx_chat_action_list_v1", proposals: [],
    }});
    return route.fulfill({status: 404, json: {error: "Unavailable in the write-authority fixture"}});
  });
  const phase = expected => page.waitForFunction(value =>
    document.querySelector('output[aria-label="Cancellation state"]')?.textContent === value, expected);
  const idle = () => page.waitForFunction(() =>
    document.querySelector(".delivery-acceptance-contract")?.getAttribute("aria-busy") === "false");
  try {
    await page.goto(origin + "/smoke/goal-acceptance-write-authority-smoke.html");
    await phase("ready");
    const section = page.locator(".delivery-acceptance-contract");
    const check = section.getByRole("button", {name: "Check delivery", exact: true});
    await check.click();
    await phase("waiting");
    readsFail = true;
    await page.getByRole("button", {name: "Refresh native read", exact: true}).click();
    await section.getByRole("alert").waitFor();
    assert.equal(await check.count(), 0, "The new native read revokes current write controls");
    await page.getByRole("button", {name: "Release query cancellation", exact: true}).click();
    await phase("released"); await idle();
    assert.deepEqual(writes, [], "Native read failure during cancellation must prevent the stale effect POST");

    // A fresh retry remains usable; the negative case must not pass by blocking every write.
    readsFail = false;
    await page.getByRole("button", {name: "Refresh native read", exact: true}).click();
    await check.waitFor();
    const sent = page.waitForResponse(response =>
      new URL(response.url()).pathname === "/api/chat/goal-acceptance" && response.request().method() === "POST");
    await check.click(); await sent; await idle();
    assert.equal(writes.length, 1);
    assert.equal(writes[0].path, "/api/chat/goal-acceptance");
    assert.equal(writes[0].body.action, "verify");
    assert.equal(writes[0].body.goal_id, "release-demo");
    assert.deepEqual(errors, []);
    console.log("Goal acceptance write authority: deferred cancellation/read failure blocks a stale POST; fresh retry passed");
  } finally {await page.close();}
}

let browser;
try {
  await waitForHttp(`http://127.0.0.1:${port}/`);
  browser = await launchBrowser(createRequire(import.meta.url)("playwright").chromium);
  if (!packaged) await checkLiveWriteAuthority(browser, `http://127.0.0.1:${port}`);
  for (const [locale, viewport] of writeAuthorityOnly ? [] : [["en", { width: 1440, height: 1000 }], ["zh-CN", { width: 390, height: 844 }]]) {
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
      goal_acceptance_contract: response.acceptance.goal_acceptance_contract ?? {},
    }}));
    await page.goto(`http://127.0.0.1:${port}/${packaged ? "chat/" : ""}?statusUrl=/status.json`, { waitUntil: "networkidle" });
    const navigation = page.getByRole("button", { name: locale === "en" ? "Open Goal navigation" : "打开 Goal 导航" });
    if (await navigation.isVisible()) await navigation.click();
    await page.locator(".personal-goal-link").first().click();
    await page.getByRole("button", { name: locale === "en" ? "Overview" : "概览", exact: true }).click();
    const section = page.locator(".delivery-acceptance-contract");
    const openInspection = async () => {
      const inspection = section.locator(".delivery-check-inspection");
      if (await inspection.isVisible() && await inspection.getAttribute("open") === null) {
        await inspection.locator(":scope > summary").focus();
        await page.keyboard.press("Enter");
      }
    };
    // Both current read owners must describe the same fixture. An empty native
    // response correctly supersedes retained status, so it cannot test stale work.
    // The owner can invalidate work without changing the contract revision.
    const imageDir = resolve(root, "output/playwright/acceptance-currentness");
    await mkdir(imageDir, {recursive: true});
    for (const status of ["stale", "held", "partial", "accepted", "failed", "unverified"]) {
      const value = verifiedContract(status);
      if (value.verification) {
        value.verification.contract_revision = value.revision;
        value.verification.contract_digest = value.digest;
      }
      response = {...snapshot, acceptance: {...acceptance, goal_acceptance_contract: value}};
      payload.run_history.goals[0].acceptance_observation = {...acceptance, goal_acceptance_contract: value};
      await page.reload({waitUntil: "networkidle"});
      const direction = page.locator(".goal-overview-direction");
      await direction.waitFor();
      await section.waitFor();
      await openInspection();
      await direction.getByText(contract.criteria[0].description, {exact: true}).waitFor();
      await page.screenshot({path: resolve(imageDir, `${locale}-${status}.png`), animations: "disabled"});
      const passing = direction.locator(".goal-overview-criteria .is-verified, .goal-overview-criteria .is-accepted");
      assert.equal(await passing.count(), status === "accepted" ? 1 : 0, `${status}: retained checks are not current whole-scope success`);
      assert.equal(await direction.locator(".goal-overview-criteria li").count(), value.criteria.length);
    }
    response = structuredClone(snapshot);
    payload.run_history.goals[0].acceptance_observation = acceptance;
    await page.reload({waitUntil: "networkidle"});
    const review = page.locator(".delivery-review");
    const openEvidence = async () => {
      const evidence = page.locator(".goal-overview-evidence");
      if (await evidence.count() && await evidence.getAttribute("open") === null) {
        await evidence.locator(":scope > summary").focus();
        await page.keyboard.press("Enter");
      }
    };
    await openEvidence();
    const refresh = review.getByRole("button", { name: locale === "en" ? "Refresh snapshot" : "刷新快照", exact: true });
    const exportButton = review.getByRole("button", { name: locale === "en" ? "Export delivery snapshot" : "导出交付快照", exact: true });
    if (locale === "en") {
      const requirement = (id, description, status) => ({
        id, description, status, planning_satisfied: status === "accepted",
      });
      const collision = {
        schema_version: "loopx_goal_outcomes_v0", revision: 1, current_lifetime: true,
        execution_authority_granted: false, goal_completion_assessed: false,
        outcomes: [
          {
            id: "plan", owner_agent_id: "author", objective: "First colliding outcome", revision: 1,
            requirements: [
              requirement("section:opening", "Colon in the requirement id", "failed"),
              requirement("detail", "Second unmet requirement", "failed"),
              requirement("closing", "Third unmet requirement", "failed"),
            ],
            todo_ids: [], sources: [], work: [],
          },
          {
            id: "plan:section", owner_agent_id: "author", objective: "Second colliding outcome", revision: 1,
            requirements: [requirement("opening", "Unrelated met requirement", "accepted")],
            todo_ids: [], sources: [], work: [],
          },
        ],
      };
      const projected = {...acceptance, goal_acceptance_contract: {outcome_requirements: collision}};
      response = {...snapshot, acceptance: projected};
      payload.run_history.goals[0].acceptance_observation = projected;
      await page.reload({waitUntil: "networkidle"});
      const outcomes = page.locator(".goal-outcomes");
      await outcomes.waitFor();
      const visibleRows = outcomes.locator(".goal-outcome-criteria > li:not([hidden])");
      assert.equal(await visibleRows.count(), 3, "Colon-bearing outcome/requirement pairs keep only three preview rows");
      await outcomes.locator(":scope > .goal-outcomes-more").click();
      assert.equal(await visibleRows.count(), 4, "Expanding reveals the unrelated fourth requirement");
      response = structuredClone(snapshot);
      payload.run_history.goals[0].acceptance_observation = acceptance;
      await page.reload({waitUntil: "networkidle"});
      if (await navigation.isVisible()) await navigation.click();
      await page.locator(".personal-goal-link").first().click();
      await page.getByRole("button", {name: "Overview", exact: true}).click();
      await openEvidence();
    }
    const openAdvanced = async () => {
      await openInspection();
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
      if (responseStatus === 200) {
        await page.reload({waitUntil: "networkidle"});
        await openEvidence();
        // Goal activation can finish after the page's network-idle boundary.
        // Compare the completed delivery view, never its loading placeholder.
        await review.locator(".delivery-chain").waitFor();
        await page.waitForFunction(() => !document.querySelector(".delivery-review-toolbar button")?.disabled);
      }
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
    await section.getByText(locale === "en" ? "Check record and versions" : "检查记录与版本", {exact: true}).click();
    await section.getByText("c".repeat(64), { exact: true }).waitFor();
    assert.match(await section.getByText(locale === "en" ? "Check record and versions" : "检查记录与版本", {exact: true}).locator("..").locator("dl").innerText(), /6/);
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
    await refreshSnapshot({});
    assert.equal(await section.count(), 0, "Removing checks removes the previous command readback");
    assert.deepEqual(writes, [], "Reading and exporting must not write state");
    assert.deepEqual(errors, []);
    await page.close();
  }
  if (!writeAuthorityOnly) console.log(`Goal acceptance contract browser (${packaged ? "packaged" : "development"}): off parity, states, keyboard, locales, mobile, revision refresh and read-only export passed`);
} finally {
  await cleanupBrowserSmoke({ browser, server, fixturePaths: [] });
}
