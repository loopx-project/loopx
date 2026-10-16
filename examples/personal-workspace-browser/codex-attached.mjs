import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {resolve} from 'node:path';
import {createInterface} from 'node:readline';
import {repoRoot, outputDir} from './fixture.mjs';
import {openWorkspacePage} from './scenario-context.mjs';
import {testPythonCommand} from '../../scripts/test-python.mjs';

export async function runCodexAttached(browser) {
  if (process.platform === "win32") return; // Windows has no qualified Desktop follower transport.
  await runConversation(browser, false);
  await runConversation(browser, true);
}

async function runConversation(browser, uncertain) {
  const fixturePath = resolve(repoRoot, 'examples/personal-workspace-browser/codex_attached_fixture.py');
  const python = testPythonCommand(`import runpy, sys\nsys.argv = [${JSON.stringify(fixturePath)}, ${JSON.stringify(uncertain ? '--uncertain' : '--confirmed')}]\nrunpy.run_path(${JSON.stringify(fixturePath)}, run_name="__main__")`, {repoRoot});
  const child = spawn(python.command, python.args, {cwd: repoRoot, env: process.env, stdio: ['ignore','pipe','pipe']});
  let stderr = '';
  child.stderr.on('data', value => {stderr += value;});
  const lines = createInterface({input: child.stdout});
  let context, timer;
  try {
    const [line] = await Promise.race([once(lines,'line'), once(child,'exit').then(() => {throw new Error(stderr);}), new Promise((_, reject) => {timer = setTimeout(() => reject(new Error('Native fixture did not start')), 30_000);})]);
    clearTimeout(timer);
    const {url} = JSON.parse(line);
    context = await openWorkspacePage(browser, url, {gotoWaitUntil: 'domcontentloaded', apiOptions: {fixturePort: new URL(url).port}, beforeGoto: async (api, page) => {
      api.transformStatus = fixture => {
        const directoryGoal = fixture.run_history.goals.find(goal => goal.id === 'product-release');
        directoryGoal.coordination = {registered_agents:['worker','other-worker','remote-worker'], thread_agent_bindings:[
          {agent_id:'worker',host_surface:'codex-app'}, {agent_id:'remote-worker',host_surface:'cursor'},
        ]};
        let goal = fixture.attention_queue.items.find(item => item.goal_id === 'product-release');
        if (!goal) {
          goal = {goal_id:'product-release',status:'active',waiting_on:'worker',severity:'info',recommended_action:'Review worker result',
            project_asset:{owner:'worker',gate:'none',next_action:'Review worker result',stop_condition:'Results reviewed'}};
          fixture.attention_queue.items.push(goal);
        }
        const todos = ['worker', 'other-worker'].map((owner, index) => ({index, done:false, status:'open', role:'agent', task_class:'advancement_task', todo_id:`owner-task-${index}`, text:`Review ${owner} result`, claimed_by:owner}));
        goal.agent_todos = {items:todos, open_count:2, total_count:2, done_count:0};
        goal.project_asset = {...goal.project_asset, agent_todos:{items:todos, open:2, done:0, total:2}};
      };
      // Unrelated Goal/status APIs use the public synthetic workspace. The
      // original-conversation route always reaches the real production owner.
      await page.route('**/api/chat/codex-attached**', route => route.continue());
    }});
    const {page} = context;
    await page.locator('.personal-goal-link').filter({hasText:'Product Release'}).click();
    if (!uncertain) {
      await page.getByRole('navigation',{name:'Goal 视图'}).getByRole('button',{name:'概览',exact:true}).click();
      // Start with the owner of the work in view. Never choose another bound
      // Agent merely because the requested owner has no native conversation.
      const other = page.getByRole('button',{name:'与 other-worker 对话',exact:true});
      await other.click();
      const unavailable = page.getByRole('region',{name:'Agent 工作会话',exact:true});
      await unavailable.getByText('此 Agent 尚无已绑定的会话来源。',{exact:true}).waitFor();
      assert.equal(await unavailable.getByText('Original result',{exact:true}).count(),0);
      assert.equal(await unavailable.getByRole('textbox').count(),0);
      await page.goBack();
      await other.waitFor();
      assert.ok(await other.evaluate(node => node === document.activeElement));
      const entry = page.getByRole('button',{name:'与 worker 对话',exact:true});
      const owner = page.getByRole('region',{name:'Agent 工作会话',exact:true});
      // Recovery begins before a binding exists. Transient discovery failures
      // must not require another user action; a permission denial must not spin.
      for (const fault of ['network', 503, 403]) {
        let failing = true, discoveryReads = 0;
        const discoveryFailure = async route => {
          const request = route.request();
          const endpoint = new URL(request.url());
          if (request.method() !== 'GET' || endpoint.searchParams.has('agent_id')) return route.continue();
          discoveryReads++;
          if (!failing) return route.continue();
          if (fault === 'network') return route.abort('failed');
          return route.fulfill({status:fault,contentType:'application/json',body:JSON.stringify({ok:false,error:fault === 403 ? 'Owner access is required' : 'Temporary discovery outage'})});
        };
        await page.route('**/api/chat/codex-attached?*',discoveryFailure);
        await entry.click();
        await owner.getByRole('alert').waitFor();
        assert.equal(await owner.getByRole('textbox').count(),0);
        failing = false;
        if (fault === 403) {
          const deniedReads = discoveryReads;
          await page.waitForTimeout(1300);
          assert.equal(discoveryReads,deniedReads,'Permission denial does not retry automatically');
          await owner.getByRole('button',{name:'重新连接',exact:true}).click();
        }
        await owner.getByText('Original result',{exact:true}).waitFor();
        assert.ok(discoveryReads >= 2,'Recovery observes the original binding again');
        assert.equal(new URL(page.url()).searchParams.get('workAgent'),'worker');
        assert.equal((await (await page.request.get(new URL('/fixture/inputs',url).href)).json()).count,0);
        await page.unroute('**/api/chat/codex-attached?*',discoveryFailure);
        await owner.getByRole('button',{name:'返回概览',exact:true}).click();
      }
      await entry.click();
      await owner.getByText('Original result',{exact:true}).waitFor();
      assert.equal(await owner.getByRole('combobox',{name:'查看 Agent'}).inputValue(),'worker', 'The owner was already selected by the entry');
      assert.equal(new URL(page.url()).searchParams.get('workAgent'),'worker');
      await page.reload();
      await owner.getByText('Original result',{exact:true}).waitFor();
      assert.equal((await (await page.request.get(new URL('/fixture/inputs',url).href)).json()).count,0);
      await owner.getByRole('textbox',{name:'回复 worker',exact:true}).fill('保留在 worker 的草稿');
      await page.reload();
      await owner.getByText('Original result',{exact:true}).waitFor();
      assert.equal(await owner.getByRole('textbox',{name:'回复 worker',exact:true}).inputValue(),'保留在 worker 的草稿');
      assert.equal(await page.getByRole('textbox',{name:'向 LoopX 发送消息',exact:true}).count(),0);
      assert.equal(await page.getByRole('combobox',{name:'选择 Chat Runtime',exact:true}).count(),0);
      await page.locator('.personal-goal-link').filter({hasText:'Research Monitor'}).click();
      assert.equal(new URL(page.url()).searchParams.has('workAgent'),false);
      assert.equal(await owner.count(),0);
      await page.goBack();
      await owner.getByText('Original result',{exact:true}).waitFor();
      assert.equal(await owner.getByRole('textbox',{name:'回复 worker',exact:true}).inputValue(),'保留在 worker 的草稿');
      await owner.getByRole('textbox',{name:'回复 worker',exact:true}).fill('');
      await owner.getByRole('combobox',{name:'查看 Agent'}).selectOption('remote-worker');
      await owner.getByText('来源：cursor。此宿主的工作记录与回复尚未接入。',{exact:true}).waitFor();
      assert.equal(await owner.getByRole('textbox').count(),0);
      await page.screenshot({path:resolve(outputDir,'attached-agent-unsupported.png'),animations:'disabled'});
      await page.goBack();
      await owner.getByText('Original result',{exact:true}).waitFor();
      await page.goForward();
      await owner.getByText('来源：cursor。此宿主的工作记录与回复尚未接入。',{exact:true}).waitFor();
      await owner.getByRole('button',{name:'返回概览',exact:true}).click();
      await entry.waitFor();
      const unknown = new URL(page.url()); unknown.searchParams.set('view','conversation'); unknown.searchParams.set('workAgent','missing-agent');
      await page.goto(unknown.href);
      await owner.getByText('此 Agent 不在当前 Goal 的已注册列表中，请选择列表中的 Agent。',{exact:true}).waitFor();
      assert.equal(await owner.getByRole('textbox').count(),0);
      await owner.getByRole('button',{name:'返回概览',exact:true}).click();
    }
    await page.getByRole('navigation',{name:'Goal 视图'}).getByRole('button',{name:'对话',exact:true}).click();
    const panel = page.getByRole('region', {name:'Agent 工作会话',exact:true});
    await page.screenshot({path:resolve(outputDir,'attached-codex-entry-desktop.png'),animations:'disabled'});
    await page.getByRole('button',{name:'Agent 工作记录',exact:true}).click();
    assert.equal(await panel.locator('.agent-work-directory button').count(),3);
    await panel.getByRole('button',{name:'worker',exact:true}).click();
    await panel.getByText('Original result',{exact:true}).waitFor();
    await panel.getByText('原会话可回复',{exact:true}).waitFor();
    const inputCount = async () => (await (await page.request.get(new URL('/fixture/inputs',url).href)).json()).count;
    const composer = panel.getByRole('textbox', {name:'回复 worker',exact:true});
    const send = panel.getByRole('button', {name:'发送到原会话',exact:true});
    const sentOperations = [];
    page.on('request', request => {
      if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/chat/codex-attached') sentOperations.push(request.postDataJSON().operation_id);
    });
    if (uncertain) {
      const unrelatedRejection = async route => {
        const response = await route.fetch();
        assert.equal((await response.json()).status,'pending');
        // Even an explicit non-submission proof cannot clear another operation.
        await route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({ok:false,error:'Unrelated operation rejected',input_not_submitted:true,operation_id:'different-operation'})});
      };
      await page.route('**/api/chat/codex-attached',unrelatedRejection);
      await composer.fill('核对一次投递，不要自动重发。');
      const submitted = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/chat/codex-attached');
      await send.click();
      await submitted;
      await page.unroute('**/api/chat/codex-attached',unrelatedRejection);
      await panel.getByText('投递尚未确认，请先在原 App 核对，不会自动重发。',{exact:false}).waitFor();
      assert.equal(await inputCount(),1);
      await page.reload();
      await panel.getByRole('button',{name:'重读投递回执',exact:true}).click();
      assert.equal(await composer.count(),0, 'Unconfirmed input must not expose another send');
      assert.equal(await inputCount(),1, 'Reload and receipt reads must not repeat the effect');
      assert.equal(sentOperations.length,1);
      await panel.getByRole('button',{name:'已核对，写下一条',exact:true}).waitFor();
      await page.screenshot({path:resolve(outputDir,'attached-codex-pending-desktop.png'),animations:'disabled'});
      let releaseReceipt;
      const holdReceipt = new Promise(resolve => {releaseReceipt = resolve;});
      let receiptStarted;
      const readingReceipt = new Promise(resolve => {receiptStarted = resolve;});
      await page.route('**/api/chat/codex-attached?*operation_id=*', async route => {
        const response = await route.fetch();
        receiptStarted();
        await holdReceipt;
        await route.fulfill({response});
      });
      await panel.getByRole('button',{name:'重读投递回执',exact:true}).click();
      await readingReceipt;
      await panel.getByRole('button',{name:'已核对，写下一条',exact:true}).click();
      await composer.fill('这是核对后新写的草稿。');
      const lateReceipt = page.waitForResponse(response => new URL(response.url()).searchParams.has('operation_id'));
      releaseReceipt();
      await lateReceipt;
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      assert.equal(await composer.inputValue({timeout:2000}),'这是核对后新写的草稿。', 'Late receipt reads must not revive a discarded operation');
      assert.equal(await inputCount(),1);
      return;
    }
    assert.equal((await (await page.request.get(new URL('/fixture/opened',url).href)).json()).count,0);
    const openOriginal = panel.getByRole('button',{name:'在 Codex App 中打开',exact:true});
    await page.request.post(new URL('/fixture/fail-open',url).href);
    await openOriginal.focus();
    await page.keyboard.press('Enter');
    await panel.getByRole('alert').filter({hasText:'Codex App could not be opened'}).waitFor();
    await page.screenshot({path:resolve(outputDir,'attached-codex-open-error-desktop.png'),animations:'disabled'});
    assert.equal((await (await page.request.get(new URL('/fixture/opened',url).href)).json()).count,0);
    await openOriginal.click();
    await panel.getByText('已请求打开原会话，请在 Codex App 中查看。',{exact:true}).waitFor();
    assert.equal((await (await page.request.get(new URL('/fixture/opened',url).href)).json()).count,1);
    assert.equal(await panel.getByRole('alert').count(),0);
    assert.equal(await panel.locator('a[href^="codex:"]').count(),0);
    assert.equal(await panel.evaluate(node => node.contains(document.activeElement)), true);
    await panel.getByRole('button',{name:'返回概览',exact:true}).click();
    assert.equal(await panel.isVisible(), false);
    await page.goBack();
    await panel.getByText('Original result',{exact:true}).waitFor();
    await page.request.post(new URL('/fixture/append',url).href);
    await panel.getByText('原 Agent 的新结果：已保留原会话。',{exact:true}).waitFor();
    await panel.getByLabel('回复 worker',{exact:true}).fill('请沿用当前原会话补充验证。');
    await panel.getByRole('button',{name:'发送到原会话',exact:true}).click();
    await panel.getByText('已由原 App 接纳；等待 Agent 结果。',{exact:false}).waitFor();
    await panel.locator('.attached-conversation-messages').getByText('请沿用当前原会话补充验证。',{exact:true}).waitFor();
    assert.equal(await composer.count(),1, 'A confirmed reply must leave the next composer available');
    assert.equal(await composer.inputValue(),'');
    assert.equal(await panel.getByRole('button',{name:'已核对，写下一条',exact:true}).count(),0);
    await page.reload();
    await panel.getByText('已由原 App 接纳；等待 Agent 结果。',{exact:false}).waitFor();
    assert.equal(await composer.inputValue(),'', 'Reload must not turn a confirmed message back into a draft');
    assert.equal(await inputCount(),1, 'Reload must not submit anything');
    await page.screenshot({path:resolve(outputDir,'attached-codex-conversation-desktop.png'),animations:'disabled'});
    await page.setViewportSize({width:390,height:844});
    await page.emulateMedia({reducedMotion:'reduce'});
    await panel.getByRole('heading',{name:'工作记录',exact:true}).scrollIntoViewIfNeeded();
    assert.ok(await panel.boundingBox());
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true);
    await page.screenshot({path:resolve(outputDir,'attached-codex-conversation-phone.png'),animations:'disabled'});
    // The host may become busy between the idle snapshot and the explicit send.
    const busyRace = async route => {
      await page.request.post(new URL('/fixture/busy',url).href);
      const response = await route.fetch();
      const rejected = await response.json();
      assert.equal(response.status(),409);
      assert.equal(rejected.input_not_submitted,true);
      assert.equal(rejected.operation_id,route.request().postDataJSON().operation_id);
      await route.fulfill({response});
    };
    await page.route('**/api/chat/codex-attached',busyRace);
    await composer.fill('继续检查这份结果。');
    await send.click();
    await panel.getByText('Agent 正在忙碌或状态尚未确认；消息未发送，草稿已保留。',{exact:true}).waitFor();
    await page.unroute('**/api/chat/codex-attached',busyRace);
    assert.equal(await inputCount(),1, 'A pre-dispatch rejection creates no native input');
    assert.equal(await composer.inputValue(),'继续检查这份结果。');
    assert.equal(await panel.getByRole('button',{name:'已核对，写下一条',exact:true}).count(),0);
    assert.equal(await page.evaluate(() => Object.keys(sessionStorage).filter(key => key.startsWith('loopx-attached-input:') && !key.endsWith(':draft')).length),0);
    await page.screenshot({path:resolve(outputDir,'attached-codex-busy-recovery-phone.png'),animations:'disabled'});
    await page.reload();
    await panel.getByText('原会话有未结束执行；可在原 App 中追加输入',{exact:true}).waitFor();
    assert.equal(await composer.inputValue(),'继续检查这份结果。', 'Rejected input remains a draft across reload');
    assert.equal(await inputCount(),1, 'Reload must not retry the rejected input');
    assert.equal(await send.isDisabled(),true, 'Keeping the composer must not bypass host activity');
    await page.request.post(new URL('/fixture/append',url).href);
    await panel.getByText('原会话可回复',{exact:true}).waitFor();
    await send.focus();
    await page.keyboard.press('Enter');
    await panel.getByText('已由原 App 接纳；等待 Agent 结果。',{exact:false}).waitFor();
    assert.equal(await inputCount(),2);
    assert.equal(sentOperations.length,3);
    assert.equal(new Set(sentOperations).size,3, 'Each explicit new reply needs its own operation');
    assert.equal(await composer.inputValue(),'');
    let receiptReadUnavailable = true;
    await page.route('**/api/chat/codex-attached**', async route => {
      if (new URL(route.request().url()).pathname !== '/api/chat/codex-attached') return route.continue();
      if (receiptReadUnavailable && new URL(route.request().url()).searchParams.has('operation_id')) return route.abort('failed');
      if (route.request().method() !== 'POST') return route.continue();
      const response = await route.fetch();
      assert.equal((await response.json()).status, 'delivered');
      // The original owner and receipt store accepted the input, but the
      // browser lost the HTTP acknowledgement. Recovery must only read it.
      await route.abort('failed');
    });
    await composer.fill('响应丢失后沿用原回执恢复。');
    const lostResponse = page.waitForEvent('requestfailed', request => request.method() === 'POST');
    await send.click();
    await lostResponse;
    assert.equal(await inputCount(),3);
    assert.equal(await page.evaluate(() => JSON.parse(Object.entries(sessionStorage).find(([key]) => key.startsWith('loopx-attached-input:') && !key.endsWith(':draft'))[1]).status),'pending');
    receiptReadUnavailable = false;
    await page.reload();
    await panel.getByText('已由原 App 接纳；等待 Agent 结果。',{exact:false}).waitFor({timeout:5000});
    assert.equal(await composer.inputValue(),'');
    assert.equal(await inputCount(),3, 'Automatic recovery must not submit another input');
    assert.equal(sentOperations.length,4);
    await composer.fill('本页也自动读取成功回执。');
    await send.click();
    await panel.getByText('已由原 App 接纳；等待 Agent 结果。',{exact:false}).waitFor();
    assert.equal(await composer.inputValue(),'');
    assert.equal(await inputCount(),4, 'In-page recovery must only read the receipt');
    assert.equal(new Set(sentOperations).size,5);
    await page.setViewportSize({width:1512,height:982});
    await page.getByRole('navigation',{name:'Goal 视图'}).getByRole('button',{name:'概览',exact:true}).click();
    await page.getByRole('button',{name:'与 worker 对话',exact:true}).click();
    const direct = page.getByRole('region',{name:'Agent 工作会话',exact:true});
    await direct.getByText('原会话可回复',{exact:true}).waitFor();
    await direct.getByRole('textbox',{name:'回复 worker',exact:true}).fill('从负责人的工作直接追问。');
    await direct.getByRole('button',{name:'发送到原会话',exact:true}).click();
    await direct.locator('.attached-conversation-messages').getByText('从负责人的工作直接追问。',{exact:true}).waitFor();
    assert.equal(await inputCount(),5);
    assert.equal(new Set(sentOperations).size,6);
    assert.equal(await direct.getByRole('textbox',{name:'回复 worker',exact:true}).inputValue(),'');
    await page.screenshot({path:resolve(outputDir,'attached-codex-owner-conversation.png'),animations:'disabled'});
    await page.request.post(new URL('/fixture/work-records',url).href);
    await direct.getByText('工作结果已返回。',{exact:true}).waitFor();
    const toolResult = direct.locator('article[data-role="tool"]').filter({hasText:'工具结果'});
    await toolResult.locator('summary').click();
    assert.equal(await toolResult.getByText('COMPLETE TOOL RESULT',{exact:false}).count(),0);
    await toolResult.getByRole('button',{name:'继续读取完整内容',exact:true}).click();
    await toolResult.getByText('COMPLETE TOOL RESULT',{exact:false}).waitFor();
    assert.equal(await toolResult.getByRole('button',{name:'继续读取完整内容',exact:true}).count(),0);
    // Unicode detail offsets must count source characters, not JS UTF-16 units.
    assert.equal(await toolResult.locator('pre').textContent(),'Validation output\n' + '🧭验证结果\n'.repeat(6000) + 'COMPLETE TOOL RESULT');
    await toolResult.locator('summary').click();
    await page.screenshot({path:resolve(outputDir,'attached-codex-work-timeline.png'),animations:'disabled'});
    if (await direct.getByRole('button',{name:'回到最新',exact:true}).count()) {
      await direct.getByRole('button',{name:'回到最新',exact:true}).click();
      await direct.getByText('工作结果已返回。',{exact:true}).waitFor();
    }
    await page.request.post(new URL('/fixture/burst',url).href);
    await direct.getByText('Activity 329',{exact:true}).waitFor();
    assert.ok(await direct.locator('article').count() <= 300, 'The visible history is bounded without making older records unreachable');
    await direct.getByRole('button',{name:'读取更早的消息',exact:true}).click();
    await direct.getByText('Original request',{exact:true}).waitFor();
    assert.equal(await inputCount(),5, 'History and tool details never execute another input');
    await page.request.post(new URL('/fixture/append',url).href);
    await direct.getByText('正在回看历史 · 有新活动',{exact:true}).waitFor();
    await direct.getByRole('button',{name:'较新记录',exact:true}).click();
    await direct.getByText('Activity 329',{exact:true}).waitFor();
    // Following history remains continuous across multi-megabyte invisible metadata.
    await page.reload();
    await direct.getByText('Activity 329',{exact:true}).waitFor();
    await page.request.post(new URL('/fixture/metadata-gap',url).href);
    await page.request.post(new URL('/fixture/tool-envelope',url).href);
    const envelope = direct.locator('article[data-role="tool"]').filter({hasText:'退出码 1'});
    await envelope.locator('summary').first().focus();
    await page.keyboard.press('Enter');
    await envelope.getByText('Readable shell result\nSecond line',{exact:true}).waitFor();
    assert.equal(await envelope.locator('pre').first().textContent(),'Readable shell result\nSecond line');
    await envelope.getByText('原始记录字段',{exact:true}).click();
    assert.match(await envelope.locator('.attached-tool-source pre').textContent(), /synthetic/);
    assert.equal(await inputCount(),5);
    await page.screenshot({path:resolve(outputDir,'attached-codex-tool-readable.png'),animations:'disabled'});
    // Reopening at an invisible tail finds prior visible work without an empty
    // page/another click. Reading older crosses the same gap in one action.
    await page.request.post(new URL('/fixture/metadata-gap',url).href);
    await page.reload();
    await panel.getByRole('textbox',{name:'回复 worker',exact:true}).waitFor();
    await panel.locator('article[data-role="tool"] summary').first().waitFor();
    await panel.getByRole('button',{name:'读取更早的消息',exact:true}).click();
    await panel.getByText('Activity 329',{exact:true}).waitFor();
    assert.equal(await send.isDisabled(),true, 'History readback must not infer idle from old events outside the observer window');
    // A pending invisible window must not keep scanning or reinsert the old
    // Agent's content after navigation away. Hold the real backend response.
    let releaseWindow, windowStarted;
    const heldWindow = new Promise(resolve => {releaseWindow = resolve;});
    const windowRead = new Promise(resolve => {windowStarted = resolve;});
    let historyReads = 0;
    const delayedWindow = async route => {
      const query = new URL(route.request().url()).searchParams;
      if (query.get('agent_id') !== 'worker' || query.has('operation_id')) return route.continue();
      historyReads++;
      const response = await route.fetch();
      const value = await response.json();
      if (value.messages?.length === 0 && value.before) {windowStarted(); await heldWindow;}
      await route.fulfill({response});
    };
    await page.route('**/api/chat/codex-attached?*',delayedWindow);
    await page.reload();
    await windowRead;
    await panel.getByRole('combobox',{name:'查看 Agent'}).selectOption('other-worker');
    await panel.getByText('此 Agent 尚无已绑定的会话来源。',{exact:true}).waitFor();
    const lateWindow = page.waitForResponse(response => new URL(response.url()).searchParams.get('agent_id') === 'worker');
    releaseWindow(); await lateWindow;
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.equal(historyReads,1, 'Switching Agent cancels the remaining empty-window chain');
    assert.equal(await panel.locator('article').count(),0);
    await page.unroute('**/api/chat/codex-attached?*',delayedWindow);
    await panel.getByRole('combobox',{name:'查看 Agent'}).selectOption('worker');
    await panel.locator('article[data-role="tool"] summary').first().waitFor();
    await page.request.post(new URL('/fixture/rebind',url).href);
    await panel.getByRole('alert').waitFor();
    await openOriginal.click();
    await panel.getByRole('alert').filter({hasText:'binding is invalid or changed'}).waitFor();
    assert.equal((await (await page.request.get(new URL('/fixture/opened',url).href)).json()).count,1);
    assert.equal(await panel.getByRole('button',{name:'发送到原会话',exact:true}).isDisabled(),true);
  } finally {
    clearTimeout(timer); lines.close();
    if (context) await context.close();
    if (child.exitCode === null) {child.kill(); await once(child,'exit');}
  }
}
