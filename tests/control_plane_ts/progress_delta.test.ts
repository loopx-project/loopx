import assert from "node:assert/strict";
import test from "node:test";
import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";
import { projectProgressDelta } from "../../loopx/control_plane/work_items/progress_delta.ts";
import { projectReplanWriteback } from "../../loopx/control_plane/work_items/replan_semantics.ts";

// Digests are opaque codec outputs: the native policy must compare, never recompute them.
function observation(digest: number, patch: JsonObject = {}): JsonObject {
  return {schema_version: "typed_progress_observation_v0", work_item_id: "todo-progress",
    surface_id: "surface-auth", hypothesis_id: "hypothesis-boundary", probe_kind: "probe-route-map",
    result_class: "unchanged", evidence_ids: ["evidence-route-map"],
    fingerprint: digest.toString(16).padStart(16, "0"), ...patch};
}
function source(current: JsonObject, baseline: JsonObject | null = null, window: JsonObject[] = []): JsonObject {
  return {current, baseline, window};
}
function unproved(observation: JsonObject): JsonObject {
  const {evidence_ids, ...withoutEvidence} = observation;
  return withoutEvidence;
}
const baseline = observation(1);

test("missing progress remains a two-field refusal; new dimensions have stable output order", () => {
  assert.deepEqual(projectProgressDelta(null), {accepted: false, reason: "typed progress observation missing"});
  const current = observation(2, {result_class: "advanced"});
  assert.deepEqual(projectProgressDelta(source(current)), {
    schema_version: "replan_semantic_delta_v0", accepted: true,
    delta_kinds: ["new_surface", "new_hypothesis", "new_probe_family"],
    evidence_novel: true, observation_repeated: false, blocker_repeated: false,
    window_size: 0, observation_fingerprint: current.fingerprint, baseline_fingerprint: null,
    reason: "typed observation changes the replan frontier",
  });
});

test("probe novelty and evidence novelty remain distinct across the complete obligation window", () => {
  const probe = observation(2, {result_class: "advanced", probe_kind: "probe-permission-graph"});
  const evidence = observation(3, {result_class: "advanced", evidence_ids: ["evidence-new-output"]});
  assert.deepEqual(projectProgressDelta(source(probe, baseline)).delta_kinds, ["new_probe_family"]);
  assert.equal(projectProgressDelta(source(probe, baseline)).evidence_novel, false);
  const evidenceOnly = projectProgressDelta(source(evidence, baseline));
  assert.equal(evidenceOnly.accepted, false);
  assert.equal(evidenceOnly.evidence_novel, true);
  assert.equal(evidenceOnly.reason, "typed observation does not change an accepted semantic dimension");
  assert.equal(projectProgressDelta(source(probe)).evidence_novel, true);
  const claimed = [evidence, probe, evidence];
  const replayed = projectProgressDelta(source(evidence, baseline, claimed));
  assert.equal(replayed.evidence_novel, false);
  assert.equal(replayed.observation_repeated, true);
  assert.equal(replayed.window_size, 3, "duplicate window rows still count");
  assert.equal(replayed.baseline_fingerprint, baseline.fingerprint);
  const fresh = observation(4, {result_class: "advanced", evidence_ids: ["evidence-unseen"]});
  const novel = projectProgressDelta(source(fresh, baseline, claimed));
  assert.equal(novel.evidence_novel, true);
  assert.equal(novel.observation_repeated, false);
  assert.deepEqual(claimed, [evidence, probe, evidence], "projection preserves source ordering");
});

test("the bound external-review baseline distinguishes identity changes, proof churn and replay", () => {
  const prior = observation(1, {result_class: "advanced", hypothesis_id: "hypothesis-2"});
  const proof = {...prior, fingerprint: "0000000000000002", evidence_ids: ["evidence-fresh"]};
  const hypothesis = {...proof, fingerprint: "0000000000000003", hypothesis_id: "hypothesis-next"};
  const renamed = {...prior, fingerprint: "0000000000000004", hypothesis_id: "hypothesis-renamed"};
  assert.equal(projectProgressDelta(source(prior, prior)).accepted, false);
  assert.equal(projectProgressDelta(source(prior, prior)).evidence_novel, false);
  assert.equal(projectProgressDelta(source(proof, prior)).accepted, false);
  assert.equal(projectProgressDelta(source(proof, prior)).evidence_novel, true);
  assert.equal(projectProgressDelta(source(hypothesis, prior)).accepted, true);
  const renamedDelta = projectProgressDelta(source(renamed, prior));
  assert.deepEqual(renamedDelta.delta_kinds, ["new_hypothesis"]);
  assert.equal(renamedDelta.evidence_novel, false);
  assert.equal(projectProgressDelta(source(prior)).accepted, true);
});

test("concrete blockers require evidence and a changed identifier; only historical blocked results repeat", () => {
  const prior = observation(1, {result_class: "blocked", blocker_id: "blocker-permission"});
  const current = observation(2, {result_class: "blocked", blocker_id: "blocker-missing-runtime"});
  assert.equal(projectProgressDelta(source(prior, prior)).accepted, false);
  assert.deepEqual(projectProgressDelta(source(current, prior)).delta_kinds, ["new_concrete_blocker"]);
  assert.equal(projectProgressDelta(source(unproved(current), prior)).accepted, false);
  assert.equal(projectProgressDelta(source(current, baseline, [current])).blocker_repeated, true);
  assert.equal(projectProgressDelta(source(current, {...current, result_class: "advanced"})).blocker_repeated, false);
  assert.equal(projectProgressDelta(source(current, null, [{...current, result_class: "unchanged"}])).blocker_repeated, false);
  const externalBaseline = observation(3, {result_class: "advanced"});
  assert.equal(projectProgressDelta(source(current, externalBaseline)).accepted, true);
});

test("exhaustion requires complete coverage, and completing prior coverage changes its semantics", () => {
  const incomplete = observation(1, {result_class: "exploration_exhausted",
    coverage_scope_id: "scope-public-entrypoints", coverage_complete: false});
  const complete = {...incomplete, fingerprint: "0000000000000002", coverage_complete: true};
  assert.equal(projectProgressDelta(source(incomplete)).accepted, false);
  for (const prior of [null, incomplete]) {
    assert.deepEqual(projectProgressDelta(source(complete, prior)).delta_kinds,
      ["coverage_backed_exploration_exhausted"]);
  }
});

for (const [result, fields, kind] of [
  ["exploration_exhausted", {coverage_complete: true}, "coverage_backed_exploration_exhausted"],
  ["no_followup", {}, "coverage_backed_no_followup"],
] as const) {
  test(`${result} requires a new coverage scope rather than refreshed evidence`, () => {
    const prior = observation(1, {result_class: result, coverage_scope_id: "scope-public-entrypoints", ...fields});
    const proof = {...prior, fingerprint: "0000000000000002", evidence_ids: ["evidence-new-output"]};
    const scope = {...prior, fingerprint: "0000000000000003", coverage_scope_id: "scope-public-cli"};
    assert.equal(projectProgressDelta(source({...prior}, prior)).accepted, false);
    assert.equal(projectProgressDelta(source(proof, prior)).accepted, false);
    assert.deepEqual(projectProgressDelta(source(scope, prior)).delta_kinds, [kind]);
    assert.equal(projectProgressDelta(source(unproved(scope), prior)).accepted, false);
    assert.equal(projectProgressDelta(source(observation(4, {result_class: result, ...fields}), prior)).accepted, false);
  });
}

test("terminal class changes count, while no-followup ignores complete-flag churn", () => {
  const exhausted = observation(1, {result_class: "exploration_exhausted",
    coverage_scope_id: "scope-public-entrypoints", coverage_complete: true});
  const terminal = observation(2, {result_class: "no_followup", coverage_scope_id: "scope-public-entrypoints"});
  assert.deepEqual(projectProgressDelta(source(terminal, exhausted)).delta_kinds, ["coverage_backed_no_followup"]);
  assert.equal(projectProgressDelta(source({...terminal, coverage_complete: true},
    {...terminal, coverage_complete: false})).accepted, false);
});

test("the effect entrypoint consumes normalized source and refuses caller-supplied outcomes", () => {
  const obligation = {triggers: [{kind: "external_progress_review_drift"}]};
  const qualify = (progress_source: JsonObject | null, patch: JsonObject = {}) =>
    projectReplanWriteback({operation: "qualify", obligation, progress_source, ...patch});
  const renamed = observation(2, {result_class: "advanced", hypothesis_id: "hypothesis-renamed"});
  assert.equal(qualify(null).accepted, false);
  assert.equal(qualify(source(renamed, baseline)).reason_code, "progress_identity_without_new_evidence");
  const fresh = {...renamed, fingerprint: "0000000000000003", evidence_ids: ["evidence-new"]};
  const accepted = qualify(source(fresh, baseline));
  assert.equal(accepted.accepted, true);
  assert.deepEqual(accepted.satisfying_outcomes, ["new_hypothesis"]);
  assert.equal(accepted.observation_fingerprint, fresh.fingerprint);
  assert.equal(qualify(source(fresh, baseline, [fresh])).reason_code, "progress_observation_replayed");
  assert.throws(() => qualify(null, {observation_delta: {delta_kinds: ["new_hypothesis"]}}), /precomputed/);
  assert.throws(() => projectReplanWriteback({operation: "qualify", obligation}), /progress_source/);
  assert.deepEqual(projectReplanWriteback({operation: "requirements", obligation}).required_any_of,
    ["fresh_vision_path_outcome", "new_surface", "new_hypothesis", "new_probe_family", "new_runnable_successor",
      "coverage_backed_exploration_exhausted", "new_concrete_blocker", "coverage_backed_no_followup"]);
});

test("the native source boundary rejects malformed normalized facts", () => {
  for (const patch of [{schema_version: "legacy"}, {result_class: "looks_advanced"}, {fingerprint: "raw-text"},
    {hypothesis_id: "unsupported prose"}, {coverage_complete: "true"}, {evidence_ids: [1]},
    {work_item_id: "unsupported prose"}, {work_item_id: "w".repeat(129)}]) {
    assert.throws(() => projectProgressDelta(source({...baseline, ...patch})), /progress|result_class|fingerprint|hypothesis|coverage|evidence|work_item/);
  }
  for (const invalid of [{current: baseline, baseline: null}, {current: baseline, window: []},
    {current: null, baseline: null, window: []}]) {
    assert.throws(() => projectProgressDelta(invalid), /progress/);
  }
});

for (const [label, evidence] of [
  ["empty list", []], ["empty identifier", [""]], ["prose identifier", ["look at the same route"]],
  ["more than twelve identifiers", Array.from({length: 13}, (_, index) => `evidence-${index}`)],
] as const) {
  test(`the actual writeback entrypoint rejects ${label} evidence`, () => {
    const current = observation(2, {result_class: "advanced", hypothesis_id: "hypothesis-new", evidence_ids: evidence});
    assert.throws(() => projectReplanWriteback({operation: "qualify",
      obligation: {triggers: [{kind: "external_progress_review_drift"}]},
      progress_source: source(current, baseline)}), /evidence_ids/);
  });
}

test("fresh Vision cannot bypass missing attribution, while a false coverage flag remains compatible", () => {
  const carrier = {schema_version: "typed_progress_observation_v0", result_class: "unchanged",
    fingerprint: "0000000000000001"};
  const qualify = (current: JsonObject) => projectReplanWriteback({operation: "qualify",
    obligation: {triggers: [{kind: "external_progress_review_drift"}]}, progress_source: source(current),
    agent_vision: {vision_patch: {acceptance_summary: "Retain the evidenced path"},
      path_delta: {outcome: "continue", evidence_refs: ["evidence-path"]}}});
  for (const current of [carrier, {...carrier, unknown_identity: "todo-progress"}]) {
    assert.throws(() => qualify(current), /stable work, coverage or evidence field/);
  }
  const compatible = qualify({...carrier, coverage_complete: false});
  assert.equal(compatible.accepted, true);
  assert.deepEqual(compatible.satisfying_outcomes, ["fresh_vision_path_outcome"]);
});
