import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import type {AuthorityStoreConformanceFactory} from "./authority_store_conformance.ts";
import {productionScaleConsumerScopeFixture} from "./production_scale_coordination_fixture.ts";
import {runPythonReadConsumer} from "./python_read_consumer.ts";

// The actual Python read consumer still hosts rendering; policy runs in TS.
const CONSUMER = `
import json, sys
from loopx.control_plane.coordination.local_authority import canonical_todo_summary_fields
from loopx.control_plane.todos.goal_todo_projection import filtered_todo_summary
from loopx.control_plane.todos.quota_summary import summarize_user_todos_for_quota
p=json.load(sys.stdin)
fields=canonical_todo_summary_fields(p['todos'])
summary=fields['user_todos']
selected=filtered_todo_summary(summary,role='user',agent_id='agent-a')
limited=filtered_todo_summary(summary,role='user',agent_id='agent-a',item_limit=1)
quota=summarize_user_todos_for_quota(summary,agent_identity={'agent_id':'agent-a'},filter_user_gate_blocks_agent=True)
base=canonical_todo_summary_fields([row for row in p['todos'] if not row['todo_id'].startswith('todo_scope_')])['user_todos']
base_quota=summarize_user_todos_for_quota(base,agent_identity={'agent_id':'agent-a'},filter_user_gate_blocks_agent=True)
prefix=lambda values: sorted(row['todo_id'] for row in values if row['todo_id'].startswith('todo_scope_'))
result={'selected':prefix(selected['items']), 'quota_delta':quota['open_count']-base_quota['open_count'],
 'limited':len(limited['items']), 'counts_equal':selected['total_count']==limited['total_count'],
 'whole':prefix(summary['items']),
 'recent':[row['todo_id'] for row in fields['agent_todos'].get('recent_completed_advancement_items', [])[:2]],
 'filtered_peer':filtered_todo_summary(summary,role='user',agent_id='agent-a',todo_id='todo_scope_peer_gate')['items'],
 'succession_gap':filtered_todo_summary(fields['agent_todos'],role='agent',todo_id=p['cases']['inferred_source']).get('completed_without_successor_count',0)}
print(json.dumps(result))
`;
export function registerTodoConsumerScopeConformance(name: string, factory: AuthorityStoreConformanceFactory): void {
  for (const schema of ["native", "legacy"] as const) test(`${name}: full-source Agent read addressing (${schema})`, async context => {
    const {store} = await factory(context);
    const {projection, cases} = productionScaleConsumerScopeFixture("consumer-scope", schema);
    const recent = (projection.todos as JsonObject[]).filter(todo => todo.status === "done" &&
      todo.task_class === "advancement_task" && todo.archive_state !== "archive").slice(0, 2);
    assert.equal(recent.length, 2);
    recent[0].completed_at = "2099-01-01T10:00:00.000001+08:00";
    recent[0].updated_at = "2099-12-01T00:00:00Z";
    recent[1].completed_at = "2099-01-01T02:00:00.000002Z";
    assert.equal((await store.commitAuthority({operation_id: "scope-source", expected_provider_revision: null,
      next_projection: projection, events: [], receipts: []})).status, "applied");
    const before = await store.loadAuthority(); assert.equal(before.status, "loaded");
    if (before.status !== "loaded") return;
    const child = runPythonReadConsumer(CONSUMER, {todos: before.head.todos, cases});
    assert.equal(child.status, 0, child.stderr);
    const result = JSON.parse(child.stdout);
    const expected = ["todo_scope_explicit_action", "todo_scope_explicit_gate", "todo_scope_global"];
    assert.deepEqual(result.selected, expected); assert.equal(result.quota_delta, 3);
    assert.equal(result.whole.length, 5); assert.deepEqual(result.filtered_peer, []);
    assert.equal(result.limited, 1); assert.equal(result.counts_equal, true);
    assert.equal(result.succession_gap, 0);
    assert.deepEqual(result.recent, [recent[1].todo_id, recent[0].todo_id]);
    assert.deepEqual(await store.loadAuthority(), before, "read consumers must never mutate authority");
  });
}
