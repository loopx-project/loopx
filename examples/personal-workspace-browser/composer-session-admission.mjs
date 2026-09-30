import { openWorkspacePage } from "./scenario-context.mjs";

// The Chat service admits a message sent while a Turn runs according to the
// Session's mode (`ChatRuntimeController.submit_turn`): a managed runtime
// Session answers 409, so the composer waits; an attached host Session queues
// the message behind the host's running Turn, so the composer stays open.
// Both cases use the same Goal Session and running Turn; only the mode differs.
export const composerSessionAdmissionScenario = {
  id: "composer-session-admission",
  async run({ browser, collectCoverage, url }) {
    const context = await openWorkspacePage(browser, url, { collectCoverage });
    const { page } = context;
    const notes = [];
    const composerInput = page.getByLabel("向 LoopX 发送消息");
    const sendButton = page.getByRole("button", { name: "发送", exact: true });
    const turnRunningHint = page.locator(".personal-composer-status", { hasText: "本轮回答进行中" });
    const openGoalChat = async () => {
      await page.getByTestId("personal-goal-home").waitFor({ state: "visible" });
      await page.locator(".personal-goal-link").first().click();
      await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
      await composerInput.waitFor({ state: "visible" });
    };
    try {
      await openGoalChat();
      let goalSession;
      for (let attempt = 0; attempt < 100 && !goalSession; attempt += 1) {
        goalSession = [...page.__loopxRuntime.sessions.values()].find((session) => session.channel_id?.startsWith("goal."));
        if (!goalSession) await page.waitForTimeout(50);
      }
      if (!goalSession) throw new Error("Opening the Goal Chat did not bind a Goal Session");
      const sessionId = goalSession.session_id;

      // Start the Turn outside this page and reload, so the page learns of it
      // only from the Session read. Its stream is held so the Turn stays running.
      const reloadWithRunningTurn = async (sessionMode, turnId) => {
        page.__loopxRuntime.turnMessages.set(turnId, "另一入口发起的运行中回合");
        page.__loopxRuntime.sessions.set(sessionId, {
          ...page.__loopxRuntime.sessions.get(sessionId),
          active_turn_id: turnId,
          status: "busy",
          session_mode: sessionMode,
          host_surface: sessionMode === "attached_host" ? "codex_cli" : null,
        });
        const heldEvents = [];
        const routes = [`**/api/chat/sessions/${sessionId}/turns/${turnId}/events`, `**/api/chat/sessions/${sessionId}/turns`];
        await page.route(routes[0], (route) => { heldEvents.push(route); });
        const posts = [];
        await page.route(routes[1], async (route) => {
          if (route.request().method() !== "POST") return route.fallback();
          const body = route.request().postDataJSON();
          posts.push(body.message);
          const queuedTurnId = `${turnId}-queued-${posts.length}`;
          // A queued follow-up waits behind the running Turn; its stream is held too.
          routes.push(`**/api/chat/sessions/${sessionId}/turns/${queuedTurnId}/events`);
          await page.route(routes.at(-1), (held) => { heldEvents.push(held); });
          await route.fulfill({ contentType: "application/json", status: 202, json: {
            ok: true, schema_version: "loopx_chat_turn_accepted_v1", session_id: sessionId, turn_id: queuedTurnId,
            created: true, status: "queued", events_url: `/api/chat/sessions/${sessionId}/turns/${queuedTurnId}/events`,
          } });
        });
        await page.reload({ waitUntil: "domcontentloaded" });
        await openGoalChat();
        // The recovered reply is the page's own evidence that it saw the Turn run.
        await page.getByRole("button", { name: "中断本轮", exact: true }).waitFor({ state: "visible", timeout: 10_000 });
        return {
          posts,
          async release() {
            for (const pattern of routes) await page.unroute(pattern);
            await Promise.all(heldEvents.splice(0).map((held) => held.abort().catch(() => {})));
          },
        };
      };

      const managed = await reloadWithRunningTurn("managed_runtime", `turn-managed-${Date.now()}`);
      await turnRunningHint.waitFor({ state: "visible", timeout: 5_000 });
      await composerInput.fill("托管回合运行中不应发送");
      if (!(await sendButton.isDisabled())) throw new Error("A managed runtime Session's composer stayed sendable while its Turn ran");
      await sendButton.click({ force: true });
      await page.waitForTimeout(300);
      if (managed.posts.length !== 0) throw new Error(`The composer posted ${managed.posts.length} times into a running managed Turn`);
      await managed.release();
      notes.push("managed-runtime: a running Turn keeps Send closed and posts nothing");

      const attached = await reloadWithRunningTurn("attached_host", `turn-attached-${Date.now()}`);
      const followUp = "宿主回合运行中排队的下一条消息";
      await composerInput.fill(followUp);
      if (await turnRunningHint.count()) throw new Error("An attached host Session showed the managed-runtime running-Turn hint");
      if (await sendButton.isDisabled()) throw new Error("An attached host Session's composer closed while the host Turn ran, though the service queues follow-ups");
      await sendButton.click();
      for (let attempt = 0; attempt < 100 && attached.posts.length === 0; attempt += 1) await page.waitForTimeout(50);
      if (attached.posts.length !== 1 || attached.posts[0] !== followUp) {
        throw new Error(`The attached host follow-up was not posted exactly once: ${JSON.stringify(attached.posts)}`);
      }
      await attached.release();
      notes.push("attached-host: a running host Turn keeps Send open and the follow-up posts once to the same Session");

      return { coverageEntries: context.coverageEntries, note: notes.join(" ") };
    } finally {
      await context.close();
    }
  },
};
