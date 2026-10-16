import assert from "node:assert/strict";
import test from "node:test";
import {
  planTodoFieldUpdate, TODO_FIELD_UPDATE_REQUEST_SCHEMA,
} from "../../loopx/control_plane/todos/field_update.ts";
import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";

const source = Object.freeze({todo_id: "todo_field_test", status: "open", claimed_by: "agent-a",
  completion_continuation: null, successor_todo_ids: [], completed_at: null});
function plan(intent: JsonObject = {}, todo: JsonObject = source): JsonObject {
  return planTodoFieldUpdate({schema_version: TODO_FIELD_UPDATE_REQUEST_SCHEMA,
    todo, intent: Object.freeze(intent), updated_at: "2026-09-01T00:00:00Z"});
}

test("omission, explicit empty collections and false remain distinct", () => {
  const {metadata_updates: updates} = plan({note: "", evidence: "Evidence", required_capabilities: [],
    no_followup: false, successor_todo_ids: [], monitor_metadata: {consecutive_no_change: 0, watch_only: false}});
  assert.deepEqual(updates, {todo_id: "todo_field_test", status: "open", evidence: "Evidence",
    required_capabilities: [], no_followup: false, successor_todo_ids: [], consecutive_no_change: 0, watch_only: false});
  assert.equal(source.claimed_by, "agent-a");
});

test("explicit clears and binding precedence are a single plan", () => {
  assert.deepEqual(plan({clear_claim: true, claimed_by: "agent-b", clear_user_binding: true,
    bound_agent: "agent-b", goal_bound: true, blocks_agent: "agent-a", clear_blocks_agent: true,
    global_gate: true, clear_global_gate: true, clear_resume_when: true}).metadata_updates, {
    todo_id: "todo_field_test", status: "open", claimed_by: null, bound_agent: null, goal_bound: null,
    blocks_agent: "agent-a", global_gate: null, resume_when: null, resume_monitor_generation: null,
  });
});

test("all lifecycle statuses have explicit timestamp behavior", () => {
  for (const status of ["open", "blocked", "deferred", "done"]) {
    const updates = plan({status, no_followup: true}).metadata_updates as JsonObject;
    assert.equal(updates.completed_at, status === "done" ? "2026-09-01T00:00:00Z" : null);
    assert.equal(updates.completion_continuation, status === "done" ? "no_followup" : undefined);
  }
  const completed = {...source, status: "done", completed_at: "2026-08-01T00:00:00Z"};
  assert.equal((plan({status: "done"}, completed).metadata_updates as JsonObject).completed_at, undefined);
});

test("resume generation belongs only to the monitor condition", () => {
  for (const [condition, generation] of [["monitor_changed:todo_monitor", 0], ["todo_done:todo_dependency", null],
    ["pr_merged:owner/repo#12", null], ["capacity_available:network", null]] as const) {
    const updates = plan({resume_when: condition, resume_monitor_generation: 0}).metadata_updates as JsonObject;
    assert.equal(updates.resume_when, condition);
    assert.equal(updates.resume_monitor_generation, generation);
  }
  assert.throws(
    () => plan({resume_when: "free text"}),
    /supported conditions are:.*todo_done:.*monitor_changed:.*pr_merged:.*capacity_available:/,
  );
  assert.throws(() => plan({resume_when: "todo_done:todo_dependency", clear_resume_when: true}), /not both/);
  assert.throws(() => plan({status: "deferred", clear_resume_when: true}), /cannot clear/);
});

test("completion composes existing state rules; finalization overrides are closed", () => {
  assert.equal((plan({status: "done", successor_todo_ids: ["todo_successor"]}).metadata_updates as JsonObject)
    .completion_continuation, "successor");
  assert.throws(() => plan({status: "done", successor_todo_ids: ["todo_successor"], no_followup: true}), /both/);
  const override = {completion_continuation: "no_followup", completion_recovery: "same_turn_terminal_closeout"};
  const updates = plan({status: "done", completion_metadata_updates_override: override}).metadata_updates as JsonObject;
  assert.equal(updates.completion_recovery, override.completion_recovery);
  assert.throws(() => plan({completion_metadata_updates_override: {claimed_by: "agent-b"}}), /shape mismatch/);
});

test("claim and removed-policy repair cannot evade the existing rules", () => {
  assert.throws(() => plan({claim_only: true, claimed_by: "agent-b"}), /already claimed_by/);
  assert.throws(() => plan({claim_only: true, status: "blocked"}), /requires status=open/);
  for (const removed of ["primary_review", "review_handoff"]) {
    const todo = {...source, removed_continuation_policy: removed};
    assert.throws(() => plan({claim_only: true}, todo), /repair it before claiming/);
    assert.throws(() => plan({continuation_policy: "independent_handoff", excluded_agents: []}, todo), /repair it explicitly/);
    assert.throws(() => plan({continuation_policy: "independent_handoff", excluded_agents: ["!!!"]}, todo), /repair it explicitly/);
    for (const field of ["text", "note"]) {
      assert.throws(() => plan({continuation_policy: "independent_handoff",
        excluded_agents: ["agent-a"], [field]: "Bundled edit"}, todo),
      /repair must be submitted without other Todo edits/);
    }
    const repaired = plan({continuation_policy: "independent_handoff", excluded_agents: ["agent-a"]}, todo);
    assert.equal((repaired.metadata_updates as JsonObject).continuation_policy, "independent_handoff");
  }
});

test("imported claims retain Python whitespace normalization and invalid-token tolerance", () => {
  const intent = {claim_only: true, claimed_by: "agent-a"};
  assert.doesNotThrow(() => plan(intent, {...source, claimed_by: "\u0085Agent\u001cA\u0085"}));
  assert.doesNotThrow(() => plan(intent, {...source, claimed_by: "!!!"}));
  assert.throws(() => plan(intent, {...source, claimed_by: "Agent B"}), /already claimed_by='agent-b'/);
});

test("unknown intent fields, wrong flag types and monitor authority injection are rejected or excluded", () => {
  for (const field of ["role", "todo_id", "lease_epoch", "last_actor_agent_id", "updated_at"]) {
    assert.throws(() => plan({[field]: "injected"}), /does not own/);
  }
  assert.throws(() => plan({clear_claim: "false"}), /must be a boolean/);
  assert.throws(() => plan({status: "superseded"}), /todo status/);
  assert.deepEqual(plan({monitor_metadata: {claimed_by: "agent-b", material_change: false}}).metadata_updates,
    {todo_id: "todo_field_test", status: "open", material_change: false});
});
