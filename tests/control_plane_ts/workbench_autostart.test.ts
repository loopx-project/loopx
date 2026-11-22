import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync, existsSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { WorkbenchAutostart, servicePlan, type ServiceRun } from '../../loopx/control_plane/runtime/workbench_autostart.ts';

function fixture(platform: 'darwin' | 'linux') {
  const root = mkdtempSync(join(tmpdir(), 'workbench-native-test-'));
  const state = join(root, 'state'); mkdirSync(state);
  const options = { platform, home: root, configHome: join(root, 'config'), uid: 501,
    path: '/usr/bin:/bin', node: '/opt/Node & tools/node', runner: '/opt/Loop X/service.ts' };
  const plan = servicePlan(state, options);
  const commands: string[][] = [];
  let loaded = false, enabled = false, wrongArguments = false, unavailable = false;
  const run: ServiceRun = async (command, args) => {
    commands.push([command, ...args]);
    if (unavailable) return { code: 1, stdout: '' };
    if (command === 'systemctl') {
      if (args.includes('--property=UnitPath')) return { code: 0, stdout: join(plan.file, '..') };
      if (args.includes('--property=Version')) return { code: 0, stdout: '252\n' };
      if (args.includes('--property=LoadState')) return { code: 0, stdout: loaded ? 'loaded\n' : 'not-found\n' };
      if (args.includes('daemon-reload')) loaded = existsSync(plan.file);
      if (args.includes('enable')) enabled = true;
      if (args.includes('disable')) enabled = false;
      return { code: 0, stdout: `LoadState=loaded\nActiveState=inactive\nUnitFileState=${enabled ? 'enabled' : 'disabled'}\nMainPID=0\nFragmentPath=${plan.file}\n` };
    }
    if (args[0] === 'print' && args[1] === 'gui/501') return { code: 0, stdout: '{}' };
    if (args[0] === 'print-disabled') return { code: 0, stdout: `disabled services = {\n "${plan.label}" => ${!enabled}\n}` };
    if (args[0] === 'print') return { code: loaded ? 0 : 113, stdout: `arguments = {\n${(wrongArguments ? ['/other/node'] : plan.args).join('\n')}\n}\npid = 4321\n` };
    if (args[0] === 'enable') enabled = true;
    if (args[0] === 'bootstrap') loaded = true;
    if (args[0] === 'bootout') loaded = false;
    return { code: 0, stdout: '' };
  };
  const service = new WorkbenchAutostart(state, { ...options, run });
  return { root, state, plan, service, commands,
    foreign: () => { loaded = true; wrongArguments = true; }, fail: () => { unavailable = true; },
    cleanup: () => rmSync(root, { recursive: true, force: true }) };
}

for (const platform of ['darwin', 'linux'] as const) {
  test(`${platform}: opt-in registration, readback, stop and removal`, async () => {
    const f = fixture(platform);
    try {
      assert.deepEqual(await f.service.inspect(), { configured: false, enabled: false, loaded: false, running: false });
      assert.equal(f.commands.length, 0);
      await f.service.install('127.0.0.1');
      assert.equal(statSync(f.plan.file).mode & 0o777, 0o600);
      assert.equal(readFileSync(f.plan.file, 'utf8'), f.plan.content);
      await f.service.start(true);
      const first = await f.service.inspect();
      assert.equal(first.error, undefined); assert.equal(first.enabled, true);
      const before = f.commands.length;
      await f.service.start(true);
      assert.equal(f.commands.slice(before).some(c => c.includes('bootout')), false);
      assert.equal(f.commands.slice(before).some(c => c.includes('bootstrap')), false);
      await f.service.stop(false);
      assert.equal((await f.service.inspect()).enabled, true);
      assert.equal(existsSync(f.plan.file), true);
      await f.service.start(false);
      await f.service.stop(true);
      assert.equal(existsSync(f.plan.file), false);
      assert.equal(f.service.registered(), false);
      assert.equal(existsSync(f.state), true);
    } finally { f.cleanup(); }
  });
  test(`${platform}: refuses unowned files, jobs, modified registration and unavailable manager`, async () => {
    const f = fixture(platform);
    try {
      mkdirSync(join(f.plan.file, '..'), { recursive: true });
      writeFileSync(f.plan.file, 'owned by somebody else');
      await assert.rejects(f.service.install('127.0.0.1'), /unowned/);
      assert.equal(readFileSync(f.plan.file, 'utf8'), 'owned by somebody else');
      rmSync(f.plan.file);
      f.foreign();
      await assert.rejects(f.service.install('127.0.0.1'), /already exists/);
      assert.equal(f.service.registered(), false);
    } finally { f.cleanup(); }
    const second = fixture(platform);
    try {
      await second.service.install('127.0.0.1');
      writeFileSync(second.plan.file, 'changed externally');
      const observed = await second.service.inspect();
      assert.equal(observed.enabled, null); assert.match(observed.error!, /changed outside/);
      await assert.rejects(second.service.stop(true), /changed outside/);
      assert.equal(readFileSync(second.plan.file, 'utf8'), 'changed externally');
    } finally { second.cleanup(); }
    const third = fixture(platform);
    try {
      third.fail();
      await assert.rejects(third.service.install('127.0.0.1'), /failed/);
      assert.equal(existsSync(third.plan.file), false);
      assert.equal(third.service.registered(), false);
    } finally { third.cleanup(); }
  });
}
test('launchd verifies loaded arguments and fails closed on unavailable readback', async () => {
  const f = fixture('darwin');
  try {
    await f.service.install('127.0.0.1'); await f.service.start(true);
    f.foreign();
    const prior = f.commands.length;
    await assert.rejects(f.service.stop(true), /arguments differ/);
    assert.equal(f.commands.slice(prior).some(c => c.includes('bootout')), false);
    f.fail(); assert.equal((await f.service.inspect()).enabled, null);
  } finally { f.cleanup(); }
});
test('native descriptors preserve argument boundaries, restart failures and reject line injection', () => {
  const mac = servicePlan('/tmp/state & "quoted"', { platform: 'darwin', home: '/tmp/user', path: '/bin' });
  assert.match(mac.content, /state &amp; &quot;quoted&quot;/);
  assert.match(mac.content, /<key>RunAtLoad<\/key><true\/>/);
  assert.match(mac.content, /<key>SuccessfulExit<\/key><false\/>/);
  const linux = servicePlan('/tmp/state %name $HOME', { platform: 'linux', home: '/tmp/user', configHome: '/tmp/config', path: '/bin' });
  assert.match(linux.content, /state %%name \$\$HOME/);
  assert.match(linux.content, /Restart=on-failure/);
  assert.doesNotMatch(linux.content, /TOKEN|API_KEY/);
  assert.throws(() => servicePlan('/tmp/state\nExecStart=bad'), /Invalid/);
  assert.throws(() => servicePlan('/tmp/state', { platform: 'win32' }), /requires/);
});
