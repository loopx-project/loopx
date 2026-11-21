import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setTimeout as delay } from 'node:timers/promises';
import { VoiceSessions, type VoiceAccess, type VoiceBinding, type VoiceEvent, type VoiceSignal, type VoiceTransport } from '../src/voice-sessions.js';

const binding: VoiceBinding = { extensionId: 'voice', hostId: 'local', providerId: 'provider', threadId: 'thread', ownerId: 'browser' };
const status = (code: number) => (error: any) => error.status === code;
const tick = () => delay(0);

function harness(options: { leaseMs?: number; startupMs?: number; stopMs?: number; maxSessions?: number; pendingStart?: boolean; pendingStop?: boolean } = {}) {
  let allowed = true;
  const transports: Array<VoiceTransport & { emit: (event: VoiceSignal) => void; resolveStart: () => void; stops: number; disposals: number; starts: number }> = [];
  const manager = new VoiceSessions({
    leaseMs: 2000, startupMs: 2000, stopMs: 100, ...options,
    authorize: () => { if (!allowed) throw new Error('PRIVATE account or extension detail'); },
    create: () => {
      const t = {
        emit: (_event: VoiceSignal) => {}, resolveStart: () => {}, stops: 0, disposals: 0, starts: 0,
        async start(_offer: string, emit: (event: VoiceSignal) => void) {
          t.starts++; t.emit = emit;
          if (options.pendingStart) await new Promise<void>(resolve => { t.resolveStart = resolve; });
        },
        async stop() { t.stops++; if (options.pendingStop) await new Promise<void>(() => {}); },
        dispose() { t.disposals++; },
      };
      transports.push(t);
      return t;
    },
  });
  const open = (overrides: Partial<VoiceBinding> = {}) => {
    const bound = { ...binding, ...overrides };
    const snapshot = manager.open(bound, 'private-sdp-offer');
    const access: VoiceAccess = { id: snapshot.id, ownerId: bound.ownerId, extensionId: bound.extensionId };
    const events: VoiceEvent[] = [];
    manager.subscribe(access, event => events.push(event));
    return { access, events, snapshot };
  };
  return { manager, transports, open, revoke: () => { allowed = false; } };
}

test('voice reserves a target before starting; the same owner must end before switching threads', async t => {
  const h = harness(); t.after(() => h.manager.dispose());
  const first = h.open();
  assert.throws(() => h.open({ ownerId: 'another-browser' }), status(409));
  assert.throws(() => h.open({ threadId: 'another-thread' }), status(409));
  assert.equal(h.transports.length, 1);
  await tick();
  assert.equal(h.manager.get(first.access).startAccepted, true);
  assert.equal(h.manager.get(first.access).state, 'connecting');
  assert.equal(h.manager.get(first.access).mediaConnected, false);
  assert.ok(!JSON.stringify(first.events).includes('private-sdp-offer'));
});

test('owner and extension scope apply to reads, subscriptions, heartbeats and stop', async t => {
  const h = harness(); t.after(() => h.manager.dispose());
  const { access } = h.open();
  for (const wrong of [{ ...access, ownerId: 'other' }, { ...access, extensionId: 'other' }]) {
    assert.throws(() => h.manager.get(wrong), status(404));
    assert.throws(() => h.manager.heartbeat(wrong), status(404));
    assert.throws(() => h.manager.subscribe(wrong, () => {}), status(404));
    assert.throws(() => h.manager.mediaConnected(wrong), status(404));
    await assert.rejects(h.manager.stop(wrong), status(404));
  }
  assert.equal(h.transports[0].stops, 0);
});

test('early stop, hung startup and late events cannot revive a session or affect its replacement', async t => {
  const h = harness({ pendingStart: true }); t.after(() => h.manager.dispose());
  const first = h.open();
  await tick();
  const old = h.transports[0];
  await Promise.all([h.manager.stop(first.access), h.manager.stop(first.access)]);
  assert.equal(old.stops, 1);
  assert.equal(old.disposals, 1);
  const replacement = h.open();
  const before = first.events.length;
  old.emit({ type: 'sdp', sdp: 'late private answer' });
  old.emit({ type: 'error' });
  old.resolveStart();
  await tick();
  assert.equal(first.events.length, before);
  assert.equal(h.manager.get(replacement.access).state, 'connecting');
  assert.equal(h.manager.get(replacement.access).startAccepted, false);
  await h.manager.stop(first.access); // Already closed, idempotent.
  assert.equal(h.transports[1].stops, 0);
});

test('stop before the launch microtask does not start the upstream connection', async () => {
  const h = harness();
  const { access } = h.open();
  await h.manager.stop(access);
  assert.equal(h.transports[0].starts, 0);
  assert.equal(h.transports[0].disposals, 1);
});

test('only negotiated browser media marks connected; late subscribers receive SDP but no transcript replay', async t => {
  const h = harness(); t.after(() => h.manager.dispose());
  const { access } = h.open();
  await tick();
  assert.throws(() => h.manager.mediaConnected(access), status(409));
  h.transports[0].emit({ type: 'started' });
  assert.equal(h.manager.get(access).state, 'connecting');
  h.transports[0].emit({ type: 'sdp', sdp: 'private-answer' });
  h.transports[0].emit({ type: 'transcript', itemId: 'item-1', text: 'spoken text', final: true });
  const later: VoiceEvent[] = [];
  h.manager.subscribe(access, event => later.push(event));
  assert.deepEqual(later.map(e => e.type), ['state', 'sdp']);
  assert.equal(h.manager.mediaConnected(access).state, 'connected');
  assert.equal(h.manager.get(access).upstreamStarted, true);
  assert.ok(!JSON.stringify(h.manager.get(access)).includes('private-answer'));
});

test('early media requires both native acknowledgement and started before marking connected', async t => {
  const h = harness({ pendingStart: true }); t.after(() => h.manager.dispose());
  const { access } = h.open(); await tick();
  h.transports[0].emit({ type: 'sdp', sdp: 'answer' });
  assert.equal(h.manager.mediaConnected(access).state, 'connecting');
  h.transports[0].resolveStart(); await tick();
  assert.equal(h.manager.get(access).startAccepted, true);
  assert.equal(h.manager.get(access).state, 'connecting');
  h.transports[0].emit({ type: 'started' });
  assert.equal(h.manager.get(access).state, 'connected');
});

test('early browser media cannot disable the deadline while native acceptance is pending', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const h = harness({ pendingStart: true, startupMs: 25 });
  const { access, events } = h.open();
  await Promise.resolve();
  h.transports[0].emit({ type: 'sdp', sdp: 'answer' });
  h.transports[0].emit({ type: 'started' });
  assert.equal(h.manager.mediaConnected(access).state, 'connecting');
  t.mock.timers.tick(25);
  await h.manager.dispose();
  assert.equal((events.at(-1) as Extract<VoiceEvent, { type: 'state' }>).session.reason, 'startup_timeout');
  assert.equal(h.transports[0].disposals, 1);
});

test('dependency revocation cuts off content delivery and tears down even without a heartbeat', async () => {
  const h = harness();
  const { access, events } = h.open(); await tick();
  h.revoke();
  h.transports[0].emit({ type: 'transcript', itemId: 'item', text: 'must not leak', final: true });
  await h.manager.revalidate(); await tick();
  assert.ok(!JSON.stringify(events).includes('must not leak'));
  assert.ok(!JSON.stringify(events).includes('PRIVATE'));
  assert.equal(h.transports[0].disposals, 1);
  assert.throws(() => h.manager.get(access), status(404));
  assert.deepEqual(events.at(-1), { type: 'state', session: { ...events[0].session, startAccepted: true, state: 'failed', reason: 'revoked' } });
});

test('revoked heartbeat fails closed, while the owner can still stop', async () => {
  const h = harness(); const { access } = h.open(); await tick();
  h.revoke();
  assert.throws(() => h.manager.heartbeat(access), status(403));
  await h.manager.stop(access);
  assert.equal(h.transports[0].stops, 1);
  assert.equal(h.transports[0].disposals, 1);
});

test('lost browser lease expires and forcibly disposes a transport whose stop hangs', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const h = harness({ leaseMs: 20, stopMs: 15, pendingStop: true });
  const { access, events } = h.open();
  await Promise.resolve();
  t.mock.timers.tick(20);
  await Promise.resolve();
  t.mock.timers.tick(15);
  await h.manager.dispose();
  assert.equal(h.transports[0].disposals, 1);
  assert.throws(() => h.manager.get(access), status(404));
  assert.equal(events.at(-1)?.type, 'state');
  assert.equal((events.at(-1) as any).session.reason, 'expired');
});

test('heartbeats cannot prolong an upstream startup that never negotiates media', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const h = harness({ startupMs: 25, leaseMs: 100, pendingStart: true });
  const { access, events } = h.open();
  await Promise.resolve();
  t.mock.timers.tick(10); h.manager.heartbeat(access);
  t.mock.timers.tick(15);
  await h.manager.dispose();
  assert.equal(h.transports[0].disposals, 1);
  assert.equal((events.at(-1) as any).session.reason, 'startup_timeout');
});

test('shutdown rejects new sessions, clears every lease and tolerates broken event consumers', async () => {
  const h = harness();
  const first = h.open();
  const second = h.open({ threadId: 'second', ownerId: 'second' });
  h.manager.subscribe(first.access, () => { throw new Error('broken stream'); });
  await tick();
  await Promise.all([h.manager.dispose(), h.manager.dispose()]);
  assert.deepEqual(h.transports.map(t => t.disposals), [1, 1]);
  assert.equal((second.events.at(-1) as any).session.reason, 'shutdown');
  assert.throws(() => h.open(), status(503));
});

test('native error before start acknowledgement survives the eventual acknowledgement', async () => {
  const h = harness({ pendingStart: true });
  const { access, events } = h.open(); await tick();
  h.transports[0].emit({ type: 'error' });
  h.transports[0].resolveStart(); await tick();
  assert.throws(() => h.manager.get(access), status(404));
  assert.equal((events.at(-1) as any).session.reason, 'provider_error');
  assert.equal((events.at(-1) as any).session.startAccepted, false);
});

test('invalid offers and over-capacity starts create no transport; event payloads are bounded', async () => {
  const h = harness({ maxSessions: 1 });
  assert.throws(() => h.manager.open(binding, 'x'.repeat(65537)), status(400));
  assert.throws(() => h.manager.open({ ...binding, threadId: '' }, 'offer'), status(400));
  assert.equal(h.transports.length, 0);
  const { access } = h.open();
  assert.throws(() => h.open({ threadId: 'another', ownerId: 'another' }), status(429));
  await tick();
  h.transports[0].emit({ type: 'transcript', itemId: 'item', text: 'x'.repeat(32769), final: true });
  await tick();
  assert.equal(h.transports[0].disposals, 1);
  assert.throws(() => h.manager.get(access), status(404));
});
