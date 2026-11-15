#!/usr/bin/env node
// The original Task's primary control reads its typed owner; only explicit launch
// submits the original Todo, and recovery observes the same native operation.
// Synthetic HTTP replies isolate the browser contract; owning-backend qualification
// remains a separate packaged journey.
import assert from "node:assert/strict";
import {dirname, resolve} from "node:path";
import {fileURLToPath} from "node:url";
import {cleanupBrowserSmoke, launchBrowser, loadPlaywright, startViteDashboardServer, waitForHttp} from "../../../../examples/dashboard-browser-smoke-support.mjs";

const dashboardDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const port = Number(process.env.LOOPX_MANAGED_TASK_TEST_PORT ?? 5371);
const origin = `http://127.0.0.1:${port}`;
const server = await startViteDashboardServer({dashboardDir, port});
const storageKey = `loopx-managed-task:${JSON.stringify(["cart-repair", "codex", "original-session"])}`;
const session = {session_id: "original-session", goal_id: "cart-repair", goal_instance_id: null,
  managed_agent_id: "codex", channel_id: "agent.codex", session_mode: "managed_runtime"};
const ready = {todo_id: "original-cart-task", state: "ready", source: "goal-creation",
  canonical_done: false, observation: {check_count: 6, pinned_file_count: 1, basis_sha256: "confirmed-check-source"}};
const unbound = {todo_id: "original-cart-task", state: "unbound", source: null, canonical_done: false};
const native = (operationId, status = "queued") => ({session_id: session.session_id, turn_id: "original-native-turn",
  client_turn_id: `managed.${operationId}`, status, origin: "native_loopx", loopx_execution: true,
  loopx_request: {operation: "native_turn", todo_id: ready.todo_id,
    actor: {goal_id: session.goal_id, agent_id: "codex", session_id: session.session_id}}});
let browser;
async function probe(initial = {}) {
  const page = await browser.newPage();
  await page.clock.install();
  await page.addInitScript(({key, attempt}) => {
    localStorage.setItem("loopx-pw-locale", "en");
    if (attempt !== undefined && sessionStorage.getItem(key) === null) sessionStorage.setItem(key, attempt);
  }, {key: storageKey, attempt: initial.attempt});
  const state = {task: ready, turn: null, available: true, loseAck: false, sessions: [session], ...initial};
  const reads = [], writes = [], errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/api/**", async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.pathname === "/api/chat/sessions") return route.fulfill({json: {ok: true,
      schema_version: "loopx_chat_session_list_v1", goal_id: session.goal_id, managed_agent_id: "codex",
      goal_instance_id: null, sessions: state.sessions}});
    assert.equal(url.pathname, "/api/chat/sessions/original-session/native-turn", "No coordinator/settings/Task/session creation endpoint");
    if (request.method() === "GET") {
      reads.push(Object.fromEntries(url.searchParams));
      assert.equal(url.searchParams.get("original_task"), "true");
      assert.equal(url.searchParams.get("agent_id"), "codex");
      assert.equal(url.searchParams.has("todo_id"), false, "Task selection belongs to Core");
      return route.fulfill({json: {ok: true, session_id: session.session_id, managed_agent_id: "codex",
        registered_agents: ["codex"], available: state.available, task: state.task, turn: state.turn}});
    }
    const body = request.postDataJSON(); writes.push(body);
    assert.deepEqual(Object.keys(body).sort(), ["agent_id", "operation_id", "todo_id"]);
    assert.equal(body.agent_id, "codex"); assert.equal(body.todo_id, ready.todo_id);
    state.turn = native(body.operation_id);
    if (state.loseAck) return route.abort("failed");
    return route.fulfill({status: 202, json: {ok: true, session_id: session.session_id, turn: state.turn, created: true}});
  });
  const firstRead = initial.attempt === "{malformed" ? null : page.waitForResponse(row => row.url().includes("native-turn"));
  await page.goto(origin + "/smoke/managed-task-entry-smoke.html");
  if (firstRead) await firstRead;
  await page.waitForFunction(() => document.querySelector('output[aria-label="History observations"]'));
  const section = page.getByRole("region", {name: "Current Task", exact: true});
  const button = section.locator("button.goal-loopx-primary");
  const poll = async () => {
    const response = page.waitForResponse(row => new URL(row.url()).pathname.endsWith("/native-turn") && row.request().method() === "GET");
    await page.clock.fastForward(5100); await response;
    await page.clock.runFor(30);
  };
  return {page, state, reads, writes, errors, section, button, poll,
    async finish() {assert.deepEqual(errors, []); await page.close();}};
}
try {
  await waitForHttp(origin + "/smoke/managed-task-entry-smoke.html");
  browser = await launchBrowser(loadPlaywright().chromium);
  {
    const p = await probe({task: unbound, available: false});
    assert.equal(await p.section.count(), 0, "Unconfirmed checks expose no launch control");
    await p.page.clock.fastForward(16000); await p.page.clock.runFor(30);
    assert.equal(p.reads.length, 1, "An unconfigured default Goal has one initial native read and no empty background queries");
    assert.deepEqual(p.writes, []); await p.finish();
  }
  {
    const p = await probe(); await p.button.waitFor();
    assert.equal(await p.button.isDisabled(), false, "A ready definition permits repair despite a failed baseline");
    await p.page.clock.fastForward(16000); await p.page.clock.runFor(30);
    assert.equal(p.reads.length, 1, "A ready idle Task also waits for an explicit operation");
    p.state.task = unbound; p.state.available = false;
    await p.button.click();
    await p.page.waitForFunction(() => document.querySelector("button.goal-loopx-primary")?.disabled);
    assert.deepEqual(p.writes, [], "A stale ready viewport cannot launch against changed current checks");
    await p.finish();
  }
  {
    const p = await probe({loseAck: true}); await p.button.waitFor(); await p.button.click();
    await p.page.waitForFunction(() => document.querySelector(".goal-loopx-mode-status")?.textContent.includes("waiting to run"));
    assert.equal(p.writes.length, 1); const operation = p.writes[0].operation_id;
    assert.match(operation, /^task\./); assert.equal(await p.button.isDisabled(), true);
    assert.equal(await p.section.locator('[data-active="true"]').count(), 0, "Queued ACK recovery does not imply running");
    await p.page.reload(); await p.button.waitFor();
    assert.equal(await p.button.isDisabled(), true); assert.equal(p.writes.length, 1);
    assert.equal(p.reads.at(-1).operation_id, operation, "Reload reads the retained operation");
    await p.page.evaluate(() => {
      Object.defineProperty(document, "hidden", {configurable: true, value: true});
      document.dispatchEvent(new Event("visibilitychange"));
    });
    const hiddenReads = p.reads.length;
    await p.page.clock.fastForward(16000); await p.page.clock.runFor(30);
    assert.equal(p.reads.length, hiddenReads, "Hidden pending Tasks do not poll");
    const returned = p.page.waitForResponse(row => row.url().includes("native-turn"));
    await p.page.evaluate(() => {
      Object.defineProperty(document, "hidden", {configurable: true, value: false});
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await returned;
    p.state.turn = native(operation, "running"); await p.poll();
    assert.equal(await p.section.locator('[data-active="true"]').count(), 1);
    p.state.turn = native(operation, "failed"); await p.poll();
    assert.equal(await p.button.isDisabled(), false); assert.equal(p.writes.length, 1, "Failure never launches automatically");
    const terminalReads = p.reads.length;
    await p.page.clock.fastForward(16000); await p.page.clock.runFor(30);
    assert.equal(p.reads.length, terminalReads, "Known terminal Turns stop readback polling");
    await p.button.click();
    await p.page.waitForFunction(() => document.querySelector(".goal-loopx-mode-status")?.textContent.includes("waiting to run"));
    assert.equal(p.writes.length, 2); assert.notEqual(p.writes[1].operation_id, operation, "Explicit retry owns a fresh operation after a confirmed terminal Turn");
    assert.ok(Number(await p.page.getByLabel("History observations").textContent()) >= 3);
    await p.finish();
  }
  for (const mutation of ["operation", "agent", "goal", "session", "todo", "turn-id", "missing-turn", "ordinary-turn"]) {
    const saved = {operationId: "retained-request", todoId: ready.todo_id, turnId: "original-native-turn", status: "running"};
    const turn = native(saved.operationId, "failed");
    if (mutation === "operation") turn.client_turn_id = "managed.unrelated-request";
    if (mutation === "agent") turn.loopx_request.actor.agent_id = "other-agent";
    if (mutation === "goal") turn.loopx_request.actor.goal_id = "other-goal";
    if (mutation === "session") turn.session_id = "other-session";
    if (mutation === "todo") turn.loopx_request.todo_id = "other-task";
    if (mutation === "turn-id") turn.turn_id = "different-turn";
    if (mutation === "ordinary-turn") turn.loopx_execution = false;
    const p = await probe({attempt: JSON.stringify(saved), turn: mutation === "missing-turn" ? null : turn});
    await p.section.getByRole("alert").waitFor();
    assert.equal(await p.button.textContent(), "Refresh"); await p.button.click();
    await p.poll(); assert.deepEqual(p.writes, [], `${mutation}: unverifiable recovery only rereads`);
    assert.equal(await p.section.locator('[data-active="true"]').count(), 0); await p.finish();
  }
  {
    const p = await probe(); await p.button.waitFor();
    await p.page.evaluate(key => sessionStorage.setItem(key, "{malformed"), storageKey);
    await p.button.click(); await p.section.getByRole("alert").waitFor();
    assert.deepEqual(p.writes, [], "Storage corruption immediately before launch also holds the request");
    await p.finish();
  }
  {
    const p = await probe({attempt: "{malformed"}); await p.section.getByRole("alert").waitFor();
    await p.button.click(); await p.page.clock.fastForward(5100);
    assert.deepEqual(p.writes, []); assert.equal(p.reads.length, 0, "Unreadable saved operation is held without inventing another request");
    await p.finish();
  }
  {
    const p = await probe({attempt: JSON.stringify({operationId: "old-failed", todoId: ready.todo_id, status: "running"}), turn: native("old-failed", "failed")});
    await p.button.waitFor(); assert.equal(await p.button.isDisabled(), false, "A saved running label cannot override fresh terminal readback");
    assert.equal(await p.section.locator('[data-active="true"]').count(), 0);
    await p.page.evaluate(key => sessionStorage.setItem(key, "[]"), storageKey);
    await p.button.click();
    await p.section.getByRole("alert").waitFor(); await p.button.click();
    assert.deepEqual(p.writes, [], "Later storage corruption also revokes execution"); await p.finish();
  }
  console.log("managed-task-entry: check-readiness, original identity, explicit native launch, ACK recovery, liveness, reload, retry and malformed-storage boundaries passed");
} finally {await cleanupBrowserSmoke({browser, server, fixturePaths: []});}
