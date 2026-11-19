import assert from "node:assert/strict";
import { openWorkspacePage } from "./scenario-context.mjs";

// A slow executor connection must never make a submitted message look lost.
export const conversationStartupScenario = {
  id: "conversation-startup",
  async run({ browser, collectCoverage, url }) {
    const waiting = [];
    let fail = false;
    let sourceAvailable = false;
    let sessionCreates = 0;
    const context = await openWorkspacePage(browser, url, {
      collectCoverage, gotoWaitUntil: "domcontentloaded",
      beforeGoto: async (_api, page) => {
        await page.route("**/api/chat/app-steward", route => sourceAvailable
          ? route.fallback()
          : route.fulfill({status: 503, json: {ok: false, error: "Source unavailable"}}));
        await page.route("**/api/chat/sessions", async route => {
          if (route.request().method() !== "POST") return route.fallback();
          sessionCreates += 1;
          if (!fail) await new Promise(resolve => waiting.push(resolve));
          if (fail) return route.fulfill({ status: 503, json: { ok: false, error_code: "host_gate", error: "Executor connection timed out." } });
          return route.fallback();
        });
      },
    });
    const { page, api } = context;
    try {
      await page.getByRole("alert").filter({hasText: "原管家会话暂不可用"}).waitFor();
      assert.equal(await page.getByLabel("向 LoopX 发送消息").isDisabled(), true);
      assert.equal(await page.getByRole("button", {name: "发送", exact: true}).isDisabled(), true);
      assert.equal(sessionCreates, 0, "failed source discovery cannot create an ordinary manager session");
      assert.equal(api.turnRequests.length, 0, "failed source discovery cannot dispatch work");
      sourceAvailable = true;
      await page.getByRole("button", {name: "重试读取", exact: true}).click();
      const send = async text => {
        await page.getByLabel("向 LoopX 发送消息").fill(text);
        await page.getByRole("button", { name: "发送", exact: true }).click();
      };
      await send("帮我整理研究目标，先不要执行。");
      assert.equal(await page.locator(".personal-manager-conversation-tray").count(), 0);
      assert.equal(await page.locator(".personal-chat-activity").count(), 0, "Preparation is not a running Turn");
      await page.getByRole("button", {name: "对话", exact: true}).click();
      const conversation = page.locator(".personal-channel-timeline");
      await conversation.locator(".personal-message-work-current").getByText("已接收，正在连接执行器", { exact: true }).waitFor();
      assert.equal(api.turnRequests.length, 0, "receipt precedes executor connection and turn submission");
      await conversation.getByRole("button", { name: "取消发送", exact: true }).click();
      await conversation.getByText("已取消发送；请求尚未交给执行器处理。", { exact: true }).waitFor();
      await page.getByLabel("向 LoopX 发送消息").fill("重试整理目标。");
      assert.equal(await page.getByRole("button", { name: "发送", exact: true }).isEnabled(), true);
      fail = true;
      waiting.splice(0).forEach(resolve => resolve());
      assert.equal(api.turnRequests.length, 0, "cancelled preparation cannot dispatch a turn");
      await send("重试整理目标。");
      await conversation.getByText("尚未提交请求。连接执行器失败，可以重新发送。", { exact: false }).waitFor();
      assert.equal(await conversation.getByRole("button", { name: "中断本轮", exact: true }).count(), 0, "no invented turn control after startup failure");
      assert.equal(await conversation.locator(".personal-message-work-current").count(), 0, "failed preparation stops presenting live progress");
      assert.equal(api.turnRequests.length, 0);
      return { coverageEntries: context.coverageEntries, note: "Immediate preparation receipt, pre-dispatch cancellation, retry and honest startup failure through the explicit Chat entry; preparation never covers the overview or claims to be running." };
    } finally {
      waiting.splice(0).forEach(resolve => resolve());
      await context.close();
    }
  },
};
