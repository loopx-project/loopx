import { spawn } from "node:child_process";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { createInterface } from "node:readline";

import { resolveTestPython } from "../../scripts/test-python.mjs";
import { outputDir, repoRoot } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

// The packaged UI writes to the actual HTTP handler and typed file authority.
// Only the surrounding workspace directory remains the shared browser fixture.
async function startCadenceAuthority() {
  const root = await mkdtemp(resolve(tmpdir(), "loopx-cadence-browser-"));
  await writeFile(resolve(root, "registry.json"), JSON.stringify({
    schema_version: "0.1", goals: [{ id: "multi-agent-projection" }],
  }));
  const child = spawn(resolveTestPython(), ["-u", "-c", `
from pathlib import Path
import sys
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
server.registry_path = Path(sys.argv[1]) / "registry.json"
server.runtime_root = Path(sys.argv[1]) / "runtime"
server.verbose = False
print(server.server_address[1], flush=True)
server.serve_forever()
`, root], { cwd: repoRoot, stdio: ["ignore", "pipe", "pipe"] });
  let diagnostic = "";
  child.stderr.on("data", (chunk) => { diagnostic = (diagnostic + chunk).slice(-2000); });
  const lines = createInterface({ input: child.stdout });
  try {
    const port = await new Promise((accept, reject) => {
      const timeout = setTimeout(() => reject(new Error(`Cadence authority startup timed out: ${diagnostic}`)), 15_000);
      child.once("error", (error) => { clearTimeout(timeout); reject(error); });
      child.once("exit", () => { clearTimeout(timeout); reject(new Error(`Cadence authority exited: ${diagnostic}`)); });
      lines.once("line", (line) => { clearTimeout(timeout); accept(Number(line)); });
    });
    if (!Number.isSafeInteger(port) || port < 1) throw new Error("Invalid authority port");
    return { root, url: `http://127.0.0.1:${port}`, async close() {
      lines.close();
      if (child.exitCode === null && child.signalCode === null) {
        const exited = new Promise((accept) => child.once("exit", accept));
        child.kill("SIGTERM");
        await exited;
      }
      await rm(root, { recursive: true, force: true });
    } };
  } catch (error) {
    lines.close(); child.kill("SIGTERM");
    await rm(root, { recursive: true, force: true });
    throw error;
  }
}

export const automationCadenceScenario = {
  id: "automation-cadence",
  async run({ browser, collectCoverage, url }) {
    const authority = await startCadenceAuthority();
    const path = "/api/chat/automation-cadence";
    let applyCount = 0;
    let concurrentChange = false;
    let unverifiedReadback = false;
    const changes = [];
    async function read(agentId = null) {
      const query = new URLSearchParams({ goal_id: "multi-agent-projection" });
      if (agentId) query.set("agent_id", agentId);
      const response = await fetch(`${authority.url}${path}?${query}`);
      if (!response.ok) throw new Error(`Authority read failed: ${response.status}`);
      return response.json();
    }
    async function interleaveGoalChange() {
      const current = await read();
      const change = { goal_id: "multi-agent-projection", agent_id: null, automation_id: null,
        min_interval_minutes: 90, expected_revision: current.configuration_revision,
        owner_reference: "Synthetic concurrent settings save", approve_reduction: false };
      const preview = await fetch(`${authority.url}${path}/preview`, { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(change) });
      if (!preview.ok) throw new Error("Concurrent policy preview failed");
      const { preview_revision } = await preview.json();
      const applied = await fetch(`${authority.url}${path}/apply`, { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...change, preview_revision }) });
      if (!applied.ok) throw new Error("Concurrent policy apply failed");
    }
    let context;
    try {
      context = await openWorkspacePage(browser, url, {
        collectCoverage,
        beforeGoto: async (_api, page) => {
          await page.route("**/api/chat/automation-cadence**", async (route) => {
            const parsed = new URL(route.request().url());
            if (parsed.pathname.endsWith("/preview") && concurrentChange) {
              concurrentChange = false;
              await interleaveGoalChange();
            }
            const response = await route.fetch({ url: authority.url + parsed.pathname + parsed.search });
            if (parsed.pathname.endsWith("/apply") && response.ok()) {
              applyCount += 1;
              changes.push(route.request().postDataJSON());
              if (unverifiedReadback) {
                unverifiedReadback = false;
                // Exercise an unverified response after a real committed write.
                await route.fulfill({ response, json: { ...await response.json(), readback_verified: false } });
                return;
              }
            }
            await route.fulfill({ response });
          });
        },
      });
      const { page, checkpointCoverage, errors } = context;
      await page.locator(".personal-goal-link", { hasText: "Multi Agent Projection" }).click();
      await page.getByRole("button", { name: "Goal 设置", exact: true }).click();
      await page.getByRole("button", { name: "自动执行间隔", exact: true }).click();
      await page.locator(".personal-settings-goal-target").getByText("Multi Agent Projection", { exact: true }).waitFor();
      const panel = page.getByRole("region", { name: "自动执行间隔" });
      const target = panel.getByLabel("修改对象", { exact: true });
      const minutes = panel.getByLabel("最短间隔（分钟）", { exact: true });
      const save = panel.getByRole("button", { name: /^保存(?:并降低下限)?$/ });
      const source = (own, parent) => panel.getByText(`本层设置 ${own} 分钟；上层下限 ${parent} 分钟。`, { exact: true });
      if (await target.inputValue() !== "") throw new Error("Cadence silently selected a target");
      for (const id of ["codex-latest-lane", "codex-older-lane"]) {
        if (!await target.locator(`option[value="agent:${id}"]`).count()) throw new Error(`Missing Agent: ${id}`);
      }
      await target.selectOption("goal");
      await minutes.waitFor();
      await minutes.fill("60");
      if (!await save.isEnabled()) throw new Error("A blank optional note blocked Save");
      // Two submits in one event boundary must still commit only once.
      await panel.locator("form").evaluate((form) => { form.requestSubmit(); form.requestSubmit(); });
      await source(60, 0).waitFor();
      await panel.getByText("已保存并核对生效值。", { exact: true }).waitFor();
      if (applyCount !== 1 || changes[0].approve_reduction || !changes[0].owner_reference.startsWith("App settings Save:")) {
        throw new Error("Save duplicated the write or lost explicit audit provenance");
      }
      await minutes.fill("-1");
      await panel.getByText("请输入 0 到 525600 之间的整数分钟。", { exact: true }).waitFor();
      if (await save.isEnabled()) throw new Error("Invalid interval was writable");
      await minutes.fill("30");
      await save.click();
      await source(30, 0).waitFor();
      if (!changes.at(-1).approve_reduction) throw new Error("Explicit lowering Save lost reduction intent");
      await minutes.fill("60"); await save.click(); await source(60, 0).waitFor();
      await target.selectOption("agent:codex-latest-lane");
      await panel.getByText("继承上层 60 分钟；本层未设置。", { exact: true }).waitFor();
      await minutes.fill("120");
      await target.selectOption("agent:codex-older-lane");
      await panel.getByText("继承上层 60 分钟；本层未设置。", { exact: true }).waitFor();
      if (await minutes.inputValue() !== "0" || await save.isEnabled()) throw new Error("Another Agent retained a writable draft");
      await target.selectOption("agent:codex-latest-lane");
      await panel.getByText("继承上层 60 分钟；本层未设置。", { exact: true }).waitFor();
      await minutes.fill("120"); await save.click(); await source(120, 60).waitFor();
      await minutes.fill("10");
      await panel.getByText("上层的 60 分钟下限仍然生效。", { exact: true }).waitFor();
      await page.screenshot({ path: resolve(outputDir, "desktop-automation-cadence-inherited.png"), animations: "disabled" });
      await save.click(); await source(10, 60).waitFor();
      if ((await read("codex-older-lane")).min_interval_minutes !== 60 || (await read()).min_interval_minutes !== 60) {
        throw new Error("Agent save changed a peer or Goal default");
      }
      concurrentChange = true;
      await minutes.fill("20"); await save.click();
      await panel.getByRole("alert").waitFor();
      if ((await read("codex-latest-lane")).sources.find((row) => row.agent_id === "codex-latest-lane").min_interval_minutes !== 10) {
        throw new Error("A stale Save overwrote the current policy");
      }
      if (await save.isEnabled()) throw new Error("Stale policy could be resubmitted without refresh");
      await panel.getByRole("button", { name: "刷新策略", exact: true }).click();
      await source(10, 90).waitFor();
      unverifiedReadback = true;
      await minutes.fill("20"); await save.click(); await source(20, 90).waitFor();
      await panel.getByText("已写入，但最新生效值尚未核实；请刷新后再修改。", { exact: true }).waitFor();
      if (await panel.getByText("已保存并核对生效值。", { exact: true }).count()) throw new Error("Unverified readback looked like verified success");
      await panel.getByRole("button", { name: "刷新策略", exact: true }).click(); await source(20, 90).waitFor();
      await target.selectOption("goal"); await source(90, 0).waitFor();
      await minutes.fill("0"); await save.click(); await source(0, 0).waitFor();
      await target.selectOption("agent:codex-latest-lane"); await source(20, 0).waitFor();
      await minutes.fill("10"); await minutes.press("Enter"); await source(10, 0).waitFor();
      if ((await read("codex-latest-lane")).min_interval_minutes !== 10 || (await read("codex-older-lane")).min_interval_minutes !== 0) {
        throw new Error("The 10-minute setting did not survive a fresh authority read");
      }
      await panel.getByText("限定单个自动化", { exact: true }).click();
      await panel.getByRole("checkbox", { name: "仅对这个自动化生效", exact: true }).check();
      await panel.getByLabel("自动化 ID", { exact: true }).fill("daily");
      await panel.getByText("继承上层 10 分钟；本层未设置。", { exact: true }).waitFor();
      await panel.getByText("添加备注（可选）", { exact: true }).click();
      await panel.getByLabel("备注", { exact: true }).fill("Synthetic optional note ".repeat(6));
      await minutes.fill("20"); await save.click(); await source(20, 10).waitFor();
      if ((await read("codex-latest-lane")).min_interval_minutes !== 10
        || !changes.at(-1).owner_reference.includes("Synthetic optional note")
        || changes.at(-1).owner_reference.length > 256) throw new Error("Automation targeting or optional note changed the Agent policy");
      await panel.getByLabel("自动化 ID", { exact: true }).fill("hourly");
      await panel.getByText("继承上层 10 分钟；本层未设置。", { exact: true }).waitFor();
      if (await minutes.inputValue() !== "0" || await save.isEnabled()) throw new Error("Changing automation retained a writable draft");
      await panel.getByRole("checkbox", { name: "仅对这个自动化生效", exact: true }).uncheck(); await source(10, 0).waitFor();
      await panel.getByText("限定单个自动化", { exact: true }).click();
      const noteDetails = panel.locator("details[open]").filter({ hasText: "添加备注（可选）" });
      if (await noteDetails.count()) await noteDetails.locator("summary").click();
      const files = await readdir(resolve(authority.root, "runtime"), { recursive: true });
      const policyPath = files.find((file) => file.endsWith(".json"));
      const stored = JSON.parse(await readFile(resolve(authority.root, "runtime", policyPath), "utf8"));
      if (!stored.rules.every((rule) => rule.owner_reference.startsWith("App settings Save:"))) throw new Error("The real policy lost Save audit references");
      await page.screenshot({ path: resolve(outputDir, "desktop-automation-cadence-agent.png"), animations: "disabled" });
      await page.setViewportSize({ width: 390, height: 844 });
      await page.screenshot({ path: resolve(outputDir, "mobile-automation-cadence-inherited.png"), fullPage: false, animations: "disabled" });
      if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)) throw new Error("Cadence settings overflowed the mobile viewport");
      await page.setViewportSize({ width: 1512, height: 982 });
      await page.getByRole("button", { name: "语言", exact: true }).click();
      await page.getByRole("radio", { name: "English", exact: true }).click();
      await page.getByRole("button", { name: "Automatic execution interval", exact: true }).click();
      await page.getByLabel("Applies to", { exact: true }).selectOption("agent:codex-latest-lane");
      await page.getByText("This scope: 10 min · inherited minimum: 0 min.", { exact: true }).waitFor();
      await page.getByLabel("Minimum interval (minutes)", { exact: true }).fill("20");
      if (!await page.getByRole("button", { name: "Save", exact: true }).isEnabled()) throw new Error("English Save required a note");
      await page.screenshot({ path: resolve(outputDir, "desktop-automation-cadence-english.png"), animations: "disabled" });
      const unexpected = errors.filter((error) => !error.includes("409 (Conflict)"));
      if (unexpected.length) throw new Error(`Browser errors: ${unexpected.join(" | ")}`);
      await checkpointCoverage();
      return { coverageEntries: await context.close(), note: "Packaged Save → real HTTP/TS file authority: optional notes, one commit on duplicate submit, explicit reduction, inheritance and peer isolation, stale CAS rejection, unverified-readback recovery, keyboard and mobile verified. Codex timers remain separately configured." };
    } catch (error) {
      if (context) {
        await context.page.screenshot({ path: resolve(outputDir, "automation-cadence-failure.png"), animations: "disabled" }).catch(() => {});
        console.error((await context.page.locator(".personal-settings-main").innerText().catch(() => "")).slice(0, 2000));
      }
      await context?.close();
      throw error;
    } finally { await authority.close(); }
  },
};
