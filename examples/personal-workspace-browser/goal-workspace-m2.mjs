import assert from "node:assert/strict";
import {resolve} from "node:path";
import {outputDir, repoRoot} from "./fixture.mjs";
import {readFile} from "node:fs/promises";
import {openWorkspacePage} from "./scenario-context.mjs";
import {installShowcaseWorkspace, openShowcaseGoal, showcaseGoalId} from "./showcase-workspace.mjs";

export const goalWorkspaceM2Scenario = {
  id: "goal-workspace-m2",
  async run({browser, collectCoverage, url}) {
    const writes = [];
    let scale = false, omitSelected = false;
    const many = Array.from({length: 200}, (_, i) => ({todo_id: `scale-${String(i).padStart(3,"0")}`, text: `Synthetic work item ${i + 1}`, title: `Synthetic work item ${i + 1}`,
      role: "agent", done: false, status: i === 0 ? "blocked" : "open", task_class: "advancement_task", claimed_by: i % 5 === 0 ? null : `worker-${i % 4}`}));
    const session = await openWorkspacePage(browser, url, {collectCoverage, viewport: {width: 1440, height: 1000}, beforeGoto: async (api,page) => {
      await installShowcaseWorkspace(api,page, {done: 23, cost: 24.8, deliveryFails: false});
      const transform = api.transformStatus;
      api.transformStatus = payload => {
        transform(payload);
        if (omitSelected) {
          const queue = payload.attention_queue.items.find(item => item.goal_id === showcaseGoalId);
          if (queue) {
            queue.agent_todos.items = queue.agent_todos.items.filter(todo => todo.todo_id !== "showcase-map-columns");
            queue.project_asset.agent_todos.items = queue.project_asset.agent_todos.items.filter(todo => todo.todo_id !== "showcase-map-columns");
          }
          for (const agent of payload.agent_management_projection.agents) if (agent.goal_ids?.includes(showcaseGoalId)) agent.current_todo = null;
        }
        if (!scale) return;
        const queue = payload.attention_queue.items.find(item => item.goal_id === showcaseGoalId);
        if (queue) {
          queue.agent_todos = {items: many, open_count: 200, done_count: 0, advancement_done_count: 0, total_count: 200};
          queue.project_asset.agent_todos = {items: many, open: 200, done: 0, advancement_done_count: 0, total: 200};
          queue.user_todos = {items: [], open_count: 0, total_count: 0};
        }
        payload.agent_management_projection.agents = payload.agent_management_projection.agents.filter(agent => !agent.goal_ids?.includes(showcaseGoalId));
      };
      await page.route("**/api/chat/delivery-review?*", async route => {
        if (!scale) {await route.fallback(); return;}
        await route.fulfill({json: {ok:true, goal_id:showcaseGoalId, observed_at:new Date().toISOString(), acceptance:null, graph:null,
          goal_map:{schema_version:"goal_task_map_v0",mode:"read_only",goal_id:showcaseGoalId,
            limits:{node_limit:200,emitted_node_count:200,omitted_node_count:0,source_truncated:false,missing_endpoint_count:0,cycle_edge_count:0,topology_complete:true},
            nodes:many.map((todo,i)=>({node_id:todo.todo_id,kind:"deliverable",title:todo.text,state:i===0?"blocked":"open",depth:i%2,refs:{todo_ids:[todo.todo_id]}})),
            edges:many.filter((_,i)=>i%2===1).map(todo=>({edge_id:`edge-${todo.todo_id}`,from_node_id:todo.todo_id,to_node_id:`scale-${String(Number(todo.todo_id.slice(6))-1).padStart(3,"0")}`,relation:"depends_on",enforcement:"typed_condition",reason:"Recorded synthetic completion condition"}))}}});
      });
      page.on("request", request => {if (request.method() !== "GET" && /actions|quota|validat/.test(request.url())) writes.push(request.url());});
    }});
    const {page} = session;
    try {
      await openShowcaseGoal(page);
      const now = page.getByRole("region", {name: "当前工作", exact: true});
      await now.waitFor();
      await page.locator('.goal-owner-group[data-owner-id="ledger-migrator"]').waitFor();
      assert.equal(await page.locator('.goal-owner-group [data-todo-id="showcase-retire-csv"]').count(),0);
      const row = now.locator('[data-todo-id="showcase-map-columns"]');
      await row.click();
      assert.equal(new URL(page.url()).searchParams.get("todo"), "showcase-map-columns");
      await page.locator('.personal-context-drawer').waitFor();
      await page.reload({waitUntil: "networkidle"});
      await page.locator('.personal-context-drawer').waitFor();
      assert.equal(new URL(page.url()).searchParams.get("goalId"),showcaseGoalId);
      assert.equal(await now.locator('[data-todo-id="showcase-map-columns"]').getAttribute('aria-pressed'),'true');
      // A selected item missing from the next bounded read can inspect its
      // retained history later, but must not keep the earlier mutation controls.
      omitSelected = true;
      await page.getByRole("button", {name:"刷新状态",exact:true}).click();
      await page.locator('.personal-context-drawer').getByText("当前任务状态不可用。请刷新或打开保留的历史记录，再决定后续操作。", {exact:true}).first().waitFor();
      await page.waitForFunction(() => !document.querySelector('.personal-context-drawer .personal-primary-action'));
      assert.equal(await page.locator('.personal-context-drawer').getByRole('button', {name:/标记完成/}).count(), 0);
      await page.locator('.personal-drawer-close').click();
      omitSelected = false;
      await page.getByRole("button", {name:"刷新状态",exact:true}).click();
      await now.locator('[data-todo-id="showcase-map-columns"]').waitFor();
      await now.getByRole('button',{name:'看板',exact:true}).click();
      await now.locator('.personal-task-board').waitFor();
      await now.getByRole('button',{name:'关系图',exact:true}).click();
      await now.locator('.work-map').waitFor();
      await now.getByRole('button',{name:'按负责人',exact:true}).click();
      await now.locator('[data-todo-id="showcase-reconcile"]').click();
      await page.waitForURL(value => value.searchParams.get("todo") === "showcase-reconcile");
      await page.locator('.personal-drawer-close').click();
      await page.waitForURL(value => !value.searchParams.has("todo"));
      await page.locator('.personal-context-drawer').waitFor({state:"hidden"});
      await page.goBack({waitUntil:"domcontentloaded"});
      // Back restores the task just closed, not a selection from an earlier view.
      await page.waitForURL(value => value.searchParams.get("todo") === "showcase-reconcile");
      await page.locator('.personal-context-drawer').waitFor();
      await now.locator('[data-todo-id="showcase-reconcile"][aria-pressed="true"]').waitFor();
      assert.equal(await now.locator('[data-todo-id="showcase-reconcile"]').getAttribute('aria-pressed'),'true');
      await page.goForward({waitUntil:"domcontentloaded"});
      await page.waitForURL(value => !value.searchParams.has("todo"));
      await page.locator('.personal-context-drawer').waitFor({state:"hidden"});
      assert.equal(writes.length,0,`Read-only navigation made an effect request: ${writes}`);
      await page.screenshot({path:resolve(outputDir,'goal-workspace-m2-desktop.png'),animations:'disabled'});
      await page.setViewportSize({width:390,height:844});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
      await page.screenshot({path:resolve(outputDir,'goal-workspace-m2-mobile.png'),animations:'disabled'});
      const direction = page.locator(".goal-direction-content");
      await direction.waitFor({state:"hidden"});
      await page.getByRole("button",{name:"展开目标与条件",exact:true}).click();
      await direction.waitFor({state:"visible"});
      await page.getByRole("button",{name:"收起目标与条件",exact:true}).click();
      const railTrigger = page.getByRole("button",{name:"打开全部工作",exact:true});
      await railTrigger.focus(); await page.keyboard.press("Enter");
      const railDialog = page.getByRole("dialog",{name:"全部工作",exact:true});
      await railDialog.waitFor();
      assert.equal(await railDialog.getByRole("complementary",{name:"全部工作",exact:true}).count(),1);
      await page.screenshot({path:resolve(outputDir,'goal-workspace-m2-mobile-rail.png'),animations:'disabled'});
      await page.keyboard.press("Escape"); await railDialog.waitFor({state:"hidden"});
      assert.equal(await railTrigger.evaluate(node=>node===document.activeElement),true,"Rail Escape restores its launcher focus");
      await railTrigger.click();
      await railDialog.locator('[data-todo-id="showcase-map-columns"]').click();
      await railDialog.waitFor({state:"hidden"});
      await page.locator('.personal-context-drawer').waitFor();
      assert.equal(new URL(page.url()).searchParams.get("todo"),"showcase-map-columns");
      await page.locator('.personal-drawer-close').click();
      await page.setViewportSize({width:1440,height:1000});
      scale = true;
      const statusResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/status.json");
      await page.getByRole("button", {name:"刷新状态",exact:true}).click();
      await statusResponse;
      const renderStart = performance.now();
      await now.locator('.goal-owner-group [data-todo-id="scale-199"]').waitFor();
      const initialMs = performance.now() - renderStart;
      assert.ok(initialMs < 2000, `200-item Now render exceeded frozen 2s budget: ${initialMs}ms`);
      assert.equal(await now.locator('.goal-owner-group [data-todo-id]').count(),200);
      await now.getByRole('button',{name:'关系图',exact:true}).click();
      await now.locator('.work-map-node').filter({hasText:'Synthetic work item 200'}).waitFor();
      await now.getByRole('button',{name:'按负责人',exact:true}).click();
      await now.locator('[data-todo-id="scale-199"][data-work-class="waiting_dependency"]').waitFor();
      assert.equal(await now.locator('.goal-owner-group [data-work-class="waiting_dependency"]').count(),100);
      assert.equal(await now.locator('.goal-owner-group [data-work-class="blocked"]').count(),1);
      assert.equal(await now.locator('.goal-owner-group [data-work-class="open"]').count(),99);
      const rail = page.getByRole('complementary',{name:'全部工作',exact:true});
      await rail.getByRole('button',{name:'全部 · 200',exact:true}).waitFor();
      assert.equal(await rail.getByRole('button',{name:'未完成 · 199',exact:true}).count(),1);
      assert.equal(await rail.getByRole('button',{name:'受阻与延后 · 1',exact:true}).count(),1);
      await now.getByRole('button',{name:'看板',exact:true}).click();
      assert.equal(await now.locator('.personal-task-card[data-work-class]').count(),200);
      assert.equal(await now.locator('.personal-task-card[data-work-class="waiting_dependency"]').count(),100);
      assert.equal(await now.locator('.personal-task-card[data-work-class="blocked"]').count(),1);
      assert.equal(await now.locator('.personal-task-card[data-work-class="open"]').count(),99);
      const switchMs = [];
      for (const name of ['按负责人','看板','关系图','按负责人','关系图']) {
        const start = performance.now();
        await now.getByRole('button',{name,exact:true}).click();
        await now.locator(name === '按负责人' ? '.goal-owner-group' : name === '看板' ? '.personal-task-board' : '.work-map').first().waitFor();
        switchMs.push(performance.now()-start);
      }
      assert.ok(switchMs.every(ms=>ms<1000), `Loaded lens switches exceeded frozen 1s budget: ${switchMs}`);
      assert.equal(writes.length,0,`Rendering created effects: ${writes}`);
      assert.equal(session.errors.length,0,session.errors.join('\n'));
      console.log(JSON.stringify({m2RenderBudget:{initialMs,switchMs,nodeCount:200}}));
      await verifyRemoteSelection(browser, url);
      return {coverageEntries:session.coverageEntries,note:'Responsibility groups, shared task selection/reload, Board/Map switching, no effect requests, desktop/mobile composition. Synthetic status/map; creation is separately qualified against real backend.'};
    } catch(error) {await page.screenshot({path:resolve(outputDir,'goal-workspace-m2-failed.png')});throw error;}
    finally {await session.close();}
  },
};


async function verifyRemoteSelection(browser, url) {
  const snapshot = JSON.parse(await readFile(resolve(repoRoot,"examples/status.example.json"),"utf8"));
  const goalId = snapshot.run_history.goals[0].id;
  const target = new URL(url);
  target.searchParams.set("statusUrl","http://127.0.0.1:8978/status.json");
  target.searchParams.set("goalId",goalId);
  target.searchParams.set("view","overview");
  target.searchParams.set("todo","missing-remote-task");
  const localOwnerReads = [];
  const session = await openWorkspacePage(browser,target.toString(),{beforeGoto: async (_api,page) => {
    await page.route("http://127.0.0.1:8978/status.json*", route => route.fulfill({json:snapshot}));
    page.on("request",request => {
      const parsed = new URL(request.url());
      if (parsed.port === new URL(url).port && /\/api\/chat\/(todo\/detail|completed-todos|delivery-review|goal-results|codex-attached)/.test(parsed.pathname)) localOwnerReads.push(parsed.pathname);
    });
  }});
  try {
    const {page} = session;
    await page.getByRole("region",{name:"当前工作",exact:true}).waitFor();
    assert.equal(await page.locator(".personal-context-drawer").count(),0,"An unknown remote Todo must not acquire a local request reader");
    await page.getByRole("status").filter({hasText:"链接中的任务未出现在此来源的当前快照中"}).waitFor();
    assert.equal(await page.getByRole("button",{name:"添加任务",exact:true}).count(),0);
    assert.equal(await page.locator('.goal-owner-conversation').count(),0,"A remote snapshot cannot open a local Agent conversation");
    await page.getByRole("region",{name:"当前工作",exact:true}).getByRole("button",{name:"关系图",exact:true}).click();
    assert.equal(await page.locator(".work-map").count(),0,"Remote selection cannot reuse a local map");
    target.searchParams.set("view", "conversation");
    target.searchParams.set("workAgent", "worker");
    await page.goto(target.href);
    await page.getByRole("status").filter({hasText:"此来源仅提供状态快照，无法在这里读取或回复原会话。"}).waitFor();
    assert.equal(await page.getByRole("region", {name:"Agent 工作会话",exact:true}).getByRole("textbox").count(),0);
    assert.deepEqual(localOwnerReads,[],"Remote deep link caused local owner reads");
  } finally {await session.close();}
}
