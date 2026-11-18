import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { projectStatus } from '../src/projection.mjs';
const fixture = JSON.parse(readFileSync(new URL('./status-1.4.2.json', import.meta.url)));
test('projects a sanitized real 1.4.2 status, preserving source identity and completeness', () => {
  const view = projectStatus(fixture, 'plugin-probe');
  assert.equal(view.title, 'plugin-probe');
  assert.equal(view.complete, true);
  assert.equal(view.items.length, 1);
  assert.ok(view.items[0].id.startsWith('agent:todo_'));
  assert.equal(view.items[0].status, 'open');
  assert.equal(view.updatedAt, undefined);
});
test('scopes, missing goals, truncated lists and malformed item identity are explicit', () => {
  assert.throws(() => projectStatus(fixture, 'different-goal'), /scope/);
  assert.throws(() => projectStatus({ ...fixture, attention_queue: { items: [] } }, 'plugin-probe'), /not_found/);
  const truncated = structuredClone(fixture);
  truncated.attention_queue.items[0].agent_todos.total_count = 100;
  assert.equal(projectStatus(truncated, 'plugin-probe').complete, false);
  delete truncated.attention_queue.items[0].agent_todos.items[0].todo_id;
  assert.equal(projectStatus(truncated, 'plugin-probe').items.length, 0);
});
