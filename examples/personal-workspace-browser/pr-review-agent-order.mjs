import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {once} from "node:events";
import {mkdtemp, readFile, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join, resolve} from "node:path";
import {createInterface} from "node:readline";
import {repoRoot, outputDir} from "./fixture.mjs";
import {openWorkspacePage} from "./scenario-context.mjs";
import {resolveTestPython} from "../../scripts/test-python.mjs";

// Configuration uses the real source writer and an isolated shared registry.
// Unrelated workspace UI uses the normal fixture; no GitHub/Host/model calls.
const serverCode = `
import json,pathlib,sys
from loopx.chat_server import ChatHTTPServer,ChatRequestHandler
r=pathlib.Path(sys.argv[1]); runtime=r/'runtime'; registry=r/'.loopx/registry.json'
registry.parent.mkdir()
registry.write_text(json.dumps({'common_runtime_root':str(runtime),'goals':[{'id':'product-release','repo':str(r),'coordination':{'registered_agents':['a','b','code','docs','recovery','research','delivery']},'control_plane':{'pull_request_review':{'schema_version':'pull_request_review_goal_configuration_v0','agent_orders':{'a':'forward'}}}}]}))
p=runtime/'machine/configuration.json';p.parent.mkdir(parents=True)
p.write_text(json.dumps({'schema_version':'loopx_machine_configuration_v0','namespaces':{'pull_request_review':{'schema_version':'pull_request_review_machine_defaults_v0','wait_for_ci':False,'review_order':'forward'}}}))
s=ChatHTTPServer(('127.0.0.1',0),ChatRequestHandler);s.registry_path=registry;s.runtime_root=runtime;s.runtime_root_override=str(runtime);s.verbose=False
print(json.dumps({'port':s.server_address[1]}),flush=True);s.serve_forever()
`;

export const prReviewAgentOrderScenario = {
  id: "pr-review-agent-order",
  async run({browser, url}) {
    const root = await mkdtemp(join(tmpdir(), "loopx-review-order-"));
    const server = spawn(resolveTestPython({repoRoot}), ["-u", "-c", serverCode, root], {
      cwd: repoRoot, env: {...process.env, PYTHONPATH: repoRoot}, stdio: ["ignore", "pipe", "pipe"],
    });
    let diagnostics = "", context, timer;
    server.stderr.on("data", data => {diagnostics = (diagnostics + data).slice(-4000);});
    const closed = once(server, "close");
    const lines = createInterface({input: server.stdout});
    try {
      const ready = await Promise.race([
        once(lines, "line").then(([line]) => JSON.parse(line)),
        closed.then(() => {throw new Error(`review configuration server exited: ${diagnostics}`);}),
        new Promise((_, reject) => {timer = setTimeout(() => reject(new Error(`review configuration startup timeout: ${diagnostics}`)), 30000);}),
      ]);
      clearTimeout(timer);
      assert.ok(ready.port > 0);
      context = await openWorkspacePage(browser, url, {beforeGoto: async (_api, page) => {
        await page.route("**/api/chat/goal-configuration**", async route => {
          const request = route.request(), parsed = new URL(request.url());
          const response = await route.fetch({url: `http://127.0.0.1:${ready.port}${parsed.pathname}${parsed.search}`});
          await route.fulfill({response});
        });
      }});
      const {page} = context;
      await page.getByRole("button", {name: "设置", exact: true}).click();
      await page.locator(".personal-settings-tabs").getByRole("button", {name: "能力中心", exact: true}).click();
      await page.getByRole("radio", {name: "单个 Goal", exact: true}).check();
      await page.getByRole("combobox", {name: "目标 Goal", exact: true}).selectOption("product-release");
      await page.getByRole("navigation", {name: "Goal 能力目录"}).getByRole("button", {name: /Pull-request Review/}).click();
      const detail = page.locator(".personal-capability-detail");
      for (const agent of ["a", "b", "code", "docs", "recovery", "research", "delivery"]) {
        try { await detail.getByLabel(agent, {exact: true}).waitFor({timeout: 5000}); }
        catch (error) { throw new Error(`${error.message}; detail=${await detail.innerText()}; errors=${context.errors.join(" | ")}`); }
      }
      await detail.getByLabel("b", {exact: true}).selectOption("reverse");
      await detail.getByRole("button", {name: "预览变更", exact: true}).click();
      await detail.getByText("锁定 revision 的变更预览", {exact: true}).waitFor();
      const firstSaved = page.waitForResponse(response => response.url().endsWith("goal-configuration/apply"));
      const reloaded = page.waitForResponse(response => new URL(response.url()).pathname === "/api/chat/goal-configuration" && response.request().method() === "GET");
      await detail.getByRole("button", {name: "应用此预览", exact: true}).click();
      assert.equal((await firstSaved).status(), 200);
      await (await reloaded).finished();
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const readback = async () => JSON.parse(await readFile(join(root, ".loopx/registry.json"), "utf8")).goals[0].control_plane.pull_request_review;
      assert.deepEqual((await readback()).agent_orders, {a: "forward", b: "reverse"});
      assert.equal(Object.hasOwn(await readback(), "wait_for_ci"), false, "Agent save must retain live machine CI inheritance");
      await detail.evaluate(element => {element.scrollTop = 0;});
      await page.screenshot({path: resolve(outputDir, "pr-review-agent-directions.png"), animations: "disabled"});
      await detail.getByLabel("b", {exact: true}).selectOption("inherit");
      await detail.getByRole("button", {name: "预览变更", exact: true}).click();
      await detail.getByText("锁定 revision 的变更预览", {exact: true}).waitFor();
      const saved = page.waitForResponse(response => response.url().endsWith("goal-configuration/apply"));
      const inheritedReadback = page.waitForResponse(response => new URL(response.url()).pathname === "/api/chat/goal-configuration" && response.request().method() === "GET");
      await detail.getByRole("button", {name: "应用此预览", exact: true}).click();
      assert.equal((await saved).status(), 200);
      await (await inheritedReadback).finished();
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      assert.deepEqual((await readback()).agent_orders, {a: "forward"});
      // An invalid Agent is a native preview error, recoverable in the same form.
      await detail.getByRole("button", {name: "编辑 JSON", exact: true}).click();
      const json = page.locator("#goal-configuration-json");
      await json.fill(JSON.stringify({review_order: "forward", wait_for_ci: false, agent_orders: {unknown: "reverse"}}));
      const rejected = page.waitForResponse(response => response.url().endsWith("goal-configuration/preview"));
      await detail.getByRole("button", {name: "预览变更", exact: true}).click();
      const rejection = await rejected;
      assert.equal(rejection.status(), 400);
      assert.equal((await rejection.json()).error_code, "invalid_goal_configuration");
      await detail.getByRole("alert").waitFor();
      assert.deepEqual((await readback()).agent_orders, {a: "forward"});
      await json.fill(JSON.stringify({review_order: "forward", wait_for_ci: false, agent_orders: {a: "forward", b: "reverse"}}));
      await detail.getByRole("button", {name: "返回表单", exact: true}).click();
      await detail.getByRole("button", {name: "预览变更", exact: true}).click();
      await detail.getByText("锁定 revision 的变更预览", {exact: true}).waitFor();
      await page.setViewportSize({width: 390, height: 844});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
      assert.deepEqual(context.errors.filter(error => error !== "Failed to load resource: the server responded with a status of 400 (Bad Request)"), []);
      assert.ok(context.errors.length <= 1, "Only the explicitly asserted invalid-Agent response may log an HTTP error");
      return {coverageEntries: context.coverageEntries, note: "Packaged Goal editor: seven Agents, real revisioned writer/readback, peer-preserving inherit, unknown-Agent error/recovery and narrow viewport; no live external calls."};
    } finally {
      clearTimeout(timer); lines.close();
      await context?.close();
      server.kill("SIGTERM");
      const kill = setTimeout(() => server.kill("SIGKILL"), 5000);
      try {await closed;} finally {clearTimeout(kill); await rm(root, {recursive: true, force: true});}
    }
  },
};
