import assert from "node:assert/strict";
import { resolve } from "node:path";
import { readFile } from "node:fs/promises";
import { outputDir, repoRoot } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

const node = (id, kind, title, state, depth, extra = {}) =>
  ({ node_id: `node_${id}`, kind, title, state, depth, refs: { todo_ids: [`todo_map_${id}`] }, ...extra });
const edge = (from, to, relation, enforcement) =>
  ({ edge_id: `edge_${from}_${to}_${relation}`, from_node_id: `node_${from}`, to_node_id: `node_${to}`, relation, enforcement, reason: "Recorded task relation." });

// A decision gates booking work, completed history feeds an open task, and a
// watch has no recorded link. Only these typed edges may appear as lines.
function goalMap(goalId, limits = {}) {
  return {
    schema_version: "goal_task_map_v0", mode: "read_only", goal_id: goalId,
    limits: { node_limit: 120, emitted_node_count: 7, omitted_node_count: 0, source_truncated: false,
      missing_endpoint_count: 0, cycle_edge_count: 0, topology_complete: true, ...limits },
    nodes: [
      node("gate", "gate", "Approve the venue hold", "open", 0),
      node("reserve", "deliverable", "Reserve the hall", "blocked", 1, { owner_agent: "logistics", task_domain: "booking" }),
      node("deposit", "deliverable", "Release the deposit", "blocked", 2, { owner_agent: "finance" }),
      node("scope", "deliverable", "Agree the event scope", "done", 0, { owner_agent: "producer" }),
      node("venues", "deliverable", "Compare three venues", "done", 1, { owner_agent: "logistics" }),
      node("budget", "deliverable", "Reprice catering", "open", 2, { owner_agent: "finance" }),
      node("watch", "monitor", "Check registration totals", "open", 0, { owner_agent: "logistics" }),
    ],
    edges: [
      edge("reserve", "gate", "depends_on", "typed_lifecycle"),
      edge("deposit", "reserve", "depends_on", "typed_lifecycle"),
      edge("venues", "scope", "continues", "lineage_only"),
      edge("budget", "venues", "continues", "lineage_only"),
      edge("budget", "venues", "depends_on", "typed_condition"),
    ],
  };
}

export const goalWorkMapScenario = {
  id: "goal-work-map",
  async run({ browser, collectCoverage, url }) {
    let limits = {};
    const reads = [];
    const statusFixture = JSON.parse(await readFile(resolve(repoRoot, "examples/status.example.json"), "utf8"));
    const firstGoal = statusFixture.attention_queue.items[0];
    const sourceTodos = goalMap(firstGoal.goal_id).nodes.filter(item => item.kind === "deliverable").map(item => ({
      todo_id: item.refs.todo_ids[0], goal_id: firstGoal.goal_id, role: "agent", task_class: "advancement_task",
      text: item.title, title: item.title, status: item.state, done: item.state === "done", claimed_by: item.owner_agent,
    }));
    firstGoal.agent_todos.items.push(...sourceTodos);
    const routeReview = async (api, page) => {
      for (const todo of sourceTodos) api.todoRequestTexts.set(JSON.stringify([todo.goal_id, todo.todo_id]), todo.text);
      await page.route("**/status.json*", route => {
        if (new URL(route.request().url()).pathname !== "/status.json") return route.fallback();
        return route.fulfill({ json: statusFixture });
      });
      return page.route("**/api/chat/delivery-review?*", route => {
        const goalId = new URL(route.request().url()).searchParams.get("goal_id");
        reads.push(goalId);
        return route.fulfill({ json: { ok: true, goal_id: goalId, observed_at: new Date().toISOString(), graph: null, goal_map: goalMap(goalId, limits), acceptance: null } });
      });
    };
    const desktop = await openWorkspacePage(browser, url, { collectCoverage, beforeGoto: routeReview });
    const { page } = desktop;
    await page.locator(".personal-goal-link").first().click();
    await page.getByRole("button", { name: "概览", exact: true }).click();
    const map = page.locator(".work-map");
    await map.getByRole("heading", { name: "工作地图" }).waitFor();
    const canvas = map.getByRole("region", { name: "工作地图画布" });
    const titles = locator => locator.locator(".work-map-node strong").allInnerTexts();

    assert.deepEqual(new Set(await titles(canvas)), new Set(["Approve the venue hold", "Reserve the hall", "Release the deposit",
      "Compare three venues", "Reprice catering"]), "Current work keeps unfinished items and their direct prerequisites");
    assert.equal(await canvas.locator("path[marker-end]").count(), 3, "Two relations between one pair draw one line; none are invented");
    assert.deepEqual(await titles(map.getByRole("region", { name: "未与其他工作关联" })), ["Check registration totals"]);
    assert.match(await map.locator(".work-map-summary").innerText(), /2\/5\s+项任务已完成.*1 需你决策.*2 受阻.*1 持续监控/s);
    await map.getByRole("button", { name: "1 项已完成或延后的工作已隐藏" }).click();
    assert.equal(await canvas.locator(".work-map-node").count(), 6);
    assert.equal(await canvas.locator("path[marker-end]").count(), 4);
    await map.getByRole("button", { name: "当前工作", exact: true }).click();

    await canvas.focus();
    await page.keyboard.press("Tab");
    await page.keyboard.press("Enter");
    assert.equal(await canvas.locator('.work-map-node[aria-pressed="true"] strong').innerText(), "Approve the venue hold", "Keyboard reaches and selects the first node");
    const inspector = map.getByRole("region", { name: "选中事项" });
    await inspector.getByText("此事项的详情未加载到工作区。").waitFor();
    assert.equal(await inspector.getByRole("button", { name: "打开详情" }).count(), 0, "An unloaded decision is never opened as an agent task");

    await canvas.locator(".work-map-node", { hasText: "Reserve the hall" }).click();
    assert.deepEqual(await inspector.locator(".work-map-relations > div").evaluateAll(columns => columns.map(column =>
      [...column.querySelectorAll("li span")].map(span => span.textContent))), [["Approve the venue hold"], ["Release the deposit"]]);
    const dimmed = await canvas.locator(".work-map-node[data-dimmed] strong").allInnerTexts();
    assert.deepEqual(new Set(dimmed), new Set(["Compare three venues", "Reprice catering"]), "Selection traces only recorded lineage");
    await page.screenshot({ path: resolve(outputDir, "goal-work-map.png"), animations: "disabled" });
    await inspector.getByRole("button", { name: "打开详情" }).click();
    const drawer = page.getByRole("dialog", { name: "Todo 详情" });
    await drawer.getByRole("heading", { name: "Reserve the hall" }).waitFor();
    assert.match(await drawer.innerText(), /logistics/);
    await page.getByRole("button", { name: /关闭详情/ }).click();
    assert.equal(await canvas.locator('.work-map-node[aria-pressed="true"] strong').innerText(), "Reserve the hall", "Closing details returns to the same selection");

    limits = { omitted_node_count: 3, topology_complete: false };
    await page.getByRole("button", { name: "刷新快照", exact: true }).click();
    await map.getByText("部分工作未出现在此地图中。").waitFor();
    assert.equal(await page.locator("[role=alert]", { hasText: "工作区状态已在此快照之后变化" }).count(), 0);
    assert.ok(reads.length <= 4, `Delivery review re-read ${reads.length} times`);
    assert.deepEqual(desktop.errors, []);
    const coverageEntries = await desktop.close();

    const phone = await openWorkspacePage(browser, url, { collectCoverage, beforeGoto: routeReview, viewport: { width: 390, height: 844 }, isMobile: true });
    const navigation = phone.page.getByRole("button", { name: "打开 Goal 导航" });
    if (await navigation.isVisible()) await navigation.click();
    await phone.page.locator(".personal-goal-link").first().click();
    await phone.page.getByRole("button", { name: "概览", exact: true }).click();
    const list = phone.page.locator(".work-map-list");
    await list.waitFor();
    assert.equal(await list.locator("li").count(), 5);
    assert.match(await list.locator("li", { hasText: "Release the deposit" }).innerText(), /之前 Reserve the hall/);
    assert.equal(await phone.page.locator(".work-map-scroll").isVisible(), false);
    assert.equal(await phone.page.getByRole("group", { name: "地图缩放" }).isVisible(), false);
    assert.equal(await phone.page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, "No page-level horizontal overflow");
    await list.scrollIntoViewIfNeeded();
    await phone.page.screenshot({ path: resolve(outputDir, "goal-work-map-mobile.png"), animations: "disabled" });
    coverageEntries.push(...await phone.close());
    return { coverageEntries, note: "Work map shows typed links only, traces lineage, opens details, discloses incomplete coverage and collapses to a list on phones." };
  },
};
