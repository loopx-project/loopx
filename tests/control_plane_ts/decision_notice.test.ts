import assert from "node:assert/strict";
import {test} from "node:test";
import {projectDecisionNotice} from "../../loopx/control_plane/presentation/decision_notice.ts";

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
