import assert from "node:assert/strict";
import {resolve} from "node:path";
import {outputDir, repoRoot} from "./fixture.mjs";
import {readFile} from "node:fs/promises";
import {openWorkspacePage} from "./scenario-context.mjs";
import {deliveryReadFixture, installShowcaseWorkspace, openShowcaseGoal, showcaseGoalId} from "./showcase-workspace.mjs";

export const goalWorkspaceM2Scenario = {
  id: "goal-workspace-m2",
  async run({browser, collectCoverage, url}) {
    const writes = [];
    let scale = false, omitSelected = false;
    const many = Array.from({length: 200}, (_, i) => ({todo_id: `scale-${String(i).padStart(3,"0")}`, text: `Synthetic work item ${i + 1}`, title: `Synthetic work item ${i + 1}`,
      role: "agent", done: false, status: i === 0 ? "blocked" : "open", task_class: "advancement_task", claimed_by: i % 5 === 0 ? null : `worker-${i % 4}`}));
    for (const [i, todo] of many.entries()) {todo.text += i % 2
      ? ": Reconcile the complete source records, preserve unresolved differences, and verify the result against the original acceptance criteria before returning evidence to the responsible owner."
      : "：逐项核对原始记录与完整验收要求，保留未解决的差异，检查来源、执行结果与实际采用情况，再向负责人返回可复核的证据。"; todo.title = todo.text;}
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
        await route.fulfill({json: deliveryReadFixture({ok:true, goal_id:showcaseGoalId, observed_at:new Date().toISOString(), acceptance:null, graph:null,
          goal_map:{schema_version:"goal_task_map_v0",mode:"read_only",goal_id:showcaseGoalId,
            limits:{node_limit:200,emitted_node_count:200,omitted_node_count:0,source_truncated:false,missing_endpoint_count:0,cycle_edge_count:0,topology_complete:true},
            nodes:many.map((todo,i)=>({node_id:todo.todo_id,kind:"deliverable",title:todo.text,state:i===0?"blocked":"open",depth:i%2,refs:{todo_ids:[todo.todo_id]}})),
            edges:many.filter((_,i)=>i%2===1).map(todo=>({edge_id:`edge-${todo.todo_id}`,from_node_id:todo.todo_id,to_node_id:`scale-${String(Number(todo.todo_id.slice(6))-1).padStart(3,"0")}`,relation:"depends_on",enforcement:"typed_condition",reason:"Recorded synthetic completion condition"}))}})});
      });
      page.on("request", request => {if (request.method() !== "GET" && /actions|quota|validat/.test(request.url())) writes.push(request.url());});
    }});
    const {page} = session;
    try {
      await openShowcaseGoal(page);
      const now = page.getByRole("region", {name: "当前工作", exact: true});
      await now.waitFor();
      await page.locator('.goal-owner-group[data-owner-id="ledger-migrator"]').waitFor();
      assert.equal(await page.getByRole("complementary", {name:"全部工作",exact:true}).isVisible(),false,"Overview must not duplicate its work in a permanent rail");
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
      await page.screenshot({path:resolve(outputDir,'task-history-restored-desktop.png'),animations:'disabled'});
      await page.goForward({waitUntil:"domcontentloaded"});
      await page.waitForURL(value => !value.searchParams.has("todo"));
      await page.locator('.personal-context-drawer').waitFor({state:"hidden"});
      assert.equal(writes.length,0,`Read-only navigation made an effect request: ${writes}`);
      await now.locator('[data-todo-id="showcase-backfill"][data-work-class="waiting_dependency"]').waitFor();
      await page.screenshot({path:resolve(outputDir,'goal-workspace-m2-desktop.png'),animations:'disabled'});
      await page.setViewportSize({width:390,height:844});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
      await page.screenshot({path:resolve(outputDir,'goal-workspace-m2-mobile.png'),animations:'disabled'});
      // An enabled contract uses the native acceptance disclosure; the
      // fallback direction button only exists without a contract or plan.
      const evidence = page.locator(".goal-overview-evidence");
      if (await evidence.getAttribute("open") === null) {
        await evidence.locator(":scope > summary").focus();
        await page.keyboard.press("Enter");
      }
      const inspection = page.locator(".goal-overview .delivery-check-inspection");
      const criteria = inspection.locator(".goal-overview-criteria");
      await page.locator(".goal-overview .delivery-acceptance-contract .goal-overview-objective").waitFor({state:"visible"});
      await criteria.waitFor({state:"hidden"});
      const summary = inspection.locator(":scope > summary");
      await summary.focus(); await page.keyboard.press("Enter");
      await criteria.waitFor({state:"visible"});
      assert.equal(await inspection.evaluate(node => node.open), true);
      await page.keyboard.press("Enter");
      await criteria.waitFor({state:"hidden"});
      assert.equal(await inspection.evaluate(node => node.open), false);
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
      await page.screenshot({path:resolve(outputDir,'task-history-restored-narrow.png'),animations:'disabled'});
      await page.locator('.personal-drawer-close').click();
      await page.setViewportSize({width:1440,height:1000});
      scale = true;
      const statusResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/status.json");
      await page.getByRole("button", {name:"刷新状态",exact:true}).click();
      await statusResponse;
      const renderStart = performance.now();
      await now.locator('.goal-owner-group [data-todo-id="scale-199"]').waitFor({state:"attached"});
      const initialMs = performance.now() - renderStart;
      assert.ok(initialMs < 2000, `200-item Now render exceeded frozen 2s budget: ${initialMs}ms`);
      assert.equal(await now.locator('.goal-owner-group [data-todo-id]').count(),200);
      assert.equal(await now.locator('.goal-owner-group [data-todo-id]:visible').count(),15,"Five owners show three tasks each before expansion");
      await now.locator('.goal-owner-group[data-owner-id=""] > header').getByText(/1 受阻/).waitFor();
      await page.screenshot({path:resolve(outputDir,'overview-populated-desktop.png'),animations:'disabled',fullPage:true});
      await page.setViewportSize({width:390,height:844});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
      await page.screenshot({path:resolve(outputDir,'overview-populated-mobile.png'),animations:'disabled'});
      await page.setViewportSize({width:1440,height:1000});
      const expandStart = performance.now();
      for (const summary of await now.locator('.goal-work-more > summary').all()) {await summary.focus();await page.keyboard.press('Enter');}
      assert.equal(await now.locator('.goal-owner-group [data-todo-id]:visible').count(),200,"All work remains reachable in the original owner order");
      const expandMs = performance.now() - expandStart;
      assert.ok(expandMs < 2000, `Full 200-item expansion exceeded 2s budget: ${expandMs}ms`);
      await now.getByRole('button',{name:'关系图',exact:true}).click();
      await now.locator('.work-map-node').filter({hasText:'Synthetic work item 200'}).waitFor();
      await now.getByRole('button',{name:'按负责人',exact:true}).click();
      await now.locator('[data-todo-id="scale-199"][data-work-class="waiting_dependency"]').waitFor();
      assert.equal(await now.locator('.goal-owner-group [data-work-class="waiting_dependency"]').count(),100);
      assert.equal(await now.locator('.goal-owner-group [data-work-class="blocked"]').count(),1);
      assert.equal(await now.locator('.goal-owner-group [data-work-class="open"]').count(),99);
      const rail = page.getByRole('complementary',{name:'全部工作',exact:true});
      await railTrigger.click();
      await railDialog.waitFor();
      await rail.getByRole('button',{name:'全部 · 200',exact:true}).waitFor();
      assert.equal(await rail.getByRole('button',{name:'未完成 · 199',exact:true}).count(),1);
      assert.equal(await rail.getByRole('button',{name:'受阻与延后 · 1',exact:true}).count(),1);
      await page.keyboard.press('Escape');await railDialog.waitFor({state:'hidden'});
      assert.equal(await railTrigger.evaluate(node=>node===document.activeElement),true,"Desktop rail Escape restores focus too");
      const remaining = now.locator('.goal-owner-group[data-owner-id="worker-3"] .goal-work-more');
      await remaining.locator(':scope > summary').click();
      await railTrigger.click();
      await rail.locator('[data-todo-id="scale-199"]').click();
      await railDialog.waitFor({state:'hidden'});
      await now.locator('[data-todo-id="scale-199"][aria-pressed="true"]').waitFor();
      assert.equal(await remaining.evaluate(node=>node.open),true,"Selecting later work reveals its owner row");
      await remaining.locator(':scope > summary').click();
      await railTrigger.click();
      await rail.locator('[data-todo-id="scale-191"]').click();
      await now.locator('[data-todo-id="scale-191"][aria-pressed="true"]').waitFor();
      assert.equal(await remaining.evaluate(node=>node.open),true,"A different selection in the same closed group also reveals its row");
      await page.locator('.personal-drawer-close').click();
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
      console.log(JSON.stringify({m2RenderBudget:{initialMs,expandMs,switchMs,nodeCount:200}}));
      await verifyRemoteSelection(browser, url);
      return {coverageEntries:session.coverageEntries,note:'Responsibility groups, shared task selection/reload, Board/Map switching, no effect requests, desktop/mobile composition. Synthetic status/map; creation is separately qualified against real backend.'};
    } catch(error) {await page.screenshot({path:resolve(outputDir,'goal-workspace-m2-failed.png')});throw error;}
    finally {await session.close();}
  },
};


async function verifyRemoteSelection(browser, url) {
  const snapshot = JSON.parse(await readFile(resolve(repoRoot,"examples/status.example.json"),"utf8"));
  const goalId = snapshot.run_history.goals[0].id;
  snapshot.run_history.goals[0].acceptance_observation = {
    schema_version:"goal_acceptance_observation_projection_v0",goal_id:goalId,read_only:true,acceptance_assessed:false,
    coverage:"partial",missing_sources:["current_run"],truncated:true,historical_progress:[],
    acceptance_gaps:[{kind:"outcome_gap",owner:"reviewer",reason:"Archived totals have not been independently reconciled.",
      evidence_required:"Compare each archived total with the source ledger and retain unresolved differences.",observed_at:null,source:"agent_vision"}],
    guards:[{kind:"owner_decision",todo_id:null,blocks_agent:null,owner:"reviewer",reason:"Cut-over remains held pending the owner's decision.",evidence_required:"Recorded owner decision",decision_scope:"cut-over"}],
    next_action:"Reconcile the archived totals before requesting cut-over.",next_action_source:"agent_vision",
  };
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
    const observation = page.locator('.goal-observation-details');
    await observation.locator(':scope > summary').getByText('1 项缺口 · 1 项限制 · 仅部分观测',{exact:true}).waitFor();
    assert.equal(await observation.evaluate(node=>node.open),false);
    await page.screenshot({path:resolve(outputDir,'overview-readonly-desktop.png'),animations:'disabled'});
    await observation.locator(':scope > summary').focus();await page.keyboard.press('Enter');
    await observation.getByText('Archived totals have not been independently reconciled.',{exact:true}).waitFor();
    await observation.getByText("Cut-over remains held pending the owner's decision.",{exact:true}).waitFor();
    await page.screenshot({path:resolve(outputDir,'overview-readonly-expanded.png'),animations:'disabled'});
    await verifyGoalDetails(page, snapshot);
    snapshot.run_history.goals[0].acceptance_observation.goal_id = 'another-goal';
    await page.getByRole('button',{name:'刷新状态',exact:true}).click();
    await observation.locator(':scope > summary').getByText('验收观测不可用，Goal 是否达成仍未知。',{exact:true}).waitFor();
    assert.equal(await observation.getByText('Archived totals have not been independently reconciled.',{exact:true}).count(),0,"Wrong-Goal observations never survive a refresh");
    await page.getByRole('button',{name:'Goal 信息',exact:true}).click();
    const unavailableDrawer = page.locator('.personal-context-drawer');
    await unavailableDrawer.getByText('验收观测不可用，Goal 是否达成仍未知。',{exact:true}).waitFor();
    assert.equal(await unavailableDrawer.getByText('Archived totals have not been independently reconciled.',{exact:true}).count(),0);
    await page.keyboard.press('Escape');
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

// The whole Goal reader, including long original evidence, unknown usage and
// a real owner gate. Styling must not alter any source fact or enable an effect.
async function verifyGoalDetails(page, snapshot) {
  const source = snapshot.run_history.goals[0];
  const original = structuredClone(source.acceptance_observation);
  const title = 'Ledger exports · 核对完整历史并保留各地区尚未解决的差异与原始证据';
  source.display_name = title;
  const owner = 'ledger-reconciliation-source-owner-with-a-long-native-identity';
  const reason = 'Some regional totals remain unresolved. 保留负面结果、未知成本和原始记录；当前数据不能证明完整验收或独立收益。';
  const required = 'Compare every original ledger entry and archive the differences with the exact input version. 不删除失败或未知，不把局部通过当成全部完成。';
  const next = 'Continue the read-only reconciliation. Request the owner’s decision before any cut-over; do not execute the example command.';
  const observed = '2026-08-01T09:15:00+00:00';
  source.acceptance_observation = {...original, next_action: next, acceptance_gaps: [
    {...original.acceptance_gaps[0], reason, evidence_required: required, owner, observed_at: observed,
      component_checks: {checkpoint_satisfied:true,checkpoint_fresh:false,path_outcome_valid:true,evidence_refs_present:true,final_outcome_claim_present:false,no_reported_outcome_gap:false}},
    {...original.acceptance_gaps[0], reason:'The retained candidate still lacks an independent comparison. 仍未完成独立对照。', evidence_required:'Keep the failed comparison and the complete original evidence.', owner:'second-reader'},
  ]};
  snapshot.usage_summary.goals = [];
  await page.getByRole('button',{name:'刷新状态',exact:true}).click();
  const info = page.getByRole('button',{name:'Goal 信息',exact:true});
  await info.click();
  const drawer = page.locator('.personal-context-drawer[data-context-kind="goal"]');
  const acceptance = drawer.locator('.personal-goal-acceptance');
  const usage = drawer.locator('.personal-goal-detail-usage');
  await acceptance.getByText(reason,{exact:true}).waitFor();
  await acceptance.getByRole('status').getByText('待核验 · 来源不全',{exact:true}).waitFor();
  assert.equal(await acceptance.getByText('仅展示前 12 条观测。',{exact:true}).isVisible(),false,'Display-limit explanation is optional help, not a second status paragraph');
  assert.equal(await drawer.getByText(title,{exact:true}).count(),1,'The Goal title is not repeated in its header and body');
  assert.equal(await usage.getByText('未采集',{exact:true}).count(),1,'An entirely unknown usage section has one status, not six identical missing values');
  await acceptance.getByText(next,{exact:true}).waitFor();
  assert.ok(await acceptance.evaluate(node => node.querySelector('.personal-acceptance-next').getBoundingClientRect().top < node.querySelector('.personal-acceptance-observation').getBoundingClientRect().top),'Next action precedes the long evidence collection');
  assert.equal(await acceptance.getByText(owner,{exact:true}).isVisible(),false);
  await acceptance.getByText('3 项检查未通过',{exact:true}).waitFor();
  await acceptance.getByText("Cut-over remains held pending the owner's decision.",{exact:true}).waitFor();
  const readCheck = async () => drawer.evaluate(node => {
    const text = node.querySelector('.personal-acceptance-observation > p');
    const styles = getComputedStyle(text);
    return {font:Number.parseFloat(styles.fontSize),line:Number.parseFloat(styles.lineHeight),overflow:node.scrollWidth-node.clientWidth};
  });
  let reading = await readCheck();
  assert.ok(reading.font >= 16 && reading.line >= 24,`Evidence is reading text, not caption metadata: ${JSON.stringify(reading)}`);
  const help = acceptance.locator('.personal-acceptance-coverage .context-help-trigger');
  await help.focus();
  await page.getByRole('tooltip').getByText('当前仅有部分观测。任务完成或缺口列表为空，都不能证明 Goal 已通过验收。',{exact:true}).waitFor();
  await page.getByRole('tooltip').getByText('仅展示前 12 条观测。',{exact:true}).waitFor();
  await page.getByRole('tooltip').getByText('未获取的来源： 最新执行证据',{exact:true}).waitFor();
  await page.screenshot({path:resolve(outputDir,'goal-detail-help.png'),animations:'disabled'});
  await page.keyboard.press('Escape');
  assert.equal(await drawer.isVisible(),true,'Closing optional help keeps the Goal reader open');
  await page.screenshot({path:resolve(outputDir,'goal-detail-desktop.png'),animations:'disabled'});
  const details = acceptance.locator('.personal-acceptance-observation details').first();
  await details.locator('summary').focus(); await page.keyboard.press('Enter');
  await details.getByText(owner,{exact:true}).waitFor();
  await details.getByText(observed,{exact:true}).waitFor();
  await details.getByText('最终成果声明齐全: 未通过',{exact:true}).waitFor();
  assert.equal(await acceptance.getByText(required,{exact:true}).innerText(),required,'Complete original evidence stays readable');
  await page.screenshot({path:resolve(outputDir,'goal-detail-expanded-desktop.png'),animations:'disabled'});
  await page.keyboard.press('Escape');
  await drawer.waitFor({state:'detached'});
  assert.ok(await info.evaluate(node=>node===document.activeElement),'Closing the Goal reader returns to its exact trigger');

  snapshot.usage_summary.goals = [{goal_id:source.id,cost_usd_24h:0,duration_ms_7d:7_200_000,input_tokens_7d:12500}];
  await page.getByRole('button',{name:'刷新状态',exact:true}).click(); await info.click();
  await usage.locator('table').waitFor();
  const rows = usage.locator('tbody tr');
  assert.match(await rows.nth(1).innerText(),/\$0\.00/,'A measured zero is not missing');
  assert.match(await rows.nth(1).innerText(),/未采集/,'The other window remains unknown');
  assert.match(await rows.nth(2).innerText(),/2\.0h/);
  assert.equal(await usage.locator('thead th[scope="col"]').count(),2,'Periods own columns instead of wrapping inside each metric label');
  await usage.scrollIntoViewIfNeeded();
  await page.screenshot({path:resolve(outputDir,'goal-detail-usage-desktop.png'),animations:'disabled'});
  await page.setViewportSize({width:390,height:844});
  await drawer.locator('.personal-drawer-body').evaluate(node=>node.scrollTop=0);
  await page.screenshot({path:resolve(outputDir,'goal-detail-phone.png'),animations:'disabled'});
  const narrowDetails = acceptance.locator('.personal-acceptance-observation details').first();
  await narrowDetails.locator('summary').click();
  await narrowDetails.getByText(owner,{exact:true}).waitFor();
  reading = await readCheck(); assert.ok(reading.overflow <= 1,`Long original text and identifiers fit the phone reader: ${JSON.stringify(reading)}`);
  await page.screenshot({path:resolve(outputDir,'goal-detail-expanded-phone.png'),animations:'disabled'});
  await page.evaluate(()=>document.documentElement.style.fontSize='200%');
  reading = await readCheck(); assert.ok(reading.font >= 32 && reading.overflow <= 1,'Text preferences enlarge the complete reader without horizontal clipping');
  await page.screenshot({path:resolve(outputDir,'goal-detail-large-text.png'),animations:'disabled'});
  await page.evaluate(()=>document.documentElement.style.fontSize='');
  await page.keyboard.press('Escape');
  await page.evaluate(()=>localStorage.setItem('loopx-pw-locale','en'));
  await page.reload({waitUntil:'networkidle'});
  await page.getByRole('button',{name:'Goal information',exact:true}).click();
  await drawer.getByRole('heading',{name:'Usage',exact:true}).waitFor();
  await acceptance.getByText('3 checks failed',{exact:true}).waitFor();
  await drawer.locator('.personal-drawer-body').evaluate(node=>node.scrollTop=0);
  await page.screenshot({path:resolve(outputDir,'goal-detail-en-phone.png'),animations:'disabled'});
  await page.keyboard.press('Escape');
  await page.evaluate(()=>localStorage.setItem('loopx-pw-locale','zh-CN'));
  source.acceptance_observation.missing_sources = [];
  await page.reload({waitUntil:'networkidle'});
  await page.getByRole('button',{name:'Goal 信息',exact:true}).click();
  await acceptance.getByRole('status').getByText('待核验 · 部分记录',{exact:true}).waitFor();
  assert.equal(await acceptance.getByText('来源不全',{exact:true}).count(),0,'A display limit does not imply an unavailable source');
  await page.keyboard.press('Escape');
  source.acceptance_observation.truncated = false;
  await page.reload({waitUntil:'networkidle'});
  await page.getByRole('button',{name:'Goal 信息',exact:true}).click();
  await acceptance.getByRole('status').getByText('待核验',{exact:true}).waitFor();
  await page.keyboard.press('Escape');
  source.acceptance_observation = original;
  await page.setViewportSize({width:1512,height:982});
  await page.reload({waitUntil:'networkidle'});
}
