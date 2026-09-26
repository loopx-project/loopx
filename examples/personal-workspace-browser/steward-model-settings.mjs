import { resolve } from "node:path";

import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

export const stewardModelSettingsScenario = {
  id: "steward-model-settings",
  async run({ browser, collectCoverage, url }) {
    const context = await openWorkspacePage(browser, url, { collectCoverage });
    const { api, page } = context;
    try {
      await page.getByRole("button", { name: "设置", exact: true }).click();
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
      await detail.getByLabel("模型").fill("gpt-6-sol");
      await detail.getByLabel("推理档位").selectOption("xhigh");
      await detail.getByRole("button", { name: "预览变更" }).click();
      await detail.getByRole("button", { name: "应用已审阅预览" }).click();
      const applied = api.machineConfigurationRequests.find((item) => item.phase === "apply");
      if (applied?.namespace !== "steward_executor"
          || applied?.namespace_configuration?.executor_model !== "gpt-6-sol"
          || applied?.namespace_configuration?.executor_reasoning_effort !== "xhigh") {
        throw new Error("Steward settings did not apply the selected model and effort");
      }
      await detail.getByLabel("模型").waitFor();
      const readBack = async () => await detail.getByLabel("模型").inputValue() === "gpt-6-sol"
        && await detail.getByLabel("推理档位").inputValue() === "xhigh";
      for (let attempt = 0; attempt < 50 && !await readBack(); attempt += 1) await page.waitForTimeout(100);
      if (!await readBack()) throw new Error("Steward model and effort were not read back after apply");
      await page.setViewportSize({ width: 390, height: 844 });
      await stewardTab.waitFor({ state: "visible" });
      if (await stewardTab.getAttribute("aria-current") !== "page") {
        throw new Error("Steward section was lost in the narrow settings navigation");
      }
      await page.screenshot({ path: resolve(outputDir, "steward-model-settings-mobile.png"), fullPage: false, animations: "disabled" });
      if (context.errors.length) throw new Error(context.errors.join(" | "));
      return { coverageEntries: await context.close(), note: "Manager settings directly select and read back Sol xhigh" };
    } catch (error) {
      await context.close();
      throw error;
    }
  },
};
