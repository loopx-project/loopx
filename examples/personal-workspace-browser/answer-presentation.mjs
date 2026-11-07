// The same conversation renderer must keep a substantive answer readable in
// steward and Goal Chat, without turning a short factual answer into a report.

import { resolve } from "node:path";
import { writeFile } from "node:fs/promises";

import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

const LONG_URL = `https://example.org/review?candidate=${"0123456789abcdef".repeat(24)}&view=full-history`;
const LONG_PROMPT = `请比较两个方案，给出完整依据和边界。完整预览：${LONG_URL}`;
const SHORT_PROMPT = "方案 A 的公开来源是什么？";
const LONG_CODE = [`unwrapped-command ${"exact-argument-".repeat(80)}`, ...Array.from({length: 200}, (_, index) => `retained-line-${index}: complete evidence`)].join("\n");
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
  "读回命令：`loopx status`。",
  `候选分支：\`codex/${"research-history-".repeat(24)}\`。`,
  "",
  "```text",
  LONG_CODE,
  "```",
  "填写地址：https://example.org/forms/community。English: https://example.org/forms/en。",
  "",
  "<script>window.pwned=true</script>",
  "",
  "## 进一步核验",
  "",
  "下一步：请项目 Agent 核验部署与可回退路径，再提交采用建议。",
  "",
  "## 进一步核验",
  "",
  "历史版本需要按原始来源逐项核对，未确认状态仍然保留。",
].join("\n");
const SHORT_ANSWER = "方案 A 的公开来源：https://example.org/report。";

async function send(page, prompt) {
  await page.getByLabel("向 LoopX 发送消息").fill(prompt);
  await page.getByRole("button", { name: "发送", exact: true }).click();
}

async function assertAnswerTypography(answer) {
  const type = await answer.evaluate((node) => {
    const body = node.querySelector('.personal-md p:not(.personal-md-heading)');
    const heading = node.querySelector('.personal-md-heading.is-h2');
    const code = node.querySelector('.personal-md-code');
    return {
      body: parseFloat(getComputedStyle(body).fontSize),
      heading: parseFloat(getComputedStyle(heading).fontSize),
      codeFamily: getComputedStyle(code).fontFamily,
      codeSize: parseFloat(getComputedStyle(code).fontSize),
    };
  });
  if (type.body < 14 || type.heading <= type.body || type.codeSize < 14
    || !type.codeFamily.includes('Geist Mono')) {
    throw new Error(`Answer typography lost readable prose, heading hierarchy or code isolation: ${JSON.stringify(type)}`);
  }
}

// Compare the actual rendered foreground/background, not a particular theme hex.
async function codePresentation(answer) {
  return answer.evaluate(node => {
    const luminance = color => color.match(/[\d.]+/g).slice(0, 3)
      .map(value => Number(value) / 255)
      .map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4)
      .reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
    return ['.personal-md-pre code', '.personal-md-code'].map(selector => {
      const code = node.querySelector(selector), style = getComputedStyle(code);
      const background = getComputedStyle(code.closest('pre') ?? code).backgroundColor;
      const levels = [luminance(style.color), luminance(background)].sort((a, b) => a - b);
      return {selector, foreground: style.color, background,
        contrast: (levels[1] + .05) / (levels[0] + .05),
        fontSize: parseFloat(style.fontSize), selectable: style.userSelect !== 'none'};
    });
  });
}

async function assertConversationFits(page) {
  const layout = await page.locator(".personal-channel-scroll").evaluate(node => ({
    width: node.clientWidth, scrollWidth: node.scrollWidth,
    messages: [...node.querySelectorAll(".personal-message")].map(message => ({width: message.clientWidth, scrollWidth: message.scrollWidth})),
  }));
  if (layout.scrollWidth > layout.width + 1 || layout.messages.some(row => row.scrollWidth > row.width + 1)) {
    await page.screenshot({path: resolve(outputDir, `conversation-overflow-${page.viewportSize().width}.png`), animations: "disabled"});
    throw new Error(`Conversation must read without sideways scrolling: ${JSON.stringify(layout)}`);
  }
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
      await assertConversationFits(page);
      await assertAnswerTypography(answer);
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
      await answer.locator(".personal-md-pre").scrollIntoViewIfNeeded();
      await page.screenshot({ path: resolve(outputDir, "answer-presentation-desktop.png"), fullPage: false, animations: "disabled" });

      await page.setViewportSize({ width: 390, height: 844 });
      await assertConversationFits(page);
      const code = answer.locator(".personal-md-pre");
      if (!await code.evaluate(node => node.scrollWidth > node.clientWidth)) throw new Error("Code must retain its own horizontal scroll");
      await assertAnswerTypography(answer);
      await answer.scrollIntoViewIfNeeded();
      const overflow = await answer.evaluate((node) => ({
        card: node.scrollWidth > node.clientWidth + 1,
        table: node.querySelector(".personal-md-table-scroll")?.scrollWidth
          > node.querySelector(".personal-md-table-scroll")?.clientWidth + 1,
      }));
      if (overflow.card || !overflow.table) {
        throw new Error(`The mobile card must fit while the wide table scrolls: ${JSON.stringify(overflow)}`);
      }
      await code.scrollIntoViewIfNeeded();
      await page.screenshot({ path: resolve(outputDir, "answer-presentation-mobile.png"), fullPage: false, animations: "disabled" });
      const measurements = [];
      for (const theme of ["loopx", "paper", "brutal"]) {
        await page.evaluate(value => localStorage.setItem("loopx-pw-theme", value), theme);
        await page.reload({ waitUntil: "networkidle" });
        await page.getByRole("navigation", {name:"管家视图"})
          .getByRole("button", {name:/^(Chat|对话)$/, exact:true}).click();
        const themed = page.locator(".personal-channel-timeline .personal-message.is-assistant", {
          hasText:"建议先验证方案 A",
        });
        await themed.locator(".personal-md-pre code").waitFor();
        for (const width of [1512, 390]) {
          await page.setViewportSize({width, height:width === 390 ? 844 : 982});
          await assertConversationFits(page);
          const colors = await codePresentation(themed);
          measurements.push({theme, width, colors});
          await writeFile(resolve(outputDir, "code-contrast-measurements.json"), JSON.stringify(measurements, null, 2));
          if (colors.some(value => value.contrast < 4.5 || value.fontSize < 14 || !value.selectable)) {
            throw new Error(`Code must meet AA contrast without shrinking or disabling selection: ${JSON.stringify({theme, width, colors})}`);
          }
          const block = themed.locator(".personal-md-pre");
          if (!await block.evaluate(node => node.scrollWidth > node.clientWidth)
            || await block.innerText() !== LONG_CODE) {
            throw new Error("Theme switching must preserve complete code and its own horizontal scroll");
          }
          await block.scrollIntoViewIfNeeded();
          await page.evaluate(async () => {await document.fonts.ready; await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));});
          await page.screenshot({path:resolve(outputDir, `code-contrast-${theme}-${width}.png`), animations:"disabled"});
        }
      }
      await page.evaluate(() => localStorage.setItem("loopx-pw-theme", "loopx"));
      await page.setViewportSize({ width: 1512, height: 982 });

      await page.reload({ waitUntil: "networkidle" });
      await page.getByRole("navigation", { name: "管家视图" })
        .getByRole("button", { name: /^(Chat|对话)$/, exact: true }).click();
      const restored = page.locator(".personal-channel-timeline .personal-message.is-assistant", {
        hasText: "建议先验证方案 A",
      });
      await restored.waitFor({ state: "visible", timeout: 15_000 });
      await assertConversationFits(page);
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
      const headingIds = await page.locator(".answer-report-content h2").evaluateAll(nodes => nodes.map(node => node.id));
      if (headingIds.length !== 3 || new Set(headingIds).size !== 3 || await page.locator("h1").count() !== 1) {
        throw new Error("The answer must have semantic document headings with unique stable anchors below one page title");
      }
      await page.locator(".answer-report-outline summary").click();
      await page.getByRole("navigation", {name: "答复目录"}).getByRole("link", {name: "进一步核验", exact: true}).last().click();
      await page.waitForFunction(id => document.activeElement?.id === id, headingIds.at(-1));
      await page.reload({waitUntil: "networkidle"});
      await page.locator(".answer-report-content h2").last().waitFor();
      if (JSON.stringify(await page.locator(".answer-report-content h2").evaluateAll(nodes => nodes.map(node => node.id))) !== JSON.stringify(headingIds)) {
        throw new Error("Reload changed the saved answer's heading destinations");
      }
      await page.evaluate(() => {history.replaceState(null, "", location.pathname + location.search); window.scrollTo(0, 1200);});
      const readingTop = await page.evaluate(() => scrollY);
      await page.reload({waitUntil: "networkidle"});
      await page.waitForFunction(top => Math.abs(scrollY - top) <= 1, readingTop);
      await page.evaluate(() => {history.replaceState(null, "", location.pathname + location.search); window.scrollTo(0, 0);});
      await page.screenshot({ path: resolve(outputDir, "answer-report-desktop.png"), fullPage: false, animations: "disabled" });
      await page.setViewportSize({ width: 390, height: 844 });
      const reportOverflow = await page.locator(".answer-report-body").evaluate((node) => node.scrollWidth > node.clientWidth + 1);
      if (reportOverflow) throw new Error("The standalone answer overflows its mobile reading surface");
      await page.screenshot({ path: resolve(outputDir, "answer-report-mobile.png"), fullPage: false, animations: "disabled" });
      await page.setViewportSize({ width: 1512, height: 982 });
      await page.evaluate(() => {
        Object.defineProperty(navigator, "clipboard", {configurable: true, value: {writeText: async () => {throw new Error("Synthetic clipboard failure");}}});
      });
      await page.getByRole("button", {name: "复制 Markdown", exact: true}).click();
      const manualCopy = page.getByRole("textbox", {name: "答复 Markdown 原文"});
      await manualCopy.waitFor();
      if (await manualCopy.inputValue() !== LONG_ANSWER || !await manualCopy.evaluate(node => document.activeElement === node && node.selectionEnd === node.value.length)) {
        throw new Error("Clipboard failure must offer the complete visible Markdown source for manual copying");
      }
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
      await page.waitForFunction(() => document.activeElement?.matches(".personal-message[data-message-id]"));
      const originalMessageId = new URL(reportHref).searchParams.get("reportMessageId");
      if (await page.evaluate(() => document.activeElement?.getAttribute("data-message-id")) !== originalMessageId
        || returned.searchParams.get("message") !== originalMessageId) {
        throw new Error("Returning from the reader must focus the exact original Session/message");
      }
      // The reply already being read stays open as a later answer arrives.
      await send(page, SHORT_PROMPT);
      await page.locator(".personal-message.is-assistant", {hasText: SHORT_ANSWER}).waitFor();
      const longHistory = page.locator(".personal-message.is-assistant", {hasText: "建议先验证方案 A"});
      if (!await longHistory.locator(".personal-md-pre").count()) throw new Error("A new answer collapsed the message being read");
      const historyUrl = new URL(page.url());
      historyUrl.searchParams.delete("message"); historyUrl.searchParams.delete("messageSession");
      await page.goto(historyUrl.toString(), {waitUntil: "networkidle"});
      const expandHistory = longHistory.getByRole("button", {name: "展开完整消息", exact: true});
      await expandHistory.waitFor();
      if (await longHistory.locator(".personal-md-pre").count() || !await longHistory.getByText("原文节选", {exact: true}).count()) {
        throw new Error("Stable historical answers must expose an honest complete-block excerpt");
      }
      await expandHistory.scrollIntoViewIfNeeded();
      await page.screenshot({path: resolve(outputDir, "answer-history-collapsed-desktop.png"), animations: "disabled"});
      const beforeExpand = await expandHistory.evaluate(node => node.getBoundingClientRect().top);
      await expandHistory.click();
      const collapseHistory = longHistory.getByRole("button", {name: "收起消息", exact: true});
      await longHistory.locator(".personal-md-pre").waitFor();
      if (await longHistory.locator(".personal-md-pre").innerText() !== LONG_CODE
        || Math.abs(await collapseHistory.evaluate(node => node.getBoundingClientRect().top) - beforeExpand) > 2) {
        throw new Error("Expanding history lost original code or moved the reading anchor");
      }
      await collapseHistory.click();
      if (Math.abs(await expandHistory.evaluate(node => node.getBoundingClientRect().top) - beforeExpand) > 2) {
        throw new Error("Collapsing history moved the reading anchor");
      }
      await page.setViewportSize({width: 390, height: 844});
      await assertConversationFits(page);
      await expandHistory.scrollIntoViewIfNeeded();
      await page.screenshot({path: resolve(outputDir, "answer-history-collapsed-mobile.png"), animations: "disabled"});
      await page.setViewportSize({width: 1512, height: 982});
      const liveText = await page.locator(".personal-live-region").innerText();
      if (liveText.length > 160 || liveText.includes(SHORT_ANSWER)) throw new Error("Live announcements must not repeat the answer body");
      const turnCountAfterHistory = api.turnRequests.length;
      const missingFocus = new URL(page.url());
      missingFocus.searchParams.set("messageSession", new URL(reportHref).searchParams.get("reportSessionId"));
      missingFocus.searchParams.set("message", "missing-original-message");
      await page.goto(missingFocus.toString(), {waitUntil: "networkidle"});
      await page.locator(".personal-message-focus-notice", {hasText: "原消息不在当前已读取的对话中"}).waitFor();
      if (api.turnRequests.length !== turnCountAfterHistory) throw new Error("A missing original message must not replay a Turn");
      const missingUrl = new URL(reportHref);
      missingUrl.searchParams.set("reportMessageId", "missing-answer");
      await page.goto(missingUrl.toString(), { waitUntil: "networkidle" });
      await page.locator('[role="alert"]', { hasText: "找不到这份答复" }).waitFor({ state: "visible" });
      if (api.turnRequests.length !== turnCountAfterHistory) throw new Error("Missing report replayed a Turn");
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
      await assertConversationFits(page);
      await assertAnswerTypography(goalAnswer);
      await page.locator(".personal-channel-scroll").evaluate(node => { node.scrollTop = 0; });
      await page.screenshot({path: resolve(outputDir, "conversation-fit-goal-desktop.png"), animations: "disabled"});
      await page.setViewportSize({width: 390, height: 844});
      await assertConversationFits(page);
      await page.screenshot({path: resolve(outputDir, "conversation-fit-goal-mobile.png"), animations: "disabled"});
      await page.setViewportSize({width: 1512, height: 982});
      const goalReportHref = await goalAnswer.getByRole("link", { name: "单独阅读完整答复" }).getAttribute("href");
      if (!goalReportHref) throw new Error("Goal answer has no stable reading link");
      const goalId = new URL(page.url()).searchParams.get("goalId");
      const goalTurnCount = api.turnRequests.length;
      await page.goto(goalReportHref, { waitUntil: "networkidle" });
      await page.locator(".answer-report-content", { hasText: "建议先验证方案 A" }).waitFor({ state: "visible" });
      // A report shared or kept open can outlive the reader's executor choice.
      await page.evaluate(id => {
        const key = "loopx.personal-agent-selection.v1";
        localStorage.setItem(key, JSON.stringify({...JSON.parse(localStorage.getItem(key) ?? "{}"), [id]: "claude-code"}));
      }, goalId);
      await page.getByRole("button", { name: "返回对话", exact: true }).click();
      await page.locator(".personal-channel-timeline .personal-message.is-assistant", {
        hasText: "建议先验证方案 A",
      }).waitFor({ state: "visible" });
      if (new URL(page.url()).searchParams.get("goalId") !== goalId || api.turnRequests.length !== goalTurnCount) {
        throw new Error("Returning from a saved Goal answer changed its Goal or replayed a Turn");
      }
      await page.waitForFunction(id => document.activeElement?.getAttribute("data-message-id") === id,
        new URL(goalReportHref).searchParams.get("reportMessageId"));
      if (!/^codex$/iu.test(await page.locator(".personal-message.is-assistant", {hasText: "建议先验证方案 A"}).locator("header strong").innerText())) {
        throw new Error("A saved answer must retain its original Session executor identity after the current runtime changes");
      }
      if (!(await page.getByRole("combobox", {name: "选择聊天 Runtime", exact: true}).innerText()).includes("Claude Code")
        || await page.evaluate(id => JSON.parse(localStorage.getItem("loopx.personal-agent-selection.v1") ?? "{}")[id], goalId) !== "claude-code") {
        throw new Error("Reading an earlier executor's answer changed the current Goal executor choice");
      }
      if (!api.turnRequests.some((turn) => turn.message === LONG_PROMPT)
        || !api.turnRequests.some((turn) => turn.message === SHORT_PROMPT)) {
        throw new Error("Answers did not come from accepted conversation Turns");
      }
      return { coverageEntries: context.coverageEntries,
        note: "Saved answers retain semantic anchors, reading position, exact copy fallback and original message focus across runtime choices; stable historical excerpts preserve complete Markdown and expand/collapse anchors without replaying Turns." };
    } finally {
      await context.close();
    }
  },
};
