import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { PiRpcAdapter } from '../src/adapters/pi.js';
import type { AgentSession, TurnInput } from '../src/adapters/types.js';

const fixture = fileURLToPath(new URL('./fixtures/fake-pi.mjs', import.meta.url));
const adapter = () => new PiRpcAdapter([process.execPath, fixture]);
async function inspect(session: AgentSession, text = 'inspect-work-mode', images: TurnInput['images'] = []) {
  let output = '';
  const result = await session.prompt({ text, images }, {
    // In plan mode the reply comes back as the plan.
    emit: event => { if (event.type === 'message.delta' || event.type === 'plan.completed') output += event.data.text; },
    approve: async () => ({ outcome: 'cancelled' }),
  });
  assert.equal(result.stopReason, 'end_turn');
  return JSON.parse(output);
}

test('pi planning uses a per-turn instruction, preserves images, and switches in the same session', async () => {
  const s = await adapter().start({ cwd: process.cwd(), workMode: 'plan' });
  const id = s.agentSessionId;
  try {
    const image = { data: 'aW1hZ2U=', mimeType: 'image/png', path: '/tmp/fixture.png' };
    const first = await inspect(s, 'inspect-work-mode 请设计登录页', [image]);
    assert.match(first.message, /^\[Hub work mode: plan\]/);
    assert.match(first.message, /不修改文件/);
    assert.ok(first.message.endsWith('inspect-work-mode 请设计登录页'));
    assert.deepEqual(first.images, [{ type: 'image', data: image.data, mimeType: image.mimeType }]);
    assert.match((await inspect(s)).message, /^\[Hub work mode: plan\]/);
    await s.setWorkMode!('execute');
    assert.match((await inspect(s)).message, /^\[Hub work mode: execute\]/);
    assert.equal(s.agentSessionId, id);
    assert.equal((await inspect(s)).message, 'inspect-work-mode');
  } finally { await s.close(); }
});

test('pi resumed execution clears prior planning and leaves slash commands intact', async () => {
  const s = await adapter().start({ cwd: process.cwd(), resumeId: 'saved-pi', workMode: 'execute' });
  try {
    assert.equal((await inspect(s, '/inspect-work-mode')).message, '/inspect-work-mode');
    assert.match((await inspect(s)).message, /^\[Hub work mode: execute\]/);
    await s.setWorkMode!('plan');
    assert.equal((await inspect(s, '/inspect-work-mode')).message, '/inspect-work-mode');
    assert.match((await inspect(s)).message, /^\[Hub work mode: plan\]/);
  } finally { await s.close(); }
  const plain = await adapter().start({ cwd: process.cwd() });
  try { assert.equal((await inspect(plain)).message, 'inspect-work-mode'); }
  finally { await plain.close(); }
});

test('pi can cancel immediately after applying the work mode', { timeout: 10000 }, async () => {
  const s = await adapter().start({ cwd: process.cwd() });
  try {
    await s.setWorkMode!('execute');
    const running = s.prompt({ text: 'slow', images: [] }, { emit: () => {}, approve: async () => ({ outcome: 'cancelled' }) });
    await s.cancel();
    assert.equal((await running).stopReason, 'cancelled');
  } finally { await s.close(); }
});
