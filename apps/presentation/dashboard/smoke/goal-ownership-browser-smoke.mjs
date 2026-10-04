import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtemp, mkdir, writeFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve} from 'node:path';
import {createRequire} from 'node:module';
import {createInterface} from 'node:readline';
import {startServer, port, repoRoot} from '../../../../examples/personal-workspace-browser/fixture.mjs';
import {openWorkspacePage} from '../../../../examples/personal-workspace-browser/scenario-context.mjs';
import {resolveTestPython} from '../../../../scripts/test-python.mjs';
const require=createRequire(resolve(repoRoot,'apps/presentation/dashboard/package.json'));
const {chromium}=require('playwright');
// The workspace directory is synthetic; policy requests use the real HTTP/SQLite owner.
const out=resolve(repoRoot,'.local/ownership-browser-smoke');
await mkdir(out,{recursive:true});
const root=await mkdtemp(resolve(tmpdir(),'loopx-ownership-browser-'));
const child=spawn(resolveTestPython(),['-u','-c',String.raw`import json, sys, tempfile, signal
from pathlib import Path
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.control_plane.effect_runtime import restart_effect_runtime
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority
root=Path(sys.argv[1]); tempfile.tempdir=str(root)
goal='multi-agent-projection'
state=root/'state.md'; state.write_text('---\nhandoff_mode: legacy\n---\n\n## Agent Todo\n')
registry=root/'registry.json'; registry.write_text(json.dumps({'common_runtime_root':str(root/'runtime'),'goals':[{'id':goal,'repo':str(root),'state_file':'state.md','coordination':{'registered_agents':['codex']}}]}))
initialize_canonical_authority(root/'runtime',goal,build_todo_runtime_shadow_projection(goal_id=goal,handoff_mode='legacy',todos=[]),state_path=state,provider='sqlite')
server=ChatHTTPServer(('127.0.0.1',0),ChatRequestHandler)
server.runtime_root=root/'runtime';server.registry_path=registry;server.verbose=False
print(server.server_port,flush=True)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
try:
    server.serve_forever()
finally:
    server.server_close()
    restart_effect_runtime()
`,root],{cwd:repoRoot,env:{...process.env,PYTHONPATH:repoRoot,TMPDIR:root,LOOPX_USAGE_PING:'0',NODE_OPTIONS:`${process.env.NODE_OPTIONS ?? ''} --experimental-sqlite`},stdio:['ignore','pipe','inherit']});
const lines=createInterface({input:child.stdout});
let backendPort;
let server, browser, context;
let drop=true; const applies=[];
try {
 backendPort=await new Promise((accept,reject)=>{
  // Bound fixture startup like the shared browser harness, not as a latency oracle.
  const timer=setTimeout(()=>reject(Error('authority startup timeout')),120_000);
  child.once('exit',code=>{clearTimeout(timer);reject(Error(`authority exited: ${code}`));});
  lines.once('line',line=>{clearTimeout(timer);accept(Number(line));});
 });
 server=await startServer();
 browser=await chromium.launch({headless:true});
 context=await openWorkspacePage(browser,`http://127.0.0.1:${port}/chat/?statusUrl=/status.json`,{beforeGoto:async(_api,page)=>{
  await page.route('**/api/chat/goal-ownership**',async route=>{
   const url=new URL(route.request().url());
   const response=await route.fetch({url:`http://127.0.0.1:${backendPort}${url.pathname}${url.search}`});
   if(url.pathname.endsWith('/apply')) {
    applies.push(route.request().postDataJSON());
    if(response.ok() && drop){drop=false; await route.abort();return;}
   }
   await route.fulfill({response});
  });
 }});
 const {page}=context;
 await page.evaluate(()=>localStorage.setItem('loopx-pw-locale','zh-CN'));await page.reload();
 async function enter(){
  await page.locator('.personal-goal-link',{hasText:'Multi Agent Projection'}).click();
  await page.getByRole('button',{name:'Goal 设置',exact:true}).click();
  await page.getByRole('button',{name:'任务所有权',exact:true}).click();
 }
 await enter();
 const panel=page.getByRole('region',{name:'任务所有权'});
 await panel.getByLabel('新策略',{exact:true}).selectOption('hard_lease');
 await panel.getByRole('button',{name:'预览迁移',exact:true}).click();
 await panel.getByText('保留 0 个任务归属和 0 条租约记录。',{exact:true}).waitFor();
 await page.screenshot({path:resolve(out,'desktop-preview.png')});
 await panel.getByRole('button',{name:'备份并应用',exact:true}).click();
 await panel.getByText('结果未知。请保留此预览并重试原操作，不要新建迁移。',{exact:true}).waitFor();
 await page.reload(); await enter();
 await panel.getByRole('button',{name:'备份并应用',exact:true}).click();
 await panel.getByText('原操作已确认，备份已验证。上方单独展示当前策略。',{exact:true}).waitFor();
 assert.equal(applies.length,2);
 assert.deepEqual(applies[0],applies[1],'Retry must use the original reviewed operation');
 await panel.locator('.personal-cadence-readback strong').getByText('独占执行租约',{exact:true}).waitFor();
 await page.setViewportSize({width:390,height:844});
 await panel.getByRole('button',{name:'放弃预览，重新开始',exact:true}).focus();
 await page.keyboard.press('Enter');
 await panel.getByLabel('新策略',{exact:true}).selectOption('soft_claim');
 await panel.getByRole('button',{name:'预览迁移',exact:true}).click();
 await panel.getByRole('button',{name:'备份并应用',exact:true}).click();
 await panel.locator('.personal-cadence-readback strong').getByText('协作认领',{exact:true}).waitFor();
 await page.screenshot({path:resolve(out,'mobile-applied.png')});
 if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('viewport overflow');
 const readback=await fetch(`http://127.0.0.1:${backendPort}/api/chat/goal-ownership?goal_id=multi-agent-projection`).then(r=>r.json());
 if(readback.current_mode!=='soft_claim')throw Error('authoritative readback mismatch');
 await writeFile(resolve(out,'browser-result.json'),JSON.stringify({ok:true,provider:'sqlite',applies,readback,scope:'Packaged app; synthetic workspace directory; real ownership HTTP and SQLite'},null,2));
 await page.evaluate(()=>localStorage.setItem('loopx-pw-locale','en'));
 await page.reload();
 await page.setViewportSize({width:1512,height:982});
 await page.locator('.personal-goal-link',{hasText:'Multi Agent Projection'}).click();
 await page.getByRole('button',{name:'Goal settings',exact:true}).click();
 await page.getByRole('button',{name:'Task ownership',exact:true}).click();
 await page.getByRole('region',{name:'Task ownership'}).getByRole('button',{name:'Back up and apply',exact:true}).waitFor();
 await page.getByRole('region',{name:'Task ownership'}).locator('.personal-cadence-readback strong').getByText('Collaborative claims',{exact:true}).waitFor();
 console.log('Ownership browser journey passed: real SQLite, original-operation recovery, EN/ZH and narrow screen.');
} finally {
 await context?.page.unrouteAll({behavior:'wait'});
 await context?.close(); await browser?.close(); server?.kill('SIGTERM'); child.kill('SIGTERM'); lines.close();
 await new Promise(resolve => child.exitCode !== null ? resolve() : child.once('exit',resolve));
 await rm(root,{recursive:true,force:true});
}
