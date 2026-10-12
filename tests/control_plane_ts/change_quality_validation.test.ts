import assert from "node:assert/strict";
import test from "node:test";
import {qualifyChangeQualityValidation} from "../../loopx/control_plane/capabilities/change_quality_validation.ts";

const digest = (char: string) => "sha256:" + char.repeat(64);
function request() {
  const scope = {base_commit: "b".repeat(40), head_commit: "c".repeat(40),
    scope_fingerprint: "d".repeat(64), changed_files: ["app.py"],
    sources: {committed: ["app.py"], staged: [], unstaged: [], untracked: []}};
  const observation = {exit_code: 1, failure_signature: digest("a"),
    fixture_digest: digest("b"), environment_digest: digest("c"), evidence_id: "base-run"};
  return {scope, validation: [
    {validator: "focused", status: "passed", required: true, covers_paths: ["app.py"]},
    {validator: "broad", status: "failed", required: false, command: "typecheck",
      failure_attribution: {schema_version: "change_quality_baseline_attribution_v0",
        disposition: "pre_existing_unrelated", base_revision: scope.base_commit,
        head_revision: scope.head_commit, scope_fingerprint: scope.scope_fingerprint,
        same_command: "typecheck", baseline_observation: observation,
        head_observation: {...observation, evidence_id: "head-run"},
        causal_scope_analysis: "The dependency's failing invariant is retained; changed behavior has independent coverage.",
        affected_invariant_evidence: ["validator:focused"]}},
  ]};
}

test("legacy qualification preserves failed/required and skipped semantics", () => {
  for (const required of [true, false]) for (const status of ["passed", "failed", "skipped"]) {
    const result = qualifyChangeQualityValidation({validation: [{validator: "check", status, required}]});
    assert.deepEqual(result.blocking_codes,
      status === "failed" || (status === "skipped" && required) ? ["validator:check"] : []);
    assert.deepEqual(result.risk_codes, status === "skipped" && !required ? ["validator:check"] : []);
  }
});

test("qualified optional baseline stays a failed observation and visible risk", () => {
  const result = qualifyChangeQualityValidation(request());
  assert.deepEqual(result.blocking_codes, []);
  assert.deepEqual(result.risk_codes, ["validator:broad"]);
  assert.equal((result.validation as Array<{status: string}>)[1].status, "failed");
});

test("required failures remain blocking with identical baseline evidence", () => {
  const input = request(); input.validation[1].required = true;
  assert.deepEqual(qualifyChangeQualityValidation(input).blocking_codes, ["validator:broad"]);
});

for (const mutation of ["dirty", "missing_scope", "same_run", "zero_exit", "signal", "invalid_digest",
  "aggregate_count", "unknown_field", "duplicate_refs", "unknown_path", "skipped", "same_revision"]) {
  test(`rejects ${mutation} attribution`, () => {
    const input: any = request();
    const attr = input.validation[1].failure_attribution;
    if (mutation === "dirty") input.scope.sources.untracked = ["app.py"];
    else if (mutation === "missing_scope") delete input.scope;
    else if (mutation === "same_run") attr.head_observation.evidence_id = "base-run";
    else if (mutation === "zero_exit") attr.head_observation.exit_code = 0;
    else if (mutation === "signal") attr.head_observation.exit_code = -9;
    else if (mutation === "invalid_digest") attr.head_observation.failure_signature = "sha256:short";
    else if (mutation === "aggregate_count") attr.head_observation.failure_signature = "12 failures";
    else if (mutation === "unknown_field") attr.non_blocking = true;
    else if (mutation === "duplicate_refs") attr.affected_invariant_evidence.push("validator:focused");
    else if (mutation === "unknown_path") input.validation[0].covers_paths.push("other.py");
    else if (mutation === "skipped") input.validation[1].status = "skipped";
    else if (mutation === "same_revision") {
      input.scope.base_commit = input.scope.head_commit; attr.base_revision = attr.head_revision;
    }
    assert.throws(() => qualifyChangeQualityValidation(input));
  });
}
