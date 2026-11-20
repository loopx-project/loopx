import assert from "node:assert/strict";
import test from "node:test";
import {decodeRoomClaimRequest, admitRoomClaimCallback} from "../../loopx/control_plane/goals/room_claim_request.ts";

const request = () => ({schema_version: "loopx_room_claim_request_v0", command: "claim_todo",
  goal_id: "goal-fixture", actor_id: "agent-a", todo_id: "todo_fixture", expected_revision: "fixture:1",
  idempotency_key: "claim-fixture", authorized_principals: ["lark:ou_fixture"], expires_at: "2030-01-01T00:00:00Z"});
const admission = (patch = {}) => ({request: request(), scope_state: "active", principal: "lark:ou_fixture",
  observed_at: "2026-01-01T00:00:00Z", ...patch});

test("claim scope never comes from room membership or recalled fields", () => {
  assert.equal(admitRoomClaimCallback(admission()).allowed, true);
  assert.equal(admitRoomClaimCallback(admission()).execution_authority_granted, false);
  assert.equal(admitRoomClaimCallback(admission({request: {...request(), authorized_principals: ["im:operator-a"]}, principal: "im:operator-a"})).allowed, true);
  assert.equal(admitRoomClaimCallback(admission({principal: "lark:ou_other"})).reason_code, "principal_not_authorized");
  assert.equal(admitRoomClaimCallback(admission({scope_state: "revoked"})).reason_code, "offer_revoked");
  assert.equal(admitRoomClaimCallback(admission({observed_at: "2030-01-01T00:00:00Z"})).reason_code, "offer_expired");
});

test("input boundary rejects broad commands, excess principals and ambiguous expiry", () => {
  for (const patch of [{command: "execute"}, {authorized_principals: []}, {authorized_principals: ["*"]},
    {authorized_principals: ["lark:ou_fixture", "lark:ou_fixture"]},
    {expires_at: "2030-01-01"}, {expected_revision: ""}, {recalled_approval: true}]) {
    assert.throws(() => decodeRoomClaimRequest({...request(), ...patch}));
  }
  assert.throws(() => admitRoomClaimCallback(admission({scope_state: "implicitly_approved"})));
});
