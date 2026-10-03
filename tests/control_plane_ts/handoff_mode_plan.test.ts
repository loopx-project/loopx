import assert from "node:assert/strict";
import test from "node:test";
import {EXECUTION_HANDOFF_MODES, HANDOFF_MODES, HANDOFF_MODE_PLAN_SCHEMA, planHandoffMode} from "../../loopx/control_plane/coordination/handoff_mode_policy.ts";
import {handoffQuiescence} from "../../loopx/control_plane/coordination/handoff_mode_facts.ts";
import {LEGACY_HANDOFF_PLAN_SCHEMA, planLegacyHandoffMode, patchHandoffMode} from "../../loopx/control_plane/coordination/handoff_mode_legacy_plan.ts";

const at = "2026-09-20T00:00:00Z";
const claim = {todo_id: "todo_owned", claimed_by: "worker", done: false, status: "open", archive_state: "active"};
const lease = {schema_version: "task_lease_v0", todo_id: "todo_leased", owner: "worker",
  status: "active", expires_at: "2026-09-20T00:00:01Z"};
const request = {schema_version: LEGACY_HANDOFF_PLAN_SCHEMA, frontmatter_text: "---\n---\n## Agent Todo\n",
  previous_value: null, requested_mode: "hard_lease", todos: [], leases: [], observed_at: at};

for (const previous of HANDOFF_MODES) for (const requested of HANDOFF_MODES) {
  for (const blockers of [false, true]) test(`${previous} -> ${requested}, blockers=${blockers}: legacy/canonical decision parity`, () => {
    const expected = requested === "legacy" ? "rejected" : previous === requested ? "no_change" : blockers ? "rejected" : "apply";
    const legacy = planLegacyHandoffMode({...request, previous_value: previous, requested_mode: requested,
      todos: blockers ? [claim] : [], leases: blockers ? [lease] : []});
    const canonical = planHandoffMode({schema_version: HANDOFF_MODE_PLAN_SCHEMA,
      previous_mode: previous, requested_mode: requested,
      active_claimed_todo_ids: blockers ? [claim.todo_id] : [], active_lease_todo_ids: blockers ? [lease.todo_id] : []});
    assert.equal(legacy.outcome, expected);
    assert.equal(canonical.outcome, expected);
    assert.equal(legacy.changed, expected === "apply");
  });
}

test("invalid persisted mode is an explicit repair; never a fabricated previous valid mode", () => {
  for (const mode of EXECUTION_HANDOFF_MODES) {
    const requestWithInvalid = {...request, previous_value: "banana", requested_mode: mode};
    const allowed = planLegacyHandoffMode(requestWithInvalid);
    assert.equal(allowed.outcome, "apply");
    assert.equal(allowed.previous_mode, "banana");
    assert.equal(allowed.previous_mode_valid, false);
    assert.equal(allowed.previous_mode_error_code, "invalid_handoff_mode");
    for (const facts of [{todos: [claim]}, {leases: [lease]}]) {
      assert.equal(planLegacyHandoffMode({...requestWithInvalid, ...facts}).outcome, "rejected");
    }
  }
});

test("no-op needs no ownership snapshot, but a changed intent explicitly requires one", () => {
  assert.equal(planLegacyHandoffMode({...request, todos: null, leases: null}).outcome, "snapshot_required");
  const result = planLegacyHandoffMode({...request, previous_value: "hard_lease",
    frontmatter_text: "no metadata", todos: null, leases: null});
  assert.equal(result.outcome, "no_change");
  assert.equal(result.changed, false);
  assert.equal(result.next_frontmatter_text, undefined);
});

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

test("frontmatter edit retains CRLF, quoted Unicode separators, body and final-newline choice", () => {
  for (const newline of ["\n", "\r\n"]) for (const trailing of ["", newline]) {
    const prefix = `---${newline}title: "a\u2028b\u0085c"${newline}`;
    const suffix = `---${newline}body: handoff_mode: legacy${trailing}`;
    const input = `${prefix}handoff_mode: legacy${newline}${suffix}`;
    assert.equal(patchHandoffMode(input, "hard_lease"), `${prefix}handoff_mode: hard_lease${newline}${suffix}`);
    assert.equal(patchHandoffMode(prefix + suffix, "hard_lease"), `${prefix}handoff_mode: hard_lease${newline}${suffix}`);
  }
});

test("missing frontmatter and duplicate fields cannot produce a partial repair", () => {
  for (const [text, code] of [["no frontmatter", "state_frontmatter_missing"],
    ["---\nhandoff_mode: legacy\nhandoff_mode: banana\n---\n", "handoff_mode_duplicate_field"]]) {
    const plan = planLegacyHandoffMode({...request, frontmatter_text: text, previous_value: "banana"});
    assert.equal(plan.outcome, "rejected");
    assert.equal(plan.code, code);
    assert.equal(plan.next_frontmatter_text, undefined);
  }
});


test("Unicode separators cannot invent metadata keys or close the frontmatter", () => {
  for (const separator of ["\u2028", "\u2029"]) {
    const prefix = `---\ntitle: "first${separator}handoff_mode: banana${separator}---"\n`;
    const source = prefix + "handoff_mode: legacy\n---\n";
    assert.equal(patchHandoffMode(source, "hard_lease"), prefix + "handoff_mode: hard_lease\n---\n");
  }
});
