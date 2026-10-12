#!/usr/bin/env node
import { spawn, spawnSync } from "node:child_process";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { existsSync, statSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { resolve } from "node:path";
import net from "node:net";
import { fileURLToPath } from "node:url";
import { resolveTestPython } from "../scripts/test-python.mjs";

const repoRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));
const pwcli = process.env.PWCLI ?? resolve(homedir(), ".codex/skills/playwright/scripts/playwright_cli.sh");
const goalId = "reward-browser-smoke-goal";
const sessionName = `ghrw${process.pid}`;

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function freePort() {
  return new Promise((resolvePort, reject) => {
    const server = net.createServer();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() => resolvePort(address.port));
    });
  });
}

function runPw(args, { allowFailure = false, timeout = 30000, timeoutMs } = {}) {
  const executable = (statSync(pwcli).mode & 0o111) !== 0;
  const command = executable ? pwcli : "bash";
  const commandArgs = executable ? args : [pwcli, ...args];
  const result = spawnSync(command, commandArgs, {
    cwd: repoRoot,
    encoding: "utf8",
    timeout: timeoutMs ?? timeout,
    env: {
      ...process.env,
      PLAYWRIGHT_CLI_SESSION: sessionName,
    },
  });
  if (allowFailure) {
    return result;
  }
  if (result.status !== 0) {
    throw new Error(`playwright_cli ${args.join(" ")} failed:\n${result.stderr || result.stdout}`);
  }
  return result.stdout;
}

function parseRawEvalOutput(output) {
  const match = output.match(/Raw eval result:\s*([\s\S]*?)\nArtifacts:/);
  return (match ? match[1] : output).trim();
}

function evalRaw(expression, options = {}) {
  return parseRawEvalOutput(runPw(["--raw", "eval", expression], options));
}

function navigateTo(url) {
  const gotoResult = runPw(["goto", url], { allowFailure: true, timeoutMs: 5000 });
  if (gotoResult.status === 0 && !gotoResult.error) {
    return;
  }
  runPw(["--raw", "eval", `() => {
    window.location.href = ${JSON.stringify(url)};
    return window.location.href;
  }`], { allowFailure: true, timeoutMs: 5000 });
}

function forceKillBrowserSession() {
  spawnSync("pkill", ["-f", `cliDaemon.js ${sessionName}`], {
    encoding: "utf-8",
    timeout: 5000,
  });
}

function startBrowserSession() {
  let lastProbe;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    runPw(["open", "about:blank"], { allowFailure: true, timeoutMs: 5000 });
    lastProbe = runPw(["eval", "() => location.href"], { allowFailure: true, timeoutMs: 10000 });
    if (lastProbe.status === 0 && !lastProbe.error) {
      return;
    }
  }
  throw new Error([
    "Unable to start Playwright CLI browser session.",
    lastProbe?.error?.message,
    lastProbe?.stdout,
    lastProbe?.stderr,
  ].filter(Boolean).join("\n"));
}

async function waitForHttp(url, label, { timeoutMs = 20000 } = {}) {
  const deadline = Date.now() + timeoutMs;
  let lastError = "";
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) {
        return;
      }
      lastError = `${response.status} ${response.statusText}`;
    } catch (error) {
      lastError = error.message;
    }
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 250));
  }
  throw new Error(`${label} did not become ready at ${url}: ${lastError}`);
}

function latestRewardRun(status) {
  const goal = status.run_history?.goals?.find((candidate) => candidate.id === goalId);
  return {
    goal,
    latestRun: goal?.latest_runs?.[0],
  };
}

async function waitForRewardStatus(statusBase, { timeoutMs = 20000 } = {}) {
  const deadline = Date.now() + timeoutMs;
  let lastStatus;
  let lastError;
  while (Date.now() < deadline) {
    try {
      lastStatus = await (await fetch(`${statusBase}/status.json`)).json();
      const { latestRun } = latestRewardRun(lastStatus);
      if (latestRun?.human_reward) {
        return lastStatus;
      }
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 250));
  }
  const { latestRun } = lastStatus ? latestRewardRun(lastStatus) : {};
  throw new Error([
    "Timed out waiting for status.json to expose appended human_reward.",
    lastError?.message,
    latestRun ? `Latest run:\n${JSON.stringify(latestRun, null, 2)}` : null,
  ].filter(Boolean).join("\n"));
}

async function writeFixture(root) {
  const projectRoot = resolve(root, "project");
  const runtimeRoot = resolve(root, "runtime");
  const stateFile = resolve(projectRoot, "ACTIVE_GOAL_STATE.md");
  const goalsDir = resolve(projectRoot, "goals", goalId);
  const runDir = resolve(runtimeRoot, "goals", goalId, "runs");
  await mkdir(goalsDir, { recursive: true });
  await mkdir(runDir, { recursive: true });

  await writeFile(
    stateFile,
    `# Active Goal State\n\n## Next Action\n\n- Record one human reward from the dashboard.\n\n## Progress Ledger\n\n- 2026-01-02: fixture awaiting human reward.\n`,
  );

  const runJson = resolve(runDir, "run.json");
  const runMd = resolve(runDir, "run.md");
  const runPayload = {
    goal_id: goalId,
    generated_at: "2026-01-02T00:00:00+00:00",
    status: "waiting_on_human_reward",
    classification: "controller_readiness",
    recommended_action: "Record a human reward before controller decision advice.",
    next_action: "Use the dashboard reward panel to append the judgment.",
    missing_gates: ["human_reward_capture"],
    controller_readiness: {
      classification: "controller_readiness",
      controller_stage: "needs_human_reward",
      decision_advisor_ready: false,
      write_controller_ready: false,
      missing_gates: ["human_reward_capture"],
      review_judgment: "Operator judgment is required before controller decision advice.",
      next_handoff_condition: "Record one run-bound human reward event.",
    },
    json_path: runJson,
    markdown_path: runMd,
  };
  await writeFile(runJson, `${JSON.stringify(runPayload, null, 2)}\n`);
  await writeFile(runMd, "# Reward Browser Smoke Run\n\nOperator judgment is required before controller decision advice.\n");
  await writeFile(resolve(runDir, "index.jsonl"), `${JSON.stringify(runPayload)}\n`);

  const registry = {
    version: 1,
    runtime_root: runtimeRoot,
    goals: [
      {
        id: goalId,
        name: "Reward browser smoke goal",
        domain: "reward-browser-smoke",
        repo: projectRoot,
        state_file: stateFile,
        project_root: projectRoot,
        cwd: projectRoot,
        adapter: { kind: "generic_project_goal_v0", status: "connected-read-only" },
      },
    ],
  };
  const registryPath = resolve(root, "registry.json");
  await writeFile(registryPath, `${JSON.stringify(registry, null, 2)}\n`);
  return { registryPath, runtimeRoot };
}

function startProcess(command, args, options) {
  const child = spawn(command, args, {
    cwd: repoRoot,
    stdio: ["ignore", "pipe", "pipe"],
    ...options,
  });
  child.stderr.setEncoding("utf8");
  child.stdout.setEncoding("utf8");
  return child;
}

function clickButton(text, { timeout = 10000 } = {}) {
  const expression = `(() => {
    const needle = ${JSON.stringify(text)};
    const button = Array.from(document.querySelectorAll("button")).find((candidate) =>
      candidate.innerText && candidate.innerText.includes(needle)
    );
    if (!button) return false;
    button.click();
    return true;
  })()`;
  const deadline = Date.now() + timeout;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const clicked = evalRaw(expression, { timeoutMs: 5000 });
      if (clicked === "true") {
        return;
      }
    } catch (error) {
      lastError = error;
    }
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 250);
  }
  throw new Error([
    `Could not click button containing "${text}"`,
    lastError?.message,
  ].filter(Boolean).join("\n"));
}

function waitForBodyText(requiredText, { timeout = 20000 } = {}) {
  const expression = `() => document.body ? document.body.innerText : ""`;
  const deadline = Date.now() + timeout;
  let bodyText = "";
  let lastError;
  while (Date.now() < deadline) {
    try {
      bodyText = evalRaw(expression, { timeoutMs: 5000 });
      if (bodyText.includes(requiredText)) {
        return bodyText;
      }
    } catch (error) {
      lastError = error;
    }
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 250);
  }
  throw new Error([
    `Timed out waiting for page text ${JSON.stringify(requiredText)}.`,
    lastError?.message,
    `Last body:\n${bodyText.slice(0, 2000)}`,
  ].filter(Boolean).join("\n"));
}

async function main() {
  assert(existsSync(pwcli), `Playwright CLI wrapper not found at ${pwcli}`);
  const tempRoot = await mkdtemp(resolve(tmpdir(), "loopx-reward-browser-smoke-"));
  let dashboardServer;
  let statusServer;
  try {
    const { registryPath, runtimeRoot } = await writeFixture(tempRoot);
    const dashboardPort = await freePort();
    const statusPort = await freePort();
    const statusBase = `http://127.0.0.1:${statusPort}`;
    const dashboardBase = `http://127.0.0.1:${dashboardPort}`;

    statusServer = startProcess(resolveTestPython(), [
      "-m",
      "loopx.cli",
      "--registry",
      registryPath,
      "--runtime-root",
      runtimeRoot,
      "serve-status",
      "--scan-root",
      projectRootForServe(registryPath),
      "--host",
      "127.0.0.1",
      "--port",
      String(statusPort),
      "--enable-reward-write-api",
    ], {
      env: { ...process.env, PYTHONPATH: repoRoot },
    });
    dashboardServer = startProcess("npm", ["run", "dev", "--", "--port", String(dashboardPort), "--strictPort"], {
      cwd: resolve(repoRoot, "apps/presentation/dashboard"),
      env: { ...process.env },
    });

    await waitForHttp(`${statusBase}/status.json`, "status server");
    await waitForHttp(dashboardBase, "dashboard server");

    forceKillBrowserSession();
    startBrowserSession();
    const targetUrl = `${dashboardBase}/?statusUrl=${encodeURIComponent(`${statusBase}/status.json`)}&goalId=${encodeURIComponent(goalId)}&actionKind=reward`;
    navigateTo(targetUrl);
    let bodyText = waitForBodyText("Reward CLI Draft");
    assert(bodyText.includes(goalId), "Dashboard did not show the selected goal id.");
    assert(
      bodyText.includes("Dry-run Check"),
      `Reward dry-run button was not visible. Page text:\n${bodyText.slice(0, 2000)}`,
    );

    clickButton("Dry-run Check");
    bodyText = waitForBodyText("Preview locked to this goal/run/reward payload");
    assert(bodyText.includes("Append reward overlay"), "Append button was not shown after dry-run.");

    clickButton("Append reward overlay");
    let status;
    try {
      status = await waitForRewardStatus(statusBase);
    } catch (error) {
      let pageText = "";
      try {
        pageText = evalRaw("() => document.body ? document.body.innerText : \"\"", { timeoutMs: 5000 });
      } catch (bodyError) {
        pageText = `Unable to read page text: ${bodyError.message}`;
      }
      throw new Error(`${error.message}\nPage text after append attempt:\n${pageText.slice(0, 8000)}`);
    }
    bodyText = waitForBodyText("No attention item");

    const { goal, latestRun } = latestRewardRun(status);
    assert(goal, "Status JSON did not include smoke goal run history.");
    assert(
      latestRun?.human_reward,
      `Latest run did not include appended human_reward. Latest run:\n${JSON.stringify(latestRun, null, 2)}\nPage text:\n${bodyText.slice(0, 2000)}`,
    );
    assert(latestRun.human_reward.decision, "Latest run human_reward is missing a decision.");
    assert(latestRun.human_reward.reward, "Latest run human_reward is missing a reward value.");
    assert(goal.lifecycle_phase === "reward_judged", "Goal lifecycle did not advance to reward_judged.");

    console.log("ok: dashboard reward append browser smoke passed");
  } finally {
    forceKillBrowserSession();
    if (dashboardServer) dashboardServer.kill();
    if (statusServer) statusServer.kill();
    await rm(tempRoot, { recursive: true, force: true });
  }
}

function projectRootForServe(registryPath) {
  return resolve(registryPath, "..", "project");
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exit(1);
});
