import assert from "node:assert/strict";
import test from "node:test";
import {rewardMemoryWriteValue, rewardMemoryScopeDraft} from "./reward-memory-settings.ts";

test("scope changes discard a write-only path and use the selected route state", () => {
  const current = {enabled_agents: ["reviewer"], agent_bindings: [{agent_id: "writer", enabled: false}]};
  assert.deepEqual(rewardMemoryScopeDraft(current, "writer"), {agent_id: "writer", config_path: "", enabled: false, enabled_agents: ["reviewer"]});
  assert.equal(rewardMemoryScopeDraft(current, "reviewer").enabled, true);
  assert.equal(rewardMemoryScopeDraft(current, "").config_path, "");
});

test("targeted disable and enable cannot serialize a sibling allowlist or stale path", () => {
  assert.deepEqual(rewardMemoryWriteValue({agent_id: "writer", enabled: false, enabled_agents: ["reviewer"], config_path: "old.json"}), {agent_id: "writer", enabled: false});
  assert.deepEqual(rewardMemoryWriteValue({agent_id: "writer", enabled: true, enabled_agents: ["reviewer"], config_path: ""}), {agent_id: "writer", enabled: true});
  assert.deepEqual(rewardMemoryWriteValue({enabled_agents: ["reviewer"], enabled: false}), {enabled_agents: ["reviewer"]});
});
