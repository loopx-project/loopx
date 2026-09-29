import { openWorkspacePage } from "./scenario-context.mjs";

// The Chat service answers every Lark request with this 503 when lark-cli is
// not installed. Lark is optional: its absence must not hide unrelated Goal
// context, and Settings must not offer an App setup that can only fail.
const larkCliMissing = {
  error: "Install lark-cli, then restart the LoopX Chat service.",
  error_code: "lark_cli_not_installed",
  ok: false,
};

const larkReads = /\/api\/chat\/lark\/(apps|connections)(\?.*)?$/u;
const answerCliMissing = (route) => route.fulfill({ contentType: "application/json", json: larkCliMissing, status: 503 });

export const larkCliMissingScenario = {
  id: "lark-cli-missing",
  async run({ browser, collectCoverage, url }) {
    const setupRequests = [];
    const context = await openWorkspacePage(browser, url, {
      beforeGoto: async (_api, page) => {
        await page.route(larkReads, answerCliMissing);
        await page.route(/\/api\/chat\/lark\/app-setups/u, (route) => {
          setupRequests.push(route.request().method());
          return route.fulfill({ contentType: "application/json", json: larkCliMissing, status: 503 });
        });
      },
      collectCoverage,
    });
    const { page } = context;
    try {
      await page.locator(".personal-goal-link", { hasText: "LoopX meta" }).click();
      await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: "概览", exact: true }).click();
      await page.getByRole("button", { name: "Goal 信息", exact: true }).click();
      await page.locator(".personal-goal-repository").getByText("loopx-ai/loopx", { exact: true }).waitFor({ state: "visible", timeout: 10_000 });

      // The Goal drawer's own entry opens the connection dialog; its
      // "register another App" option must not reach the setup either.
      await page.getByRole("dialog").getByRole("button", { name: "连接 Lark App", exact: true }).click();
      const registerOption = page.locator('select[aria-label="Lark App"] option[value="__register__"]');
      await registerOption.waitFor({ state: "attached", timeout: 10_000 });
      // The dialog opens before its Lark read fails; the option must settle disabled.
      await page.waitForFunction(() => document.querySelector('select[aria-label="Lark App"] option[value="__register__"]')?.disabled === true, null, { timeout: 10_000 })
        .catch(() => { throw new Error("The connection dialog still offered Lark App setup without lark-cli"); });
      if (await page.locator("#new-lark-app-title").count()) throw new Error("Lark App setup opened without lark-cli");
      await page.goto(url, { waitUntil: "networkidle" });
      await page.getByTestId("personal-goal-home").waitFor({ state: "visible" });

      await page.getByRole("button", { name: "设置", exact: true }).click();
      await page.getByRole("button", { name: "Lark", exact: true }).click();
      await page.getByText("未发现 lark-cli。请先安装 lark-cli，然后重新启动 LoopX。", { exact: true }).first().waitFor({ state: "visible", timeout: 10_000 });
      await page.locator(".personal-lark-tabs").getByRole("button", { name: /^Lark Apps/u }).click();
      if (!await page.getByRole("button", { name: "新建 Lark App" }).isDisabled()) {
        throw new Error("New Lark App stayed available while lark-cli is missing");
      }
      if (setupRequests.length) throw new Error(`Lark App setup was requested without lark-cli: ${setupRequests.join(",")}`);

      // With lark-cli available again the same entries stay usable.
      await page.unroute(larkReads, answerCliMissing);
      await page.reload({ waitUntil: "networkidle" });
      await page.getByTestId("personal-goal-home").waitFor({ state: "visible" });
      await page.getByRole("button", { name: "设置", exact: true }).click();
      await page.getByRole("button", { name: "Lark", exact: true }).click();
      await page.locator(".personal-lark-tabs").getByRole("button", { name: /^Lark Apps/u }).click();
      if (await page.getByRole("button", { name: "新建 Lark App" }).isDisabled()) {
        throw new Error("New Lark App stayed disabled with lark-cli available");
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
