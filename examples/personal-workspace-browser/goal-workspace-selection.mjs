import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {mkdtemp, readFile, rename, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {basename, dirname, resolve} from "node:path";
import {createInterface} from "node:readline";
import {testPythonCommand} from "../../scripts/test-python.mjs";
import {outputDir, repoRoot} from "./fixture.mjs";
import {openWorkspacePage} from "./scenario-context.mjs";

export const goalWorkspaceSelectionScenario = {
  id: "goal-workspace-selection",
  async run({browser, collectCoverage, url}) {
    const root = await mkdtemp(resolve(tmpdir(), "loopx-goal-workspace-"));
    // runpy keeps the fixture's __file__ while the shared launcher owns imports.
    const backendCommand = testPythonCommand(`
import runpy
sys.argv = [${JSON.stringify(resolve(repoRoot, "examples/personal-workspace-browser/goal-workspace-backend.py"))}, ${JSON.stringify(root)}]
runpy.run_path(sys.argv[0], run_name="__main__")
`);
    const child = spawn(backendCommand.command, backendCommand.args,
      {cwd: repoRoot, stdio: ["ignore", "pipe", "pipe"]});
    const lines = createInterface({input: child.stdout});
    let diagnostics = "", context;
    child.stderr.on("data", chunk => {diagnostics = (diagnostics + chunk).slice(-6000);});
    try {
      const backend = await new Promise((accept, reject) => {
        const timer = setTimeout(() => reject(new Error(`Workspace backend startup: ${diagnostics}`)), 30000);
        child.once("error", error => {clearTimeout(timer); reject(error);});
        child.once("exit", () => {clearTimeout(timer); reject(new Error(`Workspace backend exited: ${diagnostics}`));});
        lines.once("line", line => {clearTimeout(timer); try {accept(JSON.parse(line));} catch (error) {reject(error);}});
      });
      const requests = [], applied = [];
      let rejectPreview = true;
      context = await openWorkspacePage(browser, url, {collectCoverage, beforeGoto: async (_api, page) => {
        await page.route("**/api/chat/projects", async route => {
          await route.fulfill({response: await route.fetch({url: `http://127.0.0.1:${backend.port}/api/chat/projects`})});
        });
        await page.route("**/api/actions{,/**,?*}", async route => {
          const request = route.request(), path = new URL(request.url());
          if (path.pathname.endsWith("/preview")) {
            requests.push(request.postDataJSON());
            if (rejectPreview) {
              await route.fulfill({status: 503, json: {ok: false, error: "Workspace service unavailable; retry."}});
              return;
            }
          }
          const response = await route.fetch({url: `http://127.0.0.1:${backend.port}${path.pathname}${path.search}`});
          if (path.pathname.endsWith("/apply")) applied.push(await response.json());
          await route.fulfill({response});
        });
      }});
      const {page} = context;
      await page.getByRole("button", {name: "创建 Goal", exact: true}).first().click();
      const form = page.getByRole("dialog", {name: "创建新 Goal"});
      await form.getByLabel("目标", {exact: true}).fill("核对第二个项目的公开文档");
      await form.getByLabel("完成标准", {exact: true}).fill("输出带来源的检查结果");
      await form.getByLabel("执行权限", {exact: true}).selectOption("read_only");
      const directory = form.getByLabel("工作目录", {exact: true});
      await directory.selectOption("research-monitor");
      await directory.focus();
      assert.equal(await directory.evaluate(node => node === document.activeElement), true);
      await page.screenshot({path: resolve(outputDir, "goal-workspace-selection-desktop.png"), animations: "disabled"});
      await form.getByRole("button", {name: "检查配置"}).click();
      await form.getByRole("alert").waitFor();
      assert.equal(await directory.inputValue(), "research-monitor");
      assert.equal(await form.getByLabel("目标", {exact: true}).inputValue(), "核对第二个项目的公开文档");
      assert.equal(JSON.parse(await readFile(backend.registry, "utf8")).goals.length, 2);
      await page.setViewportSize({width: 390, height: 844});
      await directory.scrollIntoViewIfNeeded();
      const bounds = await form.boundingBox();
      assert.ok(bounds && bounds.x >= 0 && bounds.x + bounds.width <= 390);
      await page.screenshot({path: resolve(outputDir, "goal-workspace-selection-mobile.png"), animations: "disabled"});
      rejectPreview = false;
      await rename(backend.second, `${backend.second}-unavailable`);
      await form.getByRole("button", {name: "检查配置"}).click();
      await form.getByRole("alert").filter({hasText: "workspace is unavailable"}).waitFor();
      assert.equal(await directory.inputValue(), "research-monitor");
      assert.equal(JSON.parse(await readFile(backend.registry, "utf8")).goals.length, 2);
      await rename(`${backend.second}-unavailable`, backend.second);
      await form.getByRole("button", {name: "检查配置"}).click();
      await page.getByText("确认执行", {exact: true}).waitFor();
      assert.equal(requests.at(-1).normalized_parameters.workspace_ref, "research-monitor");
      // The review names the directory the Goal will use, without its path.
      // Both fixture Goals use a directory with the same name, so the review
      // adds the parent directory that tells them apart.
      assert.equal(basename(backend.first), basename(backend.second));
      const secondLabel = `${basename(dirname(backend.second))}/${basename(backend.second)}`;
      await page.getByText(`${secondLabel} · 与已有 Goal 共用`, {exact: false}).first().waitFor();
      assert.equal(await page.getByText(`${basename(dirname(backend.first))}/${basename(backend.first)} · 与已有 Goal 共用`, {exact: false}).count(), 0);
      assert.equal(await page.getByText(backend.second, {exact: false}).count(), 0);
      assert.equal(JSON.parse(await readFile(backend.registry, "utf8")).goals.length, 2, "Preview created a Goal");
      const confirm = page.getByRole("button", {name: /创建 Goal.*开始首轮/});
      await confirm.click();
      await page.waitForFunction(() => document.body.innerText.includes("进入新 Goal"));
      assert.equal(applied.length, 1);
      assert.equal(applied[0].proposal.receipt.projection_verified, true);
      const goalId = requests.at(-1).normalized_parameters.goal_id;
      const registry = JSON.parse(await readFile(backend.registry, "utf8"));
      const created = registry.goals.find(goal => goal.id === goalId);
      assert.equal(created.repo, backend.second, "Confirmed Goal was created in the other workspace");
      assert.equal(created.parent_goal_id, "research-monitor");
      assert.notEqual(created.repo, backend.first);
      const state = await readFile(resolve(created.repo, created.state_file), "utf8");
      assert.match(state, /输出带来源的检查结果/);

      // A Dashboard workspace that no Goal uses yet is offered, reviewed by name
      // and becomes the new Goal's directory.
      await page.setViewportSize({width: 1280, height: 900});
      await page.getByRole("button", {name: "创建 Goal", exact: true}).first().click();
      const next = page.getByRole("dialog", {name: "创建新 Goal"});
      await next.getByLabel("目标", {exact: true}).fill("整理文档站点的入门指南");
      await next.getByLabel("完成标准", {exact: true}).fill("列出缺失的入门步骤");
      await next.getByLabel("执行权限", {exact: true}).selectOption("read_only");
      await next.getByLabel("工作目录", {exact: true}).selectOption(backend.configured_ref);
      await next.getByRole("button", {name: "检查配置"}).click();
      await page.getByText("docs-site · Dashboard 工作区", {exact: false}).first().waitFor();
      assert.equal(requests.at(-1).normalized_parameters.workspace_ref, backend.configured_ref);
      await page.screenshot({path: resolve(outputDir, "goal-workspace-selection-review.png"), animations: "disabled"});
      const configuredGoalId = requests.at(-1).normalized_parameters.goal_id;
      await page.getByRole("button", {name: /创建 Goal.*开始首轮/}).last().click();
      for (const deadline = Date.now() + 30000; applied.length < 2 && Date.now() < deadline;) {
        await new Promise(accept => setTimeout(accept, 200));
      }
      assert.equal(applied.length, 2, "Dashboard-workspace creation was not applied");
      assert.equal(applied[1].proposal.receipt.projection_verified, true);
      const configuredGoal = JSON.parse(await readFile(backend.registry, "utf8")).goals
        .find(goal => goal.id === configuredGoalId);
      assert.equal(configuredGoal.repo, backend.configured, "Confirmed Goal was created in the Dashboard workspace");

      // Leaving the default still names the directory it resolves to before
      // anything is created.
      await page.getByRole("button", {name: "创建 Goal", exact: true}).first().click();
      const fallback = page.getByRole("dialog", {name: "创建新 Goal"});
      await fallback.getByLabel("目标", {exact: true}).fill("检查默认目录");
      await fallback.getByLabel("完成标准", {exact: true}).fill("说明默认目录");
      await fallback.getByRole("button", {name: "检查配置"}).click();
      await page.getByText("默认：docs-site · Dashboard 工作区", {exact: false}).first().waitFor();
      assert.equal(requests.at(-1).normalized_parameters.workspace_ref, "current");
      assert.equal(applied.length, 2, "Reviewing the default created a Goal");
      return {coverageEntries: context.coverageEntries, errors: context.errors,
        note: "Packaged directory choice (existing Goal and Dashboard workspace), review naming, keyboard/mobile, preview failure retention and real HTTP/bootstrap/registry readback passed. Status fixtures and model dispatch are synthetic; no installed or live-model claim."};
    } finally {
      await context?.close();
      lines.close();
      if (child.exitCode === null && child.signalCode === null) {
        const exited = new Promise(accept => child.once("exit", accept));
        child.kill("SIGTERM");
        const timer = setTimeout(() => child.kill("SIGKILL"), 5000);
        try {await exited;} finally {clearTimeout(timer);}
      }
      await rm(root, {recursive: true, force: true});
    }
  },
};
