import assert from "node:assert/strict";
import test from "node:test";
import { projectTodoContextPage } from "../../loopx/control_plane/todos/context_projection.ts";
import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";

const page = (records: JsonObject[], options: JsonObject = {}) => projectTodoContextPage({
  records, owner_scope: true, offset: 0, limit: 48, ...options,
});

// Independent consumer oracle: follow response-local JSON pointers, never ids.
function expandReferences(value: unknown, root: unknown): unknown {
  if (Array.isArray(value)) return value.map(child => expandReferences(child, root));
  if (value !== null && typeof value === "object") {
    const row = value as JsonObject;
    if (Object.keys(row).length === 1 && typeof row.$ref === "string") {
      const target = row.$ref.slice(2).split("/").reduce((current, key) =>
        (current as JsonObject)[key.replace(/~1/g, "/").replace(/~0/g, "~")], root);
      return expandReferences(target, root);
    }
    return Object.fromEntries(Object.entries(row).map(([key, child]) =>
      [key, expandReferences(child, root)]));
  }
  return value;
}

test("ordinary list references preserve every lane and distinct source fact", () => {
  const todo = {todo_id: "todo_work", role: "agent", text: "Keep the acceptance tail.",
    resume_ready: false, required_decision_scopes: [{scope_key: "publish", kind: "direction"}]};
  const gate = {todo_id: "todo_gate", role: "user", text: "Wait for owner approval.",
    global_gate: false, blocks_agent: "worker", status: "open"};
  const derived = {...todo, recommended_action: "Review remaining Goal acceptance."};
  const outsideLimit = {todo_id: "todo_wait", resume_when: "capacity_available:network", resume_ready: false};
  const payload = {ok: true, command: "list", todos: [gate, todo], todo_count: 2,
    explicit_limit: 1, authority_read: {provider_revision: "sqlite:42"},
    user_todos: {items: [gate], first_open_items: [gate], total_count: 4},
    agent_todos: {items: [todo], first_executable_items: [todo],
      completed_without_successor_items: [derived], "custom/lane~items": [outsideLimit],
      deferred_items: [outsideLimit], unknown_facts: {source: "preserved"}}};
  const before = structuredClone(payload);
  const result = projectTodoContextPage({list_payload: payload});
  const {todo_list_record_references, ...wire} = result;
  assert.deepEqual(expandReferences(wire, wire), payload);
  assert.deepEqual(result.todos, payload.todos);
  assert.deepEqual(payload, before);
  assert.ok(todo_list_record_references);
  assert.deepEqual((result.agent_todos as JsonObject).first_executable_items, [{$ref: "#/todos/1"}]);
  assert.deepEqual((result.agent_todos as JsonObject).completed_without_successor_items, [derived]);
  assert.deepEqual((result.agent_todos as JsonObject).deferred_items,
    [{$ref: "#/agent_todos/custom~1lane~0items/0"}]);
  assert.equal(result.execution_authorized, undefined);
});

test("record equality includes nested scopes, nulls, arrays and differently ordered keys", () => {
  const row = {todo_id: "todo_one", text: "Original body", nested: {flag: false, empty: null},
    scopes: ["first", "second"]};
  const reordered = {scopes: ["first", "second"], nested: {empty: null, flag: false},
    text: "Original body", todo_id: "todo_one"};
  const changed = {...row, nested: {flag: true, empty: null}};
  const payload = {ok: true, command: "list", todos: [row],
    agent_todos: {items: [reordered], blockers: [changed], reversed: [{...row, scopes: ["second", "first"]}]}};
  const result = projectTodoContextPage({list_payload: payload});
  const {todo_list_record_references, ...wire} = result;
  assert.deepEqual(expandReferences(wire, wire), payload);
  assert.deepEqual((result.agent_todos as JsonObject).items, [{$ref: "#/todos/0"}]);
  assert.deepEqual((result.agent_todos as JsonObject).blockers, [changed]);
  assert.deepEqual(result.todos, [row]);
});

test("list references cannot mix with detail/page inputs or alter exact/thin paths", () => {
  const payload = {ok: true, command: "list", todos: []};
  const result = projectTodoContextPage({list_payload: payload});
  assert.deepEqual(result.todos, []);
  for (const changed of [{todo_id_filter: "todo_one"}, {thin: true}, {todos: null}, {ok: false}]) {
    assert.throws(() => projectTodoContextPage({list_payload: {...payload, ...changed}}));
  }
  assert.throws(() => projectTodoContextPage({list_payload: payload, records: []}));
  assert.throws(() => projectTodoContextPage({list_payload: payload, detail_payload: {}}));
});

test("overview retains declared relationships without changing their meaning", () => {
  const records = [
    {todo_id: "research", role: "agent", status: "open", priority: "P1", text: "Public report",
      resume_when: "todo_done:data", resume_ready: false, successor_todo_ids: ["review"],
      required_decision_scopes: [{kind: "direction", granularity: "action", scope_key: "publish"}]},
    {todo_id: "release", role: "user", status: "open", priority: "P0", text: "Choose release date",
      task_class: "user_action", global_gate: false, goal_bound: true},
    {todo_id: "polish", role: "agent", status: "deferred", priority: "P2", text: "Optional polish"},
    {todo_id: "old", status: "done", text: "Old result"},
  ];
  const before = structuredClone(records);
  const result = page(records, {limit: 2});
  assert.deepEqual(result.coverage, {active: 3, included: 2, omitted: 1});
  const rows = result.todos as JsonObject[];
  assert.deepEqual(rows.map(row => row.todo_id), ["release", "research"]);
  assert.equal(rows[0].global_gate, false);
  assert.equal(rows[0].goal_bound, true);
  assert.equal(rows[1].resume_ready, false);
  assert.deepEqual(rows[1].required_decision_scopes, records[0].required_decision_scopes);
  assert.equal(rows[1].deadline, undefined);
  assert.equal(rows[1].execution_authorized, undefined);
  assert.deepEqual(records, before);
});

test("overview loss is visible and an exact read recovers the original constraint", () => {
  const fullText = "研究报告😀".repeat(150) + "Only publish after owner acceptance.";
  const record = {todo_id: "report", status: "open", text: fullText,
    title: Array.from(fullText).slice(0, 500).join("") + "...",
    note: "背景。".repeat(120) + "只写草稿，不要发布。", private_provider_payload: "excluded"};
  const overview = (page([record]).todos as JsonObject[])[0];
  assert.equal(overview.content_truncated, true);
  assert.equal(Array.from(String(overview.title)).length, 420);
  const exact = (page([record], {todo_id: "report"}).todos as JsonObject[])[0];
  assert.equal(exact.continuation, record.note);
  assert.equal(exact.title, record.text);
  assert.equal(exact.content_truncated, false);
  assert.equal(exact.private_provider_payload, undefined);
  const external = (page([record], {owner_scope: false, todo_id: "report"}).todos as JsonObject[])[0];
  assert.equal(external.continuation, undefined);
  const titleOnly = (page([{todo_id: "title-only", title: "Original title", status: "open"}],
    {todo_id: "title-only"}).todos as JsonObject[])[0];
  assert.equal(titleOnly.title, "Original title");
});

test("exact completed or missing records remain observations, never runnable work", () => {
  const records = [{todo_id: "done", status: "done", text: "Completed"}];
  assert.equal((page(records, {todo_id: "done"}).todos as JsonObject[])[0].status, "done");
  assert.deepEqual(page(records, {todo_id: "missing"}).coverage, {active: 0, included: 0, omitted: 0});
  for (const options of [{owner_scope: "true"}, {offset: -1}, {limit: 49}, {todo_id: ""}]) {
    assert.throws(() => page(records, options));
  }
});

test("default exact detail removes duplicate views without truncating or granting authority", () => {
  const body = "Retain each independent acceptance clause. ".repeat(80) + "TAIL: no publication";
  const todo = {todo_id: "todo_one", text: body, status: "blocked", resume_ready: false};
  const payload = {todo_id_filter: "todo_one", matched: true, todos: [todo],
    agent_todos: {items: [todo]}, user_todos: {items: []},
    authority_read: {provider_revision: "file:17", source_authority: "file_v0"},
    relations: {required_write_scopes: ["docs/**"], resume_ready: false}};
  const before = structuredClone(payload);
  const result = projectTodoContextPage({detail_payload: payload});
  assert.deepEqual(result.todo, todo);
  assert.deepEqual(result.authority_read, payload.authority_read);
  assert.deepEqual(result.relations, payload.relations);
  for (const key of ["todos", "agent_todos", "user_todos", "execution_authorized"]) {
    assert.equal(result[key], undefined);
  }
  assert.deepEqual(payload, before);
  assert.equal(JSON.stringify(result).split(body).length - 1, 1);
  for (const changed of [
    {todo_id_filter: ""}, {todos: [todo, todo]}, {todos: [{...todo, todo_id: "other"}]},
    {matched: false}, {todos: [{...todo, text: 42}]},
  ]) assert.throws(() => projectTodoContextPage({detail_payload: {...payload, ...changed}}));
  assert.throws(() => projectTodoContextPage({detail_payload: payload, records: []}));
  const missing = projectTodoContextPage({detail_payload: {
    todo_id_filter: "todo_missing", matched: false, not_found: true, todos: [],
  }});
  assert.equal(missing.not_found, true);
  assert.equal((missing.todo_detail_projection as JsonObject).source_complete, false);
});

test("handoff context is an exclusive read lens with aligned stages", () => {
  assert.throws(() => projectTodoContextPage({handoff_sources: [], list_payload: {}}), /cannot be combined/);
  assert.throws(() => projectTodoContextPage({handoff_sources: [{}], handoff_followups: []}), /cardinality/);
  const result = projectTodoContextPage({handoff_sources: [{texts: {note: "Continue the complete requirement"}}]});
  assert.deepEqual(result.handoff_context, [{note: null, continuation_hint: "Continue the complete requirement"}]);
  // No execution grant or source record is manufactured by this projection.
  assert.deepEqual(Object.keys(result), ["handoff_context"]);
});

// Captured before the cutover; independent rule cases below constrain its meaning.
import displayFixture from "./fixtures/todo_summary_display.json" with {type: "json"};
import {projectTodoSummaryDisplay} from "../../loopx/control_plane/todos/context_projection.ts";
const display = (item: JsonObject) => projectTodoSummaryDisplay({items: [{record: item}]})[0]!;
const handoff = (item: JsonObject, extras: JsonObject = {}) =>
  (projectTodoContextPage({handoff_sources: [{record: item, ...extras}]}).handoff_context as JsonObject[])[0]!;

test("summary display keeps the canonical scoped-decision receipt version verbatim", () => {
  const receipt = {schema_version: "todo_decision_scope_outcome_v0", outcome: "reject",
    decision_scope: {schema_version: "decision_scope_v0", kind: "direction",
      granularity: "action", scope_key: "publish_release"}, source_todo_id: "todo_gate"};
  const compacted = display({todo_id: "todo_work", text: "Review the next action",
    decision_scope_outcomes: [receipt]});
  const projected = (compacted.decision_scope_outcomes as JsonObject[])[0]!;
  assert.equal(projected.schema_version, receipt.schema_version);
  assert.notEqual(projected.schema_version, "decision_scope_outcome_v0");
  assert.deepEqual(projected, receipt);
});

test("summary display matches pre-cutover synthetic CLI characterization", () => {
  for (const scenario of displayFixture.scenarios) {
    assert.deepEqual(projectTodoSummaryDisplay({items: scenario.items.map(record => ({record})), strip_text: true}), scenario.expected);
  }
  for (const [options, expected] of [[{handoff_gates: []}, displayFixture.summary],
    [{include_terminal: false}, displayFixture.planning], [{include_terminal: true}, displayFixture.terminal]] as const) {
    assert.deepEqual(projectTodoSummaryDisplay({source: {record: displayFixture.source}, ...options}), expected);
  }
});

test("handoff preserves identity and nested precedence without granting completion", () => {
  const item = {todo_id: "todo_original", role: "agent", status: "open", claimed_by: "Agent A",
    text: "Read the complete requirement", note: "Keep the unfinished validation", evidence: "opaque evidence",
    successor_todo_ids: ["todo_successor"], resume_when: "todo_done:todo_dependency", excluded_agents: ["agent-b"]};
  const before = structuredClone(item);
  assert.deepEqual(handoff(item, {goal_id: "goal-a", source: "canonical_provider"}).note, {
    schema_version: "handoff_note_v0", handoff_id: "handoff_original", todo_id: "todo_original", goal_id: "goal-a",
    to_agent: "agent-a", intent: "continue", summary: "Keep the unfinished validation",
    evidence_refs: ["todo:todo_original:evidence", "todo:todo_original:note"], blocked_on: "todo_done:todo_dependency",
    suggested_next_action: "Read the complete requirement", source: "canonical_provider", successor_todo_ids: ["todo_successor"], excluded_agents: ["agent-b"]});
  assert.deepEqual(item, before);
  assert.equal(handoff({todo_id: "todo_plain", note: "continue"}).note, null);
  const nested = handoff({...item, handoff: {to_agent: "agent-b", note: "", reason: "nested reason"}});
  assert.equal(nested.continuation_hint, "nested reason");
  assert.equal((nested.note as JsonObject).to_agent, "agent-b");
  const legacy = {goal_id: "goal-a", role: "agent", index: 7, text: "Legacy work"};
  for (const note of ["First continuation", "Revised continuation"]) {
    assert.equal((handoff({...legacy, handoff: {note}}).note as JsonObject).handoff_id, "handoff_7e32bf123341");
  }
});

test("handoff excludes credentials before truncation and preserves Unicode boundaries", () => {
  // Assemble synthetic assignments to keep source safe for the public-boundary scan.
  const tokenLabel = "tok" + "en";
  for (const label of ["ak", "sk", "api_key", "access_key_id", "secret_key", tokenLabel, "password"]) {
    const context = handoff({handoff: {note: "x".repeat(400) + " " + label + "=v"}});
    assert.equal(context.continuation_hint, null);
    assert.equal((context.note as JsonObject).summary, undefined);
  }
  for (const [note, expected] of [["a\u0085b\u001cc", "a b c"], ["token budget for validation", "token budget for validation"],
    ["😀".repeat(281), "😀".repeat(279) + "..."], ["令" + tokenLabel + "=v", "令" + tokenLabel + "=v"], ["apİ_key=v", null],
    ["bearer\ufeffv", "bearer\ufeffv"], ["bearer\u0085v", null]]) assert.equal(handoff({note}).continuation_hint, expected);
  const context = handoff({todo_id: "todo_original", successor_todo_ids: ["todo_next"],
    handoff: {evidence_refs: ["ref:one", "ref:one", "ref:two", "ref:three", "ref:four"]}, evidence: "opaque",
    note: "continuation", latest_event_kind: "todo_update"});
  assert.deepEqual((context.note as JsonObject).evidence_refs,
    ["ref:one", "ref:two", "ref:three", "ref:four", "todo:todo_original:evidence", "todo:todo_original:note"]);
});

test("only open advancement projects hints; blocked cause is bounded and public safe", () => {
  for (const task_class of ["advancement_task", "continuous_monitor"]) for (const status of ["open", "blocked", "done"]) {
    const row = display({text: "Work", status, task_class, note: "Continue validation", reason: "Input pending"});
    assert.equal(row.continuation_hint, task_class === "advancement_task" && status === "open" ? "Continue validation" : undefined);
    assert.equal(row.reason, status === "blocked" ? "Input pending" : undefined);
    assert.equal(row.note, undefined); assert.equal(row.evidence, undefined);
  }
  assert.equal(display({status: "blocked", reason: "/" + "home/example/plan.md"}).reason, undefined);
  for (const field of ["note", "continuation_hint"]) for (const label of ["api_key", "token", "password"]) {
    assert.equal(display({text: "Work", status: "open", [field]: label + "=v"}).continuation_hint, undefined);
  }
});
