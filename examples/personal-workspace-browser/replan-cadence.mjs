import assert from "node:assert/strict";
import { resolve } from "node:path";
import { outputDir, waitForInputValue } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";
import { startCadenceAuthority } from "./automation-cadence.mjs";

// Configuration traffic uses the real revision-checked handlers and disposable
// storage. Only the surrounding workspace discovery remains a browser fixture.
export const replanCadenceScenario = {
  id: "replan-cadence",
  async run({ browser, collectCoverage, url }) {
    const authority = await startCadenceAuthority();
    async function request(path, body) {
      const response = await fetch(authority.url + path, body ? {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      } : undefined);
      const value = await response.json();
      if (!response.ok) throw new Error(JSON.stringify(value));
      return value;
    }
    const base = "/api/chat/machine-configuration";
    const legacy = { namespace: "todo_replan_cadence", namespace_configuration: {
      schema_version: "todo_replan_cadence_machine_defaults_v0", completed_todos: 2,
    } };
    let context;
    let heldReadback;
    let goalApplyCount = 0;
    function holdGoalReadback() {
      let started, release;
      const readback = {
        started: new Promise((resolve) => { started = resolve; }),
        released: new Promise((resolve) => { release = resolve; }),
        start: () => started(),
        release: (fail = false) => release(fail),
      };
      heldReadback = readback;
      return readback;
    }
    try {
      context = await openWorkspacePage(browser, url, { collectCoverage,
        beforeGoto: async (_api, page) => {
          for (const endpoint of ["machine-configuration", "goal-configuration"]) {
            await page.route(`**/api/chat/${endpoint}**`, async (route) => {
              const parsed = new URL(route.request().url());
              if (parsed.pathname.endsWith("/goal-configuration/apply")) goalApplyCount++;
              const response = await route.fetch({ url: authority.url + parsed.pathname + parsed.search });
              if (heldReadback && parsed.pathname === "/api/chat/goal-configuration" && route.request().method() === "GET") {
                const pending = heldReadback;
                pending.start();
                const fail = await pending.released;
                heldReadback = null;
                if (fail) {
                  await route.fulfill({ status: 503, json: { ok: false, error: "Configuration readback unavailable" } });
                  return;
                }
              }
              await route.fulfill({ response });
            });
          }
        },
      });
      const { page } = context;
      async function applyGoalPreview(name = "应用此预览") {
        const response = page.waitForResponse((value) =>
          value.url().includes("/api/chat/goal-configuration/apply") && value.request().method() === "POST");
        await page.getByRole("button", { name, exact: true }).click();
        const settled = await response;
        if (!settled.ok()) throw new Error(await settled.text());
      }

      async function openMachineCadence() {
        await page.getByRole("button", { name: "设置", exact: true }).click();
        await page.locator(".personal-settings-tabs").getByRole("button", { name: "能力中心", exact: true }).click();
        await page.getByRole("navigation", { name: "机器能力目录" }).getByRole("button", { name: /Goal 复盘与步幅/ }).click();
      }
      await openMachineCadence();
      const unit = page.getByLabel(/^复核计数依据/);
      const count = page.getByLabel("两次复核间的数量", { exact: true });
      const strategy = page.getByLabel(/^步幅策略/);
      await waitForInputValue(count, "6");
      if (await strategy.inputValue() !== "off") throw new Error("Stride must default off");
      if (await unit.inputValue() !== "effective_turns") throw new Error("Product default is not settled work Turns");
      await page.screenshot({ path: resolve(outputDir, "replan-cadence-product-default.png"), animations: "disabled" });
      const preview = await request(base + "/preview", legacy);
      await request(base + "/apply", { ...legacy, expected_plan_revision: preview.plan_revision });
      await page.reload({ waitUntil: "networkidle" });
      await openMachineCadence();
      await waitForInputValue(count, "2");
      if (await unit.inputValue() !== "completed_todos" || await count.inputValue() !== "2") throw new Error("Legacy cadence was reinterpreted");
      let stored = (await request(base)).machine_configuration.namespaces.todo_replan_cadence;
      if (stored.schema_version !== legacy.namespace_configuration.schema_version) throw new Error("Read migrated persisted state");
      await unit.selectOption("effective_turns");
      await count.fill("0");
      await page.getByRole("button", { name: "预览变更", exact: true }).click();
      await page.getByText(/effective Turn count must be an integer/).waitFor();
      stored = (await request(base)).machine_configuration.namespaces.todo_replan_cadence;
      if (stored.completed_todos !== 2) throw new Error("Invalid preview mutated state");
      await count.fill("3");
      await strategy.selectOption("evidence_conditioned_v1");
      await page.getByRole("button", { name: "预览变更", exact: true }).click();
      await page.getByRole("button", { name: "应用已审阅预览", exact: true }).click();
      await page.getByRole("button", { name: "预览回滚", exact: true }).waitFor();
      stored = (await request(base)).machine_configuration.namespaces.todo_replan_cadence;
      if (stored.count_unit !== "effective_turns" || stored.count !== 3 || "completed_todos" in stored) throw new Error("Migration did not persist the selected unit");
      if (stored.stride_strategy !== "evidence_conditioned_v1" || stored.schema_version !== "todo_replan_cadence_machine_defaults_v2") throw new Error("Machine stride version not recorded");
      await page.screenshot({ path: resolve(outputDir, "replan-cadence-machine.png"), animations: "disabled" });
      await page.getByRole("radio", { name: "单个 Goal", exact: true }).check();
      await page.getByRole("combobox", { name: "目标 Goal", exact: true }).selectOption("multi-agent-projection");
      await page.getByRole("navigation", { name: "Goal 能力目录" }).getByRole("button", { name: /Goal 复盘与步幅/ }).click();
      await waitForInputValue(count, "3");
      if (await unit.inputValue() !== "effective_turns" || await count.inputValue() !== "3") throw new Error("Goal did not inherit effective-Turn device policy");
      async function readGoalCadence() {
        const packet = await request("/api/chat/goal-configuration?goal_id=multi-agent-projection");
        return packet.capability_catalog.capabilities.find((row) => row.capability_id === "todo_replan_cadence");
      }
      if (await strategy.inputValue() !== "evidence_conditioned_v1") throw new Error("Goal did not inherit strategy version");
      await page.getByRole("button", { name: "编辑 JSON", exact: true }).click();
      await page.locator("#goal-configuration-json").fill(JSON.stringify({ stride_strategy: "evidence_conditioned_v1" }));
      await page.getByRole("button", { name: "返回表单", exact: true }).click();
      if (await unit.inputValue() !== "effective_turns" || await count.inputValue() !== "3") {
        throw new Error("JSON-to-form conversion lost the effective review interval");
      }
      await page.getByRole("button", { name: "预览变更", exact: true }).click();
      await applyGoalPreview();
      const converted = await readGoalCadence();
      if (converted.effective_configuration.configuration.count !== 3
        || converted.effective_configuration.configuration.count_unit !== "effective_turns") {
        throw new Error("JSON-to-form save substituted the product-default interval");
      }
      await page.getByText("复盘周期沿用设备默认；步幅策略由本 Goal 设置。", { exact: true }).waitFor();
      await page.screenshot({ path: resolve(outputDir, "replan-cadence-json-form.png"), animations: "disabled" });
      if (JSON.stringify(converted.current) !== JSON.stringify({ stride_strategy: "evidence_conditioned_v1" })) {
        throw new Error("JSON-to-form strategy-only save pinned a cadence");
      }
      async function changeMachineCount(value) {
        const change = { namespace: "todo_replan_cadence", namespace_configuration: {
          schema_version: "todo_replan_cadence_machine_defaults_v2", count_unit: "effective_turns", count: value,
          stride_strategy: "evidence_conditioned_v1",
        } };
        const planned = await request(base + "/preview", change);
        await request(base + "/apply", { ...change, expected_plan_revision: planned.plan_revision });
      }
      async function reopenGoalCadence() {
        await page.reload({ waitUntil: "networkidle" });
        await page.getByRole("button", { name: "设置", exact: true }).click();
        await page.locator(".personal-settings-tabs").getByRole("button", { name: "能力中心", exact: true }).click();
        await page.getByRole("radio", { name: "单个 Goal", exact: true }).check();
        await page.getByRole("combobox", { name: "目标 Goal", exact: true }).selectOption("multi-agent-projection");
        await page.getByRole("navigation", { name: "Goal 能力目录" }).getByRole("button", { name: /Goal 复盘与步幅/ }).click();
      }
      await changeMachineCount(4);
      if ((await readGoalCadence()).effective_configuration.configuration.count !== 4) {
        throw new Error("Strategy-only Goal did not follow a later machine policy");
      }
      await reopenGoalCadence();
      await waitForInputValue(count, "4");
      await page.getByRole("button", { name: "编辑 JSON", exact: true }).click();
      await page.locator("#goal-configuration-json").fill(JSON.stringify({
        count_unit: "effective_turns", count: 4, stride_strategy: "evidence_conditioned_v1",
      }));
      await page.getByRole("button", { name: "返回表单", exact: true }).click();
      await page.getByRole("button", { name: "预览变更", exact: true }).click();
      await applyGoalPreview();
      if ((await readGoalCadence()).current.count !== 4) {
        throw new Error("JSON-to-form lost an explicit cadence matching the machine policy");
      }
      await changeMachineCount(3);
      if ((await readGoalCadence()).effective_configuration.configuration.count !== 4) {
        throw new Error("An explicit Goal cadence unexpectedly followed the machine policy");
      }
      await page.getByRole("button", { name: "编辑 JSON", exact: true }).click();
      await page.locator("#goal-configuration-json").fill(JSON.stringify({ stride_strategy: "evidence_conditioned_v1" }));
      await page.getByRole("button", { name: "返回表单", exact: true }).click();
      await page.getByRole("button", { name: "预览变更", exact: true }).click();
      await applyGoalPreview();
      const intervalCleared = await readGoalCadence();
      if (JSON.stringify(intervalCleared.current) !== JSON.stringify({ stride_strategy: "evidence_conditioned_v1" })
        || intervalCleared.effective_configuration.configuration.count !== 3) {
        throw new Error("Clearing only the cadence through JSON-to-form lost the strategy or retained the pin");
      }
      await reopenGoalCadence();
      await waitForInputValue(count, "3");
      await strategy.selectOption("off");
      await page.getByRole("button", { name: "预览变更", exact: true }).click();
      const delayedReadback = holdGoalReadback();
      await applyGoalPreview();
      await delayedReadback.started;
      assert.equal(await count.isDisabled(), true, "A committed write must keep the editor closed until its readback settles");
      assert.equal(await strategy.isDisabled(), true);
      assert.equal(await page.getByRole("button", { name: "编辑 JSON", exact: true }).isDisabled(), true);
      assert.equal(await page.getByRole("button", { name: "预览变更", exact: true }).isDisabled(), true);
      const goalCatalog = page.getByRole("navigation", { name: "Goal 能力目录" });
      await goalCatalog.getByRole("button", { name: "周期报告", exact: true }).click();
      await goalCatalog.getByRole("button", { name: /Goal 复盘与步幅/ }).click();
      assert.equal(await count.isDisabled(), true, "Resetting capability-local state must not reopen an unsettled catalog read");
      await page.screenshot({ path: resolve(outputDir, "replan-cadence-saving-readback.png"), animations: "disabled" });
      delayedReadback.release();
      let goalCadence = await readGoalCadence();
      if (JSON.stringify(goalCadence.current) !== JSON.stringify({ stride_strategy: "off" })
        || goalCadence.effective_configuration.configuration.count !== 3) {
        throw new Error("Goal strategy-only override pinned or lost the inherited review interval");
      }
      await count.fill("2");
      await strategy.selectOption("off");
      await page.getByRole("button", { name: "预览变更", exact: true }).click();
      await applyGoalPreview();
      await page.getByRole("button", { name: "恢复沿用设备默认", exact: true }).waitFor();
      goalCadence = await readGoalCadence();
      if (goalCadence.effective_configuration.source !== "goal_override" || goalCadence.current.count !== 2) throw new Error("Goal override readback failed");
      if (goalCadence.current.stride_strategy !== "off") throw new Error("Goal stride opt-out failed");
      if ((await request(base)).machine_configuration.namespaces.todo_replan_cadence.stride_strategy !== "evidence_conditioned_v1") throw new Error("Goal override changed machine strategy");
      await strategy.selectOption("evidence_conditioned_v1");
      await page.getByRole("button", { name: "预览变更", exact: true }).click();
      await applyGoalPreview();
      await page.getByRole("button", { name: "恢复沿用设备默认", exact: true }).waitFor();
      if ((await readGoalCadence()).current.stride_strategy !== "evidence_conditioned_v1") throw new Error("Goal strategy activation failed");
      await page.getByRole("button", { name: "恢复沿用设备默认", exact: true }).click();
      await applyGoalPreview();
      await page.getByRole("button", { name: "恢复沿用设备默认", exact: true }).waitFor({ state: "detached" });
      goalCadence = await readGoalCadence();
      if (goalCadence.effective_configuration.source !== "machine_default" || goalCadence.effective_configuration.configuration.count !== 3) throw new Error("Goal clear did not restore device policy");
      await page.setViewportSize({ width: 390, height: 844 });
      await unit.focus();
      await page.keyboard.press("Tab");
      await page.keyboard.press("Tab");
      if (!(await strategy.evaluate((element) => element === document.activeElement))) throw new Error("Strategy is not keyboard reachable");
      await page.screenshot({ path: resolve(outputDir, "replan-cadence-goal-mobile.png"), animations: "disabled" });
      if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)) throw new Error("Cadence editor overflowed");
      await page.setViewportSize({ width: 1512, height: 982 });
      await page.evaluate(() => localStorage.setItem("loopx-pw-locale", "en"));
      await page.reload({ waitUntil: "networkidle" });
      await page.getByRole("button", { name: "Settings", exact: true }).click();
      await page.locator(".personal-settings-tabs").getByRole("button", { name: "Capability Center", exact: true }).click();
      await page.getByRole("radio", { name: "One Goal", exact: true }).check();
      await page.getByRole("combobox", { name: "Target Goal", exact: true }).selectOption("multi-agent-projection");
      await page.getByRole("navigation", { name: "Goal capability catalog" }).getByRole("button", { name: /Goal review and stride/ }).click();
      const englishUnit = page.getByLabel(/^Review after/);
      const englishCount = page.getByLabel("Number between reviews", { exact: true });
      const englishStrategy = page.getByLabel(/^Stride strategy/);
      await waitForInputValue(englishCount, "3");
      if (await englishUnit.inputValue() !== "effective_turns" || await englishStrategy.inputValue() !== "evidence_conditioned_v1") throw new Error("English editor lost the unit or strategy");
      await englishCount.fill("1");
      await page.getByRole("button", { name: "Preview changes", exact: true }).click();
      await applyGoalPreview("Apply preview");
      await page.getByRole("button", { name: "Use device defaults", exact: true }).waitFor();
      if ((await readGoalCadence()).current.count !== 1) throw new Error("English override was not applied");
      await page.screenshot({ path: resolve(outputDir, "replan-cadence-goal-english.png"), animations: "disabled" });
      await page.getByRole("button", { name: "Use device defaults", exact: true }).click();
      await applyGoalPreview("Apply preview");
      await page.getByRole("button", { name: "Use device defaults", exact: true }).waitFor({ state: "detached" });
      if ((await readGoalCadence()).effective_configuration.configuration.count !== 3) throw new Error("English clear did not restore device policy");
      const remove = { namespace: "todo_replan_cadence", operation: "remove" };
      const removalPreview = await request(base + "/preview", remove);
      await request(base + "/apply", { ...remove, expected_plan_revision: removalPreview.plan_revision });
      const inherited = (await readGoalCadence()).effective_configuration;
      if (inherited.source !== "capability_default" || inherited.configuration.count_unit !== "effective_turns" || inherited.configuration.count !== 6) throw new Error("Removing device policy did not restore product default");
      await page.reload({ waitUntil: "networkidle" });
      await page.getByRole("button", { name: "Settings", exact: true }).click();
      await page.locator(".personal-settings-tabs").getByRole("button", { name: "Capability Center", exact: true }).click();
      await page.getByRole("radio", { name: "One Goal", exact: true }).check();
      await page.getByRole("combobox", { name: "Target Goal", exact: true }).selectOption("multi-agent-projection");
      await page.getByRole("navigation", { name: "Goal capability catalog" }).getByRole("button", { name: /Goal review and stride/ }).click();
      await waitForInputValue(englishCount, "6");
      if (await englishUnit.inputValue() !== "effective_turns" || await englishStrategy.inputValue() !== "off") throw new Error("Goal editor did not restore product defaults");
      await englishCount.fill("5");
      await page.getByRole("button", { name: "Preview changes", exact: true }).click();
      const failedReadback = holdGoalReadback();
      await applyGoalPreview("Apply preview");
      await failedReadback.started;
      assert.equal(await englishCount.isDisabled(), true);
      const committedApplies = goalApplyCount;
      failedReadback.release(true);
      await page.getByRole("alert").filter({ hasText: "Configuration readback unavailable" }).waitFor();
      assert.equal(await englishCount.count(), 0, "A failed readback must not expose a stale editor");
      assert.equal((await readGoalCadence()).current.count, 5, "Readback failure does not undo the committed write");
      await page.screenshot({ path: resolve(outputDir, "replan-cadence-readback-error.png"), animations: "disabled" });
      await page.getByRole("button", { name: "Retry", exact: true }).click();
      await page.getByRole("navigation", { name: "Goal capability catalog" }).getByRole("button", { name: /Goal review and stride/ }).click();
      await waitForInputValue(englishCount, "5");
      assert.equal(goalApplyCount, committedApplies, "Readback recovery must not submit the mutation again");
      return { coverageEntries: context.coverageEntries, note: "Packaged default six effective Turns, machine v0→v2 migration, strategy-only JSON/form inheritance across later machine changes, equal-value explicit pin and interval-only clear, default-off/versioned strategy, invalid preview, Goal override/clear, device removal, mobile keyboard and English labels through real handlers. Delayed post-apply readback keeps editing closed across capability changes; failed readback recovers the committed value without another mutation." };
    } catch (error) {
      if (context) {
        await context.page.screenshot({ path: resolve(outputDir, "replan-cadence-failure.png"), animations: "disabled" });
        console.error((await context.page.locator("body").innerText()).slice(-7000));
      }
      throw error;
    } finally {
      heldReadback?.release();
      await context?.close();
      await authority.close();
    }
  },
};
