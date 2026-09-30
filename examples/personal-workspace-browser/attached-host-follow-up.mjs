import { openWorkspacePage } from "./scenario-context.mjs";

// An attached_host Session queues a follow-up while its host runs a Turn
// (ChatRuntimeController.submit_turn -> enqueue_attached_agent_turn), whereas a
// managed_runtime Session accepts one Turn at a time with native steering when supported. The composer must follow
// the Session's typed mode, not read every running Turn as a rejected send.
const attachedGoal = { id: "product-release", label: "Product Release" };
const managedGoal = { id: "research-monitor", label: "Research Monitor" };
const runningTurnId = (goalId) => `turn-running-${goalId}`;
const sessionIdFor = (goalId) => `session-goal-${goalId}-codex`;

function runningSession(goalId, mode) {
  return {
    session_id: sessionIdFor(goalId), goal_id: goalId, agent_id: "codex", adapter_kind: "codex_app_server",
    channel_id: `goal.${goalId}`, status: "busy", active_turn_id: runningTurnId(goalId), last_error_code: null,
    created_at: "2026-08-13T01:00:00Z", updated_at: "2026-08-13T01:00:01Z", last_activity_at: "2026-08-13T01:00:01Z",
    resumable: true, session_mode: mode, host_surface: mode === "attached_host" ? "codex_app" : null,
  };
}

export const attachedHostFollowUpScenario = {
  id: "attached-host-follow-up",
  async run({ browser, collectCoverage, url }) {
    const heldStreams = [];
    const posts = [];
    const context = await openWorkspacePage(browser, url, {
      collectCoverage,
      beforeGoto: async (_api, page) => {
        const runtime = page.__loopxRuntime;
        for (const [goal, mode] of [[attachedGoal, "attached_host"], [managedGoal, "managed_runtime"]]) {
          runtime.sessions.set(sessionIdFor(goal.id), runningSession(goal.id, mode));
          runtime.messages.set(sessionIdFor(goal.id), []);
        }
        // Both Turns keep running for the whole check.
        await page.route("**/api/chat/sessions/*/turns/*/events", (route) => { heldStreams.push(route); });
        await page.route("**/api/chat/sessions/*/turns", async (route) => {
          if (route.request().method() !== "POST") return route.fallback();
          const sessionId = new URL(route.request().url()).pathname.split("/")[4];
          const session = runtime.sessions.get(sessionId);
          const { message } = route.request().postDataJSON();
          posts.push({ message, sessionId });
          if (session?.session_mode !== "attached_host") {
            // The managed_runtime answer the composer exists to avoid.
            await route.fulfill({ contentType: "application/json", status: 409,
              json: { ok: false, error: "another turn is already running for this session", active_turn_id: session?.active_turn_id } });
            return;
          }
          // Like create_queued_turn: the follow-up is queued and the running
          // Turn stays the Session's active Turn.
          const turnId = `turn-queued-${posts.length}`;
          runtime.messages.get(sessionId)?.push({ message_id: `${turnId}-user`, turn_id: turnId, role: "user", text: message, created_at: "2026-08-13T01:00:02Z" });
          await route.fulfill({ contentType: "application/json", status: 202,
            json: { ok: true, schema_version: "loopx_chat_turn_accepted_v1", session_id: sessionId, turn_id: turnId, created: true, status: "queued", events_url: `/api/chat/sessions/${sessionId}/turns/${turnId}/events` } });
        });
      },
    });
    const { page } = context;
    const composer = page.getByLabel("向 LoopX 发送消息");
    const sendButton = page.getByRole("button", { name: "发送", exact: true });
    const turnRunningHint = page.locator(".personal-composer-status", { hasText: "本轮回答进行中" });
    const waitForStream = async (turnId) => {
      const streamed = () => heldStreams.some((route) => route.request().url().includes(`/turns/${turnId}/events`));
      for (let attempt = 0; attempt < 100 && !streamed(); attempt += 1) await page.waitForTimeout(50);
      return streamed();
    };
    const openGoalChat = async (goal) => {
      await page.locator(".personal-goal-link", { hasText: goal.label }).click();
      await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
      // The recovery reads the Session and resumes its running Turn.
      if (!await waitForStream(runningTurnId(goal.id))) throw new Error(`${goal.label} did not resume its running Turn`);
      await page.getByRole("button", { name: "中断本轮", exact: true }).waitFor({ state: "visible", timeout: 5_000 });
    };
    try {
      await openGoalChat(managedGoal);
      await page.locator(".personal-composer-status", { hasText: "发消息可调整当前工作" }).waitFor();
      await composer.fill("当前工作先检查依赖");
      if (await sendButton.isDisabled()) throw new Error("Managed Codex instructions were blocked");
      // This scenario isolates attached queues; exact native steering and retry
      // are checked by composer-session-admission. No new managed Turn is posted.
      if (posts.length) throw new Error("Managed readback created another Turn");
      await composer.fill("");

      await openGoalChat(attachedGoal);
      const followUp = "附着宿主回合进行中的后续消息";
      await composer.fill(followUp);
      if (await turnRunningHint.count()) throw new Error("An attached_host composer showed the one-Turn wait hint");
      if (await sendButton.isDisabled()) throw new Error("An attached_host composer blocked Send while its host ran a Turn");
      await sendButton.click();
      for (let attempt = 0; attempt < 100 && !posts.length; attempt += 1) await page.waitForTimeout(50);
      if (posts.length !== 1 || posts[0].sessionId !== sessionIdFor(attachedGoal.id) || posts[0].message !== followUp) {
        throw new Error(`The attached_host follow-up did not reach its Session queue: ${JSON.stringify(posts)}`);
      }
      await page.locator(".personal-channel-timeline .personal-message").filter({ hasText: followUp }).first().waitFor({ timeout: 5_000 });
      if (!await waitForStream("turn-queued-1")) throw new Error("The page did not follow the queued follow-up Turn it was given");
      if (await composer.inputValue()) throw new Error("The queued follow-up stayed in the composer as if it had been rejected");
      if (await page.getByRole("alert").filter({ hasText: /发送失败|already running/u }).count()) {
        throw new Error("The queued follow-up was reported as a failed send");
      }
    } finally {
      for (const route of heldStreams.splice(0)) await route.abort().catch(() => {});
      await context.close();
    }
    return {
      coverageEntries: context.coverageEntries,
      note: "managed-runtime-running-turn: native instructions stay available without creating another Turn. "
        + "attached-host-follow-up: while the host runs a Turn, Send stays open, one POST reaches the Session queue and the page follows the queued Turn.",
    };
  },
};
