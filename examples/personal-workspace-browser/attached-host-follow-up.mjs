import { resolve } from "node:path";

import { runCodexAttached } from "./codex-attached.mjs";
import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

// An attached_host Session queues a follow-up while its host runs a Turn
// (ChatRuntimeController.submit_turn -> enqueue_attached_agent_turn), whereas a
// managed_runtime Session queues ordinary sends and accepts explicit steering when supported. The composer must follow
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
    await runCodexAttached(browser);
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
          const { message, delivery } = route.request().postDataJSON();
          posts.push({ message, sessionId, delivery });
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
          runtime.queuedTurns.set(sessionId, [...(runtime.queuedTurns.get(sessionId) ?? []), {
            turn_id: turnId, created_at: "2026-08-13T01:00:02Z", origin: "web",
            preview: message, character_count: message.length, attachment_count: 0, steering: null,
          }]);
          await route.fulfill({ contentType: "application/json", status: 202,
            json: { ok: true, schema_version: "loopx_chat_turn_accepted_v1", session_id: sessionId, turn_id: turnId, created: true, status: "queued", events_url: `/api/chat/sessions/${sessionId}/turns/${turnId}/events` } });
        });
      },
    });
    const { page } = context;
    const composer = page.getByLabel("向 LoopX 发送消息");
    const sendButton = page.getByRole("button", { name: "发送", exact: true });
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
      await page.locator(`.personal-composer-stop[data-turn-id="${runningTurnId(goal.id)}"]`).waitFor({ state: "visible", timeout: 5_000 });
    };
    try {
      await openGoalChat(managedGoal);
      await composer.fill("当前工作先检查依赖");
      if (await sendButton.isDisabled()) throw new Error("Managed Codex instructions were blocked");
      // This scenario isolates attached queues; exact native steering and retry
      // are checked by composer-session-admission. No new managed Turn is posted.
      if (posts.length) throw new Error("Managed readback created another Turn");
      await composer.fill("");

      await openGoalChat(attachedGoal);
      const followUp = "附着宿主回合进行中的后续消息";
      await composer.fill(followUp);
      await composer.press("Meta+Enter");
      await page.getByRole("status").filter({ hasText: "此执行器不支持本轮纠偏" }).waitFor();
      if (posts.length || await composer.inputValue() !== followUp) throw new Error("Unsupported host steering queued or lost the draft");
      if (await sendButton.isDisabled()) throw new Error("An attached_host composer blocked Send while its host ran a Turn");
      await sendButton.click();
      for (let attempt = 0; attempt < 100 && !posts.length; attempt += 1) await page.waitForTimeout(50);
      if (posts.length !== 1 || posts[0].sessionId !== sessionIdFor(attachedGoal.id) || posts[0].message !== followUp) {
        throw new Error(`The attached_host follow-up did not reach its Session queue: ${JSON.stringify(posts)}`);
      }
      const queuedFollowUp = page.locator(".personal-conversation-queue li").filter({ hasText: followUp });
      await queuedFollowUp.waitFor({ timeout: 10_000 });
      await queuedFollowUp.locator("summary").click();
      const expandedMessage = queuedFollowUp.locator(".personal-queue-message > p");
      await expandedMessage.waitFor();
      const assertExpandedBodyTypography = async (viewport) => {
        const typography = await expandedMessage.evaluate(element => {
          const style = getComputedStyle(element);
          const root = getComputedStyle(document.documentElement);
          return {
            fontSize: parseFloat(style.fontSize),
            lineHeight: parseFloat(style.lineHeight),
            bodySize: parseFloat(root.getPropertyValue("--text-body")),
            bodyLineHeight: parseFloat(root.getPropertyValue("--leading-body")),
          };
        });
        if (typography.fontSize !== typography.bodySize || typography.lineHeight !== typography.bodyLineHeight) {
          throw new Error(`Expanded queued message lost body typography at ${viewport}: ${JSON.stringify(typography)}`);
        }
      };
      await assertExpandedBodyTypography("1440x900");
      await page.locator(`.personal-composer-stop[data-turn-id="${runningTurnId(attachedGoal.id)}"]`).waitFor({ timeout: 10_000 });
      if (page.__loopxRuntime.sessions.get(sessionIdFor(attachedGoal.id)).active_turn_id !== runningTurnId(attachedGoal.id)) {
        throw new Error("Queuing the follow-up replaced the attached host's active Turn");
      }
      if (await composer.inputValue()) throw new Error("The queued follow-up stayed in the composer as if it had been rejected");
      if (await page.getByRole("alert").filter({ hasText: /发送失败|already running/u }).count()) {
        throw new Error("The queued follow-up was reported as a failed send");
      }
      await page.screenshot({ path: resolve(outputDir, "attached-host-pending-queue.png"), fullPage: false });
      await page.setViewportSize({ width: 390, height: 844 });
      await assertExpandedBodyTypography("390x844");
      const stop = page.locator(`.personal-composer-stop[data-turn-id="${runningTurnId(attachedGoal.id)}"]`);
      const box = await stop.boundingBox();
      if (!box || box.y < 0 || box.y + box.height > 844) throw new Error("The attached host Stop control left the narrow viewport");
      await page.screenshot({ path: resolve(outputDir, "attached-host-pending-queue-390.png"), fullPage: false });
      const interruptsBefore = context.api.interrupts.length;
      await stop.click();
      for (let attempt = 0; attempt < 100 && context.api.interrupts.length === interruptsBefore; attempt += 1) await page.waitForTimeout(50);
      const interrupted = context.api.interrupts.slice(interruptsBefore);
      if (interrupted.length !== 1 || interrupted[0].sessionId !== sessionIdFor(attachedGoal.id)
        || interrupted[0].turnId !== runningTurnId(attachedGoal.id)) {
        throw new Error(`Stop targeted the queued follow-up instead of the running attached host: ${JSON.stringify(interrupted)}`);
      }
      await stop.waitFor({ state: "hidden" });
      // The page still sees idle when another page's host starts a Turn.
      // Native admission queues the ordinary send; that receipt is not execution.
      const racedTurnId = "turn-host-started-elsewhere";
      const attachedSessionId = sessionIdFor(attachedGoal.id);
      let staleQueue = true;
      await page.route(`**/api/chat/sessions/${attachedSessionId}/queue`, route => staleQueue
        ? route.fulfill({ json: { ok: true, schema_version: "loopx_chat_store_v1", session_id: attachedSessionId,
          active_turn_id: null, turns: page.__loopxRuntime.queuedTurns.get(attachedSessionId) ?? [] } })
        : route.fallback());
      page.__loopxRuntime.sessions.set(attachedSessionId, { ...page.__loopxRuntime.sessions.get(attachedSessionId), active_turn_id: racedTurnId, status: "busy" });
      const racedFollowUp = "另一页面启动宿主时仍然只排队";
      await composer.fill(racedFollowUp);
      await sendButton.click();
      await page.locator(".personal-conversation-queue li").filter({ hasText: racedFollowUp }).waitFor({ timeout: 10_000 });
      if (posts.length !== 2 || posts[1].delivery !== undefined) throw new Error("The stale page did not exercise ordinary-send native queue admission");
      if (await page.getByText("请求已接收，等待执行器输出", { exact: true }).count()) throw new Error("A queued receipt appeared as an executing assistant reply");
      staleQueue = false;
      const racedStop = page.locator(`.personal-composer-stop[data-turn-id="${racedTurnId}"]`);
      await racedStop.waitFor({ timeout: 15_000 });
      await racedStop.click();
      for (let attempt = 0; attempt < 100 && context.api.interrupts.length !== interruptsBefore + 2; attempt += 1) await page.waitForTimeout(50);
      if (context.api.interrupts.at(-1)?.turnId !== racedTurnId) throw new Error("A native-queued admission took the other page's running Turn controls");
    } finally {
      for (const route of heldStreams.splice(0)) await route.abort().catch(() => {});
      await context.close();
    }
    return {
      coverageEntries: context.coverageEntries,
      note: "managed-runtime-running-turn: native instructions stay available without creating another Turn. "
        + "attached-host-follow-up: Send stays open, one POST reaches the persistent Session queue and current-Turn controls stay on the running host Turn.",
    };
  },
};
