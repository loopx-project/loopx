import assert from "node:assert/strict";
import test from "node:test";
import {HANDOFF_MODES, HANDOFF_MODE_PLAN_SCHEMA, planHandoffMode} from "../../loopx/control_plane/coordination/handoff_mode_policy.ts";
import {handoffQuiescence} from "../../loopx/control_plane/coordination/handoff_mode_facts.ts";

const at = "2026-09-20T00:00:00Z";
const claim = {todo_id: "todo_owned", claimed_by: "worker", done: false, status: "open", archive_state: "active"};
const lease = {schema_version: "task_lease_v0", todo_id: "todo_leased", owner: "worker",
  status: "active", expires_at: "2026-09-20T00:00:01Z"};

for (const previous of HANDOFF_MODES) for (const requested of HANDOFF_MODES) {
  for (const blockers of [false, true]) test(`${previous} -> ${requested}, blockers=${blockers}: canonical decision`, () => {
    const expected = requested === "legacy" ? "rejected" : previous === requested ? "no_change" : blockers ? "rejected" : "apply";
    const canonical = planHandoffMode({schema_version: HANDOFF_MODE_PLAN_SCHEMA,
      previous_mode: previous, requested_mode: requested,
      active_claimed_todo_ids: blockers ? [claim.todo_id] : [], active_lease_todo_ids: blockers ? [lease.todo_id] : []});
    assert.equal(canonical.outcome, expected);
  });
}

test("complete claim/lease facts retain every blocker including missing identities", () => {
  const result = handoffQuiescence([claim, {...claim, todo_id: null}, {...claim, done: true},
    {...claim, archive_state: "archive"}, {...claim, claimed_by: "  "}],
    [lease, {...lease, todo_id: null}, {...lease, status: "released"}, {...lease, expires_at: at}], at);
  assert.deepEqual(result.claimed_todos.map(row => row.todo_id), ["todo_owned", null]);
  assert.deepEqual(result.active_leases.map(row => row.todo_id), ["todo_leased", null]);
  assert.throws(() => handoffQuiescence([], [{...lease, schema_version: "future"}], at), /schema/);
  assert.throws(() => handoffQuiescence([], [{...lease, expires_at: "garbage"}], at));
  assert.throws(() => handoffQuiescence([], [], "tomorrow"));
  assert.throws(() => handoffQuiescence([{...claim, claimed_by: 123}], [], at));
});
