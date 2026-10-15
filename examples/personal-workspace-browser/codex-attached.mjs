import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {resolve} from 'node:path';
import {createInterface} from 'node:readline';
import {repoRoot, outputDir} from './fixture.mjs';
import {openWorkspacePage} from './scenario-context.mjs';
import {resolveTestPython} from '../../scripts/test-python.mjs';

export async function runCodexAttached(browser) {
  if (process.platform === "win32") return; // Windows has no qualified Desktop follower transport.
  const child = spawn(resolveTestPython({repoRoot}), ['-u', 'examples/personal-workspace-browser/codex_attached_fixture.py'], {cwd: repoRoot, env: {...process.env, PYTHONPATH: repoRoot}, stdio: ['ignore','pipe','pipe']});
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
    assert.equal((await (await page.request.get(new URL('/fixture/opened',url).href)).json()).count,0);
    const openOriginal = panel.getByRole('button',{name:'在 Codex App 中打开',exact:true});
    await page.request.post(new URL('/fixture/fail-open',url).href);
    await openOriginal.focus();
    await page.keyboard.press('Enter');
    await panel.getByRole('alert').filter({hasText:'Codex App could not be opened'}).waitFor();
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
    await page.reload();
    await page.getByRole('button',{name:'与 Codex App 原会话对话',exact:true}).click();
    await panel.getByText('已由原 App 接纳；等待 Agent 结果。',{exact:false}).waitFor();
    await page.screenshot({path:resolve(outputDir,'attached-codex-conversation-desktop.png'),animations:'disabled'});
    await page.setViewportSize({width:390,height:844});
    await page.emulateMedia({reducedMotion:'reduce'});
    await panel.getByRole('heading',{name:'Codex App 原会话',exact:true}).scrollIntoViewIfNeeded();
    assert.ok(await panel.boundingBox());
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true);
    await page.screenshot({path:resolve(outputDir,'attached-codex-conversation-phone.png'),animations:'disabled'});
    await page.setViewportSize({width:1512,height:982});
    await page.request.post(new URL('/fixture/rebind',url).href);
    await panel.getByRole('alert').waitFor();
    await openOriginal.click();
    await panel.getByRole('alert').filter({hasText:'binding is invalid or changed'}).waitFor();
    assert.equal((await (await page.request.get(new URL('/fixture/opened',url).href)).json()).count,1);
    assert.equal(await panel.getByRole('button',{name:'发送到原会话',exact:true}).count(),0);
    await panel.getByRole('button',{name:'已核对，写下一条',exact:true}).click();
    assert.equal(await panel.getByRole('button',{name:'发送到原会话',exact:true}).isDisabled(),true);
  } finally {
    clearTimeout(timer); lines.close();
    if (context) await context.close();
    if (child.exitCode === null) {child.kill(); await once(child,'exit');}
  }
}
