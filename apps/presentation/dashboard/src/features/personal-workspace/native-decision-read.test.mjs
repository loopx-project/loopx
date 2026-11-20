import assert from "node:assert/strict";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";
import {runRecordSchema} from "../../data/status";
import {GoalStrategyDocument} from "./goal-source-strategies";
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
