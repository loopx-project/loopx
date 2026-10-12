import assert from "node:assert/strict";
import {mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";

import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {coordinationTodoReadModel} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {TODO_CANONICAL_READ_RECORD_SCHEMA} from "../../loopx/control_plane/coordination/coordination_state_contract.ts";
import {readLocalCoordinationTodoSource} from "../../loopx/control_plane/coordination/local_authority_read.ts";
import {completionTurnOperationId,
  COORDINATION_TODO_TERMINAL_LIFECYCLE_RECEIPT_SCHEMA} from "../../loopx/control_plane/coordination/todo_terminal_lifecycle.ts";

const REQUEST_SCHEMA = "loopx_local_coordination_todo_source_request_v0";
const GOAL = "goal-a";
const TODO = "todo-a";
const AGENT = "agent-a";
const TURN = "turn-a";

function projection(status: "open" | "done", terminal = false) {
  const todos = [{
    schema_version: "todo_item_v0", todo_id: TODO, role: "agent",
    status, done: status === "done", text: "Synthetic Todo",
    archive_state: "active", source_section: "Agent Todo",
    ...(terminal ? {
      no_followup: true, completion_continuation: "no_followup",
      completion_turn_key: TURN, last_actor_agent_id: AGENT,
      completion_receipt_id: `tcw_${"a".repeat(64)}`,
      completed_at: "2026-10-08T00:00:00Z",
    } : {}),
  }];
  return {goal_id: GOAL, handoff_mode: "soft_claim", todos, leases: [],
    todo_read_model: coordinationTodoReadModel(todos, TODO_CANONICAL_READ_RECORD_SCHEMA)};
}

for (const kind of ["file", "sqlite"] as const) {
  test(`${kind} historical Todo source survives a later mutation and rejects false lineage`, async (t) => {
    const root = await mkdtemp(join(tmpdir(), "loopx-todo-source-"));
    t.after(() => rm(root, {recursive: true, force: true}));
    const directory = join(root, "authority", kind);
    const store: AuthorityStore = kind === "file"
      ? new FileAuthorityStore(directory, GOAL)
      : new SqliteAuthorityStore(directory, GOAL);
    const first = await store.commitAuthority({expected_provider_revision: null,
      operation_id: "seed", events: [], next_projection: projection("open"), receipts: []});
    assert.equal(first.status, "applied");
    if (first.status !== "applied") return;
    const identity = await store.storeIdentity();
    assert.equal(identity.status, "available");
    if (identity.status !== "available") return;
    const source = {
      source_authority: `${kind}_v0`, store_identity: identity.store_identity,
      provider_revision: first.provider_revision, cursor: first.cursor,
    };
    const request = {schema_version: REQUEST_SCHEMA, runtime_root: root, goal_id: GOAL, source};
    const dependencies = {createStore: () => store};
    const second = await store.commitAuthority({expected_provider_revision: first.provider_revision,
      operation_id: "later", events: [], next_projection: projection("done"), receipts: []});
    assert.equal(second.status, "applied");
    const historical = await readLocalCoordinationTodoSource(request, dependencies);
    assert.equal(historical.status, "loaded", JSON.stringify(historical));
    assert.equal((historical.todos as Record<string, unknown>[])[0]?.status, "open");
    assert.equal(historical.provider_revision, first.provider_revision);
    assert.equal((await readLocalCoordinationTodoSource({
      ...request, source: {...source, store_identity: "foreign"},
    }, dependencies)).reason_code, "todo_source_lineage_mismatch");
    assert.equal((await readLocalCoordinationTodoSource({
      ...request, source: {...source, provider_revision: second.provider_revision},
    }, dependencies)).reason_code, "todo_source_history_mismatch");
    assert.equal((await readLocalCoordinationTodoSource({
      ...request, source: {...source, cursor: "999"},
    }, dependencies)).reason_code, "todo_source_history_unavailable");
    assert.equal((await readLocalCoordinationTodoSource({
      ...request, source: {...source, cursor: "bad"},
    }, dependencies)).reason_code, "todo_source_anchor_invalid");
    assert.equal((await readLocalCoordinationTodoSource({
      ...request, source: {...source, cursor: "0"},
    }, dependencies)).reason_code, "todo_source_anchor_invalid");
  });

  test(`${kind} terminal source binds the completion receipt, Turn and actor`, async (t) => {
    const root = await mkdtemp(join(tmpdir(), "loopx-terminal-source-"));
    t.after(() => rm(root, {recursive: true, force: true}));
    const directory = join(root, "authority", kind);
    const store: AuthorityStore = kind === "file"
      ? new FileAuthorityStore(directory, GOAL)
      : new SqliteAuthorityStore(directory, GOAL);
    const operationId = completionTurnOperationId({
      goal_id: GOAL, todo_id: TODO, requested_completion_turn_key: TURN,
    }, true);
    const terminalProjection = projection("done", true);
    const receipt = {schema_version: COORDINATION_TODO_TERMINAL_LIFECYCLE_RECEIPT_SCHEMA,
      operation_id: operationId, goal_id: GOAL, todo_id: TODO, command: "complete",
      request_sha256: "synthetic", result: {
        completion_identity_key: TURN,
        completion_receipt_id: `tcw_${"a".repeat(64)}`,
        completed_at: "2026-10-08T00:00:00Z",
      }};
    const committed = await store.commitAuthority({expected_provider_revision: null,
      operation_id: operationId, events: [], next_projection: terminalProjection, receipts: [receipt]});
    assert.equal(committed.status, "applied");
    const identity = await store.storeIdentity();
    assert.equal(identity.status, "available");
    if (identity.status !== "available") return;
    const request = {schema_version: REQUEST_SCHEMA, runtime_root: root, goal_id: GOAL,
      terminal: {source_authority: `${kind}_v0`, store_identity: identity.store_identity,
        todo_id: TODO, agent_id: AGENT, completion_turn_key: TURN}};
    const dependencies = {createStore: () => store};
    const later = await store.commitAuthority({expected_provider_revision:
      committed.status === "applied" ? committed.provider_revision : null,
    operation_id: "later", events: [], next_projection: projection("open"), receipts: []});
    assert.equal(later.status, "applied");
    const historical = await readLocalCoordinationTodoSource(request, dependencies);
    assert.equal(historical.status, "loaded", JSON.stringify(historical));
    assert.equal(historical.cursor, committed.status === "applied" ? committed.cursor : "");
    assert.deepEqual(historical.completion, {
      todo_id: TODO, agent_id: AGENT, completion_turn_key: TURN,
      completion_receipt_id: `tcw_${"a".repeat(64)}`,
      completed_at: "2026-10-08T00:00:00Z",
    });
    assert.equal((historical.todos as Record<string, unknown>[])[0]?.no_followup, true);
    assert.equal((await readLocalCoordinationTodoSource({
      ...request, terminal: {...request.terminal, agent_id: "wrong"},
    }, dependencies)).reason_code, "todo_source_terminal_identity_mismatch");
    assert.equal((await readLocalCoordinationTodoSource({
      ...request, terminal: {...request.terminal, completion_turn_key: "wrong"},
    }, dependencies)).status, "missing");
    assert.equal((await readLocalCoordinationTodoSource({
      ...request, terminal: {...request.terminal, source_authority: "foreign_v0"},
    }, dependencies)).reason_code, "todo_source_lineage_mismatch");
    assert.equal((await readLocalCoordinationTodoSource({
      ...request, terminal: {...request.terminal, store_identity: "foreign"},
    }, dependencies)).reason_code, "todo_source_lineage_mismatch");
  });
}
