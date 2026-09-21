import assert from "node:assert/strict";
import {resolve} from "node:path";
import {outputDir} from "./fixture.mjs";
import {openWorkspacePage} from "./scenario-context.mjs";

export const teamEvidenceScenario = {
  id: "team-evidence",
  async run({browser, collectCoverage, url}) {
    const context = await openWorkspacePage(browser, url, {collectCoverage});
    const {api, page} = context;
    try {
      await page.locator(".personal-goal-link", {hasText: "Product Release"}).click();
      await page.getByRole("navigation", {name: "Goal 视图"}).getByRole("button", {name: "对话", exact: true}).click();
      await page.getByRole("button", {name: "开启 LoopX 模式", exact: true}).click();
      await page.getByLabel("已注册的协调身份").selectOption("lead");
      await page.getByLabel("协调员总 token 额度").fill("100000");
      await page.getByRole("button", {name: "保存设置", exact: true}).click();
      const configured = api.loopxModeRequests.findLast(row => row.operation === "configure");
      const mode = page.__loopxRuntime.loopxModes.get(configured.sessionId);
      Object.assign(mode, {enabled: true, paused: false, active_turn_id: "fixture-loopx-turn", native: {status: "active", tokenBudget: 100000}});
      await page.getByText("LoopX · 正在推进", {exact: true}).waitFor();
      const results = page.getByRole("region", {name: "团队成果", exact: true});
      await results.getByRole("button", {name: "local-analyst · report.md", exact: true}).click();
      await results.getByRole("table").waitFor();
      assert.match(await results.getByRole("table").textContent(), /Free cash75/);
      assert.equal(await results.locator("script,img").count(), 0);
      assert.equal(await page.evaluate(() => window.artifactExecuted), undefined);
      await results.getByRole("button", {name: "查看原文", exact: true}).click();
      assert.equal(await results.getByRole("table").count(), 0);
      assert.match(await results.getByLabel("证据内容: report.md").textContent(), /\| Measure \| Value \|/);
      await results.getByRole("button", {name: "阅读报告", exact: true}).click();
      await results.getByRole("table").waitFor();
      await page.screenshot({path: resolve(outputDir, "team-results-desktop.png"), animations: "disabled"});
      await page.setViewportSize({width: 390, height: 844});
      assert(await results.evaluate(el => el.scrollWidth <= el.clientWidth));
      await page.screenshot({path: resolve(outputDir, "team-results-mobile.png"), animations: "disabled"});
      await page.setViewportSize({width: 1512, height: 982});
      const changedReport = async route => {
        const body = route.request().method() === "POST" ? route.request().postDataJSON() : {};
        if (body.operation === "read") return route.fulfill({json: {operation_id: body.operation_id, status: "accepted", artifacts: [
          {ref: "report.md", sha256: "f".repeat(64), text: "newer unverified report"},
        ]}});
        return route.fallback();
      };
      await page.route("**/api/chat/sessions/*/loopx", changedReport);
      await results.getByRole("button", {name: "local-analyst · report.md", exact: true}).click();
      await results.getByRole("alert").filter({hasText: "产物或验收已变化"}).waitFor();
      assert.equal(await results.getByRole("table").count(), 0);
      assert.equal(await results.getByText("newer unverified report").count(), 0);
      await page.unroute("**/api/chat/sessions/*/loopx", changedReport);
      await page.getByRole("button", {name: "团队执行情况", exact: true}).click();
      const dialog = page.getByRole("dialog", {name: "团队执行情况"});
      const openEvidence = dialog.getByRole("button", {name: "查看证据与反馈", exact: true});
      await openEvidence.first().click();
      const evidence = dialog.getByRole("region", {name: "执行证据"});
      const content = evidence.getByLabel("证据内容: report.json");
      await content.waitFor();
      assert.match(await content.textContent(), /"cash_flow":75/);
      assert.match(await content.textContent(), /<script>/);
      assert.equal(await page.evaluate(() => window.artifactExecuted), undefined);
      await evidence.getByText(/验收不代表协调员已采用/).waitFor();
      await evidence.getByText("尚无请求方采用记录。", {exact: true}).waitFor();
      mode.fixtureAdoptionState = "current";
      await evidence.getByRole("button", {name: "重新读取证据", exact: true}).click();
      await evidence.getByText("已记录采用 · 后续结果验收有效", {exact: true}).waitFor();
      await evidence.getByRole("button", {name: "查看后续结果", exact: true}).click();
      await evidence.getByLabel("证据内容: synthesis.json").waitFor();
      await evidence.getByText("源产物与接收方输入一致", {exact: true}).first().waitFor();
      const compare = evidence.getByRole("region", {name: "依据与结果对照"});
      await compare.getByRole("button", {name: /使用依据 report.json/}).click();
      await compare.getByLabel("指定来源: report.json").waitFor();
      assert.match(await compare.getByLabel("本次产物: synthesis.json").textContent(), /accepted_cash_flow/);
      assert.equal(await page.evaluate(() => window.artifactExecuted), undefined);
      await page.screenshot({path: resolve(outputDir, "team-comparison-desktop.png"), animations: "disabled"});
      await page.setViewportSize({width: 390, height: 844});
      await page.emulateMedia({reducedMotion: "reduce"});
      assert(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth));
      await page.screenshot({path: resolve(outputDir, "team-comparison-mobile.png"), animations: "disabled"});
      await page.setViewportSize({width: 1512, height: 982});
      // A changed source hash must not silently compare the newest file.
      await compare.getByRole("button", {name: /回应依据 report.md/}).click();
      await compare.getByLabel("指定来源: report.md").waitFor();
      assert.equal(await compare.getByLabel("对照产物").inputValue(), "1", "Same-name Markdown is selected, not the first JSON artifact");
      assert.equal(await compare.getByRole("table").count(), 2);
      await compare.getByRole("button", {name: "查看原文差异", exact: true}).click();
      assert.equal(await compare.getByRole("table").count(), 0);
      assert(await compare.locator('[data-changed="true"]').count());
      await compare.getByRole("button", {name: /使用依据 report.json/}).click();
      await compare.getByLabel("指定来源: report.json").waitFor();
      const changedSource = async route => {
        const body = route.request().method() === "POST" ? route.request().postDataJSON() : {};
        if (body.operation === "read" && body.operation_id === "accepted-analysis") {
          return route.fulfill({json: {operation_id: body.operation_id, status: "accepted", recovery_required: false,
            artifacts: [{ref: "report.json", sha256: "f".repeat(64), text: "newer content must not appear"}]}});
        }
        return route.fallback();
      };
      await page.route("**/api/chat/sessions/*/loopx", changedSource);
      await compare.getByRole("button", {name: /使用依据 report.json/}).click();
      await compare.getByRole("alert").filter({hasText: "指定来源版本无法核验"}).waitFor();
      assert.equal(await compare.getByLabel("指定来源: report.json").count(), 0);
      assert.equal(await compare.getByText("newer content must not appear").count(), 0);
      await page.screenshot({path: resolve(outputDir, "team-comparison-unavailable.png"), animations: "disabled"});
      await page.unroute("**/api/chat/sessions/*/loopx", changedSource);
      await compare.getByRole("button", {name: /使用依据 report.json/}).click();
      await compare.getByLabel("指定来源: report.json").waitFor();
      await evidence.getByRole("button", {name: "accepted-analysis", exact: true}).first().click();
      await content.waitFor();
      mode.fixtureAdoptionState = "unavailable";
      await evidence.getByRole("button", {name: "重新读取证据", exact: true}).click();
      await evidence.getByText("采用证据已失效或无法核验", {exact: true}).waitFor();
      assert.equal(await evidence.getByText("已记录采用 · 后续结果验收有效", {exact: true}).count(), 0);
      mode.fixtureAdoptionState = "current";
      await evidence.getByRole("button", {name: "重新读取证据", exact: true}).click();
      await evidence.getByText("已记录采用 · 后续结果验收有效", {exact: true}).waitFor();
      assert.equal(api.turnRequests.length, 0, "Evidence reading must not start a model");
      await page.screenshot({path: resolve(outputDir, "team-evidence-desktop.png"), animations: "disabled"});
      // Lose the first acknowledgement; retry must preserve identity and content.
      let first = true;
      await page.route("**/api/chat/sessions/*/loopx", async route => {
        if (route.request().method() === "POST" && route.request().postDataJSON().operation === "message" && first) {
          first = false;
          api.loopxModeRequests.push({sessionId: configured.sessionId, ...route.request().postDataJSON()});
          return route.fulfill({status: 503, json: {error: "Fixture lost acknowledgement"}});
        }
        return route.fallback();
      });
      await evidence.getByLabel("向协调员反馈此执行").fill("请解释修订后的现金流证据。 ");
      await evidence.getByRole("button", {name: "发送反馈", exact: true}).click();
      await evidence.getByRole("button", {name: "重试同一条反馈", exact: true}).click();
      await evidence.getByText("已进入协调员收件箱，等待读取", {exact: true}).waitFor();
      const messages = api.loopxModeRequests.filter(row => row.operation === "message");
      assert.equal(messages.length, 2);
      assert.equal(messages[0].operation_id, messages[1].operation_id);
      assert.equal(messages[0].message, messages[1].message);
      assert.match(messages[1].message, /accepted-analysis/);
      assert.match(messages[1].message, /sha256:dddd/);
      assert.equal(messages[1].delivery_mode, "inbox");
      mode.ingress[0].status = "delivered";
      await evidence.getByText("已交给协调员；尚无应用回执", {exact: true}).waitFor();
      await page.setViewportSize({width: 390, height: 844});
      await page.emulateMedia({reducedMotion: "reduce"});
      await content.scrollIntoViewIfNeeded();
      assert(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth));
      await page.screenshot({path: resolve(outputDir, "team-evidence-mobile.png"), animations: "disabled"});
      await dialog.getByRole("button", {name: "返回执行列表", exact: true}).click();
      assert(await openEvidence.first().evaluate(el => el === document.activeElement));
      await openEvidence.first().click();
      await content.waitFor();
      // A failed revalidation must erase the previously accepted text, not retain its badge.
      await page.route("**/api/chat/sessions/*/loopx", async route => {
        if (route.request().method() === "POST" && route.request().postDataJSON().operation === "read") {
          return route.fulfill({status: 409, json: {error: "delegation output changed after completion"}});
        }
        return route.fallback();
      });
      await evidence.getByRole("button", {name: "重新读取证据", exact: true}).click();
      await evidence.getByRole("alert").filter({hasText: "已清除上次证据"}).waitFor();
      assert.equal(await content.count(), 0);
      await page.screenshot({path: resolve(outputDir, "team-evidence-stale.png"), animations: "disabled"});
      await dialog.getByRole("button", {name: "暂停协调员", exact: true}).click();
      await dialog.getByText(/协调员已暂停。/).waitFor();
      await dialog.getByText(/当前入口不支持停止整个团队/).waitFor();
      assert(await dialog.isVisible(), "Pause must keep its scope/result visible");
      assert.equal(api.loopxModeRequests.at(-1).operation, "pause");
      assert.equal(api.turnRequests.length, 0);
      await page.keyboard.press("Escape");
      assert(await page.locator(".goal-loopx-team-trigger").evaluate(el => el === document.activeElement));
      await results.getByRole("button", {name: "local-analyst · report.md", exact: true}).click();
      await results.getByRole("alert").filter({hasText: "已清除上次报告"}).waitFor();
      assert.equal(await results.getByRole("table").count(), 0);
      assert.equal(api.turnRequests.length, 0);
      return {coverageEntries: await context.close(), note: "Packaged evidence text, stale erasure, safe rendering, idempotent feedback, receipt strength, scoped pause, mobile and keyboard focus verified with synthetic API fixtures."};
    } catch (error) {await context.close(); throw error;}
  },
};
