import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {once} from "node:events";
import {mkdtemp, readFile, rm, symlink} from "node:fs/promises";
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
      await page.screenshot({path: resolve(outputDir, "completed-history-search-unfiltered.png"), animations: "disabled"});
      const search = lane.getByRole("searchbox", {name: "搜索完成历史"});
      const submit = lane.getByRole("button", {name: "搜索", exact: true});
      // The oldest task is outside the initial page; title and ID searches
      // must ask the real retained-history owner before claiming no match.
      await search.fill("TODO_RETAINED_0");
      await submit.click();
      await lane.getByText("Retained task 0", {exact: true}).waitFor();
      assert.equal(await lane.getByText("Retained task 84", {exact: true}).count(), 0);
      await page.screenshot({path: resolve(outputDir, "completed-history-search-desktop.png"), animations: "disabled"});
      await search.fill("not-a-task");
      await search.press("Enter");
      await lane.getByText("没有匹配的已完成任务", {exact: true}).waitFor();
      await search.fill("  retained TASK 5  ");
      await submit.click();
      await lane.getByText("Retained task 59", {exact: true}).waitFor();
      await page.setViewportSize({width: 390, height: 844});
      await search.scrollIntoViewIfNeeded();
      assert.ok(await page.locator("body").evaluate(element => element.scrollWidth <= innerWidth + 1));
      await page.screenshot({path: resolve(outputDir, "completed-history-search-mobile.png"), animations: "disabled"});
      await page.setViewportSize({width: 1440, height: 1000});
      // An interrupted older query must never replace the newer search.
      let releaseOld, sawOld, finishedOld;
      const held = new Promise(resolve => { releaseOld = resolve; });
      const started = new Promise(resolve => { sawOld = resolve; });
      const finished = new Promise(resolve => { finishedOld = resolve; });
      await page.route("**/api/chat/completed-todos?*", async route => {
        if (new URL(route.request().url()).searchParams.get("q") !== "todo_retained_1") return route.fallback();
        const response = await fetchWorkspaceBackend(route, origin);
        sawOld();
        await held;
        try { await route.fulfill({response}); } catch { /* The old request was aborted. */ }
        finally { finishedOld(); }
      });
      await search.fill("todo_retained_1");
      await submit.click();
      await started;
      await search.fill("todo_retained_0");
      await submit.click();
      await lane.getByText("Retained task 0", {exact: true}).waitFor();
      releaseOld();
      await finished;
      assert.equal(await lane.getByText("Retained task 1", {exact: true}).count(), 0);
      // A failed first search page is retryable without losing the query.
      let failSearch = true;
      await page.route("**/api/chat/completed-todos?*", async route => {
        if (failSearch && new URL(route.request().url()).searchParams.get("q") === "task 2") {
          failSearch = false;
          return route.fulfill({status: 503, json: {error: "Synthetic read unavailable"}});
        }
        return route.fallback();
      });
      await search.fill("task 2");
      await submit.click();
      await lane.getByText("历史加载失败，已显示的内容仍可查看。", {exact: true}).waitFor();
      await lane.getByRole("button", {name: "重新加载", exact: true}).click();
      await lane.getByText("Retained task 29", {exact: true}).waitFor();
      assert.equal(await search.inputValue(), "task 2");
      await lane.getByRole("button", {name: "清除", exact: true}).click();
      await lane.getByText("Retained task 84", {exact: true}).waitFor();
      let slowFinished = false;
      slowRead = fetch(`${origin}/api/chat/completed-todos?goal_id=slow-goal`).then(async response => {
        assert.equal(response.status, 200);
        return response.json();
      }).finally(() => { slowFinished = true; });
      void slowRead.catch(() => {});
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
      const remoteUrl = new URL(url);
      remoteUrl.searchParams.set("statusUrl", "http://127.0.0.1:8976/status.json");
      const localHistoryReads = [];
      const remote = await openWorkspacePage(browser, remoteUrl.href, {beforeGoto: async (_api, remotePage) => {
        const snapshot = JSON.parse(await readFile(resolve(repoRoot, "examples/status.example.json"), "utf8"));
        await remotePage.route("http://127.0.0.1:8976/status.json*", route => route.fulfill({json: snapshot}));
        remotePage.on("request", request => {
          if (new URL(request.url()).pathname === "/api/chat/completed-todos") localHistoryReads.push(request.url());
        });
      }});
      try {
        await remote.page.locator(".personal-goal-row").first().locator(".personal-goal-link").click();
        await remote.page.getByRole("navigation", {name: "Goal 视图"}).getByRole("button", {name: "任务", exact: true}).click();
        const remoteSearch = remote.page.getByRole("searchbox", {name: "搜索完成历史"});
        await remoteSearch.waitFor();
        assert.equal(await remoteSearch.isDisabled(), true);
        assert.deepEqual(localHistoryReads, [], "Remote source never queries local completed history");
      } finally { await remote.close(); }
      const inspection = await command("inspect");
      assert.equal(inspection.store_unchanged, true);
      assert.equal(inspection.posts, 0);
      assert.equal(api.turnRequests.length, 0);
      assert.equal(api.actionApplies.length, 0);
      assert.deepEqual(context.errors.filter(error => !error.includes("503")), []);
      return {coverageEntries: context.coverageEntries, note: "Real HTTP cached history progresses independently; 503 recovery retains rows, reaches all85 results and starts no Turn."};
    } catch (error) {
      console.error(error);
      if (context) await context.page.screenshot({path: resolve(outputDir, "completed-history-isolation-failed.png"), animations: "disabled"});
      throw error;
    } finally {
      if (!backend.killed && backend.exitCode === null) {
        backend.stdin.write("release\n");
        if (slowRead) await slowRead.catch(() => {});
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
