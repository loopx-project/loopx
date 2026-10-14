import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {projectExploreTurnContext} from "../../loopx/control_plane/capabilities/explore_turn_context.ts";

for (const includeDigests of [false, true]) {
  test(`paged and audited findings retain complete source digests: ${includeDigests}`, () => {
    const findings = Array.from({length: 20}, (_, index) => ({
      finding_id: `finding-${index}`, node_id: `node-${index}`,
      finding: `Scoped finding ${index}`, status: "confirmed",
      summary: "Applicable only to the tested inputs. ".repeat(20),
      evidence_refs: [`validation:case-${index}`],
      tags: index < 10 ? ["writeback-result"] : [],
    }));
    const request = {
      goal_id: "research", agent_id: "worker", route: ["loopx"],
      graph_enabled: true, harness_gate: {enabled: true},
      include_finding_digests: includeDigests, result_limit: 2,
      projection: {nodes: [], findings},
      plan: {selected_branches: [{todo_id: "audit-old-finding", typed_evidence_audit: {
        requested_node_refs: ["node-19"],
        findings: [{finding_id: "finding-19", node_id: "node-19",
          finding: "Compact audit text", status: "confirmed"}],
      }}]},
    };
    const first = projectExploreTurnContext(request);
    const page = projectExploreTurnContext({...request, result_offset: 2,
      result_revision: (first.graph as JsonObject).result_page.revision});
    const graph = page.graph as JsonObject;
    const audit = (page.harness as JsonObject).selected_branches[0].typed_evidence_audit;
    assert.deepEqual(graph.recent_findings.map((row: JsonObject) => row.finding_id),
      ["finding-0", "finding-1", "finding-2"]);
    assert.deepEqual(graph.writeback_results.map((row: JsonObject) => row.finding_id),
      ["finding-2", "finding-3"]);
    assert.equal(audit.findings[0].finding_id, "finding-19");
    for (const row of [...graph.recent_findings, ...graph.writeback_results, ...audit.findings]) {
      const source = findings.find(finding => finding.finding_id === row.finding_id)!;
      if (includeDigests) {
        assert.equal(row.source_sha256,
          createHash("sha256").update(JSON.stringify(source)).digest("hex"));
      } else {
        assert.equal(Object.hasOwn(row, "source_sha256"), false);
      }
    }
    // The audit omits this source text; a new read must still bind its revision.
    findings[19].summary += "Additional applicability constraint.";
    const updated = projectExploreTurnContext(request);
    const updatedFinding = (updated.harness as JsonObject).selected_branches[0].typed_evidence_audit.findings[0];
    if (includeDigests) {
      assert.notEqual(updatedFinding.source_sha256, audit.findings[0].source_sha256);
      assert.equal(updatedFinding.source_sha256,
        createHash("sha256").update(JSON.stringify(findings[19])).digest("hex"));
    } else {
      assert.deepEqual(updatedFinding, audit.findings[0]);
    }
  });
}
