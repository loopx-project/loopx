// Source frontend with synthetic HTTP reads; no model or active Goal state.
import assert from "node:assert/strict";
import {mkdir} from "node:fs/promises";
import {resolve} from "node:path";
import {cleanupBrowserSmoke, launchBrowser, loadPlaywright, startViteDashboardServer} from "../../../../examples/dashboard-browser-smoke-support.mjs";
import {dashboardDir, repoRoot, installApi} from "../../../../examples/personal-workspace-browser/fixture.mjs";

const port = Number(process.env.LOOPX_GOAL_CREATED_SESSION_PORT ?? "5349");
const origin = `http://127.0.0.1:${port}`;
const evidence = process.env.LOOPX_GOAL_CREATED_SESSION_EVIDENCE ?? resolve(repoRoot, ".local/validation/goal-created-session");
const server = await startViteDashboardServer({dashboardDir, port});
let browser;
try {
  browser = await launchBrowser(loadPlaywright().chromium);
  await mkdir(evidence, {recursive: true});
  for (const scenario of ["managed", "creation", "legacy", "malformed"]) {
    const page = await browser.newPage({viewport: {width: 1440, height: 960}});
    page.setDefaultTimeout(10000);
    await page.emulateMedia({reducedMotion: "reduce"});
    await page.addInitScript(() => localStorage.setItem("loopx-pw-locale", "zh-CN"));
    const proposalId = `create-${scenario}`, goalId = "progress-projection", owner = "codex", sessionId = "created-original-session";
    const proposal = {schema_version: "loopx_chat_action_proposal_v1", proposal_id: proposalId, action_kind: "goal.create",
      summary: "Prepare the original Session journey", normalized_parameters: {goal_id: goalId, title: "Original Session journey / 原会话",
        objective: "Retain the original work conversation", completion_criteria: scenario === "creation" ? "Reopen its original history" : ["Reopen its original history"], execution_boundary: "Synthetic readback only", agent_id: "codex",
        ...(scenario === "creation" ? {start_execution: true, permission: "workspace_write_on_confirmation", initial_todos: ["Reopen its original history"]} : {})},
      context: {kind: "manager"}, expected_state_fingerprint: "original-fingerprint", permission_classification: "durable_write",
      validation_evidence: ["Synthetic proposal is ready for the routing check"], available_transitions: ["apply", "cancel"],
      status: "preview_ready", receipt: null, stale: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z"};
    const api = await installApi(page, {fixturePort: port, initialActionProposals: [proposal]});
    api.registeredAgentsByGoal = {[goalId]: [owner]};
    const runtime = page.__loopxRuntime;
    const session = {session_id: sessionId, goal_id: goalId, goal_instance_id: null, managed_agent_id: owner,
      agent_id: "codex", adapter_kind: "codex_app_server", channel_id: `agent.${owner}`, session_mode: "managed_runtime",
      status: "ready", active_turn_id: null, last_error_code: null, resumable: true,
      created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", last_activity_at: "2026-01-01T00:00:00Z"};
    runtime.sessions.set(sessionId, session);
    const longText = "## Original first-run result / 原首轮结果\n\n" + Array.from({length: 24}, (_, i) =>
      `### Evidence ${i + 1} / 证据 ${i + 1}\n\nOriginal scope retained; independent acceptance remains unverified. 原始范围保留；独立验收尚未确认。`).join("\n\n");
    runtime.messages.set(sessionId, Array.from({length: 19}, (_, i) => ({message_id: `original-message-${i}`, role: i % 3 === 0 ? "user" : "agent",
      turn_id: null, text: i === 18 ? longText : `Original conversation record ${i + 1} / 原会话记录 ${i + 1}：${i % 2 ? "failed check retained" : "unverified work retained"}`,
      created_at: `2026-01-01T00:00:${String(i).padStart(2, "0")}Z`})).map((row, i) => i === 18 ? {...row, role: "agent"} : row));
    const writes = [], discovery = [];
    let mode = scenario === "creation" ? "pending-creation" : "ready";
    const creationRequest = {creation_operation_id: proposalId, operation_id: `goal-create-${proposalId}`, goal_id: goalId,
      goal_instance_id: null, agent_id: owner, session_id: sessionId, todo_id: "original-task", permission: "workspace_write_on_confirmation", write_scope: ["**"]};
    const creationTurn = {session_id: sessionId, turn_id: "original-creation-native-turn", client_turn_id: `managed.${creationRequest.operation_id}`,
      status: "failed", origin: "native_loopx", loopx_execution: true,
      loopx_request: {operation: "native_turn", todo_id: creationRequest.todo_id, actor: {goal_id: goalId, agent_id: owner, session_id: sessionId}}};
    page.on("request", request => {
      if (request.method() !== "GET") writes.push({path: new URL(request.url()).pathname, body: request.postDataJSON()});
    });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.route("**/api/actions/**", async route => {
      const path = new URL(route.request().url()).pathname;
      if (path !== `/api/actions/${proposalId}/apply`) return route.fallback();
      const ids = {goal_id: goalId, goal_instance_id: null, agent_id: "codex", session_id: sessionId,
        ...(scenario === "legacy" ? {} : {managed_agent_id: owner, channel_id: scenario === "malformed" ? `goal.${goalId}` : `agent.${owner}`})};
      const applied = {...proposal, status: "applied", available_transitions: [], receipt: {outcome: "goal_created", projection_verified: true, resource_ids: ids}, updated_at: "2026-01-01T00:00:01Z"};
      runtime.actionProposals.set(proposalId, applied);
      return route.fulfill({json: {ok: true, proposal: applied}});
    });
    await page.route("**/api/chat/sessions**", async route => {
      const request = route.request(), url = new URL(request.url());
      if (scenario === "creation" && url.pathname === `/api/chat/sessions/${sessionId}/native-turn`) {
        assert.equal(request.method(), "GET", "Populated original creation observation never launches");
        return route.fulfill({json: {ok: true, session_id: sessionId, managed_agent_id: owner, registered_agents: [owner], available: true,
          task: {todo_id: creationRequest.todo_id, state: "ready", source: "goal-creation", canonical_done: false,
            observation: {check_count: 6, pinned_file_count: 1, basis_sha256: "original-confirmed-checks"},
            creation_request: creationRequest, creation_turn: mode === "pending-creation" ? null : creationTurn}, turn: null}});
      }
      if (url.pathname === "/api/chat/sessions" && request.method() === "GET" && url.searchParams.has("managed_agent_id")) {
        discovery.push([url.searchParams.get("goal_id"), url.searchParams.get("managed_agent_id")]);
        const actual = runtime.sessions.get(sessionId);
        const current = mode === "identity-failure" ? {...actual, channel_id: `goal.${goalId}`} : actual;
        return route.fulfill({json: {ok: true, schema_version: "loopx_chat_session_list_v1", goal_id: goalId,
          managed_agent_id: owner, goal_instance_id: null, sessions: [current]}});
      }
      return route.fallback();
    });
    try {
      await page.goto(`${origin}/?statusUrl=/status.json&view=conversation`, {waitUntil: "domcontentloaded"});
      const row = page.locator('.personal-proposal-row[data-action-kind="goal.create"]');
      await row.waitFor();
      await row.click();
      const drawer = page.locator('.personal-context-drawer[data-context-kind="proposal"]');
      await drawer.getByRole("button", {name: scenario === "creation" ? "创建 Goal 并开始首轮" : "创建 Goal", exact: true}).click();
      const enter = drawer.getByRole("button", {name: "进入新 Goal", exact: true});
      if (scenario === "malformed") {
        await drawer.getByRole("alert").filter({hasText: "无法验证新 Goal 的原 Session 地址"}).waitFor();
        assert.equal(await enter.count(), 0, "An invalid managed receipt must not expose the legacy Goal fallback");
        await page.screenshot({path: resolve(evidence, "managed-receipt-invalid-desktop.png"), animations: "disabled"});
        await page.reload({waitUntil: "domcontentloaded"});
        await row.waitFor(); await row.click();
        await drawer.getByRole("alert").filter({hasText: "无法验证新 Goal 的原 Session 地址"}).waitFor();
        assert.equal(await enter.count(), 0);
        console.log("goal-created-session browser: PASS malformed receipt and reload");
        continue;
      }
      await enter.waitFor();
      await page.screenshot({path: resolve(evidence, `${scenario}-creation-receipt-desktop.png`), animations: "disabled"});
      await enter.click();
      await page.waitForURL(url => url.searchParams.get("goalId") === goalId);
      const routed = new URL(page.url());
      if (scenario === "legacy") {
        assert.equal(routed.searchParams.get("workAgent"), null);
        assert.equal(routed.searchParams.get("workSession"), null);
        assert.equal(routed.searchParams.get("view"), "overview");
        console.log("goal-created-session browser: PASS legacy navigation parity");
        continue;
      }
      assert.equal(routed.searchParams.get("workAgent"), owner);
      assert.equal(routed.searchParams.get("workSession"), `managed:${sessionId}`);
      assert.equal(routed.searchParams.get("view"), "conversation");
      const original = page.getByRole("heading", {name: "Original first-run result / 原首轮结果", exact: true});
      await original.waitFor();
      if (scenario === "creation") {
        const control = page.getByRole("region", {name: "当前任务", exact: true});
        const pending = control.getByRole("button", {name: "等待", exact: true});
        await pending.waitFor();
        assert.equal(await pending.isDisabled(), true);
        await control.getByRole("status").filter({hasText: "已提交，等待执行"}).waitFor();
        assert.equal(writes.filter(row => row.path.endsWith("/native-turn")).length, 0);
        mode = "ready";
        await page.reload({waitUntil: "domcontentloaded"});
        await control.getByRole("button", {name: "继续当前任务", exact: true}).waitFor();
        for (const [label, viewport] of [["desktop", {width: 1440, height: 960}], ["narrow", {width: 390, height: 844}]]) {
          await page.setViewportSize(viewport);
          await page.screenshot({path: resolve(evidence, `creation-populated-${label}.png`), animations: "disabled"});
          await control.getByRole("button", {name: "当前任务与检查", exact: true}).click();
          await page.getByRole("tooltip").filter({hasText: "当前任务与检查"}).waitFor();
          await page.screenshot({path: resolve(evidence, `creation-populated-help-${label}.png`), animations: "disabled"});
          await page.keyboard.press("Escape");
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
        }
        assert.equal(writes.filter(row => row.path.endsWith("/native-turn")).length, 0);
        await page.setViewportSize({width: 1440, height: 960});
      }
      assert.ok(discovery.some(([goal, agent]) => goal === goalId && agent === owner));
      await page.screenshot({path: resolve(evidence, "managed-original-first-desktop.png"), animations: "disabled"});
      await original.evaluate(element => element.scrollIntoView({block: "start"}));
      await page.screenshot({path: resolve(evidence, "managed-original-long-desktop.png"), animations: "disabled"});
      await page.reload({waitUntil: "domcontentloaded"}); await original.waitFor();
      assert.equal(new URL(page.url()).searchParams.get("workSession"), `managed:${sessionId}`);
      const send = page.getByRole("button", {name: "发送", exact: true}), composer = page.getByLabel("向 LoopX 发送消息");
      await composer.fill("Keep this ordinary follow-up in the original Session.");
      mode = "identity-failure";
      await page.reload({waitUntil: "domcontentloaded"});
      const error = page.getByRole("alert").filter({hasText: "cannot verify the registered Agent"});
      await error.waitFor();
      assert.equal(await send.isDisabled(), true);
      assert.equal(await original.count(), 0, "Invalid discovery cannot show borrowed history");
      await page.screenshot({path: resolve(evidence, "managed-discovery-unavailable-desktop.png"), animations: "disabled"});
      await page.setViewportSize({width: 390, height: 844});
      await page.screenshot({path: resolve(evidence, "managed-discovery-unavailable-narrow.png"), animations: "disabled"});
      await page.setViewportSize({width: 1440, height: 960});
      mode = "ready";
      await error.getByRole("button", {name: "重新连接", exact: true}).click();
      await original.waitFor();
      await composer.fill("Keep this ordinary follow-up in the original Session.");
      await send.click();
      await page.getByText("已沿用当前 Goal 与 Agent Session。接下来会先核对状态，再继续推进。", {exact: true}).waitFor();
      const turnWrites = writes.filter(row => /\/turns$/.test(row.path));
      assert.equal(turnWrites.length, 1);
      assert.equal(turnWrites[0].path, `/api/chat/sessions/${sessionId}/turns`);
      assert.equal(Object.hasOwn(turnWrites[0].body, "execution_control"), false, "An ordinary follow-up does not gain execution authority from the receipt");
      assert.equal(writes.some(row => row.path === "/api/chat/sessions" && row.body?.goal_id === goalId), false,
        "Entering, reloading and retrying cannot create a replacement Goal Session");
      await page.setViewportSize({width: 390, height: 844});
      await original.evaluate(element => element.scrollIntoView({block: "start"}));
      await page.screenshot({path: resolve(evidence, "managed-original-long-narrow.png"), animations: "disabled"});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
      // Reopening the manager's receipt after reload is a read, never another apply.
      await page.goto(`${origin}/?statusUrl=/status.json&view=conversation`, {waitUntil: "domcontentloaded"});
      await row.waitFor(); await row.click(); await enter.waitFor(); await enter.click(); await original.waitFor();
      assert.equal(writes.filter(row => row.path === `/api/actions/${proposalId}/apply`).length, 1);
      assert.deepEqual(errors, []);
    } catch (error) {
      await page.screenshot({path: resolve(evidence, `${scenario}-failure.png`), animations: "disabled"});
      throw new Error(`${error.message}; pageerrors=${JSON.stringify(errors)}; url=${page.url()}; body=${(await page.locator("body").innerText()).slice(-10000)}`);
    } finally {await page.unrouteAll({behavior: "ignoreErrors"}); await page.close();}
    console.log(`goal-created-session browser: PASS ${scenario}`);
  }
} finally {await cleanupBrowserSmoke({browser, server, dashboardDir, fixturePaths: []});}
