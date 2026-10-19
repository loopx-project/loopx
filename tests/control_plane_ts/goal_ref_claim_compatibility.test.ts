import assert from "node:assert/strict";
import test from "node:test";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import {executeCoordinationTodoClaim} from "../../loopx/control_plane/coordination/todo_claim.ts";

test("GoalRef-bearing legacy claims reject before provider or historical receipt access", async () => {
  const store = new Proxy({} as AuthorityStore, {get() {throw new Error("provider must not be accessed");}});
  const base = {goal_id: "goal", todo_id: "todo", claimed_by: "agent", actor_agent_id: "agent",
    registered_agents: ["agent"], expected_role: "agent", operation_id: "old-operation", dry_run: false,
    now: new Date("2026-09-29T00:00:00Z")};
  for (const goalRef of [null, "invalid", {goal_id: "goal", goal_instance_id: "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]) {
    const request = {...base, goal_ref: goalRef};
    const result = await executeCoordinationTodoClaim(store, request);
    assert.equal(result.status, "failed");
    assert.equal(result.reason_code, "goal_ref_claim_contract_unqualified");
    assert.equal(result.failure_kind, "protocol_failure");
  }
});
