import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ZcodeAdapter, ZCODE_BASIC_TOOLS } from '../src/adapters/zcode.js';
import { expandEnvTemplate, mcpToolName, probeMcpToolNames } from '../src/adapters/mcp-probe.js';

const here = import.meta.dirname;
const fakeMcp = [process.execPath, join(here, 'fixtures', 'fake-mcp.mjs')];

/** A zcode home with one installed plugin, plus whatever config.json says about it. */
function zcodeHome(enabled: boolean | undefined) {
  const dir = mkdtempSync(join(tmpdir(), 'calli-zcode-home-'));
  const pluginDir = join(dir, 'plugin-vision');
  mkdirSync(pluginDir, { recursive: true });
  writeFileSync(join(pluginDir, '.mcp.json'), JSON.stringify({
    mcpServers: {
      vision: { type: 'stdio', command: fakeMcp[0], args: [fakeMcp[1]], env: { Z_AI_MODE: 'ZHIPU', Z_AI_API_KEY: '${ZAI_CODING_CN_API_KEY}' } },
    },
  }));
  mkdirSync(join(dir, '.zcode', 'cli', 'plugins'), { recursive: true });
  writeFileSync(join(dir, '.zcode', 'cli', 'plugins', 'installed_plugins.json'), JSON.stringify({
    version: 1,
    plugins: [{ id: 'vision@local', name: 'vision', marketplace: 'local', version: '0.1.0', installPath: pluginDir, scope: 'user' }],
  }));
  writeFileSync(join(dir, '.zcode', 'cli', 'config.json'), JSON.stringify({
    plugins: { enabledPlugins: enabled === undefined ? {} : { 'vision@local': enabled } },
  }));
  return dir;
}

test('mcpToolName mirrors the exact names zcode matches in the allowlist', () => {
  assert.equal(mcpToolName('zai-vision', 'analyze_image'), 'mcp__zai-vision__analyze_image');
  assert.equal(mcpToolName('odd:name:here', 'tool:now!'), 'mcp__odd_name_here__tool_now_');
  assert.equal(mcpToolName('', ''), 'mcp__unknown__unknown');
});

test('expandEnvTemplate resolves ${VAR} and reports unresolved ones as null', () => {
  const env = { ZAI_CODING_CN_API_KEY: 'k-1', HOME: '/home/u' };
  assert.equal(expandEnvTemplate('${ZAI_CODING_CN_API_KEY}', env), 'k-1');
  assert.equal(expandEnvTemplate('prefix:${HOME}:suffix', env), 'prefix:/home/u:suffix');
  assert.equal(expandEnvTemplate('plain', env), 'plain');
  assert.equal(expandEnvTemplate('${NO_SUCH_VAR}', env), null);
});

test('probeMcpToolNames lists a server\'s tools; a dead one yields none', async () => {
  assert.deepEqual(await probeMcpToolNames({ name: 'fake-mcp', command: fakeMcp[0], args: [fakeMcp[1]], env: {} }), [
    'mcp__fake-mcp__analyze_image',
    'mcp__fake-mcp__ping',
  ]);
  assert.deepEqual(await probeMcpToolNames({ name: 'dead', command: fakeMcp[0], args: [fakeMcp[1]], env: { FAKE_MCP_FAIL: '1' } }, 5000), []);
});

test('session/create passes the plugin\'s MCP server and its probed tools in the allowlist', async (t) => {
  const home = zcodeHome(undefined);
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const dir = mkdtempSync(join(tmpdir(), 'calli-mcp-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const log = join(dir, 'wire.jsonl');
  const adapter = new ZcodeAdapter([process.execPath, join(here, 'fixtures', 'fake-zcode.mjs')], {
    ZCODE_DATA_BASE_DIR: home,
    ZAI_CODING_CN_API_KEY: 'test-key',
    FAKE_ZCODE_LOG: log,
  });
  const session = await adapter.start({ cwd: here });
  await session.close();

  const requests = readFileSync(log, 'utf8').trim().split('\n').map((line) => JSON.parse(line));
  const create = requests.find((r) => r.method === 'session/create');
  assert.ok(create, 'session/create was sent');
  assert.deepEqual(create.params.mcpServers, [{ name: 'vision', command: fakeMcp[0], args: [fakeMcp[1]], env: [{ name: 'Z_AI_MODE', value: 'ZHIPU' }, { name: 'Z_AI_API_KEY', value: 'test-key' }] }]);
  assert.ok(create.params.toolAllowlist.includes('mcp__vision__analyze_image'));
  assert.ok(create.params.toolAllowlist.includes('mcp__vision__ping'));
  for (const tool of ZCODE_BASIC_TOOLS) assert.ok(create.params.toolAllowlist.includes(tool));
});

test('a plugin with no arguments still sends args as an empty list, which real zcode requires', async (t) => {
  // The real zai-vision plugin declares `"args": []`; real zcode's strict schema makes args a
  // required field, so omitting it once made every zcode session fail to create. The fake server
  // is a self-contained executable: with no args to carry the script path, command alone runs it.
  const home = zcodeHome(undefined);
  const serverScript = join(home, 'plugin-vision', 'fake-mcp-server.sh');
  writeFileSync(serverScript, `#!/bin/sh\nexec ${JSON.stringify(fakeMcp[0])} ${JSON.stringify(fakeMcp[1])}\n`, { mode: 0o755 });
  writeFileSync(join(home, 'plugin-vision', '.mcp.json'), JSON.stringify({
    mcpServers: { 'zai-vision': { type: 'stdio', command: serverScript, args: [], env: { Z_AI_MODE: 'ZHIPU' } } },
  }));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const dir = mkdtempSync(join(tmpdir(), 'calli-mcp-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const log = join(dir, 'wire.jsonl');
  const adapter = new ZcodeAdapter([process.execPath, join(here, 'fixtures', 'fake-zcode.mjs')], {
    ZCODE_DATA_BASE_DIR: home,
    ZAI_CODING_CN_API_KEY: 'test-key',
    FAKE_ZCODE_LOG: log,
  });
  const session = await adapter.start({ cwd: here });
  await session.close();

  const requests = readFileSync(log, 'utf8').trim().split('\n').map((line) => JSON.parse(line));
  const create = requests.find((r) => r.method === 'session/create');
  assert.ok(create, 'session/create was sent');
  assert.deepEqual(create.params.mcpServers, [{ name: 'zai-vision', command: serverScript, args: [], env: [{ name: 'Z_AI_MODE', value: 'ZHIPU' }] }]);
  assert.ok(create.params.toolAllowlist.includes('mcp__zai-vision__analyze_image'), 'the probed tools still widen the allowlist');
});

test('a disabled plugin and an absent zcode home both leave the baseline restrictions', async (t) => {
  for (const zcodeDataDir of [zcodeHome(false), mkdtempSync(join(tmpdir(), 'calli-empty-'))]) {
    t.after(() => rmSync(zcodeDataDir, { recursive: true, force: true }));
    const dir = mkdtempSync(join(tmpdir(), 'calli-mcp-'));
    t.after(() => rmSync(dir, { recursive: true, force: true }));
    const log = join(dir, 'wire.jsonl');
    const adapter = new ZcodeAdapter([process.execPath, join(here, 'fixtures', 'fake-zcode.mjs')], {
      ZCODE_DATA_BASE_DIR: zcodeDataDir, ZAI_CODING_CN_API_KEY: 'test-key', FAKE_ZCODE_LOG: log,
    });
    const session = await adapter.start({ cwd: here });
    await session.close();

    const requests = readFileSync(log, 'utf8').trim().split('\n').map((line) => JSON.parse(line));
    const create = requests.find((r) => r.method === 'session/create');
    assert.deepEqual(create.params.mcpServers, []);
    assert.deepEqual(create.params.toolAllowlist, [...ZCODE_BASIC_TOOLS]);
  }
});
