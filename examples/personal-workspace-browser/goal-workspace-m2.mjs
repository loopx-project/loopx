import assert from "node:assert/strict";
import {resolve} from "node:path";
import {outputDir} from "./fixture.mjs";
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
      // exact request, but must not keep the earlier mutation controls.
      omitSelected = true;
      await page.getByRole("button", {name:"刷新状态",exact:true}).click();
      await page.locator('.personal-context-drawer').getByText(/只读|完整要求/, {exact:false}).first().waitFor();
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
      await page.locator('.personal-drawer-close').click();
      await page.goBack({waitUntil:"domcontentloaded"});
      await page.locator('.personal-context-drawer').waitFor();
      assert.equal(new URL(page.url()).searchParams.get("todo"), "showcase-map-columns");
      await page.locator('.personal-drawer-close').click();
      assert.equal(writes.length,0,`Read-only navigation made an effect request: ${writes}`);
      await page.screenshot({path:resolve(outputDir,'goal-workspace-m2-desktop.png'),animations:'disabled'});
      await page.setViewportSize({width:390,height:844});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
      await page.screenshot({path:resolve(outputDir,'goal-workspace-m2-mobile.png'),animations:'disabled'});
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
      return {coverageEntries:session.coverageEntries,note:'Responsibility groups, shared task selection/reload, Board/Map switching, no effect requests, desktop/mobile composition. Synthetic status/map; creation is separately qualified against real backend.'};
    } catch(error) {await page.screenshot({path:resolve(outputDir,'goal-workspace-m2-failed.png')});throw error;}
    finally {await session.close();}
  },
};
