import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { AttentionDetailCard } from "../src/features/personal-workspace/attention-detail-card";
import { WorkspaceI18nProvider } from "../src/features/personal-workspace/i18n";
import { todoItemSchema } from "../src/data/status";
import { attentionDetails, attentionDetailsFromSnapshot, attentionSuccessor, canDecideAttention, canHandleUserAction, canReviewAttention, nextMorningResumeWhen, refreshAttention, sourceAttention } from "../src/features/personal-workspace/attention-details";
import { parseTodoResumeCondition } from "../src/features/personal-workspace/todo-resume-condition";
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
  // Only the typed task_class classifies; approval wording never makes a decision.
  assert(item.details.interaction === (task_class === "user_action" ? "user_action" : "unknown"), "prose never classifies interaction");
  assert(!canDecideAttention(item), "approval wording never grants approve/reject");
  assert(canReviewAttention(item), "existing ordinary or legacy preview preserved without granting authority");
  assert(canHandleUserAction(item) === (task_class === "user_action"), "only a typed User action is handled as done/defer/cancel");
}
// A User action is handled on its own Todo; it needs the stable id the owner validates.
const action = { ...row, details: attentionDetails({ ...source, task_class: "user_action" }) };
assert(!canHandleUserAction({ ...row, details: attentionDetails(source) }), "a User gate is decided, not marked done");
const { todo_id: _omitted, ...idless } = source;
assert(!canHandleUserAction({ ...action, details: attentionDetails({ ...idless, task_class: "user_action" }) }), "missing todo_id cannot be written");
assert(!canHandleUserAction({ ...action, decisionSource: "run_operator_gate" }), "run operator gate is never a User action");
for (const inactive of [{ status: "deferred" }, { status: "done", done: true }, { superseded_by: "todo_next" }]) {
  assert(!canHandleUserAction({ ...action, details: attentionDetails({ ...source, task_class: "user_action", ...inactive }) }), "inactive User action is not writable");
}
assert(!canHandleUserAction(refreshAttention(action, [])), "unobservable User action is not writable");
// The defer preset is tomorrow 09:00 local, in a form the resume-condition owner accepts.
for (const [now, day] of [["2026-10-04T19:13:00", "2026-10-05"], ["2026-10-04T00:30:00", "2026-10-05"],
  ["2026-01-31T23:59:00", "2026-02-01"], ["2026-12-31T10:00:00", "2027-01-01"]] as const) {
  const local = new Date(now);
  const condition = nextMorningResumeWhen(local);
  assert(condition.startsWith(`resume_at:${day}T09:00:00`), `defer preset is the next calendar day at 09:00: ${condition}`);
  assert(parseTodoResumeCondition(condition) === condition.toLowerCase(), "defer preset is a supported resume condition");
  const target = new Date(Number(day.slice(0, 4)), Number(day.slice(5, 7)) - 1, Number(day.slice(8, 10)), 9);
  assert(Date.parse(condition.slice("resume_at:".length)) === target.getTime(), "offset names the target instant");
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

// Exercise the actual compact project_asset -> same QueueItem.user_todos join,
// not a fixture that already supplies a complete body to attentionDetails.
const canonical = todoItemSchema.parse({...source, role: "user", task_class: "user_action",
  updated_at: "2026-10-01T06:00:00Z", text: longRequest,
  note: "Only a learning review; expires at 2026-10-01T07:00:00Z."});
const compact = todoItemSchema.parse({...canonical, text: `${longRequest.slice(0, 217)}...`, note: null, evidence: null});
const joined = attentionDetailsFromSnapshot(compact, [canonical], row.goalId);
assert(joined.requestText === longRequest && joined.reason === canonical.note && joined.evidence === canonical.evidence, "same revision restores full display content");
assert(joined.interaction === "user_action" && joined.lifecycle === "open", "enrichment cannot reclassify a user action as a gate");
const joinedMarkup = renderToStaticMarkup(createElement(WorkspaceI18nProvider, null,
  createElement(AttentionDetailCard, {item: {...row, text: compact.text, details: joined}})));
assert(joinedMarkup.includes("Publish version 2.0 to stable only after acceptance.")
  && joinedMarkup.includes("expires at 2026-10-01T07:00:00Z."), "rendered compact-path detail retains trailing terms and note");
for (const candidates of [[], [canonical, canonical], [{...canonical, goal_id: "other-goal"}],
  [{...canonical, updated_at: "2026-10-01T06:01:00Z"}], [{...canonical, status: "deferred"}],
  [{...canonical, done: true}], [{...canonical, role: "agent"}], [{...canonical, task_class: "user_gate"}],
  [{...canonical, superseded_by: "todo_other"}], [{...canonical, todo_id: "todo_same_title"}],
  [{...canonical, text: "A different request with the same title"}]]) {
  const rejected = attentionDetailsFromSnapshot(compact, candidates, row.goalId);
  assert(rejected.requestText === null && rejected.reason === null, "missing/conflicting details remain summary-only");
  assert(rejected.lifecycle === "open" && rejected.interaction === "user_action", "failed enrichment preserves selected lifecycle and authority");
}
const authoritySource = {...canonical, blocks_agent: "different-agent", decision_scope: {kind: "trade", granularity: "goal", scope_key: "different"}};
const displayOnly = attentionDetailsFromSnapshot(compact, [authoritySource], row.goalId);
assert(displayOnly.blocksAgent === compact.blocks_agent && displayOnly.decisionScope?.scopeKey === "route-one", "richer source cannot replace authority fields");
assert(attentionDetailsFromSnapshot({...compact, updated_at: null}, [canonical], row.goalId).requestText === null, "missing revision fails closed");
