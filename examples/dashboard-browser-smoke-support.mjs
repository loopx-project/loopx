import { spawn } from "node:child_process";
import { existsSync, readdirSync } from "node:fs";
import { once } from "node:events";
import { rm } from "node:fs/promises";
import { createRequire } from "node:module";
import { homedir } from "node:os";
import { basename, resolve } from "node:path";

const require = createRequire(import.meta.url);

export function loadPlaywright() {
  const candidates = [
    process.env.LOOPX_PLAYWRIGHT_PACKAGE,
    resolve(homedir(), ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright"),
  ].filter(Boolean);

  try {
    return require("playwright");
  } catch {
    // Try the configured or bundled runtime below.
  }

  for (const candidate of candidates) {
    if (!existsSync(candidate)) {
      continue;
    }
    try {
      return require(candidate);
    } catch {
      // Keep looking for a usable runtime.
    }
  }

  throw new Error("Playwright package not found; install playwright or set LOOPX_PLAYWRIGHT_PACKAGE");
}

// Playwright names the shell `chrome-headless-shell` on POSIX and
// `chrome-headless-shell.exe` on Windows.
const CHROME_HEADLESS_SHELL_NAMES = new Set(["chrome-headless-shell", "chrome-headless-shell.exe"]);

function findChromeHeadlessShell(root) {
  if (!existsSync(root)) {
    return null;
  }
  const matches = [];
  const pending = [root];
  while (pending.length > 0) {
    const current = pending.pop();
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      const path = resolve(current, entry.name);
      if (entry.isDirectory()) {
        pending.push(path);
      } else if (entry.isFile() && CHROME_HEADLESS_SHELL_NAMES.has(entry.name)) {
        matches.push(path);
      }
    }
  }
  return matches.sort().at(-1) ?? null;
}

export async function launchBrowser(chromium) {
  const configuredPath = process.env.LOOPX_CHROME_HEADLESS_SHELL;
  const playwrightPath = chromium.executablePath();
  const executablePath = [configuredPath, playwrightPath]
    .find((candidate) => candidate && CHROME_HEADLESS_SHELL_NAMES.has(basename(candidate)) && existsSync(candidate))
    ?? findChromeHeadlessShell(resolve(homedir(), ".cache/hyperframes/chrome"))
    ?? findChromeHeadlessShell(resolve(homedir(), ".cache/ms-playwright"))
    ?? findChromeHeadlessShell(resolve(homedir(), "Library/Caches/ms-playwright"))
    // Windows installs the browser cache under %LOCALAPPDATA% instead.
    ?? (process.env.LOCALAPPDATA
      ? findChromeHeadlessShell(resolve(process.env.LOCALAPPDATA, "ms-playwright"))
      : null);
  if (!executablePath) {
    throw new Error("chrome-headless-shell not found; set LOOPX_CHROME_HEADLESS_SHELL");
  }
  return chromium.launch({ executablePath, headless: true });
}

export async function startViteDashboardServer({ dashboardDir, port }) {
  const nodeBin = process.env.LOOPX_NODE_BIN || process.execPath;
  // HTTP from the selected port could belong to a different Vite process.
  // Confirm the bind over the channel owned by this child before browsing.
  const entry = `
import { createServer } from "vite";
const port = Number(process.argv[1]);
const vite = await createServer({
  server: { host: "127.0.0.1", port, strictPort: true },
  forceOptimizeDeps: true,
});
await vite.listen();
process.send(vite.httpServer.address().port);
`;
  const server = spawn(nodeBin, ["--input-type=module", "--eval", entry, String(port)], {
    cwd: dashboardDir,
    env: { ...process.env },
    stdio: ["ignore", "ignore", "pipe", "ipc"],
  });
  let diagnostic = "";
  server.stderr.on("data", chunk => { diagnostic = (diagnostic + chunk).slice(-8000); });
  const startup = new AbortController();
  let timer;
  try {
    const [boundPort] = await Promise.race([
      once(server, "message", { signal: startup.signal }),
      once(server, "close", { signal: startup.signal }).then(([code, signal]) => {
        throw new Error(`Dashboard Vite server exited (${code ?? signal}) before binding port ${port}: ${diagnostic.trim()}`);
      }),
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(`Dashboard Vite server did not confirm port ${port} within 20 seconds`)), 20_000);
      }),
    ]);
    if (boundPort !== port) throw new Error(`Dashboard Vite server bound port ${boundPort}; expected ${port}`);
    return server;
  } catch (error) {
    await stopServer(server);
    throw error;
  } finally {
    clearTimeout(timer);
    startup.abort();
  }
}

export async function waitForHttp(url, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      if ((await fetch(url)).ok) return;
    } catch {
      // Retry until the bounded deadline.
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 200));
  }
  throw new Error(`Timed out waiting for ${url}`);
}

async function stopServer(server) {
  if (!server?.pid || server.exitCode !== null || server.signalCode !== null) {
    return;
  }
  const exited = once(server, "exit");
  server.kill("SIGTERM");
  let timer;
  const stopped = await Promise.race([
    exited.then(() => true),
    new Promise((resolveTimeout) => { timer = setTimeout(() => resolveTimeout(false), 5_000); }),
  ]).finally(() => clearTimeout(timer));
  if (!stopped && server.exitCode === null && server.signalCode === null) {
    const forceExited = once(server, "exit");
    server.kill("SIGKILL");
    await forceExited;
  }
}

export async function cleanupBrowserSmoke({ browser, fixturePaths, server }) {
  const errors = [];
  try {
    if (browser) {
      await browser.close();
    }
  } catch (error) {
    errors.push(error);
  }
  try {
    await stopServer(server);
  } catch (error) {
    errors.push(error);
  }
  for (const fixturePath of fixturePaths) {
    try {
      await rm(fixturePath, { force: true });
    } catch (error) {
      errors.push(error);
    }
  }
  if (errors.length > 0) {
    throw new AggregateError(errors, "Browser smoke cleanup failed");
  }
}
