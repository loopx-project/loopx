import assert from "node:assert/strict";
import test from "node:test";
import {resolveExploreGoalConfiguration, normalizeResearchCompositionPolicy, researchIdentifier}
  from "../../loopx/control_plane/capabilities/explore_configuration.ts";

const resolve = resolveExploreGoalConfiguration;
test("machine preference inherits live while Goal modes retain priority", () => {
  const goal = {graph: {}, harness: {profile: "generic", composition_mode: "disabled"}};
  assert.equal(resolve(goal).mode, "off");
  for (const mode of ["off", "evidence", "planning"]) {
    const inherited = resolve({...goal, machine_mode: mode});
    assert.equal(inherited.mode, mode);
    assert.equal(inherited.mode_source, "machine_default");
    assert.equal(inherited.configuration.profile, "generic");
    assert.equal(inherited.goal_override.mode, undefined);
  }
  assert.deepEqual(goal.graph, {});
  assert.equal(resolve({harness: {enabled: false}, machine_mode: "planning"}).mode, "off");
  assert.equal(resolve({graph: {enabled: true}, machine_mode: "planning"}).mode, "evidence");
  const legacy = resolve({graph: {enabled: false}, harness: {enabled: true}, machine_mode: "off"});
  assert.equal(legacy.mode, "planning");
  assert.equal(legacy.evidence_enabled, true);
  assert.equal(legacy.mode_source, "goal_override");
  assert.throws(() => resolve({machine_mode: "unexpected"}), /explore_mode/);
});

test("composition configuration keeps explicit scope and strict activation", () => {
  assert.deepEqual(normalizeResearchCompositionPolicy({harness: {enabled: true}}), {
    schema_version: "research_composition_policy_v0", mode: "disabled", coverage_scope_id: null, enabled: false,
  });
  assert.deepEqual(normalizeResearchCompositionPolicy({harness: {enabled: true,
    composition_mode: "explicit_only", composition_scope_id: "scope-a"}}), {
    schema_version: "research_composition_policy_v0", mode: "explicit_only", coverage_scope_id: "scope-a", enabled: true,
  });
  assert.equal(normalizeResearchCompositionPolicy({harness: {enabled: "true",
    composition_mode: "explicit_only", composition_scope_id: "scope-a"}}).enabled, false);
  assert.throws(() => normalizeResearchCompositionPolicy({harness: {composition_mode: "explicit_only"}}), /composition_scope_id/);
  assert.throws(() => normalizeResearchCompositionPolicy({harness: {composition_mode: "disabled",
    composition_scope_id: "unsafe/path"}}), /opaque public-safe identifier/);
  for (const mode of [["disabled"], ["explicit_only"], [["explicit_only"]]]) {
    assert.throws(() => normalizeResearchCompositionPolicy({harness: {enabled: true,
      composition_mode: mode, composition_scope_id: "scope-a"}}), /composition_mode/);
  }
  const maximum = "A" + "a".repeat(127);
  assert.equal(researchIdentifier(maximum, "scope"), maximum);
  assert.throws(() => researchIdentifier(maximum + "a", "scope"), /opaque public-safe identifier/);
});
