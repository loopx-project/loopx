import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {once} from "node:events";
import {createInterface} from "node:readline";
import {resolve} from "node:path";
import {repoRoot, outputDir} from "./fixture.mjs";
import {openWorkspacePage} from "./scenario-context.mjs";
import {resolveTestPython} from "../../scripts/test-python.mjs";

async function verifyManagedTurnObservers({page, origin, sessions, panel, chooser, composer, control}) {
  const {session_id: sessionId} = await control("observer-session");
  const before = (await control("inspect")).calls_after_setup.filter(row => row.method === "turn/start").length;
  const requests = [], live = new Set();
  const onRequest = request => {
    if (new URL(request.url()).pathname.startsWith(`/api/chat/sessions/${sessionId}/turns/`)
      && new URL(request.url()).pathname.endsWith("/events")) {requests.push(request); live.add(request);}
  };
  const onSettled = request => live.delete(request);
  page.on("request", onRequest);
  page.on("requestfinished", onSettled);
  page.on("requestfailed", onSettled);
  const pending = panel.locator(".personal-message-pending");
  const turnPath = turnId => `/api/chat/sessions/${sessionId}/turns/${turnId}/events`;
  async function afterHistoryPoll(turnId) {
    // Observe a real history refresh while the synthetic native Turn remains
    // active, then cross another five-second poll. No model timing decides it.
    await page.waitForResponse(async response => response.url() === `${origin}/api/chat/sessions/${sessionId}`
      && (await response.json()).session?.active_turn_id === turnId, {timeout: 12_000});
    await page.waitForTimeout(5500);
  }
  async function assertSingleObserver(turnId, expectedStarts) {
    assert.equal(await pending.count(), 1, "One native Turn has one pending reply across history polling");
    assert.equal(requests.filter(request => new URL(request.url()).pathname === turnPath(turnId)).length, 1,
      "History polling reuses the original event observer");
    assert.equal([...live].filter(request => new URL(request.url()).pathname === turnPath(turnId)).length, 1,
      "Exactly one live event stream owns the active Turn");
    assert.equal((await control("inspect")).calls_after_setup.filter(row => row.method === "turn/start").length,
      before + expectedStarts, "Observing a Turn never starts another native execution");
  }
  async function interrupt(turnId) {
    const receipt = page.waitForResponse(response => response.request().method() === "POST"
      && response.url() === `${origin}/api/chat/sessions/${sessionId}/turns/${turnId}/interrupt`);
    await panel.getByRole("button", {name: "中断本轮", exact: true}).click();
    assert.equal((await (await receipt).json()).status, "interrupted");
    await pending.waitFor({state: "hidden"});
  }
  try {
    await page.setViewportSize({width: 1440, height: 900});
    await page.reload({waitUntil: "domcontentloaded"});
    await chooser.selectOption(`managed:${sessionId}`);
    await panel.getByText("Review the release checklist before the next handoff.", {exact: true}).waitFor();
    const accepted = page.waitForResponse(response => response.request().method() === "POST"
      && response.url() === `${origin}/api/chat/sessions/${sessionId}/turns`);
    await composer.fill("Review the remaining release steps; wait for interrupt.");
    await page.waitForFunction(() => !document.querySelector('button[aria-label="发送"]')?.disabled);
    await composer.press("Enter");
    const ownTurn = (await (await accepted).json()).turn_id;
    await afterHistoryPoll(ownTurn);
    await page.screenshot({path: resolve(outputDir, "managed-observer-desktop.png")});
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({path: resolve(outputDir, "managed-observer-narrow.png")});
    await assertSingleObserver(ownTurn, 1);
    await interrupt(ownTurn);

    // Start through the same real ingress while another Session is selected.
    // Entering the original must recover it without a composer-owned observer.
    await chooser.selectOption(`managed:${sessions[0]}`);
    await panel.getByText("Original managed question 1", {exact: true}).waitFor();
    const submitted = await page.request.post(`${origin}/api/chat/sessions/${sessionId}/turns`, {
      data: {message: "Continue the release verification; wait for interrupt.", client_turn_id: "managed-observer-external"},
    });
    assert.equal(submitted.ok(), true);
    const externalTurn = (await submitted.json()).turn_id;
    const recovered = afterHistoryPoll(externalTurn);
    await chooser.selectOption(`managed:${sessionId}`);
    await recovered;
    await assertSingleObserver(externalTurn, 2);
    await interrupt(externalTurn);
  } finally {
    page.off("request", onRequest);
    page.off("requestfinished", onSettled);
    page.off("requestfailed", onSettled);
  }
}

export const managedAgentSessionsScenario = {
  id: "managed-agent-sessions",
  async run({browser}) {
    const child = spawn(resolveTestPython({repoRoot}), ["-u", "examples/personal-workspace-browser/managed_agent_fixture.py"],
      {cwd: repoRoot, stdio: ["pipe", "pipe", "pipe"]});
    const lines = createInterface({input: child.stdout})[Symbol.asyncIterator]();
    let stderr = "", context;
    child.stderr.on("data", chunk => {stderr += chunk;});
    async function next() {
      let timer;
      try {
        for (;;) {
          const line = await Promise.race([lines.next(), new Promise((_, reject) => {timer = setTimeout(() => reject(new Error(stderr || "Managed fixture timed out")), 20_000);})]);
          clearTimeout(timer);
          if (line.done) throw new Error(stderr || "Managed fixture closed");
          if (line.value.startsWith("{")) return JSON.parse(line.value);
        }
      } finally {clearTimeout(timer);}
    }
    async function control(command) {child.stdin.write(`${command}\n`); return next();}
    try {
      const {origin, sessions, threads, legacy} = await next();
      assert.equal(new Set(threads).size, 3, "Each managed Session retains an independent original native thread");
      const url = `${origin}/chat/?goalId=product-release&view=conversation&workAgent=worker&statusUrl=%2Fstatus.json`;
      context = await openWorkspacePage(browser, url, {gotoWaitUntil: "domcontentloaded", viewport: {width: 1440, height: 900},
        apiOptions: {fixturePort: new URL(origin).port}, beforeGoto: async (api, page) => {
          api.transformStatus = fixture => {
            const goal = fixture.run_history.goals.find(row => row.id === "product-release");
            goal.coordination = {registered_agents: ["worker", "peer"], thread_agent_bindings: []};
          };
          await page.route("**/api/chat/sessions**", route => route.continue());
          await page.route("**/api/chat/capabilities", route => route.continue());
        }});
      const {page} = context;
      const panel = page.getByRole("region", {name: "Agent 工作会话", exact: true});
      const chooser = panel.getByRole("combobox", {name: "工作会话", exact: true});
      const composer = page.getByRole("textbox", {name: "向 LoopX 发送消息", exact: true});
      await chooser.selectOption(`managed:${sessions[0]}`);
      await panel.getByText("Original managed question 1", {exact: true}).waitFor();
      await composer.waitFor({state: "visible"});
      assert.equal(await page.getByRole("textbox").filter({visible: true}).count(), 1);
      assert.equal(await panel.getByText("Unmapped ordinary Goal history stays separate.", {exact: true}).count(), 0);
      assert.ok(!(await control("inspect")).calls_after_setup.some(row => ["thread/start", "thread/resume", "turn/start"].includes(row.method)), "Browsing neither opens an adapter nor executes a model");
      await composer.fill("Retained draft for first Session");
      await chooser.selectOption(`managed:${sessions[1]}`);
      await panel.getByText("Original managed question 2", {exact: true}).waitFor();
      assert.equal(await composer.inputValue(), "");
      assert.equal(await panel.getByText("Original managed question 1", {exact: true}).count(), 0);
      await composer.fill("Retained draft for second Session");
      await page.reload({waitUntil: "domcontentloaded"});
      await panel.getByText("Original managed question 2", {exact: true}).waitFor();
      await page.waitForFunction(() => document.querySelector('[aria-label="向 LoopX 发送消息"]')?.value === "Retained draft for second Session");
      await chooser.selectOption(`managed:${sessions[0]}`);
      await panel.getByText("Original managed question 1", {exact: true}).waitFor();
      assert.equal(await composer.inputValue(), "Retained draft for first Session");
      await chooser.selectOption(`managed:${sessions[1]}`);
      await panel.getByText("Original managed question 2", {exact: true}).waitFor();
      // A late snapshot for a previously selected Session must not cross the
      // exact route, replace the current transcript or overwrite its draft.
      await control("append");
      let observed, release;
      const started = new Promise(resolve => {observed = resolve;});
      const held = new Promise(resolve => {release = resolve;});
      const delay = async route => {const response = await route.fetch(); observed(); await held; await route.fulfill({response});};
      await page.route(`**/api/chat/sessions/${sessions[0]}`, delay);
      await chooser.selectOption(`managed:${sessions[0]}`);
      await Promise.race([started, new Promise((_, reject) => setTimeout(() => reject(new Error("Changed original Session snapshot was not requested")), 8000))]);
      await chooser.selectOption(`managed:${sessions[1]}`);
      await panel.getByText("Original managed question 2", {exact: true}).waitFor();
      release();
      await page.unroute(`**/api/chat/sessions/${sessions[0]}`, delay);
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      assert.equal(await panel.getByText("Original managed question 1", {exact: true}).count(), 0);
      assert.equal(await composer.inputValue(), "Retained draft for second Session");
      await composer.fill("Owner correction only to second Session");
      await page.waitForFunction(() => !document.querySelector('button[aria-label="发送"]')?.disabled);
      await composer.press("Enter");
      await panel.getByText("Runtime response.", {exact: true}).waitFor().catch(async error => {
        console.log(JSON.stringify({diagnostic: await control("inspect"), body: await page.locator("body").innerText(), ui: await composer.evaluate(node => ({value: node.value, disabled: node.disabled, readOnly: node.readOnly, form: node.closest("form")?.outerHTML.slice(-1000)})), errors: context.errors, stderr}));
        throw error;
      });
      const accepted = await control("inspect");
      assert.equal(accepted.sessions, 4, "Continuing uses the original Session, without replacements");
      assert.ok(!accepted.messages[sessions[0]].some(row => row.text === "Owner correction only to second Session"));
      assert.equal(accepted.messages[sessions[1]].filter(row => row.text === "Owner correction only to second Session").length, 1);
      assert.equal(accepted.calls_after_setup.filter(row => row.method === "turn/start").length, 1);
      assert.ok(!accepted.calls_after_setup.some(row => row.method === "thread/start"));
      const scroller = page.locator(".personal-channel-scroll");
      await scroller.hover(); await page.mouse.wheel(0, -3000);
      await chooser.waitFor({state: "visible"});
      await page.screenshot({path: resolve(outputDir, "managed-agent-desktop.png")});
      await page.setViewportSize({width: 390, height: 844});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      assert.equal(await panel.evaluate(node => node.scrollWidth > node.clientWidth), false);
      await scroller.hover(); await page.mouse.wheel(0, -3000);
      await chooser.waitFor({state: "visible"});
      await page.screenshot({path: resolve(outputDir, "managed-agent-narrow.png")});
      await scroller.hover(); await page.mouse.wheel(0, 600);
      await page.waitForFunction(() => document.querySelector(".personal-channel-scroll").scrollTop > 0);
      assert.notEqual(await scroller.evaluate(node => getComputedStyle(node).overflowY), "hidden", "Original managed conversation scrolls naturally");
      await composer.fill("Retained while original registration is unavailable");
      await page.waitForFunction(() => !document.querySelector('button[aria-label="发送"]')?.disabled);
      await control("revoke");
      await page.waitForFunction(() => document.querySelector('button[aria-label="发送"]')?.disabled === true, null, {timeout: 12_000});
      assert.equal(await page.getByRole("button", {name: "发送", exact: true}).isDisabled(), true, "Revocation holds the original composer");
      assert.equal(await composer.inputValue(), "Retained while original registration is unavailable");
      const final = await control("inspect");
      assert.equal(final.calls_after_setup.filter(row => row.method === "turn/start").length, 1);
      assert.ok(final.messages[sessions[0]].length === 3 && legacy);
      await control("recover");
      const directory = await (await page.request.get(`${origin}/api/chat/sessions?goal_id=product-release&managed_agent_id=worker`)).json();
      for (const invalid of [{ok: true, sessions: [{session_id: legacy, agent_id: "codex"}]}, {...directory, ok: false}, {...directory, schema_version: "unknown"}]) {
        const ignoredSelector = route => route.fulfill({json: invalid});
        await page.route("**/api/chat/sessions?*managed_agent_id=*", ignoredSelector);
        await page.reload({waitUntil: "domcontentloaded"});
        await panel.getByRole("alert").waitFor();
        assert.equal(await panel.getByText("Unmapped ordinary Goal history stays separate.", {exact: true}).count(), 0);
        assert.equal(await page.getByRole("button", {name: "发送", exact: true}).isDisabled(), true);
        assert.equal((await control("inspect")).calls_after_setup.filter(row => row.method === "turn/start").length, 1);
        await page.unroute("**/api/chat/sessions?*managed_agent_id=*", ignoredSelector);
      }
      const errorsBeforeObservation = context.errors.length;
      await verifyManagedTurnObservers({page, origin, sessions, panel, chooser, composer, control});
      assert.deepEqual(context.errors.slice(errorsBeforeObservation), [], "Turn observation adds no browser errors after source recovery");
      return {coverageEntries: await context.close(), note: "Real SQLite/Core/Chat and synthetic native provider: exact managed Session discovery, original-thread continuation, scope-specific drafts and refresh, no-model browsing and revocation; one live observer and pending reply across real history polling, external-Turn recovery and exact interruption."};
    } finally {
      await context?.close().catch(() => {});
      child.stdin.end("stop\n");
      await Promise.race([once(child, "exit"), new Promise(resolve => setTimeout(resolve, 2000))]);
      if (child.exitCode === null) child.kill("SIGTERM");
    }
  },
};
