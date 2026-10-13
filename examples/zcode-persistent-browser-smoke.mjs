// Packaged UI -> real loopback Chat/API -> Python bridge -> TS controller/journal.
// Only the vendor's NDJSON protocol is a stub. No page routes or API monkeypatches.
import assert from 'node:assert/strict';
import {spawn, spawnSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {mkdtemp, readFile, readdir, mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve, join, dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'node:net';
import {resolveTestPython} from '../scripts/test-python.mjs';
const repo=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const require=createRequire(join(repo,'apps/presentation/dashboard/package.json'));
const {chromium}=require('playwright');
const root=await mkdtemp(join(tmpdir(),'zcode-owned-'));
const socket=createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
const python=resolveTestPython();
const env={...process.env,HOME:root,USERPROFILE:root,CODEX_HOME:join(root,'codex'),LOOPX_RUNTIME_ROOT:join(root,'runtime'),PYTHONPATH:repo,PYTHONUTF8:'1',TEMP:root,TMP:root,LOOPX_USAGE_PING:'0',DO_NOT_TRACK:'1'};
const server=spawn(python,[join(repo,'examples/personal-workspace-browser/zcode-persistent-fixture.py'),root,String(port)],{cwd:repo,env,stdio:['ignore','ignore','pipe'],windowsHide:true});
let serverErrors='';server.stderr.on('data',x=>serverErrors+=x);
const url=`http://127.0.0.1:${port}`;
const route='/api/goals/persistent-zcode/agents/zcode-worker/zcode-goal';
let browser,identity;let firstOpen=true;
async function readback(){const res=await fetch(url+route);assert.equal(res.status,200);return await res.json();}
async function journal(){const dir=join(root,'runtime/zcode-goal');const files=(await readdir(dir)).filter(f=>f.endsWith('.json'));assert.equal(files.length,1);const file=join(dir,files[0]);return {file,state:JSON.parse(await readFile(file,'utf8'))};}
try {
 const deadline=Date.now()+45000;
 while(true){try {const r=await fetch(url+'/');if(r.ok)break;}catch{}if(server.exitCode!==null||Date.now()>deadline)throw Error('Startup failed: '+serverErrors+'; exit='+server.exitCode);await new Promise(r=>setTimeout(r,100));}
 browser=await chromium.launch({headless:true,...(process.env.LOOPX_BROWSER_EXECUTABLE ? {executablePath:process.env.LOOPX_BROWSER_EXECUTABLE} : {})});const page=await browser.newPage({locale:'zh-CN',viewport:{width:1512,height:982}});
 const errors=[];const nativeRequests=[];page.on('request',r=>{if(new URL(r.url()).pathname===route)nativeRequests.push(r.method());});page.on('pageerror',e=>errors.push(e.message));
 page.on('response',async r=>{if(r.status()>=400)console.error('HTTP failure',r.status(),new URL(r.url()).pathname,(await r.text()).slice(0,1800));});
 await page.goto(url,{waitUntil:'networkidle'});
 assert.equal(await page.locator('html').getAttribute('lang'),'zh-CN', 'Acceptance selectors require the explicitly configured Chinese locale');
 await page.locator('.personal-goal-link').filter({hasText:'Persistent ZCode'}).click();
 await mkdir(join(root,'evidence'));
 const open=async()=>{
  await page.getByRole('button',{name:'概览',exact:true}).click();
  const info=page.getByRole('button',{name:'Goal 信息',exact:true});
  await info.waitFor();await info.click();
  const control=page.locator('.personal-zcode-goal');await control.waitFor();
  if(!await control.evaluate(el=>el.open)){if(firstOpen){assert.deepEqual(nativeRequests,[], 'Collapsed controls must not probe the optional native host');firstOpen=false;}await control.locator('summary').first().click();}
  await control.getByRole('button',{name:'回读状态',exact:true}).waitFor();return control;
 };
 const registryBefore=await readFile(join(root,'registry.json'));
 const stateBefore=await readFile(join(root,'project/GOAL.md'));
 let control=await open();const cli=control.getByRole('textbox',{name:'CLI 路径'});
 const initial=await readback();assert.equal(initial.binding,null);assert.equal(initial.native,null);
 assert.equal((await readdir(join(root,'runtime'))).includes('zcode-goal'),false);
 await cli.fill(join(root,'missing-vendor.cjs'));await control.getByRole('button',{name:'绑定 CLI',exact:true}).click();
 await control.getByRole('alert').waitFor();assert.equal(await cli.inputValue(),join(root,'missing-vendor.cjs'));
 assert.equal((await readback()).binding,null);
 await control.getByRole('button',{name:'回读状态',exact:true}).click();
 await cli.fill(join(repo,'examples/personal-workspace-browser/zcode-persistent-vendor.cjs'));
 await control.getByRole('button',{name:'绑定 CLI',exact:true}).click();await control.getByText('已连接',{exact:true}).waitFor();
 identity=(await readback()).native.session_id;assert.equal(identity,'persistent-session');
 const first=await journal();assert.equal(first.state.session_id,identity);
 await control.getByRole('combobox',{name:'已配置模型'}).selectOption(JSON.stringify({providerId:'isolated',modelId:'fixture'}));
 await control.getByRole('button',{name:'使用此模型',exact:true}).click();
 await control.getByRole('button',{name:'启动',exact:true}).click();await control.getByText('正在运行',{exact:true}).waitFor();
 let active=await readback();assert.equal(active.native.running,true);assert.equal(active.quota.should_run,true);
 assert.equal((await journal()).state.target_id,active.native.target_id);
 await page.reload({waitUntil:'networkidle'});control=await open();
 await control.getByText('正在运行',{exact:true}).waitFor();assert.equal((await readback()).native.session_id,identity);
 await control.getByRole('button',{name:'暂停',exact:true}).click();await control.getByText('已暂停',{exact:true}).waitFor();
 assert.equal((await readback()).native.running,false);
 await control.getByRole('button',{name:'恢复',exact:true}).click();await control.getByText('正在运行',{exact:true}).waitFor();
 await control.getByRole('button',{name:'停止',exact:true}).click();await control.getByText('未连接',{exact:true}).waitFor();
 const stopped=await readback();assert.equal(stopped.binding.connected,false);assert.equal(stopped.native,null);
 const ended=await journal();assert.equal(ended.file,first.file);assert.equal(ended.state.session_id,identity);assert.equal(ended.state.target_id,null);
 const vendor=JSON.parse(await readFile(ended.file+'.host/vendor.json','utf8'));assert.equal(vendor.running,false);assert.equal(vendor.target,null);
 await page.reload({waitUntil:'networkidle'});control=await open();await control.getByText('未连接',{exact:true}).waitFor();
 assert.equal((await journal()).file,first.file);assert.equal((await readback()).binding.connected,false);
 const events=(await readFile(ended.file+'.host/events.jsonl','utf8')).trim().split('\n').map(line=>JSON.parse(line));
 assert.equal(events.filter(e=>e.action==='set').length,1);
 assert.equal(events.filter(e=>e.action==='resume').length,1);
 assert.equal(events.filter(e=>e.action==='clear').length,1);
 assert.equal(events.filter(e=>e.action==='clear')[0].running,false);
 assert.deepEqual(await readFile(join(root,'registry.json')),registryBefore);
 assert.deepEqual(await readFile(join(root,'project/GOAL.md')),stateBefore);
 const canonical=spawnSync(python,['-m','loopx.cli','--registry',join(root,'registry.json'),'--format','json','todo','list','--goal-id','persistent-zcode'],{cwd:join(root,'project'),env,encoding:'utf8',timeout:30000,windowsHide:true});
 assert.equal(canonical.status,0,canonical.stderr);
 const work=JSON.parse(canonical.stdout).todos.find(row=>row.todo_id==='todo_persistent_work');
 assert.equal(work.status,'open');assert.equal(work.done,false, 'Native stop must not settle canonical work');
 await page.screenshot({path:join(root,'evidence/stopped-reloaded.png')});assert.deepEqual(errors,[]);
 console.log(JSON.stringify({ok:true,scenario:'same-owner-packaged-recovery',session_preserved:true,journal_preserved:true,vendor_stopped:true,evidence_root:root}));
} catch(error) {
 console.error(serverErrors.slice(-3000));
 if(browser){const pages=browser.contexts().flatMap(c=>c.pages());if(pages[0])console.error((await pages[0].locator('body').innerText()).slice(0,4000));}
 throw error;
} finally {
 if(identity){try {const r=await readback();await fetch(url+route,{method:'POST',headers:{'content-type':'application/json','origin':url},body:JSON.stringify({action:'stop',expected_binding:{goal_ref:r.goal_ref,goal_creation_operation_id:r.goal_creation_operation_id}})});}catch{}}
 await browser?.close();server.kill();
}
