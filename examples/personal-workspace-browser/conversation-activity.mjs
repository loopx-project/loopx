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
    const event = (sequence, kind, payload) => `id: ${sequence}\nevent: ${kind}\ndata: ${JSON.stringify({ event_id: String(sequence), sequence, kind, payload })}\n\n`;
    const server = createServer((request, response) => {
      response.writeHead(200, { "Content-Type": "text/event-stream", "Access-Control-Allow-Origin": "*", "Cache-Control": "no-cache" });
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
      await page.route("**/events/**", route => route.continue({ url: `http://127.0.0.1:${server.address().port}${new URL(route.request().url()).pathname}` }));
      await page.getByRole("navigation", { name: "管家视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
      const send = async message => {
        await page.getByLabel("向 LoopX 发送消息").fill(message);
        await page.getByRole("button", { name: "发送", exact: true }).click();
        await page.locator(".personal-message-work-current").getByText("Agent 正在执行命令", { exact: true }).waitFor();
        return api.turnRequests.at(-1);
      };
      const turn = await send("请检查当前任务状态，并说明下一步。");
      const pending = page.locator(".personal-message").filter({ has: page.getByRole("button", { name: "中断本轮", exact: true }) });
      await page.clock.install();
      await page.clock.fastForward(25000);
      await pending.locator(".personal-message-quiet").waitFor();
      const elapsedBefore = await pending.locator(".personal-message-elapsed").textContent();
      await page.getByRole("navigation", { name: "管家视图" }).getByRole("button", { name: "总览", exact: true }).click();
      const tray = page.locator(".personal-manager-conversation-tray");
      await tray.getByRole("button", { name: "中断本轮", exact: true }).waitFor();
      assert.equal(await tray.locator(".personal-message-elapsed").textContent(), elapsedBefore, "view switch keeps request elapsed time");
      await tray.locator(".personal-message-quiet").waitFor();
      await page.screenshot({ path: resolve(outputDir, "conversation-waiting-overview.png"), animations: "disabled" });
      await tray.getByRole("button", { name: "查看完整对话", exact: true }).click();
      assert.equal(await pending.locator(".personal-message-elapsed").textContent(), elapsedBefore);
      await page.clock.resume();
      const adjustments = [];
      await page.route("**/steer", async route => {
        const body = route.request().postDataJSON();
        adjustments.push(body);
        const [sessionId, turnId] = new URL(route.request().url()).pathname.match(/sessions\/([^/]+)\/turns\/([^/]+)\/steer/).slice(1);
        if (adjustments.length === 1) return route.fulfill({ status: 409, json: { ok: false, error: "执行器暂时未确认接收，草稿已保留。" } });
        if (adjustments.length === 3) return route.fulfill({ status: 409, json: { ok: false, error: "执行器暂时不可用。", error_code: "live_steering_session_not_attached", delivery_state: "not_delivered" } });
        return route.fulfill({ json: { ok: true, session_id: sessionId, turn_id: adjustments.length === 2 ? "wrong-turn" : turnId, client_ingress_id: body.client_ingress_id, status: "delivered", created: true } });
      });
      await pending.getByRole("button", { name: "调整本轮", exact: true }).click();
      await pending.getByLabel("追加给本轮的指令").fill("先核对依赖，再继续当前任务。");
      await pending.getByRole("button", { name: "发送调整", exact: true }).click();
      await pending.getByRole("alert").filter({ hasText: "暂时未确认" }).waitFor();
      assert.equal(await pending.getByLabel("追加给本轮的指令").inputValue(), "先核对依赖，再继续当前任务。");
      await pending.getByRole("button", { name: "发送调整", exact: true }).click();
      await pending.getByRole("alert").filter({ hasText: "回执不匹配" }).waitFor();
      await page.screenshot({ path: resolve(outputDir, "conversation-steering-draft.png"), animations: "disabled" });
      await page.setViewportSize({ width: 390, height: 844 });
      await pending.getByLabel("追加给本轮的指令").scrollIntoViewIfNeeded();
      assert.ok(await pending.evaluate(node => node.scrollWidth <= node.clientWidth + 1), "steering draft fits mobile width");
      await page.screenshot({ path: resolve(outputDir, "conversation-steering-mobile.png"), animations: "disabled" });
      await page.setViewportSize({ width: 1512, height: 982 });
      await pending.getByRole("button", { name: "发送调整", exact: true }).click();
      await pending.getByRole("alert").filter({ hasText: "本次未送达" }).waitFor();
      assert.equal(await pending.getByLabel("追加给本轮的指令").inputValue(), "先核对依赖，再继续当前任务。");
      await pending.getByRole("button", { name: "发送调整", exact: true }).focus();
      await page.keyboard.press("Enter");
      await pending.getByText("执行器已接收本轮追加指令。", { exact: true }).waitFor();
      assert.equal(new Set(adjustments.slice(0, 3).map(row => row.client_ingress_id)).size, 1, "uncertain retries retain ingress identity");
      assert.notEqual(adjustments[3].client_ingress_id, adjustments[0].client_ingress_id, "confirmed rejection starts a safe new ingress");
      assert.equal(api.turnRequests.length, 1, "steering never starts another turn");
      assert.equal(streams.size, 1, "steering keeps the original output stream");
      assert.equal(await page.locator(".personal-message.is-user").filter({ hasText: "先核对依赖" }).count(), 1);
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
      await scroller.evaluate(node => { node.scrollTop = 0; node.dispatchEvent(new Event("scroll")); });
      await page.getByRole("button", { name: "回到最新消息 ↓", exact: true }).waitFor();
      live.write(event(6, "answer.delta", { text: "新增证据已经到达。" }));
      await page.getByText("新增证据已经到达。", { exact: true }).waitFor({ state: "attached" });
      assert.ok(await scroller.evaluate(node => node.scrollTop < 10), "new output preserves upward reading position");
      await page.getByRole("button", { name: "回到最新消息 ↓", exact: true }).click();
      assert.ok(await scroller.evaluate(node => node.scrollHeight - node.scrollTop - node.clientHeight < 64));
      assert.equal(await page.locator(".personal-composer-tools").count(), 0, "conversation removes redundant suggestion strip");

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
      assert.deepEqual(api.interrupts.at(-1), { sessionId: turn.sessionId, turnId: turn.turnId });
      await page.screenshot({ path: resolve(outputDir, "conversation-activity-interrupted.png"), animations: "disabled" });
      await page.unroute("**/interrupt", rejectThenMismatch);

      // Completion wins an interruption race; do not overwrite the result.
      const next = await send("继续完成这项检查。");
      assert.equal(next.sessionId, turn.sessionId, "continue uses the same conversation");
      await page.getByRole("button", { name: "调整本轮", exact: true }).click();
      await page.getByLabel("追加给本轮的指令").fill("保留我的未发送草稿。");
      await page.route("**/interrupt", async route => {
        const response = streams.get(`/events/${next.sessionId}/${next.turnId}`);
        const answer = "检查完成，下一步已列出。";
        const runtime = page.__loopxRuntime;
        runtime.sessions.set(next.sessionId, { ...runtime.sessions.get(next.sessionId), active_turn_id: null, status: "ready" });
        runtime.messages.get(next.sessionId).push({ message_id: `${next.turnId}-assistant`, turn_id: next.turnId, role: "assistant", text: answer });
        response.end(event(5, "turn.completed", { response: { schema_version: "loopx_chat_agent_response_v0", message: answer, proposals: [], gate: null } }));
        await route.fulfill({ json: { ok: true, session_id: next.sessionId, turn_id: next.turnId, status: "completed" } });
      });
      await page.getByRole("button", { name: "中断本轮", exact: true }).click();
      await page.getByText("检查完成，下一步已列出。", { exact: true }).waitFor();
      assert.equal(await page.getByRole("button", { name: "中断本轮", exact: true }).count(), 0);
      assert.equal(await page.getByLabel("追加给本轮的指令").inputValue(), "保留我的未发送草稿。");
      assert.equal(await page.getByRole("button", { name: "发送调整", exact: true }).isDisabled(), true);
      await page.unroute("**/interrupt");

      // The same interaction is present in Goal Chat, through the same timeline.
      await page.locator(".personal-goal-link").first().click();
      await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
      const goalTurn = await send("请检查这个 Goal 的当前状态。");
      await page.getByRole("button", { name: "调整本轮", exact: true }).click();
      await page.getByLabel("追加给本轮的指令").fill("先检查最新证据。");
      await page.getByRole("button", { name: "发送调整", exact: true }).click();
      await page.getByText("执行器已接收本轮追加指令。", { exact: true }).waitFor();
      await page.getByRole("button", { name: "中断本轮", exact: true }).click();
      await page.getByText("已中断。你可以在当前会话继续发送消息。", { exact: true }).waitFor();
      assert.deepEqual(api.interrupts.at(-1), { sessionId: goalTurn.sessionId, turnId: goalTurn.turnId });
      return { coverageEntries: context.coverageEntries, note: "Shared steward/Goal activity, exact-turn steering and interruption, receipt mismatch, idempotent retry, preserved drafts/partial output and completion races." };
    } finally {
      for (const response of streams.values()) response.destroy();
      server.closeAllConnections();
      await new Promise(resolveClose => server.close(resolveClose));
      await context.close();
    }
  },
};
