import assert from "node:assert/strict";
import test from "node:test";
import {originalCreationTaskSelection} from "../../loopx/control_plane/goals/task_validation_plan.ts";

import {canonicalNativeTaskSandboxPolicy} from "../../loopx/control_plane/collaboration/steward_managed_mode.ts";

const basis = (outcomes: unknown[]) => ({status: "loaded", provider_revision: "current",
  goal_acceptance_contract: {outcome_requirements: {outcomes}}});

test("original Task comes from retained creation work, independently of later outcomes", () => {
  assert.deepEqual(originalCreationTaskSelection({basis: basis([
    {id: "later", todo_ids: ["newest"]}, {id: "goal-creation", todo_ids: ["original"]},
  ])}), {todo_id: "original", reason: null});
});

for (const [name, outcomes] of [
  ["missing origin", [{id: "later", todo_ids: ["newest"]}]],
  ["multiple initial Tasks", [{id: "goal-creation", todo_ids: ["first", "second"]}]],
  ["removed work", [{id: "goal-creation", todo_ids: []}]],
  ["duplicated origin", [{id: "goal-creation", todo_ids: ["one"]}, {id: "goal-creation", todo_ids: ["two"]}]],
] as const) test(`${name} never selects the first or newest Task`, () => {
  assert.equal(originalCreationTaskSelection({basis: basis([...outcomes])}).todo_id, null);
});

test("unavailable canonical authority is not a display selection", () => {
  assert.throws(() => originalCreationTaskSelection({basis: {...basis([]), status: "unavailable"}}),
    /current Goal acceptance basis/);
});

const policyInput = () => ({host: "codex-app-server", actor: {schema_version: "loopx_managed_session_actor_v0", goal_id: "goal",
  agent_id: "agent", workspace_path: "/exercise"}, validation: {todo_id: "original", state: "ready"},
authority: {_work_identity: {goal_id: "goal", agent_id: "agent", todo_id: "original"},
  execution_preparation: {todo_id: "original", agent_id: "agent", state: "ready", delivery_allowed: true},
  should_run: true, normal_delivery_allowed: true, goal_boundary: {}}});

test("a native Task or its confirmed checks do not grant workspace writes", () => {
  assert.deepEqual(canonicalNativeTaskSandboxPolicy(policyInput()), {type: "readOnly", networkAccess: false});
});

for (const scope of ["cart.py", "outputs/**"]) test(`narrow ${scope} approval is never widened to the workspace`, () => {
  const input = policyInput();
  input.authority.goal_boundary = {checkpointed_boundary_authority: {
    schema_version: "checkpointed_boundary_authority_v0", active_count: 1, active_write_scope: [scope]}};
  assert.deepEqual(canonicalNativeTaskSandboxPolicy(input), {type: "readOnly", networkAccess: false});
});

test("the original admitted Task uses the already approved workspace with network and temp writes disabled", () => {
  const input = policyInput();
  input.authority.goal_boundary = {checkpointed_boundary_authority: {
    schema_version: "checkpointed_boundary_authority_v0", active_count: 1, active_write_scope: ["**"]}};
  assert.deepEqual(canonicalNativeTaskSandboxPolicy(input), {type: "workspaceWrite", writableRoots: ["/exercise"],
    networkAccess: false, excludeTmpdirEnvVar: true, excludeSlashTmp: true});
});

for (const count of [undefined, 0]) test(`an approval without an active count of ${count} grants no workspace writes`, () => {
  const input = policyInput();
  input.authority.goal_boundary = {checkpointed_boundary_authority: {
    schema_version: "checkpointed_boundary_authority_v0", active_count: count, active_write_scope: ["**"]}};
  assert.deepEqual(canonicalNativeTaskSandboxPolicy(input), {type: "readOnly", networkAccess: false});
});

test("Task substitution and an unavailable execution lease cannot yield host permissions", () => {
  const substituted = policyInput();
  substituted.authority._work_identity.todo_id = "other";
  assert.throws(() => canonicalNativeTaskSandboxPolicy(substituted), /original admitted Task/);
  const unavailable = policyInput();
  unavailable.authority.execution_preparation.state = "unavailable";
  assert.throws(() => canonicalNativeTaskSandboxPolicy(unavailable), /execution lease/);
});
