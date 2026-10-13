import assert from 'node:assert/strict';
import {spawn, spawnSync} from 'node:child_process';
import {mkdtemp, mkdir, writeFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve} from 'node:path';
import {createRequire} from 'node:module';
import {createServer} from 'node:net';
import {createInterface} from 'node:readline';
import {testPythonCommand} from '../../../../scripts/test-python.mjs';

const repo = resolve(import.meta.dirname, '../../../..');
const {chromium} = createRequire(resolve(repo, 'apps/presentation/dashboard/package.json'))('playwright');
const output = resolve(repo, '.local/goal-amendment-browser');
await mkdir(output, {recursive: true});
const root = await mkdtemp(resolve(tmpdir(), 'loopx-amendment-browser-'));
const reservation = createServer();
await new Promise(accept => reservation.listen(0, '127.0.0.1', accept));
const port = reservation.address().port;
await new Promise(accept => reservation.close(accept));
// Real project-register, fresh-authority owner, packaged assets and HTTP server.
// No status or provider fixture replaces a production response. Generic runtime
// reads remain unavailable for this explicit source-session profile.
const backend = testPythonCommand(String.raw`
import contextlib, io, json, signal, sys, tempfile
from pathlib import Path
from loopx.cli import main
from loopx.chat_server import serve_chat
from loopx.control_plane.effect_runtime import restart_effect_runtime
root = Path(sys.argv[1]); tempfile.tempdir = str(root)
registry = root / 'project/.loopx/registry.json'; runtime = root / 'runtime'
with contextlib.redirect_stdout(io.StringIO()) as output:
    code = main(['--registry', str(registry), '--runtime-root', str(runtime), '--format', 'json', 'project', 'register',
        '--project-id', 'governed-work', '--project-kind', 'work', '--knowledge-root', str(root / 'project'),
        '--goal-id', 'amendment-goal', '--objective', 'Verify the public contract', '--non-goal', 'No remote changes',
        '--acceptance', 'Independent validation passes', '--next-effect', 'Inspect the current contract',
        '--stop-condition', 'Stop when the Goal is inactive', '--goal-instance-profile', 'source_session_v1',
        '--operation-id', 'create-goal', '--registered-agent', 'agent-a', '--registered-agent', 'agent-b', '--canonical-storage', 'sqlite'])
assert code == 0, output.getvalue()
created = json.loads(output.getvalue())
assert created['storage_initialization']['authority_initialized'] is True
(root / 'registration.json').write_text(json.dumps(created))
# Synthetic typed observations, not model runs. The real replan reader derives
# the obligation; the fixture never supplies an arbitrary accepted obligation id.
runs = runtime / 'goals/amendment-goal/runs/index.jsonl'
runs.parent.mkdir(parents=True, exist_ok=True)
rows = [{'generated_at': f'2026-09-01T00:0{i}:00+00:00', 'goal_id': 'amendment-goal',
    'goal_ref': created['goal_ref'], 'agent_id': 'agent-a', 'classification': 'bounded_replan_progress',
    'turn_instance_id': f'synthetic-{i}', 'progress_observation': {'schema_version': 'typed_progress_observation_v0',
        'result_class': 'blocked', 'surface_id': 'contract', 'hypothesis_id': 'synthetic-gap',
        'probe_kind': 'check', 'evidence_ids': ['synthetic-evidence']}} for i in range(2)]
runs.write_text(''.join(json.dumps(row) + '\n' for row in rows))
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
try:
    serve_chat(registry_path=registry, runtime_root_override=runtime, scan_roots=[root / 'project'], port=int(sys.argv[2]),
        goal_id='amendment-goal', codex_bin='missing-synthetic-codex', claude_bin='missing-synthetic-claude', kiro_cli_bin='missing-synthetic-kiro')
finally:
    restart_effect_runtime()
`);
const child = spawn(backend.command, [...backend.args, root, String(port)], {cwd: root, env: {...process.env, LOOPX_USAGE_PING: '0', TMPDIR: root,
  NODE_OPTIONS: `${process.env.NODE_OPTIONS ?? ''} --experimental-sqlite`}, stdio: ['ignore', 'pipe', 'pipe']});
const lines = createInterface({input: child.stdout});
let serverLog = '';
child.stderr.on('data', chunk => {serverLog += String(chunk);});
let browser, page;
try {
  await new Promise((accept, reject) => {
    const timer = setTimeout(() => reject(Error('Isolated server startup timed out')), 120_000);
    child.once('exit', code => {clearTimeout(timer);reject(Error(`Server exited ${code}: ${serverLog}`));});
    lines.on('line', line => {if (line.startsWith('Serving LoopX Chat')) {clearTimeout(timer);accept();}});
  });
  browser = await chromium.launch({headless: true});
  page = await browser.newPage({viewport: {width: 1512, height: 1600}});
  await page.addInitScript(() => localStorage.setItem('loopx-pw-locale', 'zh-CN'));
  const exceptions = []; page.on('pageerror', error => exceptions.push(error.message));
  const applies = []; let drop = true;
  await page.route('**/api/chat/goal-amendment', async route => {
    const request = route.request();
    if (request.method() !== 'POST' || !request.postDataJSON().execute) return route.continue();
    applies.push(request.postDataJSON());
    const response = await route.fetch();
    assert.equal((await response.json()).ok, true);
    if (drop) {drop = false;await route.abort();}
    else await route.fulfill({response});
  });
  const url = `http://127.0.0.1:${port}/chat/`;
  await page.goto(url);
  async function enter() {
    await page.locator('.personal-goal-link', {hasText: 'amendment-goal'}).click();
    await page.getByRole('button', {name: 'Goal 设置', exact: true}).click();
    await page.getByRole('button', {name: '任务所有权', exact: true}).click();
    await page.getByText('目标约束与受治理变更', {exact: true}).click();
    await page.locator('.personal-cadence-readback strong').filter({hasText: 'Verify the public contract'}).waitFor();
  }
  await enter();
  const panel = page.locator('details').filter({has: page.locator('summary', {hasText: '目标约束与受治理变更'})});
  await panel.getByRole('button', {name: '检查本次授权', exact: true}).click();
  await panel.getByRole('alert').waitFor(); // Required template/terminal fields cannot be omitted.
  for (const [label, value] of [
    ['允许新增的工作', 'Verify the public contract'], ['动作类型', 'validate'], ['稳定目标标识', 'contract-check'],
    ['写入范围，每行一项', 'tests/**'], ['所需能力，每行一项', 'filesystem_write'], ['终止条件，每行一项', 'Accepted result returned'],
  ]) await panel.getByLabel(label, {exact: true}).fill(value);
  await panel.getByRole('button', {name: '检查本次授权', exact: true}).click();
  await panel.getByRole('button', {name: '确认固定目标与授权', exact: true}).waitFor();
  assert.equal(applies.length, 0);
  await panel.screenshot({path: resolve(output, 'desktop-preview.png')});
  await page.reload();await enter();
  assert.equal(await panel.getByLabel('允许新增的工作', {exact: true}).inputValue(), 'Verify the public contract');
  await panel.getByRole('button', {name: '确认固定目标与授权', exact: true}).click();
  await panel.getByRole('button', {name: '查阅原操作回执', exact: true}).waitFor();
  assert.equal(applies.length, 1);
  await page.waitForFunction(() => [...document.querySelectorAll('button')].find(button => button.textContent === '查阅原操作回执')?.disabled === false);
  await panel.screenshot({path: resolve(output, 'unknown-response.png')});
  await page.reload();await enter();
  await panel.getByRole('button', {name: '重试原操作', exact: true}).click();
  await panel.getByText('已固定目标约束 · 版本 1', {exact: true}).waitFor();
  await panel.getByRole('button', {name: '查阅原操作回执', exact: true}).waitFor({state: 'detached'});
  assert.equal(applies.length, 2);assert.deepEqual(applies[0], applies[1]);
  await panel.getByText('agent-a · 尚未确认当前版本', {exact: true}).waitFor();
  function advancePeers(phase) {
    const command = testPythonCommand(String.raw`
import contextlib, io, json, sys
from pathlib import Path
from loopx.cli import main
root = Path(sys.argv[1]); phase = sys.argv[2]
ref = json.loads((root / 'registration.json').read_text())['goal_ref']
def call(operation, parameters=None, agent='agent-a', operation_id=None):
    args = ['--registry', str(root / 'project/.loopx/registry.json'), '--format', 'json',
        'goal-amendment-proposal', '--governed', operation, '--goal-id', ref['goal_id'],
        '--goal-instance-id', ref['goal_instance_id'], '--agent-id', agent]
    if parameters is not None:
        path = root / 'request.json'; path.write_text(json.dumps(parameters)); args += ['--request-json', str(path)]
    if operation_id: args += ['--operation-id', operation_id, '--execute']
    with contextlib.redirect_stdout(io.StringIO()) as output: code = main(args)
    assert code == 0, output.getvalue()
    return json.loads(output.getvalue())
def basis(view):
    return {'base_goal_revision': view['intent']['goal_revision'], 'base_intent_digest': view['intent']['intent_digest'], 'base_work_digest': view['work_digest']}
view = call('inspect')
if phase == 'commit':
    call('adopt', basis(view), operation_id='agent-initial-adoption')
    proposal = call('propose', {**basis(view), 'proposal_id': 'contract-gap', 'template_id': 'owner-work',
        'evidence_refs': ['evidence:synthetic-gap'], 'replan_obligation_id': view['replan_observations'][0]['obligation_id']}, operation_id='agent-proposal')
    result = call('commit', {'proposal_id': 'contract-gap', 'proposal_digest': proposal['proposal']['digest']}, operation_id='agent-commit')
    assert result['replan_settlement'] == 'pending'
else:
    view = call('adopt', basis(view), operation_id='agent-current-adoption')['current_projection']
    result = call('claim', {**basis(view), 'todo_id': view['intent']['created_todo_ids'][0],
        'expected_provider_revision': view['provider_revision'], 'lease_request': {
            'idempotency_key': 'new-work', 'expected_version': None, 'ttl_seconds': 300}}, operation_id='agent-claim')
    call('adopt', basis(result['current_projection']), agent='agent-b', operation_id='peer-current-adoption')
print(json.dumps(call('inspect')))
`);
    const result = spawnSync(command.command, [...command.args, root, phase], {cwd: root, encoding: 'utf8', timeout: 60_000,
      env: {...process.env, LOOPX_USAGE_PING: '0', TMPDIR: root, NODE_OPTIONS: `${process.env.NODE_OPTIONS ?? ''} --experimental-sqlite`}});
    assert.equal(result.status, 0, result.stdout + result.stderr);
    return JSON.parse(result.stdout);
  }
  const committed = advancePeers('commit');
  assert.equal(committed.intent.goal_revision, 2);
  await panel.getByRole('button', {name: '读取当前结果', exact: true}).click();
  await panel.getByText('已创建工作 1 项；提案 1 项。', {exact: true}).waitFor();
  await panel.getByText('agent-a · 仍待后续工作采纳与验证', {exact: true}).waitFor();
  const adopted = advancePeers('claim');
  assert.equal(adopted.replan_observations[0].state, 'settled');
  await panel.getByRole('button', {name: '读取当前结果', exact: true}).click();
  await panel.getByText('agent-b · 当前版本确认已记录', {exact: true}).waitFor();
  await panel.getByText('agent-a · 对应 replan 已由原有规则验证', {exact: true}).waitFor();
  await page.setViewportSize({width: 390, height: 1100});
  await panel.getByRole('button', {name: '读取当前结果', exact: true}).focus();await page.keyboard.press('Enter');
  await page.waitForFunction(() => [...document.querySelectorAll('button')].find(button => button.textContent === '读取当前结果')?.disabled === false);
  await panel.getByText('已固定目标约束 · 版本 2', {exact: true}).waitFor();
  await panel.screenshot({path: resolve(output, 'mobile-readback.png')});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  const view = await fetch(`http://127.0.0.1:${port}/api/chat/goal-amendment?goal_id=amendment-goal`).then(r => r.json());
  assert.equal(view.intent.goal_revision, 2);assert.equal(view.execution_authority_granted, false);
  assert.equal(view.intent.created_todo_ids.length, 1);
  const query = new URLSearchParams({goal_id: applies[0].goal_id, goal_instance_id: applies[0].goal_instance_id, operation_id: applies[0].operation_id});
  const receipt = await fetch(`http://127.0.0.1:${port}/api/chat/goal-amendment?${query}`).then(r => r.json());
  assert.equal(receipt.status, 'found');assert.equal(receipt.historical_only, true);
  assert.equal(receipt.receipts[0].result.intent_digest, applies[0].preview_digest);
  await page.close();
  page = await browser.newPage({viewport: {width: 1512, height: 1600}});
  await page.addInitScript(() => localStorage.setItem('loopx-pw-locale', 'en'));
  await page.goto(url);
  await page.locator('.personal-goal-link', {hasText: 'amendment-goal'}).click();
  await page.getByRole('button', {name: 'Goal settings', exact: true}).click();
  await page.getByRole('button', {name: 'Task ownership', exact: true}).click();
  await page.getByText('Goal commitments and governed changes', {exact: true}).click();
  await page.getByText('Goal commitments frozen · revision 2', {exact: true}).waitFor();
  await page.getByText('agent-b · Current basis acknowledged', {exact: true}).waitFor();
  await page.locator('details').filter({has: page.locator('summary', {hasText: 'Goal commitments and governed changes'})}).screenshot({path: resolve(output, 'english-readback.png')});
  assert.deepEqual(exceptions, []);
  await writeFile(resolve(output, 'result.json'), JSON.stringify({ok: true, scope: 'Packaged Chat; real project registration, SQLite initialization, metadata discovery and amendment HTTP; no model calls',
    generic_runtime_qualified: false, same_operation_replayed: true, peer_cli_commit_and_adoption: true, synthetic_progress_observations: true, view, receipt}, null, 2));
  console.log('Governed amendment browser passed: native registration, packaged UI, invalid input, preview/reload, dropped response, original replay, durable receipt, narrow viewport.');
} catch (error) {
  if (page) {
    await writeFile(resolve(output, 'failure.txt'), await page.locator('body').innerText());
    await page.screenshot({path: resolve(output, 'failure.png'), fullPage: true});
  }
  throw error;
} finally {
  await browser?.close();child.kill('SIGTERM');lines.close();
  await new Promise(accept => child.exitCode !== null ? accept() : child.once('exit', accept));
  await writeFile(resolve(output, 'server.log'), serverLog);
  await rm(root, {recursive: true, force: true});
}
