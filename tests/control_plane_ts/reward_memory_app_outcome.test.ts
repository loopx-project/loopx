import assert from "node:assert/strict";
import test from "node:test";
import { bindRewardMemoryAppOutcome, rewardMemoryAppOutcomeIdentity } from "../../loopx/control_plane/capabilities/reward_memory_app_outcome.ts";

const identity = {goal_id: "goal", agent_id: "writer", todo_id: "todo_a", turn_instance_id: "turn-a", effect_id: "effect:a"};
const policy = {enabled: true, experimental: true, config_path: "private.json", config_digest: "sha256:a", enabled_agents: ["writer"],
  automation: {automatic_ingest: true}, enablement_receipts: {writer: {status: "verified", config_digest: "sha256:a", writability_verified: true, exact_readback_verified: true}}};
const request = {identity, policy, reflection_digest: "sha256:reflection", validation_declaration_sha256: "validator-a", revalidating: false};

test("the lookup key owns one exact settlement, independently of stage success", () => {
  const expected = rewardMemoryAppOutcomeIdentity({identity});
  assert.deepEqual(rewardMemoryAppOutcomeIdentity({identity: {...identity}}), expected);
  assert.equal(bindRewardMemoryAppOutcome(request).candidate_id, expected.candidate_id);
  for (const field of Object.keys(identity)) {
    assert.notEqual(rewardMemoryAppOutcomeIdentity({identity: {...identity, [field]: "other"}}).candidate_id, expected.candidate_id);
    assert.throws(() => rewardMemoryAppOutcomeIdentity({identity: {...identity, [field]: ""}}));
  }
});

test("replay rejects identity, reflection, binding and validator drift", () => {
  const previous = bindRewardMemoryAppOutcome(request);
  assert.deepEqual(bindRewardMemoryAppOutcome({...request, previous}), previous);
  for (const field of Object.keys(previous)) {
    assert.throws(() => bindRewardMemoryAppOutcome({...request, previous: {...previous, [field]: "other"}}), /mismatch/);
  }
  for (const changed of [
    {...policy, config_digest: "sha256:replacement"},
    {...policy, config_path: "replacement.json"},
    {...policy, enabled: false},
    {...policy, enabled_agents: []},
    {...policy, automation: {automatic_ingest: false}},
    {...policy, binding_revision: "new-route"},
    {...policy, enablement_receipts: {}},
  ]) assert.throws(() => bindRewardMemoryAppOutcome({...request, policy: changed, previous}), /binding_fingerprint/);
  assert.throws(() => bindRewardMemoryAppOutcome({...request, previous, validation_declaration_sha256: "validator-b"}), /validation_declaration/);
  assert.doesNotThrow(() => bindRewardMemoryAppOutcome({...request, previous, validation_declaration_sha256: "validator-b", revalidating: true}));
  // Revalidation never permits a different reflection or a replacement route.
  assert.throws(() => bindRewardMemoryAppOutcome({...request, previous, reflection_digest: "other", revalidating: true}), /reflection_digest/);
  assert.throws(() => bindRewardMemoryAppOutcome({...request, previous, policy: {...policy, config_path: "other.json"}, revalidating: true}), /binding_fingerprint/);
});

test("other Agents' declarations do not invalidate this candidate", () => {
  const previous = bindRewardMemoryAppOutcome(request);
  assert.deepEqual(bindRewardMemoryAppOutcome({...request, previous, policy: {...policy,
    enabled_agents: ["writer", "reviewer"], enablement_receipts: {...policy.enablement_receipts, reviewer: {status: "changed"}}}}), previous);
});
