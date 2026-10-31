import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { resolveTestPython } from "../../scripts/test-python.mjs";
import { outputDir, repoRoot } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

// The Goal masthead keeps identity and the stop control across Goal views;
// six typed vitals belong to Overview. Expected values follow their semantics:
// the fixture Goal reports the same 4087 completed advancement Todos as the
// completed-history read, has three open and two deferred advancement Todos
// plus a done monitor, two blocking decisions, a 7-day usage window and one
// host-recorded open turn.
const goalId = "progress-projection";
const title = "Quarterly cash-flow study";
const minutesAgo = (minutes) => new Date(Date.now() - minutes * 60_000).toISOString();

const acceptance = {
  schema_version: "goal_acceptance_observation_projection_v0", goal_id: goalId, read_only: true, acceptance_assessed: false,
  coverage: "partial", missing_sources: [], truncated: false, historical_progress: [], acceptance_gaps: [], guards: [],
  next_action: null, next_action_source: null,
  goal_acceptance_contract: {
    enabled: true, revision: 2, digest: `sha256:${"a".repeat(64)}`, objective: "Reconcile operating cash flow for three fiscal years from primary filings.",
    scope: { kind: "all_advancement" }, non_goals: [], held_todo_ids: [], status: "failed",
    criteria: [
      { id: "sources", description: "Every figure cites a primary filing" },
      { id: "reconcile", description: "Totals reconcile to the reported statements" },
      { id: "review", description: "An independent reviewer signs off" },
    ],
    tasks: [],
    verification: { operation_id: "verify-1", contract_revision: 2, contract_digest: `sha256:${"a".repeat(64)}`, todo_id: null,
      results: [{ criterion_id: "sources", passed: true, exit_code: 0 }, { criterion_id: "reconcile", passed: false, exit_code: 1 }] },
  },
};

function populate(api, { usage = true, hostTurn = true } = {}) {
  api.hostThreadActivity = hostTurn ? { [goalId]: { completeness: "complete", threads: [
    { agent_id: "codex", host_surface: "codex-app", state: "turn_open", last_event_at: minutesAgo(2) },
  ] } } : {};
  api.transformStatus = (fixture) => {
    const goal = fixture.run_history.goals.find((item) => item.id === goalId);
    if (goal) Object.assign(goal, { display_name: title, acceptance_observation: acceptance });
    const queueItem = fixture.attention_queue.items.find((item) => item.goal_id === goalId);
    if (queueItem?.project_asset?.agent_todos) queueItem.project_asset.agent_todos.advancement_done_count = 4087;
    if (queueItem && goal?.activation_state !== "stopped") {
      queueItem.waiting_on = "user_or_controller";
      queueItem.user_todos = { source_section: "User Todo", open_count: 2, total_count: 2, items: [
        { done: false, status: "open", index: 0, role: "user", task_class: "user_gate", goal_id: goalId, todo_id: "masthead-gate-scope", text: "Approve the FY2023 restatement treatment", updated_at: minutesAgo(190) },
        { done: false, status: "open", index: 1, role: "user", task_class: "user_gate", goal_id: goalId, todo_id: "masthead-gate-source", text: "Choose the segment disclosure source", updated_at: minutesAgo(25) },
      ] };
    }
    if (usage) {
      fixture.usage_summary.goals.push({ goal_id: goalId, cost_usd_24h: 3.1, cost_usd_7d: 18.4, duration_ms_24h: 2_400_000, duration_ms_7d: 11_520_000 });
    }
  };
}

async function openGoal(page, name) {
  await page.locator(".personal-goal-row").filter({ has: page.locator("strong", { hasText: name }) }).first().locator(".personal-goal-link").click();
  await page.getByTestId("goal-vitals").waitFor({ state: "visible", timeout: 15_000 });
}

/** The full activity sentence survives on the sidebar row's accessible name. */
async function rowSentence(page, name) {
  return (await page.locator(".personal-goal-row").filter({ has: page.locator("strong", { hasText: name }) }).first()
    .locator("small").first().getAttribute("aria-label")) ?? "";
}

const bar = (page) => page.getByTestId("goal-vitals");
const stateWord = async (page) => (await bar(page).locator(".goal-stagebar-meta > span").first().innerText()).trim();
const workText = async (page) => (await bar(page).locator(".goal-stagebar-work").innerText()).trim();
const lastTime = async (page) => (await bar(page).locator(".goal-stagebar-time").innerText()).trim();
const usageVals = async (page) => ({
  cost: (await bar(page).locator(".goal-stagebar-usage-item").nth(0).locator(".goal-stagebar-usage-value").innerText()).trim(),
  agentTime: (await bar(page).locator(".goal-stagebar-usage-item").nth(1).locator(".goal-stagebar-usage-value").innerText()).trim(),
});

export const goalMastheadScenario = {
  id: "goal-masthead",
  async run({ browser, collectCoverage, url }) {
    const coverageEntries = [];
    const session = await openWorkspacePage(browser, url, { collectCoverage, viewport: { width: 1440, height: 900 }, beforeGoto: (api) => populate(api) });
    const { api, page } = session;
    const actionRequests = [];
    page.on("request", (request) => { if (new URL(request.url()).pathname.startsWith("/api/actions/")) actionRequests.push(request.url()); });
    try {
      const pausedClock = await page.addStyleTag({content: "*, *::before, *::after {animation-play-state: paused !important;}"});
      await openGoal(page, title);
      assert.equal(await page.locator(".personal-channel-title h1").innerText(), title);
      assert.match(await page.locator(".goal-masthead-breadcrumb").innerText(), /工作台\s*Goals\s*Quarterly cash-flow study/);
      assert.equal(await page.getByTestId("goal-vitals").locator(".goal-stagebar-track li").count(), 3, "The bar is a three-stage track");
      assert.equal(await page.getByTestId("goal-vitals").locator(".goal-stagebar-track li.is-current").count(), 1, "Exactly one stage is current");

      // A native WebView may suspend its animation clock while an accessible
      // navigation commits. The first frame must still contain the real values.
      const firstFrame = await page.locator('[data-testid="goal-vitals"] .goal-stagebar-meta > span, [data-testid="goal-vitals"] .goal-stagebar-usage-value').evaluateAll(values => values.map(value => {
        let opacity = 1;
        for (let node = value; node; node = node.parentElement) opacity *= Number(getComputedStyle(node).opacity);
        return opacity;
      }));
      await page.screenshot({path: resolve(outputDir, "goal-masthead-paused-first-frame.png"), animations: "allow"});
      assert.ok(firstFrame.length >= 5, "The bar exposes state, work, last activity, cost and Agent time");
      assert(firstFrame.every(opacity => opacity === 1), "Authoritative Goal readings remain visible before the animation clock advances");
      await pausedClock.evaluate(element => element.remove());

      assert.equal(await stateWord(page), "等你", "A waiting decision leads the state, as in the Goal list");
      assert.match(await rowSentence(page, title), /另有执行进行中/, "The observed turn stays visible beside the decision on the row's full sentence");
      assert.equal(await page.locator(".personal-goal-row").filter({ has: page.locator("strong", { hasText: title }) }).locator(".personal-goal-mark.is-live").count(), 1, "A fresh host-recorded turn is live");
      assert.equal(await workText(page), "4087/4092", "done from the owner count; open counts open and deferred work and skips monitors");
      const rail = page.locator(".goal-overview-attention");
      assert.equal((await rail.locator(".goal-overview-count").innerText()).trim(), "2", "The attention rail carries the qualified blocking total");
      assert.equal((await bar(page).locator(".goal-stagebar-wait").innerText()).trim(), "等你决定", "A blocking decision is named in the bar, not only coloured");
      assert.equal(await bar(page).locator(".goal-stagebar-state.is-attention").count(), 1, "The state glyph takes the attention tone");
      assert.equal(await bar(page).locator(".goal-stagebar-state.is-danger").count(), 0, "Needs-you is attention; danger stays reserved for repair");
      assert.match((await rail.locator("li time").allInnerTexts()).join(" "), /3\s*小时前/, "The oldest waiting decision keeps its own age among the listed rows");
      assert.deepEqual(await usageVals(page), { cost: "$18.40", agentTime: "3.2h" }, "Agent time and Cost show the widest window's values");
      const usageTitle = await bar(page).locator(".goal-stagebar-usage").getAttribute("title");
      assert.match(usageTitle, /近 24 小时 \$3\.10[\s\S]*近 7 天 \$18\.40/, "Cost keeps both windows in its title");
      assert.match(usageTitle, /近 24 小时 40\.0m[\s\S]*近 7 天 3\.2h/, "Agent time keeps both windows in its title");
      assert.match(await lastTime(page), /2\s*分钟前/, "The bar keeps the relative last-activity time");
      assert.equal(await bar(page).locator(".goal-stagebar-time").getAttribute("title"), "Codex App",
        "The activity's owning host stays readable on the time slot's title");

      await page.locator(".personal-goal-tabs button", { hasText: "概览" }).click();
      const direction = page.locator(".goal-overview-direction");
      // This masthead fixture supplies a retained status snapshot, not a
      // current local acceptance read. Keep that provenance visible.
      assert.match(await direction.innerText(), /Reconcile operating cash flow[\s\S]*当前保留旧快照/);
      assert.doesNotMatch(await direction.innerText(), /项条件当前通过/);
      assert.equal(await direction.locator(".goal-overview-criteria .is-unverified").count(), 3);
      assert.equal(await direction.locator(".goal-overview-criteria .is-failed").count(), 0);
      assert.equal(await direction.getByRole("button", { name: "检查交付", exact: true }).count(), 0);
      assert.equal(await page.locator(".goal-overview-usage").count(), 0, "Usage moved to the vitals strip");
      await page.screenshot({ path: resolve(outputDir, "goal-masthead-overview.png"), animations: "disabled" });

      await page.emulateMedia({ reducedMotion: "reduce" });
      await page.evaluate(() => {
        const target = document.getElementById("goal-overview-attention");
        const scroll = target.scrollIntoView.bind(target);
        target.scrollIntoView = (options) => { target.dataset.scrollBehavior = options?.behavior ?? "auto"; scroll(options); };
      });
      await page.getByTestId("goal-vitals").locator(".goal-stagebar-track button").nth(2).click();
      await page.waitForFunction(() => document.activeElement?.id === "goal-overview-attention");
      assert.equal(await page.locator("#goal-overview-attention").getAttribute("data-scroll-behavior"), "auto", "Reduced motion reaches the decision directly without a scrolling animation");
      await page.emulateMedia({ reducedMotion: null });
      await page.getByTestId("goal-vitals").locator(".goal-stagebar-track button").nth(1).click();
      await page.locator('.personal-goal-tabs button[aria-current="page"]', { hasText: "任务" }).waitFor();
      const openLane = page.locator(".personal-task-lane").filter({ has: page.locator("header strong", { hasText: "待执行 / 进行中" }) });
      assert.equal(await openLane.locator("header > span").innerText(), "5", "The board lists the same open work the strip counts");
      await page.locator('[data-goal-panel="tasks"]').getByTestId("completed-task-lane").locator("header", { hasText: "4087" }).waitFor({ timeout: 10_000 });
      await page.locator(".personal-goal-tabs button", { hasText: "对话" }).click();
      await page.locator('.personal-goal-tabs button[aria-current="page"]', { hasText: "对话" }).waitFor();
      assert.equal(await page.getByTestId("goal-vitals").count(), 0, "Reading views do not repeat Overview statistics");
      assert.equal(await page.locator(".personal-channel-title h1").innerText(), title, "Switching views retains Goal identity");
      assert.equal(await page.locator(".goal-lifecycle-trigger").count(), 1, "The lifecycle control remains available outside Overview");
      assert.equal(actionRequests.length, 0, "Opening the Goal, switching views and focusing vitals sends no action request");

      await page.locator(".personal-goal-tabs button", { hasText: "概览" }).click();
      await page.getByTestId("goal-vitals").waitFor({ state: "visible" });
      const stop = page.locator(".goal-lifecycle-trigger");
      const pausedConfirmationClock = await page.addStyleTag({content: "*, *::before, *::after {animation-play-state: paused !important;}"});
      await stop.click();
      const dialog = page.getByRole("dialog", { name: "停止这个 Goal？" });
      await dialog.waitFor();
      assert.match(await dialog.innerText(), /已在运行的工具调用会执行完[\s\S]*只有恢复才能重新获得调度资格/);
      assert.equal(await dialog.evaluate(element => Number(getComputedStyle(element).opacity)), 1,
        "Permission and execution consequences remain readable with a suspended entrance clock");
      await pausedConfirmationClock.evaluate(element => element.remove());
      await page.screenshot({ path: resolve(outputDir, "goal-masthead-stop-confirm.png"), animations: "disabled" });
      await page.keyboard.press("Escape");
      await dialog.waitFor({ state: "detached" });
      assert.ok(await stop.evaluate((node) => node === document.activeElement), "Escape returns focus to the trigger");
      assert.equal(actionRequests.length, 0, "Cancelling sends nothing");

      await stop.click();
      await dialog.getByRole("button", { name: "停止 Goal" }).click();
      await page.locator(".goal-lifecycle-trigger", { hasText: "恢复" }).waitFor({ timeout: 10_000 });
      assert.equal(api.goalActivationStates.get(goalId), "stopped");
      assert.equal(await page.locator(".personal-channel-title h1").innerText(), title, "The page stays on the Goal to read back the stop");
      assert.equal(await stateWord(page), "正在停止", "An observed open turn finishes after the stop");
      assert.match((await bar(page).locator(".goal-stagebar-meta > span").first().getAttribute("title")) ?? "", /Turn 收尾中/,
        "The finishing sentence stays readable on the state slot's title");
      assert.equal(await page.locator(".personal-goal-row").filter({ has: page.locator("strong", { hasText: title }) }).locator(".personal-goal-mark.is-live").count(), 0, "A stopping turn is no longer live");
      assert.equal(api.actionApplies.length, 1, "One confirmed stop applies once");
      await page.screenshot({ path: resolve(outputDir, "goal-masthead-stopping.png"), animations: "disabled" });

      api.hostThreadActivity = {};
      await page.reload({ waitUntil: "networkidle" });
      await page.getByTestId("goal-vitals").waitFor({ state: "visible", timeout: 15_000 });
      assert.equal(await page.locator(".personal-channel-title h1").innerText(), title, "The stopped Goal stays selected across reload");
      assert.equal(await stateWord(page), "已停止", "Without an observed turn the Goal reads stopped");
      assert.equal(await page.getByTestId("goal-vitals").locator(".goal-stagebar-wait").count(), 0, "A stopped Goal with complete empty attention names no waiting decision");
      assert.match(await page.locator(".goal-overview-attention").innerText(), /没有等你决定的事|Nothing is waiting on you/, "The empty state is named once by its owner");

      await page.locator(".goal-lifecycle-trigger", { hasText: "恢复" }).click();
      assert.equal(await page.getByRole("dialog", { name: "恢复这个 Goal？" }).count(), 0, "Resume uses the owner's reviewed preview, not a second confirmation");
      await page.getByRole("button", { name: "恢复 Goal", exact: true }).click();
      await page.locator(".goal-lifecycle-trigger", { hasText: "停止" }).waitFor({ timeout: 10_000 });
      assert.equal(api.goalActivationStates.get(goalId), "active");
      await page.getByRole("button", { name: /关闭详情/ }).click();
      await page.locator("[data-context-drawer]").waitFor({ state: "detached" });
      await page.locator(".personal-goal-tabs button", { hasText: "概览" }).click();

      await page.setViewportSize({ width: 1024, height: 768 });
      await page.screenshot({ path: resolve(outputDir, "goal-masthead-1024.png"), animations: "disabled" });
      await page.setViewportSize({ width: 390, height: 844 });
      await page.screenshot({ path: resolve(outputDir, "goal-masthead-phone.png"), animations: "disabled" });
      const overflow = await page.getByTestId("goal-vitals").evaluate((strip) => strip.scrollWidth - strip.clientWidth);
      assert.ok(overflow <= 1, "The phone grid fits without horizontal scrolling");
      await page.locator(".personal-goal-tabs button", { hasText: "对话" }).click();
      await page.locator('.personal-goal-tabs button[aria-current="page"]', { hasText: "对话" }).waitFor();
      assert.equal(await page.getByTestId("goal-vitals").count(), 0, "Phone reading views also omit Overview statistics");
      await page.screenshot({ path: resolve(outputDir, "goal-masthead-phone-chat.png"), animations: "disabled" });
      assert.equal(session.errors.length, 0, session.errors.join(" | "));
      coverageEntries.push(...await session.close());
    } catch (error) {
      await page.screenshot({ path: resolve(outputDir, "goal-masthead-failed.png") });
      await session.close();
      throw error;
    }

    const quiet = await openWorkspacePage(browser, url, { collectCoverage, beforeGoto: (api) => populate(api, { usage: false, hostTurn: false }) });
    try {
      await openGoal(quiet.page, title);
      assert.deepEqual(await usageVals(quiet.page), { cost: "未采集", agentTime: "未采集" }, "Absent usage names the unmeasured state, never $0.00");
      assert.equal(await bar(quiet.page).locator(".goal-stagebar-usage-value.is-unmeasured").count(), 2, "Unmeasured usage is marked as such, not rendered as a zero");
      assert.equal(await lastTime(quiet.page), "暂无观测到的活动", "A claim or Todo write is not activity");
      assert.equal(await bar(quiet.page).locator(".goal-stagebar-time").getAttribute("title"), null,
        "With no observed activity the time slot claims no owning host");
      // The bar no longer carries per-cell help; the usage group opens the usage detail instead.
      await bar(quiet.page).locator(".goal-stagebar-usage").click();
      const quietUsageTable = quiet.page.locator(".personal-goal-detail-usage");
      await quietUsageTable.waitFor({ state: "visible" });
      assert.match(await quietUsageTable.innerText(), /未采集|Not measured/, "The usage detail names the unmeasured state");
      await quiet.page.getByRole("button", { name: /关闭详情/ }).click();
      await quiet.page.locator("[data-context-drawer]").waitFor({ state: "detached" });
      assert.notEqual(await stateWord(quiet.page), "执行中");
      await quiet.page.screenshot({ path: resolve(outputDir, "goal-masthead-unmeasured.png"), animations: "disabled" });
      await quiet.page.evaluate(() => localStorage.setItem("loopx-pw-locale", "en"));
      await quiet.page.reload({ waitUntil: "networkidle" });
      await quiet.page.getByTestId("goal-vitals").waitFor({ state: "visible", timeout: 15_000 });
      const englishBar = await quiet.page.getByTestId("goal-vitals").innerText();
      assert.match(englishBar, /Plan[\s\S]*Run[\s\S]*Accept/, "The stage track keeps its order in English");
      assert.match(englishBar, /Cost[\s\S]*Agent time/, "Cost precedes Agent time and both stay labelled in English");
      assert.match(englishBar, /not measured/, "The unmeasured state is spelled out in English too");
      await quiet.page.screenshot({ path: resolve(outputDir, "goal-masthead-en.png"), animations: "disabled" });
      coverageEntries.push(...await quiet.close());
    } catch (error) {
      await quiet.page.screenshot({ path: resolve(outputDir, "goal-masthead-unmeasured-failed.png") });
      await quiet.close();
      throw error;
    }
    // The canonical producer retains 21 open decisions and bounds its overview
    // to three. Pass its payload through the shipped parser
    // and adapter, rather than manufacturing a complete three-item queue.
    const fixtureEnv = { ...process.env, LOOPX_USAGE_PING: "0" };
    if (!["LOOPX_TEST_PYTHON", "LOOPX_PYTHON_BIN", "LOOPX_PYTHON"].some(key => process.env[key])) fixtureEnv.PYTHONPATH = repoRoot;
    const native = JSON.parse(execFileSync(resolveTestPython(), [
      resolve(repoRoot, "examples/personal-workspace-browser/goal-attention-fixture.py"),
    ], { cwd: repoRoot, env: fixtureEnv, encoding: "utf8", timeout: 30_000 }));
    const nativeCase = (...args) => JSON.parse(execFileSync(resolveTestPython(), [
      resolve(repoRoot, "examples/personal-workspace-browser/goal-attention-fixture.py"), ...args,
    ], { cwd: repoRoot, env: fixtureEnv, encoding: "utf8", timeout: 30_000 }));
    const mixed = nativeCase("--mixed");
    const complete = nativeCase("--full");
    const mixedComplete = nativeCase("--mixed", "--full");
    const optionalComplete = nativeCase("--optional", "--full");
    // Exercise source -> parser -> adapter -> digest -> packaged rendering.
    // Counts are removed from a real producer payload, not a prebuilt UI model.
    for (const locale of ["zh-CN", "en"]) {
      let countMode = "unknown";
      const ui = await openWorkspacePage(browser, url, { collectCoverage,
        viewport: { width: locale === "en" ? 390 : 1440, height: 900 },
        beforeGoto: async (api, page) => {
          await page.addInitScript(value => localStorage.setItem("loopx-pw-locale", value), locale);
          populate(api, { hostTurn: false });
          const previous = api.transformStatus;
          api.transformStatus = fixture => {
            previous(fixture);
            const queue = fixture.attention_queue.items.find(item => item.goal_id === goalId);
            Object.assign(queue, structuredClone(native));
            // The surrounding demo has its own current task for this Goal;
            // isolate this case to the native producer's one work item.
            fixture.agent_management_projection.agents = fixture.agent_management_projection.agents
              .filter(agent => agent.current_todo?.goal_id !== goalId);
            for (const [summary, done, open] of [[queue.agent_todos, "done_count", "open_count"],
              [queue.project_asset.agent_todos, "done", "open"]]) {
              delete summary.advancement_done_count;
              if (countMode === "unknown") { delete summary[done]; delete summary[open]; }
              else { summary.advancement_done_count = countMode === "zero" ? 0 : 2; summary[done] = summary.advancement_done_count; summary[open] = 1; }
            }
          };
        },
      });
      try {
        if (locale === "en") await ui.page.locator(".personal-mobile-menu").click();
        await openGoal(ui.page, title);
        for (const mode of ["unknown", "zero", "known", "unknown"]) {
          countMode = mode;
          await ui.page.getByRole("button", { name: /^(刷新状态|Refresh status)$/ }).click();
          const expected = mode === "unknown" ? (locale === "en" ? /^1 open$/ : /^1 项未完成$/) : new RegExp(`^${mode === "zero" ? "0/1" : "2/3"}$`);
          await bar(ui.page).locator(".goal-stagebar-work").filter({hasText: expected}).waitFor().catch(async error => {
            await ui.page.screenshot({ path: resolve(outputDir, `goal-work-count-failed-${locale}.png`), animations: "disabled" });
            throw new Error(`${locale} ${mode}: expected ${expected}; observed ${await workText(ui.page)}`, {cause: error});
          });
          if (mode === "unknown") {
            const unknownWork = await workText(ui.page);
            assert.match(unknownWork, locale === "en" ? /^\d+ open$/ : /^\d+ 项未完成$/, "An unknown total shows the open count, never a ratio");
            assert.doesNotMatch(unknownWork, /\d+\s*\/\s*\d+/, "An unknown total cannot imply a completion proportion");
            await ui.page.screenshot({ path: resolve(outputDir, `goal-work-unknown-${locale}.png`), animations: "disabled" });
          }
        }
        assert.equal(ui.api.turnRequests.length, 0);
        assert.equal(ui.api.actionApplies.length, 0);
        assert.equal(ui.errors.length, 0, ui.errors.join(" | "));
      } finally { coverageEntries.push(...await ui.close()); }
    }
    for (const locale of ["zh-CN", "en"]) for (const mode of ["mixed", "bounded", "optional", "complete", "mixed-complete", "empty", "unknown"]) {
      const ui = await openWorkspacePage(browser, url, {
        collectCoverage,
        viewport: { width: locale === "en" ? 390 : 1512, height: 982 },
        beforeGoto: async (api, page) => {
          await page.addInitScript(value => localStorage.setItem("loopx-pw-locale", value), locale);
          populate(api);
          const previous = api.transformStatus;
          api.transformStatus = (fixture) => {
            previous(fixture);
            const queue = fixture.attention_queue.items.find(item => item.goal_id === goalId);
            const source = mode === "mixed" ? mixed : mode === "complete" ? complete
              : mode === "mixed-complete" ? mixedComplete : mode === "optional" ? optionalComplete : native;
            Object.assign(queue, structuredClone(source));
            if (mode === "empty") {
              queue.user_todos.items = [];
              queue.project_asset.user_todos.items = [];
            }
            if (mode === "unknown") {
              delete queue.user_todos.open_count;
              delete queue.project_asset.user_todos.open;
            }
          };
        },
      });
      try {
        if (locale === "en") await ui.page.locator(".personal-mobile-menu").click();
        await openGoal(ui.page, title);
        const expected = mode === "complete" ? "21" : mode === "mixed-complete" ? "1" : mode === "optional" ? "0" : "—";
        const partial = !["complete", "mixed-complete", "optional"].includes(mode);
        // The strip's per-cell coverage help is gone: vitals.needsYouOldest,
        // vitals.workPartial and vitals.workTotalUnknown have no renderer any more.
        // The qualified-total rule those tooltips explained is asserted on the rail.
        if (expected === "0") assert.equal(await bar(ui.page).locator(".goal-stagebar-wait").count(), 0, "A zero decision count names no waiting decision");
        await ui.page.getByRole("navigation", { name: /^(Goal 视图|Goal view)$/ }).getByRole("button", { name: /^(概览|Overview)$/ }).click();
        const rail = ui.page.locator(".goal-overview-attention");
        if (mode === "optional") assert.match(await rail.locator("header small").innerText(), locale === "en" ? /21 optional/ : /21 项可选/);
        if (mode === "unknown") assert.doesNotMatch(await rail.innerText(), /Nothing waiting|暂无等待/);
        if (mode === "empty") {
          // The stage bar keeps a numbered attention region; an empty one must
          // explain itself and invent no decision card.
          assert.equal(await rail.count(), 1, "The attention region is part of the Goal document");
          assert.equal(await rail.locator("li").count(), 0, "No unlisted row is invented as a decision card");
          // Emptying the listed rows does not prove zero: with incomplete counts the
          // region must disclose unknown rather than claim a clean empty state.
          const emptyCopy = await rail.locator(".goal-stage-empty").innerText();
          assert.match(emptyCopy, locale === "en" ? /unknown/ : /无法确认/, "The region discloses that waiting items are unknown");
          assert.doesNotMatch(emptyCopy.trim(), /^(没有等你决定的事|Nothing is waiting on you)$/, "Partial coverage is never claimed as empty");
        } else {
          assert.equal(await rail.locator(".goal-overview-count").textContent(), expected, "The rail carries the qualified count, never a total it cannot vouch for");
          if (partial) assert.doesNotMatch(await rail.innerText(), locale === "en" ? /oldest unknown/ : /最早时间未知/, "The same coverage explanation is not repeated under the decision list");
          await rail.locator(".goal-overview-more").waitFor({ state: "visible" });
        }
        await ui.page.screenshot({ path: resolve(outputDir, `goal-attention-${mode}-${locale}.png`), animations: "disabled" });
        assert.equal(ui.errors.length, 0, ui.errors.join(" | "));
        assert.equal(ui.api.turnRequests.length, 0, "Reading attention never starts a Turn");
        assert.equal(ui.api.actionApplies.length, 0, "Reading attention never changes owning state");
      } finally { coverageEntries.push(...await ui.close()); }
    }
    return { coverageEntries, note: "masthead, stop readback, unmeasured usage and real-producer bounded attention" };
  },
};
