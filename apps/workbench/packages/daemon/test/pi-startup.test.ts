import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { PiRpcAdapter } from '../src/adapters/pi.js';
import type { EmittedEvent } from '../src/events.js';
import type { TurnHooks } from '../src/adapters/types.js';

const fixture = fileURLToPath(new URL('./fixtures/fake-pi.mjs', import.meta.url));
const image = { data: 'aW1hZ2U=', mimeType: 'image/png', path: '/tmp/fixture.png' };
const hooks = (events: EmittedEvent[]): TurnHooks => ({
  emit: event => events.push(event), requestApproval: async () => ({ cancelled: true }),
});

test('images use the live model after switching, without probing another process', async () => {
  const adapter = new PiRpcAdapter([process.execPath, fixture], { FAKE_PI_FORBID_PROBE: '1' });
  adapter.models = async () => { throw new Error('prompt must not consult the model catalog'); };
  const session = await adapter.start({ cwd: process.cwd() });
  try {
    for (const model of ['fake/think', 'fake/plain', 'fake/think']) {
      await session.setModel!(model);
      const events: EmittedEvent[] = [];
      const result = await session.prompt({ text: 'inspect-work-mode', images: [image] }, hooks(events));
      assert.equal(result.stopReason, 'end_turn');
      const output = JSON.parse(events.filter(e => e.type === 'message.delta').map(e => e.data.text).join(''));
      assert.equal(output.images.length, model === 'fake/plain' ? 0 : 1);
      if (model === 'fake/plain') assert.match(output.message, /fixture.png/);
      assert.deepEqual(events.filter(e => e.type === 'turn.phase').map(e => e.data.phase), ['image_check', 'image_ready', 'prompt_sent']);
    }
  } finally { await session.close(); }
});

test('cancelling during image preparation never submits the prompt', async () => {
  const session = await new PiRpcAdapter([process.execPath, fixture], { FAKE_PI_IMAGE_CHECK_DELAY_MS: '2000' }).start({ cwd: process.cwd() });
  try {
    const events: EmittedEvent[] = [];
    const result = session.prompt({ text: 'inspect-work-mode', images: [image] }, hooks(events));
    await session.cancel();
    let timeout: ReturnType<typeof setTimeout>;
    const deadline = new Promise<never>((_, reject) => { timeout = setTimeout(() => reject(new Error('cancellation waited for image preparation')), 1000); });
    try { assert.equal((await Promise.race([result, deadline])).stopReason, 'cancelled'); }
    finally { clearTimeout(timeout!); }
    assert.ok(!events.some(e => e.type === 'message.delta' || e.data.phase === 'prompt_sent'));
    assert.equal((await session.prompt({ text: 'inspect-work-mode', images: [] }, hooks([]))).stopReason, 'end_turn');
  } finally { await session.close(); }
});

test('model request milestone is emitted before the first output and cancellation ends the wait', async () => {
  const session = await new PiRpcAdapter([process.execPath, fixture]).start({ cwd: process.cwd() });
  try {
    const events: EmittedEvent[] = [];
    let requested!: () => void;
    const ready = new Promise<void>(resolve => { requested = resolve; });
    const callbacks = hooks(events);
    const result = session.prompt({ text: 'wait-first', images: [] }, { ...callbacks, emit: event => {
      callbacks.emit(event);
      if (event.data.phase === 'model_request') requested();
    } });
    await ready;
    assert.deepEqual(events.map(e => e.data.phase), ['prompt_sent', 'model_request']);
    await session.cancel();
    assert.equal((await result).stopReason, 'cancelled');
  } finally { await session.close(); }
});
