import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { launchBrowser } from '../../e2e/harness.mjs';
let vite, browser, base;
before(async () => {
  vite = await createServer({root:fileURLToPath(new URL('../',import.meta.url)),server:{port:0,host:'127.0.0.1'}});
  await vite.listen();base=`http://127.0.0.1:${vite.httpServer.address().port}`;browser=await launchBrowser();
});
after(async()=>{await browser?.close();await vite?.close();});
async function setup(t, phone=false){
  const page=await browser.newPage({viewport:phone?{width:390,height:844}:{width:1280,height:900},locale:'zh-CN',reducedMotion:'reduce',hasTouch:phone,isMobile:phone});
  t.after(()=>page.close());
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/src/main.tsx',r=>r.fulfill({contentType:'text/javascript',body:"import '/src/styles.css'; import '/e2e/explore-fixture.tsx';"}));
  await page.goto(base);await page.locator('.explore-dialog').waitFor();return {page,errors};
}

test('the checklist preserves edits and retries only failed saves',async t=>{
  const {page,errors}=await setup(t);
  assert.equal(await page.getByRole('switch',{name:'你和你的上级',exact:true}).isDisabled(),true);
  await page.getByRole('button',{name:'开始看看',exact:true}).click();
  await page.locator('.explore-fields').waitFor();
  assert.equal(await page.locator('.explore-draft').count(),0);
  await page.getByRole('button',{name:/还有 3 个/}).click();
  await page.getByRole('button',{name:'Person 7',exact:true}).click();
  await page.getByRole('button',{name:'Person 0 上级'}).click();
  await page.locator('.explore-pop').getByRole('button',{name:'PM',exact:true}).click();
  await page.keyboard.press('Escape');
  await page.getByRole('button',{name:'搜索添加',exact:true}).first().click();
  await page.getByRole('textbox',{name:'搜索姓名',exact:true}).fill('Search');
  await page.getByRole('button',{name:'Search result Found in directory'}).click();
  await page.getByRole('button',{name:'看草稿',exact:true}).click();
  await page.getByRole('textbox',{name:'我的说明',exact:true}).fill('Edited without opting into replacement');
  assert.equal(await page.getByRole('switch',{name:'推到飞书',exact:true}).isDisabled(),true);
  await page.getByRole('button',{name:'按这样配',exact:true}).click();
  await page.locator('.explore-failures').waitFor();
  assert.match(await page.locator('.explore-failures').innerText(),/早晚报/);
  let calls=await page.evaluate(()=>window.probe.calls);
  assert.equal(calls.filter(c=>c.key==='profile').length,0,'written notes are not overwritten');
  const lark=calls.find(c=>c.key==='lark').value;
  assert.equal(lark.people.find(p=>p.id==='person-0').role,'pm');
  assert.ok(lark.people.some(p=>p.id==='search-result'));
  assert.ok(lark.events.includes('existing.event'));
  const before=calls.length;
  await page.getByRole('button',{name:'重试未保存项',exact:true}).click();
  await page.waitForFunction(()=>Boolean(window.probe.applied));
  calls=await page.evaluate(()=>window.probe.calls);
  assert.deepEqual(calls.slice(before).map(c=>c.key),['brief']);
  assert.deepEqual(errors,[]);
});

test('stop cancels the run and closing a phone sheet stops polling',async t=>{
  const {page,errors}=await setup(t,true);
  const sheet=await page.locator('.explore-dialog').boundingBox();
  assert.equal(Math.round(sheet.y+sheet.height),844);
  await page.getByRole('button',{name:'开始看看',exact:true}).click();
  await page.getByRole('button',{name:'停下',exact:true}).click();
  await page.getByText('已停下，读到的进度留在这里。',{exact:true}).waitFor();
  assert.ok(await page.evaluate(()=>window.probe.calls.some(c=>c.key==='cancel')));
  await page.getByRole('button',{name:'开始看看',exact:true}).click();
  await page.getByRole('button',{name:'关闭',exact:true}).click();
  await page.waitForTimeout(100);
  const reads=await page.evaluate(()=>window.probe.reads);
  await page.waitForTimeout(900);
  assert.equal(await page.evaluate(()=>window.probe.reads),reads);
  assert.deepEqual(errors,[]);
});


test('phone checklist menus keep the full sheet width and time edits survive closing the menu',async t=>{
  const {page,errors}=await setup(t,true);
  await page.getByRole('button',{name:'开始看看',exact:true}).click();
  await page.locator('.explore-fields').waitFor();
  await page.getByRole('button',{name:'Person 0 上级'}).click();
  await page.locator('.explore-pop').evaluate(el=>Promise.all(el.getAnimations().map(animation=>animation.finished)));
  const menu=await page.locator('.explore-pop').boundingBox();
  assert.equal(Math.round(menu.width),390);
  assert.equal(Math.round(menu.y+menu.height),844);
  await page.keyboard.press('Escape');
  await page.locator('.explore-times').getByRole('button',{name:'早报',exact:true}).click();
  await page.getByRole('menuitemradio',{name:'09:15',exact:true}).click();
  assert.equal(await page.locator('.explore-times').getByRole('button',{name:'早报',exact:true}).innerText(),'09:15');
  assert.equal(await page.locator('.explore-dialog').count(),1);
  assert.deepEqual(errors,[]);
});
