import assert from "node:assert/strict";
import {test} from "node:test";
import {EffectRuntimeRequestError} from "../../loopx/control_plane/effect_runtime_errors.ts";
import {projectGlobalTodoReadiness} from "../../loopx/control_plane/todos/decision_scope.ts";

const scope = {kind: "write_scope", granularity: "action", scope_key: "sample"};
const item = {todo_id: "todo_sample", status: "deferred", resume_ready: true, required_decision_scopes: [scope]};
const gate = {todo_id: "todo_gate", unblocks_todo_id: "todo_sample"};
const request = {
  schema_version: "global_todo_readiness_request_v0",
  payload: {selected_todo: item, normal_delivery_allowed: true},
  gates: [{...gate, is_gate: true, target_text: gate.unblocks_todo_id}],
  decoded: {
    "/selected_todo": {todo_id: item.todo_id, required_decision_scopes: [scope],
      identity_text: item.todo_id, status_text: item.status},
  },
};

test("blocked beats deferred and selected; dependency evidence grants no execution authority", () => {
  const result = projectGlobalTodoReadiness(request);
  assert.deepEqual(result, {
    schema_version: "global_todo_readiness_v0",
    candidates: [{item, readiness: "blocked"}],
    warnings: [{reason_code: "conflicting_readiness_projection", todo_id: "todo_sample",
      detail: "Multiple structured readiness signals were projected."}],
  });
  const denied = projectGlobalTodoReadiness({...request,
    decoded: {...request.decoded, "/selected_todo": {...request.decoded["/selected_todo"], status_text: "open"}},
    gates: [],
    payload: {
    selected_todo: {...item, status: "open", resume_ready: false}, normal_delivery_allowed: false,
  }});
  assert.deepEqual(denied.candidates, []);
});

test("a scope covering a different exact target cannot block this candidate", () => {
  const otherGate = {...gate, unblocks_todo_id: "todo_other", decision_scope: scope};
  const result = projectGlobalTodoReadiness({...request,
    gates: [{...otherGate, is_gate: true, target_text: otherGate.unblocks_todo_id}],
  });
  assert.deepEqual(result.candidates, [{item, readiness: "deferred_ready"}]);
});

test("malformed protocol envelopes and codecs fail closed", () => {
  for (const value of [null, {}, {...request, schema_version: "wrong"},
    {...request, decoded: {}}, {...request, payload: []}]) {
    assert.throws(() => projectGlobalTodoReadiness(value), error =>
      error instanceof TypeError || error instanceof EffectRuntimeRequestError);
  }
});
