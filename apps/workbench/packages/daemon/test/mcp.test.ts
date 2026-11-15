import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Server } from 'node:http';
import { ClaudeAcpAdapter } from '../src/adapters/claude.js';
import { CodexAppServerAdapter } from '../src/adapters/codex.js';
import { PiRpcAdapter } from '../src/adapters/pi.js';
import type { AgentAdapter } from '../src/adapters/types.js';
import { installTomlMcpServer, removeTomlMcpServer, tomlMcpServers } from '../src/mcp-format.js';
import { createHttpServer } from '../src/http.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { Workdirs } from '../src/workdirs.js';
import { AttachmentStore } from '../src/attachments.js';

const here = dirname(fileURLToPath(import.meta.url));
const TOKEN = 'test-token-0123456789';

/**
 * The configs under test, one per format: Claude's `.claude.json` (full of its own state beyond
 * the MCP servers), Codex's config.toml (MCP tables between unrelated settings) and pi's mcp.json
 * (servers with fields only pi knows, such as `exposure`). Writes must leave everything that is
 * not the one server alone.
 */
let home: string;
let workdir: string;
let dataDir: string;
let server: Server;
let base: string;
let manager: ThreadManager;

const auth = { Authorization: `Bearer ${TOKEN}` };

function api(path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(base + path, { ...init, headers: { 'Content-Type': 'application/json', ...auth, ...(init.headers ?? {}) } });
}

const CODEX_TOML = `# 主配置
model = "gpt-5.2-codex"
model_reasoning_effort = "high"

[projects."my proj"]
trust_level = "trusted"

[mcp_servers.fs]
command = "npx"
args = [
  "-y",
  "@modelcontextprotocol/server-filesystem",
  "/tmp"
]

[mcp_servers.github]
url = "https://api.githubcopilot.com/mcp/"
bearer_token_env_var = "GITHUB_PAT"

[mcp_servers.fs.env]
FS_ROOT = "/data"
DEBUG = "true"

[profiles.work]
model = "o4-mini"
`;

before(async () => {
  home = mkdtempSync(join(tmpdir(), 'calli-mcp-home-'));
  workdir = mkdtempSync(join(tmpdir(), 'calli-mcp-work-'));
  dataDir = mkdtempSync(join(tmpdir(), 'calli-mcp-data-'));

  writeFileSync(
    join(home, '.claude.json'),
    JSON.stringify(
      {
        numStartups: 42,
        oauthAccount: { email: 'a@b.c' },
        mcpServers: { botmux: { command: 'botmux', args: ['--stdio'], env: { TOKEN: 't' } } },
        projects: {},
      },
      null,
      2,
    ),
  );
  mkdirSync(join(home, '.codex'), { recursive: true });
  writeFileSync(join(home, '.codex', 'config.toml'), CODEX_TOML);
  mkdirSync(join(home, '.pi', 'agent'), { recursive: true });
  writeFileSync(
    join(home, '.pi', 'agent', 'mcp.json'),
    JSON.stringify(
      {
        mcpServers: {
          'zai-mcp-server': {
            command: 'zai-mcp-server',
            args: [],
            env: { Z_AI_API_KEY: '${ZAI_CODING_CN_API_KEY}' },
            exposure: 'direct',
          },
        },
      },
      null,
      2,
    ),
  );

  const env = { HOME: home };
  const adapters = new Map<string, AgentAdapter>();
  adapters.set('claude', new ClaudeAcpAdapter([process.execPath, join(here, 'fixtures', 'fake-acp.mjs')], { ...env, CLAUDE_CONFIG_DIR: join(home, '.claude') }));
  adapters.set('pi', new PiRpcAdapter([process.execPath, join(here, 'fixtures', 'fake-pi.mjs')], { ...env, PI_CODING_AGENT_DIR: join(home, '.pi', 'agent') }));
  adapters.set('codex', new CodexAppServerAdapter([process.execPath, join(here, 'fixtures', 'fake-codex.mjs')], { ...env, CODEX_HOME: join(home, '.codex') }));
  const store = new Store(dataDir);
  manager = new ThreadManager(store, adapters, new Workdirs([workdir]), new AttachmentStore(dataDir));
  server = createHttpServer({ manager, token: TOKEN, webDir: null, version: 'test' });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()));
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
});

after(async () => {
  server.close();
  await manager.shutdown();
  rmSync(home, { recursive: true, force: true });
  rmSync(workdir, { recursive: true, force: true });
  rmSync(dataDir, { recursive: true, force: true });
});

test('the scan reads each format: json user scope, toml tables with sub-tables, unknown fields kept', async () => {
  const res = await api('/api/mcp');
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.deepEqual(body.agents.map((a: { agent: string }) => a.agent).sort(), ['claude', 'codex', 'pi']);

  const claude = body.agents.find((a: { agent: string }) => a.agent === 'claude');
  assert.equal(claude.writable, true);
  assert.equal(claude.servers[0].sourcePath, join(home, '.claude.json'));
  assert.deepEqual(claude.servers[0], { name: 'botmux', config: { command: 'botmux', args: ['--stdio'], env: { TOKEN: 't' } }, sourcePath: join(home, '.claude.json') });

  const codex = body.agents.find((a: { agent: string }) => a.agent === 'codex');
  const fs = codex.servers.find((s: { name: string }) => s.name === 'fs');
  assert.deepEqual(fs.config, {
    command: 'npx',
    args: ['-y', '@modelcontextprotocol/server-filesystem', '/tmp'],
    env: { FS_ROOT: '/data', DEBUG: 'true' },
  });
  const github = codex.servers.find((s: { name: string }) => s.name === 'github');
  assert.deepEqual(github.config, { url: 'https://api.githubcopilot.com/mcp/', bearer_token_env_var: 'GITHUB_PAT' });

  const pi = body.agents.find((a: { agent: string }) => a.agent === 'pi');
  assert.deepEqual(pi.servers[0].config, {
    command: 'zai-mcp-server',
    args: [],
    env: { Z_AI_API_KEY: '${ZAI_CODING_CN_API_KEY}' },
    exposure: 'direct',
  }, 'fields only pi knows travel with the server');
});

test('install into claude writes the one name and keeps the rest of .claude.json', async () => {
  const config = { command: 'weather-mcp', args: ['--stdio'], env: { UNIT: 'C' } };
  const res = await api('/api/mcp/install', { method: 'POST', body: JSON.stringify({ targets: ['claude'], name: 'weather', config }) });
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), [{ agent: 'claude', status: 'installed' }]);

  const doc = JSON.parse(readFileSync(join(home, '.claude.json'), 'utf8'));
  assert.equal(doc.numStartups, 42, 'Claude\'s own state survives');
  assert.deepEqual(doc.oauthAccount, { email: 'a@b.c' });
  assert.deepEqual(doc.mcpServers.botmux, { command: 'botmux', args: ['--stdio'], env: { TOKEN: 't' } });
  assert.deepEqual(doc.mcpServers.weather, config);

  const again = await api('/api/mcp/install', { method: 'POST', body: JSON.stringify({ targets: ['claude'], name: 'weather', config }) });
  assert.deepEqual(await again.json(), [{ agent: 'claude', status: 'same' }]);

  const changed = await api('/api/mcp/install', { method: 'POST', body: JSON.stringify({ targets: ['claude'], name: 'weather', config: { command: 'weather-mcp', args: ['--v2'] } }) });
  assert.deepEqual(await changed.json(), [{ agent: 'claude', status: 'exists' }], 'without overwrite, a different config is left alone');

  const forced = await api('/api/mcp/install', { method: 'POST', body: JSON.stringify({ targets: ['claude'], name: 'weather', config: { command: 'weather-mcp', args: ['--v2'] }, overwrite: true }) });
  assert.deepEqual(await forced.json(), [{ agent: 'claude', status: 'installed' }]);
  assert.deepEqual(JSON.parse(readFileSync(join(home, '.claude.json'), 'utf8')).mcpServers.weather, { command: 'weather-mcp', args: ['--v2'] });
});

test('install into codex replaces only that server\'s tables; every other line stands', async () => {
  const config = { command: 'npx', args: ['-y', '@modelcontextprotocol/server-filesystem', '/srv'], env: { FS_ROOT: '/srv' } };
  const res = await api('/api/mcp/install', { method: 'POST', body: JSON.stringify({ targets: ['codex'], name: 'fs', config, overwrite: true }) });
  assert.deepEqual(await res.json(), [{ agent: 'codex', status: 'installed' }]);

  const after = readFileSync(join(home, '.codex', 'config.toml'), 'utf8');
  assert.ok(after.includes('model = "gpt-5.2-codex"'), 'top-level keys stand');
  assert.ok(after.includes('[projects."my proj"]'), 'unrelated tables stand');
  assert.ok(after.includes('[profiles.work]'), 'tables after the MCP block stand');
  assert.ok(after.includes('[mcp_servers.github]'), 'other servers stand');
  assert.ok(!after.includes('"/tmp"'), 'the old fs args are gone');

  const listed = tomlMcpServers(join(home, '.codex', 'config.toml')).find((s) => s.name === 'fs');
  assert.deepEqual(listed?.config, config, 'what was written reads back the same');
});

test('install into pi keeps its own fields on the other servers', async () => {
  const res = await api('/api/mcp/install', { method: 'POST', body: JSON.stringify({ targets: ['pi'], name: 'playwright', config: { command: 'npx', args: ['-y', '@playwright/mcp@latest', '--headless'] } }) });
  assert.deepEqual(await res.json(), [{ agent: 'pi', status: 'installed' }]);
  const doc = JSON.parse(readFileSync(join(home, '.pi', 'agent', 'mcp.json'), 'utf8'));
  assert.equal(doc.mcpServers['zai-mcp-server'].exposure, 'direct');
  assert.deepEqual(doc.mcpServers.playwright, { command: 'npx', args: ['-y', '@playwright/mcp@latest', '--headless'] });
});

test('a name that is not there cannot be installed; neither can a config without command or url', async () => {
  const badName = await api('/api/mcp/install', { method: 'POST', body: JSON.stringify({ targets: ['claude'], name: 'not allowed!', config: { command: 'x' } }) });
  assert.equal(badName.status, 400);
  const badConfig = await api('/api/mcp/install', { method: 'POST', body: JSON.stringify({ targets: ['claude'], name: 'ok-name', config: { args: ['x'] } }) });
  assert.equal(badConfig.status, 400);
  const noTargets = await api('/api/mcp/install', { method: 'POST', body: JSON.stringify({ targets: [], name: 'ok-name', config: { command: 'x' } }) });
  assert.equal(noTargets.status, 400);
});

test('remove takes the server out of one instance; a missing name is a 404', async () => {
  const res = await api(`/api/mcp?agent=claude&name=weather`, { method: 'DELETE' });
  assert.equal(res.status, 204);
  const doc = JSON.parse(readFileSync(join(home, '.claude.json'), 'utf8'));
  assert.ok(!('weather' in doc.mcpServers));
  assert.ok('botmux' in doc.mcpServers);

  const toml = await api(`/api/mcp?agent=codex&name=fs`, { method: 'DELETE' });
  assert.equal(toml.status, 204);
  const after = readFileSync(join(home, '.codex', 'config.toml'), 'utf8');
  assert.ok(!after.includes('mcp_servers.fs'), 'the table and its env sub-table both go');
  assert.ok(after.includes('[mcp_servers.github]'));

  const missing = await api(`/api/mcp?agent=claude&name=weather`, { method: 'DELETE' });
  assert.equal(missing.status, 404);
});

test('registry entries: notes and manual servers, saved, listed, and deleted', async () => {
  const put = await api('/api/mcp/entries', { method: 'PUT', body: JSON.stringify({ name: 'github', businessLine: '代码平台', description: 'Issue 与 PR 操作' }) });
  assert.equal(put.status, 200);
  const manual = await api('/api/mcp/entries', { method: 'PUT', body: JSON.stringify({ name: 'weather', businessLine: '数据', description: '查天气', server: { command: 'weather-mcp' } }) });
  assert.equal(manual.status, 200);

  const list = await (await api('/api/mcp/entries')).json();
  assert.deepEqual(list.map((e: { name: string }) => e.name), ['github', 'weather']);
  assert.equal(list[0].server, null, 'an entry without a server only annotates');
  assert.deepEqual(list[1].server, { command: 'weather-mcp' });

  const bad = await api('/api/mcp/entries', { method: 'PUT', body: JSON.stringify({ name: 'a.b', businessLine: '' }) });
  assert.equal(bad.status, 400);

  const gone = await api('/api/mcp/entries/weather', { method: 'DELETE' });
  assert.equal(gone.status, 204);
  const again = await api('/api/mcp/entries/weather', { method: 'DELETE' });
  assert.equal(again.status, 404);
});

test('toml edge cases: quoted names, booleans and numbers, inline tables, fresh files', () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-mcp-toml-'));
  try {
    const path = join(dir, 'config.toml');
    writeFileSync(path, `[mcp_servers."my-server"]\ncommand = 'lit-string'\nargs = [ "a", 'b' ]\nready = true\ncount = 42\nprice = 1.5\n\n[mcp_servers."my-server"]\ncount = 7\n\n[mcp_servers.other]\nenv = { A = "x", B = "y" }\n`);
    let servers = tomlMcpServers(path);
    const mine = servers.find((s) => s.name === 'my-server');
    assert.deepEqual(mine?.config, { command: 'lit-string', args: ['a', 'b'], ready: true, count: 7, price: 1.5 }, 'a repeated table merges, last value wins');
    assert.deepEqual(servers.find((s) => s.name === 'other')?.config, { env: { A: 'x', B: 'y' } });

    installTomlMcpServer(path, 'fresh', { command: 'new', env: { K: 'v' }, numbers: [1, 2], nested: { deep: { leaf: true } } });
    servers = tomlMcpServers(path);
    assert.deepEqual(servers.find((s) => s.name === 'fresh')?.config, { command: 'new', env: { K: 'v' }, numbers: [1, 2], nested: { deep: { leaf: true } } });
    assert.ok(readFileSync(path, 'utf8').includes('[mcp_servers.fresh.nested.deep]'), 'nesting becomes sub-tables');

    assert.equal(removeTomlMcpServer(path, 'my-server'), true);
    assert.ok(!tomlMcpServers(path).some((s) => s.name === 'my-server'));
    assert.ok(readFileSync(path, 'utf8').includes('[mcp_servers.other]'));
    assert.equal(removeTomlMcpServer(path, 'my-server'), false);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
