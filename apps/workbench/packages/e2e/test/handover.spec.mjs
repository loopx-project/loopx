import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
const chainDialog = async page => {
  await page.getByLabel('这个线程的更多操作').click();
  await page.getByRole('menuitem', { name: '交接记录', exact: true }).click();
  await page.getByRole('dialog', { name: '交接记录', exact: true }).waitFor();
};
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';

let root, daemon, browser, thread, configDir;
before(async () => {
  root = mkdtempSync(join(tmpdir(),'calli-handover-e2e-'));
  configDir = join(root,'claude-work');
  daemon = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: [{
    id:'claude-work', adapter:'claude', label:'Claude 工作号', command:['fake-claude'],
    env:{CLAUDE_CONFIG_DIR:configDir,FAKE_SESSION_ROOT:configDir,FAKE_ACP_SESSION:'work-session'},
  }, {
    id:'claude-personal',adapter:'claude',label:'Claude 个人号',command:['fake-claude'],
    env:{CLAUDE_CONFIG_DIR:join(root,'claude-personal'),FAKE_SESSION_ROOT:join(root,'claude-personal'),FAKE_ACP_SESSION:'personal-session'},
  }] }) });
  thread = await (await api(daemon,'/api/threads',{method:'POST',body:JSON.stringify({agent:'claude-work',cwd:daemon.workdir,title:'待交接的任务'})})).json();
  await api(daemon,`/api/threads/${thread.id}/turns`,{method:'POST',body:JSON.stringify({text:'修复登录页并补测试'})});
  browser = await launchBrowser();
});
after(async () => { await browser?.close(); await daemon?.stop(); rmSync(root,{recursive:true,force:true}); });

for (const mobile of [false,true]) for (const theme of ['light','dark']) {
  test(`handover is ready to copy on opening: ${mobile?'phone':'desktop'} ${theme}`,async () => {
    const context = await browser.newContext({viewport: mobile?{width:390,height:844}:{width:1440,height:960}, isMobile:mobile,hasTouch:mobile,colorScheme:theme,permissions:['clipboard-read','clipboard-write']});
    const page = await context.newPage();
    try {
      await openUi(page,daemon);
      if (mobile) await page.locator('.menu-btn').click();
      await page.locator('.sidebar .thread-item',{hasText:'待交接的任务'}).click();
      await page.getByText('echo:修复登录页并补测试',{exact:true}).waitFor();
      assert.equal(await page.getByText('unknown agent: claude',{exact:true}).count(),0,'custom instance ids do not query a nonexistent default');
      const before = await (await api(daemon,`/api/threads/${thread.id}/events`)).json();
      await page.getByLabel('这个线程的更多操作').click();
      await page.getByRole('menuitem',{name:'交接',exact:true}).click();
      const dialog = page.getByRole('dialog',{name:'交接',exact:true});
      await dialog.locator('.handover-summary').waitFor();
      const summary = await dialog.locator('.handover-summary').innerText();
      for (const value of ['Claude 工作号','实例 ID：claude-work',daemon.workdir,configDir,'Session ID：work-session','work-session.jsonl','CLAUDE_CONFIG_DIR=','修复登录页并补测试']) assert.ok(summary.includes(value),value);
      assert.equal(await dialog.getByRole('radiogroup').count(),0,'no successor needed even with one available agent');
      assert.deepEqual(await (await api(daemon,`/api/threads/${thread.id}/events`)).json(),before,'opening spends no turn');
      await dialog.getByRole('button',{name:'复制交接信息'}).click();
      await dialog.getByRole('button',{name:'已复制'}).waitFor();
      assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),summary);
      const box=await dialog.boundingBox();
      const vp=page.viewportSize();
      assert.ok(box.x>=0&&box.y>=0&&box.x+box.width<=vp.width+1&&box.y+box.height<=vp.height+1);
      if (process.env.CALLI_SCREENSHOTS === '1') {
        const shots=join(repoRoot,'e2e','shots');
        mkdirSync(shots,{recursive:true});
        await page.screenshot({path:join(shots,`handover-${mobile?'phone':'desktop'}-${theme}.png`),animations:'disabled'});
      }
      await page.keyboard.press('Escape');
      await dialog.waitFor({state:'detached'});
    } finally { await context.close(); }
  });
}


for (const mobile of [false,true]) {
  test(`handover opens an editable unsent draft in the same worktree: ${mobile?'phone':'desktop'}`,async () => {
    const git=(args)=>execFileSync('git',args,{cwd:daemon.workdir,stdio:'pipe'}).toString();
    git(['init','-q']);
    git(['-c','user.name=Test','-c','user.email=test@example.com','-c','commit.gpgsign=false','commit','--allow-empty','-qm','initial']);
    const source=await (await api(daemon,'/api/threads',{method:'POST',body:JSON.stringify({agent:'claude-work',cwd:daemon.workdir,worktree:true,title:mobile?'手机接手来源':'桌面接手来源'})})).json();
    writeFileSync(join(source.cwd,'pending.txt'),'preserve uncommitted work');
    await api(daemon,`/api/threads/${source.id}/turns`,{method:'POST',body:JSON.stringify({text:'保留工作树完成任务'})});
    const context=await browser.newContext({viewport:mobile?{width:390,height:844}:{width:1440,height:960},isMobile:mobile,hasTouch:mobile,colorScheme:mobile?'light':'dark'});
    const page=await context.newPage();
    try {
      await openUi(page,daemon);
      const box=page.locator('.composer textarea');
      await box.fill('普通新线程的草稿');
      if(mobile)await page.locator('.menu-btn').click();
      const sourceRow = page.locator('.sidebar .thread-item',{hasText:source.title});
      assert.equal(await sourceRow.locator('.thread-fork').count(), 0);
      assert.ok((await sourceRow.getAttribute('title')).includes(source.worktree.branch));
      await sourceRow.click();
      await page.getByText('echo:保留工作树完成任务',{exact:true}).waitFor();
      await box.fill('原线程未发送的草稿');
      const eventsBefore=await (await api(daemon,`/api/threads/${source.id}/events`)).json();
      const treesBefore=git(['worktree','list','--porcelain']);
      const idsBefore=new Set((await (await api(daemon,'/api/threads')).json()).map(t=>t.id));
      await page.getByLabel('这个线程的更多操作').click();
      await page.getByRole('menuitem',{name:'交接',exact:true}).click();
      const dialog=page.getByRole('dialog',{name:'交接',exact:true});
      await dialog.locator('.handover-summary').waitFor();
      const summary=await dialog.locator('.handover-summary').innerText();
      await dialog.getByRole('button',{name:'Claude 个人号',exact:true}).waitFor();
      if(!mobile) {
        await page.route('**/handover-thread',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'暂时不可用，请重试'})}),{times:1});
        await dialog.getByRole('button',{name:'新建接手对话'}).click();
        await dialog.getByRole('alert').filter({hasText:'暂时不可用'}).waitFor();
        assert.equal((await (await api(daemon,'/api/threads')).json()).length,idsBefore.size);
        assert.equal(await dialog.locator('.handover-summary').innerText(),summary);
      }
      await dialog.getByRole('button',{name:'新建接手对话'}).click();
      await dialog.waitFor({state:'detached'});
      await page.locator('.topbar-title',{hasText:source.title+'（接手）'}).waitFor();
      assert.equal(await box.inputValue(),summary);
      const next=(await (await api(daemon,'/api/threads')).json()).filter(t=>!idsBefore.has(t.id));
      assert.equal(next.length,1);const successor=next[0];
      assert.equal(successor.cwd,source.cwd);assert.deepEqual(successor.worktree,source.worktree);
      assert.equal(successor.agent,'claude-personal');assert.equal(successor.agentSessionId,null);assert.equal(successor.status,'idle');
      assert.equal((await (await api(daemon,`/api/threads/${successor.id}/events`)).json()).some(e=>e.type==='turn.started'),false);
      assert.deepEqual(await (await api(daemon,`/api/threads/${source.id}/events`)).json(),eventsBefore);
      assert.equal(git(['worktree','list','--porcelain']),treesBefore);
      assert.equal(readFileSync(join(successor.cwd,'pending.txt'),'utf8'),'preserve uncommitted work');
      const edited=summary+'\n\n补充：先运行测试。';await box.fill(edited);
      if(process.env.CALLI_SCREENSHOTS==='1')await page.screenshot({path:join(repoRoot,'e2e','shots',`handover-${mobile?'phone-light':'desktop-dark'}-draft.png`),animations:'disabled'});
      // Manual handovers keep both drafts and composers independent, despite sidebar grouping.
      await chainDialog(page);
      await page.getByRole('menuitem').filter({hasText:'Claude 工作号'}).click();
      assert.equal(await page.locator('.handover-readonly, .merged-history').count(), 0);
      assert.equal(await box.inputValue(),'原线程未发送的草稿');
      assert.equal(await page.evaluate(id=>JSON.parse(localStorage.getItem('calli.drafts'))[id].text,source.id),'原线程未发送的草稿');
      await chainDialog(page);
      await page.getByRole('menuitem').filter({hasText:'Claude 个人号'}).click();
      assert.equal(await box.inputValue(),edited);
      await page.reload();await box.waitFor();
      if(mobile)await page.locator('.menu-btn').click();
      const groupedSourceRow = page.locator('.sidebar .thread-item',{hasText:source.title});
      assert.equal(await groupedSourceRow.locator('.thread-fork').count(), 0);
      assert.ok((await groupedSourceRow.getAttribute('title')).includes(source.worktree.branch));
      await groupedSourceRow.click();
      await page.locator('.topbar-title',{hasText:source.title+'（接手）'}).waitFor();
      assert.equal(await box.inputValue(),edited);
      assert.equal(await page.evaluate(()=>JSON.parse(localStorage.getItem('calli.drafts')).new.text),'普通新线程的草稿');
      await page.getByRole('button',{name:'发送',exact:true}).click();
      let sent=[];
      for(let n=0;n<100;n++) {
        sent=await (await api(daemon,`/api/threads/${successor.id}/events`)).json();
        if(sent.some(e=>e.type==='turn.done'))break;
        await new Promise(r=>setTimeout(r,50));
      }
      assert.equal(sent.filter(e=>e.type==='turn.started').length,1);
      assert.equal(sent.find(e=>e.type==='turn.started')?.data.text,edited);
      assert.equal(sent.filter(e=>e.type==='message.delta').map(e=>e.data.text).join(''),'echo:'+edited);
      assert.ok(sent.some(e=>e.type==='turn.done'));
      assert.equal((await (await api(daemon,`/api/threads/${successor.id}`)).json()).agentSessionId,'personal-session');
      assert.deepEqual(await (await api(daemon,`/api/threads/${source.id}/events`)).json(),eventsBefore);
    } finally {await context.close();}
  });
}
