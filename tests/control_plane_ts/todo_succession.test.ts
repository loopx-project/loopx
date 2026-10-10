import assert from "node:assert/strict";
import test from "node:test";
import {evaluateTodoSuccession, projectTodoSuccession, projectTodoClosure, validateTodoClosureSource, SUCCESSION_FACT_COLUMNS, SUCCESSION_EVALUATION_COLUMNS} from "../../loopx/control_plane/todos/succession.ts";

const row = (todo_id: string, overrides = {}) => ({todo_id, status: "done", active: true,
  advancement: true, no_followup: false, successors: [], superseded_by: null,
  unblocks: null, resumes: null, handoff: false, done: false, route_flag: null,
  legacy_route_label: "", context_fields: ["claimed_by"], ...overrides});

type ProjectionInput = {schema_version: string; rows: Record<string, unknown>[];
  context_field_sets?: unknown; evaluations?: Record<string, unknown>[]};
function wire(request: ProjectionInput) {
  const contexts = request.context_field_sets ?? request.rows.map(item => item.context_fields);
  return {...request, row_columns: [...SUCCESSION_FACT_COLUMNS], context_field_sets: contexts,
    rows: request.rows.map((item, index) => SUCCESSION_FACT_COLUMNS.map(name =>
      name === "context_fields" && request.context_field_sets === undefined ? index : item[name])),
    ...(request.evaluations === undefined ? {} : {evaluation_columns: [...SUCCESSION_EVALUATION_COLUMNS],
      evaluations: request.evaluations.map(item => SUCCESSION_EVALUATION_COLUMNS.map(name => item[name]))})};
}
function project(request: ProjectionInput) {
  const result = projectTodoSuccession(wire(request));
  assert.deepEqual(result.evaluation_columns, [...SUCCESSION_EVALUATION_COLUMNS]);
  return {...result, evaluations: (result.evaluations as unknown[][]).map(row =>
    Object.fromEntries(SUCCESSION_EVALUATION_COLUMNS.map((name, index) => [name, row[index]])))};
}

test("route advisory belongs to the source evaluation and never clears the handoff", () => {
  const source = row("todo_gate", {status: "open", handoff: true, legacy_route_label: "STALE handoff closeout"});
  for (const [extra, expected] of [
    [{}, true], [{route_flag: false}, false], [{route_flag: true, done: true}, true],
    [{status: "done"}, false], [{status: "deferred"}, false], [{done: true}, false],
    [{active: false}, false], [{handoff: false}, false], [{legacy_route_label: "handoff closeout"}, false],
  ] as const) {
    const value = {...source, ...extra}, evaluation = evaluateTodoSuccession([value])[0];
    assert.equal(evaluation.route_continuation_replan_required, expected);
    assert.deepEqual(evaluation.successor_todo_ids, []);
    assert.equal(evaluation.handoff_state, !value.active || !value.handoff ? null :
      value.status === "done" ? "cleared_without_successor" : value.status === "deferred" ? "deferred" : "blocking");
  }
  const evaluation = evaluateTodoSuccession([source])[0];
  for (const extra of [{route_flag: false}, {legacy_route_label: "handoff closeout"}, {done: true}]) {
    assert.throws(() => project({schema_version: "todo_succession_request_v1",
      rows: [{...source, ...extra}], evaluations: [evaluation]}), /matching full-source/);
  }
});

test("one resolver recognizes explicit, supersession and inferred links in source order", () => {
  const rows = [row("todo_source", {successors: ["todo_explicit", "todo_source", "todo_missing"], superseded_by: "todo_replaced", handoff: true}),
    row("todo_explicit", {advancement: false}), row("todo_replaced"),
    row("todo_inferred", {resumes: "todo_source", unblocks: "todo_source", active: false})];
  const result = evaluateTodoSuccession(rows)[0];
  assert.deepEqual(result.successor_todo_ids, ["todo_explicit", "todo_replaced", "todo_inferred"]);
  assert.deepEqual(result.unresolved_successor_ids, ["todo_source", "todo_missing"]);
  assert.equal(result.handoff_state, "superseded");
  assert.equal(result.successor_gap, false);
});

for (const [status, extra, expected] of [
  ["open", {}, "blocking"], ["blocked", {}, "blocking"], ["deferred", {}, "deferred"],
  ["done", {}, "cleared_without_successor"], ["done", {no_followup: true}, "cleared_no_followup"],
  ["done", {successors: ["todo_next"]}, "cleared_with_successor"],
  ["open", {superseded_by: "todo_next"}, "superseded"],
  ["open", {superseded_by: "todo_missing"}, "blocking"],
] as const) test(`handoff ${status} ${JSON.stringify(extra)} → ${expected}`, () => {
  assert.equal(evaluateTodoSuccession([row("todo_gate", {status, handoff: true, ...extra}), row("todo_next")])[0].handoff_state, expected);
});

test("archived and deferred rows are evidence but never unfinished completed work", () => {
  const results = evaluateTodoSuccession([row("todo_archive", {active: false}), row("todo_deferred", {status: "deferred"}),
    row("todo_plain", {context_fields: []}), row("todo_closed", {no_followup: true})]);
  assert.deepEqual(results.map(value => value.successor_gap), [false, false, false, false]);
});

test("filtering preserves full-source evidence; editing relevant facts invalidates it", () => {
  const source = row("todo_source");
  const evaluations = evaluateTodoSuccession([source, row("todo_next", {resumes: "todo_source"})]);
  const request = {schema_version: "todo_succession_request_v1", rows: [source], evaluations: [evaluations[0]]};
  assert.equal((project(request).evaluations as Record<string, unknown>[])[0].successor_gap, false);
  assert.throws(() => project({...request, rows: [{...source, no_followup: true}]}), /matching full-source/);
  assert.throws(() => project({...request, evaluations: []}), /cardinality/);
  assert.throws(() => evaluateTodoSuccession([source, source]), /duplicate succession identity/);
});

const closed = {status: "done", watch_only: false, no_followup: true, successor_gap: false, replan: false, handoff_state: null};
const closure = (rows: unknown[], full_selection = true) => projectTodoClosure({schema_version: "todo_closure_request_v0",
  role: "agent", source_section: "Agent Todo", full_selection, rows});
test("terminal proofs require full selection and absence of every unresolved obligation", () => {
  assert.ok(closure([closed]).terminal_closure_proof);
  assert.ok(closure([]).terminal_closure_proof);
  for (const override of [{successor_gap: true}, {replan: true}, {status: "deferred"},
    {status: "open"}, {handoff_state: "cleared_without_successor"}]) {
    assert.equal(closure([{...closed, ...override}]).terminal_closure_proof, undefined);
  }
  assert.equal(closure([closed], false).terminal_closure_proof, undefined);
  assert.equal(closure([closed], false).source_proof, undefined);
  const watch = closure([{...closed, status: "open", watch_only: true}]);
  assert.equal((watch.terminal_closure_proof as Record<string, unknown>).all_convergent_todos_done, true);
  assert.equal((watch.terminal_closure_proof as Record<string, unknown>).all_todos_done, false);
});

function closureSource() {
  return {schema_version: "todo_summary_v0", source_section: "Agent Todo",
    total_count: 1, open_count: 0, done_count: 1, deferred_count: 0,
    convergence_open_count: 0, completed_without_successor_count: 0, route_continuation_replan_count: 0,
    items: [{status: "done", done: true, watch_only: false}], monitor_open_items: [],
    deferred_item_count: 0, deferred_resume_count: 0,
    source_proof: {schema_version: "todo_source_proof_v0", role: "agent", derived: true, item_count: 1},
    terminal_closure_proof: {schema_version: "todo_terminal_closure_proof_v0", role: "agent", derived: true,
      source_section: "Agent Todo", item_count: 1, all_todos_done: true, monitor_open_count: 0,
      watch_only_monitor_count: 0, successor_gap_count: 0, route_replan_count: 0, no_followup_count: 1},
    closure_intent: {schema_version: "todo_closure_intent_v0", kind: "no_followup", derived: true, count: 1}};
}
test("retained closure proofs reject malformed integer witnesses without false terminal intent", () => {
  for (const bad of [true, false, null, "0", "bad", -1, 0.5, Number.MAX_SAFE_INTEGER + 1]) {
    for (const patch of [{item_count: bad}, {monitor_open_count: bad, watch_only_monitor_count: bad},
      {successor_gap_count: bad}, {route_replan_count: bad}, {no_followup_count: bad}]) {
      const input = closureSource();
      const result = validateTodoClosureSource({...input, terminal_closure_proof: {...input.terminal_closure_proof, ...patch}});
      assert.equal((result.source_completeness as Record<string, unknown>).status, "invalid");
      assert.equal(result.closure_intent, null);
    }
  }
  const input = closureSource(), before = structuredClone(input);
  assert.equal((validateTodoClosureSource(input).closure_intent as Record<string, unknown>).count, 1);
  assert.deepEqual(input, before);
});
test("closure source validation retains empty, bounded full-source and watch-only observations", () => {
  const input = closureSource();
  const cases = [{...input, total_count: 0, done_count: 0, items: [],
    source_proof: {...input.source_proof, item_count: 0},
    terminal_closure_proof: {...input.terminal_closure_proof, item_count: 0, no_followup_count: 0}},
  {...input, total_count: 13, done_count: 13,
    source_proof: {...input.source_proof, item_count: 13},
    terminal_closure_proof: {...input.terminal_closure_proof, item_count: 13}},
  {...input, total_count: 2, open_count: 1,
    items: [...input.items, {status: "open", done: false, watch_only: true}],
    monitor_open_items: [{watch_only: true}], source_proof: {...input.source_proof, item_count: 2},
    terminal_closure_proof: {...input.terminal_closure_proof, item_count: 2, all_todos_done: false,
      all_convergent_todos_done: true, monitor_open_count: 1, watch_only_monitor_count: 1}}];
  for (const source of cases) assert.equal((validateTodoClosureSource(source).source_completeness as Record<string, unknown>).status, "valid");
  assert.equal(validateTodoClosureSource(cases[0]).closure_intent, null);
});
test("closure proofs cannot claim all todos are done while watch-only monitors remain", () => {
  const input = closureSource();
  const watchOnly = {...input, total_count: 2, open_count: 1, done_count: 1,
    items: [...input.items as Record<string, unknown>[], {status: "open", done: false, watch_only: true}],
    monitor_open_items: [{watch_only: true}],
    source_proof: {...input.source_proof as Record<string, unknown>, item_count: 2},
    terminal_closure_proof: {...input.terminal_closure_proof as Record<string, unknown>, item_count: 2,
      all_todos_done: false, all_convergent_todos_done: true, monitor_open_count: 1, watch_only_monitor_count: 1}};
  assert.equal((validateTodoClosureSource(watchOnly).source_completeness as Record<string, unknown>).status, "valid");
  const inconsistent = {...watchOnly, terminal_closure_proof: {...watchOnly.terminal_closure_proof,
    all_todos_done: true}};
  const result = validateTodoClosureSource(inconsistent);
  assert.equal((result.source_completeness as Record<string, unknown>).status, "invalid");
  assert.equal(result.closure_intent, null);
});
test("open, partial, deferred and replan source evidence cannot reuse a terminal proof", () => {
  const input = closureSource();
  for (const patch of [{source_proof: null}, {total_count: true}, {done_count: "1"}, {open_count: 1},
    {source_section: ""}, {items: []}, {items: [null]}, {items: [{status: "open", done: false}]},
    {items: [{status: "done", done: false}]}, {items: [{status: "done", done: true, route_continuation_replan_required: true}]},
    {monitor_open_items: [{watch_only: false}]}, {monitor_open_items: null}, {deferred_item_count: null},
    {deferred_item_count: 1}, {deferred_resume_count: 1}, {convergence_open_count: true},
    {completed_without_successor_count: 1}, {route_continuation_replan_count: 1}]) {
    const result = validateTodoClosureSource({...input, ...patch});
    assert.equal((result.source_completeness as Record<string, unknown>).status, "invalid");
    assert.equal(result.closure_intent, null);
  }
});

test("interned field sets are lossless and reject invalid references", () => {
  const rows = Array.from({length: 4000}, (_, index) => row(`todo_history_${index}`, {
    active: false, context_fields: index % 2 ? ["claimed_by", "completed_at"] : [],
  }));
  const request = {schema_version: "todo_succession_request_v1", context_field_sets: [[], ["claimed_by", "completed_at"]],
    rows: rows.map((value, index) => ({...value, context_fields: index % 2}))};
  assert.deepEqual(project(request).evaluations, evaluateTodoSuccession(rows));
  for (const index of [-1, 2, 0.5, "0"]) assert.throws(() => project({...request,
    rows: [{...rows[0], context_fields: index}]}), /context field index/);
});

test("a cached result cannot contradict its matched item facts", () => {
  const source = row("todo_source"), evaluation = evaluateTodoSuccession([source])[0];
  for (const mutation of [{successor_gap: false}, {tracked_completion: false}, {handoff_state: "superseded"},
    {route_continuation_replan_required: true},
    {successor_todo_ids: [null]}, {successor_todo_ids: [source.todo_id]}]) {
    assert.throws(() => project({schema_version: "todo_succession_request_v1",
      rows: [source], evaluations: [{...evaluation, ...mutation}]}), /succession evaluation|successor identity/);
  }
});


test("active identity replaces archived edges regardless of source order", () => {
  const source = row("todo_source");
  const archived = row("todo_reused", {active: false, resumes: "todo_source"});
  const active = row("todo_reused", {status: "open"});
  for (const generations of [[archived, active], [active, archived]]) {
    const result = evaluateTodoSuccession([source, ...generations]);
    assert.equal(result[0].successor_gap, true);
    assert.deepEqual(result[0].successor_todo_ids, []);
    const explicit = evaluateTodoSuccession([{...source, successors: ["todo_reused"]}, ...generations]);
    assert.deepEqual(explicit[0].successor_todo_ids, ["todo_reused"]);
  }
  assert.throws(() => evaluateTodoSuccession([active, active]), /duplicate succession identity/);
  assert.throws(() => evaluateTodoSuccession([archived, archived, active]), /duplicate succession identity/);
});


test("columnar transport rejects reordered, missing, extra columns and malformed cells", () => {
  const request = wire({schema_version: "todo_succession_request_v1", rows: [row("todo_source")]});
  for (const row_columns of [[...SUCCESSION_FACT_COLUMNS].reverse(), SUCCESSION_FACT_COLUMNS.slice(1),
      [...SUCCESSION_FACT_COLUMNS, "extra"]]) {
    assert.throws(() => projectTodoSuccession({...request, row_columns}), /columns mismatch/);
  }
  for (const rows of [[{}], [[]], [[...request.rows[0], null]]]) {
    assert.throws(() => projectTodoSuccession({...request, rows}), /row width/);
  }
  const reused = wire({schema_version: "todo_succession_request_v1", rows: [row("todo_source")],
    evaluations: evaluateTodoSuccession([row("todo_source")])});
  assert.throws(() => projectTodoSuccession({...reused, evaluation_columns: [...SUCCESSION_EVALUATION_COLUMNS].reverse()}), /columns mismatch/);
  assert.throws(() => projectTodoSuccession({...reused, evaluations: [[]]}), /row width/);
  assert.throws(() => projectTodoSuccession({...request, schema_version: "todo_succession_request_v0"}), /schema mismatch/);
});
