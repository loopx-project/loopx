import assert from "node:assert/strict";
import {test} from "node:test";
import {projectDecisionNotice, validateDecisionNoticeReferences} from "../../loopx/control_plane/presentation/decision_notice.ts";

test("decision bodies outrank lossy scheduler labels and preserve distinct requests", () => {
  const body = "Review the public release candidate. ".repeat(9) + "Only publish after the signed build passes.";
  const result = projectDecisionNotice({requests: [
    {request_id: "todo_a", text: body, reason: "Choose the release channel", evidence: "https://example.org/release"},
    {request_id: "todo_b", text: body},
  ], actions: ["[P0] Release review"], question: "Approve?"});
  assert.deepEqual(result, {source: "request_items", items: [
    {request_id: "todo_a", text: body, reason: "Choose the release channel", evidence: "https://example.org/release"},
    {request_id: "todo_b", text: body, reason: "", evidence: ""},
  ]});
});

test("summary-only packets expose missing request content instead of a compatibility decision", () => {
  for (const input of [
    {requests: [{text: " "}], actions: ["Approve deployment"], question: "Approve?"},
    {question: "Approve?"},
  ]) assert.deepEqual(projectDecisionNotice(input), {source: "unavailable", items: []});
});

const full = {todo_id: "todo_one", role: "user", task_class: "user_action", status: "open", done: false,
  updated_at: "2026-10-01T06:00:00Z", text: "Review public release evidence. ".repeat(12) + "Expires at 2026-10-01T07:00:00Z.",
  note: "Learning review only, not permission to execute.", evidence: "https://example.org/release"};
const selected = {...full, request_id: full.todo_id, role: undefined, done: undefined,
  text: full.text.slice(0, 177) + "...", note: undefined, evidence: undefined};
const withSnapshot = (items: Record<string, unknown>[]) => ({goal_id: "goal-one", requests: [selected],
  request_snapshot: {goal_id: "goal-one", items}});

test("same-read canonical content replaces compact labels without changing membership", () => {
  const result = projectDecisionNotice(withSnapshot([full, {...full, todo_id: "todo_unselected"}]));
  assert.deepEqual(result, {source: "request_items", items: [{request_id: full.todo_id,
    text: full.text, reason: full.note, evidence: full.evidence}]});
});

test("source identity, version and lifecycle conflicts never supply decision terms", () => {
  for (const items of [[], [full, full], [{...full, todo_id: "todo_title_match"}],
    [{...full, goal_id: "other"}], [{...full, updated_at: "2026-10-01T06:01:00Z"}],
    [{...full, status: "deferred"}], [{...full, role: "agent"}], [{...full, task_class: "user_gate"}],
    [{...full, superseded_by: "todo_other"}]]) {
    const result = projectDecisionNotice(withSnapshot(items));
    assert.equal(result.source, "unavailable");
    assert.deepEqual(result.items, []);
    assert.deepEqual(result.incomplete, [{request_id: full.todo_id, reason_code: "source_mismatch"}]);
  }
  const crossGoal = withSnapshot([full]);
  crossGoal.request_snapshot.goal_id = "another-goal";
  assert.equal(projectDecisionNotice(crossGoal).source, "unavailable");
});

test("transport bounds reject the entire body instead of silently losing trailing terms", () => {
  for (const overflow of [{text: "x".repeat(901)}, {reason: "x".repeat(451)}, {evidence: "x".repeat(451)}]) {
    const result = projectDecisionNotice({requests: [{request_id: "todo_bound", text: "Release review", ...overflow}]});
    assert.deepEqual(result, {source: "unavailable", items: [], incomplete: [{request_id: "todo_bound", reason_code: "content_overflow"}]});
  }
  assert.equal(projectDecisionNotice({requests: [{text: "🌏".repeat(900), reason: "x".repeat(450)}]}).source, "request_items");
});

test("complete references allow punctuation and Markdown but reject identifier extensions", () => {
  const id = "todo_abcdef0123456789abcdef01";
  for (const text of [id, `(${id}).`, `（${id}）。`, `**${id}**`, "`" + id + "`",
    `[${id}](https://example.org/review)`, `请决定${id}。`]) {
    assert.deepEqual(validateDecisionNoticeReferences({text, requests: [{request_id: id}]}),
      {valid: true, missing_request_ids: []});
  }
  for (const text of [`prefix_${id}`, `${id}_other`, `prefix-${id}`, `${id}-other`,
    `x${id}x`, `${id}2`, `X${id}`, "Review the release."]) {
    assert.deepEqual(validateDecisionNoticeReferences({text, requests: [{request_id: id}]}),
      {valid: false, missing_request_ids: [id]});
  }
});

test("every complete request requires a literal reference rather than a partial match", () => {
  const requests = [{request_id: "todo_release"}, {request_id: "todo_icon"}];
  assert.deepEqual(validateDecisionNoticeReferences({text: "todo_release, todo_icon_other", requests}),
    {valid: false, missing_request_ids: ["todo_icon"]});
  assert.deepEqual(validateDecisionNoticeReferences({text: "todo_release; todo_icon.", requests}),
    {valid: true, missing_request_ids: []});
  assert.deepEqual(validateDecisionNoticeReferences({text: "(legacy.ref+1)", requests: [{request_id: "legacy.ref+1"}]}),
    {valid: true, missing_request_ids: []});
  assert.deepEqual(validateDecisionNoticeReferences({text: "(legacyXreff1)", requests: [{request_id: "legacy.ref+1"}]}),
    {valid: false, missing_request_ids: ["legacy.ref+1"]});
});
