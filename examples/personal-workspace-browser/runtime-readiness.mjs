import assert from "node:assert/strict";
import { resolve } from "node:path";
import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

// A machine with no Agent runtime must say so in place, name what each
// runtime needs, and let the owner re-check after installing, without the
// page installing or granting anything. The service's own readiness fields
// drive the copy; the home notice and the composer line share one rule.

const missing = [
  { agent_id: "codex", display_name: "Codex", adapter_kind: "codex_app_server", available: false, streaming: true, resume: true, interrupt: true, executable: "codex", unavailable_reason: "executable_not_found" },
  { agent_id: "claude-code", display_name: "Claude Code", adapter_kind: "claude_code_cli", available: false, streaming: true, resume: true, interrupt: true, executable: "claude", unavailable_reason: "executable_not_found" },
  { agent_id: "anthropic-api", display_name: "Claude API", adapter_kind: "anthropic_messages_api", available: false, streaming: false, resume: true, interrupt: false, environment_variable: "ANTHROPIC_API_KEY", unavailable_reason: "api_key_not_set" },
];
const repaired = [{ ...missing[0], available: true, unavailable_reason: undefined }, missing[1], missing[2]];

export const runtimeReadinessScenario = {
  id: "runtime-readiness",
  async run({ browser, collectCoverage, url }) {
    const session = await openWorkspacePage(browser, url, { collectCoverage, viewport: { width: 1440, height: 900 },
      beforeGoto: (api) => { api.runtimeAgentsOverride = missing; } });
    const { api, page } = session;
    try {
      const notice = page.getByTestId("runtime-readiness");
      await notice.waitFor({ state: "visible", timeout: 15_000 });
      const text = await notice.innerText();
      assert.match(text, /还没有可用的 Agent 运行环境/);
      assert.match(text, /Codex：未在 PATH 中找到命令「codex」/, "The notice names the missing command from the service's fields");
      assert.match(text, /Claude API：未设置环境变量 ANTHROPIC_API_KEY/);
      assert.equal(await notice.getByRole("link", { name: "安装指引" }).count(), 1);
      await page.screenshot({ path: resolve(outputDir, "runtime-readiness-none.png"), animations: "disabled" });

      // Re-check after the owner installed Codex: the notice withdraws and nothing was sent.
      api.runtimeAgentsOverride = repaired;
      await notice.getByRole("button", { name: "重新检查" }).click();
      await notice.waitFor({ state: "hidden", timeout: 15_000 });
      assert.equal(await page.getByTestId("runtime-readiness").count(), 0, "A repaired runtime clears the notice");

      // With a Goal open and execution runtimes missing, status-only remains usable.
      api.runtimeAgentsOverride = missing;
      await page.getByRole("button", { name: "刷新状态", exact: true }).click();
      await page.locator(".personal-goal-row").first().locator(".personal-goal-link").click();
      await page.getByTestId("goal-vitals").waitFor({ state: "visible", timeout: 15_000 });
      await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: "对话", exact: true }).click();
      const line = page.getByTestId("runtime-readiness-line");
      await line.waitFor({ state: "visible", timeout: 15_000 });
      assert.match(await line.innerText(), /Agent 执行尚未就绪.*仅查状态/);
      assert.doesNotMatch(await line.innerText(), /无法发送消息/);
      await page.screenshot({ path: resolve(outputDir, "runtime-readiness-composer.png"), animations: "disabled" });
      api.runtimeAgentsOverride = repaired;
      await line.getByRole("button", { name: "重新检查" }).click();
      await line.waitFor({ state: "hidden", timeout: 15_000 });
      assert.equal(api.turnRequests.length, 0, "Readiness never sends anything");
      return { coverageEntries: await session.close() };
    } catch (error) {
      await session.close().catch(() => {});
      throw error;
    }
  },
};
