import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { AgyAdapter, copyDirectorySnapshot } from '../src/adapters/agy.js';
import { PiRpcAdapter } from '../src/adapters/pi.js';
import { portableRollout } from '../src/adapters/codex.js';

test('portableRollout drops sealed reasoning and keeps every other line as it is', () => {
  const source = [
    JSON.stringify({ timestamp: 't0', type: 'session_meta', payload: { id: 's1' } }),
    JSON.stringify({ timestamp: 't1', type: 'response_item', payload: { type: 'reasoning', encrypted_content: 'sealed' } }),
    JSON.stringify({ timestamp: 't2', type: 'response_item', payload: { type: 'reasoning', summary: [{ type: 'summary_text', text: 'visible gist' }] } }),
    JSON.stringify({ timestamp: 't3', type: 'event_msg', payload: { type: 'agent_message', message: 'ok' } }),
    'not json at all',
    '',
  ].join('\n');
  const lines = portableRollout(source).split('\n');
  assert.equal(lines.length, 5, 'four records plus the trailing newline');
  assert.deepEqual(lines.map((l) => (l.startsWith('{') ? JSON.parse(l).payload?.type ?? JSON.parse(l).type : l)),
    ['session_meta', 'reasoning', 'agent_message', 'not json at all', '']);
  // The summary text of the kept reasoning survives, the sealed content does not appear anywhere.
  assert.ok(lines[1].includes('visible gist'));
  assert.ok(!portableRollout(source).includes('sealed'));
});

test('portableRollout refuses a conversation whose compaction is sealed', () => {
  const compacted = JSON.stringify({ timestamp: 't', type: 'compacted', payload: { message: 'summary', encrypted_content: 'sealed' } });
  assert.throws(() => portableRollout(compacted), /compacted natively/);
  // A plain response item that carries sealed content is just as unreadable elsewhere.
  const item = JSON.stringify({ timestamp: 't', type: 'response_item', payload: { type: 'message', encrypted_content: 'sealed' } });
  assert.throws(() => portableRollout(item), /compacted natively/);
  // A compaction without sealed content can go over as it is.
  const plain = JSON.stringify({ timestamp: 't', type: 'compacted', payload: { message: 'plain summary' } });
  assert.ok(portableRollout(plain).includes('plain summary'));
});

/** A fake Antigravity conversation laid out the way the CLI keeps it, and where its transcript sits. */
function agyTree(root: string, id: string): string {
  const base = join(root, '.gemini', 'antigravity-cli');
  mkdirSync(join(base, 'conversations'), { recursive: true });
  mkdirSync(join(base, 'brain', id, '.system_generated', 'logs'), { recursive: true });
  mkdirSync(join(base, 'brain', id, 'scratch'), { recursive: true });
  mkdirSync(join(base, 'annotations'), { recursive: true });
  writeFileSync(join(base, 'conversations', `${id}.db`), 'db-bytes');
  writeFileSync(join(base, 'conversations', `${id}.db-wal`), 'wal-bytes');
  writeFileSync(join(base, 'brain', id, '.system_generated', 'logs', 'transcript.jsonl'), 'transcript');
  writeFileSync(join(base, 'brain', id, 'scratch', 'note.txt'), 'scratch');
  writeFileSync(join(base, 'annotations', `${id}.pbtxt`), 'annotation');
  return join(base, 'brain', id, '.system_generated', 'logs', 'transcript.jsonl');
}

test('an Antigravity conversation is copied whole under the same id, and a same-id one is refused', async () => {
  const root = mkdtempSync(join(tmpdir(), 'calli-agy-import-'));
  try {
    const id = '11111111-2222-3333-4444-555555555555';
    const source = agyTree(join(root, 'one'), id);
    const adapter = new AgyAdapter(['unused'], { CALLI_AGY_HOME: join(root, 'two', '.gemini', 'antigravity-cli') });
    assert.equal(await adapter.importSession(source), id);
    const to = join(root, 'two', '.gemini', 'antigravity-cli');
    assert.equal(readFileSync(join(to, 'conversations', `${id}.db`), 'utf8'), 'db-bytes');
    assert.equal(readFileSync(join(to, 'conversations', `${id}.db-wal`), 'utf8'), 'wal-bytes');
    assert.equal(readFileSync(join(to, 'brain', id, 'scratch', 'note.txt'), 'utf8'), 'scratch');
    assert.equal(readFileSync(join(to, 'annotations', `${id}.pbtxt`), 'utf8'), 'annotation');
    // Nothing was written into the source account.
    assert.equal(readdirSync(join(root, 'one', '.gemini', 'antigravity-cli', 'conversations')).length, 2);
    // A conversation this account already has is not overwritten.
    await assert.rejects(() => adapter.importSession(source), /already has conversation/);
    // A missing annotations file does not stop the copy.
    const other = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
    const second = agyTree(join(root, 'three'), other);
    rmSync(join(root, 'three', '.gemini', 'antigravity-cli', 'annotations', `${other}.pbtxt`));
    assert.equal(await new AgyAdapter(['unused'], { CALLI_AGY_HOME: join(to) }).importSession(second), other);
    assert.ok(readFileSync(join(to, 'brain', other, 'scratch', 'note.txt'), 'utf8'));
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('copyDirectorySnapshot copies nested folders and skips entries that vanish mid-copy', async () => {
  const root = mkdtempSync(join(tmpdir(), 'calli-agy-snapshot-'));
  try {
    mkdirSync(join(root, 'from', 'deep', 'deeper'), { recursive: true });
    writeFileSync(join(root, 'from', 'top.txt'), 'top');
    writeFileSync(join(root, 'from', 'deep', 'mid.txt'), 'mid');
    writeFileSync(join(root, 'from', 'deep', 'deeper', 'bottom.txt'), 'bottom');
    symlinkSync(join(root, 'gone-target'), join(root, 'from', 'dangling'));
    await copyDirectorySnapshot(join(root, 'from'), join(root, 'to'));
    assert.equal(readFileSync(join(root, 'to', 'top.txt'), 'utf8'), 'top');
    assert.equal(readFileSync(join(root, 'to', 'deep', 'deeper', 'bottom.txt'), 'utf8'), 'bottom');
    assert.ok(!exists(join(root, 'to', 'dangling')), 'a dangling symlink is left out');
  } finally { rmSync(root, { recursive: true, force: true }); }
});

function exists(path: string): boolean {
  try { readFileSync(path); return true; } catch { return false; }
}

test('a pi session is copied under a new id with only the header renamed', async () => {
  const root = mkdtempSync(join(tmpdir(), 'calli-pi-import-'));
  try {
    const oldId = '01234567-89ab-cdef-0123-456789abcdef';
    const dir = join(root, 'one', 'sessions', '-work-app');
    mkdirSync(dir, { recursive: true });
    const source = join(dir, `2026-01-02T03-04-05Z_${oldId}.jsonl`);
    const body = [
      JSON.stringify({ type: 'session', id: oldId, cwd: '/work/app', version: '1' }),
      JSON.stringify({ type: 'message', entryId: 'm1' }),
      JSON.stringify({ type: 'message', entryId: 'm2', text: `talked about ${oldId}` }),
    ].join('\n') + '\n';
    writeFileSync(source, body);
    const id = await new PiRpcAdapter(['unused'], { PI_CODING_AGENT_DIR: join(root, 'two') }).importSession(source);
    assert.notEqual(id, oldId);
    const copy = join(root, 'two', 'sessions', '-work-app', `2026-01-02T03-04-05Z_${id}.jsonl`);
    const lines = readFileSync(copy, 'utf8').trim().split('\n').map((l) => JSON.parse(l));
    assert.equal(lines[0].type, 'session');
    assert.equal(lines[0].id, id);
    assert.equal(lines[0].cwd, '/work/app');
    // Entries keep their content, including a reference to the old id in the conversation itself.
    assert.equal(lines[2].text, `talked about ${oldId}`);
    assert.equal(readFileSync(source, 'utf8'), body);
    // A file that is not a pi session is refused rather than copied blindly.
    const stray = join(dir, 'broken.jsonl');
    writeFileSync(stray, JSON.stringify({ type: 'other', id: oldId }) + '\n');
    await assert.rejects(() => new PiRpcAdapter(['unused'], { PI_CODING_AGENT_DIR: join(root, 'two') }).importSession(stray), /not a pi session file/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
