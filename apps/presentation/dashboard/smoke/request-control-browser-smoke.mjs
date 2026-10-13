// Real source registry, canonical SQLite, Chat entrance, request controls and Agent CLI.
// Synthetic progress only; no model runs or user runtime activation.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {readFile,mkdtemp,mkdir,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve} from 'node:path';
import {createServer} from 'node:net';
import {createInterface} from 'node:readline';
import {testPythonCommand} from '../../../../scripts/test-python.mjs';
import {launchBrowser,loadPlaywright} from '../../../../examples/dashboard-browser-smoke-support.mjs';
const repo=resolve(import.meta.dirname,'../../../..');
const output=resolve(repo,'output/playwright/request-control');await mkdir(output,{recursive:true});
const root=await mkdtemp(resolve(tmpdir(),'request-control-browser-'));
const socket=createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
const command=testPythonCommand(await readFile(resolve(repo,'apps/presentation/dashboard/smoke/request-control-http-fixture.py'),'utf8'),{repoRoot:repo});
const child=spawn(command.command,[...command.args,root,String(port)],{cwd:root,env:{...process.env,LOOPX_USAGE_PING:'0',TMPDIR:root,NODE_OPTIONS:'--experimental-sqlite'},stdio:['pipe','pipe','pipe']});
let stderr='';child.stderr.on('data',x=>stderr+=String(x));
const lines=createInterface({input:child.stdout});const replies=[];const waiting=[];
let readyResolve,readyReject;const ready=new Promise((r,j)=>{readyResolve=r;readyReject=j;});
lines.on('line',line=>{if(line.startsWith('Serving LoopX Chat'))readyResolve();if(line.startsWith('FIXTURE ')){const v=JSON.parse(line.slice(8));if(waiting.length)waiting.shift()(v);else replies.push(v);}});
child.once('exit',code=>readyReject(Error(`fixture exited ${code}: ${stderr}`)));
async function fixture(op){const result=new Promise(r=>replies.length?r(replies.shift()):waiting.push(r));child.stdin.write(op+'\n');const value=await result;assert.equal(value.fixture_error,undefined);return value;}
let browser,page;
try{
 let timer; try {await Promise.race([ready,new Promise((_,j)=>{timer=setTimeout(()=>j(Error('startup timeout '+stderr)),90000);})]);} finally {clearTimeout(timer);}
 browser=await launchBrowser(loadPlaywright().chromium);page=await browser.newPage({viewport:{width:1440,height:1100}});
 await page.addInitScript(()=>!localStorage.getItem('loopx-pw-locale')&&localStorage.setItem('loopx-pw-locale','zh-CN'));
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const writes=[];let drop=true;let droppedResolve;const dropped=new Promise(r=>{droppedResolve=r;});
 await page.route('**/api/chat/request-control',async route=>{
  if(route.request().method()!=='POST'||route.request().postDataJSON().execute!==true)return route.continue();
  const body=route.request().postDataJSON();writes.push(body);
  const response=await route.fetch();
  if(drop&&body.operation==='revise'&&response.ok()){drop=false;await route.abort();droppedResolve();}else await route.fulfill({response});
 });
 await page.goto(`http://127.0.0.1:${port}/chat/`);
 async function enter(){
  await page.getByRole('navigation',{name:'管家视图'}).getByRole('button',{name:/^(Chat|对话)$/}).click();
  await page.getByText('Please verify this contract. Preserve the constraints and return findings.',{exact:true}).waitFor();
  const card=page.locator('.personal-collaboration').first();
  await card.getByText('查看交办内容',{exact:true}).click();
  const open=card.getByRole('button',{name:'修正或取消请求',exact:true});
  if(await open.count())await open.click();
  await card.locator('.personal-request-control').waitFor();
  return card;
 }
 let card=await enter();let panel=card.locator('.personal-request-control');
 await panel.getByRole('button',{name:'修正内容',exact:true}).click();
 await panel.getByLabel('背景与补充',{exact:true}).fill('Use corrected evidence for this contract');
 await panel.getByRole('button',{name:'保存修正',exact:true}).click();
 await dropped;
 await page.waitForFunction(()=>[...document.querySelectorAll('button')].find(b=>b.textContent==='重试原操作')?.disabled===false);
 assert.equal((await fixture('inspect')).revision,2);
 await page.reload();card=await enter();panel=card.locator('.personal-request-control');
 await panel.getByRole('button',{name:'重试原操作',exact:true}).click();
 await panel.getByRole('button',{name:'修正内容',exact:true}).waitFor();
 assert.deepEqual(writes[0],writes[1]);assert.equal((await fixture('inspect')).revision,2);
 await panel.getByRole('button',{name:'修正内容',exact:true}).click();
 await panel.getByLabel('背景与补充',{exact:true}).fill('A stale editor must not overwrite another correction');
 await fixture('correct');
 await panel.getByRole('button',{name:'保存修正',exact:true}).click();
 await panel.getByText('请求已有新修订。已重新读取，请基于当前内容修改。',{exact:true}).waitFor();
 await panel.getByRole('button',{name:'修正内容',exact:true}).click();
 assert.equal(await panel.getByLabel('背景与补充',{exact:true}).inputValue(),'Correction from another authorized view');
 await panel.getByRole('button',{name:'收起编辑',exact:true}).click();
 await fixture('receiver');await fixture('interim');
 await page.reload();card=await enter();panel=card.locator('.personal-request-control');
 await page.getByText('Checking the corrected request',{exact:true}).waitFor();
 await fixture('reserve');
 await panel.getByRole('button',{name:'重新读取',exact:true}).click();
 await panel.getByText('目标变更仍待核对',{exact:true}).waitFor();
 await panel.screenshot({path:resolve(output,'pending-effect.png')});
 const failedRead=async route=>route.abort();
 await page.route('**/api/chat/request-control?*',failedRead);
 await panel.getByRole('button',{name:'重新读取',exact:true}).click();
 await page.waitForFunction(()=>![...document.querySelectorAll('.personal-request-control button')].some(b=>b.textContent==='取消请求'));
 assert.equal(await panel.getByRole('button',{name:'取消请求',exact:true}).count(),0);
 await page.unroute('**/api/chat/request-control?*',failedRead);
 await panel.getByRole('button',{name:'重新读取',exact:true}).click();
 await panel.getByRole('button',{name:'取消请求',exact:true}).waitFor();
 await panel.getByRole('button',{name:'取消请求',exact:true}).click();
 await panel.getByText('请求已取消',{exact:true}).waitFor();
 assert.equal((await fixture('inspect')).terminal_effects[0].terminal.outcome,'aborted');
 await panel.screenshot({path:resolve(output,'cancelled.png')});
 await page.screenshot({path:resolve(output,'whole-viewport.png')});
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.locator('body').evaluate(e=>e.scrollWidth<=innerWidth+1));
 await panel.screenshot({path:resolve(output,'narrow.png')});
 await page.evaluate(()=>localStorage.setItem('loopx-pw-locale','en'));
 await page.reload();
 await page.getByRole('navigation',{name:'Manager view'}).getByRole('button',{name:'Chat',exact:true}).click();
 const english=page.locator('.personal-collaboration').first();
 await english.getByText('View delegation details',{exact:true}).click();
 await english.getByText('Request cancelled',{exact:true}).waitFor();
 await english.screenshot({path:resolve(output,'english.png')});
 assert.deepEqual(errors,[]);
 console.log('PASS: real source registration, normal Chat entrance, original request correction, unknown reply/reload, stale edit, independent receiver CLI, reserved effect and cancellation terminal readback.');
}catch(error){if(page){await page.screenshot({path:resolve(output,'failure.png'),fullPage:true}).catch(()=>{});await (await import('node:fs/promises')).writeFile(resolve(output,'failure.txt'),await page.locator('body').innerText());}throw error;}
finally{await page?.unrouteAll({behavior:"ignoreErrors"}).catch(()=>{});await browser?.close();child.kill('SIGTERM');await new Promise(r=>child.exitCode!==null?r():child.once('exit',r));lines.close();await rm(root,{recursive:true,force:true});}
