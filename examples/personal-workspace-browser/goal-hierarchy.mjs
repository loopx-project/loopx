import assert from "node:assert/strict";
import {mkdtemp, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join, resolve} from "node:path";
import {outputDir} from "./fixture.mjs";
import {startHistoryOwner} from "./research-history.mjs";
import {fetchWorkspaceBackend, openWorkspacePage} from "./scenario-context.mjs";

export const goalHierarchyScenario = {
  id: "goal-hierarchy",
  async run({browser, collectCoverage, url}) {
    const root = await mkdtemp(join(tmpdir(), "loopx-goal-tree-"));
    let owner, ownerB, context, denied = false;
    const statusReads = [];
    const subtreeGoalIds = ["calibration", "graph", "portfolio", "research"];
    let inFlightStatusReads = 0;
    const waitForStatusReads = async (expected, description) => {
      const deadline = Date.now() + 15_000;
      while (Date.now() < deadline) {
        if (inFlightStatusReads === 0 && expected.every(id => statusReads.includes(id))) return;
        await new Promise(resolve => setTimeout(resolve, 25));
      }
      throw new Error(`timed out waiting for ${description}; reads=${JSON.stringify(statusReads)}; in_flight=${inFlightStatusReads}`);
    };
    const captures = [
      ["research", "Research · synthetic"],
      ["portfolio", "Portfolio", "research"],
      ["calibration", "Calibration", "portfolio"],
      ["graph", "Graph", "research"],
      ["daily", "Daily work"],
      ["orphan", "Unresolved parent", "not-readable"],
      ["cycle-a", "Cycle A", "cycle-b"],
      ["cycle-b", "Cycle B", "cycle-a"],
    ];
    try {
      const paths = await Promise.all(captures.map(async ([goal_id, display_name, parent_goal_id]) => {
        const path = join(root, `${goal_id}.json`);
        await writeFile(path, JSON.stringify({goal_id, display_name, parent_goal_id, events: []}));
        return path;
      }));
      owner = await startHistoryOwner(paths, {workspaceDirectory: true});
      // A second, independent status backend with its own "research" root so a
      // history entry can restore a source and a root scope together.
      const remotePaths = await Promise.all([["research", "Research"], ["operations", "Operations"],
        ["operations-child", "Operations child", "operations"]]
        .map(async ([goal_id, display_name, parent_goal_id]) => {
          const path = join(root, `remote-${goal_id}.json`);
          await writeFile(path, JSON.stringify({goal_id, display_name, parent_goal_id, events: []}));
          return path;
        }));
      ownerB = await startHistoryOwner(remotePaths, {workspaceDirectory: true});
      const directoryResponse = await fetch(`${owner.base}/status.json?view=workspace-directory`);
      assert.equal(directoryResponse.status, 200);
      const directory = await directoryResponse.json();
      assert.deepEqual(directory.goals.map(goal => [goal.id, goal.parent_goal_id]).sort(),
        captures.map(([id, _name, parent]) => [id, parent]).sort(),
        "The real directory preserves every parent, including orphans and cycles");
      const entry = new URL(url);
      const researchFocus = {research_lens: "graph", research_goal: "calibration", research_node: JSON.stringify(["source-one", "source-two"]),
        research_pack: "a".repeat(64), research_analysis_goal: "calibration"};
      for (const [key, value] of Object.entries(researchFocus)) entry.searchParams.set(key, value);
      context = await openWorkspacePage(browser, entry.href, {collectCoverage, beforeGoto: async (_api, page) => {
        await page.addInitScript(({statusUrl}) => {
          localStorage.setItem("loopx-status-source-catalog-v1", JSON.stringify({
            schemaVersion: 1,
            sources: [{hostAlias: "remote-b", kind: "ssh_tunnel", label: "Remote B", statusUrl}],
          }));
        }, {statusUrl: `${ownerB.base}/status.json`});
        await page.route(new URL("/ssh-hosts", url).href, route => route.fulfill({
          contentType: "application/json",
          json: {ok: true, schema_version: "ssh_host_catalog_v0", hosts: [{alias: "remote-b"}]},
          status: 200,
        }));
        await page.route(new URL("/api/ssh-source/ensure", url).href, route => route.fulfill({
          contentType: "application/json",
          json: {ok: true, remote_started: true, status_url: `${ownerB.base}/status.json`, tunnel_required: true},
          status: 200,
        }));
        await page.route(target => target.pathname === "/status.json", async route => {
          inFlightStatusReads += 1;
          try {
            const selected = new URL(route.request().url()).searchParams.get("goal_id");
            if (selected) statusReads.push(selected);
            if (denied && new URL(route.request().url()).searchParams.get("goal_id") === "graph") {
              return route.fulfill({status: 403, json: {ok: false, error_code: "workspace_status_access_denied"}});
            }
            const response = await fetchWorkspaceBackend(route, owner.base);
            await route.fulfill({response});
          } finally {
            inFlightStatusReads -= 1;
          }
        });
        await page.route(`${ownerB.base}/status.json*`, async route => {
          const response = await fetch(route.request().url());
          await route.fulfill({status: response.status, headers: {
            "access-control-allow-origin": "*", "content-type": "application/json"}, body: await response.text()});
        });
      }});
      const {page, api} = context;
      const row = id => page.locator(`[data-reorder-goal="${id}"]`);
      const assertResearchFocus = () => {
        const query = new URL(page.url()).searchParams;
        for (const [key, value] of Object.entries(researchFocus)) assert.equal(query.get(key), value, `${key} survives workspace navigation`);
      };
      const assertTreeLayout = async () => {
        const bounds = await page.locator(".personal-goal-row").evaluateAll(rows => rows.map(row => {
          const link = row.querySelector(".personal-goal-link").getBoundingClientRect();
          const control = row.querySelector(".personal-goal-lifecycle")?.getBoundingClientRect();
          return {id: row.dataset.reorderGoal, x: link.x, top: link.top, bottom: link.bottom, control};
        }));
        for (const row of bounds) if (row.control) assert(row.control.top >= row.top && row.control.bottom <= row.bottom,
          `${row.id}: lifecycle controls stay alongside the Goal on narrow screens`);
        assert.equal(bounds.find(row => row.id === "portfolio").x, bounds.find(row => row.id === "graph").x, "Siblings share one indentation");
        assert(bounds.find(row => row.id === "calibration").x > bounds.find(row => row.id === "portfolio").x, "A deeper Goal is visibly nested");
      };
      await row("research").waitFor();
      assert.equal(await row("portfolio").count(), 0, "Children start folded on the ordinary workspace");
      for (const id of ["daily", "orphan", "cycle-a", "cycle-b"]) await row(id).waitFor();
      await page.screenshot({path: resolve(outputDir, "goal-hierarchy-overview.png"), animations: "disabled"});
      await waitForStatusReads(captures.map(([id]) => id), "initial workspace reads");
      await page.waitForLoadState("networkidle");
      statusReads.length = 0;
      await row("research").locator(".personal-goal-link").click();
      await page.getByRole("button", {name: "只看此 Goal 及子 Goal", exact: true}).click();
      await page.waitForURL(target => target.searchParams.get("rootGoalId") === "research");
      assertResearchFocus();
      await row("portfolio").waitFor();
      await waitForStatusReads(subtreeGoalIds, "scoped subtree reads");
      await page.waitForLoadState("networkidle");
      assert(statusReads.every(id => ["calibration", "graph", "portfolio", "research"].includes(id)),
        "reusing loaded subtree snapshots must not open unrelated Goal state");
      for (const id of ["daily", "orphan", "cycle-a", "cycle-b"]) assert.equal(await row(id).count(), 0);
      assert.equal(await page.locator(".personal-manager-link").count(), 0);
      await row("portfolio").getByRole("button", {name: "展开子 Goal：Portfolio", exact: true}).focus();
      await page.keyboard.press("Enter");
      await row("calibration").locator(".personal-goal-link").click();
      await waitForStatusReads([], "status reads before reload");
      statusReads.length = 0;
      await page.reload({waitUntil: "networkidle"});
      await waitForStatusReads(subtreeGoalIds, "reloaded subtree reads");
      await row("calibration").locator('[aria-current="page"]').waitFor();
      assert.deepEqual([...new Set(statusReads)].sort(), ["calibration", "graph", "portfolio", "research"],
        "opening a scoped workspace reads its recursive subtree without opening unrelated Goal state");
      assert.equal(new URL(page.url()).searchParams.get("rootGoalId"), "research");
      assertResearchFocus();
      await assertTreeLayout();
      await page.screenshot({path: resolve(outputDir, "goal-hierarchy-focused.png"), animations: "disabled"});
      await page.getByRole("button", {name: "调整 Goal 顺序", exact: true}).click();
      assert(await page.getByRole("button", {name: "上移 Calibration", exact: true}).isDisabled(), "A singleton cannot move into its parent's siblings");
      assert(await page.getByRole("button", {name: "下移 Calibration", exact: true}).isDisabled());
      await page.getByRole("button", {name: "上移 Graph", exact: true}).click();
      assert.deepEqual(await page.locator("[data-reorder-goal]").evaluateAll(rows => rows.map(row => row.dataset.reorderGoal)),
        ["research", "graph", "portfolio", "calibration"], "Sorting moves only siblings and preserves the subtree");
      await page.getByRole("button", {name: "调整 Goal 顺序", exact: true}).click();
      await page.getByRole("button", {name: "全部 Goals", exact: true}).click();
      await row("daily").waitFor();
      await page.goBack({waitUntil: "networkidle"});
      await row("calibration").locator('[aria-current="page"]').waitFor();
      assert.equal(await row("daily").count(), 0, "Back restores the root scope and selected child");
      assertResearchFocus();
      denied = true;
      await page.getByRole("button", {name: "刷新状态", exact: true}).click();
      await row("graph").and(page.locator("[data-load-error]")).waitFor();
      assert.equal(await row("daily").count(), 0, "A denied child does not widen the display scope");
      denied = false;
      await page.getByRole("button", {name: "刷新状态", exact: true}).click();
      await row("graph").and(page.locator("[data-load-error]")).waitFor({state: "hidden"});
      await row("graph").waitFor();
      await page.setViewportSize({width: 390, height: 844});
      await page.reload({waitUntil: "networkidle"});
      await page.getByRole("button", {name: "打开 Goal 导航", exact: true}).click();
      await row("calibration").locator('[aria-current="page"]').waitFor();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await assertTreeLayout();
      await page.screenshot({path: resolve(outputDir, "goal-hierarchy-mobile.png"), animations: "disabled"});
      const unavailable = new URL(page.url());
      unavailable.searchParams.set("rootGoalId", "missing");
      unavailable.searchParams.set("goalId", "daily");
      statusReads.length = 0;
      await page.goto(unavailable.href, {waitUntil: "networkidle"});
      await page.getByRole("alert").filter({hasText: "当前来源中此 Goal 范围不可用"}).waitFor();
      assert.equal(await page.locator(".personal-goal-row").count(), 0);
      assert.equal(statusReads.length, 0, "an absent root never fans out to unrelated Goal state");
      assert.equal(await page.getByTestId("personal-first-goal").count(), 0);
      assert.equal(await page.locator(".personal-manager-greeting, .personal-composer-wrap").count(), 0,
        "An unavailable research root must not open an unrelated steward conversation");
      assert.equal(await page.getByRole("navigation", {name: "管家视图", exact: true}).count(), 0,
        "An unavailable research root cannot offer unrelated steward navigation");
      assert.notEqual(await page.locator(".personal-channel-title h1").textContent(), "LoopX 管家");
      unavailable.searchParams.set("rootGoalId", "../missing");
      await page.goto(unavailable.href, {waitUntil: "networkidle"});
      await page.getByRole("alert").filter({hasText: "当前来源中此 Goal 范围不可用"}).waitFor();
      assert.equal(await page.locator(".personal-goal-row").count(), 0, "An invalid scope cannot silently reopen all Goals");
      await page.setViewportSize({width: 1512, height: 982});
      for (const invalid of ["[]", '[""]', '["research"]', "{}", ""]) {
        unavailable.searchParams.set("rootGoalId", invalid);
        await page.goto(unavailable.href, {waitUntil: "networkidle"});
        await page.getByRole("alert").filter({hasText: "当前来源中此 Goal 范围不可用"}).waitFor();
        assert.equal(await page.locator(".personal-goal-row").count(), 0, `${invalid}: decoded values cannot widen the scope`);
        assert.equal(await page.locator(".personal-manager-greeting, .personal-composer-wrap").count(), 0);
        assert.equal(await page.getByRole("navigation", {name: "管家视图", exact: true}).count(), 0);
        assert.notEqual(await page.locator(".personal-channel-title h1").textContent(), "LoopX 管家");
        await page.getByRole("button", {name: "全部 Goals", exact: true}).click();
        await row("daily").waitFor();
        await page.goBack({waitUntil: "networkidle"});
        await page.getByRole("alert").filter({hasText: "当前来源中此 Goal 范围不可用"}).waitFor();
        assert.equal(await page.locator(".personal-goal-row").count(), 0, "Back preserves an explicitly invalid scope");
      }
      // A history entry restores the source and the root scope together. The
      // restored route must drive the read; preferring the still-loaded source
      // would re-read Remote B with source A's root and strand the page there.
      const scopedA = new URL(url);
      scopedA.searchParams.set("statusUrl", "/status.json");
      scopedA.searchParams.set("rootGoalId", "research");
      await page.goto(scopedA.href, {waitUntil: "networkidle"});
      await row("portfolio").waitFor();
      const sourceSelect = page.getByRole("combobox", {name: "选择控制面来源"});
      await sourceSelect.click();
      await page.getByRole("listbox", {name: "选择控制面来源"})
        .getByRole("option", {name: "Remote B", exact: true}).click();
      await page.waitForURL(target => target.searchParams.get("statusUrl")?.includes(`:${ownerB.port}/`));
      await page.waitForLoadState("networkidle");
      assert.equal(new URL(page.url()).searchParams.get("rootGoalId"), "research",
        "switching sources keeps the selected root");
      assert.equal(await row("portfolio").count(), 0, "Remote B has no children under its own research root");
      await page.getByRole("button", {name: "全部 Goals", exact: true}).click();
      await row("operations").waitFor();
      await row("operations").locator(".personal-goal-link").click();
      await page.getByRole("button", {name: "只看此 Goal 及子 Goal", exact: true}).click();
      await page.waitForURL(target => target.searchParams.get("rootGoalId") === "operations");
      await page.waitForLoadState("networkidle");
      await row("operations-child").waitFor();
      await page.locator(".personal-status-source-meta .is-connected").waitFor();
      await page.evaluate(() => history.go(-4));
      await page.waitForURL(target => target.searchParams.get("statusUrl") === "/status.json"
        && target.searchParams.get("rootGoalId") === "research").catch(async error => {
        throw new Error(`${error.message}; url=${page.url()}; errors=${context.errors.join(" | ")}; body=${(await page.locator("body").innerText()).slice(0, 1000)}`);
      });
      await page.waitForLoadState("networkidle");
      await row("portfolio").waitFor().catch(async error => {
        throw new Error(`${error.message}; url=${page.url()}; errors=${context.errors.join(" | ")}; body=${(await page.locator("body").innerText()).slice(0, 1000)}`);
      });
      assert.equal(await row("operations").count(), 0,
        "restoring the combined source and root entry returns to source A");
      assert.equal(new URL(new URL(page.url()).searchParams.get("statusUrl"), page.url()).href,
        new URL("/status.json", page.url()).href, "the restored route keeps source A");
      assert.equal(new URL(page.url()).searchParams.get("rootGoalId"), "research",
        "the restored scope still selects source A's research root");
      assert.equal(await sourceSelect.locator(".personal-select-value").innerText(), "本机",
        "the restored history entry selects source A again");
      assert.equal(api.turnRequests.length, 0);
      assert.equal(api.actionApplies.length, 0);
      const readback = await (await fetch(`${owner.base}/fixture/readback`)).json();
      assert.equal(readback.source_unchanged, true);
      assert.equal(readback.mutation_attempts, 0);
      const errors = context.errors.filter(error => !error.includes("403 (Forbidden)"));
      assert.deepEqual(errors, []);
      return {coverageEntries: await context.close(), note: "Real directory/status parent transport; folded recursive navigation, root-scoped existing workspace, reload/Back, denied-child recovery, orphan/cycle retention, narrow keyboard navigation; zero Turns/actions/source mutation"};
    } finally {
      await context?.close();
      await owner?.close();
      await ownerB?.close();
      await rm(root, {recursive: true, force: true});
    }
  },
};
