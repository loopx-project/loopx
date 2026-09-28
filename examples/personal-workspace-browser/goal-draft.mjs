import assert from "node:assert/strict";
import {resolve} from "node:path";
import {outputDir} from "./fixture.mjs";
import {openWorkspacePage} from "./scenario-context.mjs";

export const goalDraftScenario = {
  id: "goal-draft",
  async run({browser, collectCoverage, url}) {
    const context = await openWorkspacePage(browser, url, {collectCoverage});
    const {page, api, close, errors} = context;
    const draft = {objective: "研究微软近三年的现金流", completion_criteria: "有公开来源的数据和可读报告", execution_boundary: "仅使用公开财报", question: "更想了解哪方面？", options: ["现金流趋势", "资本支出变化"]};
    api.answerForMessage = (message) => message === "解释一下 Goal 是什么" ? "Goal 保存目标和完成标准。" : {message: "先确认研究重点，再检查目标设置。", goal_draft: {...draft, ...(message.includes("资本支出") ? {completion_criteria: "重点比较资本支出，列出来源", question: "", options: []} : {})}};
    try {
      const composer = page.getByLabel("向 LoopX 发送消息");
      async function send(text) {
        await composer.fill(text);
        await page.getByRole("button", {name: "发送", exact: true}).click();
        await page.waitForFunction(() => !document.querySelector('.personal-quick-prompts button')?.disabled);
      }
      await send("解释一下 Goal 是什么");
      assert.equal(await page.locator(".personal-goal-draft").count(), 0);
      const writes = api.durableWriteCount;
      await send("研究微软近三年的现金流，先把目标理清");
      const card = page.getByRole("region", {name: "目标草稿"}).last();
      await card.waitFor();
      await page.screenshot({path: resolve(outputDir, "goal-draft.png"), animations: "disabled"});
      const requests = api.turnRequests.length;
      await card.getByRole("button", {name: "资本支出变化", exact: true}).click();
      assert.equal(await composer.inputValue(), "资本支出变化");
      assert.equal(api.turnRequests.length, requests, "Suggestion auto-sent a request");
      assert.equal(api.actionPreviews.length, 0);
      assert.equal(api.durableWriteCount, writes);
      await send("重点比较资本支出，列出来源");
      await card.getByText("重点比较资本支出，列出来源", {exact: true}).waitFor();
      await page.getByRole("button", {name: "查看完整对话", exact: true}).click();
      await page.reload({waitUntil: "networkidle"});
      await page.getByRole("navigation", {name: "管家视图"}).getByRole("button", {name: /^(Chat|对话)$/}).click();
      await card.getByText("重点比较资本支出，列出来源", {exact: true}).waitFor();
      await card.getByRole("button", {name: "修改", exact: true}).click();
      const form = page.getByRole("dialog", {name: "创建新 Goal"});
      assert.equal(await form.getByLabel("目标", {exact: true}).inputValue(), draft.objective);
      assert.equal(await form.getByLabel("完成标准", {exact: true}).inputValue(), "重点比较资本支出，列出来源");
      assert.equal(await form.getByLabel("执行权限", {exact: true}).inputValue(), "read_only");
      await form.getByLabel("完成标准", {exact: true}).fill("资本支出对照表与原始来源");
      await form.getByRole("button", {name: "检查配置"}).click();
      await page.getByText("确认执行", {exact: true}).waitFor();
      const preview = api.actionPreviews.at(-1);
      assert.equal(preview.action_kind, "goal.create");
      assert.equal(preview.normalized_parameters.permission, "read_only");
      assert.equal(preview.normalized_parameters.heartbeat.enabled, false);
      assert.equal(api.durableWriteCount, writes);
      await page.getByRole("button", {name: "关闭", exact: true}).click();
      await page.setViewportSize({width: 390, height: 844});
      await card.scrollIntoViewIfNeeded();
      const bounds = await card.boundingBox();
      assert.ok(bounds && bounds.x >= 0 && bounds.x + bounds.width <= 391);
      await page.screenshot({path: resolve(outputDir, "goal-draft-mobile.png"), animations: "disabled"});
      assert.equal(api.durableWriteCount, writes, "Cancelling the reviewed draft mutated state");
      await page.setViewportSize({width: 1512, height: 982});
      const beforePreview = api.actionPreviews.length;
      await card.getByRole("button", {name: "预览创建", exact: true}).click();
      await page.getByText("确认执行", {exact: true}).waitFor();
      assert.equal(await form.count(), 0, "Complete draft should skip the redundant form");
      assert.equal(api.actionPreviews.length, beforePreview + 1);
      assert.equal(api.actionPreviews.at(-1).normalized_parameters.completion_criteria, "重点比较资本支出，列出来源");
      assert.equal(api.actionPreviews.at(-1).normalized_parameters.permission, "read_only");
      assert.equal(api.actionPreviews.at(-1).normalized_parameters.heartbeat.enabled, false);
      const requestKey = api.actionPreviews.at(-1).idempotency_key;
      await page.getByRole("button", {name: "关闭", exact: true}).click();
      await page.reload({waitUntil: "networkidle"});
      await page.getByRole("navigation", {name: "管家视图"}).getByRole("button", {name: /^(Chat|对话)$/}).click();
      await card.getByRole("button", {name: "预览创建", exact: true}).click();
      await page.getByText("确认执行", {exact: true}).waitFor();
      assert.equal(api.actionPreviews.at(-1).idempotency_key, requestKey, "Reopening must preserve operation identity");
      await page.getByRole("button", {name: "创建 Goal 并开始首轮", exact: true}).last().click();
      await page.getByText("已应用，LoopX 状态将刷新。", {exact: true}).first().waitFor();
      assert.equal(api.durableWriteCount, writes + 1);
      assert.equal(api.actionApplies.length, 1);
      await page.keyboard.press("Escape");
      await page.locator(".personal-goal-link").first().click();
      await page.getByRole("navigation", {name: "Goal 视图"}).getByRole("button", {name: /^(Chat|对话)$/}).click();
      await send("研究微软近三年的现金流，先把目标理清");
      await card.getByRole("button", {name: "现金流趋势", exact: true}).waitFor();
      assert.equal(api.durableWriteCount, writes + 1, "Goal Chat draft launched work");
      await page.evaluate(() => localStorage.setItem("loopx-pw-locale", "en"));
      await page.reload({waitUntil: "networkidle"});
      await page.getByRole("navigation", {name: "Goal view"}).getByRole("button", {name: "Chat", exact: true}).click();
      const englishCard = page.getByRole("region", {name: "Goal draft"}).last();
      await englishCard.getByRole("button", {name: "Refine goal"}).waitFor();
      await englishCard.scrollIntoViewIfNeeded();
      await page.screenshot({path: resolve(outputDir, "goal-draft-english.png"), animations: "disabled"});
      assert.deepEqual(errors, []);
      return {coverageEntries: await close(), note: "Goal draft suggestions require explicit send; corrections and reload preserve draft; existing Goal preview retains read-only and disabled heartbeat defaults."};
    } catch(error) {
      await page.screenshot({path: resolve(outputDir, "goal-draft-failed.png")});
      const text = await page.locator("body").innerText();
      await close();
      throw new Error(`${error.message}; body=${text.slice(-6000)}`);
    }
  },
};
