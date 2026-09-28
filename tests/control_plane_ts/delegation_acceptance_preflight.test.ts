import test from "node:test";
import assert from "node:assert/strict";
import {delegationPreflight, delegationValidationPlan} from "../../loopx/control_plane/collaboration/delegation.ts";
import {projectTurnSelectionRejection} from "../../loopx/control_plane/turn_driver/selection_rejection.ts";

const binding = {id: "review", agent_id: "reviewer", todo_id: "todo_review"};
const effects = {host_invoked: false, state_written: false, quota_spent: false, scheduler_acknowledged: false};
const preview = {dry_run: true, status: "preview", effects,
  route: {kind: "ready_for_host", would_invoke_host: true, selected_todo_id: binding.todo_id},
  managed_executor: {executor: "dsh", available: true, unavailable_reason: null, execution_profile: "explicit-profile"}};
const input = {binding, preview, validation_files_current: true,
  acceptance: {todo_id: binding.todo_id, state: "ready"}};

test("missing independent validation is diagnosed from the existing canonical plan", () => {
  const acceptance = delegationValidationPlan({binding, declaration: null,
    basis: {status: "loaded", provider_revision: "fixture:1", completion_requirements: null,
      todo: {todo_id: binding.todo_id, status: "open", done: false}}});
  const result = delegationPreflight({...input, acceptance, validation_files_current: false});
  assert.equal(result.state, "acceptance_unavailable");
  assert.equal(result.acceptance_ready, false);
  assert.equal(result.acceptance_reason_code, "independent_delegation_validation_required");
  assert.equal(result.acceptance_next_action, "review_original_todo_validation");
  assert.deepEqual(result.effects, effects);
  assert.equal((result.executor as Record<string, unknown>).available, true);
});

test("only whitelisted current-task validation reasons reach public preflight", () => {
  for (const reason of ["completion_validation_declaration_unavailable", "completion_validation_declaration_mismatch"]) {
    const result = delegationPreflight({...input, validation_files_current: false,
      acceptance: {todo_id: binding.todo_id, state: "unbound", reason,
        effects: [{validation_argv: ["node", "/private/validator.ts"]}], private_detail: "PRIVATE_VALUE"}});
    assert.equal(result.acceptance_reason_code, reason);
    assert.equal(result.acceptance_next_action, "review_original_todo_validation");
    assert.equal(result.state, "acceptance_unavailable");
    assert.doesNotMatch(JSON.stringify(result), /private\/validator|PRIVATE_VALUE|validation_argv/);
    assert.deepEqual(result.effects, effects);
  }
});

test("unknown, stale or foreign-task reasons cannot masquerade as missing independent validation", () => {
  for (const acceptance of [null,
    {todo_id: binding.todo_id, state: "unbound", reason: "PRIVATE_VALUE /private/validator"},
    {todo_id: "other", state: "unbound", reason: "independent_delegation_validation_required"},
    {todo_id: binding.todo_id, state: "stale", reason: "independent_delegation_validation_required"},
    {todo_id: binding.todo_id, state: "unbound", reason: ["independent_delegation_validation_required"]},
    {todo_id: binding.todo_id, state: "unbound", reason: "__proto__"}]) {
    const result = delegationPreflight({...input, acceptance});
    assert.equal(result.acceptance_reason_code, "acceptance_binding_unavailable");
    assert.equal(result.acceptance_next_action, "review_original_task_acceptance");
    assert.equal(result.state, "acceptance_unavailable");
    assert.equal(result.acceptance_ready, false);
    assert.doesNotMatch(JSON.stringify(result), /PRIVATE_VALUE|private\/validator|__proto__/);
  }
});

test("validation file observations are distinct from declaration and binding faults", () => {
  const unavailable = delegationPreflight({...input, validation_files_current: false});
  assert.equal(unavailable.acceptance_reason_code, "validation_files_unavailable");
  assert.equal(unavailable.acceptance_next_action, "restore_original_validation_files");
  assert.equal(unavailable.state, "acceptance_unavailable");
  const ready = delegationPreflight({...input, acceptance: {...input.acceptance,
    reason: "independent_delegation_validation_required"}});
  assert.equal(ready.acceptance_ready, true);
  assert.equal(ready.state, "launchable");
  assert.equal(ready.acceptance_reason_code, null);
  assert.equal(ready.acceptance_next_action, "none");
  for (const validation_files_current of [undefined, null, "false", []]) {
    const unknown = delegationPreflight({...input, validation_files_current});
    assert.equal(unknown.acceptance_reason_code, "acceptance_binding_unavailable");
    assert.equal(unknown.acceptance_ready, false);
  }
});

test("workspace and authority stops never claim to have inspected task validation", () => {
  for (const patch of [{workspace: {state: "missing"}, authority: null},
    {authority: {ready: false, reason: "canonical authority unavailable"}}]) {
    const result = delegationPreflight({binding, preview: null, acceptance: null,
      validation_files_current: false, ...patch});
    assert.equal(result.acceptance_reason_code, null);
    assert.equal(result.acceptance_next_action, "none");
    assert.equal(result.acceptance_ready, false);
    assert.equal(result.executor, null);
    assert.deepEqual(result.effects, effects);
  }
});

test("structured Turn refusal keeps its priority and the same acceptance diagnosis", () => {
  const projected = projectTurnSelectionRejection({requested_todo_id: binding.todo_id, contract_error_count: 3,
    decision: {status_health_ok: false, action_selection_qualification: {state: "deferred",
      requested_todo_id: binding.todo_id, reason: "control_repair", recovery_action: "reenter_guard_without_selection"}}});
  const result = delegationPreflight({...input,
    preview: {ok: false, ...projected, effects_scope: "current_invocation", effects},
    acceptance: {todo_id: binding.todo_id, state: "unbound", reason: "independent_delegation_validation_required"}});
  assert.equal(result.state, "turn_blocked");
  assert.equal(result.acceptance_ready, false);
  assert.equal(result.acceptance_reason_code, "independent_delegation_validation_required");
  assert.deepEqual(result.turn_blocker, projected.selection_rejection);
  assert.equal(result.executor, null);
  assert.deepEqual(result.effects, effects);
});
