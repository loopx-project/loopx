import assert from "node:assert/strict";
import test from "node:test";
import {planSettledTurnJournal} from "../../loopx/control_plane/turn_driver/turn_journal_effects.ts";
const phases=['host_execute','typed_result','validation','durable_writeback','quota_spend','scheduler_apply','scheduler_ack'];
const identity={schema_version:'quota_settlement_identity_v0',goal_id:'goal',agent_id:'actor',todo_id:'todo_task',turn_instance_id:'turn-7',effect_id:'goal:actor:todo_task:turn-7'};
const key='sha256:'+'a'.repeat(64);
const receipt=(step:string)=>({schema_version:'quota_settlement_receipt_v1',step_kind:step,status:'committed',effect_id:identity.effect_id,source_ref:'native:'+step});
function fixture(){
 const journal={schema_version:'loopx_turn_journal_v0',goal_id:'goal',turn_key:key,status:'failed',completed_phases:phases.slice(0,3),
  plan:{host:{scheduler_owner:'outer_controller'},turn_envelope:{goal_id:'goal',agent_id:'actor',action:{selected_todo:{todo_id:'todo_task'}}},transaction:{turn_key:key,turn_instance_id:'turn-7',settlement_plan:{schema_version:'quota_settlement_plan_v1',identity}}},
  host_result:{schema_version:'loopx_turn_result_v0',turn_key:key,result_kind:'validated_completion',summary:'Original candidate'},task_validation:{ok:true},completion_intent:{todo_id:'todo_task',continuation:'active_goal'},
  receipt:{ok:true,turn_key:key,status:'failed',failed_phase:'durable_writeback',next_phase:'durable_writeback',result_kind:'writeback_failed'},settlement_result:{ok:false},reason:'original failure'};
 const bundle=(value:unknown,steps:string[])=>({result:{value,receipts:steps.map(receipt),failure:null},payload:{ok:true,receipts:steps.map(receipt),failure:null}});
 const readback={found:true,identity:bundle(identity,['validation']),settlement:bundle({},['validation','durable_writeback','quota_spend']),
  terminal_settlement:bundle({},['validation','durable_writeback','quota_spend','terminal_closeout']),
  writeback_run:{settlement_identity:identity,marker:'original-writeback'},spend_run:{settlement_identity:identity,slots:1,marker:'original-spend'}};
 return {journal,readback};
}
test('ordered receipt reconciliation retains original result and does no work replay',()=>{
 const {journal,readback}=fixture();const before=structuredClone(journal);const steps=planSettledTurnJournal(journal,readback);
 assert.deepEqual(journal,before);assert.deepEqual(steps.map(s=>[s.status,s.completed_phases.length]),[['in_progress',3],['in_progress',4],['in_progress',5],['committed',7]]);
 const final=steps.at(-1)!;assert.equal(final.turn_key,key);assert.equal(final.receipt.status,'committed');assert.equal(final.receipt.settlement_effect_id,identity.effect_id);
 assert.equal(final.receipt.failed_phase,null);assert.equal(final.receipt.next_phase,null);
 for(const k of ['plan','host_result','task_validation','completion_intent'])assert.deepEqual(final[k],journal[k]);
 assert.deepEqual(final.settlement_reconciliation.previous_receipt,journal.receipt);assert.deepEqual(final.settlement_reconciliation.previous_failure,journal.settlement_result);
 assert.deepEqual(planSettledTurnJournal(final,readback),[]);
});
for(const [name,mutate] of [
 ['non-outer scheduler',(j:any,r:any)=>j.plan.host.scheduler_owner='local_scheduler'],['missing readback',(j:any,r:any)=>r.found=false],['foreign effect',(j:any,r:any)=>r.identity.result.value={...identity,effect_id:'other'}],
 ['foreign structured agent',(j:any,r:any)=>r.identity.result.value={...identity,agent_id:'other'}],['missing spend',(j:any,r:any)=>r.settlement.payload.receipts.pop()],
 ['wrong spend receipt',(j:any,r:any)=>r.settlement.payload.receipts[2].effect_id='other'],['failed settlement',(j:any,r:any)=>r.settlement.payload.ok=false],
 ['missing provider source',(j:any,r:any)=>r.settlement.payload.receipts[1].source_ref=null],['no validation',(j:any,r:any)=>j.task_validation.ok=false],
 ['unvalidated prefix',(j:any,r:any)=>j.completed_phases=phases.slice(0,2)],['wrong result',(j:any,r:any)=>j.host_result.result_kind='host_failure'],
 ['prepared unknown effects',(j:any,r:any)=>j.effect_attempts={durable_writeback:{state:'unknown'}}],['active source hold',(j:any,r:any)=>j.source_effect_hold={}],
 ['foreign original key',(j:any,r:any)=>j.turn_key='sha256:'+'b'.repeat(64)],['malformed original snapshot',(j:any,r:any)=>j.schema_version='unknown'],
 ['stopped journal',(j:any,r:any)=>j.status='stopped'],['missing writeback run',(j:any,r:any)=>r.writeback_run=null],
 ['missing spend run',(j:any,r:any)=>r.spend_run=null],['missing terminal receipt',(j:any,r:any)=>{j.completion_intent.continuation='no_followup';r.terminal_settlement.payload.receipts.pop();}],
 ] as const){test('refuses '+name+' without input mutation',()=>{const {journal,readback}=fixture();mutate(journal,readback);const before=structuredClone(journal);assert.throws(()=>planSettledTurnJournal(journal,readback));assert.deepEqual(journal,before);});}
test('partial durable phase resumes only the remaining journal checkpoints',()=>{const {journal,readback}=fixture();const steps=planSettledTurnJournal(journal,readback);const afterWriteback=steps[1];assert.deepEqual(planSettledTurnJournal(afterWriteback,readback).map(s=>s.completed_phases.length),[5,7]);});
test('terminal reconciliation requires native terminal receipt',()=>{const {journal,readback}=fixture();journal.completion_intent.continuation='no_followup';const final=planSettledTurnJournal(journal,readback).at(-1)!;assert.equal(final.status,'committed');assert.ok(final.terminal_closeout);});
