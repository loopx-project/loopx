import assert from "node:assert/strict";
import test from "node:test";
import {selectRewardMemoryBinding, planRewardMemoryBinding, patchRewardMemoryBinding} from "../../loopx/control_plane/capabilities/reward_memory_binding.ts";

const shared = {enabled: true, experimental: true, config_path: ".loopx/config/shared.json", enabled_agents: ["reviewer", "writer"]};
const privateBinding = {enabled: true, experimental: true, config_path: ".loopx/config/writer.json", enabled_agents: ["writer"]};

test("legacy selection is unchanged; exact override wins, including invalid or disabled entries", () => {
  assert.deepEqual(selectRewardMemoryBinding({policy: shared, agent_id: "writer"}), {policy: shared});
  const policy = {...shared, agent_bindings: {writer: privateBinding}};
  const selected = selectRewardMemoryBinding({policy, agent_id: "writer"});
  assert.deepEqual(selected.policy, privateBinding);
  assert.equal(selected.binding_agent_id, "writer");
  assert.match(String(selected.binding_revision), /^sha256:[0-9a-f]{64}$/);
  assert.notEqual(selectRewardMemoryBinding({policy: {...policy, agent_bindings: {writer: {...privateBinding, config_digest: "sha256:changed"}}}, agent_id: "writer"}).binding_revision, selected.binding_revision);
  assert.deepEqual(selectRewardMemoryBinding({policy, agent_id: "reviewer"}), {policy});
  for (const entry of [null, false, "invalid", {enabled: false}]) {
    const selected = selectRewardMemoryBinding({policy: {...shared, agent_bindings: {writer: entry}}, agent_id: "writer"});
    assert.equal(selected.binding_agent_id, "writer");
    assert.notEqual(selected.policy, shared);
    assert.notEqual((selected.policy as {enabled?: boolean}).enabled, true);
  }
  assert.deepEqual(selectRewardMemoryBinding({policy: {...shared, agent_bindings: []}, agent_id: "writer"}).policy, {});
});

test("targeted changes cannot alter a sibling, default route or caller object", () => {
  const current = {...shared, agent_bindings: {reviewer: {enabled: false}}};
  const before = structuredClone(current);
  const applied = patchRewardMemoryBinding({current, agent_id: "writer", clear: false, binding: privateBinding}).policy as typeof current;
  assert.deepEqual(current, before);
  assert.deepEqual({...applied, agent_bindings: undefined}, {...current, agent_bindings: undefined});
  assert.deepEqual(applied.agent_bindings.reviewer, current.agent_bindings.reviewer);
  assert.deepEqual((applied.agent_bindings as Record<string, unknown>).writer, privateBinding);
  const disabled = patchRewardMemoryBinding({current: applied, agent_id: "writer", clear: true}).policy as typeof applied;
  assert.deepEqual((disabled.agent_bindings as Record<string, unknown>).writer, {enabled: false, experimental: true});
  assert.deepEqual(patchRewardMemoryBinding({current: applied, agent_id: null, clear: false, binding: {enabled: false}}).policy,
    {enabled: false, agent_bindings: applied.agent_bindings});
  assert.equal(patchRewardMemoryBinding({current: applied, agent_id: null, clear: true}).policy, null);
  assert.throws(() => patchRewardMemoryBinding({current: {...shared, agent_bindings: "invalid"}, agent_id: "writer", clear: true}));
});

test("independent targets require registered identity and unambiguous inputs before IO", () => {
  const request = {agent_id: "writer", registered_agents: ["writer", "reviewer"], requested_agents: null, config_requested: true, clear: false};
  assert.deepEqual(planRewardMemoryBinding(request), {agent_id: "writer", enabled_agents: ["writer"]});
  for (const patch of [{agent_id: "stranger"}, {requested_agents: ["reviewer"]}, {clear: true}, {agent_id: 42}]) {
    assert.throws(() => planRewardMemoryBinding({...request, ...patch}));
  }
  assert.deepEqual(planRewardMemoryBinding({...request, agent_id: null, requested_agents: ["reviewer"]}),
    {agent_id: null, enabled_agents: ["reviewer"]});
});

test("apply checks the declared binding and verified activation rather than preview observations", async () => {
  const {verifyRewardMemoryBindingReadback} = await import("../../loopx/control_plane/capabilities/reward_memory_binding.ts");
  const desired = {enabled: true, experimental: true, binding_revision: "sha256:one", enabled_agents: ["reviewer"],
    registered_agents: ["reviewer", "writer"], config_pointer_registered: true, enablement_verified_agents: ["reviewer"], effective_available: false};
  const actual = {...desired, enablement_verified_agents: ["reviewer", "writer"], effective_available: true};
  const request = {actual, desired, expected_verified_agents: ["writer"]};
  assert.equal(verifyRewardMemoryBindingReadback(request).verified, true);
  assert.equal(verifyRewardMemoryBindingReadback({...request, actual: {...actual, binding_revision: "sha256:other"}}).verified, false);
  assert.equal(verifyRewardMemoryBindingReadback({...request, actual: desired}).verified, false);
  assert.equal(verifyRewardMemoryBindingReadback({...request, actual: {...actual, enabled: false}}).verified, false);
});

test("read projection separates configured, recorded and effective bindings and includes the route owner in revision", async () => {
  const {projectRewardMemoryBindings} = await import("../../loopx/control_plane/capabilities/reward_memory_binding.ts");
  const observation = {agent_id: "writer", binding_source: "agent", enabled: true, configured_for_agent: true,
    status: "enablement_stale", available: false, binding_status: "drifted", config_pointer_registered: true,
    automatic_ingest: false, automatic_recall: false, receipt: {status: "verified", writability_verified: true, exact_readback_verified: true},
    binding_identity: ["writer", "writer", true, "private-pointer", "sha256:one", ["writer"]]};
  const request = {summary: {enabled_agents: ["reviewer"], experimental: true}, observations: [observation]};
  const projected = projectRewardMemoryBindings(request);
  assert.equal(projected.enabled, true);
  assert.equal(projected.effective_available, false);
  assert.equal(projected.binding_status, "drifted");
  assert.deepEqual(projected.recorded_verified_agents, ["writer"]);
  assert.deepEqual(projected.enablement_verified_agents, []);
  assert.deepEqual(projected.enabled_agents, ["reviewer"]);
  assert.ok(!JSON.stringify(projected).includes("private-pointer"));
  assert.notEqual(projectRewardMemoryBindings({...request, observations: [{...observation,
    binding_source: "goal", binding_identity: ["writer", null, true, "private-pointer", "sha256:one", ["writer"]]}]}).binding_revision, projected.binding_revision);
});
