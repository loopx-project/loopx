import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {resolve} from "node:path";
import {repoRoot, outputDir} from "./fixture.mjs";
import {openWorkspacePage} from "./scenario-context.mjs";
import {resolveTestPython} from "../../scripts/test-python.mjs";
import {withManagedFixture} from "./managed-fixture-process.mjs";

async function verifyQueuedEditor({page, origin, sessionId, turnId, composer, control}) {
  const nativeStarts = (await control("inspect")).calls_after_setup.filter(call => call.method === "turn/start").length;
  const source = "# Source note\nPreserve the original attachment.\n";
  const attachment = {id: "queue-source", name: "source.md", mime_type: "text/markdown",
    size: Buffer.byteLength(source), data_url: `data:text/markdown;base64,${Buffer.from(source).toString("base64")}`};
  const path = `/api/chat/sessions/${sessionId}`;
  const submitted = await page.request.post(`${origin}${path}/turns`, {
    data: {message: "Read the source before the next handoff.", attachments: [attachment],
      client_turn_id: "managed-queue-editor", delivery: "queue"},
  });
  assert.equal(submitted.status(), 202);
  const queuedId = (await submitted.json()).turn_id;
  const inputPath = `${path}/queue/${queuedId}/input`;
  const posts = [];
  const observe = request => {if (request.method() === "POST" && new URL(request.url()).pathname.startsWith(path)) posts.push(request.url());};
  page.on("request", observe);
  try {
    const row = page.locator(".personal-conversation-queue li");
    const menu = row.getByRole("button", {name: "更多操作：待处理消息 1", exact: true});
    await menu.click();
    await row.getByRole("button", {name: "编辑", exact: true}).click();
    const editor = row.getByRole("textbox", {name: "编辑待处理消息 1", exact: true});
    const draft = "先核对原始材料，再继续下一步。\n保留这份独立的队列修改草稿和原附件。";
    await editor.fill(draft);
    await composer.fill("另一份尚未发送的主输入框草稿");
    await menu.click();
    await menu.dispatchEvent("keydown", {key: "Escape", isComposing: true});
    assert.equal(await menu.getAttribute("aria-expanded"), "true", "IME Escape does not close the menu or discard the edit");
    await page.keyboard.press("Escape");
    assert.equal(await menu.getAttribute("aria-expanded"), "false");
    assert.equal(await editor.inputValue(), draft);
    assert.equal(await menu.evaluate(node => node === document.activeElement), true);
    // Dismissing a menu is independent of storage availability. It must never
    // call the editor's destructive discard path, even if that path would fail.
    await page.evaluate(() => {window.queueRemoveItem = Storage.prototype.removeItem;
      Storage.prototype.removeItem = function() {throw new Error("isolated storage failure");};});
    try {
      await menu.click(); await page.keyboard.press("Escape");
      assert.equal(await menu.getAttribute("aria-expanded"), "false");
      assert.equal(await editor.inputValue(), draft);
      assert.equal(await row.getByRole("alert").count(), 0);
    } finally {await page.evaluate(() => {Storage.prototype.removeItem = window.queueRemoveItem; delete window.queueRemoveItem;});}
    assert.deepEqual(posts, [], "Menu dismissal neither edits, cancels, steers nor sends a message");
    await page.screenshot({path: resolve(outputDir, "queue-editor-native-desktop.png")});
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({path: resolve(outputDir, "queue-editor-native-narrow.png")});
    await page.reload({waitUntil: "domcontentloaded"});
    await editor.waitFor();
    assert.equal(await editor.inputValue(), draft, "Reload retains the edit under its original queued Turn");
    assert.equal(await composer.inputValue(), "另一份尚未发送的主输入框草稿");
    const before = await (await page.request.get(`${origin}${path}/queue`)).json();
    assert.equal(before.active_turn_id, turnId);
    assert.deepEqual(before.turns.map(turn => [turn.turn_id, turn.input_revision, turn.attachment_count]), [[queuedId, 0, 1]]);
    const saved = page.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === inputPath);
    await editor.press("Meta+Enter");
    assert.equal((await (await saved).json()).input_revision, 1);
    await editor.waitFor({state: "hidden"});
    const history = await (await page.request.get(`${origin}${path}?turn_id=${queuedId}&message_limit=200`)).json();
    const input = history.messages.find(message => message.role === "user" && message.turn_id === queuedId);
    assert.equal(input.text, draft);
    assert.deepEqual(input.attachments, [attachment]);
    await menu.click();
    await row.getByRole("button", {name: "编辑", exact: true}).click();
    await editor.fill("明确放弃的修改");
    await editor.press("Escape");
    await editor.waitFor({state: "hidden"});
    assert.equal(posts.length, 1, "Escape with no open menu still discards only the local edit");
    await menu.click();
    const cancelled = page.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === inputPath);
    await row.getByRole("button", {name: "取消排队", exact: true}).click();
    assert.equal((await (await cancelled).json()).input_revision, 2);
    await row.waitFor({state: "hidden"});
    const after = await (await page.request.get(`${origin}${path}/queue`)).json();
    assert.equal(after.active_turn_id, turnId);
    assert.deepEqual(after.turns, []);
    assert.equal(posts.length, 2, "Only explicit save and cancel mutate the original queued input");
    assert.equal((await control("inspect")).calls_after_setup.filter(call => call.method === "turn/start").length, nativeStarts,
      "Queue editing adds no native execution");
  } finally {page.off("request", observe);}
}

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
    await page.waitForResponse(async response => new URL(response.url()).origin === origin
      && new URL(response.url()).pathname === `/api/chat/sessions/${sessionId}`
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
    const admissionPath = `/api/chat/sessions/${sessionId}/turns`;
    let releaseAdmission, admitted;
    const heldAdmission = new Promise(resolve => {releaseAdmission = resolve;});
    const admissionReady = new Promise(resolve => {admitted = resolve;});
    const admissionMatch = url => url.origin === origin && url.pathname === admissionPath;
    const holdAdmission = async route => {
      if (route.request().method() !== "POST") {await route.fallback(); return;}
      const response = await route.fetch();
      admitted((await response.json()).turn_id);
      await heldAdmission;
      await route.fulfill({response});
    };
    await page.route(admissionMatch, holdAdmission);
    let ownTurn, admissionTimeout;
    try {
      const accepted = page.waitForResponse(response => response.request().method() === "POST"
        && response.url() === origin + admissionPath);
      await composer.fill("Review the remaining release steps; wait for interrupt.");
      await page.waitForFunction(() => !document.querySelector('button[aria-label="发送"]')?.disabled);
      await composer.press("Enter");
      ownTurn = await Promise.race([admissionReady, new Promise((_, reject) => {
        admissionTimeout = setTimeout(() => reject(new Error("The original admission did not reach its owning backend")), 12_000);
      })]);
      clearTimeout(admissionTimeout);
      // History can discover this active native Turn before the original ACK.
      // Its composer already owns observation: recovery must not add a stream.
      await afterHistoryPoll(ownTurn);
      assert.equal(requests.length, 0, "An original admission in flight must not acquire a recovery observer");
      assert.equal(await pending.count(), 1, "The original pending reply survives active history discovery");
      releaseAdmission();
      assert.equal((await (await accepted).json()).turn_id, ownTurn);
    } finally {
      clearTimeout(admissionTimeout);
      releaseAdmission();
      await page.unroute(admissionMatch, holdAdmission);
    }
    await afterHistoryPoll(ownTurn);
    await page.screenshot({path: resolve(outputDir, "managed-observer-desktop.png")});
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({path: resolve(outputDir, "managed-observer-narrow.png")});
    await assertSingleObserver(ownTurn, 1);
    await page.setViewportSize({width: 1440, height: 900});
    await verifyQueuedEditor({page, origin, sessionId, turnId: ownTurn, composer, control});
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
    return withManagedFixture(child, async fixture => {
      const {next, control} = fixture;
      let context;
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
        // Production history hydration adds pagination query parameters. Delay
        // this exact original Session's snapshot, with or without those cursors.
        const delayedSnapshot = url => url.origin === origin && url.pathname === `/api/chat/sessions/${sessions[0]}`;
        await page.route(delayedSnapshot, delay);
        await chooser.selectOption(`managed:${sessions[0]}`);
        await Promise.race([started, new Promise((_, reject) => setTimeout(() => reject(new Error("Changed original Session snapshot was not requested")), 8000))]);
        await chooser.selectOption(`managed:${sessions[1]}`);
        await panel.getByText("Original managed question 2", {exact: true}).waitFor();
        release();
        await page.unroute(delayedSnapshot, delay);
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        assert.equal(await panel.getByText("Original managed question 1", {exact: true}).count(), 0);
        assert.equal(await composer.inputValue(), "Retained draft for second Session");
        // The owning backend may finish and publish history before its original
        // admission response reaches the composer. Hold that real response,
        // rather than forging a second message or loosening the reply selector.
        let releaseAdmission, releaseSnapshots;
        const admissionHeld = new Promise(resolve => {releaseAdmission = resolve;});
        const snapshotsHeld = new Promise(resolve => {releaseSnapshots = resolve;});
        let holdSnapshots = false;
        const sessionPath = "/api/chat/sessions/" + sessions[1];
        const snapshotMatch = url => url.origin === origin && url.pathname === sessionPath;
        const admissionMatch = url => url.origin === origin && url.pathname === sessionPath + "/turns";
        const holdSnapshot = async route => {
          const response = await route.fetch();
          if (holdSnapshots) await snapshotsHeld;
          await route.fulfill({response});
        };
        const holdAdmission = async route => {
          if (route.request().method() !== "POST") {await route.fallback(); return;}
          const response = await route.fetch();
          await admissionHeld;
          await route.fulfill({response});
        };
        await page.route(snapshotMatch, holdSnapshot);
        await page.route(admissionMatch, holdAdmission);
        try {
          await composer.fill("Owner correction only to second Session");
          await page.waitForFunction(() => !document.querySelector('button[aria-label="发送"]')?.disabled);
          await composer.press("Enter");
          await panel.getByText("Runtime response.", {exact: true}).waitFor({timeout: 12_000});
          const streamResponse = page.waitForResponse(response => new URL(response.url()).origin === origin
            && new URL(response.url()).pathname.startsWith(sessionPath + "/turns/")
            && new URL(response.url()).pathname.endsWith("/events"), {timeout: 12_000});
          // Keep the canonical-identity follow-up read outstanding while the
          // original stream completes. Both legal orderings must show one reply.
          holdSnapshots = true;
          releaseAdmission();
          assert.equal((await streamResponse).status(), 200);
          await panel.locator(".personal-message-pending").waitFor({state: "hidden"});
          await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
          await panel.getByText("Runtime response.", {exact: true}).waitFor();
          assert.equal(await panel.getByText("Runtime response.", {exact: true}).count(), 1,
            "History before the original admission ACK must not duplicate the same native reply");
          assert.equal(await panel.locator(".personal-message").count(), (await control("inspect")).messages[sessions[1]].length,
            "The settled UI contains exactly the owning Session's saved messages");
        } catch (error) {
          console.log(JSON.stringify({diagnostic: await control("inspect"), body: await page.locator("body").innerText(), errors: context.errors}));
          throw error;
        } finally {
          releaseAdmission(); releaseSnapshots();
          await page.unroute(admissionMatch, holdAdmission);
          await page.unroute(snapshotMatch, holdSnapshot);
        }
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
      }
    });
  },
};
