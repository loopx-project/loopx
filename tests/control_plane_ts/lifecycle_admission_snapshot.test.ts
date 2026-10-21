import assert from "node:assert/strict";
import test from "node:test";
import {projectReplanSemantics} from "../../loopx/control_plane/work_items/replan_semantics.ts";

const admitted = [0,1,2].map(index => ({todo_id:`todo_${index}`,index,status:"done",done:true,
  task_class:"advancement_task",archive_state:"active",claimed_by:"agent-a",last_actor_agent_id:"agent-a",
  completed_at:`2026-09-01T00:00:0${2-index}Z`,updated_at:"2026-09-01T00:00:00Z",
  completion_turn_key:`original-${index}`,completion_continuation:"active_goal"}));
const current=admitted.map(item=>({...item,no_followup:true,completion_continuation:"no_followup",
  completion_recovery:"lifecycle_reentry_terminal_closeout"}));
const source={obligation_id:"replan-1111111111111111",resolution_mode:"todo_lifecycle_settlement",
  lifecycle_admission_items:admitted,triggers:admitted.map(item=>({kind:"completed_advancement_without_successor",
    agent_id:"agent-a",todo_id:item.todo_id,completion_turn_key:item.completion_turn_key}))};
function run(obligation=source,items=current){return projectReplanSemantics({operation:"lifecycle_obligation",
  obligation,selected_obligation_id:source.obligation_id,agent_id:"agent-a",guard_recorded_at:"2026-09-02T00:00:00Z",items});}
test("sealed original admission recovers closeout without rewriting old Todo timestamps",()=>{
  assert.equal((run().obligation as Record<string,unknown>).resolution_mode,"receipt_bound_replan_settlement");
});
for(const field of ["todo_id","claimed_by","last_actor_agent_id","completion_turn_key","completed_at","updated_at"]){
  test(`snapshot recovery rejects changed ${field}`,()=>{
    const items=structuredClone(current);(items[0] as Record<string,unknown>)[field]="changed";
    assert.throws(()=>run(source,items),/lifecycle source does not match/);
  });
}
for(const shape of ["closed_before_guard","unsettled","missing_source","duplicate_source","duplicate_current","wrong_trigger"]){
  test(`snapshot recovery rejects ${shape}`,()=>{
    const obligation=structuredClone(source),items=structuredClone(current);
    if(shape==="closed_before_guard")Object.assign(obligation.lifecycle_admission_items[0],{no_followup:true});
    if(shape==="unsettled")Object.assign(items[0],{no_followup:false});
    if(shape==="missing_source")obligation.lifecycle_admission_items.pop();
    if(shape==="duplicate_source")obligation.lifecycle_admission_items[1]=obligation.lifecycle_admission_items[0];
    if(shape==="duplicate_current")items.push(items[0]);
    if(shape==="wrong_trigger")obligation.triggers[0].completion_turn_key="wrong";
    assert.throws(()=>run(obligation,items),/lifecycle source does not match/);
  });
}

test("sealed admission retains a still-pending original lifecycle", () => {
  const result = run(source, structuredClone(admitted) as typeof current);
  assert.equal((result.obligation as Record<string, unknown>).resolution_mode, "todo_lifecycle_settlement");
  assert.equal((result.obligation as Record<string, unknown>).selection_binding, "heartbeat_receipt");
});


test("sealed admission accepts the lifecycle owner's closeout timestamp", () => {
  const items = structuredClone(current);
  items[0].updated_at = "2026-09-03T00:00:00Z";
  assert.equal((run(source, items).obligation as Record<string, unknown>).resolution_mode,
    "receipt_bound_replan_settlement");
  items[0].updated_at = "2026-09-01T12:00:00Z";
  assert.throws(() => run(source, items), /lifecycle source does not match/);
});
