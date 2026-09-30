import { resolve } from "node:path";

import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

export const stewardModelSettingsScenario = {
  id: "steward-model-settings",
  async run({ browser, collectCoverage, url }) {
    const binding = {
      schema_version: "manager_channel_binding_v0", executor_endpoint: "codex",
      executor_endpoint_source: "session_binding", executor_kind: "individual",
      model: "gpt-6-sol", model_source: "machine_configuration", reasoning_effort: "xhigh",
      credential_env_var: "", operator_credential_configured: false,
      available: null, unavailable_reason: null,
    };
    let capabilityReads = 0;
    const context = await openWorkspacePage(browser, url, {
      apiOptions: { managerChannelBinding: binding }, collectCoverage,
      beforeGoto: async (_api, page) => page.on("request", request => {
        if (new URL(request.url()).pathname === "/api/chat/capabilities") capabilityReads += 1;
      }),
    });
    const { api, page } = context;
    try {
      const modelControl = page.getByRole("button", { name: "调整管家模型与思考深度", exact: true });
      await modelControl.waitFor();
      if (!(await modelControl.innerText()).includes("xhigh")) throw new Error("Current binding effort is missing");
      await page.screenshot({ path: resolve(outputDir, "steward-model-entry.png"), animations: "disabled" });
      await modelControl.focus();
      await page.keyboard.press("Enter");
      const settingsTabs = page.locator(".personal-settings-tabs");
      const stewardTab = settingsTabs.getByRole("button", { name: "管家", exact: true });
      if (await stewardTab.getAttribute("aria-current") !== "page") {
        throw new Error("Steward must be the primary settings destination");
      }
      const capabilityList = page.locator(".personal-capability-list");
      await capabilityList.getByRole("button", { name: "运行环境" }).waitFor();
      if (await capabilityList.getByRole("button").count() !== 2) {
        throw new Error("Steward settings should only contain model/executor and runtime");
      }
      await settingsTabs.getByRole("button", { name: "能力中心", exact: true }).click();
      await page.locator(".personal-capability-detail").waitFor();
      if (await capabilityList.getByRole("button", { name: "模型与执行器" }).count()
          || await capabilityList.getByRole("button", { name: "运行环境" }).count()
          || await capabilityList.getByRole("button", { name: "探索图谱" }).count()) {
        throw new Error("Other machine settings must exclude steward and Goal-only capabilities");
      }
      await stewardTab.click();
      const detail = page.locator(".personal-capability-detail");
      await detail.getByText("管家模型与思考深度").waitFor();
      await page.screenshot({ path: resolve(outputDir, "steward-model-settings.png"), fullPage: false, animations: "disabled" });
      await detail.getByLabel("模型").fill("gpt-6.1-sol");
      await detail.getByLabel("推理档位").selectOption("high");
      const readsBeforeApply = capabilityReads;
      await detail.getByRole("button", { name: "预览变更" }).click();
      await detail.getByRole("button", { name: "应用已审阅预览" }).click();
      const applied = api.machineConfigurationRequests.find((item) => item.phase === "apply");
      if (applied?.namespace !== "steward_executor"
          || applied?.namespace_configuration?.executor_model !== "gpt-6.1-sol"
          || applied?.namespace_configuration?.executor_reasoning_effort !== "high") {
        throw new Error("Steward settings did not apply the selected model and effort");
      }
      await detail.getByLabel("模型").waitFor();
      const readBack = async () => await detail.getByLabel("模型").inputValue() === "gpt-6.1-sol"
        && await detail.getByLabel("推理档位").inputValue() === "high";
      for (let attempt = 0; attempt < 50 && !await readBack(); attempt += 1) await page.waitForTimeout(100);
      if (!await readBack()) throw new Error("Steward model and effort were not read back after apply");
      for (let attempt = 0; attempt < 50 && capabilityReads === readsBeforeApply; attempt += 1) await page.waitForTimeout(100);
      if (capabilityReads === readsBeforeApply) throw new Error("Applying settings did not refresh the owning channel projection");
      await page.setViewportSize({ width: 390, height: 844 });
      await stewardTab.waitFor({ state: "visible" });
      if (await stewardTab.getAttribute("aria-current") !== "page") {
        throw new Error("Steward section was lost in the narrow settings navigation");
      }
      await page.screenshot({ path: resolve(outputDir, "steward-model-settings-mobile.png"), fullPage: false, animations: "disabled" });
      await page.getByRole("button", { name: "返回工作区", exact: true }).click();
      await modelControl.waitFor();
      if (!await modelControl.evaluate(element => element === document.activeElement)) {
        throw new Error("Closing settings did not return keyboard focus to the model control");
      }
      if (!(await modelControl.innerText()).includes("gpt-6-sol")) {
        throw new Error("A saved default was falsely shown as an adopted model");
      }
      // Simulated upstream adoption, separately proved by the native runtime
      // regression. The browser consumes that readback instead of guessing it.
      binding.model = "gpt-6.1-sol";
      binding.reasoning_effort = "high";
      await page.getByRole("button", { name: "刷新状态", exact: true }).click();
      for (let attempt = 0; attempt < 50 && !(await modelControl.innerText()).includes("gpt-6.1-sol"); attempt += 1) await page.waitForTimeout(100);
      if (!(await modelControl.innerText()).includes("gpt-6.1-sol")) throw new Error("Adopted model did not reach the header");
      await page.screenshot({ path: resolve(outputDir, "steward-model-entry-mobile.png"), animations: "disabled" });
      if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error("Model control overflows the narrow viewport");
      if (context.errors.length) throw new Error(context.errors.join(" | "));
      return { coverageEntries: await context.close(), note: "Model badge opens Steward settings; Sol 6.1/high saves and reads back, current binding remains truthful, narrow keyboard return works" };
    } catch (error) {
      await context.close();
      throw error;
    }
  },
};
