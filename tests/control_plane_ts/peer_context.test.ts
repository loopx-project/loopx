import assert from "node:assert/strict";
import test from "node:test";
import { requirePeerContextAccess } from "../../loopx/control_plane/collaboration/peer_context.ts";

const source = "lark:fixture-request";
const grant = {
  mode: "context_only", source_id: source,
  targets: ["builder", "reviewer"].map(agent_id => ({ goal_id: "delivery", agent_id })),
};
const external = {
  conversation: { channel_id: "manager.external.fixture", goal_id: "delivery", origin: "unknown" },
  source_id: source, goal_id: "delivery", agent_ids: ["builder", "reviewer"], grant,
};

test("owner context keeps its existing private scope without an external grant", () => {
  for (const channel_id of ["manager", "goal.delivery"]) {
    assert.deepEqual(requirePeerContextAccess({
      ...external, conversation: { channel_id, goal_id: "delivery", origin: "web" }, grant: null,
    }), { allowed: true });
  }
});

test("external context requires the original source and every recipient", () => {
  assert.deepEqual(requirePeerContextAccess(external), { allowed: true });
  for (const change of [
    { grant: { ...grant, source_id: "lark:another-request" } },
    { grant: { ...grant, mode: "unavailable" } },
    { grant: { ...grant, targets: [grant.targets[0]] } },
    { grant: { ...grant, targets: [grant.targets[1]] } },
    { goal_id: "other" }, { agent_ids: ["builder", "reviewer", "analyst"] },
    { conversation: { channel_id: "unknown" } },
  ]) assert.throws(() => requirePeerContextAccess({ ...external, ...change }));
});

test("malformed observations cannot masquerade as a grant", () => {
  for (const change of [{ grant: null }, { grant: { ...grant, targets: [null] } },
    { agent_ids: [] }, { agent_ids: [""] }, { agent_ids: "reviewer" }, { source_id: "" }]) {
    assert.throws(() => requirePeerContextAccess({ ...external, ...change }));
  }
});
