import { test } from 'node:test';
import assert from 'node:assert/strict';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { ClaudeAcpAdapter } from '../src/adapters/claude.js';
import { PiRpcAdapter } from '../src/adapters/pi.js';
import { ZcodeAdapter } from '../src/adapters/zcode.js';
import type { EmittedEvent } from '../src/events.js';
const here = dirname(fileURLToPath(import.meta.url));
for (const [name, Adapter, fixture] of [['Claude', ClaudeAcpAdapter, 'fake-acp.mjs'], ['pi', PiRpcAdapter, 'fake-pi.mjs'], ['zcode', ZcodeAdapter, 'fake-zcode.mjs']] as const) {
  for (const outcome of ['success', 'failure', 'cancel']) {
    test(`${name} compaction stays pending and shares progress events: ${outcome}`, async () => {
      const adapter = new Adapter([process.execPath, join(here, 'fixtures', fixture)], { ZCODE_DATA_BASE_DIR: here, FAKE_COMPACT_DELAY: '150', ...(outcome === 'failure' ? { FAKE_COMPACT_FAIL: '1' } : {}) });
      const session = await adapter.start({ cwd: here });
      const events: EmittedEvent[] = [];
      const hooks = { emit: (event: EmittedEvent) => events.push(event), requestApproval: async () => ({ cancelled: true as const }) };
      try {
        let settled = false;
        const pending = session.prompt({ text: '/compact', images: [] }, hooks).then((result) => { settled = true; return result; });
        await new Promise((resolve) => setTimeout(resolve, 50));
        assert.equal(settled, false);
        assert.equal(events.filter((event) => event.type === 'tool.start' && event.data.id === 'context-compaction').length, 1);
        assert.equal(events.some((event) => event.type === 'tool.end'), false);
        if (outcome === 'cancel') await session.cancel();
        const result = await pending;
        assert.equal(result.stopReason, outcome === 'success' ? 'end_turn' : outcome === 'cancel' ? 'cancelled' : 'error');
        const ends = events.filter((event) => event.type === 'tool.end' && event.data.id === 'context-compaction');
        assert.equal(ends.length, 1);
        assert.equal(ends[0].data.status, outcome === 'success' ? 'completed' : 'failed');
        assert.equal((await session.prompt({ text: 'hello', images: [] }, hooks)).stopReason, 'end_turn');
      } finally { await session.close(); }
    });
  }
}
