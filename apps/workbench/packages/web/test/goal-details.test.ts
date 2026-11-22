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
    assert.deepEqual(toggles().map(button => button.props['aria-expanded']), [false, false, false, false]);
    await act(async () => { toggles()[1].props.onClick(); await tick(); });
    assert.equal(paths.length, 1);
    assert.match(JSON.stringify(renderer!.toJSON()), /Write a proposal/);
    assert.equal(renderer!.root.findAllByProps({ className: 'goal-tasks-board' }).length, 0);
    assert.equal(renderer!.root.findAllByProps({ className: 'goal-tasks-switch' }).length, 0);
    await act(async () => { toggles()[2].props.onClick(); await tick(); });
    assert.equal(paths.length, 2);
    assert.equal(renderer!.root.findAllByProps({ role: 'alert' }).length, 1);
    filesFail = false;
    const refresh = renderer!.root.findAll(node => node.type === 'button' && node.children.includes(t('loopx.files.refresh')))[0];
    await act(async () => { refresh.props.onClick(); await tick(); });
    assert.equal(renderer!.root.findAllByProps({ role: 'alert' }).length, 0);
    assert.match(JSON.stringify(renderer!.toJSON()), new RegExp(t('loopx.files.empty')));
    assert.ok(!paths.some(path => path.includes('acceptance')), 'acceptance stays unread until requested');
    await act(async () => { toggles()[3].props.onClick(); await tick(); });
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
    assert.deepEqual(renderer!.root.findAllByProps({ className: 'goal-detail-toggle' }).map(button => button.props['aria-expanded']), [false, false, false, false]);
  } finally { await act(async () => renderer?.unmount()); }
});

test('an unbound Core Goal reads every section without creating a conversation', async () => {
  const saved = globalThis.fetch;
  const calls: { path: string; method: string }[] = [];
  let joined = 0, directoryPicks = 0, renderer: ReactTestRenderer | undefined;
  globalThis.fetch = async (input, init) => {
    const path = String(input); calls.push({ path, method: init?.method ?? 'GET' });
    if (path.includes('status.json')) return Response.json(tasks);
    if (path.includes('goal-results')) return Response.json({ items: [], next_cursor: null, unavailable_count: 0, unavailable_todo_ids: [] });
    if (path.includes('goal-acceptance')) return Response.json({ ok: true, goal_id: 'g', provider_revision: '1', goal_acceptance_contract: { lifecycle_state: 'active', objective: 'Review proposal', criteria: [{ id: 'check', description: 'Review evidence' }] } });
    if (path.includes('/api/actions')) return Response.json({ proposals: [] });
    throw new Error(`Unexpected request: ${path}`);
  };
  try {
    await act(async () => { renderer = create(React.createElement(StewardOverview, { token: undefined, goals: [goal], attention: [], failure: null, loading: false, threads: [], defaultAgent: 'agent', onOpenThread() { joined++; }, onChanged() {}, onRetry() {}, async pickDirectory() { directoryPicks++; return '/workspace'; } })); });
    const expand = renderer!.root.findAll(node => node.type === 'button' && node.children.includes(t('loopx.panel.openGoal')))[0];
    await act(async () => expand.props.onClick());
    assert.equal(calls.length, 0);
    const toggles = () => renderer!.root.findAllByProps({ className: 'goal-detail-toggle' });
    assert.deepEqual(toggles().map(button => button.props['aria-expanded']), [false, false, false, false]);
    for (let index = 0; index < 4; index++) await act(async () => { toggles()[index].props.onClick(); await tick(); });
    const details = renderer!.root.findByProps({ 'data-goal-detail': 'g' });
    assert.match(JSON.stringify(renderer!.toJSON()), /A verifiable result/);
    assert.match(JSON.stringify(renderer!.toJSON()), /Write a proposal/);
    assert.match(JSON.stringify(renderer!.toJSON()), /Review evidence/);
    assert.equal(details.findAll(node => node.type === 'button' && [t('loopx.tabConversations'), t('loopx.accept.verify'), t('loopx.accept.clear'), t('loopx.accept.plan.adopt')].some(label => node.children.includes(label))).length, 0);
    assert.equal(joined, 0);
    assert.equal(directoryPicks, 0);
    assert.ok(calls.length >= 5);
    assert.ok(calls.every(call => call.method === 'GET'));
    assert.ok(!calls.some(call => call.path.includes('/launch/')));
  } finally { await act(async () => renderer?.unmount()); globalThis.fetch = saved; }
});

test('a focused unbound stopped Goal opens its group and can be reopened after collapse', async () => {
  const stopped = { ...goal, activationState: 'stopped' };
  let renderer: ReactTestRenderer | undefined;
  const p = { token: undefined, goals: [stopped], attention: [], failure: null, loading: false, threads: [] as Thread[], defaultAgent: '', onOpenThread() {}, onChanged() {}, onRetry() {}, focusedGoal: { goalId: 'g' } };
  try {
    await act(async () => { renderer = create(React.createElement(StewardOverview, p)); });
    assert.equal(renderer!.root.findAllByProps({ 'data-goal-detail': 'g' }).length, 1);
    assert.equal(renderer!.root.findByProps({ className: 'overview-toggle' }).props['aria-expanded'], true);
    await act(async () => renderer!.root.findAll(node => node.type === 'button' && node.children.includes(t('loopx.panel.closeGoal')))[0].props.onClick());
    assert.equal(renderer!.root.findAllByProps({ 'data-goal-detail': 'g' }).length, 0);
    await act(async () => renderer!.update(React.createElement(StewardOverview, { ...p, focusedGoal: { goalId: 'g' } })));
    assert.equal(renderer!.root.findAllByProps({ 'data-goal-detail': 'g' }).length, 1);
    await act(async () => renderer!.update(React.createElement(StewardOverview, { ...p, goals: [stopped] })));
    assert.equal(renderer!.root.findAllByProps({ 'data-goal-detail': 'g' }).length, 1);
  } finally { await act(async () => renderer?.unmount()); }
});

test('the board shortcut is hidden for eight unbound Core Goals and appears for a bound conversation', async () => {
  const goals = Array.from({ length: 8 }, (_, index) => ({ ...goal, goalId: `core-${index}`, title: `Core Goal ${index}` }));
  const plain = Array.from({ length: 5 }, (_, index) => ({ id: `ordinary-${index}`, title: `Ordinary ${index}`, agent: 'agent', cwd: '/workspace', archivedAt: null } as Thread));
  let opened = 0, renderer: ReactTestRenderer | undefined;
  const p = { token: undefined, goals, attention: [], failure: null, loading: false, threads: plain, defaultAgent: '', onOpenThread() {}, onChanged() {}, onRetry() {}, onOpenAll() { opened++; } };
  try {
    await act(async () => { renderer = create(React.createElement(StewardOverview, p)); });
    assert.equal(renderer!.root.findAllByProps({ className: 'overview-trace' }).length, 0);
    assert.equal(renderer!.root.findAll(node => node.props['data-goal-id']).length, 8);
    await act(async () => renderer!.update(React.createElement(StewardOverview, { ...p, threads: [...plain, { ...plain[0], id: 'bound', loopxInfo: { goalId: goals[0].goalId, goalTitle: goals[0].title, basis: 'core', source: 'fixture' } }] })));
    const shortcut = renderer!.root.findByProps({ className: 'overview-trace' });
    assert.ok(shortcut.children.includes(t('goals.conversationsBoard')));
    await act(async () => shortcut.props.onClick());
    assert.equal(opened, 1);
  } finally { await act(async () => renderer?.unmount()); }
});
