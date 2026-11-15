import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AddressInfo } from 'node:net';
import { createHttpServer } from '../src/http.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { AttachmentStore } from '../src/attachments.js';
import { ClaudeAcpAdapter } from '../src/adapters/claude.js';
import { CodexAppServerAdapter } from '../src/adapters/codex.js';
import { PiRpcAdapter } from '../src/adapters/pi.js';
import { claudeSystemPromptLine, codexSystemPromptMeta, piSystemPromptLine, scanJsonl } from '../src/adapters/system-prompt.js';
import type { AgentAdapter } from '../src/adapters/types.js';

test('each agent\'s own line shape yields the whole prompt it persisted', () => {
  // Claude Code: an attachment holding a snapshot of the system prompt, its parts joined.
  assert.equal(claudeSystemPromptLine(JSON.stringify({
    type: 'attachment', attachment: { type: 'prompt_snapshot', systemPrompt: ['You are Claude Code.', '', '# Tools'] },
  })), 'You are Claude Code.\n\n# Tools');
  // A newer snapshot wins over an older one; other attachments say nothing.
  assert.equal(claudeSystemPromptLine('{"type":"attachment","attachment":{"type":"hook_success"}}'), null);
  assert.equal(claudeSystemPromptLine('not json at all'), null);
  // Codex: the rollout's first record carries the base instructions, as a string or an object.
  assert.equal(codexSystemPromptMeta(JSON.stringify({
    type: 'session_meta', payload: { base_instructions: { text: 'You are Codex.' } },
  })), 'You are Codex.');
  assert.equal(codexSystemPromptMeta(JSON.stringify({
    type: 'session_meta', payload: { base_instructions: 'You are Codex, plain string.' },
  })), 'You are Codex, plain string.');
  assert.equal(codexSystemPromptMeta('{"type":"event_msg","payload":{}}'), null);
  // pi: the system message of the session, its sections in their written order, blank line between.
  assert.equal(piSystemPromptLine(JSON.stringify({
    type: 'message', message: { role: 'system', content: '', sections: { preamble: 'You are pi.', tools: '<tools>read</tools>' } },
  })), 'You are pi.\n\n<tools>read</tools>');
  assert.equal(piSystemPromptLine('{"type":"message","message":{"role":"user","content":"hi"}}'), null);
});

test('scanJsonl walks lines across chunk edges and treats a missing file as no prompt', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-sysprompt-'));
  try {
    const path = join(dir, 'session.jsonl');
    const lines: string[] = [];
    // The last line without its newline is still the last line; a torn half of one is ignored by
    // the parsers, which answer null for anything they cannot read.
    writeFileSync(path, '{"a":1}\n{"a":2}\n{"a"');
    const seen = await scanJsonl(path, (line) => lines.push(line));
    assert.equal(seen, true);
    assert.deepEqual(lines, ['{"a":1}', '{"a":2}', '{"a"']);
    assert.equal(await scanJsonl(join(dir, 'missing.jsonl'), () => assert.fail('nothing to read')), false);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

/** The session file each agent really writes, trimmed to the lines that hold the prompt. */
function writeSessions(root: string) {
  const claude = join(root, 'claude-home');
  const codex = join(root, 'codex-home');
  const pi = join(root, 'pi-home');
  mkdirSync(join(claude, 'projects', '-project'), { recursive: true });
  writeFileSync(join(claude, 'projects', '-project', '11111111-2222-3333-4444-555555555555.jsonl'), [
    '{"type":"user","message":{"role":"user","content":"hi"}}',
    `{"type":"attachment","attachment":{"type":"prompt_snapshot","systemPrompt":["You are Claude Code.","# Tools"]}}`,
    '{"type":"assistant","message":{"role":"assistant"}}',
    `{"type":"attachment","attachment":{"type":"prompt_snapshot","systemPrompt":["You are Claude Code, revised.","# Tools"]}}`,
  ].join('\n'));
  mkdirSync(join(codex, 'sessions', '2026', '10', '16'), { recursive: true });
  writeFileSync(join(codex, 'sessions', '2026', '10', '16', 'rollout-2026-10-16T00-00-00-99999999-9999-9999-9999-999999999999.jsonl'), [
    JSON.stringify({ type: 'session_meta', payload: { session_id: '99999999-9999-9999-9999-999999999999', base_instructions: { text: 'You are Codex.' } } }),
    '{"type":"event_msg","payload":{"type":"user_message"}}',
  ].join('\n'));
  mkdirSync(join(pi, 'sessions', '-project'), { recursive: true });
  writeFileSync(join(pi, 'sessions', '-project', '2026-10-16T00-00-00.000Z_99999999-9999-9999-9999-999999999998.jsonl'), [
    '{"type":"session","version":3}',
    JSON.stringify({ type: 'message', message: { role: 'system', content: '', sections: { preamble: 'You are pi.', tools: '<tools>' } } }),
    '{"type":"message","message":{"role":"user","content":"hi"}}',
  ].join('\n'));
  return { claude, codex, pi };
}

test('adapters read the prompt from their own session file, newest snapshot last, without starting anything', async () => {
  const root = mkdtempSync(join(tmpdir(), 'calli-sysprompt-adapters-'));
  try {
    const homes = writeSessions(root);
    const id = '99999999-9999-9999-9999-999999999999';
    const codex = new CodexAppServerAdapter(['calli-not-needed'], { CODEX_HOME: homes.codex });
    assert.deepEqual(await codex.readSystemPrompt(id, root), { text: 'You are Codex.', source: 'Codex 会话文件' });
    // A session that never wrote a file has no prompt, which is not an error.
    assert.equal(await codex.readSystemPrompt('88888888-8888-8888-8888-888888888888', root), null);

    const claude = new ClaudeAcpAdapter(['calli-not-needed'], { CLAUDE_CONFIG_DIR: homes.claude });
    assert.deepEqual(await claude.readSystemPrompt('11111111-2222-3333-4444-555555555555', root), {
      text: 'You are Claude Code, revised.\n\n# Tools',
      source: 'Claude Code 会话文件',
    });

    const pi = new PiRpcAdapter(['calli-not-needed'], { PI_CODING_AGENT_DIR: homes.pi });
    assert.deepEqual(await pi.readSystemPrompt('99999999-9999-9999-9999-999999999998', root), {
      text: 'You are pi.\n\n<tools>',
      source: 'pi 会话文件',
    });
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('the endpoint answers with the prompt, and says which agents do not keep one', async () => {
  const root = mkdtempSync(join(tmpdir(), 'calli-sysprompt-api-'));
  try {
    const homes = writeSessions(root);
    const codex = new CodexAppServerAdapter(['calli-not-needed'], { CODEX_HOME: homes.codex });
    const claude = new ClaudeAcpAdapter(['calli-not-needed'], { CLAUDE_CONFIG_DIR: homes.claude });
    const store = new Store(root);
    const manager = new ThreadManager(store, new Map<string, AgentAdapter>([['codex', codex], ['claude', claude]]), [root], new AttachmentStore(root));
    const token = 'test-sysprompt-token';
    const server = createHttpServer({ manager, token, webDir: null, version: 'test' });
    await new Promise<void>(r => server.listen(0, '127.0.0.1', r));
    const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    const get = (path: string) => fetch(base + path, { headers: { Authorization: `Bearer ${token}` } });
    try {
      const thread = await manager.createThread({ agent: 'codex', cwd: root, title: 'ledger' });
      // The endpoint works from the thread's own agent session id.
      store.db.prepare('UPDATE threads SET agent_session_id = ? WHERE id = ?').run('99999999-9999-9999-9999-999999999999', thread.id);
      assert.equal((await fetch(base + `/api/threads/${thread.id}/system-prompt`)).status, 401);
      const answer = await get(`/api/threads/${thread.id}/system-prompt`);
      assert.equal(answer.status, 200);
      assert.deepEqual(await answer.json(), { text: 'You are Codex.', source: 'Codex 会话文件' });
      assert.equal((await get(`/api/threads/${thread.id}/nope`)).status, 404);
      assert.equal((await get('/api/threads/does-not-exist/system-prompt')).status, 404);
      // The activity digest answers alongside, from the same store.
      store.appendEvent(thread.id, 't1', 'turn.started', { text: '问一句' });
      const digest = await get('/api/activity?days=7');
      assert.equal(digest.status, 200);
      const report = await digest.json() as { days: Array<{ prompts: unknown[] }> };
      assert.equal(report.days.length, 1);
      assert.equal(report.days[0].prompts.length, 1);
      assert.equal((await get('/api/activity?days=x')).status, 400);
    } finally { server.close(); await manager.shutdown(); store.close(); rmSync(root, { recursive: true, force: true }); }
  } finally { rmSync(root, { recursive: true, force: true }); }
});
