import assert from "node:assert/strict";
import test from "node:test";
import {admitGoalDraft, normalizeGoalDraft} from "../../loopx/control_plane/collaboration/goal_draft.ts";

const draft = {objective: " Research public cash flows ", completion_criteria: "", execution_boundary: "Public sources only", question: "Which period?", options: ["Three years", "One year", "Three years"]};

test("partial goal drafts preserve unknowns and offer editable suggestions", () => {
  const normalized = normalizeGoalDraft(draft);
  assert.deepEqual(normalized, {...draft, objective: "Research public cash flows", options: ["Three years", "One year"]});
  assert.equal(draft.options.length, 3);
  assert.deepEqual(normalizeGoalDraft({...draft, question: ""})?.options, []);
});

test("drafts cannot carry execution authority or malformed provider data", () => {
  for (const value of [null, [], "draft", {...draft, objective: " "}, {...draft, options: "yes"}, {...draft, question: 1}, {...draft, completion_criteria: null}, {...draft, options: [false]}, {...draft, options: [" "]}, {...draft, agent_id: "lead"}, {...draft, permission: "workspace_write"}, {...draft, ready: true}]) {
    assert.equal(normalizeGoalDraft(value), null);
  }
});

test("bounded unicode drafts are admitted without truncating the owner's words", () => {
  assert.ok(normalizeGoalDraft({...draft, objective: "研".repeat(1000), options: ["🔬".repeat(300)]}));
  assert.equal(normalizeGoalDraft({...draft, objective: "研".repeat(1001)}), null);
  assert.equal(normalizeGoalDraft({...draft, options: ["🔬".repeat(301)]}), null);
  assert.equal(normalizeGoalDraft({...draft, options: Array(6).fill("yes")}), null);
});


test("existing work, protected operations and gates cannot also offer a new Goal", () => {
  assert.ok(admitGoalDraft({goal_draft: draft, proposals: [], context_handoff: null}));
  for (const conflict of [
    {context_handoff: {goal_id: "existing", agent_id: "owner"}},
    {context_handoff: {}}, {context_handoff: "invalid"},
    {protected_action: {operation: "merge", target: "#1"}},
    {gate: {kind: "binding_required"}}, {proposals: [{kind: "todo", text: "Continue"}]},
    {proposals: "malformed"},
  ]) assert.equal(admitGoalDraft({goal_draft: draft, ...conflict}), null);
});
