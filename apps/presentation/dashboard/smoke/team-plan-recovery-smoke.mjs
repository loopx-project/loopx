import assert from "node:assert/strict";
import {createElement} from "react";
import {renderToStaticMarkup} from "react-dom/server";
import {compileActionReviewPlan} from "../../../../loopx/control_plane/presentation/action_review_plan.ts";
import {ContextDrawer} from "../src/features/personal-workspace/context-drawer.tsx";
import {WorkspaceI18nProvider} from "../src/features/personal-workspace/i18n.tsx";

const failed = {
  schema_version: "loopx_chat_action_proposal_v1", proposal_id: "assignment-1",
  action_kind: "team.plan", status: "failed", permission_classification: "durable_write",
  expected_state_fingerprint: "source-1", available_transitions: ["apply", "cancel"],
  failure: {retry_safe: true}, context: {kind: "manager", goal_id: "goal-1"},
  normalized_parameters: {goal_id: "goal-1", plan: {
    schema_version: "steward_team_plan_preview_v0", kind: "steward_team_plan_preview",
    goal_id: "goal-1", objective: "Inspect the contract", applies: false,
    stop_condition: "Return evidence", quota_envelope: {slots: 1}, gaps: [],
    lanes: [{lane_id: "inspect", agent_id: "alpha", staffing: "ready",
      acceptance: "Return verified evidence", first_todo: {text: "Inspect the contract",
        priority: "P1", task_class: "advancement_task", action_kind: "implement"}}],
  }},
};
function render(raw, patch = {}) {
  const reviewPlan = compileActionReviewPlan(raw);
  const item = {actionKind: raw.action_kind, previewId: raw.proposal_id, goalId: "goal-1",
    title: "Assign inspection", fields: [], status: raw.status === "stale" ? "stale" : "error",
    reviewPlan, ...patch};
  return renderToStaticMarkup(createElement(WorkspaceI18nProvider, null,
    createElement(ContextDrawer, {agents: [], callbacks: {}, onClose() {},
      selection: {kind: "proposal", item}})));
}
assert.match(render(failed), /<button[^>]*>.*?Retry original operation<\/button>/s,
  "A retry-safe original assignment has a recovery action");
for (const patch of [
  {failure: {retry_safe: false}}, {failure: null},
  {normalized_parameters: {...failed.normalized_parameters, goal_id: "another-goal"}},
  {permission_classification: "protected"}, {available_transitions: ["cancel"]},
]) {
  const html = render({...failed, ...patch});
  assert.doesNotMatch(html, /Retry original operation|Retry assignment/,
    "An unsafe or mismatched assignment cannot expose original apply");
  assert.match(html, /does not permit retrying the assignment/);
}
assert.doesNotMatch(render(failed, {reviewPlan: {...compileActionReviewPlan(failed), canApply: false}}),
  /Retry original operation<\/button>/, "Even a retained recovery flag cannot bypass disabled apply");
const stale = render({...failed, status: "stale", stale: {current: "source-2"}});
assert.doesNotMatch(stale, /Retry original operation|Recover operation result/);
assert.match(stale, /Recheck against latest state/);
assert.match(render({...failed, action_kind: "todo.create", failure: null}), /Retry with latest state/,
  "Unrelated failed previews retain their existing regeneration action");
console.log("PASS: rendered Team Plan recovery respects typed admission and stale state");
