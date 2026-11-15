import assert from "node:assert/strict";
import test from "node:test";
import {goalCreationExecutionReview, normalizeGoalCreationExecution} from "../../loopx/control_plane/goals/creation_execution_review.ts";

const original = () => ({agent_id: "codex", start_execution: true, permission: "read_only",
  completion_criteria: "The existing independent check accepts the requested result.", initial_todos: ["Complete the original work."]});

test("old creation and explicit no-start retain no executable request", () => {
  assert.deepEqual(normalizeGoalCreationExecution({agent_id: "codex"}), {});
  assert.equal(goalCreationExecutionReview({permission: "read_only"}), undefined);
  assert.equal(goalCreationExecutionReview({...original(), start_execution: false}), undefined);
});

test("an explicit read-only request starts work without granting writes", () => {
  assert.deepEqual(goalCreationExecutionReview(original()), {requested: true, permission: "read_only", writeScope: []});
});

test("only the existing explicit workspace-write review selects the whole workspace", () => {
  assert.deepEqual(goalCreationExecutionReview({...original(), permission: "workspace_write_on_confirmation"}),
    {requested: true, permission: "workspace_write_on_confirmation", writeScope: ["**"]});
});

for (const changed of [{permission: "write"}, {permission: ""}, {permission: null}, {start_execution: "true"},
  {agent_id: "claude-code"}, {native_token_budget: 1000}, {initial_todos: []}, {initial_todos: ["one", "two"]},
  {completion_criteria: ""}]) {
  test(`invalid or competing creation intent ${JSON.stringify(changed)} is rejected before creation`, () => {
    assert.throws(() => normalizeGoalCreationExecution({...original(), ...changed}));
  });
}
