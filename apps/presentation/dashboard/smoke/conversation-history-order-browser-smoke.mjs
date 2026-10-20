import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { cleanupBrowserSmoke, launchBrowser, loadPlaywright, startViteDashboardServer, waitForHttp } from "../../../../examples/dashboard-browser-smoke-support.mjs";

const dashboardDir = fileURLToPath(new URL("..", import.meta.url));
const port = Number(process.env.LOOPX_HISTORY_ORDER_PORT ?? "5298");
const origin = `http://127.0.0.1:${port}`;
const server = startViteDashboardServer({ dashboardDir, port });
const scenarios = ["late-backfill", "failed-backfill", "backfill-during-refresh", "refresh-after-backfill", "overlapping-refreshes", "scope-switch", "scope-return", "retry-progress", "closed-first", "closed-only", "failed-current"];

function session(id, goal, version = 0) {
  return { session_id: id, goal_id: goal, agent_id: "codex", adapter_kind: "codex_app_server",
    channel_id: `goal.${goal}`, status: version ? "busy" : "ready", active_turn_id: version ? `turn-${version}` : null,
    last_error_code: null, resumable: true, created_at: id.startsWith("archive") ? "2026-01-01T00:00:00Z" : "2026-02-01T00:00:00Z",
    updated_at: "2026-02-01T00:00:00Z", last_activity_at: "2026-02-01T00:00:00Z" };
}

function snapshot(id, goal, version) {
  const current = session(id, goal, id.startsWith("current") ? version : 0);
  const texts = [goal === "b" ? "GOAL_B" : id.startsWith("closed") ? "CLOSED_BASE" : id.startsWith("archive") ? "ARCHIVE_BASE" : "CURRENT_BASE"];
  if (id.startsWith("closed")) Object.assign(current, { status: "closed", resumable: false, active_turn_id: null });
  if (version && goal === "a") texts.push(`${id.startsWith("archive") ? "ARCHIVE" : "CURRENT"}_${version}`);
  return { ok: true, schema_version: "loopx_chat_store_v1", session: current,
    active_turn: current.active_turn_id ? { turn_id: current.active_turn_id, status: "running", response: null } : null,
    messages: texts.map(text => ({ message_id: text, role: "agent", turn_id: null, text, created_at: current.created_at })) };
}

async function started(promise) {
  let timer;
  try {
    await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("Expected history request was not observed")), 5000); })]);
  } finally { clearTimeout(timer); }
}

let browser;
const failures = [];
try {
  await waitForHttp(origin);
  browser = await launchBrowser(loadPlaywright().chromium);
  for (const scenario of scenarios) {
    const page = await browser.newPage();
    const oldRead = Promise.withResolvers();
    const oldStarted = Promise.withResolvers();
    const fullRead = Promise.withResolvers();
    const fullStarted = Promise.withResolvers();
    let version = 0, mode = "initial", partial = true;
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.route("**/api/chat/sessions**", async route => {
      assert.equal(route.request().method(), "GET", "History observation never starts a Turn");
      const url = new URL(route.request().url());
      if (url.pathname === "/api/chat/sessions") {
        const goal = url.searchParams.get("goal_id");
        const closed = { ...session("closed-a", "a"), status: "closed", resumable: false };
        let sessions = goal === "b" ? [session("current-b", "b")]
          : [session("current-a", "a", version), session("archive-a", "a")];
        if (scenario === "closed-first") sessions.unshift(closed);
        if (scenario === "closed-only") sessions = [closed];
        if (scenario === "failed-current") sessions = [{ ...session("current-a", "a"), status: "resume_failed", resumable: false }];
        return route.fulfill({ json: { ok: true, schema_version: "loopx_chat_session_list_v1", sessions } });
      }
      const id = url.pathname.split("/").at(-1), goal = id === "current-b" ? "b" : "a";
      const response = snapshot(id, goal, version);
      if (scenario === "failed-current") Object.assign(response.session, { status: "resume_failed", resumable: false });
      if (id === "archive-a" && mode === "retry") {
        oldStarted.resolve();
        await oldRead.promise;
        return route.fulfill(scenario === "failed-backfill" ? { status: 503, json: { error: "Synthetic delayed read failure" } }
          : { json: snapshot(id, goal, 0) }).catch(() => {});
      }
      if (id === "current-a" && mode === "held-refresh") {
        fullStarted.resolve();
        await fullRead.promise;
        return route.fulfill({ json: response }).catch(() => {});
      }
      if (id === "archive-a" && partial) return route.fulfill({ status: 503, json: { error: "Synthetic missing history" } });
      return route.fulfill({ json: response });
    });
    const state = async () => JSON.parse(await page.getByLabel("History state").innerText());
    const ready = () => page.waitForFunction(() => JSON.parse(document.querySelector("output").textContent).phase === "ready");
    const retry = async () => {
      mode = "retry";
      await page.getByRole("button", { name: "Retry missing history" }).click();
      await started(oldStarted.promise);
    };
    const refresh = async (held = false) => {
      version += 1; partial = false; mode = held ? "held-refresh" : "refresh";
      const archive = held ? page.waitForResponse(reply => new URL(reply.url()).pathname === "/api/chat/sessions/archive-a") : null;
      await page.getByRole("button", { name: "Refresh history" }).click();
      if (held) { await started(fullStarted.promise); await (await archive).finished(); }
      else await page.waitForFunction(turn => JSON.parse(document.querySelector("output").textContent).lastRefreshTurn === turn, `turn-${version}`);
    };
    const release = async (gate, id) => {
      const response = page.waitForResponse(reply => new URL(reply.url()).pathname === `/api/chat/sessions/${id}`);
      gate.resolve();
      await (await response).finished();
      await page.evaluate(() => new Promise(resolveFrame => requestAnimationFrame(() => requestAnimationFrame(resolveFrame))));
    };
    try {
      await page.goto(`${origin}/smoke/conversation-history-order-smoke.html`);
      if (scenario === "closed-only" || scenario === "failed-current") {
        await ready();
        const observed = await state();
        assert.equal(observed.sessionId, scenario === "closed-only" ? null : "current-a");
        assert.equal(observed.status, scenario === "closed-only" ? undefined : "resume_failed");
        assert.deepEqual(observed.texts, [scenario === "closed-only" ? "CLOSED_BASE" : "CURRENT_BASE"]);
        assert.deepEqual(errors, []);
        console.log(`history-read-order: PASS ${scenario}`);
        continue;
      }
      await page.waitForFunction(() => JSON.parse(document.querySelector("output")?.textContent ?? "{}").phase === "partial");
      if (scenario === "backfill-during-refresh") {
        await refresh(true); await retry();
        await release(fullRead, "current-a");
        await release(oldRead, "archive-a");
      } else if (scenario === "overlapping-refreshes" || scenario === "scope-switch" || scenario === "scope-return") {
        await refresh(true);
        if (scenario === "overlapping-refreshes") await refresh();
        else {
          await page.getByRole("button", { name: "Switch Goal" }).click(); await ready();
          if (scenario === "scope-return") {
            version = 2; mode = "refresh";
            await page.getByRole("button", { name: "Switch Goal" }).click(); await ready();
          }
        }
        await release(fullRead, "current-a");
        await page.waitForFunction(() => JSON.parse(document.querySelector("output").textContent).lastRefreshTurn === "turn-1");
      } else {
        await retry();
        if (scenario === "refresh-after-backfill") await release(oldRead, "archive-a");
        if (scenario === "retry-progress") {
          version = 1; mode = "refresh";
          await page.getByRole("button", { name: "Refresh history" }).click();
          await page.waitForFunction(() => JSON.parse(document.querySelector("output").textContent).lastRefreshTurn === "turn-1");
          partial = false;
        } else await refresh();
        if (scenario !== "refresh-after-backfill") await release(oldRead, "archive-a");
        if (scenario === "retry-progress") { mode = "refresh"; await ready(); }
      }
      const observed = await state();
      assert.equal(observed.phase, "ready", `${scenario}: recovered history stays readable`);
      if (scenario === "scope-switch") {
        assert.equal(observed.goal, "b"); assert.deepEqual(observed.texts, ["GOAL_B"]);
      } else {
        const expectedVersion = scenario === "overlapping-refreshes" || scenario === "scope-return" ? 2 : 1;
        assert.equal(observed.status, "busy", `${scenario}: current execution cannot regress`);
        assert.equal(observed.turnId, `turn-${expectedVersion}`);
        assert.ok(observed.texts.includes(`CURRENT_${expectedVersion}`), `${scenario}: preserve the refreshed answer`);
        assert.ok(observed.texts.includes("CURRENT_BASE"), `${scenario}: preserve earlier history`);
        assert.ok(observed.texts.includes("ARCHIVE_BASE"), `${scenario}: missing history still recovers`);
        if (scenario === "closed-first") {
          assert.equal(observed.sessionId, "current-a");
          assert.ok(observed.texts.includes("CLOSED_BASE"), "Closed history stays readable alongside the current Session");
        }
      }
      assert.deepEqual(errors, []);
      console.log(`history-read-order: PASS ${scenario}`);
    } catch (error) {
      failures.push({ scenario, error: String(error), state: await state().catch(() => null) });
      console.error(`history-read-order: FAIL ${scenario}: ${error.message}`);
    } finally {
      oldRead.resolve(); fullRead.resolve(); await page.close();
    }
  }
  assert.deepEqual(failures, [], JSON.stringify(failures, null, 2));
} finally {
  await cleanupBrowserSmoke({ browser, server, fixturePaths: [] });
}
