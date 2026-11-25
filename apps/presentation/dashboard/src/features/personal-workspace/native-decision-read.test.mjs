import assert from "node:assert/strict";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";
import {runRecordSchema} from "../../data/status";
import {ContextDrawer} from "./context-drawer";
import {GoalStrategyDocument} from "./goal-source-strategies";
import {WorkspaceI18nProvider} from "./i18n";
import {projectGoalSourceStatus} from "../../../../../../loopx/control_plane/presentation/goal_source_links.ts";
const base = {goal_id:"archived", generated_at:"2026-01-01T00:00:00Z"};
for (const agent_vision of [undefined, null, [], "invalid", 42]) {
  assert.equal(runRecordSchema.parse({...base, agent_vision}).agent_vision ?? null, null);
}
const value = {vision_patch:{vision_summary:"Retain the independently verified baseline", future_context:"Preserve the recorded caveat"},
  path_delta:{observed_reality:"The new route failed", unresolved_questions:["Cost remains unknown"]},
  stride_decision:{
    axes:{work_slice:{plan:"Compare before replacing",reason:"Preserve the counterexample"}},
    next_probe:{scope:"Compare both original routes", future_scope:{seen:0, accepted:false}},
    previous_review:{lesson:"The earlier record did not verify adoption", future_review:"Retain the limitation"},
  },
  future_field:{unknown_key:"Keep future evidence readable"}};
const parsed = runRecordSchema.parse({...base, agent_vision:value});
assert.deepEqual(parsed.agent_vision,value);
for (const zh of [true,false]) {
  const html = renderToStaticMarkup(React.createElement(GoalStrategyDocument, {
    value:parsed.agent_vision,recordedAt:base.generated_at,version:"retained-version",
    originalGoalId:"original-goal",namespace:"source-a",zh,
  }));
  for (const text of ["Retain the independently verified baseline","The new route failed","Compare before replacing",
    "Preserve the counterexample","Keep future evidence readable","original-goal","source-a","retained-version"]) assert.ok(html.includes(text),text);
  const [reading, raw] = html.split('<details class="research-node-provenance">');
  for (const text of [value.vision_patch.vision_summary, value.path_delta.observed_reality,
    value.path_delta.unresolved_questions[0], value.stride_decision.next_probe.scope, value.stride_decision.previous_review.lesson]) {
    assert.equal(reading.split(text).length - 1, 1, `Repeat-free reading for ${text}`);
    assert.ok(raw.includes(text), `Complete original record for ${text}`);
  }
  const extraFields = reading.match(/<summary>(?:其他决策字段|Additional decision fields)<\/summary>(.*?)<\/details>/s)?.[1] ?? "";
  for (const text of ["Preserve the recorded caveat","Compare before replacing","Retain the limitation","Keep future evidence readable"]) {
    assert.ok(extraFields.includes(text), `Additional native field remains readable: ${text}`);
  }
  assert.match(extraFields, /<dt>seen<\/dt><dd><span>0<\/span>/);
  assert.match(extraFields, /<dt>accepted<\/dt><dd><span>false<\/span>/);
  assert.deepEqual(parsed.agent_vision, value, "Reading the document does not mutate the decision");
}
const structuredOnly = renderToStaticMarkup(React.createElement(GoalStrategyDocument, {
  value:{vision_patch:{vision_summary:"An entirely structured decision"}},
  recordedAt:base.generated_at,version:"retained-version",originalGoalId:"original-goal",namespace:"source-a",zh:false,
}));
assert.equal((structuredOnly.match(/<details\b/g) ?? []).length, 1, "No empty additional fields disclosure");
console.log("Native decisions preserve optional compatibility, complete fields and provenance in both languages");

// The original document must survive the real source routing and schema path.
for (const workspaceId of ["original-solver", "case-a"]) {
  const decision = {goal_id:"original-solver",
    vision_patch:{vision_summary:"Keep the native record"},
    future_field:{goal_id:"original-solver", nested:[{goal_id:"original-solver"}]}};
  const native = {...base, goal_id:"original-solver", agent_vision:decision};
  const snapshot = structuredClone(native);
  const projected = projectGoalSourceStatus({
    status:{goal_filter:"original-solver",run_history:{goals:[{id:"original-solver",latest_runs:[native]}]}},
    row:{id:workspaceId,parent_goal_id:"parent",source_access:{original_goal_id:"original-solver"}},
  });
  const run = runRecordSchema.parse(projected.run_history.goals[0].latest_runs[0]);
  assert.equal(run.goal_id, workspaceId, "workspace routing still uses the local alias");
  assert.deepEqual(run.agent_vision, decision, "native decision identities stay original");
  assert.deepEqual(native, snapshot, "native record remains unchanged");
  for (const zh of [true,false]) {
    const html = renderToStaticMarkup(React.createElement(GoalStrategyDocument, {
      value:run.agent_vision,recordedAt:base.generated_at,version:"native-version",
      originalGoalId:"original-solver",namespace:"source-a",zh,
    }));
    assert.ok(html.includes("original-solver"));
    assert.ok(html.includes("future_field"));
    assert.ok(!html.includes("case-a"), "workspace alias cannot enter the original document");
  }
}
console.log("Native source decisions preserve original identities through alias and non-alias projection, schema and renderer");

const run = {
  runId: "execution-1",
  goalId: "archived",
  goalTitle: "Benchmark case",
  agentId: "worker",
  agentLabel: "Worker",
  status: "completed",
  title: "Original execution",
  latestActivity: "Recorded an assessment",
  completedSteps: 1,
  totalSteps: 1,
};
const archivedGoal = {
  goalId: run.goalId,
  sourceAccess: {label: "Retained snapshot", original_goal_id: "original-solver"},
  sourceRunHistory: {total: 1, recent: [{...base, agent_vision: value}]},
};
const renderDrawer = (selection, goals = []) => renderToStaticMarkup(
  React.createElement(WorkspaceI18nProvider, null, React.createElement(ContextDrawer, {
    agents: [], callbacks: {}, goals, onClose: () => {}, selection,
  })),
);
const diagnosticsTrigger = /<button[^>]+class="personal-diagnostics-trigger"/;
const archivedDrawer = renderDrawer({kind: "run", item: run}, [archivedGoal]);
assert.match(archivedDrawer, /class="personal-detail-card personal-source-run-history"/);
assert.ok(archivedDrawer.includes(value.vision_patch.vision_summary));
assert.doesNotMatch(archivedDrawer, /personal-run-drawer-tabs/);
assert.doesNotMatch(archivedDrawer, diagnosticsTrigger, "Archived records have their own provenance");

const currentDrawer = renderDrawer({kind: "run", item: run});
assert.match(currentDrawer, /personal-run-drawer-tabs/);
assert.match(currentDrawer, diagnosticsTrigger, "Current executions retain live diagnostics");

const proposalDrawer = renderDrawer({
  kind: "proposal",
  item: {goalId: run.goalId, previewId: "preview-1", title: "Apply a change", actionKind: "goal.lifecycle", status: "ready", fields: []},
});
assert.match(proposalDrawer, diagnosticsTrigger);
const scheduleDrawer = renderDrawer({
  kind: "schedule",
  item: {goalId: run.goalId, scheduleId: "schedule-1", label: "Refresh the report", scheduleKind: "heartbeat"},
});
assert.match(scheduleDrawer, diagnosticsTrigger);
const outputDrawer = renderDrawer({
  kind: "output",
  item: {goalId: run.goalId, outputId: "output-1", title: "Assessment notes", safePreview: "Recorded evidence"},
});
assert.doesNotMatch(outputDrawer, diagnosticsTrigger);
console.log("Archived executions expose source provenance and live drawers retain diagnostics where applicable");
