import assert from 'node:assert/strict';
import { test } from 'node:test';
import * as React from 'react';
import { createElement } from 'react';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { Popover } from '../src/components/Popover.js';
import { GoalLifecycle } from '../src/components/GoalLifecycle.js';
import { LoopxGoalGroup } from '../src/components/LoopxGoalGroup.js';
import { StewardOverview } from '../src/components/StewardOverview.js';
import type { Thread } from '../src/api.js';
import type { LoopxAttention, LoopxGoal } from '../src/loopx/status.js';
import { t } from '../src/i18n/index.js';

(globalThis as unknown as { React: typeof React }).React = React;
const tick = () => new Promise<void>(resolve => setImmediate(resolve));
const goal = (goalId: string, activationState = 'active'): LoopxGoal => ({ goalId, title: `Goal ${goalId}`, activationState, status: 'connected_without_run', parentId: null, objective: null, lifecyclePhase: null, updatedAt: null, agents: [] });
const attention = (goalId: string, open = 0, waitingOn: string | null = null): LoopxAttention => ({ goalId, status: null, waitingOn, severity: null, recommendedAction: 'Review the proposal', nextAction: 'Prepare the next step', topTodo: { text: 'Choose the approach', priority: null, claimedBy: null, updatedAt: null }, lastActivityAt: null, userTodos: { open, done: 2, total: open + 2 }, agentTodos: { open: 1, done: 1, total: 2 } });
const props = () => ({ token: undefined, goals: [] as LoopxGoal[], attention: [] as LoopxAttention[], failure: null, loading: false, onChanged() {}, onRetry() {}, threads: [] as Thread[], onOpenThread(_id: string) {}, defaultAgent: 'codex' });
const openButton = (row: ReactTestInstance) => row.findAll(node => node.type === 'button' && node.children.includes(t('loopx.overview.openConversation')))[0]!;

test('an ordinary conversation has no Goal block and never reads Goal state', async () => {
  const saved = globalThis.fetch;
  let reads = 0;
  globalThis.fetch = async () => { reads++; throw new Error('unexpected read'); };
  let renderer: ReactTestRenderer | undefined;
  try {
    await act(async () => { renderer = create(createElement(LoopxGoalGroup, { token: undefined, thread: { id: 'plain', status: 'idle' } as Thread, onChanged() {} })); });
    assert.equal(renderer!.toJSON(), null);
    assert.equal(reads, 0);
  } finally { await act(async () => renderer?.unmount()); globalThis.fetch = saved; }
});

test('a bound Goal has compact progress and its real lifecycle, while a missing Goal has no actions', async () => {
  const g = goal('current');
  const thread = { id: 'current-thread', status: 'idle', loopxInfo: { goalId: g.goalId, goalTitle: g.title, missing: false } } as Thread;
  const a = attention(g.goalId, 1);
  let changed = 0;
  const p = { token: undefined, thread, goal: g, attention: a, onChanged() { changed++; } };
  let renderer: ReactTestRenderer | undefined;
  try {
    await act(async () => { renderer = create(createElement(LoopxGoalGroup, p)); });
    assert.match(JSON.stringify(renderer!.toJSON()), /Goal current/);
    assert.match(JSON.stringify(renderer!.toJSON()), /3\/5/);
    assert.equal(renderer!.root.findByType(GoalLifecycle).props.goal, g);
    const manage = renderer!.root.findAll(node => node.type === 'button' && node.props.className === 'tool-btn goal-lifecycle')[0]!;
    assert.ok(manage.children.length > 0, 'the lifecycle trigger has a visible label');
    await act(async () => renderer!.root.findByType(GoalLifecycle).props.onChanged());
    assert.equal(changed, 1);
    await act(async () => renderer!.update(createElement(LoopxGoalGroup, { ...p, thread: { ...thread, loopxInfo: { ...thread.loopxInfo!, missing: true } } })));
    assert.equal(renderer!.root.findAllByType(GoalLifecycle).length, 0);
    assert.equal(renderer!.root.findAllByType('button').length, 0);
  } finally { await act(async () => renderer?.unmount()); }
});

test('the steward groups owner work separately, folds stopped Goals, and preserves folding on refresh', async () => {
  const p = { ...props(), goals: [goal('need'), goal('work'), goal('stopped', 'stopped')], attention: [attention('need', 1), attention('work')] };
  let renderer: ReactTestRenderer | undefined;
  try {
    await act(async () => { renderer = create(createElement(StewardOverview, p)); });
    const groups = renderer!.root.findAllByType('section');
    assert.deepEqual(groups.map(group => group.props['aria-label']), [t('loopx.needsYou'), t('loopx.running'), t('loopx.stoppedShort')]);
    assert.deepEqual(groups.map(group => group.findByProps({ className: 'overview-toggle' }).props['aria-expanded']), [true, true, false]);
    assert.equal(renderer!.root.findAllByProps({ 'data-goal-id': 'need' }).length, 1, 'owner work is not duplicated under active');
    assert.equal(renderer!.root.findAllByProps({ 'data-goal-id': 'stopped' }).length, 0);
    await act(async () => groups[2]!.findByProps({ className: 'overview-toggle' }).props.onClick());
    const stoppedRow = renderer!.root.findByProps({ 'data-goal-id': 'stopped' });
    assert.equal(stoppedRow.findByType(GoalLifecycle).props.goal.activationState, 'stopped');
    assert.equal(stoppedRow.findAll(node => node.type === 'button' && node.children.includes(t('loopx.overview.openConversation'))).length, 0);
    await act(async () => renderer!.update(createElement(StewardOverview, { ...p, goals: [...p.goals] })));
    assert.equal(renderer!.root.findByProps({ 'data-goal-id': 'stopped' }).props.className, 'overview-goal');
  } finally { await act(async () => renderer?.unmount()); }
});

test('failed, loading and empty steward reads stay distinct and offer no empty board shortcut', async () => {
  let retried = 0, opened = 0;
  const p = { ...props(), onRetry() { retried++; }, onOpenAll() { opened++; } };
  let renderer: ReactTestRenderer | undefined;
  try {
    await act(async () => { renderer = create(createElement(StewardOverview, { ...p, failure: { kind: 'unavailable', detail: 'unavailable' } })); });
    assert.equal(renderer!.root.findAllByProps({ role: 'alert' }).length, 1);
    assert.doesNotMatch(JSON.stringify(renderer!.toJSON()), /0 活跃|还没有目标/);
    await act(async () => renderer!.root.findByProps({ className: 'icon-btn overview-refresh' }).props.onClick());
    assert.equal(retried, 1);
    assert.equal(renderer!.root.findAllByProps({ className: 'overview-trace' }).length, 0);
    assert.equal(opened, 0);
    await act(async () => renderer!.update(createElement(StewardOverview, { ...props(), loading: true })));
    assert.match(JSON.stringify(renderer!.toJSON()), new RegExp(t('common.loading')));
    assert.equal(renderer!.root.findAllByProps({ className: 'overview-trace' }).length, 0);
    assert.doesNotMatch(JSON.stringify(renderer!.toJSON()), /0 活跃/);
    await act(async () => renderer!.update(createElement(StewardOverview, props())));
    assert.match(JSON.stringify(renderer!.toJSON()), new RegExp(t('loopx.overview.empty')));
    assert.equal(renderer!.root.findAllByType('section').length, 0);
  } finally { await act(async () => renderer?.unmount()); }
});

test('Open conversation reuses an existing binding before asking for a directory or joining', async () => {
  const saved = globalThis.fetch;
  let joins = 0, picks = 0;
  globalThis.fetch = async () => { joins++; throw new Error('unexpected join'); };
  const opened: string[] = [];
  const p = { ...props(), goals: [goal('existing')], threads: [{ id: 'existing-thread', loopxInfo: { goalId: 'existing', missing: false } } as Thread], defaultAgent: '', onOpenThread(id: string) { opened.push(id); }, async pickDirectory() { picks++; return '/workspace'; } };
  let renderer: ReactTestRenderer | undefined;
  try {
    await act(async () => { renderer = create(createElement(StewardOverview, p)); });
    const button = openButton(renderer!.root.findByProps({ 'data-goal-id': 'existing' }));
    assert.equal(button.props.disabled, false, 'an existing conversation needs no new agent');
    await act(async () => { button.props.onClick(); await tick(); });
    assert.deepEqual(opened, ['existing-thread']);
    assert.equal(picks, 0);
    assert.equal(joins, 0);
  } finally { await act(async () => renderer?.unmount()); globalThis.fetch = saved; }
});

test('a first conversation joins with the selected directory; cancel and duplicate clicks do not join', async () => {
  const saved = globalThis.fetch;
  const bodies: unknown[] = [], opened: string[] = [];
  let picks = 0;
  let release!: (cwd: string | null) => void;
  globalThis.fetch = async (_input, init) => { bodies.push(JSON.parse(String(init?.body))); return Response.json({ threadId: 'joined-thread', goalId: 'new' }); };
  const p = { ...props(), goals: [goal('new')], onOpenThread(id: string) { opened.push(id); }, pickDirectory() { picks++; return new Promise<string | null>(resolve => { release = resolve; }); } };
  let renderer: ReactTestRenderer | undefined;
  try {
    await act(async () => { renderer = create(createElement(StewardOverview, p)); });
    const button = () => openButton(renderer!.root.findByProps({ 'data-goal-id': 'new' }));
    await act(async () => { button().props.onClick(); button().props.onClick(); await tick(); });
    assert.equal(picks, 1);
    assert.equal(button().props.disabled, true);
    await act(async () => { release(null); await tick(); });
    assert.deepEqual(bodies, []);
    assert.deepEqual(opened, []);
    await act(async () => { button().props.onClick(); await tick(); });
    await act(async () => { release('/workspace/project'); await tick(); });
    assert.deepEqual(bodies, [{ goalId: 'new', agent: 'codex', cwd: '/workspace/project' }]);
    assert.deepEqual(opened, ['joined-thread']);
  } finally { await act(async () => renderer?.unmount()); globalThis.fetch = saved; }
});

test('a failed join stays visible and a retry without a directory picker can open the conversation', async () => {
  const saved = globalThis.fetch;
  let calls = 0;
  const opened: string[] = [];
  globalThis.fetch = async () => ++calls === 1 ? Response.json({ error: 'workspace_required' }, { status: 400 }) : Response.json({ threadId: 'retry-thread' });
  let renderer: ReactTestRenderer | undefined;
  try {
    await act(async () => { renderer = create(createElement(StewardOverview, { ...props(), goals: [goal('retry')], onOpenThread(id: string) { opened.push(id); } })); });
    const button = () => openButton(renderer!.root.findByProps({ 'data-goal-id': 'retry' }));
    await act(async () => { button().props.onClick(); await tick(); });
    assert.equal(renderer!.root.findAllByProps({ role: 'alert' }).length, 1);
    assert.match(JSON.stringify(renderer!.toJSON()), /workspace_required/);
    await act(async () => { button().props.onClick(); await tick(); });
    assert.deepEqual(opened, ['retry-thread']);
    assert.equal(renderer!.root.findAllByProps({ role: 'alert' }).length, 0);
  } finally { await act(async () => renderer?.unmount()); globalThis.fetch = saved; }
});

test('unknown activation stays visible without offering lifecycle mutations, and unavailable agents do not look runnable', async () => {
  let renderer: ReactTestRenderer | undefined;
  try {
    await act(async () => { renderer = create(createElement(StewardOverview, { ...props(), defaultAgent: '', goals: [goal('unknown', 'unknown'), goal('active')] })); });
    assert.match(JSON.stringify(renderer!.toJSON()), new RegExp(t('loopx.overview.noAgent')));
    const activeRow = renderer!.root.findByProps({ 'data-goal-id': 'active' });
    assert.equal(openButton(activeRow).props.disabled, true);
    const unknownGroup = renderer!.root.findByProps({ 'aria-label': t('loopx.goal.status.unknown') });
    await act(async () => unknownGroup.findByProps({ className: 'overview-toggle' }).props.onClick());
    assert.equal(renderer!.root.findByProps({ 'data-goal-id': 'unknown' }).findAllByType(GoalLifecycle).length, 0);
  } finally { await act(async () => renderer?.unmount()); }
});

test('a delayed Goal read cannot replace the next conversation state', async () => {
  const saved = globalThis.fetch;
  let release!: (response: Response) => void;
  const binding = (goalId: string) => ({ goalId, goalAgentId: 'codex', managedAgentId: null, goalInstanceId: null, workspace: '/workspace', permission: 'read_only' });
  const read = (goalId: string, summary: string) => ({ binding: binding(goalId), state: { goalId, title: goalId, status: 'connected_without_run', summary, agentTodos: { total: 0, open: 0 }, userTodos: { total: 0, open: 0 } }, freshness: 'fresh' });
  globalThis.fetch = async input => String(input).includes('/first') ? new Promise<Response>(resolve => { release = resolve; }) : Response.json(read('second', 'The second conversation'));
  const thread = (id: string) => ({ id, status: 'idle', loopx: binding(id), loopxInfo: { goalId: id, goalTitle: id, missing: false, basis: 'workbench', source: 'test' } } as Partial<Thread>) as Thread;
  const p = { token: undefined, thread: thread('first'), onChanged() {} };
  let renderer: ReactTestRenderer | undefined;
  try {
    await act(async () => { renderer = create(createElement(LoopxGoalGroup, p)); await tick(); });
    await act(async () => { renderer!.update(createElement(LoopxGoalGroup, { ...p, thread: thread('second') })); await tick(); });
    await act(async () => { release(Response.json(read('first', 'The old conversation'))); await tick(); });
    assert.match(JSON.stringify(renderer!.toJSON()), /The second conversation/);
    assert.doesNotMatch(JSON.stringify(renderer!.toJSON()), /The old conversation/);
  } finally { await act(async () => renderer?.unmount()); globalThis.fetch = saved; }
});


test('a rejected lifecycle preview remains visible after its menu closes and never applies', async () => {
  const saved = globalThis.fetch;
  const paths: string[] = [];
  globalThis.fetch = async input => { paths.push(String(input)); return Response.json({ error: 'core unavailable' }, { status: 502 }); };
  let renderer: ReactTestRenderer | undefined;
  try {
    await act(async () => { renderer = create(createElement(GoalLifecycle, { token: undefined, goal: goal('preview'), onChanged() {} })); });
    const content = renderer!.root.findByType(Popover).props.children(() => {}) as React.ReactElement<{ children: React.ReactNode }>;
    const stop = React.Children.toArray(content.props.children).find(child => React.isValidElement<{ className: string }>(child) && child.props.className === 'pop-item') as React.ReactElement<{ onClick: () => void }>;
    await act(async () => { stop.props.onClick(); await tick(); });
    assert.deepEqual(paths, ['/api/loopx/api/actions/preview']);
    assert.equal(renderer!.root.findByProps({ role: 'alert' }).props.className, 'goal-lifecycle-error');
    assert.match(JSON.stringify(renderer!.toJSON()), new RegExp(t('loopx.coreDown')));
  } finally { await act(async () => renderer?.unmount()); globalThis.fetch = saved; }
});
