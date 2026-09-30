import { openWorkspacePage } from "./scenario-context.mjs";

// The ordinary composer steers a managed Codex Turn through exact-turn ingress;
// attached hosts retain queued follow-ups and unsupported adapters remain blocked.
// Browser routes are synthetic; the production HTTP/store/subprocess contract is
// independently covered by tests/test_chat_turn_steering.py.
export const composerSessionAdmissionScenario = {
  id: "composer-session-admission",
  async run({ browser, collectCoverage, url }) {
    const context = await openWorkspacePage(browser, url, { collectCoverage,
      beforeGoto: async (_api, page) => page.addInitScript(() => {
        if (!sessionStorage.getItem("loopx-pw-composer-steering")) {
          sessionStorage.setItem("loopx-pw-composer-steering", JSON.stringify([["invalid", { id: "incomplete" }]]));
        }
      }),
    });
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
      const reloadWithRunningTurn = async (sessionMode, turnId, adapterKind = "codex_app_server") => {
        page.__loopxRuntime.turnMessages.set(turnId, "另一入口发起的运行中回合");
        page.__loopxRuntime.sessions.set(sessionId, {
          ...page.__loopxRuntime.sessions.get(sessionId),
          active_turn_id: turnId,
          status: "busy",
          session_mode: sessionMode,
          adapter_kind: adapterKind,
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
            created: true, status: sessionMode === "attached_host" ? "queued" : "running", events_url: `/api/chat/sessions/${sessionId}/turns/${queuedTurnId}/events`,
          } });
        });
        await page.reload({ waitUntil: "domcontentloaded" });
        await openGoalChat();
        // The recovered reply is the page's own evidence that it saw the Turn run.
        await page.getByRole("button", { name: "中断本轮", exact: true }).waitFor({ state: "visible", timeout: 10_000 });
        return {
          posts, heldEvents, turnId,
          async release() {
            for (const pattern of routes) await page.unroute(pattern);
            await Promise.all(heldEvents.splice(0).map((held) => held.abort().catch(() => {})));
          },
        };
      };

      const managed = await reloadWithRunningTurn("managed_runtime", `turn-managed-${Date.now()}`);
      const adjustments = [];
      let delayedReceipt;
      await page.route(`**/api/chat/sessions/${sessionId}/turns/*/steer`, async (route) => {
        const body = route.request().postDataJSON();
        const target = new URL(route.request().url()).pathname.split("/")[6];
        adjustments.push({ ...body, turnId: target });
        if ([1, 6].includes(adjustments.length)) return route.fulfill({ status: 409, json: { ok: false, error: "接收状态未确认" } });
        if (adjustments.length === 4) return route.fulfill({ status: 409, json: { ok: false, error: "本次未送达", delivery_state: "not_delivered" } });
        const receipt = { ok: true, session_id: sessionId, turn_id: adjustments.length === 2 ? "wrong-turn" : target,
          client_ingress_id: body.client_ingress_id, status: "delivered", created: adjustments.length !== 7 };
        if (adjustments.length === 3) { delayedReceipt = () => route.fulfill({ json: receipt }); return; }
        await route.fulfill({ json: receipt });
      });
      const instruction = "先做中文，别发布。";
      await page.locator(".personal-composer-status", { hasText: "发消息可调整当前工作" }).waitFor();
      await composerInput.fill(instruction);
      if (await sendButton.isDisabled()) throw new Error("The native Turn cannot receive instructions from its ordinary composer");
      await sendButton.click();
      await page.getByRole("status").filter({ hasText: "接收状态未确认" }).waitFor();
      await Promise.all(managed.heldEvents.splice(0).map(route => route.abort().catch(() => {})));
      await page.reload({ waitUntil: "domcontentloaded" });
      await openGoalChat();
      await page.getByRole("button", { name: "中断本轮", exact: true }).waitFor();
      if (adjustments.length !== 1 || managed.posts.length || await composerInput.inputValue() !== instruction) {
        throw new Error("Restoring an unconfirmed instruction sent automatically or lost its draft");
      }
      await sendButton.click();
      await page.getByRole("status").filter({ hasText: "回执不匹配" }).waitFor();
      if (await composerInput.inputValue() !== instruction) throw new Error("Unconfirmed steering lost its draft");
      await sendButton.click();
      for (let attempt = 0; attempt < 100 && !delayedReceipt; attempt += 1) await page.waitForTimeout(50);
      if (!delayedReceipt) throw new Error("Retry did not reach the original Turn");
      await composerInput.fill("先写下另一条指令");
      await delayedReceipt();
      await page.getByRole("status").filter({ hasText: "执行器已接收本轮追加指令" }).waitFor();
      if (await composerInput.inputValue() !== "先写下另一条指令") throw new Error("Delivery erased the draft typed while it was sending");
      if (new Set(adjustments.slice(0, 3).map(row => row.client_ingress_id)).size !== 1) throw new Error("Uncertain retries minted new ingress identities");
      await composerInput.fill(instruction);
      await sendButton.click();
      await page.getByRole("status").filter({ hasText: "本次未送达" }).waitFor();
      await sendButton.click();
      await page.getByRole("status").filter({ hasText: "执行器已接收本轮追加指令" }).waitFor();
      if (adjustments[3].client_ingress_id === adjustments[4].client_ingress_id) throw new Error("Confirmed non-delivery could not retry after recovery");

      // Lose a response, then let the original Turn finish. Retry reads its own
      // receipt; it must neither create another Turn nor steer a newer one.
      await composerInput.fill(instruction);
      await sendButton.click();
      await page.getByRole("status").filter({ hasText: "接收状态未确认" }).waitFor();
      page.__loopxRuntime.sessions.set(sessionId, { ...page.__loopxRuntime.sessions.get(sessionId), active_turn_id: null, status: "ready" });
      page.__loopxRuntime.messages.get(sessionId).push({ message_id: "stored-correction", turn_id: managed.turnId,
        role: "user", text: instruction, created_at: "2026-08-13T01:00:01Z" });
      const completed = { event_id: "done", sequence: 1, kind: "turn.completed", payload: { response: { schema_version: "loopx_chat_agent_response_v0", message: "本轮已完成", proposals: [], gate: null } } };
      await Promise.all(managed.heldEvents.splice(0).map(route => route.fulfill({ contentType: "text/event-stream",
        body: `id: done\nevent: turn.completed\ndata: ${JSON.stringify(completed)}\n\n` })));
      await page.getByText("本轮已完成", { exact: true }).waitFor();
      await page.reload({ waitUntil: "domcontentloaded" });
      await openGoalChat();
      if (adjustments.length !== 6 || managed.posts.length) throw new Error("Reload automatically replayed uncertain work");
      if (await composerInput.inputValue() !== instruction) throw new Error("Reload lost the unconfirmed correction draft");
      const storedInstruction = page.locator(".personal-message").getByText(instruction, { exact: true });
      await storedInstruction.waitFor();
      await sendButton.click();
      await page.getByRole("status").filter({ hasText: "执行器已接收本轮追加指令" }).waitFor();
      if (await storedInstruction.count() !== 1) {
        throw new Error("Reading a delivered receipt duplicated its stored instruction in the conversation");
      }
      if (adjustments.length !== 7 || adjustments[5].client_ingress_id !== adjustments[6].client_ingress_id
        || adjustments.some(row => row.turnId !== managed.turnId) || managed.posts.length) {
        throw new Error("Steering retry changed its identity/target or started another Turn");
      }
      if (await page.evaluate(() => JSON.parse(sessionStorage.getItem("loopx-pw-composer-steering")).length)) {
        throw new Error("Accepted instructions left an uncertain retry cached");
      }
      // A Turn sent from this page keeps its original send promise pending.
      // That promise must not block the same composer's native instructions.
      await composerInput.fill("给 LoopX 做份社区问卷，先给我草稿。");
      await sendButton.click();
      await page.locator(".personal-composer-status", { hasText: "发消息可调整当前工作" }).waitFor();
      await composerInput.fill(instruction);
      if (await sendButton.isDisabled()) throw new Error("Original send promise blocks native instructions");
      await sendButton.click();
      await page.getByRole("status").filter({ hasText: "执行器已接收本轮追加指令" }).waitFor();
      if (adjustments.length !== 8 || managed.posts.length !== 1
        || adjustments[7].turnId === managed.turnId || await composerInput.inputValue()) {
        throw new Error("Instructions during the original send started new work or failed to clear the submitted draft");
      }
      await managed.release();
      notes.push("managed Codex: ordinary composer steers its exact Turn while the original send waits, retaining drafts and retry identity, including after completion and reload; restored requests never dispatch automatically");

      const unsupported = await reloadWithRunningTurn("managed_runtime", `turn-unsupported-${Date.now()}`, "external");
      await turnRunningHint.waitFor();
      await composerInput.fill("不应向不支持的执行器追加指令");
      if (!await sendButton.isDisabled()) throw new Error("Unsupported executor was offered native steering");
      await sendButton.click({ force: true });
      await page.waitForTimeout(100);
      if (unsupported.posts.length || adjustments.length !== 8) throw new Error("Unsupported executor received an effect");
      await composerInput.fill("");
      await unsupported.release();
      notes.push("unsupported managed adapter: draft retained and no Turn or steering effect");

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
