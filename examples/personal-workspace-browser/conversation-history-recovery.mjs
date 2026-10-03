import assert from "node:assert/strict";
import { resolve } from "node:path";
import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

async function recoveryObserverHandover(browser, url) {
  const sessionId = "session-goal-product-release-codex";
  const turnId = "recovered-survey-draft";
  let releasePreview;
  let releaseNewTurn;
  let markPreviewStarted;
  let previewHeld = false;
  const previewWait = new Promise(resolve => { releasePreview = resolve; });
  const newTurnWait = new Promise(resolve => { releaseNewTurn = resolve; });
  const previewStarted = new Promise(resolve => { markPreviewStarted = resolve; });
  const context = await openWorkspacePage(browser, url, {
    async beforeGoto(api, page) {
      const runtime = page.__loopxRuntime;
      runtime.sessions.set(sessionId, {
        session_id: sessionId, goal_id: "product-release", agent_id: "codex", adapter_kind: "codex_app_server",
        channel_id: "goal.product-release", status: "busy", active_turn_id: turnId, resumable: true,
        created_at: "2026-08-13T01:00:00Z", updated_at: "2026-08-13T01:00:01Z",
        last_activity_at: "2026-08-13T01:00:01Z", last_error_code: null,
      });
      runtime.messages.set(sessionId, [{ message_id: "survey-request", turn_id: turnId, role: "user",
        text: "给 LoopX 做份社区问卷，先给我草稿。", created_at: "2026-08-13T01:00:01Z" }]);
      runtime.turnMessages.set(turnId, "community survey");
      api.answerForMessage = () => ({ message: "问卷草稿已整理。", proposals: [
        { kind: "todo", priority: "P1", text: "审阅社区问卷草稿", rationale: "发布前先审阅。" },
      ] });
      await page.route("**/events/**", async route => {
        // Keep the replacement worker active until its scoped Stop is tested;
        // completion of the old projection must not control this stream.
        await newTurnWait;
        await route.fulfill({ contentType: "text/event-stream", body: "", status: 200 });
      });
      await page.route("**/api/actions?**", async route => {
        if (!previewHeld && runtime.completedTurns.has(JSON.stringify([sessionId, turnId]))) {
          previewHeld = true;
          markPreviewStarted();
          await previewWait;
        }
        await route.fallback();
      });
    },
  });
  const { page, api } = context;
  const goalChat = async () => {
    await page.locator(".personal-goal-link").filter({ hasText: "Product Release" }).click();
    await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: "对话", exact: true }).click();
  };
  try {
    await goalChat();
    await Promise.race([previewStarted, new Promise((_, reject) => setTimeout(() => reject(new Error("Recovery never reached its draft read")), 10000))]);
    assert.equal(previewHeld, true, "The completed recovery is still awaiting its draft projection");
    await page.locator(".personal-manager-link").click();
    await goalChat();
    await page.locator(".personal-message-pending").waitFor({ state: "hidden", timeout: 5000 });
    assert.equal(api.turnRequests.length, 0, "Leaving and returning only observes the stored completion");
    const composer = page.getByLabel("向 LoopX 发送消息");
    await composer.fill("先做中文，别发布。");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await page.locator(".personal-message-pending").waitFor({ state: "visible" });
    assert.equal(api.turnRequests.length, 1, "The new request is admitted rather than steered to the ended Turn");
    releasePreview();
    await page.locator(".personal-proposal-row").filter({ hasText: "审阅社区问卷草稿" }).waitFor({ state: "visible" });
    const stop = page.getByRole("button", { name: "中断本轮", exact: true });
    await stop.scrollIntoViewIfNeeded();
    await page.screenshot({ path: resolve(outputDir, "conversation-observer-handover-desktop.png"), animations: "disabled" });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(await page.locator("body").evaluate(body => body.scrollWidth <= innerWidth + 1));
    await stop.focus();
    await page.screenshot({ path: resolve(outputDir, "conversation-observer-handover-mobile.png"), animations: "disabled" });
    await stop.press("Enter");
    await page.waitForFunction(() => !document.querySelector(".personal-message-pending"));
    assert.equal(api.interrupts.at(-1)?.turnId, api.turnRequests[0].turnId,
      "Late cleanup of the old observer cannot erase the new Turn's control identity");
    assert.equal(api.actionApplies.length, 0, "Observation and interruption do not apply a proposed task");
  } finally {
    releasePreview();
    releaseNewTurn();
    await context.close();
  }
}

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
      await recoveryObserverHandover(browser, url);
      return { coverageEntries: context.coverageEntries, note: "History read recovery retains the draft and exact session. Navigation retires only its display observer; delayed recovery projection cannot block a new request or erase its interrupt target." };
    } finally {
      await context.close();
    }
  },
};
