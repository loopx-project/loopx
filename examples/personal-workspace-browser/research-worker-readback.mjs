import assert from "node:assert/strict";
import {execFile, spawn} from "node:child_process";
import {mkdtemp, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {delimiter, dirname, resolve} from "node:path";
import {createInterface} from "node:readline";
import {promisify} from "node:util";
import {resolveTestPython} from "../../scripts/test-python.mjs";
import {outputDir, repoRoot} from "./fixture.mjs";
import {fetchWorkspaceBackend, openWorkspacePage} from "./scenario-context.mjs";

export async function startAuthority(mode) {
  const root = await mkdtemp(resolve(tmpdir(), "loopx-worker-readback-"));
  const child = spawn(resolveTestPython(), ["-u", "tests/control_plane/research_worker_readback_fixture.py", root, mode], {
    cwd: repoRoot, env: {...process.env, TMPDIR: root, TEMP: root, TMP: root, LOOPX_USAGE_PING: "0"},
    stdio: ["ignore", "pipe", "pipe"],
  });
  let diagnostic = "";
  child.stderr.on("data", chunk => { diagnostic = (diagnostic + chunk).slice(-4000); });
  const closed = new Promise(accept => child.once("close", (code, signal) => accept({code, signal})));
  const lines = createInterface({input: child.stdout});
  const close = async () => {
    lines.close();
    if (child.exitCode === null && child.signalCode === null) {
      child.kill("SIGTERM");
    }
    const result = await closed;
    assert.equal(result.code, 0, `Worker authority cleanup failed (${result.signal ?? result.code}): ${diagnostic}`);
    await rm(root, {recursive: true, force: true});
  };
  try {
    const value = await new Promise((accept, reject) => {
      const timeout = setTimeout(() => reject(new Error(`Worker authority startup timed out: ${diagnostic}`)), 60_000);
      child.once("error", error => { clearTimeout(timeout); reject(error); });
      child.once("exit", () => { clearTimeout(timeout); reject(new Error(`Worker authority exited: ${diagnostic}`)); });
      lines.once("line", line => { clearTimeout(timeout); accept(JSON.parse(line)); });
    });
    assert.ok(Number.isSafeInteger(value.port) && value.port > 0);
    return {...value, url: `http://127.0.0.1:${value.port}`, close};
  } catch (error) { await close(); throw error; }
}

export const researchWorkerReadbackScenario = {
  id: "research-worker-readback",
  async run({browser, collectCoverage, url}) {
    const coverageEntries = [];
    for (const mode of ["paused", "unknown"]) {
      const authority = await startAuthority(mode);
      try {
        for (const locale of ["zh-CN", "en"]) for (const mobile of [false, true]) {
          const zh = locale === "zh-CN";
          const beforeGoal = JSON.parse(await readFile(authority.registry, "utf8")).goals;
          const beforeHistory = await readFile(authority.history, "utf8");
          const overviewUrl = new URL(url);
          overviewUrl.searchParams.set("goalId", authority.goal_id);
          overviewUrl.searchParams.set("view", "overview");
          let writes = 0;
          const writePaths = [];
          const failedReads = [];
          const sessionPreparations = [];
          const ui = await openWorkspacePage(browser, overviewUrl.href, {
            collectCoverage, viewport: mobile ? {width: 390, height: 844} : {width: 1512, height: 982}, isMobile: mobile,
            beforeGoto: async (_api, page) => {
              await page.addInitScript(value => localStorage.setItem("loopx-pw-locale", value), locale);
              // Goal readback and actions reach the real disposable backend,
              // including Stop. The shared smoke keeps its separate fixture
              // for the optional Lark connector, which is not exercised here.
              await page.route(target => ["/status.json", "/extension-presentation-surfaces", "/extension-projection"].includes(target.pathname)
                || target.pathname.startsWith("/api/chat/") && !target.pathname.startsWith("/api/chat/lark/")
                || target.pathname.startsWith("/api/actions"), async route => {
                const path = new URL(route.request().url()).pathname;
                // The standard page may acknowledge its displayed device
                // telemetry notice. That is not Goal work or adoption.
                const emptySession = route.request().method() === "POST" && path === "/api/chat/sessions";
                if (route.request().method() !== "GET" && path !== "/api/chat/usage-statistics" && !emptySession) {
                  writes += 1;
                  writePaths.push(path);
                }
                const response = await fetchWorkspaceBackend(route, authority.url);
                if (emptySession) sessionPreparations.push({status: response.status(), payload: await response.json()});
                if (response.status() >= 400) failedReads.push({path: new URL(route.request().url()).pathname,
                  status: response.status(), body: (await response.text()).slice(0, 800)});
                await route.fulfill({response});
              });
            },
          });
          const {page} = ui;
          try {
            await page.locator(".personal-goal-tabs").getByRole("button", {name: zh ? "概览" : "Overview", exact: true}).waitFor();
            const region = page.getByRole("region", {name: zh ? "外部执行器状态" : "External worker status", exact: true});
            await region.waitFor();
            assert.match(await region.innerText(), mode === "paused" ? /已暂停复核|review paused/u : /原请求结果未知|Original outcome unknown/u);
            await region.getByRole("button", {name: zh ? "执行记录范围" : "Execution record scope", exact: true}).click();
            const recordScope = region.getByRole("tooltip");
            await recordScope.waitFor({state: "visible"});
            assert.match(await recordScope.innerText(), /完整费用未知|Complete costs are unknown/u);
            await page.keyboard.press("Escape");
            await recordScope.waitFor({state: "hidden"});
            await page.screenshot({path: resolve(outputDir, `research-worker-${mode}-${locale}-${mobile ? "mobile" : "desktop"}.png`), animations: "disabled"});
            await region.locator("summary").click();
            assert.ok((await region.innerText()).includes(authority.obligation_id));
            assert.match(await region.innerText(), /未绑定|Unbound/u);
            assert.equal(await region.evaluate(element => element.scrollWidth > element.clientWidth + 1), false);
            await region.getByRole("button", {name: zh ? "查看任务与依赖" : "View work and dependencies", exact: true}).click();
            await page.locator('[data-goal-panel="tasks"]:not([hidden])').waitFor();
            await page.locator(".personal-goal-tabs").getByRole("button", {name: zh ? "概览" : "Overview", exact: true}).click();
            assert.equal(writes, 0, "Observation and task navigation must not dispatch or adopt work: " + JSON.stringify(writePaths));
            assert.equal(JSON.parse(await readFile(authority.dispatches, "utf8")), 1);
            assert.deepEqual(JSON.parse(await readFile(authority.registry, "utf8")).goals, beforeGoal);
            assert.equal(await readFile(authority.history, "utf8"), beforeHistory);

            if (!mobile && zh) {
              const refresh = async () => {
                await Promise.all([
                  page.waitForResponse(response => new URL(response.url()).pathname === "/status.json" && response.status() === 200),
                  page.getByRole("button", {name: "刷新状态", exact: true}).click(),
                ]);
                await page.locator(".personal-refresh-control.is-done").waitFor();
              };
              const original = await readFile(authority.receipt, "utf8");
              for (const damage of ["different-goal", "old-scope", "corrupt", "invalid-utf8", "wrong-schema"]) {
                const receipt = JSON.parse(original);
                if (damage === "different-goal") receipt.goal_id = "unrelated-goal";
                if (damage === "old-scope") {
                  const observation = receipt.worker_observations[authority.agent_id];
                  assert.equal(typeof observation.source_goal_observation_digest, "string");
                  observation.source_goal_observation_digest = "obsolete";
                }
                if (damage === "wrong-schema") receipt.schema_version = "unrecognized-receipt";
                const contents = damage === "corrupt" ? "{broken" : damage === "invalid-utf8" ? Buffer.from([0xff]) : JSON.stringify(receipt);
                await writeFile(authority.receipt, contents);
                if (damage !== "old-scope") {
                  // The next ordinary CLI poll must not silently replace an
                  // unreadable source while the original worker is unresolved.
                  const root = dirname(authority.registry);
                  const environment = {...process.env, PYTHONPATH: [repoRoot, process.env.PYTHONPATH].filter(Boolean).join(delimiter), HOME: resolve(root, "home"),
                    USERPROFILE: resolve(root, "home"), TMPDIR: root, TEMP: root, TMP: root, LOOPX_USAGE_PING: "0"};
                  for (const variable of ["CODEX_HOME", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"]) delete environment[variable];
                  const {stdout} = await promisify(execFile)(resolveTestPython(), ["-m", "loopx.cli",
                    "--registry", authority.registry, "--runtime-root", authority.runtime, "--format", "json",
                    "quota", "should-run", "--goal-id", authority.goal_id, "--agent-id", authority.agent_id,
                    "--runtime-profile", "generic_cli", "--record-host-poll"],
                  {cwd: repoRoot, env: environment, timeout: 60_000, maxBuffer: 1_000_000});
                  assert.equal(JSON.parse(stdout).ok, true);
                  assert.deepEqual(await readFile(authority.receipt), Buffer.from(contents));
                  assert.equal(JSON.parse(await readFile(authority.dispatches, "utf8")), 1);
                }
                await refresh();
                await region.getByRole("button", {name: "执行结果未知", exact: true}).click();
                await region.getByRole("status").waitFor();
                assert.match(await region.innerText(), /当前执行结果未知/u);
                assert.ok(!(await region.innerText()).includes(authority.obligation_id), "Invalid association must hide the old operation");
                assert.equal(await region.getByText(authority.obligation_id, {exact: true}).count(), 0);
                await page.keyboard.press("Escape");
                await region.getByRole("status").waitFor({state: "hidden"});
                await writeFile(authority.receipt, original);
                await refresh();
                await region.locator("details").waitFor();
              }
            }
            if (mobile && !zh) {
              // Stop remains a real canonical operation. A receipt cannot
              // reactivate the Goal or convert this stop into completion.
              await page.locator(".goal-lifecycle-trigger").click();
              const dialog = page.getByRole("dialog");
              await dialog.getByRole("button", {name: "Stop Goal", exact: true}).click();
              await page.locator(".goal-lifecycle-trigger", {hasText: "Resume"}).waitFor();
              const registry = JSON.parse(await readFile(authority.registry, "utf8"));
              assert.equal(registry.goals.find(goal => goal.id === authority.goal_id).activation?.state, "stopped");
              assert.equal(JSON.parse(await readFile(authority.dispatches, "utf8")), 1);
              assert.ok(writes > 0);
            }
            // The existing Chat panel may prepare an empty Session. This
            // fixture has no installed model endpoint, so its real owner must
            // refuse preparation before a Turn or any Goal work can start.
            for (const preparation of sessionPreparations) {
              assert.equal(preparation.status, 424, JSON.stringify(preparation));
              assert.equal(preparation.payload.error_code, "agent_endpoint_unavailable");
            }
            const unavailable = failedReads.filter(row => row.path === "/api/chat/sessions" && row.status === 424
              && JSON.parse(row.body).error_code === "agent_endpoint_unavailable");
            assert.equal(failedReads.length, unavailable.length, JSON.stringify(failedReads));
            const errors = ui.errors.filter(message => !(unavailable.length > 0
              && message === "Failed to load resource: the server responded with a status of 424 (Failed Dependency)"));
            assert.equal(errors.length, 0, errors.join(" | ") + JSON.stringify(failedReads));
          } finally { coverageEntries.push(...await ui.close()); }
        }
      } finally { await authority.close(); }
    }
    console.log(JSON.stringify({scenario: this.id, source: "real-file-cli-worker-source-global-http", modes: 2, locales: 2, viewports: 2,
      actualStop: true, corruptAndStale: true, unreadableSurvivesPoll: true, newWorkFromReadback: 0}));
    return {coverageEntries, note: "Actual source CLI worker → global registry synchronization → canonical File HTTP readback → packaged overview; paused/unknown, Chinese/English, desktop/mobile, original Stop and zero work from observation."};
  },
};
