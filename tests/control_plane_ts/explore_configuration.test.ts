import assert from "node:assert/strict";
import test from "node:test";
import {resolveExploreGoalConfiguration} from "../../loopx/control_plane/capabilities/explore_configuration.ts";

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
