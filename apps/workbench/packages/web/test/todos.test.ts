import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Todo } from '../src/api.js';
import { buildTodoBoard, indentPlace, linkableThreads, moveTodo, openRows, outdentPlace, type TodoNode } from '../src/todos.js';

const todo = (id: string, over: Partial<Todo> = {}): Todo => ({
  id,
  text: `plan ${id}`,
  done: false,
  parentId: null,
  position: 0,
  createdAt: '2025-01-01T00:00:00Z',
  doneAt: null,
  threads: [],
  ...over,
});

test('orders plans by position and nests sub-items under their plan', () => {
  const board = buildTodoBoard([
    todo('b', { position: 2 }),
    todo('a', { position: 1 }),
    todo('a2', { parentId: 'a', position: 2 }),
    todo('a1', { parentId: 'a', position: 1 }),
    todo('orphan', { parentId: 'missing' }),
  ]);
  assert.deepEqual(board.open.map((t: TodoNode) => t.id), ['a', 'b']);
  assert.deepEqual(board.open[0].children.map((t) => t.id), ['a1', 'a2']);
});

test('sinks finished plans into a done group, most recently finished first', () => {
  const board = buildTodoBoard([
    todo('old-done', { done: true, doneAt: '2025-01-02T00:00:00Z', position: 1 }),
    todo('open', { position: 2 }),
    todo('new-done', { done: true, doneAt: '2025-01-05T00:00:00Z', position: 3 }),
    todo('undated-done', { done: true, position: 4 }),
  ]);
  assert.deepEqual(board.open.map((t) => t.id), ['open']);
  assert.deepEqual(board.done.map((t) => t.id), ['new-done', 'old-done', 'undated-done']);
});

test('linkableThreads skips threads the plan already has and keeps recency order', () => {
  // Only ids matter here; the helper takes full Thread views.
  const threads = [
    { id: 't1' },
    { id: 't2' },
    { id: 't3' },
  ] as unknown as Todo['threads'];
  const todos = [todo('p', { threads: [threads[1]] })];
  assert.deepEqual(linkableThreads(todos, 'p', threads).map((t) => t.id), ['t1', 't3']);
  // An unknown plan (race with a delete) offers everything.
  assert.equal(linkableThreads(todos, 'gone', threads).length, 3);
});

const ids = (list: Todo[], parentId: string | null) =>
  list.filter((t) => t.parentId === parentId).sort((a, b) => a.position - b.position).map((t) => t.id);

test('moveTodo reorders a level and renumbers it, as the server does', () => {
  const list = [todo('a', { position: 1 }), todo('b', { position: 2 }), todo('c', { position: 5 })];
  const up = moveTodo(list, 'c', { parentId: null, before: 'a' });
  assert.deepEqual(ids(up, null), ['c', 'a', 'b']);
  assert.deepEqual(up.map((t) => t.position).sort(), [1, 2, 3]);
  assert.deepEqual(ids(moveTodo(list, 'a', { parentId: null, before: null }), null), ['b', 'c', 'a']);
  assert.equal(moveTodo(list, 'missing', { parentId: null, before: null }), list);
});

test('Tab puts a plan under the open plan above it; Shift+Tab brings it back right after', () => {
  const list = [todo('a', { position: 1 }), todo('b', { position: 2 }), todo('c', { position: 3 }), todo('a1', { parentId: 'a', position: 1 })];
  const board = buildTodoBoard(list);
  assert.equal(indentPlace(board, 'a'), null, 'the first plan has nothing above it');
  assert.equal(indentPlace(board, 'a1'), null, 'a sub-item stays one level down');
  assert.deepEqual(indentPlace(board, 'b'), { parentId: 'a', before: null });

  const indented = moveTodo(list, 'b', indentPlace(board, 'b')!);
  assert.deepEqual(ids(indented, 'a'), ['a1', 'b']);
  assert.deepEqual(indentPlace(buildTodoBoard(indented), 'c'), { parentId: 'a', before: null }, 'the plan above may have sub-items');
  const withKids = buildTodoBoard([todo('x', { position: 0 }), ...list]);
  assert.equal(indentPlace(withKids, 'a'), null, 'a plan with sub-items of its own stays a plan');

  assert.deepEqual(outdentPlace(indented, 'b'), { parentId: null, before: 'c' });
  assert.deepEqual(ids(moveTodo(indented, 'b', outdentPlace(indented, 'b')!), null), ['a', 'b', 'c']);
  assert.equal(outdentPlace(indented, 'a'), null, 'a plan is already at the top');
});

test('openRows reads the open list top to bottom, sub-items after their plan', () => {
  const board = buildTodoBoard([todo('a', { position: 1 }), todo('b', { position: 2 }), todo('a1', { parentId: 'a', position: 1 })]);
  assert.deepEqual(openRows(board).map((t) => t.id), ['a', 'a1', 'b']);
});
