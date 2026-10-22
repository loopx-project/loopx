import assert from "node:assert/strict";
import {resolve} from "node:path";

import {outputDir} from "./fixture.mjs";
import {openWorkspacePage} from "./scenario-context.mjs";

export const dshHostReadinessScenario = {
  id: "dsh-host-readiness",
  async run({browser, collectCoverage, url}) {
    const binding = {
      schema_version: "manager_channel_binding_v0",
      executor_endpoint: "dsh",
      executor_endpoint_source: "machine_configuration",
      executor_kind: "managed",
      model: "deepseek-flash",
      model_source: "machine_configuration",
      reasoning_effort: "high",
      credential_env_var: "DEEPSEEK_API_KEY",
      operator_credential_configured: false,
      available: false,
      unavailable_reason: "dsh_runtime_unavailable",
    };
    const adapter = (available, unavailableReason) => ({
      agent_id: "dsh",
      display_name: "DeepSeek Harness (managed)",
      adapter_kind: "deepseek_harness_segment",
      available,
      streaming: false,
      resume: true,
      interrupt: false,
      execution_profile: "deepseek-flash@high",
      unavailable_reason: unavailableReason,
    });
    const session = await openWorkspacePage(browser, url, {
      apiOptions: {managerChannelBinding: binding},
      collectCoverage,
      viewport: {width: 1440, height: 900},
      beforeGoto: (api) => {
        api.runtimeAgentsOverride = [adapter(false, "dsh_runtime_unavailable")];
        api.transformStatus = (fixture) => {
          const goal = fixture.run_history.goals.find((item) => item.id === "product-release");
          if (goal) {
            goal.coordination = {
              ...(goal.coordination ?? {}),
              thread_agent_bindings: [{
                agent_id: "free-agent-dsh",
                host_surface: "deepseek-harness-native",
                thread_id: "dsh-session-fixture",
              }],
            };
          }
        };
      },
    });
    const {api, page} = session;
    try {
      await page.getByRole("button", {name: "调整管家模型与思考深度", exact: true}).click();
      const readiness = page.getByTestId("dsh-host-readiness");
      await readiness.waitFor({state: "visible", timeout: 15_000});

      const managed = page.getByTestId("dsh-managed-readiness");
      await managed.getByText("缺少 DSH runtime", {exact: true}).waitFor();
      const managedText = await managed.innerText();
      assert.match(managedText, /deepseek-flash@high/, "The managed profile remains visible while unavailable");
      assert.equal(await managed.getByRole("link", {name: "检查安装"}).count(), 1);
      assert.equal(await page.getByTestId("dsh-native-binding").getByText("已绑定 1 个 Goal", {exact: true}).count(), 1);
      await page.screenshot({path: resolve(outputDir, "dsh-host-readiness-unavailable.png"), animations: "disabled"});

      api.runtimeAgentsOverride = [adapter(true, null)];
      await readiness.getByRole("button", {name: "重新检查 DSH 状态"}).click();
      await managed.getByText("已就绪", {exact: true}).waitFor();
      assert.equal(await managed.getByRole("link", {name: "检查安装"}).count(), 0);
      await page.screenshot({path: resolve(outputDir, "dsh-host-readiness-ready.png"), animations: "disabled"});

      api.runtimeAgentsOverride = [adapter(false, "operator_credential_unconfigured")];
      await readiness.getByRole("button", {name: "重新检查 DSH 状态"}).click();
      const credential = managed.getByRole("button", {name: "配置模型凭据"});
      await credential.waitFor();
      await page.screenshot({path: resolve(outputDir, "dsh-host-readiness-credential.png"), animations: "disabled"});
      await credential.click();
      await page.getByTestId("operator-credential-settings").waitFor({state: "visible"});
      assert.equal(await page.getByRole("heading", {name: "模型 Provider 配置", exact: true}).count(), 1);
      assert.equal(await page.getByLabel("API Key").inputValue(), "", "The password field never receives a readback secret");

      await page.setViewportSize({width: 390, height: 844});
      await page.locator(".personal-settings-mobile-category select").selectOption("steward");
      await page.getByTestId("dsh-host-readiness").waitFor({state: "visible"});
      if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)) {
        throw new Error("DSH readiness overflows the narrow settings viewport");
      }
      await page.screenshot({path: resolve(outputDir, "dsh-host-readiness-mobile.png"), animations: "disabled"});
      if (session.errors.length) throw new Error(session.errors.join(" | "));
      return {
        coverageEntries: await session.close(),
        note: "Managed runtime, credential and native Session binding remain distinct; repairs are actionable; no secret is read back",
      };
    } catch (error) {
      await session.close().catch(() => {});
      throw error;
    }
  },
};
