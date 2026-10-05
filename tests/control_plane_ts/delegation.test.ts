import test from "node:test";
import assert from "node:assert/strict";
import {recordDelegationAdoption, decideDelegationStop, decideDelegationWakeObservation, delegationCheckedArtifacts, delegationInventoryItem, delegationInventoryQuery, delegationPreflight, delegationTurnPlanDecision, delegationValidationPlan, recoverValidatedDelegationSettlement, selectDelegationBinding, transitionDelegationObservation} from "../../loopx/control_plane/collaboration/delegation.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {projectTurnSelectionRejection} from "../../loopx/control_plane/turn_driver/selection_rejection.ts";

const binding = {id: "review", agent_id: "reviewer", todo_id: "todo_review", workspace: "/fixture",
  requesters: ["coordinator", "analyst"], host_args: ["--host", "dsh"], timeout_seconds: 60, output_refs: ["output.json"]};
const params = {agent_id: "coordinator", binding_id: "review",
  config: {schema_version: "loopx_local_delegation_v0", bindings: [binding]}};

test("wake observation preserves conversation and current Goal-reference scope", () => {
  const goalRef = {goal_id: "goal", goal_instance_id: "original-instance"};
  const intent = {requester: {goal_id: "goal", agent_id: "lead", goal_ref: goalRef},
    conversation: {session_id: "origin", turn_id: "original-turn"}};
  const observer = {goal_id: "goal", agent_id: "lead", goal_ref: goalRef, session_id: "origin"};
  assert.equal(decideDelegationWakeObservation({intent, observer}).observed, true);
  for (const delta of [{session_id: "other"}, {goal_id: "other"}, {agent_id: "other"},
    {goal_ref: {...goalRef, goal_instance_id: "replacement-instance"}}]) {
    assert.equal(decideDelegationWakeObservation({intent, observer: {...observer, ...delta}}).observed, false);
  }
});

const declaration = {validation_command: null, validation_command_argv: ["node", "validate.ts"],
  validation_label: "Independent verification", validation_timeout_seconds: 5};
const validationTodo = {todo_id: binding.todo_id, done: false, status: "open",
  completion_validation_required: true, completion_validation_sha256: canonicalAuthoritySha256(declaration)};
const validationBasis = {status: "loaded", provider_revision: "fixture:1", todo: validationTodo,
  completion_requirements: null};

test("preflight preserves a quota refusal, but rejects effectful or retargeted errors", () => {
  const projected = projectTurnSelectionRejection({requested_todo_id:binding.todo_id,contract_error_count:3,
    decision:{status_health_ok:false,action_selection_qualification:{state:"deferred",
      requested_todo_id:binding.todo_id,reason:"control_repair",recovery_action:"reenter_guard_without_selection"}}});
  const preview = {ok:false,...projected,effects_scope:"current_invocation",
    effects:{host_invoked:false,state_written:false,quota_spent:false,scheduler_acknowledged:false}};
  const input = {binding,preview,acceptance:{todo_id:binding.todo_id,state:"ready"},validation_files_current:true};
  const observed = delegationPreflight(input);
  assert.equal(observed.state,"turn_blocked"); assert.equal(observed.turn_eligible,false);
  assert.equal(observed.acceptance_ready,true); assert.equal(observed.executor,null);
  assert.deepEqual(observed.turn_blocker,projected.selection_rejection);
  assert.deepEqual(delegationPreflight({...input,preview:{...preview,selection_rejection:{
    ...(projected.selection_rejection as Record<string,unknown>),private_context:"not exported"}}}).turn_blocker,projected.selection_rejection);
  for (const effects of [{...preview.effects,host_invoked:true}, {...preview.effects,state_written:null}])
    assert.throws(() => delegationPreflight({...input,preview:{...preview,effects}}),/effect-free/);
  assert.throws(() => delegationPreflight({...input,preview:{...preview,
    selection_rejection:{...(projected.selection_rejection as Record<string,unknown>),requested_todo_id:"other"}}}),/matching/);
  for (const raw of [["deferred"],["rejected"],["unavailable"]])
    assert.throws(() => delegationPreflight({...input,preview:{...preview,
      selection_rejection:{...(projected.selection_rejection as Record<string,unknown>),state:raw},
      error_code:`turn_todo_selection_${raw[0]}`}}),/matching/);
});

test("independent delegation requires the current canonical declaration, not a Goal-wide contract", () => {
  const plan = delegationValidationPlan({binding, basis: validationBasis, declaration});
  assert.equal(plan.state, "ready");
  assert.equal(plan.source, "todo_validation");
  assert.equal(plan.canonical_done, false);
  assert.deepEqual((plan.effects as Record<string, unknown>[])[0].validation_argv, ["node", "validate.ts"]);
  const completed = delegationValidationPlan({binding, declaration, basis: {...validationBasis,
    todo: {...validationTodo, status: "done", done: true}}});
  assert.equal(completed.state, "ready");
  assert.equal(completed.canonical_done, true);
  for (const value of [null, {...declaration, validation_command_argv: ["node", "other.ts"]},
    {...declaration, validation_command_argv: []}]) {
    assert.equal(delegationValidationPlan({binding, basis: validationBasis, declaration: value}).state, "unbound");
  }
  const undeclared = {...validationBasis, todo: {todo_id: binding.todo_id, status: "open", done: false}};
  assert.equal(delegationValidationPlan({binding, basis: undeclared, declaration: null}).state, "unbound");
  assert.throws(() => delegationValidationPlan({binding, basis: undeclared, declaration}), /without canonical/);
  assert.throws(() => delegationValidationPlan({binding, basis: {...validationBasis,
    todo: {...validationTodo, todo_id: "other"}}, declaration}), /matching canonical/);
  assert.throws(() => delegationValidationPlan({binding, basis: {...validationBasis,
    completion_requirements: undefined}, declaration}));
});

test("owner acceptance and ordinary Todo validation remain cumulative", () => {
  const criteria = [{id: "review", description: "Check the result", validation_argv: ["node", "owner.ts"],
    validation_timeout_seconds: 5, validation_files: [{path: "owner.ts", sha256: "a".repeat(64)}]}];
  const basis = {...validationBasis, completion_requirements: {todo_id: binding.todo_id, criteria}};
  const plan = delegationValidationPlan({binding, basis, declaration});
  assert.equal(plan.source, "goal_acceptance");
  assert.equal((plan.effects as unknown[]).length, 2);
  assert.equal(delegationValidationPlan({binding, basis, declaration: null}).state, "unbound");
  const onlyOwner = {...basis, todo: {todo_id: binding.todo_id, status: "open", done: false}};
  assert.equal(delegationValidationPlan({binding, basis: onlyOwner, declaration: null}).state, "ready");
  for (const requirements of [{todo_id: "other", criteria}, {todo_id: binding.todo_id, criteria: []}]) {
    assert.throws(() => delegationValidationPlan({binding, declaration,
      basis: {...basis, completion_requirements: requirements}}), /matching owner/);
  }
});

test("current validation provenance identifies definitions without exporting private commands", () => {
  const args = {binding, basis: validationBasis, declaration};
  const plan = delegationValidationPlan(args);
  const observed = plan.observation as Record<string, unknown>;
  assert.equal(observed.source, "todo_validation");
  assert.equal(observed.check_count, 1);
  assert.equal(observed.pinned_file_count, 0);
  assert.match(String(observed.basis_sha256), /^[a-f0-9]{64}$/);
  assert.deepEqual(Object.keys(observed).sort(), ["basis_sha256", "check_count", "pinned_file_count", "source"]);
  assert.deepEqual(delegationValidationPlan({...args,
    basis: {...validationBasis, provider_revision: "fixture:2"}}).observation, observed);
  const changed = {...declaration, validation_command_argv: ["node", "other-private-validator.ts"]};
  const changedPlan = delegationValidationPlan({...args, declaration: changed, basis: {...validationBasis,
    todo: {...validationTodo, completion_validation_sha256: canonicalAuthoritySha256(changed)}}});
  assert.notEqual((changedPlan.observation as Record<string, unknown>).basis_sha256, observed.basis_sha256);
  assert.equal(delegationValidationPlan({...args, declaration: null}).observation, undefined);
  const criteria = [{id: "review", description: "Private owner rule", validation_argv: ["node", "private-owner.ts"],
    validation_timeout_seconds: 5, validation_files: [{path: "private-owner.ts", sha256: "a".repeat(64)}]}];
  const combined = delegationValidationPlan({...args, basis: {...validationBasis,
    completion_requirements: {todo_id: binding.todo_id, criteria}}}).observation as Record<string, unknown>;
  assert.equal(combined.source, "goal_acceptance");
  assert.equal(combined.check_count, 2);
  assert.equal(combined.pinned_file_count, 1);
  assert.notEqual(combined.basis_sha256, observed.basis_sha256);
  assert.doesNotMatch(JSON.stringify(combined), /private|Independent verification|validation_argv/);
});

test("validation plan isolates progress metadata but fences work and claim/lifecycle", () => {
  const args = {binding, basis: validationBasis, declaration};
  const initial = delegationValidationPlan(args);
  assert.match(String(initial.task_basis_sha256), /^[a-f0-9]{64}$/);
  for (const delta of [{note: "Peer checked another result"}, {updated_at: "2026-10-05T00:00:00Z"},
    {priority: "P1"}, {evidence: "Current observation"}]) {
    assert.deepEqual(delegationValidationPlan({...args, basis: {...validationBasis,
      provider_revision: "fixture:99", todo: {...validationTodo, ...delta}}}), initial);
  }
  for (const delta of [{text: "Changed requested result"}, {future_work_field: "new obligation"},
    {claimed_by: "other"}, {role: "user"}, {status: "blocked"}, {archive_state: "archived"},
    {done: true, status: "done"}, {task_repository: "git:example.invalid/other/repository"}]) {
    assert.notEqual(delegationValidationPlan({...args, basis: {...validationBasis,
      todo: {...validationTodo, ...delta}}}).task_basis_sha256, initial.task_basis_sha256);
  }
});

test("fresh host checks bind stable declared output versions, without attesting independence", () => {
  const plan = delegationValidationPlan({binding, declaration, basis: {...validationBasis,
    todo: {...validationTodo, status: "done", done: true}}});
  const outputs = [{ref: "output.json", sha256: "a".repeat(64)}];
  const checkedAt = "2026-01-02T03:04:05.123456Z";
  const args = {binding, plan, before: outputs, after: outputs, checked_at: checkedAt};
  assert.deepEqual(delegationCheckedArtifacts(args), {...plan.observation as object,
    checked_at: checkedAt, output_versions: outputs});
  assert.deepEqual(delegationCheckedArtifacts({...args, after: [{...outputs[0], private_log: "never export"}]}),
    delegationCheckedArtifacts(args));
  assert.throws(() => delegationCheckedArtifacts({...args, after: [{...outputs[0], sha256: "b".repeat(64)}]}),
    /output changed during validation/);
  for (const versions of [[], [outputs[0], outputs[0]], [{...outputs[0], ref: "other.json"}],
    [{...outputs[0], ref: "/private/output.json"}], [{...outputs[0], sha256: "not a digest"}]]) {
    assert.throws(() => delegationCheckedArtifacts({...args, after: versions}));
    assert.throws(() => delegationCheckedArtifacts({...args, before: versions}));
  }
  for (const patch of [{canonical_done: false}, {state: "unbound"}, {todo_id: "other"},
    {observation: {...plan.observation as object, check_count: 0}}])
    assert.throws(() => delegationCheckedArtifacts({...args, plan: {...plan, ...patch}}));
  for (const time of [null, "invalid", "2026-01-02", "2026-01-02T03:04:05+00:00"])
    assert.throws(() => delegationCheckedArtifacts({...args, checked_at: time}), /host check time/);
  const two = [{ref: "output.json", sha256: "a".repeat(64)}, {ref: "second.txt", sha256: "c".repeat(64)}];
  assert.deepEqual(delegationCheckedArtifacts({...args, binding: {...binding, output_refs: ["output.json", "second.txt"]},
    before: two, after: [...two].reverse()}).output_versions, two);
});

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

const acceptedRequester = {goal_id: "research", agent_id: "coordinator", goal_ref: null,
  operation_id: "analysis-1", request_id: "req-1", artifacts: [{ref: "output.json", sha256: "a".repeat(64)}]};
const acceptedFacts = {canonical_done: true, acceptance_ready: true, artifacts_current: true, requester: acceptedRequester};

test("message receipt and model return do not imply accepted work", () => {
  assert.throws(() => transitionDelegationObservation({from: "prepared", to: "accepted"}), /transition/);
  assert.throws(() => transitionDelegationObservation({from: "turn_returned", to: "accepted"}), /canonical/);
  assert.throws(() => transitionDelegationObservation({from: "rejected", to: "running"}), /transition/);
  const accepted = transitionDelegationObservation({from: "turn_returned", to: "accepted", ...acceptedFacts});
  assert.equal(accepted.status, "accepted");
});

test("only a conversation-origin accepted result leaves a wake intent for the exact requester", () => {
  // An ordinary CLI/MCP delegation has no conversation. It must keep the
  // transition it always had: no intent, no wake state, no widened readback.
  const conversationless = transitionDelegationObservation({from: "turn_returned", to: "accepted", ...acceptedFacts});
  assert.deepEqual(conversationless, {status: "accepted"});
  assert.equal(conversationless.wake_intent, undefined);

  // With an originating conversation the intent is produced and pinned to it.
  const accepted = transitionDelegationObservation({from: "turn_returned", to: "accepted", ...acceptedFacts,
    requester: {...acceptedRequester, conversation: {session_id: "s-1", turn_id: "t-1", extra: "dropped"}}});
  const intent = accepted.wake_intent as Record<string, unknown>;
  assert.equal(intent.schema_version, "loopx_delegation_wake_intent_v0");
  assert.match(String(intent.intent_id), /^[a-f0-9]{64}$/);
  assert.deepEqual(intent.requester, {goal_id: "research", agent_id: "coordinator", goal_ref: null});
  assert.equal(intent.operation_id, "analysis-1");
  assert.equal(intent.request_id, "req-1");
  assert.deepEqual(intent.conversation, {session_id: "s-1", turn_id: "t-1"});
  // Another conversation of the same requester yields a different identity.
  const pinned = accepted;
  const pinnedIntent = intent;
  const elsewhere = transitionDelegationObservation({from: "turn_returned", to: "accepted", ...acceptedFacts,
    requester: {...acceptedRequester, conversation: {session_id: "s-2", turn_id: "t-1"}}});
  assert.notEqual((elsewhere.wake_intent as Record<string, unknown>).intent_id, pinnedIntent.intent_id);
  assert.throws(() => transitionDelegationObservation({from: "turn_returned", to: "accepted", ...acceptedFacts,
    requester: {...acceptedRequester, conversation: {session_id: "s-1"}}}), /conversation/);
  // Same requester and result: same intent, so a replayed transition cannot mint a second wake.
  assert.deepEqual(transitionDelegationObservation({from: "turn_returned", to: "accepted", ...acceptedFacts,
    requester: {...acceptedRequester, conversation: {session_id: "s-1", turn_id: "t-1"}}}), accepted);
  const changed = transitionDelegationObservation({from: "turn_returned", to: "accepted", ...acceptedFacts,
    requester: {...acceptedRequester, conversation: {session_id: "s-1", turn_id: "t-1"},
      artifacts: [{ref: "output.json", sha256: "b".repeat(64)}]}});
  assert.notEqual((changed.wake_intent as Record<string, unknown>).intent_id, intent.intent_id);
  // accepted -> accepted is an idempotent readback, never a new wake.
  assert.deepEqual(transitionDelegationObservation({from: "accepted", to: "accepted", ...acceptedFacts}), {status: "accepted"});
  // A transition to accepted without any requester identity is not a wake, and a
  // conversation-origin one with a forged digest is rejected.
  assert.deepEqual(transitionDelegationObservation({from: "turn_returned", to: "accepted",
    canonical_done: true, acceptance_ready: true, artifacts_current: true}), {status: "accepted"});
  const conversation = {session_id: "s-1", turn_id: "t-1"};
  assert.throws(() => transitionDelegationObservation({from: "turn_returned", to: "accepted", ...acceptedFacts,
    requester: {...acceptedRequester, conversation, artifacts: [{ref: "output.json", sha256: "short"}]}}), /artifact/);
  assert.throws(() => transitionDelegationObservation({from: "turn_returned", to: "accepted", ...acceptedFacts,
    requester: {...acceptedRequester, conversation, artifacts: []}}), /artifacts/);
  assert.throws(() => transitionDelegationObservation({from: "turn_returned", to: "accepted", ...acceptedFacts,
    requester: {...acceptedRequester, conversation, goal_ref: ["not", "a", "reference"]}}), /goal reference/);
  // Rejection is terminal and wakes nobody.
  assert.deepEqual(transitionDelegationObservation({from: "turn_returned", to: "rejected"}), {status: "rejected"});
});

test("stopped is terminal and reachable only from open observations", () => {
  for (const from of ["prepared", "running", "turn_returned"])
    assert.deepEqual(transitionDelegationObservation({from, to: "stopped"}), {status: "stopped"});
  assert.deepEqual(transitionDelegationObservation({from: "stopped", to: "stopped"}), {status: "stopped"});
  for (const from of ["accepted", "rejected"])
    assert.throws(() => transitionDelegationObservation({from, to: "stopped"}), /transition/);
  for (const to of ["running", "turn_returned", "accepted", "rejected"])
    assert.throws(() => transitionDelegationObservation({from: "stopped", to}), /transition/);
  const observation = {operation_id: "op-1", request_id: "req", agent_id: "reviewer", todo_id: "todo_review",
    status: "stopped", worker_active: false, recovery_required: false};
  assert.equal(delegationInventoryItem({record: {record_id: "a".repeat(64), operation_id: "op-1"},
    observation}).status, "stopped");
});

const stopRecord = (phase: string, reason: string) =>
  ({action: "record", phase, terminal: phase === "settled" || phase === "unknown", reason});

test("a stop settles only on an acknowledgement plus released holders; time alone proves nothing", () => {
  const open = {phase: "requested", acknowledged: false, operation_lock_free: false, worker_lane_released: false,
    host_process: "drained", lease: "unchecked"};
  assert.deepEqual(decideDelegationStop(open), stopRecord("requested", "awaiting_acknowledgement"));
  assert.deepEqual(decideDelegationStop({...open, timed_out: true}),
    stopRecord("requested", "holder_still_running_after_grace"));
  // A lane release without a free operation lock is not a vanished holder.
  assert.deepEqual(decideDelegationStop({...open, worker_lane_released: true, timed_out: true}),
    stopRecord("requested", "holder_still_running_after_grace"));
  // A free operation lock with an unattributed lane holder proves nothing yet.
  assert.deepEqual(decideDelegationStop({...open, operation_lock_free: true, timed_out: true}),
    stopRecord("requested", "worker_lane_release_unproven"));
  const gone = {...open, operation_lock_free: true, worker_lane_released: true};
  assert.deepEqual(decideDelegationStop(gone), {action: "resolve_lease"});
  assert.deepEqual(decideDelegationStop({...gone, lease: "not_owed"}),
    stopRecord("unknown", "holder_gone_without_acknowledgement"));
  const acked = {phase: "acknowledged", acknowledged: true, operation_lock_free: false, worker_lane_released: false,
    host_process: "drained", lease: "unchecked"};
  assert.deepEqual(decideDelegationStop(acked), stopRecord("acknowledged", "operation_lock_still_held"));
  assert.deepEqual(decideDelegationStop({...acked, worker_lane_released: true}),
    stopRecord("acknowledged", "operation_lock_still_held"));
  assert.deepEqual(decideDelegationStop({...acked, operation_lock_free: true}),
    stopRecord("acknowledged", "worker_lane_release_unproven"));
  const released = {...acked, operation_lock_free: true, worker_lane_released: true};
  for (const patch of [{phase: "requested"}, {timed_out: true}]) {
    assert.deepEqual(decideDelegationStop({...released, ...patch}), {action: "resolve_lease"});
    assert.deepEqual(decideDelegationStop({...released, ...patch, lease: "not_owed"}),
      stopRecord("settled", "acknowledged_worker_and_host_released"));
  }
  for (const patch of [{phase: "settled"}, {phase: "unknown"}, {phase: "noop"}, {acknowledged: "yes"},
    {host_process: undefined}, {host_process: "exited"}, {host_process: true},
    {operation_lock_free: 1}, {worker_lane_released: undefined}, {lane_lock_free: true, worker_lane_released: undefined},
    {timed_out: "later"}, {phase: "acknowledged", acknowledged: false}])
    assert.throws(() => decideDelegationStop({...open, ...patch}));
});

test("a released worker and lane never settle a stop while the native Host still drains", () => {
  const released = {phase: "acknowledged", acknowledged: true, operation_lock_free: true, worker_lane_released: true,
    lease: "unchecked"};
  assert.deepEqual(decideDelegationStop({...released, host_process: "draining", timed_out: true}),
    stopRecord("acknowledged", "host_process_still_running"));
  // Without an attributable drain the stop stays open for a later same-identity read.
  assert.deepEqual(decideDelegationStop({...released, host_process: "unattributable"}),
    stopRecord("acknowledged", "host_process_drain_unproven"));
  for (const host_process of ["drained", "not_launched"])
    assert.deepEqual(decideDelegationStop({...released, host_process, lease: "released"}),
      stopRecord("settled", "acknowledged_worker_and_host_released"));
  // A held lock still dominates a drained Host.
  assert.deepEqual(decideDelegationStop({...released, operation_lock_free: false, host_process: "drained"}),
    stopRecord("acknowledged", "operation_lock_still_held"));
  // A vanished holder is unknown only once its Host is no longer seen running;
  // with a Host that cannot be attributed nothing more can be learned, and its
  // lease is left unresolved rather than handed on beside a possible Host.
  const vanished = {...released, phase: "requested", acknowledged: false};
  assert.deepEqual(decideDelegationStop({...vanished, host_process: "draining"}),
    stopRecord("requested", "host_process_still_running"));
  assert.deepEqual(decideDelegationStop({...vanished, host_process: "unattributable"}),
    stopRecord("unknown", "holder_gone_without_acknowledgement"));
  for (const host_process of ["drained", "not_launched"])
    assert.deepEqual(decideDelegationStop({...vanished, host_process, lease: "not_owed"}),
      stopRecord("unknown", "holder_gone_without_acknowledgement"));
});

test("the lease is resolved only once the execution is gone, and only a resolved lease ends a stop", () => {
  const facts = {operation_lock_free: true, worker_lane_released: true, host_process: "drained"};
  const stops = [
    {stop: {phase: "acknowledged", acknowledged: true, ...facts}, open: "acknowledged",
      terminal: stopRecord("settled", "acknowledged_worker_and_host_released")},
    {stop: {phase: "requested", acknowledged: false, ...facts}, open: "requested",
      terminal: stopRecord("unknown", "holder_gone_without_acknowledgement")},
  ];
  for (const {stop, open, terminal} of stops) {
    // Not checked yet is a next step, never a receipt, and never "nothing owed".
    assert.deepEqual(decideDelegationStop({...stop, lease: "unchecked"}), {action: "resolve_lease"});
    for (const lease of ["not_owed", "released"]) assert.deepEqual(decideDelegationStop({...stop, lease}), terminal);
    // A held lease whose release is unproven, and an obligation canonical
    // authority could not confirm, both keep the stop open, distinguishably.
    assert.deepEqual(decideDelegationStop({...stop, lease: "release_unproven"}),
      stopRecord(open, "required_lease_release_unproven"));
    assert.deepEqual(decideDelegationStop({...stop, lease: "obligation_unproven"}),
      stopRecord(open, "lease_obligation_unproven"));
    // Omission no longer reads as "no lease"; the old boolean is not a lease fact.
    for (const patch of [{}, {lease: undefined}, {lease: "held"}, {lease: true}, {lease_released: true}])
      assert.throws(() => decideDelegationStop({...stop, ...patch}), /lease fact required/);
  }
  // A lease is never resolved beside an execution that may still run.
  for (const pending of [{operation_lock_free: false}, {worker_lane_released: false},
    {host_process: "draining"}, {host_process: "unattributable"}]) {
    for (const lease of ["not_owed", "released", "release_unproven", "obligation_unproven"])
      assert.throws(() => decideDelegationStop({...stops[0].stop, ...pending, lease}), /proven gone/);
    assert.notEqual(decideDelegationStop({...stops[0].stop, ...pending, lease: "unchecked"}).action, "resolve_lease");
  }
});

test("a false rejection can reopen only for exact validated settlement recovery", () => {
  const evidence = {
    from: "rejected",
    identity_matched: true,
    journal_status: "in_progress",
    result_kind: "validated_progress",
    task_validation_passed: true,
    completed_phases: ["host_execute", "typed_result", "validation"],
  };
  assert.deepEqual(recoverValidatedDelegationSettlement(evidence), {
    status: "turn_returned",
    recovery_kind: "settlement_only",
    host_reexecution_allowed: false,
  });
  for (const patch of [
    {from: "turn_returned"},
    {identity_matched: false},
    {journal_status: "committed"},
    {result_kind: "host_failure"},
    {task_validation_passed: false},
    {completed_phases: ["host_execute", "typed_result"]},
  ]) assert.throws(() => recoverValidatedDelegationSettlement({...evidence, ...patch}));
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
  assert.equal(delegationPreflight(params).authority_state, "promoted");
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

const runtimePreflight = (executor: Record<string, unknown>) => delegationPreflight({
  binding, validation_files_current: true, acceptance: {todo_id: binding.todo_id, state: "ready"},
  preview: {dry_run: true, status: "preview",
    effects: {host_invoked: false, state_written: false, quota_spent: false, scheduler_acknowledged: false},
    route: {kind: "ready_for_host", would_invoke_host: true, selected_todo_id: binding.todo_id},
    managed_executor: executor},
});

test("preflight retains scoped runtime facts without leaking host configuration", () => {
  const probe = {schema_version: "managed_runtime_probe_v0", scope: "probing_interpreter",
    module: "deepseek_harness", available: false};
  const remedies = ["configure_dsh_runtime", "select_individual_host"];
  const result = runtimePreflight({executor: "dsh", available: false,
    unavailable_reason: "dsh_runtime_unavailable", execution_profile: "explicit-profile",
    runtime_probe: {...probe, private_path: "/private/interpreter"}, unavailable_remediation: remedies,
    credential_env: "PRIVATE_CREDENTIAL", endpoint_env: "PRIVATE_ENDPOINT"});
  assert.equal(result.state, "runtime_unavailable");
  assert.deepEqual(result.executor, {host: "dsh", available: false,
    reason: "dsh_runtime_unavailable", profile: "explicit-profile",
    runtime_probe: probe, unavailable_remediation: remedies});
  assert.equal(JSON.stringify(result).includes("PRIVATE_"), false);
  assert.equal(JSON.stringify(result).includes("/private/interpreter"), false);
  assert.equal(Object.values(result.effects as Record<string, boolean>).some(Boolean), false);
});

test("runtime probes cannot override credential failure or unprobed generic readiness", () => {
  const result = runtimePreflight({executor: "dsh", available: false,
    unavailable_reason: "operator_credential_unconfigured", execution_profile: "explicit-profile",
    runtime_probe: {schema_version: "managed_runtime_probe_v0", scope: "configured_runner",
      module: null, available: true},
    unavailable_remediation: ["configure_operator_credential", "select_individual_host"]});
  assert.equal(result.state, "runtime_unavailable");
  assert.equal((result.executor as Record<string, unknown>).available, false);
  assert.deepEqual((result.executor as Record<string, unknown>).runtime_probe,
    {schema_version: "managed_runtime_probe_v0", scope: "configured_runner", module: null, available: true});
  const generic = runtimePreflight({executor: "generic-cli", available: null,
    unavailable_reason: null, execution_profile: null, runtime_probe: null, unavailable_remediation: []});
  assert.equal(generic.state, "runtime_unverified");
  assert.deepEqual(generic.executor, {host: "generic-cli", available: null, reason: null,
    profile: null, runtime_probe: null, unavailable_remediation: []});
});

test("malformed scoped runtime facts fail closed; legacy omissions remain compatible", () => {
  const probe = {schema_version: "managed_runtime_probe_v0", scope: "probing_interpreter",
    module: "deepseek_harness", available: false};
  const executor = {executor: "dsh", available: false, unavailable_reason: "dsh_runtime_unavailable",
    execution_profile: "explicit-profile", runtime_probe: probe, unavailable_remediation: ["configure_dsh_runtime"]};
  for (const runtime_probe of [false, {}, {...probe, schema_version: "other"},
    {...probe, scope: "whole_machine"}, {...probe, scope: ["probing_interpreter"]},
    {...probe, module: "/private/path"},
    {...probe, available: "false"}]) {
    assert.throws(() => runtimePreflight({...executor, runtime_probe}), /runtime probe/);
  }
  for (const unavailable_remediation of [null, "configure_dsh_runtime", ["/private/path"],
    ["x".repeat(81)], Array(9).fill("configure_dsh_runtime")]) {
    assert.throws(() => runtimePreflight({...executor, unavailable_remediation}), /runtime remediation/);
  }
  const legacy = runtimePreflight({executor: "dsh", available: true, unavailable_reason: null,
    execution_profile: "explicit-profile"});
  assert.equal(legacy.state, "launchable");
  assert.deepEqual(legacy.executor,
    {host: "dsh", available: true, reason: null, profile: "explicit-profile"});
});

test("requester adoption needs accepted downstream use, not reading, revision or prose", () => {
  const artifact = {ref: "result.json", sha256: "a".repeat(64)};
  const source = {operation_id: "source", status: "accepted", artifacts: [artifact]};
  const consumer = {operation_id: "consumer", request_id: "request", agent_id: "reviewer",
    todo_id: "task", status: "accepted", artifacts: [{...artifact, sha256: "b".repeat(64)}]};
  const input = {ref: "input.json", sha256: artifact.sha256,
    delegation: {operation_id: "source", ref: artifact.ref, relation: "uses"}};
  const params = {source, consumer, inputs: [input], inputs_current: true};
  assert.equal(recordDelegationAdoption(params).consumer_operation_id, "consumer");
  for (const patch of [{inputs_current: false}, {inputs: []}, {consumer: {...consumer, status: "running"}},
    {source: {...source, status: "rejected"}}, {consumer: {...consumer, operation_id: "source"}},
    {inputs: [{...input, sha256: "c".repeat(64)}]},
    {inputs: [{...input, delegation: {...input.delegation, relation: "revises"}}]}]) {
    assert.throws(() => recordDelegationAdoption({...params, ...patch}));
  }
});

test("preflight reports unavailable current authority without granting execution", () => {
  const result = delegationPreflight({binding, authority: {ready: false,
    reason: "Goal acceptance requires an existing canonical authority"}, preview: null,
  acceptance: null, validation_files_current: false});
  assert.equal(result.state, "authority_unavailable");
  assert.equal(result.authority_ready, false);
  assert.equal(result.authority_state, "unavailable");
  assert.equal(result.authority_next_action, "repair_canonical_authority");
  assert.equal(result.promotion_from_surface_allowed, false);
  assert.equal(result.turn_eligible, false);
  assert.equal(result.executor, null);
  assert.equal(Object.values(result.effects as Record<string, boolean>).some(Boolean), false);
  assert.match(String(result.authority_reason), /canonical authority/);
  assert.doesNotMatch(String(result.note), /no Turn or provider was inspected/);
  assert.match(String(result.note), /no executable permission is returned/);
  const legacy = delegationPreflight({binding, authority: {ready: false,
    reason: "canonical authority absent", state: "promotion_required",
    next_action: "preview_reviewed_goal_authority_promotion"}, preview: null,
  acceptance: null, validation_files_current: false});
  assert.equal(legacy.authority_state, "promotion_required");
  assert.equal(legacy.authority_next_action, "preview_reviewed_goal_authority_promotion");
  assert.throws(() => delegationPreflight({binding, authority: {ready: false, reason: "missing"},
    preview: {}, acceptance: null, validation_files_current: false}));
});

test("workspace faults are bounded observations, not authority or launch permits", () => {
  const input = {binding, authority: null, preview: null, acceptance: null,
    validation_files_current: false};
  for (const state of ["missing", "not_directory", "unavailable"]) {
    const result = delegationPreflight({...input, workspace: {state, path: "/private/workspace"}});
    assert.equal(result.state, "workspace_unavailable");
    assert.equal(result.workspace_state, state);
    assert.equal(result.workspace_next_action, "review_operator_workspace_binding");
    assert.equal(result.authority_ready, null);
    assert.equal(result.authority_state, "uninspected");
    assert.equal(result.authority_next_action, "none");
    assert.equal(result.authority_reason, null);
    assert.equal(result.acceptance_ready, false);
    assert.equal(result.turn_eligible, false);
    assert.equal(result.executor, null);
    assert.deepEqual(result.binding, {id: binding.id, agent_id: binding.agent_id, todo_id: binding.todo_id});
    assert.equal(result.promotion_from_surface_allowed, false);
    assert.equal(Object.values(result.effects as Record<string, boolean>).some(Boolean), false);
    assert.equal(JSON.stringify(result).includes("/private"), false);
    for (const patch of [{authority: {ready: true}}, {preview: {}},
      {acceptance: {todo_id: binding.todo_id, state: "ready"}}, {validation_files_current: true}]) {
      assert.throws(() => delegationPreflight({...input, workspace: {state}, ...patch}), /cannot claim/);
    }
  }
  for (const workspace of [null, false, {}, {state: ["missing"]}, {state: "unknown"}])
    assert.throws(() => delegationPreflight({...input, workspace}), /workspace observation/);
  // A present directory does not bypass the existing authority or Turn owners.
  assert.throws(() => delegationPreflight({...input, workspace: {state: "available"}}), /authority readiness/);
  const unavailable = {...input, authority: {ready: false, reason: "canonical read failed"}};
  assert.deepEqual(delegationPreflight({...unavailable, workspace: {state: "available"}}),
    delegationPreflight(unavailable));
});

test("Turn plan decision preserves a rejection and validates a successful transaction", () => {
  const rejected = delegationTurnPlanDecision({plan: {ok: false,
    error: "Requested Turn Todo is not accepted by canonical authority"}});
  assert.deepEqual(rejected, {
    schema_version: "loopx_delegation_turn_plan_decision_v0", state: "rejected",
    turn_key: null, reason: "Requested Turn Todo is not accepted by canonical authority",
  });
  const turnKey = `sha256:${"a".repeat(64)}`;
  assert.equal(delegationTurnPlanDecision({plan: {ok: true,
    transaction: {turn_key: turnKey}}}).turn_key, turnKey);
  assert.throws(() => delegationTurnPlanDecision({plan: {ok: true, transaction: {}}}));
});
