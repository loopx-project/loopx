import { resolve } from "node:path";

import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

export const automationCadenceScenario = {
  id: "automation-cadence",
  async run({ browser, collectCoverage, url }) {
    let revision = 0;
    let rules = [];
    const context = await openWorkspacePage(browser, url, {
      collectCoverage,
      beforeGoto: async (_api, page) => {
        await page.route("**/api/chat/automation-cadence**", async (route) => {
          const request = route.request();
          const parsed = new URL(request.url());
          const body = request.method() === "POST" ? request.postDataJSON() : null;
          const goalId = body?.goal_id ?? parsed.searchParams.get("goal_id");
          const agentId = body?.agent_id ?? parsed.searchParams.get("agent_id") ?? null;
          const automationId = body?.automation_id ?? parsed.searchParams.get("automation_id") ?? null;
          const applicable = (rows) => rows.filter((rule) => rule.agent_id === null
            || (rule.agent_id === agentId && (rule.automation_id === null || rule.automation_id === automationId)));
          if (body && body.expected_revision !== revision) {
            await route.fulfill({ status: 409, json: { error: "configuration revision conflict" } });
            return;
          }
          const prior = rules.find((rule) => rule.agent_id === agentId && rule.automation_id === automationId);
          if (body && prior && body.min_interval_minutes < prior.min_interval_minutes && !body.approve_reduction) {
            await route.fulfill({ status: 400, json: { error: "reduction requires explicit owner approval" } });
            return;
          }
          if (body) {
            const changed = [...rules.filter((rule) => rule !== prior), {
              agent_id: agentId, automation_id: automationId, min_interval_minutes: body.min_interval_minutes,
            }];
            if (parsed.pathname.endsWith("/apply")) {
              rules = changed;
              revision += 1;
            }
            const sources = applicable(changed);
            await route.fulfill({ status: 200, json: {
              ok: true, schema_version: "chat_automation_cadence_v0", goal_id: goalId,
              agent_id: agentId, automation_id: automationId,
              configuration_revision: revision + (parsed.pathname.endsWith("/preview") ? 1 : 0),
              min_interval_minutes: Math.max(0, ...sources.map((rule) => rule.min_interval_minutes)),
              enabled: sources.some((rule) => rule.min_interval_minutes > 0),
              enforcement: "scheduler_recommendation", pre_model_admission: "not_qualified",
              sources, preview_revision: "fixture-preview", written: parsed.pathname.endsWith("/apply"),
              readback_verified: parsed.pathname.endsWith("/apply"),
            } });
            return;
          }
          const sources = applicable(rules);
          await route.fulfill({ status: 200, json: {
            ok: true, schema_version: "chat_automation_cadence_v0", goal_id: goalId,
            agent_id: agentId, automation_id: automationId, configuration_revision: revision,
            min_interval_minutes: Math.max(0, ...sources.map((rule) => rule.min_interval_minutes)),
            enabled: sources.some((rule) => rule.min_interval_minutes > 0),
            enforcement: "scheduler_recommendation", pre_model_admission: "not_qualified", sources,
          } });
        });
      },
    });
    const { page, close, checkpointCoverage, errors } = context;
    try {
      await page.locator(".personal-goal-link", { hasText: "Product Release" }).click();
      await page.getByRole("button", { name: "Goal 设置", exact: true }).click();
      const target = page.locator(".personal-settings-goal-target");
      await page.getByRole("combobox", { name: "目标 Goal", exact: true }).waitFor();
      await page.getByRole("button", { name: "能力中心" }).click();
      await page.getByRole("radio", { name: "此设备默认", exact: true }).check();
      if (await target.count()) throw new Error("Machine settings retained a Goal-specific target");
      await page.getByRole("button", { name: "自动执行间隔" }).click();
      await target.getByText("Product Release", { exact: true }).waitFor();
      const panel = page.getByRole("region", { name: "自动执行间隔" });
      await panel.getByText("0 分钟", { exact: true }).first().waitFor();
      if (!await panel.getByText("App 定时触发到启动前钩子的拦截尚未验证。", { exact: false }).count()) {
        throw new Error("Cadence settings overstated App enforcement");
      }
      await panel.getByLabel("最短间隔（分钟）").fill("60");
      await panel.getByLabel("所有者指令或原因").fill("Owner requested hourly automatic runs");
      await panel.getByRole("button", { name: "预览变更", exact: true }).click();
      await panel.getByText("变更后生效下限：60 分钟").waitFor();
      if (revision !== 0) throw new Error("Preview wrote the policy");
      await panel.getByLabel("最短间隔（分钟）").fill("61");
      if (await panel.getByRole("button", { name: "应用已预览变更" }).isEnabled()) {
        throw new Error("Editing the policy retained a stale preview");
      }
      await panel.getByLabel("最短间隔（分钟）").fill("60");
      await panel.getByRole("button", { name: "预览变更", exact: true }).click();
      await page.screenshot({ path: resolve(outputDir, "desktop-automation-cadence-preview.png"), fullPage: false, animations: "disabled" });
      await panel.getByRole("button", { name: "应用已预览变更" }).click();
      await panel.getByText("已保存并从 quota 策略读回。").waitFor();
      if (revision !== 1) throw new Error("Apply did not write the reviewed policy");
      await panel.getByLabel("最短间隔（分钟）").fill("30");
      await panel.getByLabel("所有者指令或原因").fill("Owner explicitly lowers cadence");
      if (await panel.getByRole("button", { name: "预览变更", exact: true }).isEnabled()) {
        throw new Error("Reducing a floor did not require explicit owner intent");
      }
      await panel.getByRole("checkbox", { name: "我明确要降低或移除此层现有的间隔下限。" }).check();
      if (!await panel.getByRole("button", { name: "预览变更", exact: true }).isEnabled()) {
        throw new Error("Explicit reduction remained unavailable");
      }
      await panel.getByRole("radio", { name: "单个 Agent" }).check();
      await panel.getByText("继承上层 60 分钟；本层未设置。").waitFor();
      await panel.getByText("60 分钟", { exact: true }).first().waitFor();
      await page.setViewportSize({ width: 390, height: 844 });
      await page.screenshot({ path: resolve(outputDir, "mobile-automation-cadence-inherited.png"), fullPage: false, animations: "disabled" });
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
      if (overflow) throw new Error("Cadence settings overflowed the mobile viewport");
      if (errors.length) throw new Error(`Browser errors: ${errors.join(" | ")}`);
      await checkpointCoverage();
      return { coverageEntries: await close(), note: "Goal policy preview, apply/readback, Agent inheritance, App boundary and desktop/mobile layouts verified." };
    } catch (error) {
      await close();
      throw error;
    }
  },
};
