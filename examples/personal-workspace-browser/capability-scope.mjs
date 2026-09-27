import { resolve } from "node:path";
import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

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
      const defaults = page.getByRole("radio", { name: "此设备默认", exact: true });
      const goalScope = page.getByRole("radio", { name: "单个 Goal", exact: true });
      if (!await defaults.isChecked()) throw new Error("Global settings must start at device defaults");
      await page.getByRole("navigation", { name: "机器能力目录" }).waitFor();
      await page.screenshot({ path: resolve(outputDir, "capability-scope-defaults.png"), animations: "disabled" });
      await goalScope.check();
      const target = page.getByRole("combobox", { name: "目标 Goal", exact: true });
      if (await target.inputValue() !== "") throw new Error("Global entry silently chose a Goal");
      if (await page.locator(".personal-capability-detail").count()) throw new Error("An unselected Goal exposed an editor");
      await target.selectOption("product-release");
      await page.getByRole("heading", { level: 2, name: "周期报告", exact: true }).waitFor();
      await page.locator(".personal-capability-supported-scopes").filter({ hasText: "支持配置：此设备默认 · 单个 Goal" }).waitFor();
      if (await page.getByRole("navigation", { name: "Goal 能力目录" }).getByRole("button", { name: /管家/ }).count()) throw new Error("Machine-only steward leaked into Goal catalog");
      await page.getByLabel(/^报告 Profile/u).fill("unsaved-scope-test");
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
      await defaults.check();
      await page.getByRole("navigation", { name: "机器能力目录" }).waitFor();
      if (await page.locator(".personal-capability-preview").count()) throw new Error("A Goal preview crossed into device defaults");
      await goalScope.check();
      await page.getByRole("heading", { level: 2, name: "周期报告", exact: true }).waitFor();
      if (await page.locator(".personal-capability-preview").count()) throw new Error("Returning revived a discarded preview");
      if (!reads.includes("product-release") || !reads.includes("research-monitor")) throw new Error("Target changes did not read their own configuration");
      if (api.goalConfigurationRequests.some((r) => r.phase === "apply") || api.machineConfigurationRequests.some((r) => r.phase === "apply")) throw new Error("Navigation wrote configuration");
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
      if (context.errors.length) throw new Error(context.errors.join(" | "));
      return { coverageEntries: await context.close(), note: "One capability destination; explicit device/Goal scope; fresh target reads; no cross-scope drafts or writes; Goal entry preserved; desktop/mobile verified." };
    } catch (error) { await context.close(); throw error; }
  },
};
