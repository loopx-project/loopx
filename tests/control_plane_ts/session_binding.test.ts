import assert from "node:assert/strict";
import test from "node:test";
import {evaluateTurnEnvelope} from "../../loopx/control_plane/quota/turn_envelope.ts";
import {turnSessionPlan} from "../../loopx/control_plane/turn_driver/session_binding.ts";
import {TURN_ROUTES} from "../../loopx/control_plane/turn_driver/turn_contract_generated.ts";

const lineage = {goal_id: "goal", agent_id: "agent", todo_id: "todo", action_hash: "sha256:original"};
const request = {operation: "session_plan", route: "ready_for_host", lineage,
  session_scope: "todo", iteration_context_policy: "resume", session_binding: {}, binding_identity_text: {}};

test("resume keeps omission distinct from fresh not-found and rejects schema before identity", () => {
  const empty = evaluateTurnEnvelope(request);
  assert.deepEqual(empty, {session: {schema_version: "loopx_turn_session_binding_v0", action: "start_new",
    context_policy: {schema_version: "loopx_iteration_context_policy_v0", mode: "resume", scope: "iteration"}}, error: null});
  assert.deepEqual(evaluateTurnEnvelope({...request, session_binding: {schema_version: "future"},
    binding_identity_text: null}), {session: {schema_version: "loopx_turn_session_binding_v0", action: "reject",
      binding_status: "unsupported_schema"}, error: "unsupported LoopX Turn session binding schema"});
});

for (const route of TURN_ROUTES) test(`session policy respects the ${route} route`, () => {
  const result = evaluateTurnEnvelope({...request, route, lineage: {}});
  if (["ready_for_host", "repair_required", "replan_required"].includes(route)) {
    assert.equal((result.session as {binding_status: string}).binding_status, "missing_turn_lineage");
    assert.equal(result.error, "host-bound routes require goal, agent, todo, and action-hash lineage");
  } else {
    assert.equal((result.session as {action: string}).action, "none");
    assert.equal(result.error, null);
  }
});

test("a fresh request binds the Agent even at todo scope; resume still requires a work binding", () => {
  const scoped = {...request, lineage: {...lineage, todo_id: "", request_binding_id: "original-request"},
    iteration_context_policy: "fresh", session_binding: {schema_version: "future"}};
  assert.equal((evaluateTurnEnvelope(scoped).session as {action: string}).action, "start_new");
  assert.equal((evaluateTurnEnvelope({...scoped, iteration_context_policy: "resume"}).session as
    {binding_status: string}).binding_status, "missing_turn_lineage");
  const agent = {...request, session_scope: "agent", lineage: {...lineage, todo_id: "", replan_obligation_id: "replan"}};
  assert.equal((evaluateTurnEnvelope(agent).session as {action: string}).action, "start_new");
  assert.equal((evaluateTurnEnvelope({...agent, lineage: {...agent.lineage, replan_obligation_id: ""}}).session as
    {binding_status: string}).binding_status, "missing_turn_lineage");
});

test("reuse compares only conversation identity and never normalizes encoded historical text", () => {
  const binding = {schema_version: "loopx_turn_session_binding_v0"};
  const result = turnSessionPlan({...request, session_binding: binding, session_scope: "agent",
    binding_identity_text: {...lineage, todo_id: "old-work", action_hash: "old-action"}});
  assert.equal(result.session.action, "resume");
  if (false) {
    // @ts-expect-error A refused session cannot carry the successful context policy.
    const illegal: typeof result.session = {schema_version: "loopx_turn_session_binding_v0", action: "reject", binding_status: "identity_mismatch", context_policy: {}};
    void illegal;
  }
  const historical = {...request, session_binding: binding,
    lineage: {...lineage, todo_id: "1.0"}, binding_identity_text: {...lineage, todo_id: "1.0"}};
  assert.equal(turnSessionPlan(historical).session.action, "resume");
  assert.equal(turnSessionPlan({...historical, binding_identity_text: {...lineage, todo_id: "1"}}).session.action, "reject");
});
