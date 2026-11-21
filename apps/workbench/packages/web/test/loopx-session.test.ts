import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { SessionLoopx } from '../src/api';
import { drivenByApp, wakeOwner } from '../src/loopx/session';

const info = (patch: Partial<SessionLoopx>): SessionLoopx => ({ goalId: 'g', goalTitle: 'G', basis: 'core', source: 'codex-app', ...patch });

test('the core names the host that wakes a conversation; text alone falls back to who started it', () => {
  assert.equal(wakeOwner(info({ source: 'codex-app' }), null), 'app');
  assert.equal(wakeOwner(info({ source: 'codex-cli-tui' }), 'app'), 'cli');
  assert.equal(wakeOwner(info({ basis: 'content', source: 'bootstrap' }), 'app'), 'app');
  assert.equal(wakeOwner(info({ basis: 'content', source: 'bootstrap' }), null), 'unknown');
  assert.equal(wakeOwner(info({ basis: 'workbench', source: 'launch' }), 'app'), 'workbench');
});

test('only a conversation the desktop app still wakes warns about sending from here too', () => {
  assert.equal(drivenByApp(info({}), null), true);
  assert.equal(drivenByApp(info({ source: 'codex-cli-tui' }), 'cli'), false);
  assert.equal(drivenByApp(info({ missing: true }), 'app'), false);
  assert.equal(drivenByApp(info({ basis: 'workbench' }), 'app'), false);
  assert.equal(drivenByApp(null, 'app'), false);
});
