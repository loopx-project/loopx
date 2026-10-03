// The same conversation renderer must keep a substantive answer readable in
// steward and Goal Chat, without turning a short factual answer into a report.

import { resolve } from "node:path";

import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

const LONG_PROMPT = "请比较两个方案，给出完整依据和边界";
const SHORT_PROMPT = "方案 A 的公开来源是什么？";
const LONG_ANSWER = [
  "建议先验证方案 A，再决定是否采用。现有记录支持小范围试行，尚不足以证明生产部署成功。",
  "",
  "## 关键比较",
  "",
  "| 方案 | 已核验 | 待验证 |",
  "|---|---|---|",
  "| A | 本地验收通过 | 生产状态 |",
  "| B | 无验收记录 | 成本与风险 |",
  "",
  "依据：[公开报告](https://example.org/report)。这是记录里的结论；生产状态仍需独立读回。",
  "填写地址：https://example.org/forms/community。English: https://example.org/forms/en。",
  "",
  "<script>window.pwned=true</script>",
  "",
  "下一步：请项目 Agent 核验部署与可回退路径，再提交采用建议。",
].join("\n");
const SHORT_ANSWER = "方案 A 的公开来源：https://example.org/report。";

async function send(page, prompt) {
  await page.getByLabel("向 LoopX 发送消息").fill(prompt);
  await page.getByRole("button", { name: "发送", exact: true }).click();
}

export const answerPresentationScenario = {
  id: "answer-presentation",
  async run({ browser, collectCoverage, url }) {
    const context = await openWorkspacePage(browser, url, { collectCoverage });
    const { api, page } = context;
    try {
      api.answerForMessage = (message) => message === LONG_PROMPT ? LONG_ANSWER
        : message === SHORT_PROMPT ? SHORT_ANSWER : null;
      const managerNavigation = page.getByRole("navigation", { name: "管家视图" });
      await managerNavigation.getByRole("button", { name: /^(Chat|对话)$/, exact: true }).click();
      await send(page, LONG_PROMPT);
      const answer = page.locator(".personal-channel-timeline .personal-message.is-assistant", {
        hasText: "建议先验证方案 A",
      });
      await answer.waitFor({ state: "visible", timeout: 15_000 });
      if (await answer.locator("table").count() !== 1) throw new Error("Steward answer table was not rendered");
      if (await answer.locator('a[href="https://example.org/report"]').count() !== 1) {
        throw new Error("Steward evidence link was lost");
      }
      for (const href of ["https://example.org/forms/community", "https://example.org/forms/en"]) {
        const link = answer.locator(`a[href="${href}"]`);
        if (await link.count() !== 1 || await link.getAttribute("target") !== "_blank") {
          throw new Error("A returned plain web destination is not directly usable from the conversation");
        }
      }
      if (await answer.locator("script").count() || !((await answer.innerText()).includes("下一步：请项目 Agent"))) {
        throw new Error("Steward answer lost its ending or executed model HTML");
      }
      await answer.getByRole("link", { name: "单独阅读完整答复" }).waitFor({ state: "visible", timeout: 15_000 });
      await answer.scrollIntoViewIfNeeded();
      await page.screenshot({ path: resolve(outputDir, "answer-presentation-desktop.png"), fullPage: false, animations: "disabled" });

      await page.setViewportSize({ width: 390, height: 844 });
      await answer.scrollIntoViewIfNeeded();
      const overflow = await answer.evaluate((node) => ({
        card: node.scrollWidth > node.clientWidth + 1,
        table: node.querySelector(".personal-md-table-scroll")?.scrollWidth
          > node.querySelector(".personal-md-table-scroll")?.clientWidth + 1,
      }));
      if (overflow.card || !overflow.table) {
        throw new Error(`The mobile card must fit while the wide table scrolls: ${JSON.stringify(overflow)}`);
      }
      await page.screenshot({ path: resolve(outputDir, "answer-presentation-mobile.png"), fullPage: false, animations: "disabled" });
      await page.setViewportSize({ width: 1512, height: 982 });

      await page.reload({ waitUntil: "networkidle" });
      await page.getByRole("navigation", { name: "管家视图" })
        .getByRole("button", { name: /^(Chat|对话)$/, exact: true }).click();
      const restored = page.locator(".personal-channel-timeline .personal-message.is-assistant", {
        hasText: "建议先验证方案 A",
      });
      await restored.waitFor({ state: "visible", timeout: 15_000 });
      if (await restored.locator("table").count() !== 1 || !(await restored.innerText()).includes("可回退路径")) {
        throw new Error("Reload lost the complete Markdown answer in the original conversation");
      }
      const reportLink = restored.getByRole("link", { name: "单独阅读完整答复" });
      await reportLink.waitFor({ state: "visible", timeout: 15_000 });
      if (await reportLink.getAttribute("target") !== "_blank") {
        throw new Error("The answer link must leave the original conversation open");
      }
      const reportHref = await reportLink.getAttribute("href");
      if (!reportHref) throw new Error("Saved answer has no stable link");
      const originalTurnCount = api.turnRequests.length;
      await page.goto(reportHref, { waitUntil: "networkidle" });
      await page.getByRole("heading", { name: "完整答复" }).waitFor({ state: "visible" });
      // The report heading is static while the saved answer arrives from
      // ``fetchChatSession``; wait for hydration before asserting its content.
      await page.locator(".answer-report-content table").waitFor({ state: "visible", timeout: 15_000 });
      if (await page.locator(".answer-report-content table").count() !== 1
        || !(await page.locator(".answer-report-content").innerText()).includes("可回退路径")
        || await page.locator('.answer-report-content a[href="https://example.org/forms/community"]').count() !== 1
        || await page.locator(".answer-report-content script").count() || await page.evaluate(() => window.pwned === true)) {
        throw new Error("The standalone answer lost content or executed model HTML");
      }
      await page.screenshot({ path: resolve(outputDir, "answer-report-desktop.png"), fullPage: false, animations: "disabled" });
      await page.setViewportSize({ width: 390, height: 844 });
      const reportOverflow = await page.locator(".answer-report-body").evaluate((node) => node.scrollWidth > node.clientWidth + 1);
      if (reportOverflow) throw new Error("The standalone answer overflows its mobile reading surface");
      await page.screenshot({ path: resolve(outputDir, "answer-report-mobile.png"), fullPage: false, animations: "disabled" });
      await page.setViewportSize({ width: 1512, height: 982 });
      await page.reload({ waitUntil: "networkidle" });
      await page.locator(".answer-report-content table").waitFor({ state: "visible", timeout: 15_000 });
      if (await page.locator(".answer-report-content table").count() !== 1
        || await page.locator('.answer-report-content a[href="https://example.org/forms/en"]').count() !== 1
        || api.turnRequests.length !== originalTurnCount) {
        throw new Error("Reloading the answer link replayed a Turn or lost its content");
      }
      await page.evaluate(() => localStorage.setItem("loopx-pw-locale", "en"));
      await page.reload({ waitUntil: "networkidle" });
      await page.getByRole("heading", { name: "Full answer" }).waitFor({ state: "visible" });
      await page.evaluate(() => localStorage.setItem("loopx-pw-locale", "zh-CN"));
      await page.getByRole("button", { name: "Back to Steward", exact: true }).click();
      await page.locator(".personal-channel-timeline .personal-message.is-assistant", {
        hasText: "建议先验证方案 A",
      }).waitFor({ state: "visible", timeout: 15_000 });
      const returned = new URL(page.url());
      if (returned.searchParams.get("goalId") !== "" || returned.searchParams.get("view") !== "conversation"
        || returned.searchParams.has("reportMessageId") || returned.searchParams.has("reportSessionId")) {
        throw new Error("Returning from a saved Steward answer lost its conversation scope");
      }
      if (api.turnRequests.length !== originalTurnCount) {
        throw new Error("Returning to the Steward conversation replayed a Turn");
      }
      const missingUrl = new URL(reportHref);
      missingUrl.searchParams.set("reportMessageId", "missing-answer");
      await page.goto(missingUrl.toString(), { waitUntil: "networkidle" });
      await page.locator('[role="alert"]', { hasText: "找不到这份答复" }).waitFor({ state: "visible" });
      if (api.turnRequests.length !== originalTurnCount) throw new Error("Missing report replayed a Turn");
      await page.goto(url, { waitUntil: "networkidle" });

      await page.getByRole("navigation", { name: "管家视图" })
        .getByRole("button", { name: "总览", exact: true }).click();
      await page.locator(".personal-home-goal-card", { hasText: "Product Release" }).first().click();
      await page.getByRole("navigation", { name: "Goal 视图" })
        .getByRole("button", { name: /^(Chat|对话)$/, exact: true }).click();
      await send(page, SHORT_PROMPT);
      const short = page.locator(".personal-channel-timeline .personal-message.is-assistant", {
        hasText: "方案 A 的公开来源",
      });
      await short.waitFor({ state: "visible", timeout: 15_000 });
      if (await short.locator("a").count() !== 1 || await short.locator("h1,h2,h3,h4,table").count()) {
        throw new Error("Goal Chat lost a short direct sourced answer or forced a report layout");
      }
      if (await short.locator('a[href="https://example.org/report"]').count() !== 1) {
        throw new Error("Goal Chat did not preserve the plain source URL destination");
      }
      await send(page, LONG_PROMPT);
      const goalAnswer = page.locator(".personal-channel-timeline .personal-message.is-assistant", {
        hasText: "建议先验证方案 A",
      });
      await goalAnswer.waitFor({ state: "visible" });
      const goalReportHref = await goalAnswer.getByRole("link", { name: "单独阅读完整答复" }).getAttribute("href");
      if (!goalReportHref) throw new Error("Goal answer has no stable reading link");
      const goalId = new URL(page.url()).searchParams.get("goalId");
      const goalTurnCount = api.turnRequests.length;
      await page.goto(goalReportHref, { waitUntil: "networkidle" });
      await page.locator(".answer-report-content", { hasText: "建议先验证方案 A" }).waitFor({ state: "visible" });
      await page.getByRole("button", { name: "返回对话", exact: true }).click();
      await page.locator(".personal-channel-timeline .personal-message.is-assistant", {
        hasText: "建议先验证方案 A",
      }).waitFor({ state: "visible" });
      if (new URL(page.url()).searchParams.get("goalId") !== goalId || api.turnRequests.length !== goalTurnCount) {
        throw new Error("Returning from a saved Goal answer changed its Goal or replayed a Turn");
      }
      if (!api.turnRequests.some((turn) => turn.message === LONG_PROMPT)
        || !api.turnRequests.some((turn) => turn.message === SHORT_PROMPT)) {
        throw new Error("Answers did not come from accepted conversation Turns");
      }
      return { coverageEntries: context.coverageEntries,
        note: "Steward long Markdown and Goal short answer render safely, survive reload and fit mobile" };
    } finally {
      await context.close();
    }
  },
};
