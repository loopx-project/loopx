import assert from "node:assert/strict";
import { execFile, spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createInterface } from "node:readline";
import { promisify } from "node:util";
import { repoRoot, outputDir } from "./fixture.mjs";
import { resolveTestPython } from "../../scripts/test-python.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

const actionText = "在桌面 App 中手动创建剩余的 3 个角色会话";
const idlessText = "核对本机备份目录是否可写";

// The compiled UI's own payloads reach the real ChatActionService and File
// hard-lease owner. Other unrelated APIs retain the shared browser fixture.
async function boundActorJourney(browser, url, registerDefault) {
  const root = await mkdtemp(join(tmpdir(), "loopx-user-action-"));
  const child = spawn(resolveTestPython({ repoRoot }), ["-u", "-c", `
import json, pathlib, sys
sys.path.insert(0, 'tests/control_plane')
from canonical_authority_fixture import initialize_canonical_authority
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.chat_actions import ChatActionService
from loopx.chat_action_store import ChatActionStore
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.todos import add_goal_todo, list_goal_todos
r=pathlib.Path(sys.argv[1]); runtime=r/'runtime'; registry=r/'registry.json'; state=r/'state.md'
state.write_text('---\\ngoal_id: product-release\\n---\\n\\n## Agent Todo\\n')
agents=['codex-review','codex-delivery'] + (['codex'] if sys.argv[2]=='true' else [])
registry.write_text(json.dumps({'common_runtime_root':str(runtime),'goals':[{'id':'product-release','repo':str(r),'state_file':'state.md','coordination':{'agent_model':'peer_v1','registered_agents':agents}}]}))
ids={}
for operation in ['complete','cancel']:
    target=add_goal_todo(registry_path=registry,goal_id='product-release',role='agent',text='Dependent '+operation,status='blocked',task_class='advancement_task',claimed_by='codex-delivery')
    action=add_goal_todo(registry_path=registry,goal_id='product-release',role='user',text='Handle '+operation,task_class='user_action',bound_agent='codex-delivery',unblocks_todo_id=target['todo_id'])
    ids[operation]=[action['todo_id'],target['todo_id']]
projection=build_todo_runtime_shadow_projection(goal_id='product-release',handoff_mode='hard_lease',todos=list_goal_todos(registry_path=registry,goal_id='product-release')['todos'])
config=json.loads(registry.read_text()); config['goals'][0]['coordination']['handoff_mode']='hard_lease'; registry.write_text(json.dumps(config))
initialize_canonical_authority(runtime,'product-release',projection,state_path=state,provider='file')
s=ChatHTTPServer(('127.0.0.1',0),ChatRequestHandler)
s.registry_path,s.runtime_root,s.runtime_root_override,s.verbose=registry,runtime,str(runtime),False
s.selected_goal_id,s.scan_roots,s.limit='product-release',[],10
s.action_store=ChatActionStore(r/'actions')
s.action_service=ChatActionService(store=s.action_store,registry_path=registry)
print(json.dumps({'port':s.server_port,'ids':ids}),flush=True); s.serve_forever()
`, root, String(registerDefault)], { cwd: repoRoot, env: { ...process.env, PYTHONPATH: repoRoot, LOOPX_USAGE_PING: "0" }, stdio: ["ignore", "pipe", "pipe"] });
  const exited = once(child, "close");
  const lines = createInterface({ input: child.stdout });
  let diagnostics = "", context, timer;
  child.stderr.on("data", data => { diagnostics = (diagnostics + data).slice(-4000); });
  try {
    const ready = await Promise.race([
      once(lines, "line").then(([line]) => JSON.parse(line)),
      exited.then(() => { throw new Error(`User action backend exited: ${diagnostics}`); }),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`User action backend timeout: ${diagnostics}`)), 30000); }),
    ]);
    clearTimeout(timer);
    const backend = `http://127.0.0.1:${ready.port}`;
    const read = async id => {
      const role = Object.values(ready.ids).some(([actionId]) => actionId === id) ? "user" : "agent";
      const { stdout } = await promisify(execFile)(resolveTestPython({ repoRoot }), ["-c", "from loopx.entrypoint import main; main()",
        "--format", "json", "--registry", join(root, "registry.json"), "todo", "list", "--goal-id", "product-release", "--role", role, "--todo-id", id],
        { cwd: repoRoot, env: { ...process.env, PYTHONPATH: repoRoot, LOOPX_USAGE_PING: "0" }, timeout: 30000 });
      const result = JSON.parse(stdout); assert.ok(result.ok && result.matched); return result.todo;
    };
    const previews = [];
    let applications = 0;
    context = await openWorkspacePage(browser, url, { beforeGoto: async (_api, page) => {
      for (const pattern of ["**/status.json*", "**/api/actions**", "**/api/chat/todo/detail?*", "**/api/chat/completed-todos?*"]) {
        await page.route(pattern, async route => {
          const request = route.request(), parsed = new URL(request.url());
          if (pattern === "**/status.json*" && parsed.pathname !== "/status.json") { await route.fallback(); return; }
          if (parsed.pathname === "/api/actions/preview") previews.push(request.postDataJSON());
          if (parsed.pathname.endsWith("/apply")) applications++;
          const response = await route.fetch({ url: `${backend}${parsed.pathname}${parsed.search}` });
          await route.fulfill({ response });
        });
      }
    }});
    const { page } = context, drawer = page.locator(".personal-context-drawer");
    for (const operation of ["complete", "cancel"]) {
      const [actionId, targetId] = ready.ids[operation];
      // Registration alone never lets an unrelated peer handle this request.
      const denied = await fetch(`${backend}/api/actions/preview`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action_kind: "todo.update", normalized_parameters: { agent_id: "codex-review", goal_id: "product-release", todo_id: actionId, operation: "complete" }, context: {}, summary: "Wrong actor", idempotency_key: `wrong-${operation}` }) });
      assert.equal(denied.status, 400);
      assert.equal((await read(actionId)).status, "open");
      await page.getByRole("button", { name: /LoopX 管家/ }).first().click();
      await page.getByTestId("personal-home-lane-needs_you").locator(".personal-home-goal-card").first().click();
      await page.getByRole("button", { name: /^任务$/ }).first().click();
      await page.getByText(`Handle ${operation}`, { exact: true }).first().click();
      if (operation === "cancel") await drawer.locator("summary", { hasText: "其他处理" }).click();
      await drawer.getByRole("button", { name: operation === "complete" ? "我已完成" : "不再需要", exact: true }).click();
      await page.locator('[data-context-kind="proposal"]').waitFor({ state: "visible" });
      assert.equal(previews.at(-1).normalized_parameters.agent_id, "codex-delivery");
      assert.equal((await read(actionId)).status, "open", "Native preview writes nothing");
      await page.screenshot({ path: resolve(outputDir, `user-action-native-${registerDefault}-${operation}.png`), animations: "disabled" });
      try {
        await drawer.getByRole("button", { name: operation === "complete" ? "确认并应用" : "确认撤回", exact: true }).click({ timeout: 5000 });
      } catch (error) {
        throw new Error(`${error.message}; drawer=${await drawer.innerText()}; errors=${context.errors.join(" | ")}`);
      }
      await page.getByRole("button", { name: "查看更新后的 Goal" }).click();
      assert.equal((await read(actionId)).status, "done");
      assert.equal((await read(targetId)).status, operation === "complete" ? "open" : "blocked");
      await page.reload({ waitUntil: "networkidle" });
      await page.getByTestId("personal-goal-home").waitFor();
      assert.equal((await read(actionId)).status, "done", "Reload preserves canonical completion");
    }
    assert.equal(applications, 2, "Exactly one confirmed write per request");
  } finally {
    clearTimeout(timer); lines.close();
    if (context) await context.close();
    if (child.exitCode === null && child.signalCode === null) child.kill("SIGTERM");
    await exited;
    await rm(root, { recursive: true, force: true });
  }
}

// A User action in "needs you" is handled in place through the existing typed
// Todo actions: done / defer / no longer needed, each previewed and confirmed once.
export const attentionUserActionScenario = {
  id: "attention-user-action",
  async run({ browser, collectCoverage, url }) {
    const { api, page, close, errors } = await openWorkspacePage(browser, url, { apiOptions: { userActionAttention: true }, collectCoverage });
    const drawer = page.locator(".personal-context-drawer");
    async function openRequest(text) {
      await page.getByRole("button", { name: /LoopX 管家/ }).first().click();
      await page.getByTestId("personal-home-lane-needs_you").locator(".personal-home-goal-card").first().click();
      await page.getByRole("button", { name: /^任务$/ }).first().click();
      await page.getByText(text, { exact: true }).first().click();
      await drawer.getByText(text, { exact: true }).first().waitFor({ state: "visible" });
    }
    try {
      await openRequest(actionText);
      for (const forbidden of ["批准", "拒绝", "解释此决定"]) {
        assert.equal(await drawer.getByRole("button", { name: forbidden, exact: true }).count(), 0, `A User action must not offer "${forbidden}"`);
      }
      const done = drawer.getByRole("button", { name: "我已完成", exact: true });
      await drawer.getByRole("button", { name: "在对话中回复", exact: true }).waitFor({ state: "visible" });

      // Failure: nothing is written, the error is visible, and the same action recovers.
      const writesBefore = api.durableWriteCount;
      api.failNextActionPreview = true;
      await done.click();
      await drawer.getByRole("alert").getByText(/未能准备这项变更.*没有保存任何内容/u).waitFor({ state: "visible" });
      assert.equal(api.durableWriteCount, writesBefore, "A failed preview must not write");
      await done.click();
      await page.locator('[data-context-kind="proposal"]').waitFor({ state: "visible" });
      const completion = api.actionPreviews.at(-1);
      assert.equal(completion.action_kind, "todo.update");
      assert.equal(completion.normalized_parameters.operation, "complete");
      assert.equal(completion.normalized_parameters.todo_id, "todo-browser-user-action");
      assert.equal(completion.normalized_parameters.agent_id, "codex-delivery", "Uses the request's bound Agent, never the first available Agent");
      assert.equal(api.durableWriteCount, writesBefore, "Preview waits for owner confirmation");
      await page.locator('[data-context-kind="proposal"]').getByRole("button", { name: "确认并应用" }).click();
      await page.getByRole("button", { name: "查看更新后的 Goal" }).click();
      assert.equal(api.durableWriteCount, writesBefore + 1, "Confirmation writes exactly once");
      await page.getByRole("button", { name: /^任务$/ }).first().click();
      await page.getByText(idlessText, { exact: true }).first().waitFor({ state: "visible" });
      assert.equal(await page.getByText(actionText, { exact: true }).count(), 0, "The completed request leaves needs-you on readback");

      // Without a stable todo_id the drawer explains why and offers the conversation instead.
      await openRequest(idlessText);
      await drawer.getByText(/缺少稳定的 Todo 标识/u).waitFor({ state: "visible" });
      assert.equal(await drawer.getByRole("button", { name: "我已完成", exact: true }).count(), 0, "No write without a stable Todo identity");
      const previews = api.actionPreviews.length;
      await drawer.getByRole("button", { name: "在对话中回复", exact: true }).click();
      await page.locator('[data-goal-panel="chat"]').waitFor({ state: "visible" });
      await page.waitForFunction((expected) => document.querySelector("textarea")?.value === expected, `关于「${idlessText}」：`);
      assert.equal(api.actionPreviews.length, previews, "Replying drafts a message; it previews no write");
      assert.equal(api.turnRequests.length, 0, "Replying never sends without the owner");

      // Defer and "no longer needed" reuse the same owners on a fresh request.
      const fresh = await openWorkspacePage(browser, url, { apiOptions: { userActionAttention: true } });
      try {
        const freshDrawer = fresh.page.locator(".personal-context-drawer");
        await fresh.page.getByRole("button", { name: /LoopX 管家/ }).first().click();
        await fresh.page.getByTestId("personal-home-lane-needs_you").locator(".personal-home-goal-card").first().click();
        await fresh.page.getByRole("button", { name: /^任务$/ }).first().click();
        for (const [label, check] of [
          ["暂缓到明天 9:00", (preview) => {
            assert.equal(preview.action_kind, "todo.update");
            assert.equal(preview.normalized_parameters.operation, "defer");
            assert.match(preview.normalized_parameters.resume_when, /^resume_at:\d{4}-\d{2}-\d{2}T09:00:00[+-]\d{2}:\d{2}$/u);
            assert.ok(Date.parse(preview.normalized_parameters.resume_when.slice(10)) > Date.now());
          }],
          ["不再需要", (preview) => {
            assert.equal(preview.action_kind, "gate.resolve");
            assert.equal(preview.normalized_parameters.decision, "cancel");
            assert.equal(preview.normalized_parameters.todo_id, "todo-browser-user-action");
          }],
        ]) {
          await fresh.page.getByText(actionText, { exact: true }).first().click();
          await freshDrawer.locator("summary", { hasText: "其他处理" }).click();
          await freshDrawer.getByRole("button", { name: label, exact: true }).click();
          await fresh.page.locator('[data-context-kind="proposal"]').waitFor({ state: "visible" });
          check(fresh.api.actionPreviews.at(-1));
          assert.equal(fresh.api.actionPreviews.at(-1).normalized_parameters.agent_id, "codex-delivery");
          await fresh.page.locator(".personal-drawer-close").click();
        }
        assert.equal(fresh.api.durableWriteCount, 0, "Opening previews never writes");
      } finally {
        await fresh.close();
      }

      // A User gate keeps its approve/reject decision.
      await openRequest("确认本轮独立审查范围");
      await drawer.getByRole("button", { name: "批准", exact: true }).waitFor({ state: "visible" });
      assert.equal(await drawer.getByRole("button", { name: "我已完成", exact: true }).count(), 0);
      assert.deepEqual(errors.filter((message) => !/Failed to load resource/u.test(message)), [], "Only the injected preview failure may log");
      await boundActorJourney(browser, url, false);
      await boundActorJourney(browser, url, true);
      return { coverageEntries: await close(), note: "User action done/defer/cancel/reply with failure recovery and readback" };
    } catch (error) {
      await close();
      throw error;
    }
  },
};
