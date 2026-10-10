import assert from "node:assert/strict";
import test from "node:test";
import { configureSourceRecipient, configureSourceScope, resolveSourceRecipients, sourceExecutionBindings } from "../../loopx/control_plane/collaboration/source_grants.ts";

const worker = { goal_id: "research", agent_id: "worker" };
const peer = { goal_id: "research", agent_id: "peer" };
const other = { goal_id: "other", agent_id: "worker" };
const available = [worker, peer, other];
const source = { local_delivery_scope: "selected", sender_ids: ["owner"], targets: [{ goal_id: "research" }] };

test("execution needs an independent exact grant, current sender and both registered identities", () => {
  const binding = {...worker, binding_id: "review", requester_agent_id: "peer"};
  const params = {source, sender_id: "owner", available};
  assert.deepEqual(sourceExecutionBindings(params), {bindings: []});
  const granted = {...source, execution_bindings: [binding]};
  assert.deepEqual(sourceExecutionBindings({...params, source: granted}), {bindings: [binding]});
  assert.deepEqual(sourceExecutionBindings({...params, source: granted, available: [worker]}), {bindings: []});
  assert.deepEqual(sourceExecutionBindings({...params, source: {...granted, blocked_targets: [worker]}}), {bindings: []});
  assert.throws(() => sourceExecutionBindings({...params, source: granted, sender_id: "other"}));
  for (const bad of [null, {}, [binding, binding], [{...binding, requester_agent_id: "worker"}],
    [{...binding, workspace: "/injected"}], [{...binding, binding_id: "../escape"}]]) {
    assert.throws(() => sourceExecutionBindings({...params, source: {...source, execution_bindings: bad}}));
  }
});

test("operator scope repair shares the existing source policy and preserves revocations", () => {
  const policy = {...source, blocked_targets: [peer], evidence_goal_ids: ["research"]};
  const result = configureSourceScope({source: policy, local_delivery_scope: "all_registered"});
  assert.deepEqual(resolveSourceRecipients({sender_id: "owner", source: result.source, available}), {targets: [other, worker]});
  assert.deepEqual((result.source as typeof policy).evidence_goal_ids, policy.evidence_goal_ids);
  assert.equal(configureSourceScope({source: result.source, local_delivery_scope: "all_registered"}).would_change, false);
  for (const bad of [{local_delivery_scope: "all"}, {source: {...policy, sender_ids: []}},
    {source: {...policy, sender_ids: [" "]}}, {source: {...policy, blocked_targets: null}}]) {
    assert.throws(() => configureSourceScope({source: policy, local_delivery_scope: "all_registered", ...bad}));
  }
});

test("a selected managed Goal includes current and future registered Agents, never another Goal", () => {
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
  assert.deepEqual(resolveSourceRecipients({ sender_id: "owner", source: { local_delivery_scope: "selected", sender_ids: ["owner"], evidence_goal_ids: ["research"] }, available }), { targets: [] });
});

test("malformed optional Agent identity cannot widen a grant to the whole Goal", () => {
  for (const bad of [null, "", 7, false]) {
    assert.throws(() => resolveSourceRecipients({ sender_id: "owner", source: { ...source, targets: [{ goal_id: "research", agent_id: bad }] }, available }));
  }
  for (const bad of [null, {}, [null], [{ agent_id: "worker" }]]) {
    assert.throws(() => resolveSourceRecipients({ sender_id: "owner", source: { ...source, blocked_targets: bad }, available }));
  }
  for (const change of [{ sender_id: "another-person" }, { sender_id: "" },
    { source: { ...source, sender_ids: "owner" } }, { source: { ...source, targets: null } }]) {
    assert.throws(() => resolveSourceRecipients({ sender_id: "owner", source, available, ...change }));
  }
});


test("sender-bound source defaults to every registered local recipient, including new Goals", () => {
  const policy = { sender_ids: ["owner"] };
  assert.deepEqual(resolveSourceRecipients({ sender_id: "owner", source: policy, available: [worker] }), { targets: [worker] });
  assert.deepEqual(resolveSourceRecipients({ sender_id: "owner", source: policy, available }), { targets: [other, peer, worker] });
  // Existing enrollment does not narrow the new default. Selected mode is explicit.
  assert.deepEqual(resolveSourceRecipients({ sender_id: "owner", source: { ...source, local_delivery_scope: "all_registered" }, available }), { targets: [other, peer, worker] });
  for (const value of [null, "", "all", false]) assert.throws(() => resolveSourceRecipients({ sender_id: "owner", source: { ...source, local_delivery_scope: value }, available }));
  assert.throws(() => resolveSourceRecipients({ sender_id: "visitor", source: policy, available }));
  assert.throws(() => resolveSourceRecipients({ sender_id: "owner", source: { evidence_goal_ids: ["research"] }, available }));
});

test("default local access keeps exact and Goal revocations across future registrations", () => {
  const policy = { sender_ids: ["owner"] };
  const params = { source: policy, goal_id: "research", agent_id: "peer", available, active_goal_ids: ["research", "other"], grant: false };
  const revoked = configureSourceRecipient(params);
  assert.equal(revoked.granted_before, true);
  assert.deepEqual(resolveSourceRecipients({ sender_id: "owner", source: revoked.source, available }), { targets: [other, worker] });
  const renewed = configureSourceRecipient({ ...params, source: revoked.source, agent_id: null, grant: true });
  assert.deepEqual(resolveSourceRecipients({ sender_id: "owner", source: renewed.source, available }), { targets: [other, worker] });
  const blockedGoal = configureSourceRecipient({ ...params, source: renewed.source, agent_id: null });
  const newcomer = { goal_id: "research", agent_id: "newcomer" };
  assert.deepEqual(resolveSourceRecipients({ sender_id: "owner", source: blockedGoal.source, available: [...available, newcomer] }), { targets: [other] });
  assert.equal(configureSourceRecipient({ ...params, source: blockedGoal.source, agent_id: null }).would_change, false);
  const restoredGoal = configureSourceRecipient({ ...params, source: blockedGoal.source, agent_id: null, grant: true });
  assert.deepEqual(resolveSourceRecipients({ sender_id: "owner", source: restoredGoal.source, available: [...available, newcomer] }), { targets: [other, newcomer, worker] });
  const restoredAgent = configureSourceRecipient({ ...params, source: restoredGoal.source, grant: true });
  assert.deepEqual(resolveSourceRecipients({ sender_id: "owner", source: restoredAgent.source, available }), { targets: [other, peer, worker] });
});

test("an Agent revocation while its Goal is disabled survives Goal restoration", () => {
  for (const policy of [{ sender_ids: ["owner"] }, source]) {
    const params = { source: policy, goal_id: "research", agent_id: null, available,
      active_goal_ids: ["research", "other"], grant: false };
    const disabledGoal = configureSourceRecipient(params);
    const disabledAgent = configureSourceRecipient({ ...params, source: disabledGoal.source, agent_id: "peer" });
    assert.equal(disabledAgent.granted_before, false);
    assert.equal(disabledAgent.would_change, true);
    assert.equal(configureSourceRecipient({ ...params, source: disabledAgent.source, agent_id: "peer" }).would_change, false);
    const restoredGoal = configureSourceRecipient({ ...params, source: disabledAgent.source, grant: true });
    assert.deepEqual(resolveSourceRecipients({ sender_id: "owner", source: restoredGoal.source, available }),
      { targets: policy === source ? [worker] : [other, worker] });
    const restoredAgent = configureSourceRecipient({ ...params, source: restoredGoal.source, agent_id: "peer", grant: true });
    assert.deepEqual(resolveSourceRecipients({ sender_id: "owner", source: restoredAgent.source, available }),
      { targets: policy === source ? [peer, worker] : [other, peer, worker] });
  }
});
