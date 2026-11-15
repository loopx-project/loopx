import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { probeVoice } from '../../../scripts/codex-voice-probe.mjs';

const fixture = fileURLToPath(new URL('./fixtures/fake-codex-voice.mjs', import.meta.url));

async function probe(scenario: string, connect = true) {
  const dir = await mkdtemp(join(tmpdir(), 'calli-voice-test-'));
  const log = join(dir, 'requests.jsonl');
  try {
    const result = await probeVoice({
      command: [process.execPath, fixture], connect, protocol: 'v3', audioOutput: true,
      requestTimeoutMs: scenario === 'timeout' ? 200 : 3000, observeMs: 80,
      env: { ...process.env, FAKE_VOICE_SCENARIO: scenario, FAKE_VOICE_LOG: log },
    });
    const messages = (await readFile(log, 'utf8')).trim().split('\n').map(line => JSON.parse(line));
    return { result, messages };
  } finally { await rm(dir, { recursive: true, force: true }); }
}

test('voice probe is read-only by default and excludes account data', async () => {
  const { result, messages } = await probe('quiet', false);
  assert.equal(result.status, 'capabilities_checked');
  assert.equal(result.accountType, 'chatgpt');
  assert.equal(result.interfaceAvailable, true);
  assert.deepEqual(result.voiceCounts, { v1: 1, v2: 2 });
  assert.equal(result.processClosed, true);
  assert.equal(result.subscriptionBillingVerified, false);
  assert.ok(!messages.some(m => m.method === 'thread/start' || m.method === 'thread/realtime/start'));
  assert.ok(!JSON.stringify(result).includes('SECRET_'));
});

test('voice probe refuses to connect with API-key auth', async () => {
  const { result, messages } = await probe('key');
  assert.equal(result.status, 'blocked');
  assert.equal(result.error, 'chatgpt_login_required');
  assert.ok(!messages.some(m => m.method === 'thread/start'));
  assert.equal(result.processClosed, true);
});

for (const scenario of ['early-error', 'late-error']) {
  test(`voice probe preserves ${scenario} despite an accepted start RPC`, async () => {
    const { result, messages } = await probe(scenario);
    assert.equal(result.startAccepted, true);
    assert.equal(result.status, 'failed');
    assert.equal(result.error, 'api_key_auth_required');
    assert.equal(result.stopAcknowledged, true);
    assert.equal(result.processClosed, true);
    assert.ok(!JSON.stringify(result).includes('SECRET_'));
    assert.ok(!JSON.stringify(result).includes('private.invalid'));
    const start = messages.find(m => m.method === 'thread/start');
    assert.equal(start.params.ephemeral, true);
    assert.equal(start.params.sandbox, 'read-only');
    assert.equal(start.params.approvalPolicy, 'on-request');
    assert.equal(messages.filter(m => m.method === 'thread/realtime/start').length, 1);
    assert.ok(messages.some(m => m.method === 'thread/realtime/stop'));
  });
}

test('started notification does not claim audio or subscription billing verification', async () => {
  const { result } = await probe('started');
  assert.equal(result.startedNotification, true);
  assert.equal(result.nativeSessionIdObserved, true);
  assert.equal(result.status, 'unverified');
  assert.equal(result.audioVerified, false);
  assert.equal(result.subscriptionBillingVerified, false);
  assert.ok(!JSON.stringify(result).includes('SECRET_'));
});

test('diagnostics do not approve native requests', async () => {
  const { messages } = await probe('tool');
  const response = messages.find(m => m.id === 'tool-request');
  assert.equal(response.error.code, -32601);
  assert.equal(response.result, undefined);
});

test('RPC timeouts close the owned process', async () => {
  const { result } = await probe('timeout');
  assert.equal(result.error, 'timeout');
  assert.equal(result.processClosed, true);
  assert.equal(result.startAccepted, false);
});

test('native process exit fails the pending start', async () => {
  const { result } = await probe('exit');
  assert.equal(result.status, 'failed');
  assert.equal(result.processClosed, true);
  assert.equal(result.startAccepted, false);
});
