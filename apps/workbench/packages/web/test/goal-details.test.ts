import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as React from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { GoalDetails } from '../src/components/GoalDetails';
import { LoopxGoalGroup } from '../src/components/LoopxGoalGroup';
import { StewardOverview } from '../src/components/StewardOverview';
import type { LoopxGoal } from '../src/loopx/status';
import type { Thread } from '../src/api';
import { t } from '../src/i18n';

(globalThis as unknown as { React: typeof React }).React = React;
const tick = () => new Promise<void>(resolve => setImmediate(resolve));
const goal = { goalId: 'g', title: 'Proposal', status: 'connected_without_run', activationState: 'active', objective: 'A verifiable result', parentId: null, agents: [], lifecyclePhase: null, updatedAt: null } satisfies LoopxGoal;
const thread = { id: 'thread', status: 'idle', loopxInfo: { goalId: 'g', goalTitle: 'Proposal' } } as Thread;
const tasks = { goal_filter: 'g', attention_queue: { items: [{ goal_id: 'g', user_todos: { items: [], total_count: 0, open_count: 0, done_count: 0 }, agent_todos: { items: [{ todo_id: 'one', text: 'Write a proposal', status: 'open', claimed_by: 'agent', task_class: 'advancement_task' }], total_count: 1, open_count: 1, done_count: 0 } }] } };

test('Goal details fetch each heavy reader only when opened, with list-only tasks and file failure recovery', async () => {
  const saved = globalThis.fetch;
  const paths: string[] = [];
  let filesFail = true, renderer: ReactTestRenderer | undefined;
  globalThis.fetch = async input => {
    const path = String(input); paths.push(path);
    if (path.includes('goal-results')) return filesFail ? Response.json({ error: 'unavailable' }, { status: 502 }) : Response.json({ items: [], next_cursor: null, unavailable_count: 0, unavailable_todo_ids: [] });
    if (path.includes('status.json')) return Response.json(tasks);
    return Response.json({ error: 'unavailable' }, { status: 502 });
  };
  try {
    await act(async () => { renderer = create(React.createElement(GoalDetails, { goalId: 'g', goal, threads: [thread], onBrowseFiles() {}, onOpenConversation() {} })); });
    assert.equal(paths.length, 0, 'collapsed details mount no readers');
    const toggles = () => renderer!.root.findAllByProps({ className: 'goal-detail-toggle' });
    assert.deepEqual(toggles().map(button => button.props['aria-expanded']), [false, false, false]);
    await act(async () => { toggles()[0].props.onClick(); await tick(); });
    assert.equal(paths.length, 1);
    assert.match(JSON.stringify(renderer!.toJSON()), /Write a proposal/);
    assert.equal(renderer!.root.findAllByProps({ className: 'goal-tasks-board' }).length, 0);
    assert.equal(renderer!.root.findAllByProps({ className: 'goal-tasks-switch' }).length, 0);
    await act(async () => { toggles()[1].props.onClick(); await tick(); });
    assert.equal(paths.length, 2);
    assert.equal(renderer!.root.findAllByProps({ role: 'alert' }).length, 1);
    filesFail = false;
    const refresh = renderer!.root.findAll(node => node.type === 'button' && node.children.includes(t('loopx.files.refresh')))[0];
    await act(async () => { refresh.props.onClick(); await tick(); });
    assert.equal(renderer!.root.findAllByProps({ role: 'alert' }).length, 0);
    assert.match(JSON.stringify(renderer!.toJSON()), new RegExp(t('loopx.files.empty')));
    assert.ok(!paths.some(path => path.includes('acceptance')), 'acceptance stays unread until requested');
    await act(async () => { toggles()[2].props.onClick(); await tick(); });
    assert.equal(renderer!.root.findAllByProps({ role: 'alert' }).length, 1, 'an unavailable acceptance read is visible');
    assert.doesNotMatch(JSON.stringify(renderer!.toJSON()), new RegExp(t('loopx.accept.noChecks')), 'an unavailable reader never claims there are no checks');
    assert.ok(paths.length > 3);
  } finally { await act(async () => renderer?.unmount()); globalThis.fetch = saved; }
});

test('Open Goal expands in place and switching conversations resets the Goal disclosures', async () => {
  let renderer: ReactTestRenderer | undefined;
  const p = { token: undefined, thread, goal, onChanged() {}, onBrowseFiles() {} };
  try {
    await act(async () => { renderer = create(React.createElement(LoopxGoalGroup, p)); });
    assert.equal(renderer!.root.findAllByProps({ 'data-goal-detail': 'g' }).length, 0);
    await act(async () => renderer!.root.findByProps({ className: 'overview-more loopx-group-open' }).props.onClick());
    assert.equal(renderer!.root.findAllByProps({ 'data-goal-detail': 'g' }).length, 1);
    assert.equal(renderer!.root.findByProps({ className: 'overview-more loopx-group-open' }).props['aria-expanded'], true);
    await act(async () => renderer!.update(React.createElement(LoopxGoalGroup, { ...p, thread: { ...thread, id: 'next' } })));
    assert.equal(renderer!.root.findAllByProps({ 'data-goal-detail': 'g' }).length, 0);
  } finally { await act(async () => renderer?.unmount()); }
});

test('steward Goals share the same lazy details, including stopped Goals', async () => {
  let renderer: ReactTestRenderer | undefined;
  try {
    await act(async () => { renderer = create(React.createElement(StewardOverview, { token: undefined, goals: [goal], attention: [], failure: null, loading: false, threads: [thread], defaultAgent: '', onOpenThread() {}, onChanged() {}, onRetry() {} })); });
    const open = renderer!.root.findAll(node => node.type === 'button' && node.children.includes(t('loopx.panel.openGoal')))[0];
    await act(async () => open.props.onClick());
    assert.equal(renderer!.root.findAllByProps({ 'data-goal-detail': 'g' }).length, 1);
    assert.deepEqual(renderer!.root.findAllByProps({ className: 'goal-detail-toggle' }).map(button => button.props['aria-expanded']), [false, false, false]);
  } finally { await act(async () => renderer?.unmount()); }
});
