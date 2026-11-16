import assert from "node:assert/strict";
import {mkdtemp, mkdir, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import {missingCloseoutReceipts} from "../../loopx/control_plane/quota/prior_turn_closeout.ts";
import {QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA,readQuotaSettlement} from "../../loopx/control_plane/quota/settlement_readback.ts";

const missing={result:{value:null,receipts:[],failure:{kind:"writeback_missing"}},payload:{ok:false}};
const base={found:true,settlement:missing,writeback:missing,spend:missing,monitor_phase:null,replay_phase:"open"};
const both=["durable_writeback_receipt","quota_spend_receipt"];

test("monitor phases cannot bypass the guard bound-Todo verification",()=>{
  assert.deepEqual(missingCloseoutReceipts({...base,monitor_phase:"settled",replay_phase:"settled"}),both);
});
for(const [name,patch] of [
  ["no monitor",{replay_phase:"settled"}],
  ["pending replay",{monitor_phase:"settled",replay_phase:"settlement_pending"}],
  ["pending monitor",{monitor_phase:"settlement_pending",replay_phase:"settled"}],
  ["truthy monitor",{monitor_phase:true,replay_phase:"settled"}],
  ["truthy replay",{monitor_phase:"settled",replay_phase:true}],
  ["missing identity",{found:false,monitor_phase:"settled",replay_phase:"settled"}],
] as const) test(`missing monitor proof remains owed: ${name}`,()=>{
  assert.deepEqual(missingCloseoutReceipts({...base,...patch}),both);
});
test("ordinary validated settlement retains its existing closeout",()=>{
  assert.equal(missingCloseoutReceipts({...base,settlement:{payload:{ok:true}}}),null);
});
test("malformed ordinary readback does not become an exemption",()=>{
  assert.throws(()=>missingCloseoutReceipts({...base,settlement:{}}),/no settlement result/);
});

const goal="monitor-owner-fixture",agent="owner",todo="todo_monitor",old="old-turn",current="current-turn";
const effect=`quota-monitor-poll:${goal}:${agent}:${old}`;
const variants:[string,Record<string,unknown>,boolean][]=[
  ["exact Todo commit",{},true],
  ["legacy exact Turn commit",{quota_monitor_poll_commit:{effect_id:effect}},true],
  ["missing commit",{quota_monitor_poll_commit:null},false],
  ["wrong effect",{quota_monitor_poll_commit:{effect_id:`${effect}:todo:todo_other`}},false],
  ["wrong agent",{agent_id:"foreign"},false],
  ["wrong Goal",{goal_id:"foreign"},false],
  ["wrong Todo",{todo_id:"todo_other"},false],
  ["wrong Turn",{turn_instance_id:"foreign"},false],
  ["uncommitted observation",{classification:"state_refreshed"},false],
];
for(const [name,patch,settled] of variants) test(`persisted owning monitor readback: ${name}`,async()=>{
  const root=await mkdtemp(join(tmpdir(),"loopx-neutral-prior-"));
  const dir=join(root,"goals",goal),index=join(dir,"runs/index.jsonl"),events=join(dir,"rollout-event-log.jsonl");
  try {
    await mkdir(join(dir,"runs"),{recursive:true});
    const rows=[{schema_version:"loopx_rollout_event_v0",event_id:"original-guard",event_kind:"quota_should_run",
      goal_id:goal,agent_id:agent,run_id:old,details:{todo_id:todo,closeout_required:true,
        settlement_effect_id:`${goal}:${agent}:${todo}:${old}`}},
      {schema_version:"loopx_rollout_event_v0",event_id:"current-guard",event_kind:"quota_should_run",
        goal_id:goal,agent_id:agent,run_id:current,details:{}}];
    const poll={classification:"quota_monitor_poll",material_change:false,delivery_outcome:"surface_only",
      goal_id:goal,agent_id:agent,todo_id:todo,turn_instance_id:old,
      quota_monitor_poll_commit:{effect_id:`${effect}:todo:${todo}`},...patch};
    await writeFile(events,rows.map(row=>JSON.stringify(row)).join("\n")+"\n");
    await writeFile(index,JSON.stringify(poll)+"\n");
    const before=await Promise.all([readFile(events),readFile(index)]);
    const request={schema_version:QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA,runtime_root:root,
      goal_id:goal,agent_id:agent,todo_id:todo,turn_instance_id:old,
      infer_turn_instance_id:false,allow_unbound_binding:false};
    const original=await readQuotaSettlement(request);
    assert.equal(original.monitor_phase,settled?"settled":"poll_due");
    assert.equal(original.replay_phase,settled?"settled":"open");
    assert.equal((original.spend as any).payload.ok,false,"A monitor never fabricates a debit receipt");
    assert.equal((original.writeback as any).payload.ok,false,"Observation is not an accountable work writeback");
    assert.deepEqual(missingCloseoutReceipts(original),both,"The guard retains its bound-fact checkpoint");
    const unbound=await readQuotaSettlement({...request,todo_id:"todo_product",turn_instance_id:current});
    const failure=(unbound.settlement as any).result.failure;
    assert.equal(failure.kind,"receipt_unbound");
    if(settled){
      assert.equal(failure.details.owed_prior_turn,undefined,"A committed no-spend monitor cannot block replan binding");
      assert.doesNotMatch(failure.reason,/earlier Turn owes/);
    }else{
      assert.equal(failure.details.owed_prior_turn.prior_turn_instance_id,old,"Unproved original work must remain owed");
    }
    const after=await Promise.all([readFile(events),readFile(index)]);
    assert.deepEqual(after,before,"Readback must not rewrite records,acknowledge or spend");
  }finally{await rm(root,{recursive:true,force:true});}
});
