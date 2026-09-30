import assert from "node:assert/strict";
import { resolve } from "node:path";
import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

export const conversationHistoryRecoveryScenario = {
  id: "conversation-history-recovery",
  async run({ browser, collectCoverage, url }) {
    const currentId = "session-manager-loopx-manager-codex";
    const oldId = "previous-worker-session";
    let unavailable = oldId;
    let failedReads = 0;
    const context = await openWorkspacePage(browser, url, {
      collectCoverage,
      beforeGoto(_api, page) {
        for (const [id, agentId, date, text] of [
          ["other-executor-session", "claude-code", "2026-08-14T01:00:00Z", "另一个执行器保留了独立对话。"],
          [currentId, "codex", "2026-08-13T01:00:00Z", "目前正在核对微软现金流。"],
          [oldId, "codex", "2026-08-12T01:00:00Z", "上一轮已经找到公开财报。"],
        ]) {
          page.__loopxRuntime.sessions.set(id, {
            session_id: id, goal_id: "loopx-manager", agent_id: agentId, adapter_kind: agentId,
            channel_id: "manager", status: "ready", active_turn_id: null, resumable: true,
            created_at: date, updated_at: date, last_activity_at: date, last_error_code: null,
          });
          // Message identity is scoped to a session, even if IDs collide.
          page.__loopxRuntime.messages.set(id, [{
            message_id: "answer", turn_id: `turn-${id}`, role: "agent", text, created_at: date,
          }]);
        }
        return page.route("**/api/chat/sessions/*", async (route) => {
          if (route.request().method() === "GET" && new URL(route.request().url()).pathname === `/api/chat/sessions/${unavailable}`) {
            failedReads += 1;
            await route.fulfill({ status: 503, json: { ok: false, error: "Synthetic history read unavailable" } });
          } else await route.fallback();
        });
      },
    });
    const { page, api } = context;
    try {
      await page.getByRole("navigation", { name: "管家视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
      await page.getByText("目前正在核对微软现金流。", { exact: true }).waitFor({ state: "visible", timeout: 5000 });
      const notice = page.getByRole("status").filter({ hasText: "部分历史暂时无法读取" });
      await notice.waitFor({ state: "visible" });
      await page.screenshot({ path: resolve(outputDir, "conversation-history-partial-desktop.png"), animations: "disabled" });
      await page.setViewportSize({ width: 390, height: 844 });
      assert.ok(await notice.isVisible(), "Recovery feedback stays visible at narrow width");
      assert.ok(await page.locator("body").evaluate(body => body.scrollWidth <= innerWidth + 1), "Recovery must not add horizontal overflow");
      await page.screenshot({ path: resolve(outputDir, "conversation-history-partial-mobile.png"), animations: "disabled" });
      await page.setViewportSize({ width: 1512, height: 982 });
      assert.ok(failedReads > 0, "The unavailable historical read was exercised");
      assert.equal(api.turnRequests.length, 0, "Reading history must not start work");
      unavailable = "";
      await notice.getByRole("button", { name: "重试读取" }).click();
      await page.getByText("上一轮已经找到公开财报。", { exact: true }).waitFor({ state: "visible" });
      await notice.waitFor({ state: "hidden" });
      assert.equal(await page.getByText("目前正在核对微软现金流。", { exact: true }).count(), 1);
      assert.equal(api.turnRequests.length, 0, "Retry must not replay a model turn");

      // The current session's unreadability is a different boundary: keep the
      // known transcript, but do not send into a guessed replacement session.
      unavailable = currentId;
      await page.reload({ waitUntil: "networkidle" });
      await page.getByRole("navigation", { name: "管家视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
      await page.getByText("上一轮已经找到公开财报。", { exact: true }).waitFor({ state: "visible" });
      await page.getByText("正在恢复当前会话，恢复后可以继续发送。", { exact: true }).waitFor({ state: "visible" });
      await page.getByLabel("向 LoopX 发送消息").fill("接着昨天的做。");
      assert.equal(await page.getByRole("button", { name: "发送", exact: true }).isEnabled(), false);
      await page.getByLabel("向 LoopX 发送消息").press("Enter");
      assert.equal(api.turnRequests.length, 0, "Enter must not bypass current-session recovery");
      unavailable = "";
      await page.getByRole("button", { name: "重试读取", exact: true }).click();
      await page.getByRole("button", { name: "发送", exact: true }).waitFor({ state: "visible" });
      await page.waitForFunction(() => !document.querySelector('button[aria-label="发送"]')?.disabled);
      assert.equal(await page.getByLabel("向 LoopX 发送消息").inputValue(), "接着昨天的做。", "Recovery retains the draft");
      await page.getByRole("button", { name: "发送", exact: true }).click();
      await page.getByText("已沿用当前 Goal 与 Agent Session。接下来会先核对状态，再继续推进。", { exact: true }).waitFor({ state: "visible" });
      assert.equal(api.turnRequests.length, 1);
      assert.equal(api.turnRequests[0].sessionId, currentId);
      await page.locator(".personal-goal-link").first().click();
      await page.locator(".personal-manager-link").click();
      await page.getByRole("navigation", { name: "管家视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
      await page.getByText("接着昨天的做。", { exact: true }).waitFor({ state: "visible" });
      assert.equal(await page.getByText("接着昨天的做。", { exact: true }).count(), 1, "Rehydration must not duplicate the user's accepted request");
      assert.equal(api.turnRequests.length, 1, "Returning to the conversation must not repeat work");
      await page.getByRole("combobox", { name: "选择聊天 Runtime", exact: true }).click();
      await page.getByRole("option", { name: "仅查状态", exact: true }).click();
      await page.getByLabel("向 LoopX 发送消息").fill("只看当前状态。");
      const projectionSaved = page.waitForResponse(response => new URL(response.url()).pathname === "/api/chat/projection-messages" && response.status() === 201);
      await page.getByRole("button", { name: "发送", exact: true }).click();
      await projectionSaved;
      await page.locator(".personal-goal-link").first().click();
      await page.locator(".personal-manager-link").click();
      await page.getByRole("navigation", { name: "管家视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
      assert.equal(await page.getByText("只看当前状态。", { exact: true }).count(), 1, "Projection-only messages retain their exact stored identities");
      assert.equal(api.turnRequests.length, 1, "Projection history does not run an Agent");
      return { coverageEntries: context.coverageEntries, note: "One failed history read preserves readable messages; read-only retry recovers session-scoped records, retains the draft and sends exactly once into the recovered current session." };
    } finally {
      await context.close();
    }
  },
};
