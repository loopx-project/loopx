import { installApi } from "./fixture.mjs";

// A disposable backend has a different port from the browser fixture. Rebase
// same-origin requests with it; foreign origins must still reach the real guard.
export function fetchWorkspaceBackend(route, backendUrl, { pathname } = {}) {
  const request = route.request();
  const source = new URL(request.url());
  const target = new URL(backendUrl);
  target.pathname = pathname ?? source.pathname;
  target.search = source.search;
  const headers = { ...request.headers(), host: target.host };
  if (headers.origin === source.origin) headers.origin = target.origin;
  return route.fetch({ url: target.href, headers });
}

export async function openWorkspacePage(
  browser,
  url,
  {
    apiOptions,
    beforeGoto,
    collectCoverage = false,
    gotoWaitUntil = "networkidle",
    viewport = { width: 1512, height: 982 },
    isMobile = false,
  } = {},
) {
  const page = await browser.newPage({ viewport, isMobile });
  const errors = [];
  const coverageEntries = [];
  let coverageRunning = false;
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") {
      const source = message.location().url;
      errors.push(`${message.text()}${source ? ` (${source})` : ""}`);
    }
  });
  if (collectCoverage) {
    await page.coverage.startJSCoverage({ resetOnNavigation: false });
    coverageRunning = true;
  }
  const api = await installApi(page, apiOptions);
  await beforeGoto?.(api, page);
  await page.goto(url, { waitUntil: gotoWaitUntil });
  try {
    await page.getByTestId("personal-goal-home").waitFor({ state: "visible", timeout: 15_000 });
  } catch (error) {
    throw new Error(
      `${error.message}; url=${page.url()}; errors=${errors.join(" | ")}; body=${(await page.locator("body").innerText()).slice(0, 1000)}`,
    );
  }

  async function checkpointCoverage() {
    if (!coverageRunning) return;
    coverageEntries.push(...await page.coverage.stopJSCoverage());
    await page.coverage.startJSCoverage({ resetOnNavigation: false });
  }

  async function close() {
    if (coverageRunning) {
      coverageEntries.push(...await page.coverage.stopJSCoverage());
      coverageRunning = false;
    }
    // A route handler still proxying a request when the page goes away must
    // not surface as an uncaught rejection that takes the whole smoke down.
    await page.unrouteAll({ behavior: "ignoreErrors" }).catch(() => {});
    await page.close();
    return coverageEntries;
  }

  return { api, checkpointCoverage, close, coverageEntries, errors, page };
}
