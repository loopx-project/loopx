import { resolve } from "node:path";
import assert from "node:assert/strict";
import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

// Independent ordering oracle: input order is deliberately unrelated to tier or
// name order. Exercise both actual settings consumers, not the sort helper.
async function verifyFilteredCatalogOrder(page) {
  const snapshots = await page.evaluate(async () => ({
    machine: await (await fetch('/api/chat/machine-configuration')).json(),
    goal: await (await fetch('/api/chat/goal-configuration?goal_id=product-release')).json(),
  }));
  const template = snapshots.goal.capability_catalog.capabilities.find(item => item.capability_id === 'periodic_report');
  const capability = (id, title, availability, writable = true) => ({...template,
    capability_id: id, display_name: title, description: '', available_scopes: ['machine', 'goal'], machine_namespace: id,
    availability, configuration_editor: {...template.configuration_editor, fields: [],
      editable: writable, supported_scopes: ['machine', 'goal'], writable_scopes: writable ? ['machine', 'goal'] : []},
  });
  const capabilities = [
    capability('order_retired', 'Order Retired', 'retired', false),
    capability('order_zulu', 'Order Zulu', 'supported_opt_in'),
    capability('order_experiment', 'Order Experiment', 'experimental_opt_in'),
    capability('order_readonly', 'Order Read only', 'supported_opt_in', false),
    capability('unrelated', 'Unrelated', 'supported_explicit_override'),
    capability('order_alpha', 'Order Alpha', 'supported_opt_in'),
    capability('order_priority', 'Order Priority', 'supported_explicit_override'),
  ];
  for (const [scope, snapshot] of Object.entries(snapshots)) {
    await page.route(`**/api/chat/${scope}-configuration*`, async route => {
      if (route.request().method() !== 'GET') return route.fallback();
      await route.fulfill({json: {...snapshot, capability_catalog: {...snapshot.capability_catalog, capabilities}}});
    });
  }
  await page.reload({waitUntil: 'networkidle'});
  const expected = ['Order Priority', 'Unrelated', 'Order Alpha', 'Order Zulu', 'Order Read only', 'Order Experiment', 'Order Retired'];
  for (const scope of ['Goal', 'Machine']) {
    if (scope === 'Machine') await page.getByRole('radio', {name: 'This device', exact: true}).check();
    const catalog = page.getByRole('navigation', {name: `${scope} capability catalog`, exact: true});
    const labels = () => catalog.locator('button strong').allTextContents();
    const search = page.getByRole('searchbox', {name: 'Search capabilities', exact: true});
    await catalog.getByRole('button', {name: /^Order Priority/}).waitFor();
    assert.deepEqual(await labels(), expected, `${scope}: priority overrides precede actionable, read-only, experimental and retired entries; peers sort by name`);
    await search.fill('order');
    assert.deepEqual(await labels(), expected.filter(label => label !== 'Unrelated'), `${scope}: filtering preserves tier and name order`);
    await search.fill('order zulu');
    assert.deepEqual(await labels(), ['Order Zulu'], `${scope}: every term must match`);
    await search.press('Escape');
    assert.deepEqual(await labels(), expected, `${scope}: clearing restores complete presentation order`);
  }
}

export const capabilityScopeScenario = {
  id: "capability-scope",
  async run({ browser, collectCoverage, url }) {
    const context = await openWorkspacePage(browser, url, { collectCoverage });
    const { api, page } = context;
    const reads = [];
    page.on("request", (request) => {
      const parsed = new URL(request.url());
      if (parsed.pathname === "/api/chat/goal-configuration") reads.push(parsed.searchParams.get("goal_id"));
    });
    try {
      await page.getByRole("button", { name: "设置", exact: true }).click();
      const tabs = page.locator(".personal-settings-tabs");
      await tabs.getByRole("button", { name: "能力中心", exact: true }).click();
      if (await tabs.getByRole("button", { name: "Goal 能力", exact: true }).count()) throw new Error("Duplicate capability destinations remain");
      const defaults = page.getByRole("radio", { name: "本机设置", exact: true });
      const goalScope = page.getByRole("radio", { name: "Goal 设置", exact: true });
      if (!await defaults.isChecked()) throw new Error("Global settings must start at device defaults");
      await page.getByRole("navigation", { name: "机器能力目录" }).waitFor();
      const search = page.getByRole('searchbox', {name: '搜索能力', exact: true});
      const machineCatalog = page.getByRole('navigation', {name: '机器能力目录'});
      const initialEditor = await page.locator('.personal-capability-detail h2').innerText();
      const allMachineEntries = await machineCatalog.getByRole('button').count();
      const toggleBox = await page.locator('.personal-capability-fields input[role="switch"]').first().boundingBox();
      assert(toggleBox && toggleBox.width <= 44 && toggleBox.height <= 28, 'Current capability toggles are compact, not oversized native checkboxes');
      console.log(`current capability switch: ${toggleBox.width}×${toggleBox.height} CSS pixels`);

      await search.fill('  PERIODIC_REPORT  ');
      assert.equal(await machineCatalog.getByRole('button').count(), 1);
      assert.equal(await page.locator('.personal-capability-detail h2').innerText(), initialEditor, 'Filtering does not select or replace the editor');
      await search.fill('not-a-capability-[.*]');
      assert.equal(await machineCatalog.getByRole('button').count(), 0, 'Search is literal, not a regular expression');
      await page.getByText('没有匹配的能力，请尝试其他关键词。', {exact: true}).waitFor();
      await search.press('Escape');
      assert.equal(await search.inputValue(), '');
      assert(await search.evaluate(element => element === document.activeElement));
      for (const width of [1280, 960, 390]) {
        await page.setViewportSize({width, height: width === 390 ? 844 : 820});
        await search.fill('周期');
        assert(await machineCatalog.getByRole('button').count() >= 1);
        await machineCatalog.getByRole('button', {name: '周期报告', exact: true}).click();
        assert.equal(await page.locator('.personal-capability-detail h2').innerText(), '周期报告');
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
        const box = await search.boundingBox();
        assert(box && box.width > 80 && box.height >= 40);
        await page.screenshot({path: resolve(outputDir, `capability-search-${width}.png`), animations: 'disabled'});
        await page.getByRole('button', {name: '清除能力搜索', exact: true}).click();
        assert.equal(await search.inputValue(), '');
        assert.equal(await machineCatalog.getByRole('button').count(), allMachineEntries);
        assert(await search.evaluate(element => element === document.activeElement));
      }
      await page.setViewportSize({width: 1512, height: 982});

      await tabs.getByRole("button", { name: "管家", exact: true }).click();
      await page.locator(".personal-capability-supported-scopes").filter({ hasText: "设备全局配置 · 不支持 Goal 覆盖" }).waitFor();
      await tabs.getByRole("button", { name: "能力中心", exact: true }).click();
      await page.getByRole("navigation", { name: "机器能力目录" }).waitFor();
      await page.screenshot({ path: resolve(outputDir, "capability-scope-defaults.png"), animations: "disabled" });
      await goalScope.check();
      const target = page.getByRole("combobox", { name: "目标 Goal", exact: true });
      if (await target.inputValue() !== "") throw new Error("Global entry silently chose a Goal");
      if (await page.locator(".personal-capability-detail").count()) throw new Error("An unselected Goal exposed an editor");
      await target.selectOption("product-release");
      await page.getByRole("heading", { level: 2, name: "周期报告", exact: true }).waitFor();
      await page.locator(".personal-capability-supported-scopes").filter({ hasText: "设备默认配置 · 支持 Goal 单独覆盖" }).waitFor();
      if (await page.getByRole("navigation", { name: "Goal 能力目录" }).getByRole("button", { name: /管家/ }).count()) throw new Error("Machine-only steward leaked into Goal catalog");
      const catalog = page.getByRole("navigation", { name: "Goal 能力目录" });
      await catalog.getByRole("button", { name: /飞书事件收件箱/ }).click();
      await page.locator(".personal-capability-supported-scopes").filter({ hasText: "仅单个 Goal 配置 · 无设备默认值" }).waitFor();
      await page.locator(".personal-capability-editor-status.is-read-only").waitFor();
      await catalog.getByRole("button", { name: /周期报告/ }).click();
      let releasePreview;
      let previewRequested;
      const previewGate = new Promise(resolve => { releasePreview = resolve; });
      const previewStarted = new Promise(resolve => { previewRequested = resolve; });
      const previewRoute = "**/api/chat/goal-configuration/preview";
      await page.route(previewRoute, async route => {
        previewRequested();
        await previewGate;
        await route.fallback();
      });
      await page.getByLabel(/^报告 Profile/u).fill("discarded-periodic-draft");
      const delayedPreview = page.waitForResponse(response => response.url().endsWith("/goal-configuration/preview"));
      await page.getByRole("button", { name: "预览变更", exact: true }).click();
      await previewStarted;
      await catalog.getByRole("button", { name: /飞书事件收件箱/ }).click();
      await page.locator(".personal-capability-editor-status.is-read-only").waitFor();
      releasePreview();
      await (await delayedPreview).finished();
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      assert.equal(await page.locator(".personal-capability-preview").count(), 0,
        "A late Goal capability preview cannot cross to a different capability");
      await page.unroute(previewRoute);
      await catalog.getByRole("button", { name: /周期报告/ }).click();
      assert.notEqual(await page.getByLabel(/^报告 Profile/u).inputValue(), "discarded-periodic-draft");
      await page.getByLabel(/^报告 Profile/u).fill("unsaved-scope-test");
      await search.fill('multi_subagent');
      assert.equal(await catalog.getByRole('button').count(), 1);
      assert.equal(await page.getByLabel(/^报告 Profile/u).inputValue(), 'unsaved-scope-test', 'Filtering preserves the unsaved draft');
      await search.fill('reward_memory');
      assert.equal(await catalog.getByRole('button', {name: '记忆与资料', exact: true}).count(), 1, 'A member ID finds its grouped memory entry');
      await search.fill('');

      await page.getByRole("button", { name: "预览变更", exact: true }).click();
      await page.getByText("锁定 revision 的变更预览", { exact: true }).waitFor();
      if (api.goalConfigurationRequests.at(-1)?.goal_id !== "product-release") throw new Error("Preview targeted a different Goal");
      await target.selectOption("research-monitor");
      await page.getByRole("heading", { level: 2, name: "周期报告", exact: true }).waitFor();
      if (await page.locator(".personal-capability-preview").count()) throw new Error("A preview crossed Goal boundaries");
      if (await page.getByLabel(/^报告 Profile/u).inputValue() === "unsaved-scope-test") throw new Error("Draft crossed Goal boundaries");
      await page.getByRole("button", { name: "预览变更", exact: true }).click();
      await page.getByText("锁定 revision 的变更预览", { exact: true }).waitFor();
      if (api.goalConfigurationRequests.at(-1)?.goal_id !== "research-monitor") throw new Error("Changed Goal was not used by preview");
      await catalog.getByRole("button", { name: /探索 Harness/ }).click();
      const explorationMode = page.getByRole("combobox", { name: /^探索模式/u });
      await explorationMode.selectOption("planning");
      const composition = page.getByRole("combobox", { name: /^组合策略/u });
      await composition.selectOption("explicit_only");
      await page.getByLabel(/^研究覆盖范围/u).fill("joint-scope");
      await page.getByRole("button", { name: "预览变更", exact: true }).click();
      await page.getByText("锁定 revision 的变更预览", { exact: true }).waitFor();
      const preview = api.goalConfigurationRequests.at(-1);
      if (preview?.goal_id !== "research-monitor" || preview?.configuration?.composition_mode !== "explicit_only"
        || preview?.configuration?.composition_scope_id !== "joint-scope" || preview?.configuration?.mode !== "planning") throw new Error("Composition preview lost its Goal, activation or typed scope");
      await composition.selectOption("disabled");
      if (await page.locator(".personal-capability-preview").count()) throw new Error("Composition edit retained a stale preview");
      await composition.selectOption("explicit_only");
      await page.getByRole("button", { name: "预览变更", exact: true }).click();
      await page.getByText("锁定 revision 的变更预览", { exact: true }).waitFor();
      let readbackUnavailable = true;
      await page.route("**/api/chat/goal-configuration?*", async route => {
        if (readbackUnavailable && api.goalConfigurationRequests.some(item => item.phase === "apply")) {
          await route.fulfill({status: 503, json: {ok: false, error: "Synthetic Goal configuration readback outage"}});
        } else await route.fallback();
      });
      await Promise.all([
        page.waitForResponse((response) => new URL(response.url()).pathname === "/api/chat/goal-configuration" && response.request().method() === "GET"),
        page.getByRole("button", { name: "应用此预览", exact: true }).click(),
      ]);
      const recovery = page.locator(".personal-capability-recovery");
      await recovery.getByText("配置已写入，读回尚未确认", {exact: true}).waitFor();
      assert.equal(await page.getByLabel(/^研究覆盖范围/u).inputValue(), "joint-scope",
        "A failed post-write readback keeps the current Goal draft mounted");
      const writesBeforeRetry = api.goalConfigurationRequests.filter(item => item.phase === "apply").length;
      readbackUnavailable = false;
      await recovery.getByRole("button", {name: "刷新源配置", exact: true}).click();
      await recovery.waitFor({state: "hidden"});
      assert.equal(api.goalConfigurationRequests.filter(item => item.phase === "apply").length, writesBeforeRetry,
        "Goal configuration readback recovery must not apply another write");
      await catalog.getByRole("button", { name: /探索 Harness/ }).click();
      if (await composition.inputValue() !== "explicit_only" || await page.getByLabel(/^研究覆盖范围/u).inputValue() !== "joint-scope"
        || await explorationMode.inputValue() !== "planning") throw new Error("Applied composition configuration did not read back from its Goal");
      await composition.selectOption("disabled");
      await page.getByRole("button", { name: "预览变更", exact: true }).click();
      await page.getByText("锁定 revision 的变更预览", { exact: true }).waitFor();
      await Promise.all([
        page.waitForResponse((response) => new URL(response.url()).pathname === "/api/chat/goal-configuration" && response.request().method() === "GET"),
        page.getByRole("button", { name: "应用此预览", exact: true }).click(),
      ]);
      await catalog.getByRole("button", { name: /探索 Harness/ }).click();
      if (await composition.inputValue() !== "disabled" || await page.getByLabel(/^研究覆盖范围/u).inputValue() !== "joint-scope"
        || await explorationMode.inputValue() !== "planning") throw new Error("Disabling composition changed its Goal activation or scope");
      const applied = api.goalConfigurationRequests.filter((request) => request.phase === "apply");
      if (applied.length !== 2 || applied.some((request) => request.goal_id !== "research-monitor"
        || request.capability_id !== "explore_harness" || request.expected_plan_revision !== "sha256:goal-plan-explore_harness")
        || applied[0].configuration.composition_mode !== "explicit_only"
        || applied[1].configuration.composition_mode !== "disabled") throw new Error("Composition apply lost its locked revision or target");
      await page.screenshot({ path: resolve(outputDir, "capability-composition-desktop.png"), animations: "disabled" });
      await page.setViewportSize({ width: 390, height: 844 });
      await page.getByLabel(/^研究覆盖范围/u).scrollIntoViewIfNeeded();
      await page.getByLabel(/^研究覆盖范围/u).focus();
      await page.screenshot({ path: resolve(outputDir, "capability-composition-mobile.png"), animations: "disabled" });
      if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)) throw new Error("Composition controls overflow the narrow viewport");
      await page.setViewportSize({ width: 1512, height: 982 });
      // A failed sibling binding must not make an available private route look empty.
      const memory = {enabled: true, binding_status: "unbound", effective_available: true,
        registered_agents: ["reviewer", "writer"], enabled_agents: ["reviewer"], agent_bindings: [
          {agent_id: "writer", binding_source: "agent", enabled: true, available: true, status: "available"},
          {agent_id: "reviewer", binding_source: "goal", enabled: true, available: false, status: "config_invalid"},
        ]};
      api.goalMemoryConfigurations.set("research-monitor", memory);
      await catalog.getByRole("button", {name: "记忆与资料", exact: true}).click();
      const reloadMemory = () => Promise.all([
        page.waitForResponse(response => new URL(response.url()).pathname === "/api/chat/goal-configuration" && response.request().method() === "GET"),
        page.getByRole("button", {name: "重新读取配置", exact: true}).click(),
      ]);
      await reloadMemory();
      await page.getByRole("tab", {name: "经验学习", exact: true}).click();
      await page.getByRole("combobox", {name: "经验作用范围", exact: true}).selectOption("writer");
      const memoryReadback = page.getByRole("region", {name: "Agent 记忆绑定读回", exact: true});
      await memoryReadback.getByText(/独立绑定.*可用/u).waitFor();
      await memoryReadback.scrollIntoViewIfNeeded();
      if (await page.locator(".personal-memory-unbound").count()) throw new Error("An available Agent route is contradicted by a Goal-wide empty claim");
      await page.screenshot({path: resolve(outputDir, "capability-memory-available-desktop.png"), animations: "disabled"});
      await page.setViewportSize({width: 390, height: 844});
      await catalog.getByRole("button", {name: "记忆与资料", exact: true}).scrollIntoViewIfNeeded();
      await memoryReadback.scrollIntoViewIfNeeded();
      await page.screenshot({path: resolve(outputDir, "capability-memory-available-mobile.png"), animations: "disabled"});
      if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)) throw new Error("Memory scope overflows the narrow viewport");
      await page.setViewportSize({width: 1512, height: 982});
      api.goalMemoryConfigurations.set("research-monitor", {...memory, enabled: false, effective_available: false,
        agent_bindings: memory.agent_bindings.map(row => ({...row, available: false, enabled: false, status: "disabled"}))});
      await reloadMemory();
      await page.locator(".personal-memory-unbound").waitFor({state: "visible"});
      api.goalMemoryConfigurations.set("research-monitor", {...memory, effective_available: undefined, agent_bindings: []});
      await reloadMemory();
      await page.locator(".personal-memory-unbound").waitFor({state: "hidden"});
      await defaults.check();
      await page.getByRole("navigation", { name: "机器能力目录" }).waitFor();
      if (await page.locator(".personal-capability-preview").count()) throw new Error("A Goal preview crossed into device defaults");
      await goalScope.check();
      if (await target.inputValue() !== "") throw new Error("Returning to Goal scope must not restore an implicit edit target");
      await target.selectOption("research-monitor");
      await page.getByRole("heading", { level: 2, name: "周期报告", exact: true }).waitFor();
      if (await page.locator(".personal-capability-preview").count()) throw new Error("Returning revived a discarded preview");
      if (!reads.includes("product-release") || !reads.includes("research-monitor")) throw new Error("Target changes did not read their own configuration");
      if (api.goalConfigurationRequests.filter((r) => r.phase === "apply").length !== 2 || api.machineConfigurationRequests.some((r) => r.phase === "apply")) throw new Error("Navigation wrote configuration");
      await page.screenshot({ path: resolve(outputDir, "capability-scope-goal.png"), animations: "disabled" });
      await page.setViewportSize({ width: 390, height: 844 });
      await target.focus();
      await page.screenshot({ path: resolve(outputDir, "capability-scope-mobile.png"), animations: "disabled" });
      if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)) throw new Error("Scope controls overflow the narrow viewport");
      await page.setViewportSize({ width: 1512, height: 982 });
      await page.getByRole("button", { name: "返回工作区", exact: true }).click();
      await page.locator(".personal-goal-link", { hasText: "Product Release" }).click();
      await page.getByRole("button", { name: "Goal 设置", exact: true }).click();
      if (!await goalScope.isChecked() || await target.inputValue() !== "product-release") throw new Error("Goal settings entry lost its target");
      await page.evaluate(() => localStorage.setItem('loopx-pw-locale', 'en'));
      await page.reload({waitUntil: 'networkidle'});
      const englishSearch = page.getByRole('searchbox', {name: 'Search capabilities', exact: true});
      await englishSearch.fill('periodic reports');
      const englishCatalog = page.getByRole('navigation', {name: 'Goal capability catalog'});
      await englishCatalog.getByRole('button', {name: /^Periodic reports/}).waitFor();
      await englishSearch.press('Tab');
      await page.getByRole('button', {name: 'Clear capability search', exact: true}).press('Enter');
      assert.equal(await englishSearch.inputValue(), '');
      assert(await englishSearch.evaluate(element => element === document.activeElement));
      await verifyFilteredCatalogOrder(page);
      if (context.errors.some(error => !/503/.test(error))) throw new Error(context.errors.join(" | "));
      return { coverageEntries: await context.close(), note: "One capability destination; explicit device/Goal scope; fresh target reads; no cross-scope drafts or writes; Goal entry preserved; desktop/mobile verified." };
    } catch (error) {
      await page.screenshot({ path: resolve(outputDir, "capability-scope-failure.png"), animations: "disabled" });
      await context.close(); throw error;
    }
  },
};
