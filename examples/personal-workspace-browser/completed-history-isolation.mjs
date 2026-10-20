import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {once} from "node:events";
import {mkdtemp, rm, symlink} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join, resolve} from "node:path";
import {createInterface} from "node:readline";
import {resolveTestPython} from "../../scripts/test-python.mjs";
import {outputDir, repoRoot} from "./fixture.mjs";
import {fetchWorkspaceBackend, openWorkspacePage} from "./scenario-context.mjs";

export const completedHistoryIsolationScenario = {
  id: "completed-history-isolation",
  async run({browser, collectCoverage, url}) {
    const temporary = await mkdtemp(join(tmpdir(), "loopx-history-alias-"));
    const alias = join(temporary, "alias");
    await symlink(tmpdir(), alias, process.platform === "win32" ? "junction" : "dir");
    const backend = spawn(resolveTestPython({repoRoot}), ["-u", resolve(repoRoot, "examples/personal-workspace-browser/completed_history_fixture.py")], {
      cwd: repoRoot, env: {...process.env, PYTHONPATH: "", TMPDIR: alias, TEMP: alias, TMP: alias}, stdio: ["pipe", "pipe", "pipe"],
    });
    const closed = once(backend, "close");
    let diagnostics = "", context, slowRead;
    backend.stderr.on("data", chunk => { diagnostics = (diagnostics + chunk).slice(-4000); });
    const lines = createInterface({input: backend.stdout});
    const records = lines[Symbol.asyncIterator]();
    async function receive(event) {
      let timer;
      try {
        const record = await Promise.race([records.next(), new Promise((_, reject) => {
          timer = setTimeout(() => reject(new Error(`History backend timeout: ${diagnostics}`)), 15000);
        })]);
        assert.equal(record.done, false, diagnostics);
        const payload = JSON.parse(record.value);
        assert.equal(payload.event, event);
        return payload;
      } finally { clearTimeout(timer); }
    }
    async function command(event) {
      backend.stdin.write(`${event}\n`);
      return receive(event);
    }
    try {
      const {origin} = await receive("ready");
      context = await openWorkspacePage(browser, url, {collectCoverage, beforeGoto: async (_api, page) => {
        await page.route("**/api/chat/completed-todos?*", async route => {
          assert.equal(route.request().method(), "GET");
          await route.fulfill({response: await fetchWorkspaceBackend(route, origin)});
        });
      }});
      const {api, page} = context;
      await page.locator(".personal-goal-link").filter({hasText: "Progress Projection"}).click();
      await page.getByRole("navigation", {name: "Goal 视图"}).getByRole("button", {name: "任务", exact: true}).click();
      const lane = page.getByTestId("completed-task-lane");
      const scroll = lane.getByRole("region");
      await lane.getByText("Retained task 84", {exact: true}).waitFor();
      let slowFinished = false;
      slowRead = fetch(`${origin}/api/chat/completed-todos?goal_id=slow-goal`).then(async response => {
        assert.equal(response.status, 200);
        return response.json();
      }).finally(() => { slowFinished = true; });
      await receive("slow-read");
      const nextPage = page.waitForResponse(response => {
        const parsed = new URL(response.url());
        return parsed.pathname === "/api/chat/completed-todos" && parsed.searchParams.has("cursor") && response.status() === 200;
      }, {timeout: 3000});
      await scroll.evaluate(element => { element.scrollTop = element.scrollHeight; });
      await nextPage;
      await lane.getByText("Retained task 44", {exact: true}).waitFor();
      assert.equal(slowFinished, false, "Cached pagination finishes while the other Goal's source is still held");
      await scroll.evaluate(element => { element.scrollTop = 40 * 148; });
      await page.screenshot({path: resolve(outputDir, "completed-history-isolation-desktop.png"), animations: "disabled"});
      await command("release");
      await slowRead;

      await command("fault");
      await scroll.evaluate(element => { element.scrollTop = element.scrollHeight; });
      await lane.getByText("历史加载失败，已显示的内容仍可查看。", {exact: true}).waitFor();
      await lane.getByText("Retained task 5", {exact: true}).waitFor();
      await command("recover");
      const recoveredPage = page.waitForResponse(response => {
        const parsed = new URL(response.url());
        return parsed.pathname === "/api/chat/completed-todos" && parsed.searchParams.has("cursor") && response.status() === 200;
      });
      await lane.getByRole("button", {name: "重新加载", exact: true}).click();
      await recoveredPage;
      await scroll.evaluate(element => { element.scrollTop = element.scrollHeight; });
      await lane.getByText("Retained task 0", {exact: true}).waitFor();
      assert.ok(await lane.locator(".personal-completed-row").count() <= 20);
      await page.setViewportSize({width: 390, height: 844});
      await lane.getByText("Retained task 0", {exact: true}).scrollIntoViewIfNeeded();
      assert.ok(await page.locator("body").evaluate(element => element.scrollWidth <= innerWidth + 1));
      await page.screenshot({path: resolve(outputDir, "completed-history-isolation-mobile.png"), animations: "disabled"});
      const inspection = await command("inspect");
      assert.equal(inspection.store_unchanged, true);
      assert.equal(inspection.posts, 0);
      assert.equal(api.turnRequests.length, 0);
      assert.equal(api.actionApplies.length, 0);
      assert.deepEqual(context.errors.filter(error => !error.includes("503")), []);
      return {coverageEntries: context.coverageEntries, note: "Real HTTP cached history progresses independently; 503 recovery retains rows, reaches all85 results and starts no Turn."};
    } catch (error) {
      if (context) await context.page.screenshot({path: resolve(outputDir, "completed-history-isolation-failed.png"), animations: "disabled"});
      throw error;
    } finally {
      if (!backend.killed && backend.exitCode === null) {
        backend.stdin.write("release\n");
        if (slowRead) await slowRead;
      }
      if (context) {
        await context.page.unrouteAll({behavior: "wait"});
        await context.close();
      }
      lines.close();
      backend.stdin.end();
      await closed;
      await rm(temporary, {recursive: true, force: true});
    }
  },
};
