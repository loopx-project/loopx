import assert from 'node:assert/strict';
import { test } from 'node:test';
import { workspaceCommandEntries, filterWorkspaceCommands, isPaletteShortcut } from './workspace-command-entries.ts';

const goals = [
  {goalId: 'one', title: '发布计划', agentTodos: [{todoId: 'task-a', text: 'Verify Export', claimedBy: 'worker'}]},
  {goalId: 'two', title: 'Research', agentTodos: [{todoId: 'task-b', text: 'Other task'}]},
  {goalId: 'loading', title: 'Loading', loadState: 'loading', agentTodos: [{todoId: 'stale', text: 'Stale task'}]},
];
const items = [
  {kind: 'output', output: {outputId: 'result-a', goalId: 'one', title: '验证报告'}},
  {kind: 'output', output: {outputId: 'result-b', goalId: 'two', title: 'Other result'}},
  ...['draft', 'ready', 'gated', 'stale', 'error', 'applied', 'rejected', 'deferred'].map(status => ({kind: 'proposal', proposal: {previewId: status, goalId: 'one', title: 'Review change', status}})),
  {kind: 'proposal', proposal: {previewId: 'foreign', goalId: 'two', title: 'Other preview', status: 'ready'}},
];
const input = {userTodos: [], sourceId: '/status.json', goals, goalId: 'one', items, readOnly: false, unavailable: false};
test('indexes Goals and only current loaded Goal details and reviewable previews', () => {
  const before = JSON.stringify(input);
  const entries = workspaceCommandEntries(input);
  assert.deepEqual(entries.map(entry => entry.id), ['goal:one', 'goal:two', 'goal:loading', 'todo:task-a', 'output:result-a', 'proposal:draft', 'proposal:ready', 'proposal:gated']);
  assert.equal(entries[2].disabled, 'loading');
  assert.equal(entries[3].destination.selection.item.goalId, 'one');
  assert.equal(JSON.stringify(input), before, 'projection is pure');
  assert.deepEqual(workspaceCommandEntries({...input, goalId: 'loading'}).map(entry => entry.group), ['goal', 'goal', 'goal']);
});
test('read-only and unavailable sources cannot borrow local actions or stale entries', () => {
  assert.ok(workspaceCommandEntries({...input, readOnly: true}).every(entry => entry.group !== 'proposal'));
  assert.ok(workspaceCommandEntries({...input, goals: goals.map(goal => ({...goal, sourceAccess: {readOnly: true}}))}).every(entry => entry.group !== 'proposal'));
  assert.deepEqual(workspaceCommandEntries({...input, unavailable: true}), []);
  assert.deepEqual(workspaceCommandEntries({...input, goals: [], goalId: 'one'}), []);
  const changed = workspaceCommandEntries({...input, goalId: 'two'});
  assert.ok(changed.some(entry => entry.id === 'todo:task-b'));
  assert.ok(changed.every(entry => !['todo:task-a', 'output:result-a', 'proposal:ready'].includes(entry.id)));
});
test('search supports Chinese, case, width normalization, multiple terms and empty results', () => {
  const entries = workspaceCommandEntries(input);
  assert.deepEqual(filterWorkspaceCommands(entries, '报告').map(entry => entry.id), ['output:result-a']);
  assert.deepEqual(filterWorkspaceCommands(entries, ' ＥＸＰＯＲＴ verify ').map(entry => entry.id), ['todo:task-a']);
  assert.deepEqual(filterWorkspaceCommands(entries, 'task-a').map(entry => entry.id), ['todo:task-a']);
  assert.deepEqual(filterWorkspaceCommands(entries, 'unknown'), []);
  assert.equal(filterWorkspaceCommands(entries, ' ').length, entries.length);
});
test('keyboard shortcut respects IME, repeat and modifier combinations', () => {
  const key = {key: 'k', metaKey: false, ctrlKey: true, altKey: false, shiftKey: false, isComposing: false, keyCode: 75, repeat: false};
  assert.equal(isPaletteShortcut(key), true);
  assert.equal(isPaletteShortcut({...key, key: 'K', metaKey: true, ctrlKey: false}), true);
  for (const override of [{isComposing: true}, {keyCode: 229}, {repeat: true}, {altKey: true}, {shiftKey: true}, {ctrlKey: false}, {key: 'j'}]) {
    assert.equal(isPaletteShortcut({...key, ...override}), false);
  }
});

test('user_gate and user_action title/ID search reuses same-Goal, same-source attention details', () => {
  const userTodos = ['user_gate', 'user_action'].map((taskClass, index) => ({
    todoId: `user-${index}`, goalId: 'one', sourceId: '/status.json',
    text: index ? 'Provide export sample' : 'Approve export scope', blocking: !index,
    details: {taskClass, interaction: index ? 'user_action' : 'decision', lifecycle: 'open'},
  }));
  const scoped = {...input, userTodos: [...userTodos,
    {...userTodos[0], goalId: 'two', text: 'Other Goal gate'},
    {...userTodos[0], sourceId: '/remote.json', text: 'Other source gate'},
  ]};
  const before = JSON.stringify(scoped);
  for (const readOnly of [false, true]) {
    const entries = workspaceCommandEntries({...scoped, readOnly});
    for (const item of userTodos) {
      for (const query of [item.text, item.todoId]) {
        const matches = filterWorkspaceCommands(entries, query);
        assert.equal(matches.length, 1);
        assert.equal(matches[0].group, 'todo');
        assert.deepEqual(matches[0].destination, {kind: 'detail', selection: {kind: 'attention', item}});
      }
    }
    assert.equal(filterWorkspaceCommands(entries, 'Other Goal gate').length, 0);
    assert.equal(filterWorkspaceCommands(entries, 'Other source gate').length, 0);
  }
  assert.equal(JSON.stringify(scoped), before, 'search/navigation cannot mutate attention state');
  assert.deepEqual(workspaceCommandEntries({...scoped, unavailable: true}), []);
  assert.ok(workspaceCommandEntries({...scoped, goalId: 'loading'}).every(entry => entry.group === 'goal'));
  const remote = workspaceCommandEntries({...scoped, sourceId: '/remote.json', readOnly: true});
  assert.equal(filterWorkspaceCommands(remote, 'Other source gate').length, 1);
  assert.equal(filterWorkspaceCommands(remote, 'Approve export scope').length, 0);
});
