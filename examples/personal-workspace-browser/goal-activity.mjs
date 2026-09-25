import assert from "node:assert/strict";
import { resolve } from "node:path";
import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

// Execution is shown only from session-owner facts; open Todos and quota eligibility stay "queued".
function taskSession(goalId, sessionId, activeTurnId, lastActivityAt, host = null) {
  return {
    ...(host ? { session_mode: "attached_host", host_surface: host } : { session_mode: "managed" }),
    session_id: sessionId, goal_id: goalId, agent_id: "codex", adapter_kind: "codex",
    channel_id: `task.${sessionId}`, status: activeTurnId ? "busy" : "ready", active_turn_id: activeTurnId,
    last_error_code: null, created_at: lastActivityAt, updated_at: lastActivityAt, last_activity_at: lastActivityAt, resumable: true,
  };
}

async function goalRow(page, title) {
  return page.locator(".personal-goal-row").filter({ has: page.locator("strong", { hasText: title }) }).first();
}

export const goalActivityScenario = {
  id: "goal-activity",
  async run({ browser, collectCoverage, url }) {
    const recent = new Date(Date.now() - 2 * 60_000).toISOString();
    const silent = new Date(Date.now() - 30 * 60_000).toISOString();
    const live = await openWorkspacePage(browser, url, {
      collectCoverage,
      beforeGoto: (_api, page) => {
        page.__loopxRuntime.sessions.set("activity-live", taskSession("progress-projection", "activity-live", "turn-activity-live", recent, "codex"));
        page.__loopxRuntime.sessions.set("activity-idle", taskSession("multi-agent-projection", "activity-idle", null, recent, "cursor"));
        page.__loopxRuntime.sessions.set("activity-silent", taskSession("research-monitor", "activity-silent", "turn-activity-silent", silent));
      },
    });
    const coverageEntries = [];
    try {
      const { page } = live;
      const running = await goalRow(page, "Progress Projection");
      const queued = await goalRow(page, "Multi Agent Projection");
      await running.locator("small", { hasText: "执行中" }).waitFor({ timeout: 15_000 });
      assert.match(await queued.locator("small").innerText(), /^已安排 · 在 Cursor 宿主中执行，状态以宿主为准/, "Queued work bound to an attached host defers live state to the host");
      const silentRow = await goalRow(page, "Research Monitor");
      assert.match(await silentRow.locator("small").innerText(), /^执行中 · 已 3\d 分钟无新动静/, "A silent claimed turn discloses its silence");
      assert.equal(await silentRow.locator(".personal-goal-mark.is-live").count(), 0, "A silent turn is never shown as live");
      assert.doesNotMatch(await queued.innerText(), /执行中|推进中/);
      assert.equal(await page.locator(".personal-goal-list .personal-goal-mark.is-live").count(), 1, "Only the Goal with an active turn is live");
      assert.equal(await running.locator(".personal-goal-mark.is-live").count(), 1);
      assert.match(await running.locator("small").innerText(), /执行中 · Codex 宿主 · \d+\s*分钟前/, "Running names the attached host and the observed activity time");
      const brief = page.locator(".personal-brief");
      assert.equal(await brief.getByTestId("personal-brief-running").locator(".personal-brief-row").count(), 2, "The brief lists Goals with a claimed turn, including a silent one");
      assert.match(await brief.getByTestId("personal-brief-running").innerText(), /Research Monitor\s+已 3\d 分钟无新动静/);
      assert.match(await brief.getByTestId("personal-brief-running").innerText(), /Progress Projection[\s\S]*\d+\s*分钟前/);
      assert.equal(await brief.locator(".personal-brief-tile.is-live").count(), 1);
      assert.equal(
        await brief.getByTestId("personal-brief-needs").locator(".personal-brief-row").count(),
        await page.getByTestId("personal-home-lane-needs_you").locator(".personal-home-goal-card").count(),
        "The brief and the needs-you lane agree",
      );
      assert.ok(await brief.getByTestId("personal-brief-completed").locator(".personal-brief-row").count() > 0, "Recently completed work is surfaced");
      await page.screenshot({ path: resolve(outputDir, "goal-activity-sidebar.png"), animations: "disabled" });
      await running.locator(".personal-goal-link").click();
      await page.locator(".personal-channel-activity", { hasText: "执行中" }).waitFor();
      await page.screenshot({ path: resolve(outputDir, "goal-activity-running-goal.png"), animations: "disabled" });
      await queued.locator(".personal-goal-link").click();
      await page.locator(".personal-channel-activity", { hasText: "已安排" }).waitFor();
      await page.setViewportSize({ width: 390, height: 844 });
      await page.screenshot({ path: resolve(outputDir, "goal-activity-mobile.png"), animations: "disabled" });
      coverageEntries.push(...await live.close());
    } catch (error) {
      await live.page.screenshot({ path: resolve(outputDir, "goal-activity-failed.png") });
      await live.close();
      throw error;
    }

    const offline = await openWorkspacePage(browser, url, {
      collectCoverage,
      beforeGoto: async (_api, page) => {
        await page.route("**/api/chat/sessions?*", (route) => route.request().method() === "GET"
          ? route.fulfill({ status: 503, contentType: "application/json", json: { ok: false, error: "unavailable" } })
          : route.fallback());
      },
    });
    try {
      const { page } = offline;
      const queued = await goalRow(page, "Multi Agent Projection");
      await queued.locator("small", { hasText: "暂时读不到执行状态" }).waitFor({ timeout: 15_000 });
      assert.equal(await page.locator(".personal-goal-mark.is-live").count(), 0, "An unreadable session owner never produces a live Goal");
      assert.match(await page.getByTestId("personal-brief-running").innerText(), /暂时读不到执行状态/, "The brief discloses unreadable run state");
      await page.screenshot({ path: resolve(outputDir, "goal-activity-unavailable.png"), animations: "disabled" });
      coverageEntries.push(...await offline.close());
    } catch (error) {
      await offline.page.screenshot({ path: resolve(outputDir, "goal-activity-unavailable-failed.png") });
      await offline.close();
      throw error;
    }
    return { coverageEntries, note: "Execution comes from active session turns; queued work and unreadable session state are never shown as running." };
  },
};
