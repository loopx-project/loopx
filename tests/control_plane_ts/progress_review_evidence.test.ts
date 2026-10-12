import test from "node:test";
import assert from "node:assert/strict";
import {progressReviewCriterionBasis, progressReviewEvidenceScope} from "../../loopx/control_plane/work_items/progress_review_evidence.ts";
const identity = {goal_id: "goal", agent_id: "agent"};
const run = {agent_id: "agent", todo_id: "task"};
const requirements = {todo_id: "task", contract_revision: 1, contract_digest: "a".repeat(64),
  todo_semantic_digest: "b".repeat(64), criteria: [{id: "check", description: "Prove the declared postcondition"}]};
test("canonical criteria come from the task's current scope, not operator assertions", () => {
  const result = progressReviewCriterionBasis({...identity, requirements, criterion_ids: ["check"], acceptance: ["Claim success"]});
  assert.deepEqual(result.acceptance, ["Prove the declared postcondition"]);
  assert.equal((result.binding as any).origin, "goal_acceptance");
  assert.throws(() => progressReviewCriterionBasis({...identity, requirements, criterion_ids: ["another"]}));
  assert.throws(() => progressReviewCriterionBasis({...identity, requirements, criterion_ids: ["check", "check"]}));
  const manual = progressReviewCriterionBasis({requirements: null, acceptance: ["Study this behavior"]});
  assert.equal((manual.binding as any).origin, "operator_study");
  const scope = {criterion_binding: result.binding, coverage: "declared_file_net_change", files: ["code.ts"]};
  assert.deepEqual(progressReviewEvidenceScope({...identity, run, scope}).scope, scope);
  assert.throws(() => progressReviewEvidenceScope({...identity, run, scope: {...scope, files: ["../outside"]}}));
  assert.throws(() => progressReviewEvidenceScope({...identity, run, scope: {...scope, coverage: "all_steps"}}));
});

test("canonical criteria cannot be used for a different goal, agent or task", () => {
  const basis = progressReviewCriterionBasis({...identity, requirements, criterion_ids: ["check"]});
  const scope = {criterion_binding: basis.binding, coverage: "declared_file_net_change", files: []};
  for (const changed of [{goal_id: "other", run}, {goal_id: "goal", run: {...run, todo_id: "other"}},
    {goal_id: "goal", run: {...run, agent_id: "other"}}, {goal_id: "goal", run: {}}])
    assert.throws(() => progressReviewEvidenceScope({...changed, scope}));
});
