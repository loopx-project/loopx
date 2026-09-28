import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { AttentionDetailCard } from "../src/features/personal-workspace/attention-detail-card";
import { WorkspaceI18nProvider } from "../src/features/personal-workspace/i18n";
import { todoItemSchema } from "../src/data/status";
import { attentionDetails, attentionSuccessor, canReviewAttention, refreshAttention, sourceAttention } from "../src/features/personal-workspace/attention-details";
import { normalizePersonalHomeModel, type WorkspaceAttention } from "../src/features/personal-workspace/personal-workspace-model";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}
const source = {
  index: 1, todo_id: "todo_gate_one", done: false, status: "open", text: "Review change",
  task_class: "user_gate", note: "The chosen direction needs review", evidence: "review:bounded-check",
  blocks_agent: "worker-one", unblocks_todo_id: "todo_target",
  decision_scope: { schema_version: "decision_scope_v0", kind: "direction", granularity: "action", scope_key: "route-one" },
};
// Production Zod parse must preserve the public projection facts before mapping.
const details = attentionDetails(todoItemSchema.parse(source));
assert(details.reason === source.note && details.evidence === source.evidence, "reason/evidence survived schema");
assert(details.blocksAgent === "worker-one" && details.unblocksTodoId === "todo_target", "exact relationships survive schema");
assert(details.decisionScope?.scopeKey === "route-one", "scope survives schema");
assert(details.interaction === "decision", "typed user gate is decision");
const row: WorkspaceAttention = { blocking: true, goalId: "goal-one", todoId: "todo_gate_one", sourceId: "source-a", text: source.text, details };
const model = normalizePersonalHomeModel({ blockingTodoCount: 1, goals: [], openUserTodoCount: 1, userTodos: [row], attentionHistory: [row] });
assert(model.attentionHistory?.[0].details?.decisionScope?.scopeKey === "route-one", "workspace normalization retains details/history");
assert(canReviewAttention(row), "open gate retains governed preview");
for (const task_class of ["user_action", undefined, "unknown_future_class"]) {
  const item = { ...row, details: attentionDetails({ ...source, task_class, text: "Please authorize production", note: "read approval required" }) };
  assert(item.details.interaction === "unknown", "prose never classifies interaction");
  assert(canReviewAttention(item), "existing ordinary or legacy preview preserved without granting authority");
}
assert(attentionDetails({ ...source, done: true, status: "deferred" }).lifecycle === "deferred", "explicit deferral overrides checked legacy marker");
for (const status of ["done", "deferred", "closed", "completed", "archived"]) {
  const item = { ...row, details: attentionDetails({ ...source, status }) };
  assert(!canReviewAttention(item), "inactive row cannot preview");
}
const replacement = { ...row, todoId: "todo_gate_two", text: "Review change" };
const old = { ...row, details: attentionDetails(todoItemSchema.parse({ ...source, superseded_by: "todo_gate_two", done: true })) };
assert(old.details.lifecycle === "superseded", "supersession takes precedence over completion");
assert(!canReviewAttention(old), "superseded cannot preview");
assert(attentionSuccessor(old, [{ ...replacement, goalId: "other" }]) === undefined, "same title and id in other Goal not a successor");
assert(attentionSuccessor(old, [{ ...replacement, sourceId: "source-b" }]) === undefined, "cross-source link rejected");
assert(attentionSuccessor(old, [replacement]) === replacement, "exact source and Goal successor linked");
assert(attentionSuccessor({ ...old, details: { ...old.details, supersededBy: "todo_gate_one" } }, [row]) === undefined, "self-cycle not linked");
assert(refreshAttention(row, [old]).details?.lifecycle === "superseded", "selection refreshes terminal facts");
const missing = refreshAttention(row, []);
assert(missing.details?.lifecycle === "unavailable" && !canReviewAttention(missing), "missing is unknown availability, not completion");
assert(refreshAttention(row, [{ ...row, sourceId: "source-b" }]).details?.lifecycle === "unavailable", "source switch fences old selection");
assert(attentionDetails({ ...source, decision_scope: { kind: "direction" } }).decisionScope === null, "partial scope is unknown");
assert(attentionDetails({}).lifecycle === "unknown", "missing state never means open or completed");
assert(source.status === "open" && !source.done, "projection has no mutation side effects");
console.log("attention-details-smoke: ok");

const failedSource = sourceAttention(row, "source-a", false, "Current Goal title");
assert(failedSource.goalTitle === "Current Goal title", "current source preserves display title");
assert(failedSource.details?.lifecycle === "unavailable" && !canReviewAttention(failedSource), "retained row from failed source cannot preview");
const healthySource = sourceAttention(row, "source-b", true, "Healthy Goal");
assert(canReviewAttention(refreshAttention(healthySource, [failedSource, healthySource])), "another source failure cannot fence healthy source");
const healthyGoal = sourceAttention({ ...row, goalId: "healthy-goal" }, "source-a", true);
assert(canReviewAttention(refreshAttention(healthyGoal, [failedSource, healthyGoal])), "another Goal read failure cannot fence healthy Goal");

const longRequest = "Review the public release evidence. ".repeat(12) + "Publish version 2.0 to stable only after acceptance.";
const longSource = todoItemSchema.parse({ ...source, text: longRequest });
const longRow = { ...row, text: "Release review", details: attentionDetails(longSource) };
const longModel = normalizePersonalHomeModel({ blockingTodoCount: 1, goals: [], openUserTodoCount: 1, userTodos: [longRow], attentionHistory: [longRow] });
assert(longModel.userTodos[0]?.details?.requestText === longRequest, "App decision detail retains the object after the short card label");
assert(longModel.userTodos[0]?.details?.evidence === source.evidence, "App decision evidence retained with the full request");

const markup = renderToStaticMarkup(createElement(WorkspaceI18nProvider, null, createElement(AttentionDetailCard, { item: longModel.userTodos[0] })));
assert(markup.includes("Publish version 2.0 to stable only after acceptance."), "rendered App detail contains object beyond compact label");
assert(markup.includes(source.evidence), "rendered App detail contains evidence");
