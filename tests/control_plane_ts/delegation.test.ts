import test from "node:test";
import assert from "node:assert/strict";
import {recordDelegationAdoption, delegationInventoryItem, delegationInventoryQuery, delegationPreflight, delegationTurnPlanDecision, delegationValidationPlan, recoverValidatedDelegationSettlement, selectDelegationBinding, transitionDelegationObservation} from "../../loopx/control_plane/collaboration/delegation.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {projectTurnSelectionRejection} from "../../loopx/control_plane/turn_driver/selection_rejection.ts";

const binding = {id: "review", agent_id: "reviewer", todo_id: "todo_review", workspace: "/fixture",
  requesters: ["coordinator", "analyst"], host_args: ["--host", "dsh"], timeout_seconds: 60, output_refs: ["output.json"]};
const params = {agent_id: "coordinator", binding_id: "review",
  config: {schema_version: "loopx_local_delegation_v0", bindings: [binding]}};

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

test("preflight reports unavailable canonical authority without pretending to inspect a Turn", () => {
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
