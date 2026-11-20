import assert from "node:assert/strict";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";
import {runRecordSchema} from "../../data/status";
import {GoalStrategyDocument} from "./goal-source-strategies";
import {projectGoalSourceStatus} from "../../../../../../loopx/control_plane/presentation/goal_source_links.ts";
const base = {goal_id:"archived", generated_at:"2026-01-01T00:00:00Z"};
for (const agent_vision of [undefined, null, [], "invalid", 42]) {
  assert.equal(runRecordSchema.parse({...base, agent_vision}).agent_vision ?? null, null);
}
const value = {vision_patch:{vision_summary:"Retain the independently verified baseline"},
  path_delta:{observed_reality:"The new route failed", unresolved_questions:["Cost remains unknown"]},
  stride_decision:{axes:{work_slice:{plan:"Compare before replacing",reason:"Preserve the counterexample"}}},
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
}
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
