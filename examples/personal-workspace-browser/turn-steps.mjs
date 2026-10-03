import assert from "node:assert/strict";
import { createServer } from "node:http";
import { resolve } from "node:path";
import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

// Real incremental HTTP/SSE transport with synthetic executor steps. No model,
// live Goal, or user conversation is involved.
export const turnStepsScenario = {
  id: "turn-steps",
  async run({ browser, collectCoverage, url }) {
    let sequence = 0;
    const event = (kind, payload) => {
      sequence += 1;
      return `id: ${sequence}\nevent: ${kind}\ndata: ${JSON.stringify({ event_id: String(sequence), sequence, kind, payload })}\n\n`;
    };
    const phase = (label, step) => event("agent.phase", { label, method: "item/started", ...(step ? { step } : {}) });
    let live;
    const server = createServer((request, response) => {
      response.writeHead(200, { "Content-Type": "text/event-stream", "Access-Control-Allow-Origin": "*", "Cache-Control": "no-cache" });
      response.write(phase("Agent 正在思考", { id: "rs_1", kind: "reasoning", state: "running", title: "Compare the release owners",
        detail: "Compare the release owners\n\nThe English and Chinese notes publish on separate schedules, so keep both owners." }));
      response.write(phase("Agent 正在执行命令", { id: "exec_1", kind: "command", state: "running", verb: "search", title: "release owner",
        detail: "rg -n \"release owner\" docs/releases" }));
      live = response;
    });
    await new Promise(resolveListen => server.listen(0, "127.0.0.1", resolveListen));
    const context = await openWorkspacePage(browser, url, { collectCoverage });
    const { page, api } = context;
    try {
      await page.route("**/events/**", route => {
        const path = new URL(route.request().url()).pathname;
        return route.continue({ url: `http://127.0.0.1:${server.address().port}${path}` });
      });
      await page.getByRole("navigation", { name: "管家视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
      await page.getByLabel("向 LoopX 发送消息").fill("核对发布负责人。");
      await page.getByRole("button", { name: "发送", exact: true }).click();
      const current = page.locator(".personal-message-work-current .personal-message-pending");
      await current.getByText("正在搜索 release owner", { exact: true }).waitFor();

      live.write(phase("Agent 返回了处理状态", { id: "rs_1", kind: "reasoning", state: "completed", title: "Compare the release owners", duration_ms: 4200 }));
      live.write(phase("Agent 返回了处理状态", { id: "exec_1", kind: "command", state: "completed", verb: "search", title: "release owner", exit_code: 0, duration_ms: 120 }));
      live.write(phase("Agent 正在调用工具", { id: "tool_1", kind: "tool", state: "running", title: "loopx · todo_list" }));
      live.write(phase("Agent 返回了处理状态", { id: "tool_1", kind: "tool", state: "completed", title: "loopx · todo_list", duration_ms: 1800 }));
      live.write(phase("Agent 返回了处理状态", { id: "exec_2", kind: "command", state: "failed", verb: "run", title: "npm run check:release", exit_code: 2, duration_ms: 3100 }));
      live.write(phase("Agent 返回了处理状态", { id: "edit_1", kind: "file_change", state: "completed", title: "docs/releases/owners.md", count: 2,
        detail: "docs/releases/owners.md\ndocs/releases/owners.zh-CN.md" }));
      live.write(phase("Agent 正在思考", { id: "rs_2", kind: "reasoning", state: "running", title: "" }));
      live.write(phase("Agent 正在思考", { id: "bad", kind: "telepathy", state: "running", title: "ignored" }));
      live.write(event("answer.delta", { text: "两位负责人都保留，中英文分别择时发布。" }));
      await current.getByText("正在思考", { exact: true }).waitFor();

      const pending = page.locator(".personal-message").filter({ has: page.getByRole("button", { name: "中断本轮", exact: true }) });
      const steps = pending.locator(".personal-turn-steps");
      await steps.locator("summary").first().click();
      const rows = steps.locator("li.personal-turn-step");
      assert.equal(await rows.count(), 6, "started and completed events share one row; malformed steps are dropped");
      assert.match(await steps.locator(":scope > summary").textContent(), /执行过程\s*6\s*1 步失败/);
      const texts = (await rows.locator("summary, .personal-turn-step-head").allTextContents()).map(text => text.replace(/\s+/g, " ").trim());
      assert.deepEqual(texts, [
        "思考Compare the release owners4.2 秒",
        "搜索release owner",
        "调用loopx · todo_list1.8 秒",
        "运行npm run check:release失败 · 退出码 2 · 3.1 秒",
        "修改docs/releases/owners.md2 个文件",
        "思考进行中",
      ]);
      const search = rows.nth(1);
      assert.equal(await search.locator("pre").isVisible(), false, "details start collapsed");
      await search.locator("summary").focus();
      await page.keyboard.press("Enter");
      assert.equal(await search.locator("pre").textContent(), "rg -n \"release owner\" docs/releases");
      await rows.nth(0).locator("summary").click();
      assert.equal(await rows.nth(0).locator("pre").textContent(), "The English and Chinese notes publish on separate schedules, so keep both owners.",
        "the expanded thought does not repeat its heading");
      assert.equal(await rows.nth(5).locator("details").count(), 0, "a step without exposed text does not pretend to expand");
      await pending.scrollIntoViewIfNeeded();
      await page.screenshot({ path: resolve(outputDir, "turn-steps-desktop.png"), animations: "disabled" });
      await page.setViewportSize({ width: 390, height: 844 });
      await pending.scrollIntoViewIfNeeded();
      assert.ok(await pending.evaluate(node => node.scrollWidth <= node.clientWidth + 1), "steps fit mobile width");
      await page.screenshot({ path: resolve(outputDir, "turn-steps-mobile.png"), animations: "disabled" });
      await page.setViewportSize({ width: 1512, height: 982 });

      const turn = api.turnRequests.at(-1);
      const answer = "两位负责人都保留，中英文分别择时发布。";
      const runtime = page.__loopxRuntime;
      runtime.sessions.set(turn.sessionId, { ...runtime.sessions.get(turn.sessionId), active_turn_id: null, status: "ready" });
      runtime.messages.get(turn.sessionId).push({ message_id: `${turn.turnId}-assistant`, turn_id: turn.turnId, role: "assistant", text: answer });
      live.end(event("turn.completed", { response: { schema_version: "loopx_chat_agent_response_v0", message: answer, proposals: [], gate: null } }));
      await page.getByRole("button", { name: "中断本轮", exact: true }).waitFor({ state: "detached" });
      const done = page.locator(".personal-message .personal-turn-steps").last();
      assert.equal(await done.locator("li.personal-turn-step").count(), 6, "steps stay readable after the turn ends");
      assert.equal(await done.getByText("未结束", { exact: true }).count(), 1, "an unfinished step is not shown as live after the turn ends");
      return { coverageEntries: context.coverageEntries, note: "Steps merge per host item, expand on demand, mark failures, drop malformed payloads and stop looking live after the Turn ends." };
    } finally {
      live?.destroy();
      server.closeAllConnections();
      await new Promise(resolveClose => server.close(resolveClose));
      await context.close();
    }
  },
};
