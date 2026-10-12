import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {TODO_SUMMARY_PROJECTION_COLUMNS, projectTodoSummary} from "../../loopx/control_plane/todos/summary_projection.ts";
import {projectAdvancementFrontier} from "../../loopx/control_plane/todos/frontier_revision.ts";
import {productionScaleCoordinationFixture} from "./production_scale_coordination_fixture.ts";
import {evaluateTodoSuccession, SUCCESSION_FACT_COLUMNS, SUCCESSION_EVALUATION_COLUMNS} from "../../loopx/control_plane/todos/succession.ts";

const row = (fields: JsonObject = {}): JsonObject => ({status: "open", done: false,
  task_class: "advancement_task", has_resume: false, resume_ready: null, resume_evaluated: false,
  acceptance_blocked: false, claimed: false, claim: null, preferred: false, watch_only: false,
  due_at: null, expires_at: null, sort: [1, 1, "", ""], todo_id: null, bound: null,
  blocks: null, global: false, excluded: [], completed_at: null, updated_at: null,
  completion_index: 0, linked_user_action: false, no_followup: false,
  successor_gap: false, handoff_state: null, replan: false, ...fields});
const request = (rows: JsonObject[], fields: JsonObject = {}): JsonObject => ({
  schema_version: "todo_summary_projection_request_v1",
  columns: [...TODO_SUMMARY_PROJECTION_COLUMNS],
  rows: rows.map(row => TODO_SUMMARY_PROJECTION_COLUMNS.map(name => row[name] ?? null)),
  observed_at: 100,
  selection: null, role: "agent", source_section: "Agent Todo", item_limit: 12, full_selection: true, ...fields});
const done = (fields: JsonObject = {}) => row({status: "done", done: true, no_followup: true, ...fields});

function fused(rows: JsonObject[], facts: JsonObject[], evaluations = evaluateTodoSuccession(facts)): JsonObject {
  return request(rows, {schema_version: "todo_summary_projection_request_v2", succession: {
    schema_version: "todo_succession_request_v1", row_columns: [...SUCCESSION_FACT_COLUMNS],
    context_field_sets: facts.map(fact => fact.context_fields),
    rows: facts.map((fact, index) => SUCCESSION_FACT_COLUMNS.map(name => name === "context_fields" ? index : fact[name])),
    evaluation_columns: [...SUCCESSION_EVALUATION_COLUMNS],
    evaluations: evaluations.map(evaluation => SUCCESSION_EVALUATION_COLUMNS.map(name => evaluation[name])),
  }});
}

function withFrontier(rows: JsonObject[]): JsonObject {
  const facts = rows.map(row => ({todo_id: row.todo_id, status: row.status, active: true,
    advancement: row.task_class === "advancement_task", no_followup: row.no_followup,
    successors: [], superseded_by: null, unblocks: null, resumes: null, handoff: false,
    done: row.done, route_flag: null, legacy_route_label: "", context_fields: []}));
  return {...fused(rows, facts), schema_version: "todo_summary_projection_request_v3",
    frontier_rows: rows.map(row => ({id: row.todo_id, claim: row.claim, excluded: row.excluded,
      advancement: row.task_class === "advancement_task", actionable: row.status === "open",
      updated: row.updated_at, serialized: JSON.stringify({todo_id: row.todo_id, status: row.status})}))};
}

test("summary frontier uses selected full-source rows before caps and retains the existing index codec", () => {
  const rows = Array.from({length: 32}, (_, index) => row({todo_id: `todo_batch_${index}`,
    claimed: true, claim: index < 24 ? "a" : "b", updated_at: "2026-01-01T00:00:00.000001Z"}));
  const carrier = withFrontier(rows), before = structuredClone(carrier);
  const original = projectAdvancementFrontier({schema_version: "todo_frontier_revision_request_v0",
    operation: "index", rows: carrier.frontier_rows}).index;
  for (const limit of [null, 0, 1, 12]) {
    const result = projectTodoSummary({...carrier, item_limit: limit});
    assert.deepEqual(result.fields.advancement_frontier_revision_index, original);
    const selected = projectTodoSummary({...carrier, item_limit: limit,
      selection: {role: "agent", status: null, todo_id: null, agent_id: "a"}});
    const index = selected.fields.advancement_frontier_revision_index as JsonObject;
    assert.deepEqual(index.claimed_advancement_counts, {a: 24});
    assert.equal(selected.fields.total_count, 24);
  }
  assert.deepEqual(carrier, before);
  assert.equal(projectTodoSummary(fused([], [])).fields.advancement_frontier_revision_index, undefined);
  assert.deepEqual((projectTodoSummary(withFrontier([])).fields.advancement_frontier_revision_index as JsonObject).all,
    {complete: false});
});

test("summary frontier rejects absent, reordered and conflicting facts instead of mixing snapshots", () => {
  const carrier = withFrontier([row({todo_id: "todo_first", claimed: true, claim: "a", updated_at: "2026-01-01T00:00:00Z"}),
    row({todo_id: "todo_second", updated_at: "2026-01-01T00:00:00Z"})]);
  for (const mutate of [
    (value: JsonObject) => { value.frontier_rows = null; },
    (value: JsonObject) => { value.frontier_rows = []; },
    (value: JsonObject) => { (value.frontier_rows as JsonObject[]).reverse(); },
    ...["claim", "excluded", "advancement", "actionable", "updated"].map(key => (value: JsonObject) => {
      (value.frontier_rows as JsonObject[])[0][key] = {claim: "b", excluded: ["b"], advancement: false,
        actionable: false, updated: "2026-02-01T00:00:00Z"}[key];
    }),
  ]) {
    const changed = structuredClone(carrier); mutate(changed);
    assert.throws(() => projectTodoSummary(changed), /summary frontier/);
  }
  assert.throws(() => projectTodoSummary({...carrier, role: "user"}), /non-Agent summary/);
});

test("fused summary preserves legacy decisions and full-source inferred edges through filtering", () => {
  const facts = [{todo_id: "todo_source", status: "done", active: true, advancement: true,
    no_followup: false, successors: [], superseded_by: null, unblocks: null, resumes: null,
    handoff: true, done: true, route_flag: null, legacy_route_label: "", context_fields: ["claimed_by"]},
  {todo_id: "todo_archived", status: "done", active: false, advancement: true,
    no_followup: true, successors: [], superseded_by: null, unblocks: "todo_source", resumes: null,
    handoff: false, done: true, route_flag: null, legacy_route_label: "", context_fields: []}];
  const evaluations = evaluateTodoSuccession(facts);
  const rows = [done({todo_id: "todo_source", no_followup: false, handoff_state: "cleared_with_successor"})];
  const carrier = fused(rows, facts.slice(0, 1), evaluations.slice(0, 1));
  for (const limit of [null, 0, 1, 12]) {
    assert.deepEqual(projectTodoSummary({...carrier, item_limit: limit}), projectTodoSummary(request(rows, {item_limit: limit})));
  }
  const empty = projectTodoSummary(fused([], []));
  assert.deepEqual(empty, projectTodoSummary(request([])));
});

test("fused summary refuses missing, stale, reordered and contradictory evidence before closure", () => {
  const facts = ["todo_first", "todo_second"].map(todo_id => ({todo_id, status: "done", active: true,
    advancement: true, no_followup: true, successors: [], superseded_by: null, unblocks: null,
    resumes: null, handoff: false, done: true, route_flag: null, legacy_route_label: "", context_fields: ["claimed_by"]}));
  const rows = facts.map(fact => done({todo_id: fact.todo_id}));
  const carrier = fused(rows, facts);
  assert.ok(projectTodoSummary(carrier).fields.terminal_closure_proof);
  assert.throws(() => projectTodoSummary({...carrier, succession: undefined}), /summary succession/);
  for (const mutate of [
    (value: JsonObject) => { (value.succession as JsonObject).evaluations = []; },
    (value: JsonObject) => { ((value.succession as JsonObject).evaluations as unknown[][])[0][1] = "stale"; },
    (value: JsonObject) => { (value.rows as unknown[][]).reverse(); },
    (value: JsonObject) => { (value.rows as unknown[][])[0][TODO_SUMMARY_PROJECTION_COLUMNS.indexOf("successor_gap")] = true; },
    (value: JsonObject) => { (value.rows as unknown[][])[0][TODO_SUMMARY_PROJECTION_COLUMNS.indexOf("handoff_state")] = "blocking"; },
    (value: JsonObject) => { (value.rows as unknown[][])[0][TODO_SUMMARY_PROJECTION_COLUMNS.indexOf("no_followup")] = false; },
    (value: JsonObject) => { (value.rows as unknown[][])[0][TODO_SUMMARY_PROJECTION_COLUMNS.indexOf("replan")] = true; },
  ]) {
    const changed = structuredClone(carrier); mutate(changed);
    assert.throws(() => projectTodoSummary(changed));
  }
  const changed = structuredClone(carrier);
  delete (changed.succession as JsonObject).evaluations;
  assert.throws(() => projectTodoSummary(changed), /full-source succession/);
});

test("one whole-source projection computes counts, visibility and closure before limits", () => {
  for (const limit of [null, 0, 1, 12]) {
    const rows = Array.from({length: 32}, (_, index) => row({claimed: true, claim: index < 24 ? "a" : "b"}));
    const before = structuredClone(rows), result = projectTodoSummary(request(rows, {item_limit: limit}));
    assert.equal(result.fields.open_count, 32);
    assert.equal((result.fields.work_counts as JsonObject).advancement, 32);
    assert.equal(result.fields.claimed_open_count, 32);
    assert.deepEqual(result.lanes.claimed_open_items.indices, [...Array(8).keys(), ...Array.from({length: 8}, (_, i) => 24 + i)]);
    assert.equal(result.lanes.items.indices.length, limit === null ? 32 : limit);
    assert.equal(result.fields.terminal_closure_proof, undefined);
    assert.deepEqual(rows, before);
  }
});

test("succession diagnostics guide continuation without authorizing terminal closeout", () => {
  const facts = [{todo_id: "todo_stage", status: "done", active: true, advancement: true,
    no_followup: false, successors: [], superseded_by: null, unblocks: null, resumes: null,
    handoff: false, done: true, route_flag: null, legacy_route_label: "", context_fields: ["claimed_by"]}];
  const stage = done({todo_id: "todo_stage", no_followup: false, successor_gap: true});
  for (const limit of [null, 0, 1]) {
    const carrier = fused([stage], facts);
    const source = structuredClone(carrier);
    const result = projectTodoSummary({...carrier, item_limit: limit});
    assert.deepEqual(result, projectTodoSummary(request([stage], {item_limit: limit})));
    assert.equal(result.fields.completed_without_successor_count, 1);
    assert.equal(result.fields.terminal_closure_proof, undefined);
    const warning = result.fields.todo_succession_warning as JsonObject;
    assert.equal(warning.count, 1);
    assert.match(String(warning.recommended_action), /Review remaining authorized Goal acceptance/);
    assert.match(String(warning.recommended_action), /runnable frontier/);
    assert.match(String(warning.recommended_action), /ordinary Todo completion needs no artificial successor/);
    assert.match(String(warning.recommended_action), /only for final scope closeout.*current settlement contract/);
    assert.deepEqual(carrier, source, "read guidance must not add successor or terminal facts");
  }
  const continuing = projectTodoSummary(request([stage, row({todo_id: "todo_next"})]));
  assert.equal(continuing.fields.completed_without_successor_count, 1);
  assert.deepEqual(continuing.lanes.first_executable_items.indices, [1]);
  assert.equal(continuing.fields.terminal_closure_proof, undefined);
});

test("recent completion orders true microsecond instants and ignores later edits", () => {
  const rows = [done({completed_at: "2026-01-01T10:00:00.000001+08:00", updated_at: "2026-12-01T00:00:00Z"}),
    done({completed_at: "2026-01-01T02:00:00.000002Z"}), done({completed_at: "invalid"})];
  const result = projectTodoSummary(request(rows));
  assert.deepEqual(result.lanes.recent_completed_advancement_items.indices, [1, 0]);
  assert.equal(result.fields.advancement_done_count, 3);
  assert.equal(result.lanes.items.indices.length, 3);
});

test("equal instants retain reverse source coordinate and stable ties", () => {
  const rows = [done({completed_at: "2026-01-01T10:00:00+08:00", completion_index: 2}),
    done({completed_at: "2026-01-01T02:00:00Z", completion_index: 4}),
    done({completed_at: "2026-01-01T02:00:00Z", completion_index: 4})];
  assert.deepEqual(projectTodoSummary(request(rows)).lanes.recent_completed_advancement_items.indices, [1, 2, 0]);
});

test("selection cannot turn partial source knowledge into a closure proof", () => {
  const select = {role: "agent", status: null, todo_id: null, agent_id: null};
  const full = projectTodoSummary(request([done()], {selection: select}));
  assert.ok(full.fields.terminal_closure_proof);
  const partial = projectTodoSummary(request([done()], {selection: select, full_selection: false}));
  assert.equal(partial.full_selection, false);
  assert.equal(partial.fields.source_proof, undefined);
  assert.equal(partial.fields.terminal_closure_proof, undefined);
  const filtered = projectTodoSummary(request([done(), row()], {selection: {...select, status: "done"}}));
  assert.equal(filtered.fields.terminal_closure_proof, undefined);
  assert.deepEqual(filtered.source_indices, [0]);
});

test("scope preserves original ordinals and closure uses only the selected graph decisions", () => {
  const rows = [row({todo_id: "peer", claimed: true, claim: "other"}),
    done({todo_id: "own", claimed: true, claim: "me"})];
  const result = projectTodoSummary(request(rows, {selection: {role: "agent", status: null, todo_id: null, agent_id: "me"}}));
  assert.deepEqual(result.source_indices, [1]);
  assert.deepEqual(result.lanes.items.indices, [1]);
  assert.equal(result.fields.done_count, 1);
  assert.equal(result.fields.terminal_closure_proof, undefined);
});

test("invalid source or budgets fail closed rather than hiding rows", () => {
  for (const fields of [{item_limit: -1}, {item_limit: 1.5}, {item_limit: true}, {full_selection: null}]) {
    assert.throws(() => projectTodoSummary(request([row()], fields)));
  }
  for (const fields of [{claimed: true}, {claim: "agent"}, {done: true}, {completed_at: 2},
    {has_resume: true}, {successor_gap: "false"}, {handoff_state: "unrecognized"}]) {
    assert.throws(() => projectTodoSummary(request([row(fields)])));
  }
});

test("large native and imported corpora preserve source coverage across every display cap", () => {
  for (const format of ["native", "legacy"] as const) {
    const fixture = productionScaleCoordinationFixture("summary-source", format);
    const records = fixture.projection.todos as JsonObject[];
    const rows = records.map((todo, ordinal) => row({status: todo.status, done: todo.done,
      task_class: todo.task_class, sort: [1, ordinal, "", ""], todo_id: todo.todo_id,
      claim: todo.claimed_by ?? null, claimed: Boolean(todo.claimed_by)}));
    const full = projectTodoSummary(request(rows, {item_limit: null}));
    assert.equal(full.fields.total_count, records.length);
    assert.equal(full.lanes.items.indices.length, records.length);
    for (const limit of [0, 1, 12]) {
      const limited = projectTodoSummary(request(rows, {item_limit: limit}));
      assert.deepEqual(limited.fields, full.fields);
      assert.equal(limited.lanes.items.indices.length, limit);
      for (const lane of Object.values(limited.lanes)) {
        assert.equal(new Set(lane.indices).size, lane.indices.length);
        assert.ok(lane.indices.every(index => index >= 0 && index < records.length));
      }
    }
  }
});
