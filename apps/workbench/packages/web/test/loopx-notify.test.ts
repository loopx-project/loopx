import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { newlyWaiting } from '../src/loopx/notify.js';
import { parseLoopxStatus, type LoopxAttention } from '../src/loopx/status.js';

const waiting = (goalId: string): LoopxAttention => ({ goalId, status: null, waitingOn: 'user', severity: null, recommendedAction: null, userTodos: { open: 1, done: 0, total: 1 }, agentTodos: { open: 0, done: 0, total: 0 } });

describe('newlyWaiting', () => {
  it('treats the first read as the baseline, not as news', () => {
    const { now, fresh } = newlyWaiting(null, [waiting('a')]);
    assert.deepEqual(fresh, []);
    assert.deepEqual([...now], ['a']);
  });
  it('reports only the Goals that were not waiting before', () => {
    assert.deepEqual(newlyWaiting(new Set(['a']), [waiting('a'), waiting('b')]).fresh, ['b']);
    assert.deepEqual(newlyWaiting(new Set(['a']), []).fresh, []);
  });
});

describe('notification projection', () => {
  const base = { run_history: { goals: [] } };
  it('reads the per-Goal bindings and ignores a status without them', () => {
    assert.deepEqual(parseLoopxStatus(base).notifications, []);
    const parsed = parseLoopxStatus({ ...base, goal_channel_notification_projection: { goals: [
      { goal_id: 'g1', configured: true, enabled: true, human_gate_auto_notify_enabled: true, target_ref: 'team', receipt_count: 3, last_notified_at: '2026-11-16T01:00:00Z' },
      { configured: true },
    ] } });
    assert.deepEqual(parsed.notifications, [{ goalId: 'g1', configured: true, enabled: true, humanGateAutoNotify: true, blockedNoticeAutoNotify: false, targetRef: 'team', receiptCount: 3, lastNotifiedAt: '2026-11-16T01:00:00Z' }]);
  });
});
