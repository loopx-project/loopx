import test from "node:test";
import assert from "node:assert/strict";
import {projectTurnSelectionRejection} from "../../loopx/control_plane/turn_driver/selection_rejection.ts";

const qualification = {schema_version:"action_selection_qualification_v0", state:"deferred",
  requested_todo_id:"todo_worker", reason:"control_repair", recovery_action:"reenter_guard_without_selection",
  delivery_preemptions:["control_repair","delivery_not_allowed"]};
const params = {requested_todo_id:"todo_worker", contract_error_count:3,
  decision:{status_health_ok:false, action_selection_qualification:qualification}};

test("rejection reflects the quota reason and health count without another eligibility decision", () => {
  const before = structuredClone(params);
  const result = projectTurnSelectionRejection(params);
  assert.equal(result.error_code,"turn_todo_selection_deferred");
  assert.deepEqual(result.selection_rejection, {schema_version:"loopx_turn_selection_rejection_v0",
    source:"quota.should-run",requested_todo_id:"todo_worker",state:"deferred",reason_code:"control_repair",
    delivery_preemptions:["control_repair","delivery_not_allowed"],recovery_action:"reenter_guard_without_selection",
    status_health_ok:false,contract_error_count:3});
  assert.deepEqual(params,before);
});

test("absent or different selection stays unavailable, never borrowed or launchable", () => {
  for (const raw of [undefined,{}, {...qualification,requested_todo_id:"other"}, {...qualification,state:"qualified"}]) {
    const result = projectTurnSelectionRejection({...params,decision:{action_selection_qualification:raw}});
    assert.equal(result.error_code,"turn_todo_selection_unavailable");
    assert.equal((result.selection_rejection as Record<string,unknown>).reason_code,null);
  }
});

test("raw messages, paths and unsupported counts are not disclosed by the bounded projection", () => {
  const result = projectTurnSelectionRejection({...params,contract_error_count:-1,decision:{...params.decision,
    private_context:"not exported",action_selection_qualification:{...qualification,reason:"raw message",
      delivery_preemptions:["control_repair","/operator/path"],recovery_action:"raw recovery"}}});
  const refusal = result.selection_rejection as Record<string,unknown>;
  assert.equal(refusal.reason_code,null); assert.equal(refusal.recovery_action,null);
  assert.equal(refusal.contract_error_count,null); assert.deepEqual(refusal.delivery_preemptions,["control_repair"]);
  assert.equal(JSON.stringify(result).includes("not exported"),false);
});
