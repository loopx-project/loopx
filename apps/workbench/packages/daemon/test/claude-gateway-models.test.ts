import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { ClaudeAcpAdapter } from '../src/adapters/claude.js';
import { RpcError } from '../src/adapters/jsonrpc.js';

const here = dirname(fileURLToPath(import.meta.url));
const command = [process.execPath, join(here, 'fixtures/fake-acp.mjs')];

for (const gateway of [false, true]) {
  test(`Claude ${gateway ? 'gateway' : 'subscription'} listing sends no model selection requests`, async t => {
    const directory = mkdtempSync(join(tmpdir(), 'claude-model-list-'));
    t.after(() => rmSync(directory, { recursive: true, force: true }));
    const log = join(directory, 'selections.log');
    const adapter = new ClaudeAcpAdapter(command, {
      FAKE_ACP_MODEL_SELECTION_LOG: log,
      ...(gateway ? { ANTHROPIC_BASE_URL: 'http://gateway.invalid' } : {}),
    });
    const models = await adapter.models();
    assert.deepEqual(models.map(model => model.id), ['default', 'big', 'small', 'broken']);
    assert.equal(existsSync(log), false, 'reading the catalog must not select any model');
    await assert.rejects(adapter.start({ cwd: here, model: 'broken' }), error => error instanceof RpcError && error.code === -32603);
    assert.equal(readFileSync(log, 'utf8'), 'broken\n', 'access is checked when the model is selected');
  });
}
