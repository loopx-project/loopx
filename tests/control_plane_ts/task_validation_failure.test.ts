import test from "node:test";
import assert from "node:assert/strict";
import {taskValidationFailure} from "../../loopx/control_plane/turn_driver/task_validation_failure.ts";
import {recoverRevalidatedDelegation} from "../../loopx/control_plane/collaboration/delegation.ts";
const failure = {result_kind: "validation_failed", status: "failed", validation_stage: "task_postcondition",
  receipt: {ok: true, status: "failed", result_kind: "validation_failed", turn_key: "sha256:original",
    failed_phase: "validation", completed_phases: ["host_execute", "typed_result"]},
  validation: {schema_version: "loopx_turn_task_validation_v0", ok: false, status: "failed",
    recovery_kind: "replan_required", validator_kind: "independent", summary: "Declared postcondition absent", errors: []}};
test("independent failure retains its scope, original identity and replan direction", () => {
  assert.equal(taskValidationFailure(failure).failure?.recovery_kind, "replan_required");
  assert.equal(taskValidationFailure({...failure, validation_stage: undefined}).failure, null);
  assert.equal(taskValidationFailure({...failure, validation_stage: "host_result_contract"}).failure, null);
  for (const patch of [{ok: true}, {status: "passed"}, {recovery_kind: "wait"}])
    assert.throws(() => taskValidationFailure({...failure, validation: {...failure.validation, ...patch}}));
  assert.throws(() => taskValidationFailure({...failure, receipt: {...failure.receipt, failed_phase: "quota_spend"}}));
});
test("a task recheck cannot reopen rejected work without successful original settlement", () => {
  const facts = {from: "rejected", original_task_failure: true, committed_progress: true, host_reinvoked: false};
  assert.equal(recoverRevalidatedDelegation(facts).status, "turn_returned");
  assert.throws(() => recoverRevalidatedDelegation({...facts, original_task_failure: false}));
  assert.throws(() => recoverRevalidatedDelegation({...facts, committed_progress: false}));
  assert.throws(() => recoverRevalidatedDelegation({...facts, host_reinvoked: true}));
});
