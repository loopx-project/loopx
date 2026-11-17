import assert from "node:assert/strict";
import test from "node:test";
import {prepareCompactionMemory} from "../../loopx/control_plane/quota/compaction_memory.ts";

const scope = {goal_id: "goal", agent_id: "agent", session_id: "native", profile: "codex_app_heartbeat"};
const source = {path: "/runtime/original.json", sha256: "a".repeat(64)};
const recovery = {path: "/runtime/required.json", sha256: "b".repeat(64)};
const note = "R1 failed isolation. R2 passed the targeted test; rollback remains unverified.";
const context = {goal_id: "goal", selected_todo: {todo_id: "work", title: "Check isolation", note},
  interaction_contract: {agent_channel: {required_reads: ["Read the unchanged original requirements."]}},
  unknown_evidence: {failed: true, instruction: "Do not infer acceptance."}};

test("memory preserves sourced negative distinctions without editing the source or inventing authority", () => {
  const before = structuredClone(context);
  const result = prepareCompactionMemory({context, scope, source, recovery});
  assert.equal(result.available, true);
  const doc = result.document as Record<string, any>;
  assert.deepEqual(doc.frontier, [{ref: "/selected_todo/note", note}]);
  assert.deepEqual(doc.coverage, ["/selected_todo/title", "/selected_todo/note"]);
  assert.deepEqual(doc.needed_reads, ["/interaction_contract/agent_channel/required_reads"]);
  assert.deepEqual(doc.binding, scope);
  assert.equal(doc.binding.context_epoch, undefined);
  assert.equal(doc.binding.turn_instance_id, undefined);
  assert.equal(doc.complete, undefined);
  assert.equal(doc.authority, undefined);
  assert.deepEqual(context, before);
  assert.deepEqual(prepareCompactionMemory({context, scope, source, recovery}), result);
});

test("long notes remain exact original pointers, never silently truncated memories", () => {
  const result = prepareCompactionMemory({context: {...context, selected_todo: {...context.selected_todo, note: note.repeat(80)}},
    scope, source, recovery});
  const doc = result.document as Record<string, any>;
  assert.deepEqual(doc.frontier, [{ref: "/selected_todo/note"}]);
  assert.deepEqual(doc.coverage, ["/selected_todo/title"]);
  assert.equal(doc.source.sha256, source.sha256);
});

test("scope and exact source changes cannot reuse an old memory revision", () => {
  for (const patch of [{goal_id: "foreign"}, {agent_id: "foreign"}])
    assert.throws(() => prepareCompactionMemory({context: {...context, ...patch}, scope, source, recovery}));
  const initial = prepareCompactionMemory({context, scope, source, recovery});
  const later = prepareCompactionMemory({context, scope, source: {...source, sha256: "c".repeat(64)}, recovery});
  assert.notEqual(initial.revision, later.revision);
  const withTurn = prepareCompactionMemory({context, scope: {...scope, turn_instance_id: "original"}, source, recovery});
  assert.equal((withTurn.document as Record<string, any>).binding.turn_instance_id, "original");
});

test("canonical selected note uses its own exact location; a foreign work row is ignored", () => {
  const todo = {todo_id: "work", title: "Verify rollback", note};
  const row = {source: "selected_todo", content: {ok: true, todo}};
  const result = prepareCompactionMemory({context: {...context,
    interaction_contract: {agent_channel: {work_context: {sources: [row]}}}}, scope, source, recovery});
  const doc = result.document as Record<string, any>;
  assert.deepEqual(doc.frontier, [{ref: "/interaction_contract/agent_channel/work_context/sources/0/content/todo/note", note}]);
  const other = prepareCompactionMemory({context: {...context,
    interaction_contract: {agent_channel: {work_context: {sources: [{...row, content: {todo: {...todo, todo_id: "other"}}}]}}}},
    scope, source, recovery});
  assert.equal((other.document as Record<string, any>).frontier[0].ref, "/selected_todo/note");
});
