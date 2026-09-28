import { openWorkspacePage } from "./scenario-context.mjs";

// The Chat service answers every Lark request with this 503 when lark-cli is
// not installed. Lark is optional: its absence must not hide unrelated Goal
// context, and Settings must not offer an App setup that can only fail.
const larkCliMissing = {
  error: "Install lark-cli, then restart the LoopX Chat service.",
  error_code: "lark_cli_not_installed",
  ok: false,
};

export const larkCliMissingScenario = {
  id: "lark-cli-missing",
  async run({ browser, collectCoverage, url }) {
    const context = await openWorkspacePage(browser, url, {
      beforeGoto: async (_api, page) => {
        await page.route(/\/api\/chat\/lark\/(apps|connections)(\?.*)?$/u, (route) => route.fulfill({
          contentType: "application/json",
          json: larkCliMissing,
          status: 503,
        }));
      },
      collectCoverage,
    });
    const { page } = context;
    try {
      await page.locator(".personal-goal-link", { hasText: "LoopX meta" }).click();
      await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: "概览", exact: true }).click();
      await page.getByRole("button", { name: "Goal 信息", exact: true }).click();
      await page.locator(".personal-goal-repository").getByText("loopx-ai/loopx", { exact: true }).waitFor({ state: "visible", timeout: 10_000 });
      await page.keyboard.press("Escape");

      await page.getByRole("button", { name: "设置", exact: true }).click();
      await page.getByRole("button", { name: "Lark", exact: true }).click();
      await page.getByText("未发现 lark-cli。请先安装 lark-cli，然后重新启动 LoopX。", { exact: true }).first().waitFor({ state: "visible", timeout: 10_000 });
      await page.locator(".personal-lark-tabs").getByRole("button", { name: /^Lark Apps/u }).click();
      if (!await page.getByRole("button", { name: "新建 Lark App" }).isDisabled()) {
        throw new Error("New Lark App stayed available while lark-cli is missing");
      }
    } finally {
      await context.close();
    }
    return {
      coverageEntries: context.coverageEntries,
      note: "A missing lark-cli keeps Goal repository context and disables Lark App setup with its reason.",
    };
  },
};
