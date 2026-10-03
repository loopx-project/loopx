import assert from "node:assert/strict";
import test from "node:test";
import { configureSourceRecipient, resolveSourceRecipients } from "../../loopx/control_plane/collaboration/source_grants.ts";

const worker = { goal_id: "research", agent_id: "worker" };
const peer = { goal_id: "research", agent_id: "peer" };
const other = { goal_id: "other", agent_id: "worker" };
const available = [worker, peer, other];
const source = { sender_ids: ["owner"], targets: [{ goal_id: "research" }] };

test("a managed Goal includes current and future registered Agents, never another Goal", () => {
  assert.deepEqual(resolveSourceRecipients({ sender_id: "owner", source, available: [worker, other] }), { targets: [worker] });
  assert.deepEqual(resolveSourceRecipients({ sender_id: "owner", source, available }), { targets: [peer, worker] });
  assert.deepEqual(resolveSourceRecipients({ sender_id: "owner", source, available: [] }), { targets: [] });
  assert.deepEqual(resolveSourceRecipients({ sender_id: "owner", source: { ...source, targets: [worker] }, available }), { targets: [worker] });
});

test("specific revocation overrides a Goal grant and survives another Goal grant", () => {
  const params = { source, goal_id: "research", agent_id: "peer", available,
    active_goal_ids: ["research", "other"], grant: false };
  const revoked = configureSourceRecipient(params);
  assert.deepEqual(resolveSourceRecipients({ sender_id: "owner", source: revoked.source, available }), { targets: [worker] });
  assert.equal(configureSourceRecipient({ ...params, source: revoked.source }).would_change, false);
  const renewed = configureSourceRecipient({ ...params, source: revoked.source, agent_id: null, grant: true });
  assert.deepEqual(resolveSourceRecipients({ sender_id: "owner", source: renewed.source, available }), { targets: [worker] });
  const restored = configureSourceRecipient({ ...params, source: revoked.source, grant: true });
  assert.deepEqual(resolveSourceRecipients({ sender_id: "owner", source: restored.source, available }), { targets: [peer, worker] });
  assert.deepEqual(resolveSourceRecipients({ sender_id: "owner", source, available }), { targets: [peer, worker] });
});

test("Goal revocation also removes individual grants, and permits revocation after unregistering", () => {
  const revoked = configureSourceRecipient({ source: { ...source, targets: [...source.targets, worker, other] },
    goal_id: "research", agent_id: null, grant: false, available: [], active_goal_ids: [] });
  assert.deepEqual(resolveSourceRecipients({ sender_id: "owner", source: revoked.source, available }), { targets: [other] });
});

test("operator configuration requires an existing sender, active membership and explicit read scope", () => {
  const params = { source, goal_id: "research", agent_id: null, grant: true, available,
    active_goal_ids: ["research"] };
  assert.equal(configureSourceRecipient(params).would_change, false);
  for (const change of [
    { source: { ...source, sender_ids: [] } }, { active_goal_ids: [] },
    { agent_id: "unregistered" }, { goal_id: "unregistered" },
    { source: { ...source, evidence_goal_ids: ["other"] } },
    { source: { ...source, evidence_goal_ids: "research" } },
  ]) assert.throws(() => configureSourceRecipient({ ...params, ...change }));
  // Read access alone never becomes a delegation grant.
  assert.deepEqual(resolveSourceRecipients({ sender_id: "owner", source: { sender_ids: ["owner"], evidence_goal_ids: ["research"] }, available }), { targets: [] });
});

test("malformed optional Agent identity cannot widen a grant to the whole Goal", () => {
  for (const bad of [null, "", 7, false]) {
    assert.throws(() => resolveSourceRecipients({ sender_id: "owner", source: { ...source, targets: [{ goal_id: "research", agent_id: bad }] }, available }));
  }
  for (const bad of [null, {}, [null], [{ goal_id: "research" }]]) {
    assert.throws(() => resolveSourceRecipients({ sender_id: "owner", source: { ...source, blocked_targets: bad }, available }));
  }
  for (const change of [{ sender_id: "another-person" }, { sender_id: "" },
    { source: { ...source, sender_ids: "owner" } }, { source: { ...source, targets: null } }]) {
    assert.throws(() => resolveSourceRecipients({ sender_id: "owner", source, available, ...change }));
  }
});
