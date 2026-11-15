import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { AgentRegistry, defaultInstances, type AgentInstance } from '../src/agents.js';
import { HttpError } from '../src/errors.js';

const here = dirname(fileURLToPath(import.meta.url));

const defaults = defaultInstances({
  claudeCommand: ['node', join(here, 'fixtures', 'fake-acp.mjs')],
  piCommand: ['node', join(here, 'fixtures', 'fake-pi.mjs')],
  agyCommand: ['node', join(here, 'fixtures', 'fake-agy.mjs')],
  traexCommand: ['node', join(here, 'fixtures', 'fake-acp.mjs'), 'acp', 'serve'],
  dshCommand: ['node', join(here, 'fixtures', 'fake-acp.mjs'), '--profile', 'acp'],
  doubaoCommand: ['node', join(here, 'fixtures', 'fake-doubao.mjs')],
  codexCommand: ['node', join(here, 'fixtures', 'fake-codex.mjs')],
});

let dir: string;

before(() => {
  dir = mkdtempSync(join(tmpdir(), 'calli-agents-'));
});

after(() => {
  rmSync(dir, { recursive: true, force: true });
});

test('the built-in instances are one adapter each, keyed by instance id', () => {
  const registry = new AgentRegistry(defaults, null);
  assert.deepEqual([...registry.adapters.keys()].sort(), ['agy', 'claude', 'codex', 'doubao', 'dsh', 'pi', 'traex', 'zcode']);
  assert.equal(registry.adapters.get('claude')!.capabilities.approvals, true);
  // The traex default command carries "acp serve"; the adapter keeps the plain CLI for deletions.
  assert.equal(registry.adapters.get('traex')!.label, 'TRAE CLI');
  assert.equal(registry.adapters.get('traex')!.resumeCommand('s1'), 'traex resume s1');
});

test('fromEnv prefers agents.json over the defaults, and instance labels reach the adapter', () => {
  const extra: AgentInstance[] = [...defaults, { id: 'pi2', adapter: 'pi', label: 'pi 备用', env: { FAKE_PI_LABEL: 'pi2' } }];
  const registry = new AgentRegistry(extra, null);
  const adapter = registry.adapters.get('pi2');
  assert.ok(adapter);
  assert.equal(adapter.label, 'pi 备用');
});

test('add persists to agents.json and a later fromEnv reads it back', async () => {
  const dataDir = mkdtempSync(join(tmpdir(), 'calli-agents-env-'));
  const registry = AgentRegistry.fromEnv({ dataDir, env: {}, defaults });
  await registry.add({ id: 'agy2', adapter: 'agy', command: ['/opt/agy2/bin/agy2'] });
  assert.ok(registry.adapters.has('agy2'));

  const reloaded = AgentRegistry.fromEnv({
    dataDir,
    env: {},
    defaults: defaultInstances({ claudeCommand: ['claude-agent-acp'], piCommand: ['pi'], agyCommand: ['agy'], traexCommand: ['traex', 'acp', 'serve'], codexCommand: ['codex', 'app-server'] }),
  });
  // The file holds every instance, including the built-ins it was seeded with.
  assert.deepEqual(
    reloaded.list().map((i) => i.id),
    ['claude', 'pi', 'agy', 'traex', 'codex', 'zcode', 'dsh', 'doubao', 'agy2'],
  );
  rmSync(dataDir, { recursive: true, force: true });
});

test('update renames and remove drops an instance from agents.json', async () => {
  const dataDir = mkdtempSync(join(tmpdir(), 'calli-agents-manage-'));
  const registry = AgentRegistry.fromEnv({ dataDir, env: {}, defaults });
  await registry.add({ id: 'agy2', adapter: 'agy', command: ['/opt/agy2/agy2'] });

  const renamed = await registry.update('agy2', { label: '备用号' });
  assert.equal(renamed.label, '备用号');
  assert.equal(registry.adapters.get('agy2')!.label, '备用号');
  const reloaded = AgentRegistry.fromEnv({ dataDir, env: {}, defaults });
  assert.equal(reloaded.list().find((i) => i.id === 'agy2')?.label, '备用号');

  await registry.remove('agy2');
  assert.equal(registry.adapters.has('agy2'), false);
  const after = AgentRegistry.fromEnv({ dataDir, env: {}, defaults });
  assert.equal(after.list().some((i) => i.id === 'agy2'), false);

  await assert.rejects(registry.update('agy2', { label: 'x' }), HttpError);
  await assert.rejects(registry.remove('agy2'), HttpError);
  rmSync(dataDir, { recursive: true, force: true });
});

test('update persists hiding and restoring without losing instance settings or rebuilding its adapter', async () => {
  const dataDir = mkdtempSync(join(tmpdir(), 'calli-agents-hidden-'));
  try {
    const registry = AgentRegistry.fromEnv({ dataDir, env: {}, defaults });
    const instance = await registry.add({ id: 'pi2', adapter: 'pi', label: 'Second', command: ['node', 'pi.mjs'], env: { PI_CODING_AGENT_DIR: '/tmp/pi-two' }, account: 'second@example.com' });
    const adapter = registry.adapters.get('pi2');
    for (const hidden of [true, false]) {
      assert.deepEqual(await registry.update('pi2', { hidden }), { ...instance, hidden });
      assert.deepEqual(AgentRegistry.fromEnv({ dataDir, env: {}, defaults }).get('pi2'), { ...instance, hidden });
      assert.equal(registry.adapters.get('pi2'), adapter);
      assert.equal(adapter?.label, 'Second');
    }
    await registry.update('pi2', { hidden: true, label: ' Renamed ' });
    assert.equal(registry.get('pi2').hidden, true);
    assert.equal(adapter?.label, 'Renamed');
    await registry.update('pi2', { label: 'Again' });
    assert.equal(registry.get('pi2').hidden, true);
  } finally {
    rmSync(dataDir, { recursive: true, force: true });
  }
});

test('update rejects empty or invalid patches without partially changing the instance', async () => {
  const registry = new AgentRegistry(defaults, null);
  const original = registry.get('pi');
  for (const patch of [{}, { hidden: 'true' }, { hidden: null }, { hidden: 0 }, { label: '' }, { label: '  ', hidden: true }, { label: 'New', hidden: 'false' }]) {
    await assert.rejects(registry.update('pi', patch), (error: unknown) => error instanceof HttpError && error.status === 400);
    assert.deepEqual(registry.get('pi'), original);
  }
  await registry.update('pi', { label: 'x'.repeat(61) });
  assert.equal(registry.get('pi').label?.length, 60);
});

test('the built-in command survives deleting the instance that supplied it', async () => {
  const dataDir = mkdtempSync(join(tmpdir(), 'calli-agents-builtin-'));
  const registry = AgentRegistry.fromEnv({ dataDir, env: {}, defaults });
  await registry.remove('claude');
  await registry.add({ id: 'claude-2', adapter: 'claude', env: { CLAUDE_CONFIG_DIR: '/tmp/claude-two' } });
  const reloaded = AgentRegistry.fromEnv({ dataDir, env: {}, defaults });
  assert.equal(await reloaded.adapters.get('claude-2')!.available(), true);
  rmSync(dataDir, { recursive: true, force: true });
});

test('adapters that can start a login expose a command in their own environment', () => {
  const registry = new AgentRegistry([
    { id: 'claude', adapter: 'claude', command: ['claude'], env: { CLAUDE_CONFIG_DIR: '/tmp/claude-login' } },
    { id: 'codex', adapter: 'codex', command: ['codex', 'app-server'] },
    { id: 'traex', adapter: 'traex', command: ['traex', 'acp', 'serve'] },
    { id: 'zcode', adapter: 'zcode', command: ['zcode'] },
    { id: 'pi', adapter: 'pi', command: ['pi'] },
    { id: 'agy', adapter: 'agy', command: ['agy'] },
  ], null);
  assert.match(registry.adapters.get('claude')!.loginCommand!(), /CLAUDE_CONFIG_DIR='\/tmp\/claude-login'.*auth login/);
  assert.match(registry.adapters.get('codex')!.loginCommand!(), /login --device-auth/);
  assert.match(registry.adapters.get('traex')!.loginCommand!(), /login --sso-device/);
  assert.match(registry.adapters.get('zcode')!.loginCommand!(), /login --no-browser/);
  assert.equal(registry.adapters.get('pi')!.loginCommand, undefined);
  assert.equal(registry.adapters.get('agy')!.loginCommand, undefined);
});

test('add refuses unknown adapters, duplicate ids and junk ids', async () => {
  const registry = new AgentRegistry(defaults, null);
  await assert.rejects(registry.add({ adapter: 'codex' }), HttpError);
  await assert.rejects(registry.add({ id: 'pi', adapter: 'pi' }), HttpError);
  await assert.rejects(registry.add({ id: 'not a name!', adapter: 'pi' }), HttpError);
  // An id can come from the command's basename when none is given.
  const made = await registry.add({ adapter: 'pi', command: ['/opt/bin/claude2'] });
  assert.equal(made.id, 'claude2');
});

test('an instance without a command inherits its adapter default', async () => {
  const registry = new AgentRegistry(defaults, null);
  const second = await registry.add({ id: 'claude2', adapter: 'claude', env: { CLAUDE_CONFIG_DIR: '/tmp/claude-b' } });
  assert.equal(second.command, undefined);
  assert.ok(registry.adapters.has('claude2'));
});

test('discover finds extra CLIs on PATH and leaves lookalikes alone', async () => {
  const bin = join(dir, 'bin');
  mkdirSync(bin);
  const oldPath = process.env.PATH;
  for (const name of ['agy2', 'claude2', 'ping', 'pix', 'pi', 'traecli']) {
    writeFileSync(join(bin, name), '#!/bin/sh\nexit 0\n');
    chmodSync(join(bin, name), 0o755);
  }
  process.env.PATH = `${bin}:${oldPath}`;
  try {
    const found = await new AgentRegistry(defaults, null).discover();
    // 'pi' is already a configured instance; 'ping' and 'pix' only look like pi. Other CLIs may
    // exist elsewhere on this machine's PATH, so only the ones from our bin directory count here.
    const ours = found.filter((f) => f.command.startsWith(bin));
    assert.deepEqual(
      ours.map((f) => f.id),
      ['agy2', 'claude2', 'traecli'],
    );
    assert.equal(ours[0].adapter, 'agy');
    assert.equal(ours[2].adapter, 'traex', "traecli is traex's compatibility name");
  } finally {
    process.env.PATH = oldPath;
  }
});

test('discover lists one row per executable and skips aliases an instance already uses', async () => {
  const bin = join(dir, 'alias-bin');
  mkdirSync(bin);
  const target = join(bin, 'traex');
  writeFileSync(target, '#!/bin/sh\nexit 0\n', { mode: 0o755 });
  // The installer's compatibility names are symlinks to the same executable.
  for (const name of ['coco', 'ta', 'trae-agent', 'trae-cli', 'traecli']) {
    symlinkSync('traex', join(bin, name));
  }
  // Separate programs whose names start with an agent's name are not copies of it.
  for (const name of ['claude-agent-acp', 'traex-bridge']) {
    writeFileSync(join(bin, name), '#!/bin/sh\nexit 0\n', { mode: 0o755 });
  }
  const oldPath = process.env.PATH;
  process.env.PATH = `${bin}:${oldPath}`;
  try {
    // Without a TRAE instance, the aliases collapse to the CLI's own name.
    const fresh = new AgentRegistry(defaults.filter((i) => i.adapter !== 'traex'), null);
    const offered = (await fresh.discover()).filter((f) => f.command.startsWith(bin));
    assert.deepEqual(offered.map((f) => f.id), ['traex']);

    // Once an instance runs that executable, none of its aliases are offered.
    const used = new AgentRegistry([
      ...defaults.filter((i) => i.adapter !== 'traex'),
      { id: 'traex', adapter: 'traex', command: [target, 'acp', 'serve'] },
    ], null);
    assert.deepEqual((await used.discover()).filter((f) => f.command.startsWith(bin)), []);
  } finally {
    process.env.PATH = oldPath;
  }
});

test('discover offers zcode for existing installations and add makes it available', async () => {
  const bin = join(dir, 'zcode-bin');
  mkdirSync(bin);
  for (const name of ['zcode', 'zcode-2', 'zcodec']) {
    writeFileSync(join(bin, name), '#!/bin/sh\nexit 0\n');
    chmodSync(join(bin, name), 0o755);
  }
  const oldPath = process.env.PATH;
  process.env.PATH = `${bin}:${oldPath}`;
  try {
    const registry = new AgentRegistry(defaults.filter((i) => i.adapter !== 'zcode'), null);
    const found = (await registry.discover()).filter((f) => f.command.startsWith(bin));
    assert.deepEqual(found.map((f) => [f.id, f.adapter]), [['zcode', 'zcode'], ['zcode-2', 'zcode']]);
    await registry.add({ id: found[0].id, adapter: found[0].adapter, command: [found[0].command] });
    assert.equal(await registry.adapters.get('zcode')!.available(), true);
    assert.equal((await registry.discover()).some((f) => f.id === 'zcode'), false);
  } finally {
    process.env.PATH = oldPath;
  }
});

test('discover offers Doubao and numbered copies but not similarly named helpers', async () => {
  const bin = join(dir, 'doubao-bin');
  mkdirSync(bin);
  for (const name of ['doubao', 'doubao-2', 'doubao2', 'doubao-helper', 'doubaox']) {
    writeFileSync(join(bin, name), '#!/bin/sh\nexit 0\n', { mode: 0o755 });
  }
  const oldPath = process.env.PATH;
  process.env.PATH = `${bin}:${oldPath}`;
  try {
    const registry = new AgentRegistry(defaults.filter(instance => instance.adapter !== 'doubao'), null);
    const found = (await registry.discover()).filter(item => item.command.startsWith(bin));
    assert.deepEqual(found.map(item => [item.id, item.adapter]), [['doubao', 'doubao'], ['doubao-2', 'doubao'], ['doubao2', 'doubao']]);
    assert.equal(found[0].label, 'Doubao CLI');
    await registry.add({ id: found[0].id, adapter: found[0].adapter, command: [found[0].command] });
    assert.equal(await registry.adapters.get('doubao')!.available(), true);
    assert.equal((await registry.discover()).some(item => item.id === 'doubao'), false);
  } finally { process.env.PATH = oldPath; }
});

// The stub claude the account-discovery tests point the adapter at: it answers `auth status`
// from the config directory it was given, so each directory below stands for one account.
function fakeClaudeHome(): string {
  const home = mkdtempSync(join(tmpdir(), 'calli-accounts-home-'));
  for (const name of ['.claude', '.claude-2', '.claude-3', '.claude-out', '.claude-broken']) {
    mkdirSync(join(home, name));
  }
  return home;
}

function fakeClaudeBin(home: string): string {
  const bin = join(home, 'bin');
  mkdirSync(bin);
  const script = join(bin, 'claude');
  writeFileSync(
    script,
    [
      '#!/bin/sh',
      'name=$(basename "$CLAUDE_CONFIG_DIR")',
      'case "$name" in',
      "  .claude-out) echo '{\"loggedIn\":false}' ;;",
      '  .claude-broken) echo "not json" ;;',
      "  *) printf '{\"loggedIn\":true,\"email\":\"%s@%s\"}\n' \"$name\" \"$(echo \"$name\" | tr -d .)\" ;;",
      'esac',
    ].join('\n'),
  );
  chmodSync(script, 0o755);
  return script;
}

test('account discovery offers signed-in accounts no instance uses', async () => {
  const home = fakeClaudeHome();
  const oldBin = process.env.CALLI_CLAUDE_BIN;
  const oldHome = process.env.HOME;
  process.env.CALLI_CLAUDE_BIN = fakeClaudeBin(home);
  process.env.HOME = home;
  try {
    // The built-in claude has no env of its own, so it claims the default account (~/.claude);
    // the logged-out and unparseable directories answer nothing usable.
    const found = await new AgentRegistry(defaults, null).discoverAccounts();
    assert.deepEqual(found, [
      {
        adapter: 'claude',
        email: '.claude-2@claude-2',
        env: { CLAUDE_CONFIG_DIR: join(home, '.claude-2') },
        id: 'claude-2',
        label: 'Claude Code 2',
      },
      {
        adapter: 'claude',
        email: '.claude-3@claude-3',
        env: { CLAUDE_CONFIG_DIR: join(home, '.claude-3') },
        id: 'claude-3',
        label: 'Claude Code 3',
      },
    ]);
  } finally {
    if (oldBin === undefined) delete process.env.CALLI_CLAUDE_BIN;
    else process.env.CALLI_CLAUDE_BIN = oldBin;
    if (oldHome === undefined) delete process.env.HOME;
    else process.env.HOME = oldHome;
  }
});

test('account discovery still sees an adapter after every instance of it is removed', async () => {
  const home = fakeClaudeHome();
  const oldBin = process.env.CALLI_CLAUDE_BIN;
  const oldHome = process.env.HOME;
  process.env.CALLI_CLAUDE_BIN = fakeClaudeBin(home);
  process.env.HOME = home;
  try {
    const registry = new AgentRegistry(defaults.filter((i) => i.adapter !== 'claude'), null, defaults);
    const found = await registry.discoverAccounts();
    assert.deepEqual(found.map((a) => a.id).sort(), ['claude', 'claude-2', 'claude-3']);
    assert.equal(found.find((a) => a.id === 'claude')?.label, 'Claude Code');
    assert.equal(found.find((a) => a.id === 'claude-3')?.label, 'Claude Code 3');
  } finally {
    if (oldBin === undefined) delete process.env.CALLI_CLAUDE_BIN;
    else process.env.CALLI_CLAUDE_BIN = oldBin;
    if (oldHome === undefined) delete process.env.HOME;
    else process.env.HOME = oldHome;
  }
});

test('account discovery skips accounts an instance already links, and ids keep counting', async () => {
  const home = fakeClaudeHome();
  const oldBin = process.env.CALLI_CLAUDE_BIN;
  const oldHome = process.env.HOME;
  process.env.CALLI_CLAUDE_BIN = fakeClaudeBin(home);
  process.env.HOME = home;
  try {
    const instances: AgentInstance[] = [
      ...defaults,
      { id: 'claude-2', adapter: 'claude', env: { CLAUDE_CONFIG_DIR: join(home, '.claude-2') } },
    ];
    const found = await new AgentRegistry(instances, null).discoverAccounts();
    // .claude is claimed by the built-in, .claude-2 by the linked instance; claude-2 is taken,
    // so the suggestion for .claude-3 moves on to claude-3 / "Claude Code 3".
    assert.deepEqual(found, [
      {
        adapter: 'claude',
        email: '.claude-3@claude-3',
        env: { CLAUDE_CONFIG_DIR: join(home, '.claude-3') },
        id: 'claude-3',
        label: 'Claude Code 3',
      },
    ]);
  } finally {
    if (oldBin === undefined) delete process.env.CALLI_CLAUDE_BIN;
    else process.env.CALLI_CLAUDE_BIN = oldBin;
    if (oldHome === undefined) delete process.env.HOME;
    else process.env.HOME = oldHome;
  }
});

test('supported lists all built-in adapters with labels and install guides', () => {
  const registry = new AgentRegistry(defaults, null);
  const supported = registry.supported();
  assert.equal(supported.length, 8);
  const adapters = supported.map((s) => s.adapter);
  assert.deepEqual(adapters, ['claude', 'codex', 'pi', 'agy', 'traex', 'zcode', 'dsh', 'doubao']);
  for (const item of supported) {
    assert.ok(item.label, `adapter ${item.adapter} has a label`);
    assert.ok(item.icon, `adapter ${item.adapter} has an icon`);
  }
  assert.equal(supported.find((s) => s.adapter === 'claude')?.installGuide, 'npm install -g @anthropic-ai/claude-code');
  assert.equal(supported.find((s) => s.adapter === 'codex')?.installGuide, 'npm install -g @openai/codex');
  assert.equal(supported.find((s) => s.adapter === 'pi')?.installGuide, 'npm install -g @earendil-works/pi-coding-agent');
  assert.equal(supported.find((s) => s.adapter === 'agy')?.installGuide, undefined);
  assert.equal(supported.find((s) => s.adapter === 'traex')?.installGuide, undefined);
  assert.equal(supported.find((s) => s.adapter === 'zcode')?.installGuide, undefined);
  assert.equal(supported.find((s) => s.adapter === 'dsh')?.installGuide, 'npm install -g @deepseek-ai/dsh');
  assert.equal(supported.find((s) => s.adapter === 'doubao')?.installGuide, 'npm install -g @bytedance/doubao-cli');
});

test('AgentRegistry.fromEnv supports an explicitly empty agents array', () => {
  const registry = AgentRegistry.fromEnv({
    dataDir: dir,
    env: { CALLI_AGENTS_JSON: JSON.stringify({ agents: [] }) },
    defaults,
  });
  assert.deepEqual(registry.list(), []);
});

/** A machine with only what the test put there: PATH holds one bin directory plus the system's
 *  own (shell stubs need coreutils; agent CLIs live elsewhere, so discovery sees only the test's),
 *  HOME is empty, and the claude CLI is a stub that does not exist, so accounts find nothing. */
function isolatedMachine(): { dataDir: string; bin: string; restore: () => void } {
  const dataDir = mkdtempSync(join(tmpdir(), 'calli-autojoin-'));
  const home = mkdtempSync(join(tmpdir(), 'calli-autojoin-home-'));
  const bin = join(dataDir, 'bin');
  mkdirSync(bin);
  const oldPath = process.env.PATH;
  const oldHome = process.env.HOME;
  const oldBin = process.env.CALLI_CLAUDE_BIN;
  process.env.PATH = `${bin}:/usr/bin:/bin`;
  process.env.HOME = home;
  process.env.CALLI_CLAUDE_BIN = join(home, 'no-such-claude');
  return {
    dataDir,
    bin,
    restore: () => {
      process.env.PATH = oldPath;
      if (oldHome === undefined) delete process.env.HOME;
      else process.env.HOME = oldHome;
      if (oldBin === undefined) delete process.env.CALLI_CLAUDE_BIN;
      else process.env.CALLI_CLAUDE_BIN = oldBin;
      rmSync(dataDir, { recursive: true, force: true });
      rmSync(home, { recursive: true, force: true });
    },
  };
}

test('auto-join adds discovered CLIs, and repeated scans add nothing new', async () => {
  const machine = isolatedMachine();
  try {
    writeFileSync(join(machine.bin, 'agy2'), '#!/bin/sh\nexit 0\n', { mode: 0o755 });
    writeFileSync(join(machine.bin, 'ping'), '#!/bin/sh\nexit 0\n', { mode: 0o755 });
    const registry = AgentRegistry.fromEnv({ dataDir: machine.dataDir, env: {}, defaults });
    const added = await registry.autoJoin();
    // 'ping' only looks like an agent; 'agy2' joins on its own, as the page's one-click entry does.
    assert.deepEqual(added.map((i) => i.id), ['agy2']);
    assert.deepEqual(added[0].command, [join(machine.bin, 'agy2')]);
    assert.equal(added[0].label, 'agy2');
    assert.deepEqual(await registry.autoJoin(), []);
    // A restarted daemon reads the same agents.json and still finds nothing new.
    const reloaded = AgentRegistry.fromEnv({ dataDir: machine.dataDir, env: {}, defaults });
    assert.deepEqual(await reloaded.autoJoin(), []);
    assert.ok(reloaded.adapters.has('agy2'));
  } finally {
    machine.restore();
  }
});

test('a removed CLI is not auto-joined back, not even after a restart', async () => {
  const machine = isolatedMachine();
  try {
    writeFileSync(join(machine.bin, 'agy2'), '#!/bin/sh\nexit 0\n', { mode: 0o755 });
    const registry = AgentRegistry.fromEnv({ dataDir: machine.dataDir, env: {}, defaults });
    await registry.autoJoin();
    await registry.remove('agy2');
    // Discovery would offer it again — no instance runs it now — but auto-join leaves it out.
    assert.ok((await registry.discover()).some((f) => f.id === 'agy2'));
    assert.deepEqual(await registry.autoJoin(), []);
    // The memory is `removed-agents.json`, so a restart keeps it out too.
    const reloaded = AgentRegistry.fromEnv({ dataDir: machine.dataDir, env: {}, defaults });
    assert.deepEqual(await reloaded.autoJoin(), []);
  } finally {
    machine.restore();
  }
});

test('auto-join links signed-in accounts, and a removed account stays out', async () => {
  const machine = isolatedMachine();
  const home = fakeClaudeHome();
  const oldBin = process.env.CALLI_CLAUDE_BIN;
  const oldHome = process.env.HOME;
  process.env.CALLI_CLAUDE_BIN = fakeClaudeBin(home);
  process.env.HOME = home;
  try {
    const registry = AgentRegistry.fromEnv({ dataDir: machine.dataDir, env: {}, defaults });
    // The built-in claude claims the default account (~/.claude); the other two sign-ins join.
    const added = await registry.autoJoin();
    assert.deepEqual(added.map((i) => i.id), ['claude-2', 'claude-3']);
    assert.equal(added[0].account, '.claude-2@claude-2');
    assert.deepEqual(added[0].env, { CLAUDE_CONFIG_DIR: join(home, '.claude-2') });

    // Removing one keeps it out: its config directory is remembered, the account stays signed in.
    await registry.remove('claude-2');
    assert.deepEqual(await registry.autoJoin(), []);
    assert.equal(registry.adapters.has('claude-2'), false);
    assert.equal(registry.adapters.has('claude-3'), true);
    const reloaded = AgentRegistry.fromEnv({ dataDir: machine.dataDir, env: {}, defaults });
    assert.deepEqual(await reloaded.autoJoin(), []);
  } finally {
    if (oldBin === undefined) delete process.env.CALLI_CLAUDE_BIN;
    else process.env.CALLI_CLAUDE_BIN = oldBin;
    if (oldHome === undefined) delete process.env.HOME;
    else process.env.HOME = oldHome;
    rmSync(home, { recursive: true, force: true });
    machine.restore();
  }
});

test('an inline CALLI_AGENTS_JSON hub joins nothing, and CALLI_AUTO_JOIN=off turns it off too', async () => {
  const machine = isolatedMachine();
  // Each registry gets its own data dir: one hub's agents.json must not feed the next.
  const dirs = [mkdtempSync(join(tmpdir(), 'calli-autojoin-')), mkdtempSync(join(tmpdir(), 'calli-autojoin-'))];
  try {
    writeFileSync(join(machine.bin, 'agy2'), '#!/bin/sh\nexit 0\n', { mode: 0o755 });
    // The control: a hub of its own does pick the CLI up through the timer, so the waits below
    // are long enough to have seen a joining had one started.
    const plain = AgentRegistry.fromEnv({ dataDir: machine.dataDir, env: {}, defaults });
    plain.startAutoJoin(60_000, {});
    await new Promise((r) => setTimeout(r, 300));
    assert.ok(plain.list().some((i) => i.id === 'agy2'));
    plain.dispose();

    // An inline hub owns its agent list: the machine's real CLIs and accounts never join it.
    const inline = AgentRegistry.fromEnv({
      dataDir: dirs[0],
      env: { CALLI_AGENTS_JSON: JSON.stringify({ agents: defaults }) },
      defaults,
    });
    inline.startAutoJoin(60_000, {});
    await new Promise((r) => setTimeout(r, 300));
    assert.deepEqual(inline.list().map((i) => i.id), defaults.map((i) => i.id));
    assert.deepEqual(await inline.autoJoin(), []);
    inline.dispose();

    // The switch turns the joining off for a hub of its own too.
    const off = AgentRegistry.fromEnv({ dataDir: dirs[1], env: {}, defaults });
    off.startAutoJoin(60_000, { CALLI_AUTO_JOIN: 'off' });
    await new Promise((r) => setTimeout(r, 300));
    assert.ok(!off.list().some((i) => i.id === 'agy2'));
    off.dispose();
  } finally {
    for (const extra of dirs) rmSync(extra, { recursive: true, force: true });
    machine.restore();
  }
});

test('a removed account instance is remembered by its env, never by the binary its adapter runs', async () => {
  const machine = isolatedMachine();
  const home = fakeClaudeHome();
  const oldBin = process.env.CALLI_CLAUDE_BIN;
  const oldHome = process.env.HOME;
  // One claude binary, on PATH, both probing accounts and named as the adapter's command.
  const claudeBin = join(machine.bin, 'claude');
  writeFileSync(claudeBin, [
    '#!/bin/sh',
    'name=$(basename "$CLAUDE_CONFIG_DIR")',
    'case "$name" in',
    "  .claude-out) echo '{\"loggedIn\":false}' ;;",
    '  .claude-broken) echo "not json" ;;',
    "  *) printf '{\"loggedIn\":true,\"email\":\"%s@%s\"}\\n' \"$name\" \"$(echo \"$name\" | tr -d .)\" ;;",
    'esac',
  ].join('\n'), { mode: 0o755 });
  process.env.CALLI_CLAUDE_BIN = claudeBin;
  process.env.HOME = home;
  try {
    // A hub whose claude is one linked account, not the default instance.
    const localDefaults = defaults.map((d) => (d.id === 'claude' ? { ...d, command: [claudeBin] } : d));
    writeFileSync(join(machine.dataDir, 'agents.json'), JSON.stringify({ agents: [
      { id: 'claude-2', adapter: 'claude', env: { CLAUDE_CONFIG_DIR: join(home, '.claude-2') }, account: '.claude-2@claude-2' },
    ] }));
    const registry = AgentRegistry.fromEnv({ dataDir: machine.dataDir, env: {}, defaults: localDefaults });

    await registry.remove('claude-2');
    // The memory holds the account's env and nothing else: the shared binary is not blacklisted.
    const memory = JSON.parse(readFileSync(join(machine.dataDir, 'removed-agents.json'), 'utf8'));
    assert.deepEqual(memory, { paths: [], envs: [`CLAUDE_CONFIG_DIR=${join(home, '.claude-2')}`] });

    // The proof: with no instance running it, the very same binary joins back as a plain CLI copy.
    const added = await registry.autoJoin();
    assert.ok(added.some((i) => i.adapter === 'claude' && i.command?.[0] === claudeBin));
    // And the removed account itself still stays out.
    assert.ok(!added.some((i) => i.env?.CLAUDE_CONFIG_DIR === join(home, '.claude-2')));
  } finally {
    if (oldBin === undefined) delete process.env.CALLI_CLAUDE_BIN;
    else process.env.CALLI_CLAUDE_BIN = oldBin;
    if (oldHome === undefined) delete process.env.HOME;
    else process.env.HOME = oldHome;
    rmSync(home, { recursive: true, force: true });
    machine.restore();
  }
});
