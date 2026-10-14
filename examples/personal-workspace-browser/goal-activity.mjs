import assert from "node:assert/strict";
import { resolve } from "node:path";
import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

// Execution is shown only from session-owner facts; open Todos, quota eligibility and bound host threads stay "queued".
function taskSession(goalId, sessionId, activeTurnId, lastActivityAt, host = null, mode = host ? "attached_host" : "managed_runtime") {
  return {
    session_mode: mode,
    ...(host ? { host_surface: host } : {}),
    session_id: sessionId, goal_id: goalId, agent_id: "codex", adapter_kind: "codex",
    channel_id: `task.${sessionId}`, status: activeTurnId ? "busy" : "ready", active_turn_id: activeTurnId,
    last_error_code: null, created_at: lastActivityAt, updated_at: lastActivityAt, last_activity_at: lastActivityAt, resumable: true,
  };
}

async function goalRow(page, title) {
  return page.locator(".personal-goal-row").filter({ has: page.locator("strong", { hasText: title }) }).first();
}

// Execution is a session-owner fact, so the fixture pins every mixture the
// brief and the home lanes must tell apart: a managed turn, a claim-only Goal
// with no activity, a queued Goal with no active turn, and both mixed-clock
// directions where a claim and a managed turn share one Goal.
function seedSessions(page) {
  const recent = new Date(Date.now() - 2 * 60_000).toISOString();
  const silent = new Date(Date.now() - 30 * 60_000).toISOString();
  const old = new Date(Date.now() - 45 * 60_000).toISOString();
  page.__loopxRuntime.sessions.set("activity-live", taskSession("progress-projection", "activity-live", "turn-activity-live", recent));
  page.__loopxRuntime.sessions.set("activity-claimed", taskSession("product-release", "activity-claimed", "turn-activity-claimed", recent, "codex-app"));
  page.__loopxRuntime.sessions.set("activity-silent", taskSession("research-monitor", "activity-silent", "turn-activity-silent", silent));
  // A managed turn and an attached claim in one Goal are two different
  // facts: the fresh claim must not refresh the silent managed turn.
  page.__loopxRuntime.sessions.set("activity-fresh-claim", taskSession("research-monitor", "activity-fresh-claim", "turn-activity-fresh-claim", recent, "codex-app"));
  // The opposite mixture keeps its live ring from the managed turn and
  // still reports the older claim as a claim.
  page.__loopxRuntime.sessions.set("activity-old-claim", taskSession("progress-projection", "activity-old-claim", "turn-activity-old-claim", old, "codex-app"));
}

/** Title, count and rows of one brief tile, so both locales assert all three independently. */
async function briefTile(page, kind) {
  const tile = page.getByTestId(`personal-brief-${kind}`);
  await tile.waitFor({ state: "visible", timeout: 15_000 });
  return {
    count: Number(await tile.locator("header b").innerText()),
    rows: await tile.locator(".personal-brief-row").count(),
    text: await tile.innerText(),
    title: await tile.locator("header span").innerText(),
  };
}

/** Home lane title, count and cards, mirroring the brief assertions. */
async function lane(page, key) {
  const section = page.getByTestId(`personal-home-lane-${key}`);
  await section.waitFor({ state: "visible", timeout: 15_000 });
  return {
    count: Number(await section.locator("header b").innerText()),
    text: await section.innerText(),
    title: await section.locator("header span").innerText(),
  };
}

/** Whether the leading `prefix` of a row's status line is fully visible at the sidebar width. */
async function leadVisible(row, prefix) {
  return row.locator("small").evaluate((element, lead) => {
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    let node = walker.nextNode();
    while (node && !node.textContent.includes(lead)) node = walker.nextNode();
    if (!node) return false;
    const start = node.textContent.indexOf(lead);
    const range = document.createRange();
    range.setStart(node, start);
    range.setEnd(node, start + lead.length);
    const clip = element.getBoundingClientRect();
    return range.getBoundingClientRect().right <= clip.right + 0.5;
  }, prefix);
}

/** Brief tiles that escape their container, so a new bucket cannot break the row. */
async function briefTileOverflow(page) {
  return page.locator(".personal-brief").evaluate((brief) => {
    const bounds = brief.getBoundingClientRect();
    return [...brief.querySelectorAll(".personal-brief-tile")].filter((tile) => {
      const rect = tile.getBoundingClientRect();
      return rect.left < bounds.left - 1 || rect.right > bounds.right + 1;
    }).length;
  });
}

export const goalActivityScenario = {
  id: "goal-activity",
  async run({ browser, collectCoverage, url }) {
    const live = await openWorkspacePage(browser, url, {
      collectCoverage,
      beforeGoto: (_api, page) => seedSessions(page),
    });
    const coverageEntries = [];
    try {
      const { page } = live;
      const running = await goalRow(page, "Progress Projection");
      const queued = await goalRow(page, "Multi Agent Projection");
      await running.locator("small", { hasText: "执行中" }).waitFor({ timeout: 15_000 });
      assert.match(await queued.locator("small").innerText(), /^已安排 · 由 Codex App 线程负责，执行情况以宿主为准/, "A Goal owned by a bound host thread defers execution to the host");
      assert.doesNotMatch(await queued.innerText(), /执行中|推进中/);
      const claimed = await goalRow(page, "Product Release");
      assert.match(await claimed.locator("small").innerText(), /^宿主已领取 · Codex App 宿主 · 领取于\d+\s*分钟前/, "An attached claim reports its claim time, not ongoing activity");
      assert.equal(await claimed.locator(".personal-goal-mark.is-live").count(), 0, "An attached claim is never shown as live");
      const silentRow = await goalRow(page, "Research Monitor");
      assert.equal(await silentRow.locator(".personal-goal-mark.is-live").count(), 0, "A silent turn is never shown as live");
      assert.equal(await page.locator(".personal-goal-list .personal-goal-mark.is-live").count(), 1, "Only the Goal with a recently active managed turn is live");
      assert.equal(await running.locator(".personal-goal-mark.is-live").count(), 1);
      assert.match(await running.locator("small").innerText(), /^执行中 · Codex App 宿主 · \d+\s*分钟前 · 领取于\d+\s*分钟前/, "A managed turn leads with its own activity time and the attached claim stays a claim");
      assert.match(await silentRow.locator("small").innerText(), /^执行中 · Codex App 宿主 · 已 3\d 分钟无新动静 · 领取于\d+\s*分钟前/, "A silent turn discloses its own silence while a fresh claim stays a claim");
      assert.equal(await silentRow.locator(".personal-goal-mark.is-live").count(), 0, "A recent attached claim never makes a silent managed turn live");
      // The brief splits managed execution from a host-only claim: the two
      // managed Goals are "running", the claim-only Goal never enters that count,
      // and the queued Goal stays queued.
      const briefRunning = await briefTile(page, "running");
      assert.equal(briefRunning.title, "正在执行");
      assert.equal(briefRunning.count, 2, "Only a managed turn counts as running");
      assert.equal(briefRunning.rows, 2);
      assert.match(briefRunning.text, /Progress Projection[\s\S]*\d+\s*分钟前/);
      assert.match(briefRunning.text, /Research Monitor\s+Codex App 宿主 · 已 3\d 分钟无新动静 · 领取于\d+\s*分钟前/, "The brief separates a silent managed turn from a fresh claim");
      assert.doesNotMatch(briefRunning.text, /Product Release/, "A claim-only Goal never enters the running tile");
      assert.match(briefRunning.text, /另有 1 个已安排/, "Queued work is reported without entering the running count");
      const briefClaimed = await briefTile(page, "claimed");
      assert.equal(briefClaimed.title, "宿主已领取");
      assert.equal(briefClaimed.count, 1);
      assert.equal(briefClaimed.rows, 1);
      assert.match(briefClaimed.text, /Product Release\s+Codex App 宿主 · 领取于\d+\s*分钟前/, "The claimed tile keeps the pending work reachable with its claim fact");
      assert.equal(await page.locator(".personal-brief .personal-brief-tile.is-live").count(), 1);
      assert.equal(await page.locator(".personal-brief .personal-brief-tile").count(), 4, "The brief renders one tile per populated bucket");
      assert.equal(await briefTileOverflow(page), 0, "The four brief tiles fit the manager width");
      await page.setViewportSize({ width: 390, height: 844 });
      assert.equal(await briefTileOverflow(page), 0, "The brief tiles fit the 390px mobile width");
      await page.screenshot({ path: resolve(outputDir, "goal-activity-home-mobile.png"), animations: "disabled" });
      await page.setViewportSize({ width: 1512, height: 982 });

      // The home lanes carry the same rule, so a claim-only Goal cannot inflate
      // the "执行中" lane either.
      const runningLane = await lane(page, "running");
      assert.equal(runningLane.title, "执行中");
      assert.equal(runningLane.count, 2, "The running lane count excludes a host-only claim");
      assert.doesNotMatch(runningLane.text, /Product Release/);
      const claimedLane = await lane(page, "claimed");
      assert.equal(claimedLane.title, "宿主已领取");
      assert.equal(claimedLane.count, 1);
      assert.match(claimedLane.text, /Product Release/);
      assert.equal(
        await page.getByTestId("personal-brief-needs").locator(".personal-brief-row").count(),
        await page.getByTestId("personal-home-lane-needs_you").locator(".personal-home-goal-card").count(),
        "The brief and the needs-you lane agree",
      );
      assert.deepEqual(
        await page.getByTestId("personal-brief-completed").locator(".personal-brief-row strong").allTextContents(),
        ["Newest cross-goal result", "Completed B", "Completed C"],
        "Recent work survives status parsing and is sorted across Goals before taking three rows",
      );
      await page.screenshot({ path: resolve(outputDir, "goal-activity-sidebar.png"), animations: "disabled" });
      await running.locator(".personal-goal-link").click();
      await page.getByTestId("goal-vitals").locator(".goal-vital-value", { hasText: "执行中" }).first().waitFor();
      await page.screenshot({ path: resolve(outputDir, "goal-activity-running-goal.png"), animations: "disabled" });
      await queued.locator(".personal-goal-link").click();
      await page.getByTestId("goal-vitals").locator(".goal-vital-value", { hasText: "已安排" }).first().waitFor();
      await page.setViewportSize({ width: 390, height: 844 });
      await page.screenshot({ path: resolve(outputDir, "goal-activity-mobile.png"), animations: "disabled" });
      coverageEntries.push(...await live.close());
    } catch (error) {
      await live.page.screenshot({ path: resolve(outputDir, "goal-activity-failed.png") });
      await live.close();
      throw error;
    }

    const minutesAgo = (minutes) => new Date(Date.now() - minutes * 60_000).toISOString();
    const thread = (state, lastEventAt) => ({ agent_id: "codex", host_surface: "codex-app", state, last_event_at: lastEventAt });
    const observed = await openWorkspacePage(browser, url, {
      collectCoverage,
      beforeGoto: (api) => {
        api.hostThreadActivity = {
          "product-release": { completeness: "complete", threads: [thread("turn_open", minutesAgo(1)), thread("idle", minutesAgo(90))] },
          "multi-agent-projection": { completeness: "complete", threads: [thread("idle", minutesAgo(40)), thread("archived", null)] },
          "research-monitor": { completeness: "complete", threads: [thread("turn_open", minutesAgo(8 * 60))] },
        };
      },
    });
    try {
      const { page } = observed;
      const running = await goalRow(page, "Product Release");
      await running.locator("small", { hasText: "执行中" }).waitFor({ timeout: 15_000 });
      assert.match(await running.locator("small").innerText(), /^执行中 · Codex App 宿主 · \d+\s*分钟前/, "An open host-recorded turn is execution with its last event time");
      assert.equal(await running.locator(".personal-goal-mark.is-live").count(), 1, "A recently active host turn is live");
      assert.match(await (await goalRow(page, "Multi Agent Projection")).locator("small").innerText(), /^已安排 · Codex App 线程空闲/, "Observed idle threads are reported as idle");
      const abandoned = await goalRow(page, "Research Monitor");
      assert.doesNotMatch(await abandoned.locator("small").innerText(), /执行中/, "An open turn with no event for hours is not execution");
      assert.equal(await page.locator(".personal-goal-list .personal-goal-mark.is-live").count(), 1);
      assert.match(await page.getByTestId("personal-brief-running").innerText(), /Product Release\s+Codex App 宿主 · \d+\s*分钟前/);
      assert.equal(await page.getByTestId("personal-brief-running").locator(".personal-brief-row").count(), 1);
      await page.screenshot({ path: resolve(outputDir, "goal-activity-host-observed.png"), animations: "disabled" });
      coverageEntries.push(...await observed.close());
    } catch (error) {
      await observed.page.screenshot({ path: resolve(outputDir, "goal-activity-host-observed-failed.png") });
      await observed.close();
      throw error;
    }

    for (const completeness of ["incomplete", undefined, "unrecognized"]) {
      const partial = await openWorkspacePage(browser, url, {
        collectCoverage,
        beforeGoto: (api) => {
          api.hostThreadActivity = {
            "product-release": { completeness: "incomplete", threads: [thread("turn_open", minutesAgo(1))] },
            "multi-agent-projection": { completeness, threads: Array.from({ length: 32 }, () => thread("idle", minutesAgo(40))) },
            "research-monitor": { completeness: "complete", threads: [{ ...thread("unknown", null), reason: "record_unrecognized" }] },
          };
        },
      });
      try {
        const { api, page } = partial;
        const queued = await goalRow(page, "Multi Agent Projection");
        await queued.locator("small", { hasText: "执行情况以宿主为准" }).waitFor({ timeout: 15_000 });
        assert.doesNotMatch(await queued.locator("small").innerText(), /线程空闲|执行中/, "A partial or legacy sample cannot prove every bound thread idle");
        assert.match(await (await goalRow(page, "Product Release")).locator("small").innerText(), /^执行中/, "A positive open-turn observation remains usable with partial coverage");
        assert.doesNotMatch(await (await goalRow(page, "Research Monitor")).locator("small").innerText(), /线程空闲|执行中/, "An unrecognized record never becomes idle or running");
        assert.equal(await page.getByTestId("personal-brief-running").locator(".personal-brief-row").count(), 1);
        if (completeness === "incomplete") {
          await page.screenshot({ path: resolve(outputDir, "goal-activity-partial.png"), animations: "disabled" });
          await page.setViewportSize({ width: 390, height: 844 });
          await page.screenshot({ path: resolve(outputDir, "goal-activity-partial-mobile.png"), animations: "disabled" });
        }
        api.hostThreadActivity["multi-agent-projection"] = { completeness: "complete", threads: [thread("idle", minutesAgo(1))] };
        await page.setViewportSize({ width: 1512, height: 982 });
        await page.reload({ waitUntil: "networkidle" });
        await (await goalRow(page, "Multi Agent Projection")).locator("small", { hasText: "线程空闲" }).waitFor({ timeout: 15_000 });
        coverageEntries.push(...await partial.close());
      } catch (error) {
        await partial.page.screenshot({ path: resolve(outputDir, "goal-activity-partial-failed.png") });
        await partial.close();
        throw error;
      }
    }

    // The same rule must hold for the English copy: the title, count and rows
    // are asserted independently so a localized regression cannot hide behind
    // the Chinese scenario.
    const english = await openWorkspacePage(browser, url, {
      collectCoverage,
      beforeGoto: async (_api, page) => {
        await page.addInitScript(() => localStorage.setItem("loopx-pw-locale", "en"));
        seedSessions(page);
      },
    });
    try {
      const { page } = english;
      assert.equal(await page.locator("html").getAttribute("lang"), "en");
      const runningTile = await briefTile(page, "running");
      assert.equal(runningTile.title, "Running now");
      assert.equal(runningTile.count, 2, "Only a managed turn counts as running in English too");
      assert.equal(runningTile.rows, 2);
      assert.doesNotMatch(runningTile.text, /Product Release/, "A claim-only Goal never enters the running tile");
      assert.match(runningTile.text, /2 queued|more queued/, "Queued work is reported without entering the running count");
      const claimedTile = await briefTile(page, "claimed");
      assert.equal(claimedTile.title, "Claimed by host");
      assert.equal(claimedTile.count, 1);
      assert.equal(claimedTile.rows, 1);
      assert.match(claimedTile.text, /Product Release[\s\S]*Codex App host[\s\S]*claimed \d+ min/, "The claimed tile keeps the pending work reachable with its claim fact");
      const claimedLane = await lane(page, "claimed");
      assert.equal(claimedLane.title, "Claimed by host");
      assert.equal(claimedLane.count, 1);
      assert.match(claimedLane.text, /Product Release/);
      assert.equal((await lane(page, "running")).count, 2, "The English running lane excludes a host-only claim");
      await page.screenshot({ path: resolve(outputDir, "goal-activity-sidebar-english.png"), animations: "disabled" });
      coverageEntries.push(...await english.close());
    } catch (error) {
      await english.page.screenshot({ path: resolve(outputDir, "goal-activity-english-failed.png") });
      await english.close();
      throw error;
    }

    // A turn whose session mode is missing or outside the backend's closed set
    // is not managed execution and not a host claim: it is named as unknown,
    // never live, and the brief does not claim that nothing is running.
    const unrecognized = await openWorkspacePage(browser, url, {
      collectCoverage,
      beforeGoto: (_api, page) => {
        const recent = new Date(Date.now() - 2 * 60_000).toISOString();
        page.__loopxRuntime.sessions.set("activity-unknown-mode", taskSession("progress-projection", "activity-unknown-mode", "turn-unknown-mode", recent, "codex-app", "cloud_runtime"));
        const { session_mode: _omitted, ...missingMode } = taskSession("research-monitor", "activity-missing-mode", "turn-missing-mode", recent);
        page.__loopxRuntime.sessions.set("activity-missing-mode", missingMode);
      },
    });
    try {
      const { page } = unrecognized;
      for (const title of ["Progress Projection", "Research Monitor"]) {
        const row = await goalRow(page, title);
        await row.locator("small", { hasText: "执行方式未知" }).waitFor({ timeout: 15_000 });
        assert.match(await row.locator("small").innerText(), /^执行方式未知 · 回合未结束/, `${title}: an unrecognized mode is named, not read as managed`);
        assert.doesNotMatch(await row.locator("small").innerText(), /执行中|宿主已领取|Codex App 宿主/, `${title}: neither managed execution nor a host claim`);
        assert.ok(await leadVisible(row, "执行方式未知"), `${title}: the unknown mode survives sidebar truncation`);
      }
      assert.equal(await page.locator(".personal-goal-list .personal-goal-mark.is-live").count(), 0, "An unrecognized mode is never live");
      const runningTile = await briefTile(page, "running");
      assert.equal(runningTile.count, 0, "An unrecognized mode never counts as running");
      assert.match(runningTile.text, /没有可确认的执行 · 2 个 Goal 执行方式未知/, "The empty running tile discloses the open turns");
      assert.equal(await page.getByTestId("personal-brief-claimed").count(), 0, "An unrecognized mode never counts as a host claim");
      assert.equal(await page.getByTestId("personal-home-lane-running").count(), 0, "An unrecognized mode never enters the running lane");
      await page.screenshot({ path: resolve(outputDir, "goal-activity-mode-unrecognized.png"), animations: "disabled" });
      await page.evaluate(() => localStorage.setItem("loopx-pw-locale", "en"));
      await page.reload({ waitUntil: "networkidle" });
      const englishRow = await goalRow(page, "Progress Projection");
      await englishRow.locator("small", { hasText: "Mode unknown" }).waitFor({ timeout: 15_000 });
      assert.match(await englishRow.locator("small").innerText(), /^Mode unknown · turn open/);
      assert.ok(await leadVisible(englishRow, "Mode unknown"), "The English unknown mode survives sidebar truncation");
      assert.match(await page.getByTestId("personal-brief-running").innerText(), /No verified run · 2 Goal\(s\) with unknown execution mode/);
      await page.screenshot({ path: resolve(outputDir, "goal-activity-mode-unrecognized-english.png"), animations: "disabled" });
      await page.setViewportSize({ width: 390, height: 844 });
      assert.equal(await briefTileOverflow(page), 0, "The disclosed empty state fits the 390px mobile width");
      await page.locator(".personal-mobile-menu").click();
      assert.ok(await leadVisible(await goalRow(page, "Progress Projection"), "Mode unknown"), "The unknown mode survives truncation in the mobile navigation");
      await page.screenshot({ path: resolve(outputDir, "goal-activity-mode-unrecognized-mobile.png"), animations: "disabled" });
      coverageEntries.push(...await unrecognized.close());
    } catch (error) {
      await unrecognized.page.screenshot({ path: resolve(outputDir, "goal-activity-mode-unrecognized-failed.png") });
      await unrecognized.close();
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
    return { coverageEntries, note: "Execution comes from active session turns and open host-recorded turns; attached claims show claim time without a live ring, idle or abandoned host threads stay queued, and unreadable session state is never running." };
  },
};
