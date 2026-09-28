import assert from "node:assert/strict";
import { resolve } from "node:path";
import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

// Synthetic adapter responses test the App's transport and effects, not model understanding.
export const conversationInputScenario = {
  id: "conversation-input",
  async run({ browser, collectCoverage, url }) {
    const context = await openWorkspacePage(browser, url, { collectCoverage });
    const { page, api, errors, close } = context;
    try {
      const composer = page.getByLabel("向 LoopX 发送消息");
      async function send(text) {
        const count = api.turnRequests.length;
        const previews = api.actionPreviews.length;
        const writes = api.durableWriteCount;
        await composer.fill(text);
        await page.getByRole("button", { name: "发送", exact: true }).click();
        const deadline = Date.now() + 10_000;
        while (api.turnRequests.length === count && Date.now() < deadline) await page.waitForTimeout(25);
        await page.waitForFunction(() => !document.querySelector('.personal-quick-prompts button')?.disabled);
        assert.equal(api.turnRequests.length, count + 1, `Request did not reach Chat exactly once: ${text}`);
        assert.equal(api.turnRequests.at(-1).message, text);
        assert.equal(api.actionPreviews.length, previews, `Browser manufactured a preview: ${text}`);
        assert.equal(api.durableWriteCount, writes);
      }
      await send("解释一下创建 Goal 和创建任务有什么不同");
      await send("建个目标：研究微软近三年的现金流，给我份报告。");
      const managerSession = api.turnRequests.at(-1).sessionId;
      await page.locator(".personal-goal-link").first().click();
      await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
      for (const text of [
        "解释一下 monitor 的工作原理", "比较 daily workflow 与一次性任务",
        "文档写着 set up a heartbeat，解释这句话", "先把报告做完，再讨论是否创建监控",
        "创建一个任务，先不要执行，也不要设置 heartbeat", "让 Claude Code 负责管理这个 Goal",
        "帮我修复一下这个问题，完成以后把报告给我", "Do not create a goal; explain daily progress.",
      ]) await send(text);
      assert.notEqual(api.turnRequests.at(-1).sessionId, managerSession, "Goal request crossed into manager conversation");
      await page.screenshot({ path: resolve(outputDir, "conversation-input.png"), fullPage: false, animations: "disabled" });
      const previews = api.actionPreviews.length;
      const writes = api.durableWriteCount;
      await composer.fill("这段话还没发，不要丢。");
      await page.locator(".personal-composer-tools > summary").click();
      await page.getByRole("button", { name: "配置定时检查", exact: true }).click();
      const schedule = page.getByRole("dialog", { name: "配置定时检查", exact: true });
      await schedule.getByLabel("检查间隔", { exact: true }).fill("0");
      await schedule.getByRole("button", { name: "检查配置" }).click();
      assert.equal(api.actionPreviews.length, previews, "Invalid interval admitted");
      await schedule.getByLabel("检查间隔", { exact: true }).fill("2");
      await schedule.getByLabel("检查内容", { exact: true }).fill("核对公开财报的新版本");
      await page.screenshot({ path: resolve(outputDir, "schedule-explicit-form.png"), animations: "disabled" });
      await schedule.getByRole("button", { name: "检查配置" }).click();
      await page.getByText("确认执行", { exact: true }).waitFor();
      assert.equal(api.actionPreviews.at(-1).action_kind, "monitor.create");
      assert.equal(api.actionPreviews.at(-1).normalized_parameters.cadence, "2h");
      assert.equal(api.actionPreviews.at(-1).normalized_parameters.target, "核对公开财报的新版本");
      assert.equal(api.durableWriteCount, writes);
      await page.getByRole("button", { name: "关闭", exact: true }).click();
      assert.equal(await composer.inputValue(), "这段话还没发，不要丢。");
      await page.locator(".personal-manager-link").first().click();
      await page.locator(".personal-composer-tools > summary").click();
      await page.getByRole("button", { name: "创建新 Goal", exact: true }).last().click();
      const goal = page.getByRole("dialog", { name: "创建新 Goal", exact: true });
      await goal.getByLabel("执行权限", {exact: true}).selectOption("read_only");
      await goal.getByLabel("目标", { exact: true }).fill("研究微软的现金流");
      await goal.getByLabel("完成标准", { exact: true }).fill("给出数据来源和可读报告");
      await goal.getByLabel("执行边界（可选）", { exact: true }).fill("每天研究不代表要创建监控");
      await goal.getByLabel("完成标准", { exact: true }).fill("数".repeat(400));
      await goal.getByRole("status").waitFor();
      assert.equal(await goal.getByRole("button", { name: "检查配置" }).isDisabled(), true, "Over-limit Goal fields reached preview");
      await goal.getByLabel("完成标准", { exact: true }).fill("给出数据来源和可读报告");
      api.failNextActionPreview = true;
      await goal.getByRole("button", { name: "检查配置" }).click();
      await goal.getByRole("alert").waitFor();
      assert.equal(await goal.getByLabel("目标", { exact: true }).inputValue(), "研究微软的现金流");
      await page.screenshot({ path: resolve(outputDir, "goal-explicit-form.png"), animations: "disabled" });
      await goal.getByRole("button", { name: "检查配置" }).click();
      await page.getByText("确认执行", { exact: true }).waitFor();
      const params = api.actionPreviews.at(-1).normalized_parameters;
      assert.equal(params.title, "研究微软的现金流");
      assert.equal(params.permission, "read_only");
      assert.equal(params.heartbeat.enabled, false);
      assert.equal(api.durableWriteCount, writes);
      await page.getByRole("button", {name: "关闭", exact: true}).click();
      await page.setViewportSize({width: 390, height: 844});
      await page.getByRole("button", {name: "创建新 Goal", exact: true}).last().click();
      const mobileForm = page.getByRole("dialog", {name: "创建新 Goal", exact: true});
      const bounds = await mobileForm.boundingBox();
      assert.ok(bounds && bounds.x >= 0 && bounds.x + bounds.width <= 391, "Configuration overflows narrow viewport");
      await page.screenshot({path: resolve(outputDir, "goal-explicit-form-mobile.png"), animations: "disabled"});
      await page.keyboard.press("Escape");
      await mobileForm.waitFor({state: "hidden"});
      assert.equal(api.durableWriteCount, writes, "Cancel mutated state");
      assert.deepEqual(errors, ["Failed to load resource: the server responded with a status of 503 (Service Unavailable)"]);
      return { coverageEntries: await close(), note: "Ordinary requests reach scoped Chat unchanged; explicit forms validate, retain drafts on failure and preview without writes." };
    } catch (error) { await page.screenshot({path: resolve(outputDir, "conversation-input-failed.png")}); const body = await page.locator("body").innerText(); await close(); throw new Error(`${error.message}; body=${body.slice(-7000)}`); }
  },
};
