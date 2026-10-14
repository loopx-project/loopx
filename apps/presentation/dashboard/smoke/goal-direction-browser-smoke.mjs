import assert from 'node:assert/strict';
import {spawn, spawnSync} from 'node:child_process';
import {mkdtemp, mkdir, writeFile, readFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve} from 'node:path';
import {createRequire} from 'node:module';
import {createServer} from 'node:net';
import {createInterface} from 'node:readline';
import {testPythonCommand} from '../../../../scripts/test-python.mjs';

const repo = resolve(import.meta.dirname, '../../../..');
const {chromium} = createRequire(resolve(repo, 'apps/presentation/dashboard/package.json'))('playwright');
const output = resolve(repo, '.local/goal-direction-browser');
await mkdir(output, {recursive: true});
const root = await mkdtemp(resolve(tmpdir(), 'loopx-direction-browser-'));
const reservation = createServer();
await new Promise(accept => reservation.listen(0, '127.0.0.1', accept));
const port = reservation.address().port;
await new Promise(accept => reservation.close(accept));
// Real project-register, fresh-authority owner, packaged assets and HTTP server.
// No status or provider fixture replaces a production response. Generic runtime
// reads remain unavailable for this explicit source-session profile.
const backend = testPythonCommand(String.raw`
import contextlib, io, json, signal, sys, tempfile
from pathlib import Path
from loopx.cli import main
from loopx.chat_server import serve_chat
from loopx.control_plane.effect_runtime import restart_effect_runtime
root = Path(sys.argv[1]); tempfile.tempdir = str(root)
registry = root / 'project/.loopx/registry.json'; runtime = root / 'runtime'
with contextlib.redirect_stdout(io.StringIO()) as output:
    code = main(['--registry', str(registry), '--runtime-root', str(runtime), '--format', 'json', 'project', 'register',
        '--project-id', 'governed-work', '--project-kind', 'work', '--knowledge-root', str(root / 'project'),
        '--goal-id', 'amendment-goal', '--objective', 'Verify the public contract', '--non-goal', 'No remote changes',
        '--acceptance', 'Independent validation passes', '--next-effect', 'Inspect the current contract',
        '--stop-condition', 'Stop when the Goal is inactive', '--goal-instance-profile', 'source_session_v1',
        '--operation-id', 'create-goal', '--registered-agent', 'agent-a', '--registered-agent', 'agent-b', '--canonical-storage', 'sqlite'])
assert code == 0, output.getvalue()
created = json.loads(output.getvalue())
assert created['storage_initialization']['authority_initialized'] is True
(root / 'registration.json').write_text(json.dumps(created))
(root / 'project/direction.md').write_text('# Direction\nPreserve the original Goal.\n')
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
try:
    serve_chat(registry_path=registry, runtime_root_override=runtime, scan_roots=[root / 'project'], port=int(sys.argv[2]),
        goal_id='amendment-goal', codex_bin='missing-synthetic-codex', claude_bin='missing-synthetic-claude', kiro_cli_bin='missing-synthetic-kiro')
finally:
    restart_effect_runtime()
`);
const child = spawn(backend.command, [...backend.args, root, String(port)], {cwd: root, env: {...process.env, LOOPX_USAGE_PING: '0', TMPDIR: root, TEMP: root, TMP: root,
  NODE_OPTIONS: `${process.env.NODE_OPTIONS ?? ''} --experimental-sqlite`}, stdio: ['ignore', 'pipe', 'pipe']});
const lines = createInterface({input: child.stdout});
let serverLog = '';
child.stderr.on('data', chunk => {serverLog += String(chunk);});
let browser,page;
try {
  await new Promise((accept,reject)=>{
    const timer=setTimeout(()=>reject(Error('Direction server startup timed out')),120_000);
    child.once('exit',code=>{clearTimeout(timer);reject(Error(`Server exited ${code}: ${serverLog}`));});
    lines.on('line',line=>{if(line.startsWith('Serving LoopX Chat')){clearTimeout(timer);accept();}});
  });
  browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:1512,height:1100}});
  await page.addInitScript(()=>localStorage.setItem('loopx-pw-locale','zh-CN'));
  let drop=true;const applications=[];
  await page.route('**/api/chat/goal-direction',async route=>{
    const request=route.request();if(request.method()!=='POST'||!request.postDataJSON().execute)return route.continue();
    applications.push(request.postDataJSON());const response=await route.fetch();
    const result=await response.json();if(drop&&result.ok){drop=false;await route.abort();}else await route.fulfill({response});
  });
  await page.goto(`http://127.0.0.1:${port}/chat/`);
  async function enter(){
    await page.locator('.personal-goal-link',{hasText:'amendment-goal'}).click();
    await page.getByRole('button',{name:'Goal 设置',exact:true}).click();
    await page.getByRole('button',{name:'任务所有权',exact:true}).click();
    await page.getByText('方向依据与资料版本',{exact:true}).click();
    await page.getByRole('combobox',{name:'查看 Agent',exact:true}).waitFor();
  }
  await enter();
  const panel=page.locator('details').filter({has:page.locator('summary',{hasText:'方向依据与资料版本'})});
  await panel.getByText('设置或更新资料',{exact:true}).click();
  for(const [label,value] of [['资料标识 1','contract'],['资料路径 1','direction.md'],['资料用途 1','Preserve the original Goal']])await panel.getByLabel(label,{exact:true}).fill(value);
  await panel.getByRole('button',{name:'检查资料声明',exact:true}).click();
  await panel.getByRole('button',{name:'确认应用声明',exact:true}).waitFor();
  await panel.screenshot({path:resolve(output,'preview.png')});
  await panel.getByRole('button',{name:'确认应用声明',exact:true}).click();
  await panel.getByRole('button',{name:'查阅原操作回执',exact:true}).waitFor();
  await page.reload();await enter();
  await panel.getByRole('button',{name:'重试原操作',exact:true}).click();
  await panel.getByRole('button',{name:'重试原操作',exact:true}).waitFor({state:'detached'});
  assert.equal(applications.length,2);assert.deepEqual(applications[0],applications[1]);
  await panel.getByText('仍有资料未读取或不可用',{exact:true}).waitFor();
  const invoke=testPythonCommand(String.raw`
import contextlib,io,json,sys
from pathlib import Path
from loopx.cli import main
root=Path(sys.argv[1]);episode=sys.argv[2];ref=json.loads((root/'registration.json').read_text())['goal_ref']
def call(operation,fields=None):
    args=['--registry',str(root/'project/.loopx/registry.json'),'--format','json','goal-direction',
        '--operation',operation,'--goal-id',ref['goal_id'],'--goal-instance-id',ref['goal_instance_id'],'--agent-id','agent-a']
    if fields is not None:
        path=root/'request.json';path.write_text(json.dumps(fields));args+=['--request-json',str(path),'--execute']
    with contextlib.redirect_stdout(io.StringIO()) as output:code=main(args)
    assert code==0,output.getvalue()
    return json.loads(output.getvalue())
view=call('inspect');item=view['projection']['items'][0]
read=call('read',{'material_id':'contract','source_path':'direction.md','required_revision':item['required_revision'],
    'authority_digest':view['authority_digest'],'operation_id':'peer-read-'+episode})
assert read['content']==(root/'project/direction.md').read_text()
assert call('inspect')['projection']['direction_state']!='current'
call('acknowledge',{'read_operation_id':'peer-read-'+episode,'delivery_digest':read['delivery_digest'],'operation_id':'peer-ack-'+episode})
assert call('inspect')['projection']['direction_state']=='current'
`);
  const run=spawnSync(invoke.command,[...invoke.args,root,'initial'],{cwd:root,encoding:'utf8',timeout:60_000,
    env:{...process.env,LOOPX_USAGE_PING:'0',TMPDIR:root,TEMP:root,TMP:root,NODE_OPTIONS:`${process.env.NODE_OPTIONS??''} --experimental-sqlite`}});
  assert.equal(run.status,0,run.stdout+run.stderr);
  await panel.getByRole('button',{name:'读取当前方向状态',exact:true}).click();
  await panel.getByText('已确认当前资料版本',{exact:true}).waitFor();
  await panel.getByRole('combobox',{name:'查看 Agent',exact:true}).selectOption('agent-b');
  await panel.getByText('仍有资料未读取或不可用',{exact:true}).waitFor();
  await panel.getByRole('combobox',{name:'查看 Agent',exact:true}).selectOption('agent-a');
  await panel.getByText('已确认当前资料版本',{exact:true}).waitFor();
  // Saturate only the disposable registered SQLite projection. The actual
  // source read and UI readback still use the production owner and HTTP route.
  const capacity=testPythonCommand(String.raw`
import contextlib,io,json,subprocess,sys
from pathlib import Path
import loopx
from loopx.cli import main
root=Path(sys.argv[1]);ref=json.loads((root/'registration.json').read_text())['goal_ref']
provider=(Path(loopx.__file__).resolve().parent/'control_plane/coordination/local_authority_provider.ts').as_uri()
seed="""
const {openLocalAuthorityStore}=await import(process.argv[1]);
const store=await openLocalAuthorityStore(process.argv[2],process.argv[3]);
const loaded=await store.loadAuthority();if(loaded.status!=='loaded')throw Error('missing');
const row=loaded.head.material_usage_receipts[0];
const records=Array.from({length:4096},(_,i)=>({...row,read_operation_id:'history-'+i,
 receipt:{...row.receipt,receipt_id:'history-'+i,recorded_at:new Date(Date.UTC(2026,0,1)+i).toISOString()}}));
const result=await store.commitAuthority({expected_provider_revision:loaded.provider_revision,operation_id:'full-history',
 events:[],receipts:[],next_projection:{...loaded.head,material_usage_receipts:records}});
if(result.status!=='applied')throw Error(JSON.stringify(result));
"""
subprocess.run(['node','--no-warnings','--experimental-strip-types','--input-type=module','-e',seed,provider,str(root/'runtime'),ref['goal_id']],check=True)
def call(operation,fields=None):
    args=['--registry',str(root/'project/.loopx/registry.json'),'--format','json','goal-direction',
        '--operation',operation,'--goal-id',ref['goal_id'],'--goal-instance-id',ref['goal_instance_id'],'--agent-id','agent-a']
    if fields is not None:
        request=root/'capacity-request.json';request.write_text(json.dumps(fields));args+=['--request-json',str(request),'--execute']
    with contextlib.redirect_stdout(io.StringIO()) as output:code=main(args)
    return code,json.loads(output.getvalue())
_,view=call('inspect');assert view['projection']['direction_state']=='current'
path=root/'project/direction.md';(root/'capacity-original.txt').write_text(path.read_text());path.unlink()
code,failed=call('read',{'material_id':'contract','source_path':'direction.md',
    'required_revision':view['projection']['items'][0]['required_revision'],
    'authority_digest':view['authority_digest'],'operation_id':'full-unavailable'})
assert code==1 and failed['read_outcome']=='unavailable',failed
_,cold=call('inspect');assert cold['projection']['direction_state']=='blocked' and cold['receipt_count']==4096,cold
`);
  const exhausted=spawnSync(capacity.command,[...capacity.args,root],{cwd:root,encoding:'utf8',timeout:60_000,
    env:{...process.env,LOOPX_USAGE_PING:'0',TMPDIR:root,TEMP:root,TMP:root}});
  assert.equal(exhausted.status,0,exhausted.stdout+exhausted.stderr);
  await panel.getByRole('button',{name:'读取当前方向状态',exact:true}).click();
  await panel.getByText('仍有资料未读取或不可用',{exact:true}).waitFor();
  await page.screenshot({path:resolve(output,'full-capacity-blocked.png')});
  await writeFile(resolve(root,'project/direction.md'),await readFile(resolve(root,'capacity-original.txt')));
  const capacityRecovered=spawnSync(invoke.command,[...invoke.args,root,'capacity-recovery'],{cwd:root,encoding:'utf8',timeout:60_000,
    env:{...process.env,LOOPX_USAGE_PING:'0',TMPDIR:root,TEMP:root,TMP:root}});
  assert.equal(capacityRecovered.status,0,capacityRecovered.stdout+capacityRecovered.stderr);
  await panel.getByRole('button',{name:'读取当前方向状态',exact:true}).click();
  await panel.getByText('已确认当前资料版本',{exact:true}).waitFor();
  await page.screenshot({path:resolve(output,'full-capacity-recovered.png')});
  // Owner updates the declared basis; a source edit between review/apply
  // refuses, unlocks correction and never restamps the old usage receipt.
  await writeFile(resolve(root,'project/direction.md'),'# Revised direction\nPreserve the Goal and independently check dependency.\n');
  await panel.getByText('设置或更新资料',{exact:true}).click();
  await panel.getByRole('button',{name:'检查资料声明',exact:true}).click();
  await panel.getByRole('button',{name:'确认应用声明',exact:true}).waitFor();
  await writeFile(resolve(root,'project/direction.md'),'# New reviewed direction\nPreserve all commitments and independently check dependency.\n');
  await panel.getByRole('button',{name:'确认应用声明',exact:true}).click();
  await panel.getByRole('alert').waitFor();
  await panel.getByRole('button',{name:'检查资料声明',exact:true}).click();
  await panel.getByRole('button',{name:'确认应用声明',exact:true}).click();
  await panel.getByText('资料版本已变化，需要重新评估方向',{exact:true}).waitFor();
  await panel.screenshot({path:resolve(output,'revision-drift.png')});
  const reread=spawnSync(invoke.command,[...invoke.args,root,'updated'],{cwd:root,encoding:'utf8',timeout:60_000,
    env:{...process.env,LOOPX_USAGE_PING:'0',TMPDIR:root,TEMP:root,TMP:root,NODE_OPTIONS:`${process.env.NODE_OPTIONS??''} --experimental-sqlite`}});
  assert.equal(reread.status,0,reread.stdout+reread.stderr);
  await panel.getByRole('button',{name:'读取当前方向状态',exact:true}).click();
  await panel.getByText('已确认当前资料版本',{exact:true}).waitFor();
  await page.setViewportSize({width:390,height:1100});
  await panel.screenshot({path:resolve(output,'peer-readback.png')});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await panel.getByRole('button',{name:'停用方向投影',exact:true}).click();
  await panel.getByRole('button',{name:'确认应用声明',exact:true}).click();
  await panel.getByText('方向资料未启用',{exact:true}).waitFor();
  const state=await fetch(`http://127.0.0.1:${port}/api/chat/goal-direction?goal_id=amendment-goal&agent_id=agent-a`).then(r=>r.json());
  assert.equal(state.enabled,false);assert.equal(state.projection,null);assert.equal(state.execution_authority_granted,false);
  assert.equal(state.receipt_count,4096);assert.equal(state.acceptance_assessed,false);
  await writeFile(resolve(output,'result.json'),JSON.stringify({ok:true,boundary:'Real native source registration, SQLite, packaged operator UI and separate Agent CLI read/ack; no model calls',state},null,2));
  await page.close();page=await browser.newPage({viewport:{width:1512,height:1100}});
  await page.addInitScript(()=>localStorage.setItem('loopx-pw-locale','en'));
  await page.goto(`http://127.0.0.1:${port}/chat/`);
  await page.locator('.personal-goal-link',{hasText:'amendment-goal'}).click();
  await page.getByRole('button',{name:'Goal settings',exact:true}).click();
  await page.getByRole('button',{name:'Task ownership',exact:true}).click();
  await page.getByText('Direction materials and revisions',{exact:true}).click();
  await page.getByText('Direction materials are disabled',{exact:true}).waitFor();
  console.log('Direction browser passed: native declaration, unknown-response recovery, separate Agent read/ack, peer isolation, full-capacity failure/recovery and disable readback.');
}catch(error){if(page){await writeFile(resolve(output,'failure.txt'),await page.locator('body').innerText());await page.screenshot({path:resolve(output,'failure.png'),fullPage:true});}throw error;}
finally{await browser?.close();child.kill('SIGTERM');lines.close();await new Promise(accept=>child.exitCode!==null?accept():child.once('exit',accept));
  await writeFile(resolve(output,'server.log'),serverLog);await rm(root,{recursive:true,force:true});}
