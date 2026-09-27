import { openWorkspacePage } from "./scenario-context.mjs";

export const conversationReturnContinuityScenario = {
  id: "conversation-return-continuity",
  async run({ browser, collectCoverage, url }) {
    const oldSessionId = "previous-worker-session";
    const request = {
      schema_version: "collaboration_request_readback_v0", request_id: "b".repeat(64), agent_id: "researcher",
      brief: { purpose: "核对公开现金流数据", context: "继续已有研究。", constraints: [], inputs: [],
        acceptance: ["核对期间与单位"], return_requirement: "结果回到这段对话" },
      read_status: "supplied", decision: "adopt", returns: [],
    };
    const context = await openWorkspacePage(browser, url, {
      collectCoverage,
      beforeGoto(_api, page) {
        page.__loopxRuntime.sessions.set(oldSessionId, {
          session_id: oldSessionId, goal_id: "loopx-manager", agent_id: "codex", adapter_kind: "codex",
          channel_id: "manager", status: "ready", active_turn_id: null, resumable: true,
          created_at: "2026-08-12T01:00:00Z", updated_at: "2026-08-12T01:00:00Z",
          last_activity_at: "2026-08-12T01:00:00Z", last_error_code: null,
        });
        page.__loopxRuntime.messages.set(oldSessionId, [{
          message_id: "old-delegation", turn_id: "old-turn", role: "agent", text: "已交给研究负责人。",
          created_at: "2026-08-12T01:00:00Z", collaboration: request,
        }]);
      },
    });
    const { page, api } = context;
    try {
      await page.getByRole("navigation", { name: "管家视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
      await page.getByText("已交给研究负责人。", { exact: true }).waitFor({ state: "visible" });
      // A subsequent request runs in the new session while the old delegation
      // remains outstanding. Returning a result must not invoke another model.
      await page.getByLabel("向 LoopX 发送消息").fill("我现在该做什么？只读回答。");
      await page.getByRole("button", { name: "发送", exact: true }).click();
      await page.getByText("管家已读取当前授权范围的 Goal 证据。", { exact: true }).waitFor({ state: "visible" });
      if (api.turnRequests.at(-1).sessionId === oldSessionId) throw new Error("Fixture did not replace the active session");
      const turnsBeforeReturn = api.turnRequests.length;
      let droppedRead = false;
      await page.route(`**/api/chat/sessions/${oldSessionId}`, async (route) => {
        if (!droppedRead) { droppedRead = true; await route.abort("connectionreset"); }
        else await route.fallback();
      });
      const answer = "核对完成：现金流期间和单位一致。";
      page.__loopxRuntime.messages.get(oldSessionId).push({
        message_id: "handoff.old-result", turn_id: "old-turn", role: "agent", origin: "manager_followup",
        text: answer, created_at: "2026-08-13T01:00:03Z",
        return_delivery: { schema_version: "manager_return_delivery_status_v0", phase: "conclusion", status: "delivered" },
      });
      request.returns = [{ phase: "conclusion", status: "delivered" }];
      await page.getByText(answer, { exact: true }).waitFor({ state: "visible", timeout: 10_000 });
      // Delivery label owned by collaboration-card.tsx copy: main distinguishes
      // "reply awaiting delivery" from "reply delivered", so the delivered
      // conclusion reads 回复已送达 (English: Reply delivered).
      await page.getByText("回复已送达", { exact: true }).waitFor({ state: "visible" });
      if (!droppedRead) throw new Error("The old-session recovery path was not exercised");
      await page.waitForTimeout(3500);
      if (await page.getByText(answer, { exact: true }).count() !== 1) throw new Error("Late result duplicated");
      if (await page.getByRole("region", { name: "交办说明" }).count() !== 1) throw new Error("New-session readback erased the old delegation");
      if (api.turnRequests.length !== turnsBeforeReturn) throw new Error("Readback replayed the model");
      return { coverageEntries: context.coverageEntries, note: "Late result survives active-session replacement, stays linked to its original delegation and invokes no model turn." };
    } finally {
      await context.close();
    }
  },
};
