import test from "node:test";
import assert from "node:assert/strict";
import {delegationInventoryItem, delegationInventoryQuery, delegationPreflight, selectDelegationBinding, transitionDelegationObservation} from "../../loopx/control_plane/collaboration/delegation.ts";

const binding = {id: "review", agent_id: "reviewer", todo_id: "todo_review", workspace: "/fixture",
  requesters: ["coordinator", "analyst"], host_args: ["--host", "dsh"], timeout_seconds: 60, output_refs: ["output.json"]};
const params = {agent_id: "coordinator", binding_id: "review",
  config: {schema_version: "loopx_local_delegation_v0", bindings: [binding]}};

test("same explicit grant contract applies to a coordinator and an ordinary member", () => {
  assert.deepEqual(selectDelegationBinding(params), binding);
  assert.deepEqual(selectDelegationBinding({...params, agent_id: "analyst"}), binding);
  assert.throws(() => selectDelegationBinding({...params, agent_id: "unbound"}), /no delegation grant/);
  assert.throws(() => selectDelegationBinding({...params, agent_id: "reviewer"}), /no delegation grant/);
  assert.throws(() => selectDelegationBinding({...params, binding_id: "other"}), /unavailable/);
});

test("malformed operator binding fails before launch", () => {
  for (const patch of [{timeout_seconds: 0}, {timeout_seconds: 5000}, {output_refs: ["../secret"]},
    {output_refs: ["/secret"]}, {host_args: []}, {todo_id: null}]) {
    assert.throws(() => selectDelegationBinding({...params,
      config: {...params.config, bindings: [{...binding, ...patch}]}}));
  }
});

test("message receipt and model return do not imply accepted work", () => {
  assert.throws(() => transitionDelegationObservation({from: "prepared", to: "accepted"}), /transition/);
  assert.throws(() => transitionDelegationObservation({from: "turn_returned", to: "accepted"}), /canonical/);
  assert.throws(() => transitionDelegationObservation({from: "rejected", to: "running"}), /transition/);
  assert.deepEqual(transitionDelegationObservation({from: "turn_returned", to: "accepted",
    canonical_done: true, acceptance_ready: true, artifacts_current: true}), {status: "accepted"});
});

test("inventory paging is bounded and never interprets a missing result as accepted", () => {
  assert.deepEqual(delegationInventoryQuery({}), {limit: 20, cursor: null});
  for (const limit of [0, 51, true, "2"]) assert.throws(() => delegationInventoryQuery({limit}));
  assert.throws(() => delegationInventoryQuery({cursor: "../other"}));
  const record = {record_id: "a".repeat(64), operation_id: "review-1"};
  const observation = {operation_id: "review-1", request_id: "request", agent_id: "reviewer",
    todo_id: "todo_review", status: "accepted", worker_active: false, recovery_required: false,
    artifacts: [{ref: "output.json", sha256: "b".repeat(64), text: "private body"}]};
  const accepted = delegationInventoryItem({record, observation});
  assert.equal(accepted.status, "accepted");
  assert.equal(JSON.stringify(accepted).includes("private body"), false);
  assert.throws(() => delegationInventoryItem({record, observation: {...observation, artifacts: []}}));
  assert.throws(() => delegationInventoryItem({record, observation: {...observation, status: "done"}}));
  assert.throws(() => delegationInventoryItem({record, observation: {...observation, operation_id: "other"}}));
  const unavailable = delegationInventoryItem({record, observation: null});
  assert.equal(unavailable.status, "unavailable");
  assert.equal(unavailable.recovery_required, null);
  assert.equal(unavailable.artifacts, undefined);
});

test("preflight separates task admission, acceptance binding and runtime availability", () => {
  const effects = {host_invoked: false, state_written: false, quota_spent: false, scheduler_acknowledged: false};
  const preview = {dry_run: true, status: "preview", effects,
    route: {kind: "ready_for_host", would_invoke_host: true, selected_todo_id: "todo_review"},
    managed_executor: {executor: "dsh", available: true, unavailable_reason: null, execution_profile: "explicit-profile"}};
  const params = {binding, preview, validation_files_current: true, acceptance: {todo_id: "todo_review", state: "ready"}};
  assert.equal(delegationPreflight(params).state, "launchable");
  for (const [available, expected] of [[null, "runtime_unverified"], [false, "runtime_unavailable"]] as const) {
    assert.equal(delegationPreflight({...params, preview: {...preview,
      managed_executor: {...preview.managed_executor, available}}}).state, expected);
  }
  assert.equal(delegationPreflight({...params, acceptance: null}).state, "acceptance_unavailable");
  assert.equal(delegationPreflight({...params, acceptance: {...params.acceptance, state: "stale"}}).state, "acceptance_unavailable");
  assert.equal(delegationPreflight({...params, preview: {...preview, route: {...preview.route,
    selected_todo_id: "other"}}}).state, "turn_blocked");
  assert.equal(delegationPreflight({...params, preview: {...preview, route: {...preview.route,
    would_invoke_host: false}}}).state, "turn_blocked");
  assert.throws(() => delegationPreflight({...params, preview: {...preview, effects: {...effects, host_invoked: true}}}));
});
