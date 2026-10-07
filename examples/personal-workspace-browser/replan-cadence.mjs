import { resolve } from "node:path";
import { outputDir } from "./fixture.mjs";
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
    try {
      context = await openWorkspacePage(browser, url, { collectCoverage,
        beforeGoto: async (_api, page) => {
          for (const endpoint of ["machine-configuration", "goal-configuration"]) {
            await page.route(`**/api/chat/${endpoint}**`, async (route) => {
              const parsed = new URL(route.request().url());
              await route.fulfill({ response: await route.fetch({ url: authority.url + parsed.pathname + parsed.search }) });
            });
          }
        },
      });
      const { page } = context;
      async function openMachineCadence() {
        await page.getByRole("button", { name: "设置", exact: true }).click();
        await page.locator(".personal-settings-tabs").getByRole("button", { name: "能力中心", exact: true }).click();
        await page.getByRole("navigation", { name: "机器能力目录" }).getByRole("button", { name: /Goal 复核周期/ }).click();
      }
      await openMachineCadence();
      const unit = page.getByLabel(/^复核计数依据/);
      const count = page.getByLabel("两次复核间的数量", { exact: true });
      await page.waitForFunction(() => [...document.querySelectorAll("input[type=number]")].some((input) => input.value === "5"));
      if (await unit.inputValue() !== "effective_turns") throw new Error("Product default is not settled work Turns");
      await page.screenshot({ path: resolve(outputDir, "replan-cadence-product-default.png"), animations: "disabled" });
      const preview = await request(base + "/preview", legacy);
      await request(base + "/apply", { ...legacy, expected_plan_revision: preview.plan_revision });
      await page.reload({ waitUntil: "networkidle" });
      await openMachineCadence();
      await page.waitForFunction(() => [...document.querySelectorAll("input[type=number]")].some((input) => input.value === "2"));
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
      await page.getByRole("button", { name: "预览变更", exact: true }).click();
      await page.getByRole("button", { name: "应用已审阅预览", exact: true }).click();
      await page.getByRole("button", { name: "预览回滚", exact: true }).waitFor();
      stored = (await request(base)).machine_configuration.namespaces.todo_replan_cadence;
      if (stored.count_unit !== "effective_turns" || stored.count !== 3 || "completed_todos" in stored) throw new Error("Migration did not persist the selected unit");
      await page.screenshot({ path: resolve(outputDir, "replan-cadence-machine.png"), animations: "disabled" });
      await page.getByRole("radio", { name: "单个 Goal", exact: true }).check();
      await page.getByRole("combobox", { name: "目标 Goal", exact: true }).selectOption("multi-agent-projection");
      await page.getByRole("navigation", { name: "Goal 能力目录" }).getByRole("button", { name: /Goal 复核周期/ }).click();
      await page.waitForFunction(() => [...document.querySelectorAll("input[type=number]")].some((input) => input.value === "3"));
      if (await unit.inputValue() !== "effective_turns" || await count.inputValue() !== "3") throw new Error("Goal did not inherit effective-Turn device policy");
      async function readGoalCadence() {
        const packet = await request("/api/chat/goal-configuration?goal_id=multi-agent-projection");
        return packet.capability_catalog.capabilities.find((row) => row.capability_id === "todo_replan_cadence");
      }
      await count.fill("2");
      await page.getByRole("button", { name: "预览变更", exact: true }).click();
      await page.getByRole("button", { name: "应用此预览", exact: true }).click();
      await page.getByRole("button", { name: "恢复沿用设备默认", exact: true }).waitFor();
      let goalCadence = await readGoalCadence();
      if (goalCadence.effective_configuration.source !== "goal_override" || goalCadence.current.count !== 2) throw new Error("Goal override readback failed");
      await page.getByRole("button", { name: "恢复沿用设备默认", exact: true }).click();
      await page.getByRole("button", { name: "应用此预览", exact: true }).click();
      await page.getByRole("button", { name: "恢复沿用设备默认", exact: true }).waitFor({ state: "detached" });
      goalCadence = await readGoalCadence();
      if (goalCadence.effective_configuration.source !== "machine_default" || goalCadence.effective_configuration.configuration.count !== 3) throw new Error("Goal clear did not restore device policy");
      await page.setViewportSize({ width: 390, height: 844 });
      await unit.focus();
      await page.screenshot({ path: resolve(outputDir, "replan-cadence-goal-mobile.png"), animations: "disabled" });
      if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)) throw new Error("Cadence editor overflowed");
      await page.setViewportSize({ width: 1512, height: 982 });
      await page.evaluate(() => localStorage.setItem("loopx-pw-locale", "en"));
      await page.reload({ waitUntil: "networkidle" });
      await page.getByRole("button", { name: "Settings", exact: true }).click();
      await page.locator(".personal-settings-tabs").getByRole("button", { name: "Capability Center", exact: true }).click();
      await page.getByRole("radio", { name: "One Goal", exact: true }).check();
      await page.getByRole("combobox", { name: "Target Goal", exact: true }).selectOption("multi-agent-projection");
      await page.getByRole("navigation", { name: "Goal capability catalog" }).getByRole("button", { name: /Goal review cadence/ }).click();
      const englishUnit = page.getByLabel(/^Review after/);
      const englishCount = page.getByLabel("Number between reviews", { exact: true });
      await page.waitForFunction(() => [...document.querySelectorAll("input[type=number]")].some(input => input.value === "3"));
      if (await englishUnit.inputValue() !== "effective_turns") throw new Error("English editor lost the persisted unit");
      await englishCount.fill("1");
      await page.getByRole("button", { name: "Preview changes", exact: true }).click();
      await page.getByRole("button", { name: "Apply preview", exact: true }).click();
      await page.getByRole("button", { name: "Use device defaults", exact: true }).waitFor();
      if ((await readGoalCadence()).current.count !== 1) throw new Error("English override was not applied");
      await page.screenshot({ path: resolve(outputDir, "replan-cadence-goal-english.png"), animations: "disabled" });
      await page.getByRole("button", { name: "Use device defaults", exact: true }).click();
      await page.getByRole("button", { name: "Apply preview", exact: true }).click();
      await page.getByRole("button", { name: "Use device defaults", exact: true }).waitFor({ state: "detached" });
      if ((await readGoalCadence()).effective_configuration.configuration.count !== 3) throw new Error("English clear did not restore device policy");
      const remove = { namespace: "todo_replan_cadence", operation: "remove" };
      const removalPreview = await request(base + "/preview", remove);
      await request(base + "/apply", { ...remove, expected_plan_revision: removalPreview.plan_revision });
      const inherited = (await readGoalCadence()).effective_configuration;
      if (inherited.source !== "capability_default" || inherited.configuration.count_unit !== "effective_turns" || inherited.configuration.count !== 5) throw new Error("Removing device policy did not restore product default");
      await page.reload({ waitUntil: "networkidle" });
      await page.getByRole("button", { name: "Settings", exact: true }).click();
      await page.locator(".personal-settings-tabs").getByRole("button", { name: "Capability Center", exact: true }).click();
      await page.getByRole("radio", { name: "One Goal", exact: true }).check();
      await page.getByRole("combobox", { name: "Target Goal", exact: true }).selectOption("multi-agent-projection");
      await page.getByRole("navigation", { name: "Goal capability catalog" }).getByRole("button", { name: /Goal review cadence/ }).click();
      await page.waitForFunction(() => [...document.querySelectorAll("input[type=number]")].some(input => input.value === "5"));
      if (await englishUnit.inputValue() !== "effective_turns") throw new Error("Goal editor did not restore product unit");
      return { coverageEntries: context.coverageEntries, note: "Packaged default five effective Turns, machine v0→v1 migration, invalid preview, Goal override/clear and device removal through real handlers." };
    } catch (error) {
      if (context) {
        await context.page.screenshot({ path: resolve(outputDir, "replan-cadence-failure.png"), animations: "disabled" });
        console.error((await context.page.locator("body").innerText()).slice(-7000));
      }
      throw error;
    } finally {
      await context?.close();
      await authority.close();
    }
  },
};
