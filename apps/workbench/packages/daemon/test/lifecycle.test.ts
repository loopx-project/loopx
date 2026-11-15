import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { AttachmentStore } from '../src/attachments.js';
import type { StoredEvent } from '../src/events.js';
import { attentionOf, turnOrigin, turnOutcome } from '../src/lifecycle.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';

const ev = (type: StoredEvent['type'], data: Record<string, unknown> = {}): StoredEvent => ({ threadId: 't', seq: 1, turnId: 'u', type, ts: '2026-01-01T00:00:00.000Z', data });

test('turnOutcome reads every way a turn ends', () => {
  // Finished normally, whatever the agent called its stop.
  assert.deepEqual(turnOutcome(ev('turn.done', { stopReason: 'end_turn' })), { outcome: 'ok', code: null, message: null, imported: false, fromTerminal: false });
  assert.equal(turnOutcome(ev('turn.done', { stopReason: 'max_tokens' }))?.outcome, 'ok');
  // Stopped by the user, including before the agent had started.
  assert.equal(turnOutcome(ev('turn.done', { stopReason: 'cancelled' }))?.outcome, 'cancelled');
  // Failed: only turn.error, with what it said and maybe a code.
  assert.deepEqual(turnOutcome(ev('turn.error', { message: 'quota', code: 'plan_exhausted' })), {
    outcome: 'error', code: 'plan_exhausted', message: 'quota', imported: false, fromTerminal: false,
  });
  assert.equal(turnOutcome(ev('turn.error', {}))?.message, 'agent error');
  // Cut off by a restart.
  assert.equal(turnOutcome(ev('turn.error', { message: 'restarted', code: 'interrupted' }))?.outcome, 'interrupted');
  // History: imported, and synced from the terminal.
  assert.equal(turnOutcome(ev('turn.done', { stopReason: 'end_turn', imported: true }))?.imported, true);
  assert.equal(turnOutcome(ev('turn.done', { stopReason: 'end_turn', fromTerminal: true }))?.fromTerminal, true);
  // Anything else ends nothing.
  for (const type of ['turn.started', 'message.delta', 'approval.request', 'input.resolved', 'thread.updated'] as const) assert.equal(turnOutcome(ev(type)), null);
});

test('turnOrigin keeps who started a turn apart from what kind of turn it is', () => {
  assert.deepEqual(turnOrigin(ev('turn.started', { text: 'hi' })), { initiator: 'user', imported: false, fromTerminal: false, control: false });
  assert.equal(turnOrigin(ev('turn.started', { coordination: { messageId: 'm' } })).initiator, 'coordination');
  assert.equal(turnOrigin(ev('turn.started', { handover: 'codex' })).initiator, 'handover');
  assert.equal(turnOrigin(ev('turn.started', { scheduled: true })).initiator, 'schedule');
  assert.deepEqual(turnOrigin(ev('turn.started', { imported: true })), { initiator: 'unknown', imported: true, fromTerminal: false, control: false });
  assert.deepEqual(turnOrigin(ev('turn.started', { fromTerminal: true })), { initiator: 'unknown', imported: false, fromTerminal: true, control: false });
  assert.equal(turnOrigin(ev('turn.started', { text: '/goal pause', control: true })).control, true);
});

test('attentionOf puts waits first, then unread results, failures named as such', () => {
  const base = { waiting: false, waitingReason: null, unread: false, status: 'idle' } as const;
  assert.deepEqual(attentionOf({ ...base, waiting: true, waitingReason: 'approval', unread: true }), { kind: 'approval' });
  assert.deepEqual(attentionOf({ ...base, waiting: true, waitingReason: 'question' }), { kind: 'question' });
  assert.deepEqual(attentionOf({ ...base, waiting: true, waitingReason: 'plan', status: 'running' }), { kind: 'plan' });
  // Older daemons did not say which; approvals were all there was.
  assert.deepEqual(attentionOf({ ...base, waiting: true }), { kind: 'approval' });
  assert.deepEqual(attentionOf({ ...base, unread: true, status: 'error' }), { kind: 'error' });
  assert.deepEqual(attentionOf({ ...base, unread: true }), { kind: 'unread' });
  // A failure that has been looked at, and a running turn, want nothing from a person.
  assert.equal(attentionOf({ ...base, status: 'error' }), null);
  assert.equal(attentionOf({ ...base, status: 'running' }), null);
});

test('a turn a restart cut off reads as interrupted', () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-lifecycle-'));
  const store = new Store(dir);
  try {
    store.createThread({ id: 't1', agent: 'pi', cwd: dir, title: 'x', permissionMode: 'auto_edit', model: null, effort: null });
    store.appendEvent('t1', 'turn-1', 'turn.started', { text: 'work' });
    store.updateThread('t1', { status: 'running' });
    new ThreadManager(store, new Map(), [dir], new AttachmentStore(dir)).recoverInterrupted();
    const end = store.listEventsOfType('t1', 'turn.error').at(-1)!;
    assert.equal(turnOutcome(end)?.outcome, 'interrupted');
    assert.equal(turnOutcome(end)?.code, 'interrupted');
  } finally {
    store.close();
    rmSync(dir, { recursive: true, force: true });
  }
});
