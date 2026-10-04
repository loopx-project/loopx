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

    // The provider URL arrives asynchronously. Browser popups retain the click
    // gesture; native hosts return no popup proxy and must receive the actual
    // provider URL, never a placeholder LoopX workspace. No OAuth grant is made.
    for (const desktop of [false, true]) {
      let polls = 0;
      const verificationUrl = "https://example.org/verify?request=synthetic";
      const setup = await openWorkspacePage(browser, url, {
        collectCoverage,
        beforeGoto: async (_api, setupPage) => {
          await setupPage.addInitScript(({ desktop }) => {
            if (desktop) window.__TAURI__ = { core: {} };
            window.setupDestinations = [];
            window.open = (destination) => {
              window.setupDestinations.push(String(destination));
              if (desktop) return null;
              return {
                closed: false,
                close() {},
                location: {
                  set href(value) { window.setupDestinations.push(value); },
                },
              };
            };
          }, { desktop });
          await setupPage.route(/\/api\/chat\/lark\/app-setups/u, (route) => {
            const method = route.request().method();
            if (method === "GET") polls += 1;
            return route.fulfill({
              contentType: "application/json",
              json: {
                ok: true,
                app_ref: "synthetic-app",
                error: null,
                setup_id: "synthetic-setup",
                status: method === "POST" ? "starting" : method === "DELETE" ? "cancelled" : "waiting_for_feishu",
                verification_url: method === "GET" ? verificationUrl : null,
              },
            });
          });
        },
      });
      try {
        await setup.page.getByRole("button", { name: "设置", exact: true }).click();
        await setup.page.getByRole("button", { name: "Lark", exact: true }).click();
        await setup.page.locator(".personal-lark-tabs").getByRole("button", { name: /^Lark Apps/u }).click();
        await setup.page.getByRole("button", { name: "新建 Lark App", exact: true }).click();
        const workspaceUrl = setup.page.url();
        await setup.page.getByRole("button", { name: "在飞书中继续", exact: true }).click();
        await setup.page.locator(`a[href="${verificationUrl}"]`).waitFor({ state: "visible" });
        await setup.page.waitForResponse((response) => response.request().method() === "GET" && response.url().endsWith("/app-setups/synthetic-setup"));
        if (polls < 2) throw new Error("Setup did not observe the repeated provider URL");
        const destinations = await setup.page.evaluate(() => window.setupDestinations);
        const expected = desktop ? [verificationUrl] : [workspaceUrl, verificationUrl];
        if (JSON.stringify(destinations) !== JSON.stringify(expected)) {
          throw new Error(`Wrong ${desktop ? "native" : "browser"} setup destinations: ${JSON.stringify(destinations)}`);
        }
        await setup.page.getByRole("button", { name: "取消", exact: true }).click();
        await setup.page.locator("#new-lark-app-title").waitFor({ state: "hidden" });
      } finally {
        await setup.close();
      }
      context.coverageEntries.push(...setup.coverageEntries);
    }
    return {
      coverageEntries: context.coverageEntries,
      note: "Missing CLI recovery and asynchronous setup destinations in browser/native hosts, without OAuth activation.",
    };
  },
};
