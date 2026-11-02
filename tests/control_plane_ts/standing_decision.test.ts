import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {TODO_DOMAIN_ITEM_SCHEMA} from "../../loopx/control_plane/coordination/coordination_state_contract.ts";
import {selectCoordinationTodoArchive} from "../../loopx/control_plane/coordination/todo_archive_selection.ts";
import {isStandingDecisionReceipt, projectStandingDecisions} from "../../loopx/control_plane/todos/standing_decision.ts";
import {readTodo} from "../../loopx/control_plane/todos/read_projection.ts";

const decision = (todo_id: string, decision_outcome: string, extra: JsonObject = {}): JsonObject => ({
  schema_version: TODO_DOMAIN_ITEM_SCHEMA, todo_id, decision_outcome, role: "user", task_class: "user_gate",
  status: "done", archive_state: "active", global_gate: true,
  decision_scope: {kind: "write_scope", granularity: "goal", scope_key: "release"}, ...extra,
});
const entries = (result: JsonObject) => result.entries as JsonObject[];

test("decision chronology uses instants and microseconds, not IDs, offsets or prose edit time", () => {
  const old = decision("todo_zzz", "approve", {completed_at: "2026-09-10T10:00:00.000001+08:00", updated_at: "2027-01-01"});
  const next = decision("todo_aaa", "reject", {completed_at: "2026-09-10T02:00:00.000002Z"});
  for (const items of [[old, next], [next, old]]) {
    const result = projectStandingDecisions(items)!;
    assert.equal(result.active_count, 0);
    assert.equal(entries(result)[0].source_todo_id, "todo_aaa");
    assert.deepEqual(projectStandingDecisions(items, true), result);
  }
});

test("updated_at is a historical fallback only when completed_at is absent", () => {
  const result = projectStandingDecisions([
    decision("todo_old", "approve", {updated_at: "2026-01-01"}),
    decision("todo_new", "cancel", {updated_at: "2026-01-02"}),
  ])!;
  assert.equal(result.inactive_count, 1);
  assert.equal(entries(result)[0].outcome, "cancel");
});

test("canonical ambiguous chronology diagnoses conflict without manufacturing a decision", () => {
  for (const [left, right] of [
    [{}, {}],
    [{completed_at: "2026-09-10"}, {}],
    [{completed_at: "2026-09-10"}, {completed_at: "2026-09-10"}],
    [{completed_at: "2026-02-30"}, {completed_at: "2026-03-01"}],
    [{completed_at: "broken", updated_at: "2026-09-11"}, {completed_at: "2026-09-10"}],
  ]) {
    const items = [decision("todo_old", "approve", left), decision("todo_new", "reject", right)];
    const result = projectStandingDecisions(items)!;
    assert.deepEqual(result.entries, []);
    assert.equal(result.active_count, 0);
    assert.equal(result.conflict_count, 1);
    assert.deepEqual(projectStandingDecisions([...items].reverse()), result);
    assert.equal((result.conflicts as JsonObject[])[0].reason_code, "standing_decision_order_unresolved");
  }
});

test("legacy source order is a compatibility input, never a native display index", () => {
  const items = [decision("todo_old", "approve"), decision("todo_new", "reject")];
  assert.equal(entries(projectStandingDecisions(items, true)!)[0].outcome, "reject");
  const imported = items.map((item, i) => ({...item, schema_version: "todo_item_v0", index: i + 1, source_section: "User Todo"}));
  assert.equal(entries(projectStandingDecisions([...imported].reverse())!)[0].outcome, "reject");
  const native = imported.map(item => ({...item, schema_version: TODO_DOMAIN_ITEM_SCHEMA}));
  assert.equal(projectStandingDecisions(native)!.conflict_count, 1);
  assert.equal(projectStandingDecisions([imported[0], {...imported[1], source_section: "Archive"}])!.conflict_count, 1);
  assert.equal(projectStandingDecisions(imported.map(item => ({...item, index: 1})))!.conflict_count, 1);
});

test("matching outcomes are unambiguous; scope and agent ownership remain separate", () => {
  const items = [decision("todo_aaa", "approve"), decision("todo_zzz", "approve"),
    decision("todo_lane", "reject", {global_gate: false, blocks_agent: "agent-b"}),
    decision("todo_scope", "cancel", {decision_scope: {kind: "resource", granularity: "goal", scope_key: "release"}})];
  const result = projectStandingDecisions(items)!;
  assert.equal(result.active_count, 1);
  assert.equal(result.inactive_count, 2);
  assert.equal(result.conflict_count, undefined);
  assert.deepEqual(projectStandingDecisions(items.reverse()), result);
});

test("read and archive share explicit standing eligibility, not notification prose heuristics", () => {
  for (const extra of [
    {role: "agent"}, {task_class: "user_action", text: "approve authorization"}, {task_class: null, action_kind: "approval"},
    {status: "open"}, {unblocks_todo_id: "todo_delivery"}, {unblocks_todo_id: "malformed-link"}, {unblocks_todo_id: 123},
    {global_gate: false}, {decision_outcome: "unknown"},
    {decision_scope: {granularity: "goal", key: "release"}},
    {decision_scope: {kind: "write_scope", granularity: "action", scope_key: "release"}},
  ]) {
    const item = decision("todo_invalid", "approve", extra);
    assert.equal(isStandingDecisionReceipt(item), false);
    assert.equal(projectStandingDecisions([item]), null);
    assert.equal(selectCoordinationTodoArchive({todos: [item], role: "user", max_active_done: 0}).retained_standing_decision_count, 0);
  }
});

test("retained archive revocation stays effective; all decision outcomes survive ordinary archive selection", () => {
  const approval = decision("todo_old", "approve", {completed_at: "2026-01-01"});
  const rejection = decision("todo_new", "reject", {completed_at: "2026-01-02", archive_state: "archive"});
  assert.equal(projectStandingDecisions([rejection, approval])!.active_count, 0);
  const retained = [approval, {...rejection, archive_state: "active"}, decision("todo_cancel", "cancel")];
  const archive = selectCoordinationTodoArchive({todos: retained, role: "user", max_active_done: 0});
  assert.deepEqual(archive.moved_todo_ids, []);
  assert.equal(archive.retained_standing_decision_count, 3);
});

test("composed Todo read rejects malformed requests and accepts an empty standing inventory", async () => {
  const request = {schema_version: "todo_read_request_v0", kind: "source_fields", groups: [], standing_items: [], legacy_source_order: false};
  const result = await readTodo(request);
  assert.equal((result.fields as JsonObject).standing_decision_authority, undefined);
  for (const extra of [{schema_version: "unknown"}, {kind: "unknown"}, {groups: {}}, {standing_items: [null]}]) {
    await assert.rejects(() => readTodo({...request, ...extra}));
  }
});

test("composed read refuses contradictory authority when legacy source order is not explicitly true", async () => {
  for (const legacy_source_order of [false, "false"]) {
    const result = await readTodo({schema_version: "todo_read_request_v0", kind: "source_fields", groups: [],
      standing_items: [decision("todo_approve", "approve"), decision("todo_revoke", "reject")], legacy_source_order});
    const authority = (result.fields as JsonObject).standing_decision_authority as JsonObject;
    assert.equal(authority.active_count, 0);
    assert.deepEqual(authority.entries, []);
    assert.equal(authority.conflict_count, 1);
  }
});
