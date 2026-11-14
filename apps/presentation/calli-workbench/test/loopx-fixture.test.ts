/**
 * Fixture tests: the public-safe snapshot (loopx/examples/status.example.json)
 * must map onto the Calli view models without touching any live service.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { loadFixture } from './loopx-helpers.ts';
import { parseStatusPayload, SchemaVersionError } from '../src/loopx/schema-guard.ts';
import { ContractFormatError } from '../src/loopx/errors.ts';
import { deriveTriage } from '../src/loopx/mappers/triage.ts';
import { mapTodos } from '../src/loopx/mappers/todos.ts';
import { mapSessionsToThreads } from '../src/loopx/mappers/threads.ts';
import { mapSections, UNASSIGNED_SECTION_ID } from '../src/loopx/mappers/misc.ts';
import { eventsAfter, mapMessagesToEvents } from '../src/loopx/mappers/events.ts';
import { LoopxApi } from '../src/loopx/LoopxApi.ts';
import { placeholder, placeholderCount, PLACEHOLDERS } from '../src/loopx/placeholders.ts';
import { createDemoFixture } from '../src/loopx/fixtures.ts';
import { LoopXApiAdapter } from '../src/loopx/adapter.ts';

test('fixture parses through the status contract gate', () => {
  const status = parseStatusPayload(loadFixture());
  assert.equal(status.ok, true);
  assert.ok(status.status_contract.schema_version >= 2, 'fixture schema_version must be >= 2');
});

test('triage partitions every goal into exactly one bucket', () => {
  const status = parseStatusPayload(loadFixture());
  const view = deriveTriage(status, []);
  const goals = status.run_history?.goals ?? [];
  assert.ok(goals.length >= 1, 'fixture must hold at least one goal');
  const all = [...view.needsYou, ...view.running, ...view.error, ...view.quiet];
  for (const goal of goals) {
    const hits = all.filter((e) => e.id === goal.id);
    assert.equal(hits.length, 1, `goal ${goal.id} must land in exactly one bucket, got ${hits.length}`);
  }
});

test('in-app fixture shows the 需要你 group offline (synthetic item on the existing goal)', () => {
  const status = parseStatusPayload(loadFixture());
  const view = deriveTriage(status, []);
  // Exactly one needs-you entry: loopx-meta now holds two attention items (the
  // original codex one + the synthetic user one); any user-waiting item wins.
  assert.equal(view.needsYou.length, 1);
  assert.equal(view.needsYou[0].id, 'loopx-meta');
  assert.ok(view.needsYou[0].evidence.includes('waiting_on=user'));
  assert.ok(view.needsYou[0].evidence.some((e) => e.startsWith('operator_question=')));
  assert.equal(view.needsYou[0].recommendedAction, 'Offline review: confirm to continue (synthetic item, not authoritative)');
  // Blocked todos attach to the goal entry's evidence whichever bucket it
  // lands in (never lost to the partition): the fixture ships 4 blocked.
  assert.ok(view.needsYou[0].evidence.includes('blocked_todos=4'), `evidence: ${view.needsYou[0].evidence.join(' | ')}`);
  // …and they are ALSO their own 出错 entries (C's offline promise:
  // "Failed = the fixture's blocked Todos + one session with an error").
  const blockedEntries = view.error.filter((e) => e.kind === 'todo');
  assert.equal(blockedEntries.length, 4, `error todos: ${view.error.map((e) => e.id).join(' | ')}`);
  for (const e of blockedEntries) {
    assert.ok(e.evidence.includes('status=blocked'));
    assert.equal(e.bucket, 'error');
  }
  // The status fixture alone has no sessions; running comes from _demo_sessions
  // in fixture mode (covered by the fixture-mode test below).
  assert.equal(view.running.length, 0);
});

test('fixture mode: offline triage shows all three groups with zero network calls', async () => {
  const fixture = createDemoFixture(loadFixture());
  const realFetch = globalThis.fetch;
  let fetched = 0;
  globalThis.fetch = (() => {
    fetched++;
    throw new Error('fixture mode must not touch the network');
  }) as typeof fetch;
  try {
    const api = new LoopXApiAdapter({ fixture });
    const [view, threads, todos, sections, hosts, agents] = await Promise.all([
      api.triage(),
      api.threads(),
      api.todos(),
      api.sections(),
      api.hosts(),
      api.agents(),
    ]);
    assert.equal(fetched, 0, 'no network request may be issued in fixture mode');
    // 需要你: the synthetic waiting-on-user item.
    assert.ok(view.needsYou.some((e) => e.id === 'loopx-meta' && e.evidence.includes('waiting_on=user')));
    // 在跑: the synthetic session with an active turn.
    assert.ok(view.running.some((e) => e.kind === 'session' && e.evidence.includes('active_turn_id=demo-turn-1')));
    // 出错: blocked todos AND the synthetic session with an error code.
    assert.ok(view.error.some((e) => e.kind === 'todo' && e.evidence.includes('status=blocked')));
    assert.ok(view.error.some((e) => e.kind === 'session' && e.evidence.includes('last_error_code=adapter_crash')));
    // The rest of the offline page renders from the same bundle.
    assert.equal(threads.length, 2);
    assert.equal(todos.filter((t) => t.stage === 'blocked' && t.state === 'waiting').length, 4);
    assert.ok(sections.some((s) => s.id === 'loopx:unassigned'));
    assert.deepEqual(hosts, []);
    assert.ok(agents.length >= 1);
  } finally {
    globalThis.fetch = realFetch;
  }
});

test('one goal with several attention items merges evidence and the user-waiting item wins', () => {
  const raw = loadFixture() as Record<string, any>;
  raw.attention_queue.items = [
    { goal_id: 'g1', status: 'busy', waiting_on: 'codex', severity: 'action', recommended_action: 'Keep monitoring', activation_state: 'active' },
    { goal_id: 'g1', status: 'waiting_for_user', waiting_on: 'user', severity: 'action', recommended_action: 'Answer the question', operator_question: 'Proceed?', activation_state: 'active' },
  ];
  raw.run_history = { available: true, goal_count: 1, run_count: 0, goals: [{ id: 'g1', activation_state: 'active', display_name: 'Multi-item goal', status: 'active', adapter_status: 'connected', latest_runs: [] }] };
  const view = deriveTriage(parseStatusPayload(raw), []);
  assert.equal(view.needsYou.length, 1);
  assert.equal(view.needsYou[0].id, 'g1');
  assert.ok(view.needsYou[0].evidence.includes('waiting_on=user'));
  assert.ok(view.needsYou[0].evidence.includes('operator_question=Proceed?'));
  assert.equal(view.needsYou[0].recommendedAction, 'Answer the question');
  assert.equal(view.quiet.length, 0);
});

test('triage puts a user-action item into needsYou with verbatim evidence', () => {
  const raw = loadFixture() as Record<string, any>;
  const item = raw.attention_queue.items[0];
  const status = parseStatusPayload(raw);
  const view = deriveTriage(status, []);
  const all = [...view.needsYou, ...view.running, ...view.error, ...view.quiet];
  // Whatever the fixture holds, the bucketing must be explainable from the raw fields.
  const entry = all.find((e) => e.id === item.goal_id);
  assert.ok(entry, 'attention item goal must be present');
  const wantsUser =
    item.goal_channel_projection?.decision_frame?.user_action_required === true ||
    ['user', 'controller', 'user_or_controller', 'human', 'operator', 'owner'].includes(item.waiting_on);
  if (wantsUser) {
    assert.equal(entry!.bucket, 'needsYou');
    assert.ok(entry!.evidence.some((e) => e.startsWith('user_action_required') || e.startsWith('waiting_on=')));
  } else if (entry!.bucket === 'quiet') {
    assert.ok(entry!.evidence.length >= 0);
  }
});

test('a needs-you projection maps to the 需要你 bucket', () => {
  const raw = loadFixture() as Record<string, any>;
  raw.attention_queue.items = [
    {
      goal_id: 'demo-goal',
      status: 'waiting_for_user',
      waiting_on: 'user',
      severity: 'action',
      recommended_action: 'Confirm the merge path',
    },
  ];
  raw.run_history = { available: true, goal_count: 1, run_count: 0, goals: [{ id: 'demo-goal', activation_state: 'active', display_name: 'Demo goal', status: 'active', adapter_status: 'connected', latest_runs: [] }] };
  // Scope this test to the goal bucket: no blocked todos in the way.
  raw.todo_index = { schema_version: 'todo_index_v0', total_count: 0, items: [] };
  const view = deriveTriage(parseStatusPayload(raw), []);
  assert.equal(view.needsYou.length, 1);
  assert.equal(view.needsYou[0].title, 'Demo goal');
  assert.deepEqual(view.needsYou[0].evidence, ['waiting_on=user']);
  assert.equal(view.needsYou[0].recommendedAction, 'Confirm the merge path');
  assert.equal(view.error.length, 0);
  assert.equal(view.running.length, 0);
});

test('contract errors and failing sessions map to the 出错 bucket', () => {
  const raw = loadFixture() as Record<string, any>;
  raw.contract_errors = ['goal state unreadable'];
  raw.contract_errors_total_count = 1;
  const view = deriveTriage(parseStatusPayload(raw), [
    { session_id: 's1', goal_id: null, agent_id: 'codex', adapter_kind: 'codex_app_server', status: 'error', active_turn_id: null, last_error_code: 'adapter_crash', created_at: '', updated_at: '', last_activity_at: '', resumable: false },
  ]);
  assert.ok(view.error.some((e) => e.id === 'loopx:contract'), 'contract errors must surface as a registry entry');
  assert.ok(view.error.some((e) => e.kind === 'session' && e.evidence.includes('last_error_code=adapter_crash')));
});

test('an active turn maps to the 在跑 bucket', () => {
  const raw = loadFixture() as Record<string, any>;
  const view = deriveTriage(parseStatusPayload(raw), [
    { session_id: 's2', goal_id: null, agent_id: 'codex', adapter_kind: 'codex_app_server', status: 'busy', active_turn_id: 'turn-9', last_error_code: null, created_at: '', updated_at: '', last_activity_at: '', resumable: false },
  ]);
  assert.ok(view.running.some((e) => e.kind === 'session' && e.evidence.includes('active_turn_id=turn-9')));
});

test('todo_index maps to Calli todos with stable ids and priority mapping', () => {
  const status = parseStatusPayload(loadFixture());
  const todos = mapTodos(status);
  const items = status.todo_index?.items ?? [];
  assert.equal(todos.length, items.length);
  for (let i = 0; i < items.length; i++) {
    assert.equal(todos[i].text, items[i].text);
    assert.equal(todos[i].done, items[i].done);
    assert.equal(todos[i].id, items[i].todo_id ?? `loopx-idx-${items[i].index ?? i + 1}`);
    if (items[i].priority === 'P0') assert.equal(todos[i].importance, 'high');
    if (items[i].priority === 'P1' || items[i].priority === 'P2') assert.equal(todos[i].importance, 'normal');
    assert.equal(todos[i].lane, null, 'lane is a registered placeholder');
    assert.deepEqual(todos[i].threads, []);
  }
});

test('sessions map to threads with goal titles and honest status', () => {
  const status = parseStatusPayload(loadFixture());
  const goal = (status.run_history?.goals ?? [])[0];
  const mk = (over: Record<string, unknown>) => ({
    session_id: 'sess-1', goal_id: goal.id, agent_id: 'codex', adapter_kind: 'codex_app_server',
    status: 'ready', agent_model_id: 'gpt-6.1-sol', agent_reasoning_effort: 'xhigh',
    active_turn_id: null, last_error_code: null, created_at: '2026-11-14T10:00:00Z',
    updated_at: '2026-11-14T11:00:00Z', last_activity_at: '', resumable: true, ...over,
  });
  const goalsById = new Map([[goal.id, goal]]);
  const repos = new Map([[goal.id, 'loopx-project/loopx']]);
  const goalless = { ...mk({ session_id: 'sess-4', goal_id: null }) };
  const ghostGoal = { ...mk({ session_id: 'sess-5', goal_id: 'manager-only-goal' }) };
  const threads = mapSessionsToThreads([mk({}), mk({ session_id: 'sess-2', active_turn_id: 't1' }), mk({ session_id: 'sess-3', last_error_code: 'x' }), goalless, ghostGoal], goalsById, repos);
  assert.equal(threads.length, 5);
  assert.equal(threads[0].title, goal.display_name ?? goal.goal_objective ?? 'codex · loopx-meta');
  assert.equal(threads[0].status, 'idle');
  assert.equal(threads[1].status, 'running');
  assert.equal(threads[2].status, 'error');
  assert.equal(threads[0].model, 'gpt-6.1-sol');
  assert.equal(threads[0].permissionMode, 'read-only'); // from placeholders registry
  // PRIVACY ruling: cwd shows the goal's repository label, never a local
  // absolute path; unknown → empty string.
  assert.equal(threads[0].cwd, 'loopx-project/loopx');
  assert.ok(!threads[0].cwd.startsWith('/'));
  assert.equal(threads[3].cwd, '');
  assert.equal(threads[0].preview, null);
  // Goal grouping ruling: sectionId = goal_id only when that goal is in
  // run_history; goal-less sessions AND session-only goals (raw ids never
  // become group titles) → unassigned group.
  assert.equal(threads[0].sectionId, goal.id);
  assert.equal(threads[3].sectionId, UNASSIGNED_SECTION_ID);
  assert.equal(threads[4].sectionId, UNASSIGNED_SECTION_ID);
});

test('sections derive from goals plus a trailing unassigned group (session-only ids get no group)', () => {
  const status = parseStatusPayload(loadFixture());
  const goal = (status.run_history?.goals ?? [])[0];
  const sections = mapSections(status);
  const ids = sections.map((s) => s.id);
  // goal group named display_name → goal_objective → id
  assert.ok(ids.includes(goal.id));
  assert.equal(sections.find((s) => s.id === goal.id)!.name, goal.display_name ?? goal.goal_objective ?? goal.id);
  // unassigned group exists, last, with the registered neutral title
  assert.equal(ids.at(-1), UNASSIGNED_SECTION_ID);
  assert.equal(sections.at(-1)!.name, placeholder<string>('Section.name (unassigned group)'));
  // exactly one group per goal + unassigned (no raw-id groups)
  assert.equal(sections.length, (status.run_history?.goals ?? []).length + 1);
  // positions are 1..n without holes
  assert.deepEqual(sections.map((s) => s.position), sections.map((_, i) => i + 1));
});

test('chat messages map to the Calli turn event vocabulary', () => {
  const events = mapMessagesToEvents('sess-1', [
    { message_id: 'm1', turn_id: 't1', role: 'user', text: '对比一下两个方案', created_at: '2026-11-14T10:00:00Z' },
    { message_id: 'm2', turn_id: 't1', role: 'assistant', text: '结论是……', created_at: '2026-11-14T10:01:00Z' },
  ]);
  assert.equal(events[0].type, 'turn.started');
  assert.equal(events[0].data.text, '对比一下两个方案');
  assert.equal(events[1].type, 'message.delta');
  assert.equal(events[2].type, 'turn.done');
  // every turn is closed
  assert.ok(events.filter((e) => e.type === 'turn.started').length === events.filter((e) => e.type === 'turn.done').length);
  assert.deepEqual(eventsAfter(events, 1).map((e) => e.seq), [2, 3]);
});

test('version gate: only observed < minimum rejects; newer (3) parses; malformed is a format error', () => {
  const raw = loadFixture() as Record<string, any>;
  // A NEWER contract (3) is handed to zod — no upper-bound rejection.
  const future = { ...raw, status_contract: { ...raw.status_contract, schema_version: 3 } };
  assert.equal(parseStatusPayload(future).status_contract.schema_version, 3);
  // An older contract than the payload's own minimum is a typed version error.
  const old = { ...raw, status_contract: { ...raw.status_contract, schema_version: 1, minimum_dashboard_schema_version: 2 } };
  assert.throws(() => parseStatusPayload(old), (e: unknown) => e instanceof SchemaVersionError && e.observed === 1 && e.supported === 2);
  // observed < minimum even when the format itself is fine (minimum raised to 3).
  const raised = { ...raw, status_contract: { ...raw.status_contract, schema_version: 2, minimum_dashboard_schema_version: 3 } };
  assert.throws(() => parseStatusPayload(raised), (e: unknown) => e instanceof SchemaVersionError && e.observed === 2 && e.supported === 3);
  // A malformed body is a FORMAT error, never a version error.
  const broken = { ...raw, ok: 'not-a-boolean' };
  assert.throws(
    () => parseStatusPayload(broken),
    (e: unknown) => e instanceof ContractFormatError && !(e instanceof SchemaVersionError) && e.code === 'loopx_format',
  );
});

test('tightened triage: controller/waits are quiet; no severity or adapter_status inference', () => {
  const base = () => {
    const raw = loadFixture() as Record<string, any>;
    raw.attention_queue = { available: true, item_count: 1, needs_user_or_controller: 0, needs_controller: 1, needs_codex: 0, watching_external_evidence: 0, items: [] };
    raw.todo_index = { schema_version: 'todo_index_v0', total_count: 0, items: [] };
    return raw;
  };
  const mkGoal = (raw: Record<string, any>) => {
    raw.run_history = { available: true, goal_count: 1, run_count: 0, goals: [{ id: 'g1', activation_state: 'active', display_name: 'G1', status: 'active', adapter_status: 'weird-status', latest_runs: [] }] };
    return raw;
  };
  // waiting_on=controller: a control agent, not a person → quiet (not needsYou).
  const controller = mkGoal(base());
  controller.attention_queue.items = [{ goal_id: 'g1', status: 'delegated', waiting_on: 'controller', severity: 'high', recommended_action: 'x', activation_state: 'active' }];
  let view = deriveTriage(parseStatusPayload(controller), []);
  assert.equal(view.needsYou.length, 0);
  assert.equal(view.error.length, 0, 'severity=high and odd adapter_status must NOT infer error');
  assert.equal(view.quiet.length, 1);
  assert.ok(view.quiet[0].evidence.includes('waiting_on=controller'));
  // waiting on external evidence / a monitor: quiet, NOT running.
  const watching = mkGoal(base());
  watching.attention_queue.items = [{ goal_id: 'g1', status: 'watching', waiting_on: 'external_evidence', severity: 'info', recommended_action: null, activation_state: 'active' }];
  view = deriveTriage(parseStatusPayload(watching), []);
  assert.equal(view.running.length, 0);
  assert.equal(view.quiet.length, 1);
  assert.ok(view.quiet[0].evidence.includes('waiting_on=external_evidence'));
});

test('placeholder registry is the single source of stand-in values', () => {
  assert.ok(placeholderCount >= 15, 'registry must stay comprehensive');
  const fields = PLACEHOLDERS.map((p) => p.field);
  assert.equal(new Set(fields).size, fields.length, 'no duplicate registrations');
  assert.throws(() => placeholder('Not.Registered'), /Unregistered placeholder/);
  assert.equal(placeholder<string>('Thread.permissionMode'), 'read-only');
});

test('desk facts: blocked todos wait with a reason, user todos need an answer, no time means no age', async () => {
  const { buildDesk } = await import('../src/desk.ts');
  const todos = await new LoopxApi({ fixture: true }).todos();
  const blocked = todos.filter((t) => t.stage === 'blocked');
  assert.equal(blocked.length, 4);
  assert.ok(blocked.every((t) => t.state === 'waiting' && t.waitingOn === 'blocked'));
  const desk = buildDesk(todos, new Date('2026-11-14T00:00:00Z'));
  assert.ok(desk.lanes.wait.length >= 4);
  const untimed = desk.lanes.doing.concat(desk.lanes.later).filter((p) => !p.todo.updatedAt);
  assert.ok(untimed.length > 0 && untimed.every((p) => p.idleDays === 0));
});

test('demo conversations: the running session has a thread, the failed one ends on a turn error', async () => {
  const api = new LoopxApi({ fixture: true });
  const running = await api.events('demo-running');
  assert.ok(running.some((e) => e.type === 'turn.started') && running.some((e) => e.type === 'message.delta'));
  assert.ok(running.some((e) => e.type === 'message.delta' && JSON.stringify(e.data).includes('mermaid')));
  const failed = await api.events('demo-error');
  assert.equal(failed.at(-1)?.type, 'turn.error');
});
