import { openWorkspacePage } from "./scenario-context.mjs";

// The status source answers while the Chat execution service does not. The
// workspace must say so in one voice and keep the conversation on screen.
export const executionServiceOfflineScenario = {
  id: "execution-service-offline",
  async run({ browser, collectCoverage, url }) {
    const context = await openWorkspacePage(browser, url, {
      beforeGoto: async (_api, page) => {
        await page.route(/\/api\/chat\/sessions(\?.*)?$/u, (route) => (route.request().method() === "GET"
          ? route.fulfill({ contentType: "application/json", json: { error: "Chat service unavailable", ok: false }, status: 503 })
          : route.fallback()));
      },
      collectCoverage,
      viewport: { width: 1440, height: 900 },
    });
    const { page } = context;
    try {
      await page.locator(".personal-goal-link", { hasText: "Product Release" }).click();
      await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
      const notice = page.locator(".personal-workspace-main > .personal-service-notice");
      await notice.waitFor({ state: "visible", timeout: 20_000 });
      const source = await page.locator(".personal-status-source-meta > span").innerText();
      if (source.trim() !== "已连接 · 执行服务不可用") {
        throw new Error(`The source indicator disagreed with the execution notice: ${source}`);
      }
      const layout = await page.evaluate(() => ({
        composerBottom: document.querySelector(".personal-channel-composer")?.getBoundingClientRect().bottom ?? Infinity,
        overflow: document.scrollingElement.scrollHeight - window.innerHeight,
        viewport: window.innerHeight,
      }));
      if (layout.overflow > 1 || layout.composerBottom > layout.viewport) {
        throw new Error(`The execution notice pushed the workspace below the viewport: ${JSON.stringify(layout)}`);
      }
    } finally {
      await context.close();
    }
    return {
      coverageEntries: context.coverageEntries,
      note: "An unavailable execution service degrades the source indicator and its notice keeps the conversation within the viewport.",
    };
  },
};
