import assert from "node:assert/strict";
import { resolve } from "node:path";
import { spawn } from "node:child_process";
import { mkdtemp, rename, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { createInterface } from "node:readline";
import { resolveTestPython } from "../../scripts/test-python.mjs";
import { outputDir, repoRoot } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

async function requestAuthority(text) {
  const root = await mkdtemp(resolve(tmpdir(), "loopx-map-request-"));
  const child = spawn(resolveTestPython(), ["-u", "-c", `
from pathlib import Path
import json, os, signal, sys, tempfile
root = Path(sys.argv[1]); tempfile.tempdir = str(root)
for key in ("TMPDIR", "TEMP", "TMP"): os.environ[key] = str(root)
sys.path.insert(0, str(Path.cwd() / "tests/control_plane"))
from canonical_authority_fixture import initialize_canonical_authority
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.effect_runtime import restart_effect_runtime
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
runtime, state, registry = root / "runtime", root / "state.md", root / "registry.json"
state.write_text("# Synthetic Goal\\n\\n## Agent Todo\\n")
record = dict(schema_version="todo_item_v0", todo_id="todo_map_reserve", index=1,
    role="agent", status="open", done=False, archive_state="active", text=sys.argv[2],
    source_section="Agent Todo", task_class="advancement_task", claimed_by="logistics")
projection = build_todo_runtime_shadow_projection(goal_id="product-release", todos=[record], handoff_mode="soft_claim")
initialize_canonical_authority(runtime, "product-release", projection, state_path=state, provider="sqlite")
state.unlink()
registry.write_text(json.dumps(dict(common_runtime_root=str(runtime), goals=[dict(id="product-release", repo=str(root), state_file="state.md")])))
before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="product-release")
server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
server.registry_path, server.runtime_root_override, server.verbose = registry, str(runtime), False
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
print(server.server_port, flush=True)
try: server.serve_forever()
finally:
    server.server_close()
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="product-release") == before
    restart_effect_runtime()
`, root, text], { cwd: repoRoot, stdio: ["ignore", "pipe", "pipe"] });
  const lines = createInterface({ input: child.stdout });
  let diagnostic = "";
  child.stderr.on("data", chunk => { diagnostic = (diagnostic + chunk).slice(-3000); });
  try {
    const port = await new Promise((accept, reject) => {
      const timer = setTimeout(() => reject(new Error(`Request authority startup: ${diagnostic}`)), 20_000);
      child.once("error", error => { clearTimeout(timer); reject(error); });
      child.once("exit", () => { clearTimeout(timer); reject(new Error(`Request authority exited: ${diagnostic}`)); });
      lines.once("line", line => { clearTimeout(timer); accept(Number(line)); });
    });
    assert.ok(Number.isInteger(port) && port > 0);
    return { url: `http://127.0.0.1:${port}`, registry: resolve(root, "registry.json"), async close() {
      lines.close();
      let code = child.exitCode;
      if (code === null && child.signalCode === null) {
        const exited = new Promise(accept => child.once("exit", accept));
        child.kill("SIGTERM");
        code = await exited;
      }
      assert.equal(code, 0, `Request authority cleanup: ${diagnostic}`);
      await rm(root, { recursive: true, force: true });
    } };
  } catch (error) {
    lines.close(); child.kill("SIGTERM");
    await rm(root, { recursive: true, force: true });
    throw error;
  }
}

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
    const originalRequest = "Reserve the hall only after approval; confirm the full room capacity.";
    const authority = await requestAuthority(originalRequest);
    try {
      const routeRequest = (api, page) => page.route("**/api/chat/todo/detail?*", async route => {
        const url = new URL(route.request().url());
        api.todoRequestReads.push({ goalId: url.searchParams.get("goal_id"), todoId: url.searchParams.get("todo_id") });
        await route.fulfill({ response: await route.fetch({ url: authority.url + url.pathname + url.search }) });
      });
      const routeReview = (api, page) => page.route("**/api/chat/delivery-review?*", route => {
        const goalId = new URL(route.request().url()).searchParams.get("goal_id");
        reads.push(goalId);
        return route.fulfill({ json: { ok: true, goal_id: goalId, observed_at: new Date().toISOString(), graph: null, goal_map: goalMap(goalId, limits), acceptance: null } });
      });
      const beforeGoto = async (api, page) => { await routeReview(api, page); await routeRequest(api, page); };
      const desktop = await openWorkspacePage(browser, url, { collectCoverage, beforeGoto });
      const { page } = desktop;
      await page.locator(".personal-goal-link", { hasText: "Product Release" }).click();
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
      await drawer.getByRole("heading", { name: originalRequest, exact: true }).waitFor();
      assert.deepEqual(desktop.api.todoRequestReads, [{ goalId: reads[0], todoId: "todo_map_reserve" }],
        "Map details read the exact canonical Task request rather than trusting the projected node title");
      assert.match(await drawer.innerText(), /当前状态与操作仍不可用/);
      assert.equal(await drawer.locator(".personal-task-inspector-fields,.personal-task-inspector-actions").count(), 0,
        "A graph-only Task cannot present snapshot metadata as current or enable mutations");
      await page.screenshot({ path: resolve(outputDir, "goal-map-request-only-desktop.png"), animations: "disabled" });
      await page.getByRole("button", { name: /关闭详情/ }).click();
      assert.equal(await canvas.locator('.work-map-node[aria-pressed="true"] strong').innerText(), "Reserve the hall", "Closing details returns to the same selection");

      const hiddenRegistry = `${authority.registry}.hidden`;
      const errorsBeforeLoss = desktop.errors.length;
      await rename(authority.registry, hiddenRegistry);
      await inspector.getByRole("button", { name: "打开详情" }).click();
      await drawer.getByRole("alert").filter({ hasText: "完整要求读取失败" }).waitFor();
      assert.deepEqual(desktop.errors.splice(errorsBeforeLoss), [
        "Failed to load resource: the server responded with a status of 503 (Service Unavailable)",
      ], "Only the deliberately unavailable source may emit a transport error");
      assert.equal(await drawer.getByRole("heading", { name: originalRequest, exact: true }).count(), 0,
        "A missing source must not retain the previously read canonical request");
      assert.equal(await drawer.locator(".personal-task-inspector-actions").count(), 0);
      await rename(hiddenRegistry, authority.registry);
      await drawer.getByRole("button", { name: "重试读取完整要求", exact: true }).click();
      await drawer.getByRole("heading", { name: originalRequest, exact: true }).waitFor();
      await page.getByRole("button", { name: /关闭详情/ }).click();

      limits = { omitted_node_count: 3, topology_complete: false };
      await page.getByRole("button", { name: "刷新快照", exact: true }).click();
      await map.getByText("部分工作未出现在此地图中。").waitFor();
      assert.equal(await page.locator("[role=alert]", { hasText: "工作区状态已在此快照之后变化" }).count(), 0);
      assert.ok(reads.length <= 4, `Delivery review re-read ${reads.length} times`);
      assert.deepEqual(desktop.errors, []);
      const coverageEntries = await desktop.close();

      const phone = await openWorkspacePage(browser, url, { collectCoverage, beforeGoto, viewport: { width: 390, height: 844 }, isMobile: true });
      const navigation = phone.page.getByRole("button", { name: "打开 Goal 导航" });
      if (await navigation.isVisible()) await navigation.click();
      await phone.page.locator(".personal-goal-link", { hasText: "Product Release" }).click();
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
      await list.locator(".work-map-node", { hasText: "Reserve the hall" }).click();
      await phone.page.getByRole("region", { name: "选中事项" }).getByRole("button", { name: "打开详情" }).click();
      const phoneDrawer = phone.page.getByRole("dialog", { name: "Todo 详情" });
      await phoneDrawer.getByRole("heading", { name: originalRequest, exact: true }).waitFor();
      assert.equal(await phoneDrawer.locator(".personal-task-inspector-actions").count(), 0);
      await phone.page.screenshot({ path: resolve(outputDir, "goal-map-request-only-mobile.png"), animations: "disabled" });
      coverageEntries.push(...await phone.close());
      return { coverageEntries, note: "Work map opens canonical requests outside the status summary, withdraws lost sources, retries without mutation, and retains typed links and mobile navigation." };
    } finally { await authority.close(); }
  },
};
