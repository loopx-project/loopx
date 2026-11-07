import assert from "node:assert/strict";
import test from "node:test";
import {settlementIdentity, settlementIdentityFromPlan, settlementIdentityPayload} from "../../loopx/control_plane/effect_program.ts";

const subject = {request_id: "a".repeat(64), request_revision: 1,
  request_digest: "b".repeat(64), execution_scope_sha256: "c".repeat(64)};
const scope = {goal_id: "fixture-goal", agent_id: "receiver", turn_instance_id: "original-turn"};

test("ordinary request identity preserves the original revision and execution scope", () => {
  const identity = settlementIdentity({...scope, request_subject: subject});
  assert.equal(identity.binding_kind, "request");
  assert.equal(identity.todo_id, null);
  assert.equal(identity.replan_obligation_id, null);
  const wire = settlementIdentityPayload(identity);
  assert.equal(wire.schema_version, "quota_settlement_identity_v1");
  assert.deepEqual(wire.request_subject, subject);
  const parsed = settlementIdentityFromPlan({settlement_plan: {identity: wire}});
  assert.equal(parsed.failure, null);
  assert.equal(parsed.value?.effect_id, identity.effect_id);
  for (const change of [{request_revision: 2}, {request_id: "d".repeat(64)},
    {request_digest: "d".repeat(64)}, {execution_scope_sha256: "d".repeat(64)}]) {
    assert.notEqual(settlementIdentity({...scope, request_subject: {...subject, ...change}}).effect_id,
      identity.effect_id);
  }
});

test("a request cannot impersonate a Todo or planning obligation, or change a retained effect", () => {
  assert.throws(() => settlementIdentity({...scope, todo_id: "todo_existing", request_subject: subject}));
  assert.throws(() => settlementIdentity({...scope, replan_obligation_id: "replan-aaaaaaaaaaaaaaaa", request_subject: subject}));
  const wire = settlementIdentityPayload({...scope, request_subject: subject});
  for (const request_subject of [{...subject, request_revision: true}, {...subject, request_revision: 0},
    {...subject, request_revision: 1.5}, {...subject, extra: "authority"}, {...subject, request_digest: "missing"}]) {
    const parsed = settlementIdentityFromPlan({settlement_plan: {identity: {...wire, request_subject}}});
    assert.equal(parsed.failure?.kind, "invalid_identity");
  }
  const changed = settlementIdentityFromPlan({settlement_plan: {identity: {
    ...wire, request_subject: {...subject, request_revision: 2}}}});
  assert.equal(changed.failure?.kind, "invalid_identity");
});
