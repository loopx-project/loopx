import assert from "node:assert/strict";
import test from "node:test";
import {qualifyCliSourceActivation as decide} from "../../loopx/control_plane/runtime/cli_source_activation.ts";

const scope = {activation_id: "a".repeat(32), container_id: "b".repeat(64), started_at: "original-start",
  goal_id: "fixture-goal", agent_id: "fixture-agent", todo_id: "todo_fixture0001", turn_instance_id: "original-turn",
  session_id: "original-native-session", registry_path: "/control/registry.json", launcher: "/profile/bin/loopx", launcher_uid: 0, launcher_gid: 0, release_root: "/profile/releases",
  old_target: "/profile/releases/old/scripts/loopx", new_target: "/profile/releases/new/scripts/loopx",
  target_revision: "c".repeat(40), target_archive_sha256: "d".repeat(64), worker_lock: "/controller/worker.lock"};
const identity = {schema_version: "quota_settlement_identity_v0", goal_id: scope.goal_id, agent_id: scope.agent_id,
  todo_id: scope.todo_id, turn_instance_id: scope.turn_instance_id,
  effect_id: `${scope.goal_id}:${scope.agent_id}:${scope.todo_id}:${scope.turn_instance_id}`};
function fixture() {
  return {container_id: scope.container_id, started_at: scope.started_at, running: true, paused: true,
    owned_barrier: true, control: "continue", source_control: "continue", ready_present: false, launcher_target: scope.old_target,
    launcher_uid: 0, launcher_gid: 0, launcher_mode: 0o777,
    probe_status: "ready", unknown_process_ids: [] as number[],
    scheduler: {host_pending: false, host_attempt: null, last_wake_status: "wake_ok", last_wake_rc: 0},
    settlement: {identity: {result: {value: identity, failure: null}}, progress: {state: "settled"}},
    native: {execution_authority_granted: false, model_calls: 0, session_id: scope.session_id,
      turn_ids: ["native-original"], completed_turn_id: "native-original", visible_history_sha256: "e".repeat(64),
      quiescent_readback: true}, source_witness_before: "f".repeat(64), source_witness_after: "f".repeat(64)};
}
test("only an owned pause with the same settled identity and stable native idle source qualifies", () => {
  const result = decide({operation: "qualify", scope, observation: fixture()});
  assert.equal(result.qualified, true);
  assert.equal(result.source_change_allowed, true);
  assert.equal(result.resume_allowed, false);
  assert.equal(result.execution_authority_granted, false);
  assert.deepEqual(result.original_settlement_identity, {goal_id: scope.goal_id, agent_id: scope.agent_id,
    todo_id: scope.todo_id, turn_instance_id: scope.turn_instance_id, replan_obligation_id: null,
    binding_kind: "todo", binding_id: scope.todo_id, effect_id: identity.effect_id});
});
for (const [name, mutation] of [
  ["Stop", {control: "stop"}], ["unknown control", {control: "unknown"}],
  ["typed source Stop", {source_stopped: true}], ["unpaused source", {paused: false}],
  ["missing Goal-control proof", {source_control: undefined}], ["unknown Goal-control proof", {source_control: "unknown"}],
  ["unowned barrier", {owned_barrier: false}], ["other namespace", {started_at: "replacement-start"}],
  ["ready checkpoint", {ready_present: true}], ["unknown ready", {ready_present: null}],
  ["unknown processes", {unknown_process_ids: [19]}], ["failed probe", {probe_status: "unavailable"}],
  ["unfinished Host", {scheduler: {...fixture().scheduler, host_pending: true}}],
  ["unresolved attempt", {scheduler: {...fixture().scheduler, host_attempt: {status: "started"}}}],
  ["failed wake", {scheduler: {...fixture().scheduler, last_wake_rc: 1}}],
  ["unsettled Turn", {settlement: {...fixture().settlement, progress: {state: "spend_receipt_required"}}}],
  ["other Turn", {settlement: {identity: {result: {failure: null, value: {...identity, turn_instance_id: "another-turn",
    effect_id: `${scope.goal_id}:${scope.agent_id}:${scope.todo_id}:another-turn`}}}, progress: {state: "settled"}}}],
  ["native model request", {native: {...fixture().native, model_calls: 1}}],
  ["no native idle proof", {native: {...fixture().native, quiescent_readback: false}}],
  ["unfinished native cursor", {native: {...fixture().native, completed_turn_id: "old-cursor"}}],
  ["other session", {native: {...fixture().native, session_id: "another-session"}}],
  ["moving source", {source_witness_after: "a".repeat(64)}],
  ["launcher CAS mismatch", {launcher_target: scope.new_target}],
  ["launcher owner mismatch", {launcher_uid: 23}],
] as const) {
  test(`${name} cannot authorize a launcher write`, () => {
    const result = decide({operation: "qualify", scope, observation: {...fixture(), ...mutation}});
    assert.equal(result.source_change_allowed, false);
    assert.equal(result.resume_allowed, false);
  });
}
test("preflight permits only a candidate; the paused native and settlement checks remain mandatory", () => {
  const observed = {...fixture(), paused: false, owned_barrier: false};
  const result = decide({operation: "preflight", scope, observation: observed});
  assert.equal(result.pause_allowed, true);
  assert.equal(result.source_change_allowed, false);
  assert.equal(decide({operation: "qualify", scope, observation: observed}).qualified, false);
  assert.equal(decide({operation: "preflight", scope, observation: {...observed, ready_present: true}}).pause_allowed, false);
});
test("readback reconciles exactly old or qualified new; partial and unqualified new remain held", () => {
  for (const [target, qualified, allowed] of [[scope.old_target, false, true], [scope.new_target, true, true],
    [scope.new_target, false, false], [null, true, false], ["/profile/releases/partial", true, false]] as const) {
    const result = decide({operation: "readback", scope, observation: {...fixture(), launcher_target: target,
      qualified_source: qualified, unchanged_source_witness: true}});
    assert.equal(result.resume_allowed, allowed);
    assert.equal(result.source_change_allowed, false);
  }
});
test("explicit transport repair never overrides changed work or Stop", () => {
  const observed = {...fixture(), repair_target: "old", qualified_source: true, unchanged_source_witness: true};
  assert.equal(decide({operation: "repair", scope, observation: observed}).source_change_allowed, true);
  for (const changed of [{control: "stop"}, {unchanged_source_witness: false}, {qualified_source: false}]) {
    assert.equal(decide({operation: "repair", scope, observation: {...observed, ...changed}}).source_change_allowed, false);
  }
});
test("malformed or nonversioned scopes are rejected, not interpreted as permission", () => {
  for (const changed of [{new_target: "/profile/releases/not-a-launcher"}, {new_target: scope.old_target},
    {launcher: scope.old_target}, {target_archive_sha256: "invalid"}, {container_id: "short"}, {extra: true}]) {
    assert.throws(() => decide({operation: "qualify", scope: {...scope, ...changed}, observation: fixture()}));
  }
});
