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
    context = await openWorkspacePage(browser, url, {gotoWaitUntil: 'domcontentloaded', apiOptions: {fixturePort: new URL(url).port}, beforeGoto: async (_api, page) => {
      // Unrelated Goal/status APIs use the public synthetic workspace. The
      // original-conversation route always reaches the real production owner.
      await page.route('**/api/chat/codex-attached**', route => route.continue());
    }});
    const {page} = context;
    await page.locator('.personal-goal-link').filter({hasText:'Product Release'}).click();
    await page.getByRole('navigation',{name:'Goal 视图'}).getByRole('button',{name:'对话',exact:true}).click();
    const panel = page.getByRole('dialog', {name:'Codex App 原会话',exact:true});
    await page.screenshot({path:resolve(outputDir,'attached-codex-entry-desktop.png'),animations:'disabled'});
    await page.getByRole('button',{name:'与 Codex App 原会话对话',exact:true}).click();
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
      await composer.fill('核对一次投递，不要自动重发。');
      const submitted = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/chat/codex-attached');
      await send.click();
      await submitted;
      await panel.getByText('投递尚未确认，请先在原 App 核对，不会自动重发。',{exact:false}).waitFor();
      assert.equal(await inputCount(),1);
      await page.reload();
      await page.getByRole('button',{name:'与 Codex App 原会话对话',exact:true}).click();
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
    await page.keyboard.press('Escape');
    assert.equal(await panel.isVisible(), false);
    await page.getByRole('button',{name:'与 Codex App 原会话对话',exact:true}).click();
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
    await page.getByRole('button',{name:'与 Codex App 原会话对话',exact:true}).click();
    await panel.getByText('已由原 App 接纳；等待 Agent 结果。',{exact:false}).waitFor();
    assert.equal(await composer.inputValue(),'', 'Reload must not turn a confirmed message back into a draft');
    assert.equal(await inputCount(),1, 'Reload must not submit anything');
    await page.screenshot({path:resolve(outputDir,'attached-codex-conversation-desktop.png'),animations:'disabled'});
    await page.setViewportSize({width:390,height:844});
    await page.emulateMedia({reducedMotion:'reduce'});
    await panel.getByRole('heading',{name:'Codex App 原会话',exact:true}).scrollIntoViewIfNeeded();
    assert.ok(await panel.boundingBox());
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true);
    await page.screenshot({path:resolve(outputDir,'attached-codex-conversation-phone.png'),animations:'disabled'});
    await page.request.post(new URL('/fixture/busy',url).href);
    await panel.getByText('原会话有未结束执行；可在原 App 中追加输入',{exact:true}).waitFor();
    await composer.fill('继续检查这份结果。');
    assert.equal(await send.isDisabled(),true, 'Keeping the composer must not bypass host activity');
    await page.request.post(new URL('/fixture/append',url).href);
    await panel.getByText('原会话可回复',{exact:true}).waitFor();
    await send.focus();
    await page.keyboard.press('Enter');
    await panel.getByText('已由原 App 接纳；等待 Agent 结果。',{exact:false}).waitFor();
    assert.equal(await inputCount(),2);
    assert.equal(sentOperations.length,2);
    assert.equal(new Set(sentOperations).size,2, 'Each explicit new reply needs its own operation');
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
    assert.equal(await page.evaluate(() => JSON.parse(Object.entries(sessionStorage).find(([key]) => key.startsWith('loopx-attached-input:'))[1]).status),'pending');
    receiptReadUnavailable = false;
    await page.reload();
    await page.getByRole('button',{name:'与 Codex App 原会话对话',exact:true}).click();
    await panel.getByText('已由原 App 接纳；等待 Agent 结果。',{exact:false}).waitFor({timeout:5000});
    assert.equal(await composer.inputValue(),'');
    assert.equal(await inputCount(),3, 'Automatic recovery must not submit another input');
    assert.equal(sentOperations.length,3);
    await composer.fill('本页也自动读取成功回执。');
    await send.click();
    await panel.getByText('已由原 App 接纳；等待 Agent 结果。',{exact:false}).waitFor();
    assert.equal(await composer.inputValue(),'');
    assert.equal(await inputCount(),4, 'In-page recovery must only read the receipt');
    assert.equal(new Set(sentOperations).size,4);
    await page.setViewportSize({width:1512,height:982});
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
