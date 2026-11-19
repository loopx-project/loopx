import assert from "node:assert/strict";
import test from "node:test";
import {planReportReview, projectReportReviews} from "../../loopx/control_plane/presentation/report_review.ts";
import {reportReviewDigest} from "../../loopx/control_plane/presentation/report_review_binding.ts";

const input = {goal_id: "research", agent_id: "researcher", bundle_sha256: "a".repeat(64), title: "研究审阅材料"};
test("review identity belongs to the exact edition, Goal and producer", () => {
  const original = planReportReview(input);
  assert.deepEqual(planReportReview({...input}), original);
  for (const change of [{goal_id: "other"}, {agent_id: "peer"}, {bundle_sha256: "b".repeat(64)}])
    assert.notEqual(planReportReview({...input, ...change}).operation_id, original.operation_id);
  assert.equal(original.task_class, "user_action");
  assert.equal(original.blocks_agent, undefined);
  assert.equal(original.global_gate, undefined);
  assert.ok(String(planReportReview({...input, goal_id: "g".repeat(150), agent_id: "a".repeat(80)}).operation_id).length < 128);
});
test("only the registered binding selects review work; wording and done counts do not", () => {
  const row = {...planReportReview(input), todo_id: "todo_review", status: "open", goal_id: input.goal_id};
  assert.equal(reportReviewDigest(row), input.bundle_sha256);
  for (const change of [{role: "agent"}, {task_class: "user_gate"}, {action_kind: "approve"}, {capability_binding_ref: "periodic-report-review:r../../other"}])
    assert.equal(reportReviewDigest({...row, ...change}), null);
  const projected = projectReportReviews({goal_id: input.goal_id, todos: [row,
    {todo_id: "todo_not_a_result", text: "Please review and accept this report", status: "done"}]});
  assert.equal((projected.items as unknown[]).length, 1);
  assert.equal((projectReportReviews({goal_id: input.goal_id, todos: [{...row, status: "done"}]}).items as typeof row[])[0].status, "done");
  assert.throws(() => projectReportReviews({goal_id: "foreign", todos: [row]}));
  assert.throws(() => projectReportReviews({goal_id: input.goal_id}));
});

test("an exact producer's queued edition is replayed without interpreting a human decision", () => {
  const row = {...planReportReview(input), todo_id: "todo_original", created_by: input.agent_id,
    bound_agent: "new-response-owner", status: "done"};
  assert.equal(planReportReview({...input, todos: [row]}).existing_todo_id, row.todo_id);
  assert.equal(planReportReview({...input, agent_id: "peer", todos: [row]}).existing_todo_id, null);
  assert.throws(() => planReportReview({...input, todos: [row, {...row, todo_id: "todo_duplicate"}]}), /Ambiguous/);
});

test("the existing 200-code-point title limit counts Unicode code points, not UTF-16 units", () => {
  for (const unit of ["a", "研", "🧪"]) {
    assert.equal(planReportReview({...input, title: unit.repeat(200)}).text, unit.repeat(200));
    assert.throws(() => planReportReview({...input, title: unit.repeat(201)}), /title exceeds/);
  }
});
