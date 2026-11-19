import assert from "node:assert/strict";
import {fetchExploreResults} from "../src/data/chat";

// The saved-finding transport used to strip the backend's quarantine reason.
// A measured, quarantined and unmeasured finding must remain distinguishable.
const row = {finding_id: "legacy", finding: "Recorded finding", summary: "Original observation",
  status: "confirmed", node_id: "question", question: "Compare candidates", scope: "Fixed input",
  agent_id: "recorder", evidence_refs: ["result:original"], last_updated_at: "2026-01-01T00:00:00Z",
  linked_todos: []};
const measurement = {metric: "quality", value: 8, unit: "points", higher_is_better: true,
  comparison_scope: "validation:fixed", input_revision: "input:v1", evaluation_ref: "method:v1",
  candidate_ref: "candidate:a", evidence_ref: "result:original"};
const reason = "measurements must cite the executed candidate's result reference";
const previousFetch = globalThis.fetch;
try {
  globalThis.fetch = async () => new Response(JSON.stringify({ok: true, goal_id: "research", total: 3,
    next_cursor: null, items: [{...row, measurement_error: reason},
      {...row, finding_id: "measured", measurements: [measurement]}, {...row, finding_id: "unmeasured"}]}),
    {headers: {"Content-Type": "application/json"}});
  const page = await fetchExploreResults("research");
  assert.equal(page.items[0]!.measurement_error, reason, "Quarantine reason reaches the existing reader");
  assert.equal(page.items[0]!.measurements, undefined, "Quarantined quantities stay absent");
  assert.equal(page.items[0]!.status, "confirmed", "The transport does not rewrite the recorded finding status");
  assert.equal(page.items[1]!.measurements?.[0]?.value, 8, "Valid quantities retain their value");
  assert.equal(page.items[1]!.measurement_error, undefined);
  assert.equal(page.items[2]!.measurements, undefined);
  assert.equal(page.items[2]!.measurement_error, undefined, "Unmeasured is not a quarantine error");
} finally {
  globalThis.fetch = previousFetch;
}
console.log("PASS saved finding measurement diagnostics, valid values and unmeasured parity");
