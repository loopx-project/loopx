import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, renameSync, mkdirSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { ClaudeAcpAdapter } from '../src/adapters/claude.js';
import { PiRpcAdapter } from '../src/adapters/pi.js';
import { CodexAppServerAdapter } from '../src/adapters/codex.js';
import { AgyAdapter } from '../src/adapters/agy.js';
import { TraexAcpAdapter } from '../src/adapters/traex.js';

const fixture = join(dirname(fileURLToPath(import.meta.url)), 'fixtures', 'fake-session-store.mjs');
const record = (kind: string, root: string, id: string, cwd: string) => execFileSync(process.execPath, [fixture, kind, id, cwd], {
  env: { ...process.env, FAKE_SESSION_ROOT: root }, encoding: 'utf8',
});

test('session locations belong to the configured instance and only report files that exist', async () => {
  const root = mkdtempSync(join(tmpdir(), 'calli-session-location-'));
  const cwd = join(root, 'project with spaces');
  const id = 'session-123';
  try {
    const first = join(root, "claude's first");
    const second = join(root, 'claude-second');
    const file1 = record('claude', first, id, cwd);
    const file2 = record('claude', second, id, cwd);
    const a = new ClaudeAcpAdapter(['unused'], { CLAUDE_CONFIG_DIR: first, ANTHROPIC_API_KEY: 'must-not-copy' });
    const b = new ClaudeAcpAdapter(['unused'], { CLAUDE_CONFIG_DIR: second });
    const before = readFileSync(file1, 'utf8');
    const one = await a.sessionLocation(id, cwd);
    const two = await b.sessionLocation(id, cwd);
    assert.equal(one.sessionPath, file1);
    assert.equal(two.sessionPath, file2);
    assert.equal(one.configDir, first);
    assert.match(one.resumeCommand!, /CLAUDE_CONFIG_DIR=/);
    assert.doesNotMatch(JSON.stringify(one), /must-not-copy|ANTHROPIC_API_KEY/);
    // Shell quoting preserves an apostrophe in the account directory.
    const command = one.resumeCommand!.replace(/'claude' --resume .+$/, "sh -c 'printf %s \"$CLAUDE_CONFIG_DIR\"'");
    assert.equal(execFileSync('/bin/sh', ['-c', command], { encoding: 'utf8' }), first);
    assert.equal(readFileSync(file1, 'utf8'), before, 'locating a session is read-only');
    assert.equal((await a.sessionLocation(null, cwd)).sessionPath, null);
    assert.equal((await a.sessionLocation('missing', cwd)).sessionPath, null);
    assert.equal((await a.sessionLocation('../escape', cwd)).sessionPath, null);

    const cases = [
      ['pi', 'PI_CODING_AGENT_DIR', PiRpcAdapter],
      ['codex', 'CODEX_HOME', CodexAppServerAdapter],
      ['agy', 'CALLI_AGY_HOME', AgyAdapter],
      ['traex', 'CALLI_TRAEX_HOME', TraexAcpAdapter],
    ] as const;
    for (const [kind, key, Adapter] of cases) {
      const configDir = join(root, kind);
      const path = record(kind, configDir, id, cwd);
      const adapter = new Adapter(['unused'], { [key]: configDir });
      const location = await adapter.sessionLocation(id, cwd);
      assert.equal(location.configDir, configDir, kind);
      assert.equal(location.sessionPath, path, kind);
      assert.equal((await adapter.sessionLocation('missing', cwd)).sessionPath, null, kind);
      if (kind === 'codex') {
        const archived = join(configDir, 'archived_sessions');
        mkdirSync(archived);
        const moved = join(archived, `rollout-2026-10-12-${id}.jsonl`);
        renameSync(path, moved);
        assert.equal((await adapter.sessionLocation(id, cwd)).sessionPath, moved);
      }
    }
  } finally { rmSync(root, { recursive: true, force: true }); }
});
