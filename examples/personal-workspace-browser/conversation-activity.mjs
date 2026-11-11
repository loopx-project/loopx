import assert from "node:assert/strict";
import { createServer } from "node:http";
import { resolve } from "node:path";
import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

// Real incremental HTTP/SSE transport with synthetic executor events. No model,
// live Goal, or user conversation is involved in this presentation acceptance.
export const conversationActivityScenario = {
  id: "conversation-activity",
  async run({ browser, collectCoverage, url }) {
    const streams = new Map();
    const observations = [];
    const event = (sequence, kind, payload) => `id: ${sequence}\nevent: ${kind}\ndata: ${JSON.stringify({ event_id: String(sequence), sequence, kind, payload })}\n\n`;
    const server = createServer((request, response) => {
      observations.push(request.url);
      response.writeHead(200, { "Content-Type": "text/event-stream", "Access-Control-Allow-Origin": "*", "Cache-Control": "no-cache" });
      response.write(event(0, "turn.started", {}));
      response.write(event(1, "agent.phase", { label: "Agent 正在执行命令" }));
      response.write(event(2, "agent.phase", { label: "Agent 正在检索" }));
      response.write(event(3, "agent.phase", { label: "Agent 正在执行命令" }));
      response.write(event(4, "answer.delta", { text: "已找到两个待处理项，正在核对。" }));
      streams.set(request.url, response);
      response.on("close", () => { if (streams.get(request.url) === response) streams.delete(request.url); });
    });
    await new Promise(resolveListen => server.listen(0, "127.0.0.1", resolveListen));
    const context = await openWorkspacePage(browser, url, { collectCoverage });
    const { page, api } = context;
    try {
      for (const pattern of ["**/events/**", "**/api/chat/sessions/*/turns/*/events"]) {
        await page.route(pattern, route => {
          const path = new URL(route.request().url()).pathname;
          const restored = path.match(/^\/api\/chat\/sessions\/([^/]+)\/turns\/([^/]+)\/events$/);
          const streamPath = restored ? `/events/${restored[1]}/${restored[2]}` : path;
          return route.continue({ url: `http://127.0.0.1:${server.address().port}${streamPath}` });
        });
      }
      await page.getByRole("navigation", { name: "管家视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
      const send = async message => {
        await page.getByLabel("向 LoopX 发送消息").fill(message);
        await page.getByRole("button", { name: "发送", exact: true }).click();
        await page.locator(".personal-message-work-current").getByText("Agent 正在执行命令", { exact: true }).waitFor();
        return api.turnRequests.at(-1);
      };
      assert.equal(await page.getByRole("button", { name: "恢复未确认的追加指令", exact: true }).count(), 0, "A new conversation has no recovery control");
      const turn = await send("请检查当前任务状态，并说明下一步。");
      await page.getByRole("status", {name: "正在回复", exact: true}).waitFor();
      const pending = page.locator(".personal-message").filter({ has: page.getByRole("button", { name: "中断本轮", exact: true }) });
      // Pin Date to a fixed instant first, then advance timers without a
      // running wall clock, and pause. Any finite future pause target leaves a
      // scheduling race — the clock keeps running between commands, so a
      // delayed pause can again aim into the past. With the Date pinned the
      // comparisons use one deterministic instant regardless of command
      // latency; the system time is restored before the scenario continues.
      const clockStart = Date.UTC(2030, 0, 1);
      await page.clock.install({time: clockStart});
      await page.clock.setFixedTime(clockStart);
      await page.clock.fastForward(25000);
      await page.clock.pauseAt(clockStart);
      await pending.locator(".personal-message-quiet").waitFor();
      const elapsedBefore = await pending.locator(".personal-message-elapsed").textContent();
      await page.getByRole("navigation", { name: "管家视图" }).getByRole("button", { name: "总览", exact: true }).click();
      const tray = page.locator(".personal-manager-conversation-tray");
      await page.screenshot({ path: resolve(outputDir, "conversation-waiting-overview.png"), animations: "disabled" });
      assert.equal(await tray.count(), 0, "Background updates never cover the overview with a conversation");
      assert.equal(await page.getByRole("status", {name: "正在回复", exact: true}).count(), 1, "Activity stays on the Chat entry");
      await page.getByRole("navigation", { name: "管家视图" }).getByRole("button", { name: "对话", exact: true }).click();
      assert.equal(await pending.locator(".personal-message-elapsed").textContent(), elapsedBefore);
      await page.clock.setSystemTime(Date.now());
      await page.clock.resume();
      const adjustments = [];
      let legacyMigrationAttempts = 0;
      let rejectHistoryRead = false;
      let rejectedHistoryReads = 0;
      await page.route("**/api/chat/sessions?*", route => {
        const target = new URL(route.request().url());
        if (rejectHistoryRead && target.searchParams.get("channel_id") === "manager") {
          rejectHistoryRead = false;
          rejectedHistoryReads += 1;
          return route.fulfill({ status: 503, json: { error: "History temporarily unavailable" } });
        }
        return route.fallback();
      });
      await page.route("**/steer", async route => {
        const body = route.request().postDataJSON();
        const [sessionId, turnId] = new URL(route.request().url()).pathname.match(/sessions\/([^/]+)\/turns\/([^/]+)\/steer/).slice(1);
        adjustments.push(body);
        if (body.message === "先检查最新证据，再继续。" && legacyMigrationAttempts++ === 0)
          return route.fulfill({ status: 409, json: { ok: false, error: "原回合指令未确认" } });
        if (adjustments.length === 1) return route.fulfill({ status: 409, json: { ok: false, error: "执行器暂时未确认接收，草稿已保留。" } });
        if (adjustments.length === 3) return route.fulfill({ status: 409, json: { ok: false, error: "执行器暂时不可用。", error_code: "live_steering_session_not_attached", delivery_state: "not_delivered" } });
        if (adjustments.length !== 2) {
          const rows = page.__loopxRuntime.messages.get(sessionId);
          const messageId = `instruction:${body.client_ingress_id}`;
          if (!rows.some(row => row.message_id === messageId)) rows.push({
            message_id: messageId, turn_id: turnId, role: "user", text: body.message,
            created_at: await page.evaluate(() => new Date().toISOString()),
          });
          if (adjustments.length === 4) rejectHistoryRead = true;
        }
        return route.fulfill({ json: { ok: true, session_id: sessionId, turn_id: adjustments.length === 2 ? "wrong-turn" : turnId, client_ingress_id: body.client_ingress_id, status: "delivered", created: true } });
      });
      const composer = page.getByLabel("向 LoopX 发送消息");
      const submit = page.getByRole("button", { name: "发送", exact: true });
      const feedback = page.locator(".personal-action-feedback");
      await pending.getByRole("button", { name: "调整本轮", exact: true }).click();
      assert.equal(await composer.evaluate(node => node === document.activeElement), true, "Adjust focuses the shared composer");
      assert.equal(await page.locator("textarea:visible").count(), 1, "Only one editable conversation input is visible");
      assert.equal(await page.locator(".personal-message-steer").count(), 0, "No inline adjustment form remains");
      assert.equal(await composer.getAttribute("placeholder"), "输入消息…");
      await composer.fill("先核对依赖，再继续当前任务。");
      await page.screenshot({ path: resolve(outputDir, "conversation-composer-unified-desktop.png"), animations: "disabled" });
      await composer.press("Meta+Enter");
      await feedback.filter({ hasText: "暂时未确认" }).waitFor();
      assert.equal(await composer.inputValue(), "先核对依赖，再继续当前任务。");
      await page.getByRole("navigation", { name: "管家视图" }).getByRole("button", { name: "总览", exact: true }).click();
      assert.equal(await composer.inputValue(), "先核对依赖，再继续当前任务。", "The same draft survives switching to the overview");
      assert.equal(await tray.count(), 0, "An uncertain steering receipt cannot recreate a floating conversation");
      await page.getByRole("navigation", { name: "管家视图" }).getByRole("button", { name: "对话", exact: true }).click();
      await pending.getByRole("button", { name: "调整本轮", exact: true }).click();
      assert.equal(await composer.evaluate(node => node === document.activeElement), true, "Chat activity focuses the same input");
      await submit.click();
      await feedback.filter({ hasText: "回执不匹配" }).waitFor();
      await page.reload({ waitUntil: "domcontentloaded" });
      await page.getByRole("navigation", { name: "管家视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
      await pending.getByRole("button", { name: "调整本轮", exact: true }).waitFor();
      assert.equal(await composer.inputValue(), "先核对依赖，再继续当前任务。", "Reload retains the uncertain adjustment in the one composer");
      assert.equal(adjustments.length, 2, "Restoration never resends an adjustment");
      assert.equal(api.turnRequests.length, 1, "Restoration never starts work");
      await page.screenshot({ path: resolve(outputDir, "conversation-steering-draft.png"), animations: "disabled" });
      await page.setViewportSize({ width: 390, height: 844 });
      await composer.scrollIntoViewIfNeeded();
      assert.ok(await page.locator(".personal-composer-wrap").evaluate(node => node.scrollWidth <= node.clientWidth + 1), "Shared composer fits mobile width");
      await page.screenshot({ path: resolve(outputDir, "conversation-steering-mobile.png"), animations: "disabled" });
      await page.setViewportSize({ width: 1512, height: 982 });
      await submit.click();
      await feedback.filter({ hasText: "执行器暂时不可用" }).waitFor();
      assert.equal(await composer.inputValue(), "先核对依赖，再继续当前任务。");
      await composer.press("Meta+Enter");
      await feedback.filter({ hasText: "执行器已接收本轮追加指令" }).waitFor();
      assert.equal(await composer.inputValue(), "", "Only the accepted draft clears");
      await page.getByRole("button", { name: "重试读取", exact: true }).waitFor({ timeout: 5000 });
      assert.equal(rejectedHistoryReads, 1, "Confirmed delivery can outlive a failed history read");
      await page.getByRole("button", { name: "重试读取", exact: true }).click();
      await page.locator(".personal-message.is-user").filter({ hasText: "先核对依赖" }).waitFor();
      assert.equal(adjustments.length, 4, "History recovery reads the stored instruction without sending it again");
      assert.equal(new Set(adjustments.slice(0, 3).map(row => row.client_ingress_id)).size, 1, "Uncertain retries retain ingress identity");
      assert.notEqual(adjustments[3].client_ingress_id, adjustments[0].client_ingress_id, "Confirmed rejection starts a safe new ingress");
      assert.equal(api.turnRequests.length, 1, "steering never starts another turn");
      assert.equal(streams.size, 1, "steering keeps the original output stream");
      assert.equal(await page.locator(".personal-message.is-user").filter({ hasText: "先核对依赖" }).count(), 1);
      const conversation = await page.locator(".personal-channel-timeline > .personal-message").evaluateAll(rows => rows.map(row => ({
        user: row.classList.contains("is-user"), text: row.textContent,
      })));
      const originalIndex = conversation.findIndex(row => row.user && row.text.includes(turn.message));
      const workIndex = conversation.findIndex(row => !row.user && row.text.includes("中断本轮"));
      const correctionIndex = conversation.findIndex(row => row.user && row.text.includes("先核对依赖"));
      assert.ok(originalIndex >= 0 && originalIndex < workIndex && workIndex < correctionIndex,
        "The request, ongoing work and accepted correction remain together in conversation order");
      await pending.locator("summary").filter({ hasText: "最近活动" }).click();
      assert.deepEqual((await pending.locator(".personal-message-activity li").allTextContents()).slice(-3), ["Agent 正在执行命令", "Agent 正在检索", "Agent 正在执行命令"]);
      await page.screenshot({ path: resolve(outputDir, "conversation-activity-desktop.png"), animations: "disabled" });
      await page.setViewportSize({ width: 390, height: 844 });
      await pending.scrollIntoViewIfNeeded();
      assert.ok(await pending.evaluate(node => node.scrollWidth <= node.clientWidth + 1), "activity must fit mobile width");
      await page.screenshot({ path: resolve(outputDir, "conversation-activity-mobile.png"), animations: "disabled" });
      await page.setViewportSize({ width: 1512, height: 982 });

      // Streaming must not take the reading position away from the user.
      const live = streams.get(`/events/${turn.sessionId}/${turn.turnId}`);
      live.write(event(5, "answer.delta", { text: "\n\n" + "公开资料与待核实事项。\n\n".repeat(80) }));
      await page.getByText("公开资料与待核实事项。", { exact: true }).first().waitFor();
      const scroller = page.locator(".personal-channel-scroll");
      // A responsive resize can leave scrollTop at zero before the follow
      // frame runs. Start at a real end position so the next step moves up.
      const startingPosition = await scroller.evaluate(node => {
        node.scrollTop = node.scrollHeight;
        node.dispatchEvent(new Event("scroll"));
        return { top: node.scrollTop, remaining: node.scrollHeight - node.scrollTop - node.clientHeight };
      });
      assert.ok(startingPosition.top > 0 && startingPosition.remaining < 64,
        "Exercise upward reading from the scrollable end, not an already-zero position");
      await scroller.evaluate(node => { node.scrollTop = 0; node.dispatchEvent(new Event("scroll")); });
      await page.getByRole("button", { name: "回到最新消息 ↓", exact: true }).waitFor();
      live.write(event(6, "answer.delta", { text: "新增证据已经到达。" }));
      await page.getByText("新增证据已经到达。", { exact: true }).waitFor({ state: "attached" });
      assert.ok(await scroller.evaluate(node => node.scrollTop < 10), "new output preserves upward reading position");
      await page.getByRole("button", { name: "回到最新消息 ↓", exact: true }).click();
      assert.ok(await scroller.evaluate(node => node.scrollHeight - node.scrollTop - node.clientHeight < 64));
      assert.equal(await page.locator(".personal-composer-tools-panel").count(), 0, "conversation removes redundant suggestion strip");

      let interrupts = 0;
      const rejectThenMismatch = async route => {
        interrupts += 1;
        if (interrupts === 1) return route.fulfill({ status: 424, json: { ok: false, error: "执行器暂时无法中断，请重试。" } });
        if (interrupts === 2) return route.fulfill({ json: { ok: true, session_id: turn.sessionId, turn_id: "another-turn", status: "interrupted" } });
        return route.fallback();
      };
      await page.route("**/interrupt", rejectThenMismatch);
      await pending.getByRole("button", { name: "中断本轮", exact: true }).focus();
      await page.keyboard.press("Enter");
      await pending.getByRole("alert").filter({ hasText: "暂时无法中断" }).waitFor();
      await page.screenshot({ path: resolve(outputDir, "conversation-activity-error.png"), animations: "disabled" });
      assert.equal(await page.getByRole("button", { name: "发送", exact: true }).isDisabled(), true);
      await pending.getByRole("button", { name: "中断本轮", exact: true }).click();
      await pending.getByRole("alert").filter({ hasText: "回执与本次请求不一致" }).waitFor();
      assert.equal(streams.size, 1, "rejected or mismatched interruption must not abort the live stream");
      await pending.getByRole("button", { name: "中断本轮", exact: true }).click();
      await page.getByText("已中断。你可以在当前会话继续发送消息。", { exact: true }).waitFor();
      assert.equal(await page.locator(".personal-md").filter({ hasText: "已找到两个待处理项" }).count(), 1, "interruption preserves partial output");
      await page.locator(".personal-chat-activity").waitFor({state: "hidden"});
      assert.deepEqual(api.interrupts.at(-1), { sessionId: turn.sessionId, turnId: turn.turnId });
      await page.screenshot({ path: resolve(outputDir, "conversation-activity-interrupted.png"), animations: "disabled" });
      await page.unroute("**/interrupt", rejectThenMismatch);

      // Completion wins an interruption race; do not overwrite the result.
      const next = await send("继续完成这项检查。");
      assert.equal(next.sessionId, turn.sessionId, "continue uses the same conversation");
      await page.getByRole("button", { name: "调整本轮", exact: true }).click();
      await composer.fill("保留我的未发送草稿。");
      await page.route("**/interrupt", async route => {
        const response = streams.get(`/events/${next.sessionId}/${next.turnId}`);
        const answer = "检查完成，下一步已列出。";
        const runtime = page.__loopxRuntime;
        runtime.sessions.set(next.sessionId, { ...runtime.sessions.get(next.sessionId), active_turn_id: null, status: "ready" });
        runtime.messages.get(next.sessionId).push({
          message_id: `${next.turnId}-assistant`, turn_id: next.turnId, role: "assistant", text: answer,
          created_at: await page.evaluate(() => new Date().toISOString()),
        });
        response.end(event(5, "turn.completed", { response: { schema_version: "loopx_chat_agent_response_v0", message: answer, proposals: [], gate: null } }));
        await route.fulfill({ json: { ok: true, session_id: next.sessionId, turn_id: next.turnId, status: "completed" } });
      });
      await page.getByRole("button", { name: "中断本轮", exact: true }).click();
      await page.getByText("检查完成，下一步已列出。", { exact: true }).waitFor();
      await page.locator(".personal-chat-activity").waitFor({state: "hidden"});
      assert.equal(await page.getByRole("button", { name: "中断本轮", exact: true }).count(), 0);
      assert.equal(await composer.inputValue(), "保留我的未发送草稿。");
      assert.equal(await submit.isEnabled(), true, "An unsent draft becomes an explicit follow-up after completion");
      await page.getByRole("navigation", { name: "管家视图" }).getByRole("button", { name: "总览", exact: true }).click();
      await page.getByRole("navigation", { name: "管家视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
      assert.equal(await composer.inputValue(), "保留我的未发送草稿。", "finished turn retains the unsent draft across unmount");
      assert.equal(adjustments.length, 4, "Completion never dispatches the retained draft");
      await page.unroute("**/interrupt");

      // The same interaction is present in Goal Chat, through the same timeline.
      await page.locator(".personal-goal-link").first().click();
      await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
      // Native ordinary sends can be acknowledged as queued before starting.
      // Their original observer must survive the asynchronous current-Turn read.
      let pendingActiveRead;
      await page.route("**/api/chat/sessions/*/turns", async route => {
        const body = route.request().postDataJSON();
        if (body.message !== "请检查这个 Goal 的当前状态。") return route.fallback();
        const sessionId = new URL(route.request().url()).pathname.split("/").at(-2);
        const turnId = `queued-start-${Date.now()}`;
        const runtime = page.__loopxRuntime;
        runtime.turnMessages.set(turnId, body.message);
        runtime.sessions.set(sessionId, {...runtime.sessions.get(sessionId), active_turn_id: turnId, status: "busy"});
        runtime.messages.get(sessionId).push({
          message_id: `${turnId}-user`, turn_id: turnId, role: "user", text: body.message,
          created_at: await page.evaluate(() => new Date().toISOString()),
        });
        api.turnRequests.push({message: body.message, sessionId, turnId});
        let holdFirstRead = true;
        await page.route(`**/api/chat/sessions/${sessionId}`, snapshot => {
          if (holdFirstRead) { holdFirstRead = false; pendingActiveRead = snapshot; return; }
          return snapshot.fallback();
        });
        await route.fulfill({status: 202, json: {ok: true, session_id: sessionId, turn_id: turnId,
          created: true, status: "queued", events_url: `/events/${sessionId}/${turnId}`}});
      });
      const goalTurn = await send("请检查这个 Goal 的当前状态。");
      const goalTabs = page.getByRole("navigation", {name: "Goal 视图"});
      for (const width of [1512, 390]) {
        await page.setViewportSize({width, height: width === 390 ? 844 : 982});
        for (const tab of ["概览", "任务", "研究", "成果"]) {
          await goalTabs.getByRole("button", {name: tab, exact: true}).click();
          assert.equal(await tray.count(), 0, `${tab} stays clear of background conversations`);
          assert.equal(await page.getByRole("status", {name: "正在回复", exact: true}).count(), 1);
          assert.equal(api.turnRequests.length, 3, "Navigation neither restarts nor cancels work");
          await page.screenshot({path: resolve(outputDir, `conversation-background-${tab}-${width}.png`), animations: "disabled"});
        }
      }
      await page.setViewportSize({width: 1512, height: 982});
      await page.locator(".personal-goal-link").nth(1).click();
      await page.locator(".personal-chat-activity").waitFor({state: "hidden"});
      assert.equal(api.interrupts.length, 1, "Changing Goals does not interrupt the active conversation");
      await page.locator(".personal-goal-link").first().click();
      await page.getByRole("status", {name: "正在回复", exact: true}).waitFor();
      await page.emulateMedia({reducedMotion: "reduce"});
      assert.equal(await page.locator(".personal-chat-activity svg").evaluate(node => getComputedStyle(node).animationName), "none");
      await page.emulateMedia({reducedMotion: "no-preference"});
      await page.setViewportSize({width: 1512, height: 982});
      await goalTabs.getByRole("button", {name: "对话", exact: true}).focus();
      const resumedSession = page.waitForResponse(response => new URL(response.url()).pathname === "/api/chat/sessions"
        && response.request().method() === "POST");
      await page.keyboard.press("Enter");
      await resumedSession;
      await page.evaluate(() => new Promise(resolveFrame => requestAnimationFrame(() => requestAnimationFrame(resolveFrame))));
      assert.equal(await goalTabs.getByRole("button", {name: "对话", exact: true}).getAttribute("aria-current"), "page");
      await page.screenshot({path: resolve(outputDir, "conversation-observer-desktop.png"), animations: "disabled"});
      await page.setViewportSize({width: 390, height: 844});
      await page.screenshot({path: resolve(outputDir, "conversation-observer-narrow.png"), animations: "disabled"});
      await page.setViewportSize({width: 1512, height: 982});
      assert.equal(observations.filter(path => path === `/events/${goalTurn.sessionId}/${goalTurn.turnId}`).length, 1,
        "Returning to Goal Chat preserves the original live observer instead of attaching a recovery stream");
      assert.equal(await page.getByRole("button", {name: "调整本轮", exact: true}).count(), 1,
        "The same running Session/Turn has one set of controls after navigation");
      assert.ok(pendingActiveRead, "The queued-start confirmation remains outstanding during navigation");
      await pendingActiveRead.fallback();
      await page.getByRole("button", { name: "调整本轮", exact: true }).click();
      assert.equal(await composer.inputValue(), "", "another conversation never inherits the old draft");
      await page.evaluate(({sessionId, turnId}) => {
        const legacyKey = JSON.stringify(["inline", sessionId, turnId]);
        const rows = JSON.parse(sessionStorage.getItem("loopx-pw-composer-steering") ?? "[]");
        rows.push([legacyKey, {sessionId, turnId, text: "旧版保留的原回合草稿。", id: "legacy-exact-ingress"}]);
        sessionStorage.setItem("loopx-pw-composer-steering", JSON.stringify(rows));
      }, goalTurn);
      await page.reload({ waitUntil: "domcontentloaded" });
      await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
      await page.waitForFunction(() => document.querySelector("#personal-conversation-composer")?.value === "旧版保留的原回合草稿。");
      assert.equal(adjustments.length, 4, "Migrating an old draft never dispatches it");
      const migrated = await page.evaluate(() => JSON.parse(sessionStorage.getItem("loopx-pw-composer-steering"))
        .filter(([, request]) => request.id === "legacy-exact-ingress"));
      assert.equal(migrated.length, 1, "One retry owner remains after durable migration");
      assert.equal(migrated[0][1].turnId, goalTurn.turnId);
      assert.ok(!migrated[0][0].startsWith("["), "The request moved into the conversation composer key");
      await submit.click();
      await feedback.filter({ hasText: "执行器已接收本轮追加指令" }).waitFor();
      assert.equal(adjustments.at(-1).client_ingress_id, "legacy-exact-ingress");
      await composer.fill("");
      await page.getByRole("button", { name: "中断本轮", exact: true }).click();
      await page.getByText("已中断。你可以在当前会话继续发送消息。", { exact: true }).waitFor();
      await page.locator(".personal-chat-activity").waitFor({state: "hidden"});
      assert.deepEqual(api.interrupts.at(-1), { sessionId: goalTurn.sessionId, turnId: goalTurn.turnId });
      page.__loopxRuntime.queuedTurns.set(goalTurn.sessionId, [{
        turn_id: "waiting-for-host", created_at: "2026-08-13T01:00:01Z", origin: "web",
        preview: "等待执行器接收的后续消息", character_count: 13, attachment_count: 0, steering: null,
      }]);
      await page.reload({waitUntil: "domcontentloaded"});
      await page.getByRole("button", {name: "对话", exact: true}).click();
      await page.getByRole("region", {name: "待处理消息", exact: true}).waitFor();
      assert.equal(await page.locator(".personal-chat-activity").count(), 0, "A queued message alone is not active execution");
      assert.equal(api.turnRequests.length, 3, "Queue readback does not start work");
      return { coverageEntries: context.coverageEntries, note: "Shared steward/Goal activity and steering: view/reload recovery without automatic dispatch, one composer, terminal draft isolation, interruption and completion races." };
    } finally {
      for (const response of streams.values()) response.destroy();
      server.closeAllConnections();
      await new Promise(resolveClose => server.close(resolveClose));
      await context.close();
    }
  },
};
