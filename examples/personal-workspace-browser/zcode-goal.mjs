import assert from "node:assert/strict";
import {resolve} from "node:path";
import {outputDir} from "./fixture.mjs";
import {openWorkspacePage} from "./scenario-context.mjs";

// Stateful transport fixture: this proves packaged caller behavior, not a live ZCode model or native protocol.
export const zcodeGoalScenario = {
  id: "zcode-goal",
  async run({browser, collectCoverage, url}) {
    const calls = [];
    const agents = new Map();
    const modelSelection = {providerId: "browser-provider", modelId: "browser-model"};
    const modelCatalog = [
      {selection: modelSelection, label: "Browser model", provider_label: "Browser Provider", reasoning_levels: ["low", "high"], default_reasoning_level: null, disabled: false},
      {selection: {providerId: "browser-provider", modelId: "browser-default"}, label: "Default reasoning model", provider_label: "Browser Provider", reasoning_levels: ["low", "high"], default_reasoning_level: "low", disabled: false},
      {selection: {providerId: "browser-provider", modelId: "browser-disabled"}, label: "Unavailable model", provider_label: "Browser Provider", reasoning_levels: [], default_reasoning_level: null, disabled: true},
    ];
    let mismatchPause = true;
    let creationWitness = "browser-generation-A";
    let modelRequests = 0;
    let heldReadback;
    let heldAgentId = "zcode-primary";
    let primaryReadStarted;
    let primaryReadObserved;
    const resultFor = agentId => {
      const state = agents.get(agentId);
      return {
        ok: !state?.executionFailed, ...(state?.executionFailed ? {reason: "zcode_native_execution_failed"} : {}), available: state?.connected !== false, goal_id: "loopx-meta", agent_id: agentId,
        goal_ref: {goal_id: "loopx-meta"}, goal_creation_operation_id: creationWitness, identity_scope: "legacy_goal_alias",
        binding: state?.bound ? {mode: "managed_cli", connected: state.connected !== false, cli_path: "synthetic-zcode", protocol: "ZCode Protocol v1"} : null,
        native: state?.bound ? {session_id: `native-${agentId}`, target_id: state.started ? "target-browser" : null,
          status: state.status, raw_status: state.status, running: state.running, session_status: state.running ? "running" : "idle", usage: null,
          selected_model: state.model ?? null, available_models: modelCatalog} : null,
        quota: state?.bound ? {should_run: state.quota, checked_at: "2026-10-06T00:00:00Z", reason: state.quota ? "Quota permits continuation" : "Quota admission held"} : null,
        actions: state?.bound ? state.actions : ["bind", "status"],
      };
    };
    const context = await openWorkspacePage(browser, url, {collectCoverage,
      async beforeGoto(api, page) {
        api.registeredAgentsByGoal = {"loopx-meta": ["zcode-looking"]};
        api.zcodeEligibleAgentsByGoal = {"loopx-meta": []};
        await page.route("**/api/goals/*/agents/*/zcode-goal", async route => {
          const request = route.request();
          const pathname = new URL(request.url()).pathname;
          const agentId = decodeURIComponent(pathname.split("/")[5]);
          assert(pathname.startsWith("/api/goals/loopx-meta/agents/"));
          const body = request.method() === "POST" ? request.postDataJSON() : null;
          calls.push({agentId, body});
          if (body) {
            assert.deepEqual(Object.keys(body).sort(), body.action === "bind" ? ["action", "cli_path", "expected_binding"] : body.action === "select_model" ? ["action", "expected_binding", "model_selection"] : ["action", "expected_binding"]);
            assert.deepEqual(body.expected_binding.goal_ref, {goal_id: "loopx-meta"});
            if (body.expected_binding.goal_creation_operation_id !== creationWitness) {
              await route.fulfill({status: 409, json: {error: "Goal binding changed; read current state before continuing.", error_code: "zcode_goal_binding_stale"}});
              return;
            }
            if (body.action === "bind" && body.cli_path === "missing-zcode") {
              await route.fulfill({json: {...resultFor(agentId), ok: false, available: false, reason: "ZCode CLI 不可用"}});
              return;
            }
            if (body.action === "bind") agents.set(agentId, {bound: true, connected: true, started: false, status: null, running: false, quota: true, actions: ["bind", "select_model", "status"]});
            const state = agents.get(agentId);
            if (body.action === "select_model") {
              assert.deepEqual(body.model_selection, {...modelSelection, options: {reasoningLevel: "high"}});
              Object.assign(state, {model: body.model_selection, actions: ["bind", "select_model", "start", "status"]});
            }
            if (["start", "resume"].includes(body.action)) {modelRequests += 1;}
            if (["start", "resume"].includes(body.action)) Object.assign(state, {started: true, status: "active", running: true, actions: ["pause", "stop", "status"]});
            if (body.action === "pause") {
              Object.assign(state, {status: "paused", running: false, actions: ["resume", "stop", "status"]});
              if (mismatchPause) {
                mismatchPause = false;
                await route.fulfill({json: {...resultFor(agentId), goal_id: "wrong-goal"}});
                return;
              }
            }
            if (body.action === "stop") Object.assign(state, {connected: false, started: false, status: null, running: false, actions: ["bind", "status"]});
          } else if (agentId === heldAgentId && heldReadback) {
            const release = heldReadback;
            const stale = resultFor(agentId);
            primaryReadObserved();
            await release;
            await route.fulfill({json: {...stale, native: {...stale.native, status: "active", running: true}}}).catch(() => {});
            return;
          }
          await route.fulfill({json: resultFor(agentId)});
        });
      },
    });
    try {
      const {page} = context;
      await page.locator(".personal-goal-link").filter({hasText: "LoopX meta"}).click();
      await page.getByRole("navigation", {name: "Goal 视图"}).getByRole("button", {name: "概览", exact: true}).click();
      await page.getByRole("button", {name: "Goal 信息", exact: true}).click();
      const control = page.locator(".personal-zcode-goal");
      assert.equal(await control.count(), 0, "A pure other-host Goal must not advertise ZCode controls");
      assert.equal(calls.length, 0, "An ineligible Goal must never probe ZCode");
      context.api.registeredAgentsByGoal = {"loopx-meta": ["zcode-looking", "zcode-primary", "zcode-secondary"]};
      context.api.zcodeEligibleAgentsByGoal = {"loopx-meta": ["zcode-primary", "zcode-secondary"]};
      await page.getByRole("button", {name: "刷新状态", exact: true}).click();
      await control.waitFor();
      assert.equal(calls.length, 0, "Collapsed opt-in controls must not probe or mutate ZCode");
      await control.locator(":scope > summary").click();
      await control.getByRole("button", {name: "绑定 CLI", exact: true}).waitFor();
      await page.waitForFunction(() => !document.querySelector(".personal-zcode-agent")?.getAttribute("aria-busy")?.includes("true"));
      assert.equal(calls.filter(call => call.body).length, 0, "Readback must not bind or execute a model");
      const agentPicker = control.getByRole("combobox", {name: "已注册 Agent"});
      assert.deepEqual(await agentPicker.locator("option").evaluateAll(options => options.map(option => option.value)), ["zcode-primary", "zcode-secondary"], "Only backend-eligible candidates belong in a mixed Goal's selector");
      assert.equal(await agentPicker.inputValue(), "zcode-primary", "The default must skip the first registered but ineligible Agent");
      assert.equal(calls.every(call => call.agentId !== "zcode-looking"), true, "Agent names cannot grant provider eligibility");
      const cli = control.getByRole("textbox", {name: "CLI 路径"});
      await cli.fill("missing-zcode");
      await control.getByRole("button", {name: "绑定 CLI", exact: true}).click();
      await control.getByRole("alert").filter({hasText: "ZCode CLI 不可用"}).waitFor();
      assert.equal(await cli.inputValue(), "missing-zcode", "An unavailable binding preserves the user's draft for repair");
      assert.equal(await control.getByRole("button", {name: "启动", exact: true}).isDisabled(), true);
      await cli.fill("synthetic-zcode");
      await control.getByRole("button", {name: "绑定 CLI", exact: true}).click();
      await control.getByText("已连接", {exact: true}).waitFor();
      assert.equal(await control.getByRole("button", {name: "启动", exact: true}).isDisabled(), true, "No model is automatically chosen at binding");
      assert.equal(calls.some(call => call.body?.action === "select_model"), false);
      const modelPicker = control.getByRole("combobox", {name: "已配置模型"});
      assert.equal(await modelPicker.inputValue(), "");
      assert.equal(await modelPicker.locator('option').filter({hasText: "Unavailable model"}).getAttribute("disabled"), "");
      await modelPicker.selectOption(JSON.stringify({providerId: "browser-provider", modelId: "browser-default"}));
      assert.equal(await control.getByRole("combobox", {name: "推理级别"}).inputValue(), "low", "Only a host-provided reasoning default may be prefilled");
      assert.equal(calls.some(call => call.body?.action === "select_model"), false);
      await modelPicker.selectOption(JSON.stringify(modelSelection));
      const reasoning = control.getByRole("combobox", {name: "推理级别"});
      assert.equal(await reasoning.inputValue(), "", "An absent host reasoning default must remain unselected");
      assert.equal(await control.getByRole("button", {name: "使用此模型", exact: true}).isDisabled(), true);
      await reasoning.selectOption("high");
      await control.getByRole("button", {name: "使用此模型", exact: true}).click();
      await control.getByText("Browser model", {exact: true}).waitFor();
      creationWitness = "browser-generation-B";
      await control.getByRole("button", {name: "启动", exact: true}).click();
      await control.getByRole("alert").filter({hasText: "Goal binding changed"}).waitFor();
      assert.equal(modelRequests, 0, "A cached A-panel cannot run models for the same alias's new B binding");
      assert.equal(await control.getByRole("button", {name: "启动", exact: true}).isDisabled(), true);
      await control.getByRole("button", {name: "回读状态", exact: true}).click();
      await control.getByText("已连接", {exact: true}).waitFor();
      await control.getByRole("button", {name: "启动", exact: true}).click();
      await control.getByText("正在运行", {exact: true}).waitFor();
      await cli.fill("another-zcode");
      assert.equal(await control.getByRole("button", {name: "暂停", exact: true}).isEnabled(), true, "Editing a future CLI path must not hide the current session's stop controls");
      await cli.fill("synthetic-zcode");
      await control.getByRole("button", {name: "暂停", exact: true}).click();
      await control.getByRole("alert").filter({hasText: "source changed"}).waitFor();
      assert.equal(await control.getByRole("button", {name: "恢复", exact: true}).isDisabled(), true, "Wrong-context receipt cannot authorize a second operation");
      await control.getByRole("button", {name: "回读状态", exact: true}).click();
      await control.getByText("已暂停", {exact: true}).waitFor();
      Object.assign(agents.get("zcode-primary"), {quota: false, actions: ["stop", "status"]});
      await control.getByRole("button", {name: "回读状态", exact: true}).click();
      await control.getByText("续跑受限", {exact: true}).waitFor();
      assert.equal(await control.getByRole("button", {name: "恢复", exact: true}).isDisabled(), true, "Paused status does not override the owner's unavailable resume action");
      Object.assign(agents.get("zcode-primary"), {quota: true, actions: ["resume", "stop", "status"]});
      await control.getByRole("button", {name: "回读状态", exact: true}).click();
      await control.getByText("允许续跑", {exact: true}).waitFor();
      await control.getByRole("button", {name: "恢复", exact: true}).click();
      await control.getByText("正在运行", {exact: true}).waitFor();
      await control.getByRole("button", {name: "停止", exact: true}).click();
      await control.getByText("未运行", {exact: true}).waitFor();
      assert.equal(agents.get("zcode-primary").started, false, "Stop clears the native target without claiming Goal completion");
      await control.getByText("未连接", {exact: true}).waitFor();
      assert.equal(agents.get("zcode-primary").connected, false, "Stop readback confirms the owned CLI controller has disconnected");
      agents.get("zcode-primary").executionFailed = true;
      await control.getByRole("button", {name: "回读状态", exact: true}).click();
      await control.getByRole("alert").filter({hasText: "ZCode 原生执行失败"}).waitFor();
      assert.equal(await control.getByText("正在运行", {exact: true}).count(), 0);
      agents.get("zcode-primary").executionFailed = false;
      await control.getByRole("button", {name: "回读状态", exact: true}).click();
      await control.getByText("未运行", {exact: true}).waitFor();
      await control.locator(".personal-zcode-diagnostics > summary").click();
      await control.getByText("native-zcode-primary", {exact: true}).waitFor();
      await control.locator(".personal-zcode-diagnostics > summary").click();
      await control.locator(":scope > summary").scrollIntoViewIfNeeded();
      await page.screenshot({path: resolve(outputDir, "zcode-goal-desktop.png"), animations: "disabled"});
      let releasePrimary;
      heldReadback = new Promise(resolveWait => {releasePrimary = resolveWait;});
      primaryReadStarted = new Promise(resolveWait => {primaryReadObserved = resolveWait;});
      await control.getByRole("button", {name: "回读状态", exact: true}).click();
      await primaryReadStarted;
      await control.getByRole("combobox", {name: "已注册 Agent"}).selectOption("zcode-secondary");
      await control.getByText("未绑定", {exact: true}).waitFor();
      releasePrimary();
      heldReadback = null;
      await page.waitForTimeout(100);
      assert.equal(await control.getByText("正在运行", {exact: true}).count(), 0, "An old Agent response cannot populate the new selection");
      assert.equal(await control.getByRole("button", {name: "启动", exact: true}).isDisabled(), true);
      await page.setViewportSize({width: 390, height: 844});
      await control.locator(":scope > summary").scrollIntoViewIfNeeded();
      assert(await control.evaluate(element => element.scrollWidth <= element.clientWidth), "Native controls must fit the mobile drawer");
      await page.screenshot({path: resolve(outputDir, "zcode-goal-mobile.png"), animations: "disabled"});
      await page.setViewportSize({width: 1512, height: 982});
      let releaseRemoved;
      heldAgentId = "zcode-secondary";
      heldReadback = new Promise(resolveWait => {releaseRemoved = resolveWait;});
      primaryReadStarted = new Promise(resolveWait => {primaryReadObserved = resolveWait;});
      await control.getByRole("button", {name: "回读状态", exact: true}).click();
      await primaryReadStarted;
      context.api.registeredAgentsByGoal = {"loopx-meta": ["zcode-looking"]};
      context.api.zcodeEligibleAgentsByGoal = {"loopx-meta": []};
      const callsAtRemoval = calls.length;
      await page.getByRole("button", {name: "刷新状态", exact: true}).click();
      await control.waitFor({state: "detached"});
      releaseRemoved();
      heldReadback = null;
      await page.waitForTimeout(100);
      assert.equal(await control.count(), 0, "Removing the selected Agent's eligibility discards native controls and any pending receipt");
      assert.equal(calls.length, callsAtRemoval, "A removed Agent must cause no further native status or operation calls");
      context.api.registeredAgentsByGoal = {"loopx-meta": ["zcode-looking", "zcode-secondary"]};
      context.api.zcodeEligibleAgentsByGoal = {"loopx-meta": ["zcode-secondary"]};
      await page.getByRole("button", {name: "刷新状态", exact: true}).click();
      await control.waitFor();
      assert.equal(calls.length, callsAtRemoval, "A newly compatible advisory Agent remains an explicit, collapsed entry");
      await control.locator(":scope > summary").click();
      await control.getByText("未绑定", {exact: true}).waitFor();
      assert.equal(await control.getByRole("button", {name: "启动", exact: true}).isDisabled(), true, "A late removed-Agent receipt cannot authorize a new panel");
      assert.deepEqual(await control.getByRole("combobox", {name: "已注册 Agent"}).locator("option").evaluateAll(options => options.map(option => option.value)), ["zcode-secondary"]);
      assert.deepEqual(calls.filter(call => call.body).map(call => call.body.action), ["bind", "bind", "select_model", "start", "start", "pause", "resume", "stop"]);
      assert.deepEqual(context.errors.filter(message => !/server responded with a status of 409/.test(message)), []);
      return {coverageEntries: await context.close(), note: "Packaged Goal drawer consumes backend eligibility: pure other-host entries stay hidden with zero probes, mixed candidates and defaults are filtered, advisory compatibility remains explicit, and removal fences late receipts. It proves explicit binding and model/reasoning selection, all native controls, unavailable path recovery, quota action gating, same-alias stale-write rejection before models, explicit native execution failure and wrong-context receipt recovery and stale Agent response isolation using a stateful transport fixture."};
    } catch (error) {
      await context.close();
      throw error;
    }
  },
};
