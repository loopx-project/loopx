import assert from "node:assert/strict";
import test from "node:test";
import { createHash } from "node:crypto";
import { ZCodeAppServer, ZCodeProtocolError } from "../../loopx/zcode_goal_mode/app-server.ts";

// An independent native host fixture, with strict CAS and a self-running Goal.
const HOST = String.raw`
const readline = require('node:readline');
const mode = process.argv[1]; let target = null, revision = 0, running = false, selectedModel=null, durable=false;
const snapshot = () => ({protocol:{name:'ZCode Protocol',version:1},session:{sessionId:'sess_fixture'},settings:{model:{current:selectedModel,available:[{ref:{providerId:'synthetic',modelId:'synthetic'},label:'Synthetic',reasoning:{levels:[{value:'disabled',label:'disabled'}],defaultLevel:'disabled'}}]}},projection:{sessionId:'unknown',status:running?'running':'idle',target:target&&{sessionId:'sess_fixture',targetId:target.id,objective:target.objective||'Synthetic goal',status:target.status}},runtime:{stateRevision:revision}});
const send = value => process.stdout.write(JSON.stringify(value)+'\n');
readline.createInterface({input:process.stdin}).on('line', line => {
  const r=JSON.parse(line);
  if (r.result || r.error) { if (r.id==='permission') {if(r.result?.decision!=='deny')process.exit(8);send({method:'fixture/permissionDenied'});} if(r.id==='unknown'){if(r.error?.code!==-32601)process.exit(9);send({method:'fixture/unknownDenied'});} return; }
  if (mode==='timeout') return;
  if (mode==='garbage') {process.stdout.write('private-key-secret\n');return;}
  if (mode==='exit') {process.stderr.write('private-key-secret');process.exit(5);}
  if (mode==='limit') {process.stdout.write('x'.repeat(4*1024*1024+1));return;}
  if (r.method==='runtime/capabilities') {
    if(mode==='reverse') {send({id:'permission',method:'interaction/requestPermission',params:{sessionId:'sess_fixture'}});send({id:'unknown',method:'interaction/requestProviderRuntimeHeaders',params:{secret:'private'}});send({id:r.id,result:{independentPlanState:true}});return;}
    if(mode==='split') { const wire=JSON.stringify({id:r.id,result:{independentPlanState:true}})+'\n';process.stdout.write(wire.slice(0,13));setTimeout(()=>process.stdout.write(wire.slice(13)),8);return; }
    if(mode==='reject') {send({id:r.id,error:{code:-32009,message:'private-key-secret',data:{credential:'secret'}}});return;}
    send({id:r.id,result:{independentPlanState:true}});return;
  }
  if(r.method==='session/create') {
    if(r.params.titleGenerationEnabled!==false||r.params.mode!=='build'||r.params.sessionId) throw Error('unsafe create');
    const s=snapshot();if(mode==='wrong-protocol') s.protocol.version=9;send({id:r.id,result:s});return;
  }
  if(r.method==='session/resume') {target={id:'goal-existing',status:'paused'};send({id:r.id,result:snapshot()});return;}
  if(r.method==='session/read') {
    if(r.params.messageLimit!==1) throw Error('invalid message limit');
    if(mode==='replace-target'&&target) target.id='goal-foreign';
    if(mode==='budget-limited'&&target){target.status='budget_limited';running=false;}const s=snapshot();if(mode==='stale-cas'&&durable)revision++;if(mode==='wrong-session') s.session.sessionId='foreign';if(mode==='wrong-target'&&target)s.projection.target.sessionId='foreign';if(mode==='unknown-status'&&target)s.projection.target.status='future';send({id:r.id,result:s});return;
  }
  if(r.method==='session/setModel'){if(r.params.expectedRevision!==revision)throw Error('missing CAS');selectedModel=r.params.model;revision++;send({id:r.id,result:snapshot()});return;}
  if(r.method==='session/goal') {
    if(r.params.expectedRevision!==revision){send({id:r.id,error:{code:-32009,message:'CAS rejected'}});return;}
    if(r.params.action==='set'){if(mode==='require-durable'&&!durable)throw Error('unpersisted session');target={id:'goal-fixture',objective:r.params.objective.trim(),status:'active'};running=true;}
    if(r.params.action==='resume'){target.status='active';running=true;}
    if(r.params.action==='pause'){if(!target&&mode==='empty-pause-rejected'){send({id:r.id,error:{code:-32004,message:'persistence rejected'}});return;}if(!target&&mode==='require-durable')send({method:'fixture/sessionDurable'});durable=true;if(target)target.status='paused';running=false;}
    if(r.params.action==='clear'){if(running||target?.status==='active')throw Error('clear before pause');target=null;}
    revision++; const startedTurn = r.params.action==='pause'&&mode==='empty-pause-started' ? true : r.params.action==='pause'&&mode==='empty-pause-missing' ? undefined : r.params.action==='set'||r.params.action==='resume'; send({id:r.id,result:{response:'',snapshot:snapshot(),startedTurn}});return;
  }
  send({id:r.id,error:{code:-32601,message:'unsupported'}});
});
`;
function host(mode = "normal", options: ConstructorParameters<typeof ZCodeAppServer>[3] = {}) {
  return new ZCodeAppServer([process.execPath, "-e", HOST, mode], process.cwd(), { ...process.env }, { timeoutMs: 1000, ...options });
}
function safeError(code: string) {
  return (error: unknown) => error instanceof ZCodeProtocolError && error.code === code && !String(error).includes("private-key-secret");
}

test("native lifecycle retains one server and returns while its Goal runs", async (t) => {
  const server=host();t.after(()=>server.close());
  assert.equal((await server.initialize()).protocol,"zcode-ndjson-session-goal");
  const created=await server.create({providerId:"synthetic",modelId:"synthetic"});
  assert.equal(created.target_id,null);assert.equal(created.running,false);
  const started=await server.setGoal(created.session_id,"Synthetic goal");
  assert.equal(started.started_turn,true);assert.equal(started.running,true);assert.equal(started.target_id,"goal-fixture");
  const paused=await server.pauseGoal(created.session_id);
  assert.equal(paused.status,"paused");assert.equal(paused.running,false);assert.equal(paused.target_id,started.target_id);
  assert.equal((await server.resumeGoal(created.session_id)).started_turn,true);
  assert.equal((await server.clearGoal(created.session_id)).target_id,null);
});
test("binding confirms an empty native session is durable without starting a Goal", async (t) => {
  const events: string[] = [];
  const server = host("require-durable", {onEvent: event => events.push(event.method)});
  t.after(() => server.close());
  const bound = await server.create();
  assert.equal(bound.target_id, null);
  assert.equal(bound.running, false);
  assert.equal(bound.selected_model, null);
  assert.deepEqual(events, ["fixture/sessionDurable"]);
});
test("an unconfirmed empty persistence boundary cannot be reported as bound", async (t) => {
  const server = host("empty-pause-rejected");
  t.after(() => server.close());
  await assert.rejects(server.create(), (error: unknown) => safeError("request_rejected")(error)
    && (error as ZCodeProtocolError).protocolCode === -32004);
});
test("metadata-only pause requires explicit confirmation that execution did not start", async (t) => {
  for (const mode of ["empty-pause-started", "empty-pause-missing"]) {
    const server = host(mode);
    t.after(() => server.close());
    await assert.rejects(server.create(), safeError("unexpected_execution"));
  }
});
test("resumeSession binds existing Goal without starting it",async(t)=>{
  const server=host();t.after(()=>server.close());
  const resumed=await server.resumeSession("sess_fixture");assert.equal(resumed.target_id,"goal-existing");assert.equal(resumed.status,"paused");
  assert.equal((await server.resumeGoal(resumed.session_id)).running,true);
});
test("NDJSON fragmented frame is assembled and concurrent reads are correlated",async(t)=>{
  const server=host("split");t.after(()=>server.close());await server.initialize();await server.create();
  const result=await Promise.all([server.readGoal("sess_fixture"),server.readGoal("sess_fixture")]);assert.equal(result.length,2);
});
test("unbound and foreign Goal identity never receives mutation",async(t)=>{
  const server=host("replace-target");t.after(()=>server.close());await server.create();await server.setGoal("sess_fixture","Synthetic");
  await assert.rejects(server.pauseGoal("sess_fixture"),safeError("goal_identity_changed"));
});
test("protocol and session identities are checked before accepting readback",async(t)=>{
  const protocol=host("wrong-protocol");const session=host("wrong-session");t.after(()=>Promise.all([protocol.close(),session.close()]));
  await assert.rejects(protocol.create(),safeError("unsupported_protocol"));await assert.rejects(session.create(),safeError("session_identity_mismatch"));
});
test("target identity and new status vocabulary fail closed",async(t)=>{
  for(const mode of ["wrong-target","unknown-status"]){const server=host(mode);t.after(()=>server.close());await server.create();await server.setGoal("sess_fixture","Synthetic");await assert.rejects(server.readGoal("sess_fixture"),safeError(mode==='wrong-target'?"target_identity_mismatch":"unsupported_goal_status"));}
});
test("upstream error messages and stderr remain private",async(t)=>{
  const rejected=host("reject"),exited=host("exit");t.after(()=>Promise.all([rejected.close(),exited.close()]));
  await assert.rejects(rejected.initialize(),(error: unknown)=>safeError("request_rejected")(error)&&(error as ZCodeProtocolError).protocolCode===-32009);
  await assert.rejects(exited.initialize(),safeError("process_exited"));
});
test("malformed and oversized frames terminate the owned host",async(t)=>{
  for(const [mode,code] of [["garbage","invalid_frame"],["limit","frame_limit"]]){const server=host(mode);t.after(()=>server.close());await assert.rejects(server.initialize(),safeError(code));await assert.rejects(server.create(),safeError(code));}
});
test("request timeout and close reject pending calls within bounded time",async(t)=>{
  const timed=host("timeout",{timeoutMs:80});t.after(()=>timed.close());await assert.rejects(timed.initialize(),safeError("request_timeout"));
  const closed=host("timeout");const request=closed.initialize();const rejected=assert.rejects(request,safeError("closed"));await closed.close();await rejected;
});
test("reverse permissions are denied and unknown host interactions are rejected",async(t)=>{
  const events: string[]=[];
  const server=host("reverse",{onEvent:event=>events.push(event.method)});t.after(()=>server.close());
  await server.initialize();await server.create();await new Promise(resolve=>setTimeout(resolve,30));
  assert.deepEqual(events.sort(),["fixture/permissionDenied","fixture/unknownDenied"].sort());
});


test("model catalogue requires explicit available selection and preserves its reasoning",async(t)=>{
 const server=host();t.after(()=>server.close());const created=await server.create();
 assert.equal(created.selected_model,null);assert.equal(created.available_models[0].selection.providerId,"synthetic");
 await assert.rejects(server.selectModel(created.session_id,{providerId:"foreign",modelId:"synthetic",options:{reasoningLevel:"disabled"}}),safeError("model_unavailable"));
 await assert.rejects(server.selectModel(created.session_id,{providerId:"synthetic",modelId:"synthetic"}),safeError("reasoning_unavailable"));
 const selection={providerId:"synthetic",modelId:"synthetic",options:{reasoningLevel:"disabled"}};
 assert.deepEqual((await server.selectModel(created.session_id,selection)).selected_model,selection);
 await server.setGoal(created.session_id,"Synthetic");await assert.rejects(server.selectModel(created.session_id,selection),safeError("model_change_requires_pause"));
});

test("stale native state revision rejects mutation without starting a Goal",async(t)=>{
 const server=host("stale-cas");t.after(()=>server.close());const created=await server.create();
 await assert.rejects(server.setGoal(created.session_id,"Synthetic"),(error:unknown)=>safeError("request_rejected")(error)&&(error as ZCodeProtocolError).protocolCode===-32009);
 assert.equal((await server.readGoal(created.session_id)).target_id,null);
});
test("budget-limited native status remains distinct and objective readback exposes only its hash",async(t)=>{
 const server=host("budget-limited");t.after(()=>server.close());const created=await server.create();const objective="Private synthetic objective";
 const receipt=await server.setGoal(created.session_id,`  ${objective}  `);
 assert.equal(receipt.objective_sha256,createHash("sha256").update(objective).digest("hex"));assert.equal(JSON.stringify(receipt).includes(objective),false);
 const read=await server.readGoal(created.session_id);assert.equal(read.status,"budget_limited");assert.equal(read.raw_status,"budget_limited");assert.equal(read.running,false);
});
