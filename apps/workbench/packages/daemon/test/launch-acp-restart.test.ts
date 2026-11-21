import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

const fixture = fileURLToPath(new URL('./fixtures/launch-acp-restart.ts', import.meta.url));
const run = (root: string, stage: string) => new Promise<{ code: number | null; signal: string | null; stderr: string }>((resolve, reject) => {
  const child = spawn(process.execPath, ['--import', 'tsx', '--import', fileURLToPath(new URL('./home-env.mjs', import.meta.url)), fixture, root, stage], { stdio: ['ignore', 'ignore', 'pipe'], timeout: 15000 });
  let stderr = '';
  child.stderr.on('data', chunk => { stderr += chunk; });
  child.on('error', reject);
  child.on('exit', (code, signal) => resolve({ code, signal, stderr }));
});

test('a Claude launch interrupted in native usage preparation resends once, then confirms without duplication', async () => {
  const root = mkdtempSync(join(tmpdir(), 'launch-acp-restart-'));
  mkdirSync(join(root, 'claude', 'projects'), { recursive: true });
  try {
    const stopped = await run(root, 'prepare');
    assert.equal(stopped.signal, 'SIGKILL', stopped.stderr);
    const requests = () => readFileSync(join(root, 'requests.jsonl'), 'utf8').trim().split('\n').map(line => JSON.parse(line));
    assert.equal(requests().filter(row => row.method === 'session/prompt').length, 0, 'the native agent was never asked');
    const before = JSON.parse(readFileSync(join(root, 'before.json'), 'utf8'));
    assert.equal(before.binding.firstMessage, 'Review the release');
    const restarted = await run(root, 'recover');
    assert.equal(restarted.code, 0, restarted.stderr);
    const after = JSON.parse(readFileSync(join(root, 'after.json'), 'utf8'));
    assert.equal(requests().filter(row => row.method === 'session/prompt').length, 1, 'confirming must dispatch the unsent first message');
    assert.equal(before.events.filter((e: any) => e.type === 'turn.phase' && e.data.phase === 'prompt_sent').length, 0, 'preparation is not a submission attempt');
    assert.equal(after.events.filter((e: any) => e.type === 'turn.started').length, 2, 'one interrupted turn and one recovery turn');
    assert.equal(after.events.filter((e: any) => e.type === 'turn.phase' && e.data.phase === 'prompt_sent').length, 1);
    assert.equal(after.confirmed.turnId, after.recovered.turnId);
    assert.equal(after.binding.firstMessage, undefined, 'agent output confirms delivery');
  } finally { rmSync(root, { recursive: true, force: true }); }
});


test('a submitted Claude prompt without agent output remains uncertain across repeated confirmation', async () => {
  const root = mkdtempSync(join(tmpdir(), 'launch-acp-uncertain-'));
  try {
    const stopped = await run(root, 'submitted');
    assert.equal(stopped.signal, 'SIGKILL', stopped.stderr);
    const restarted = await run(root, 'recover');
    assert.equal(restarted.code, 0, restarted.stderr);
    const after = JSON.parse(readFileSync(join(root, 'after.json'), 'utf8'));
    const requests = readFileSync(join(root, 'requests.jsonl'), 'utf8').trim().split('\n').map(line => JSON.parse(line));
    assert.equal(requests.filter(row => row.method === 'session/prompt').length, 1, 'an uncertain prompt must not be replayed');
    assert.equal(after.recovered.delivery, 'uncertain');
    assert.equal(after.confirmed.delivery, 'uncertain');
    assert.equal(after.confirmed.turnId, after.recovered.turnId);
    assert.equal(after.binding.firstMessage, 'Review the release', 'the request remains recoverable');
    assert.equal(after.events.filter((e: any) => e.type === 'turn.started').length, 1);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
