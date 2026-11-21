import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { createInterface } from 'node:readline';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as pause } from 'node:timers/promises';
import { startDaemon, api } from '../harness.mjs';

const firstText = 'journey-first-answer';
const secondText = 'journey-second-answer';
const self = fileURLToPath(import.meta.url);
const fakeCodex = fileURLToPath(new URL('../../daemon/test/fixtures/fake-codex.mjs', import.meta.url));

if (process.argv[2] === '--model') {
  // Only the model is scripted: hold the first attempt at the second request;
  // the same request succeeds after interruption and daemon restart.
  const marker = process.argv[3];
  // A new native session gets a new id; only thread/resume can keep the old id.
  const model = spawn(process.execPath, [fakeCodex, ...process.argv.slice(4)], {
    stdio: ['pipe', 'pipe', 'inherit'], env: { ...process.env, FAKE_CODEX_THREAD: `journey-session-${process.pid}` },
  });
  model.stdout.pipe(process.stdout);
  const lines = createInterface({ input: process.stdin });
  lines.on('line', (line) => {
    const message = JSON.parse(line);
    if (message.method === 'turn/start' && message.params.input.some((part) => part.text?.includes(secondText)) && !existsSync(marker)) {
      writeFileSync(marker, 'accepted');
      message.params.input.push({ type: 'text', text: ' livecommand slow' });
    }
    model.stdin.write(JSON.stringify(message) + '\n');
  });
  lines.on('close', () => model.stdin.end());
  model.on('exit', (code) => process.exit(code ?? 1));
  process.on('SIGTERM', () => model.kill('SIGTERM'));
} else {
  let input = '';
  for await (const chunk of process.stdin) input += chunk;
  const options = JSON.parse(input);
  // Remove enclosing agent-session wiring before the harness constructs its own environment.
  for (const key of Object.keys(process.env)) if (/^(LOOCHAT_|CALLI_|RAH_|LIMI_|ANTHROPIC_|CLAUDE_CODE_)/.test(key)) delete process.env[key];
  const dataDir = join(options.root, 'workbench');
  mkdirSync(dataDir);
  const env = {
    HOME: options.home, CODEX_HOME: join(options.home, '.codex'),
    LOOCHAT_DATA_DIR: dataDir, LOOCHAT_SOCKET: join(dataDir, 'daemon.sock'),
    LOOCHAT_WORKDIRS: options.project, LOOCHAT_ENV_FILE: join(options.root, 'no-env-file'),
    LOOCHAT_LOOPX_CORE_URL: options.core, LOOCHAT_LOOPX_REGISTRY: options.registry,
    LOOCHAT_LOOPX_CMD: `${options.python} -m loopx.cli`, LOOCHAT_LOOPX_CWD: options.repository,
    LOOCHAT_AGENTS_JSON: JSON.stringify({ agents: [{ id: 'codex', adapter: 'codex',
      command: [process.execPath, self, '--model', join(dataDir, 'model-second-attempt')] }] }),
  };
  let daemon;
  const json = async (path, body, expected = 200) => {
    const response = await api(daemon, path, body === undefined ? {} : { method: 'POST', body: JSON.stringify(body) });
    const value = await response.json();
    assert.equal(response.status, expected, JSON.stringify({ path, value }));
    return value;
  };
  // Poll a concrete persisted state; never use elapsed time as evidence of success.
  const until = async (read, predicate) => {
    const deadline = Date.now() + 20_000;
    let value;
    do {
      value = await read();
      if (predicate(value)) return value;
      await pause(25);
    } while (Date.now() < deadline);
    assert.fail(`State was not reached within the harness budget: ${JSON.stringify(value)}`);
  };
  const core = (...args) => JSON.parse(execFileSync(options.python,
    ['-m', 'loopx.cli', '--registry', options.registry, '--format', 'json', ...args],
    { cwd: options.repository, encoding: 'utf8', timeout: 20_000, maxBuffer: 8 * 1024 * 1024 }));
  const answer = (page, turn) => page.events.filter((event) => event.turnId === turn && event.type === 'message.delta')
    .map((event) => event.data.text).join('');
  const done = (page, turn) => page.events.some((event) => event.turnId === turn && event.type === 'turn.done' && event.data.stopReason !== 'cancelled');
  try {
    daemon = await startDaemon(env);
    const page = await fetch(daemon.base);
    assert.equal(page.status, 200);
    assert.equal(await page.text(), readFileSync(new URL('../../web/dist/index.html', import.meta.url), 'utf8'));
    assert.equal(core('registry').goals.length, 0);
    const preview = await json('/api/loopx/launch/preview', {
      agent: 'codex', cwd: options.project, permission: 'read_only', title: 'Goal journey',
      objective: 'Record two answers', completion: 'Both answers remain readable after a restart',
    });
    assert.equal(preview.status, 'preview_ready');
    assert.equal(core('registry').goals.length, 0, 'preview must not create a Goal');
    const launched = await json('/api/loopx/launch/apply', { proposalId: preview.proposalId, text: firstText });
    const { goalId, threadId, turnId: firstTurn } = launched;
    assert.equal(goalId, preview.goalId);
    assert.ok(threadId && firstTurn);
    const registered = core('registry').goals.filter((goal) => goal.id === goalId);
    assert.equal(registered.length, 1);
    assert.equal(registered[0].repo, options.project);
    assert.equal(core('status', '--goal-id', goalId, '--limit', '1').run_history.goals[0].id, goalId);
    const history = () => json(`/api/threads/${threadId}/history`);
    const first = await until(history, (value) => done(value, firstTurn));
    assert.equal(answer(first, firstTurn).split(firstText).length - 1, 1);
    const session = await json(`/api/threads/${threadId}`);
    assert.ok(session.agentSessionId);
    assert.equal(session.loopx.goalId, goalId);
    assert.equal(session.loopx.permission, 'read_only');
    const second = await json(`/api/threads/${threadId}/turns`, { text: secondText }, 202);
    await until(() => json(`/api/threads/${threadId}/events?stream=0`), (events) =>
      events.some((event) => event.turnId === second.turnId && event.type === 'tool.start' && event.data.id === 'live-command'));
    await json(`/api/threads/${threadId}/cancel`, {});
    const interrupted = await until(history, (value) => value.events.some((event) =>
      event.turnId === second.turnId && event.type === 'turn.done' && event.data.stopReason === 'cancelled'));
    assert.equal(answer(interrupted, second.turnId), '');
    await daemon.stop(); daemon = null;
    daemon = await startDaemon(env);
    const retained = await json(`/api/threads/${threadId}`);
    assert.equal(retained.agentSessionId, session.agentSessionId);
    assert.deepEqual(retained.loopx, session.loopx);
    assert.ok(answer(await history(), firstTurn).includes(firstText));
    const retry = await json(`/api/threads/${threadId}/turns`, { text: secondText }, 202);
    assert.notEqual(retry.turnId, second.turnId);
    const retried = await until(history, (value) => done(value, retry.turnId));
    assert.equal(answer(retried, retry.turnId).split(secondText).length - 1, 1);
    // A second restart proves the retry's completed result is durable as well.
    await daemon.stop(); daemon = null;
    daemon = await startDaemon(env);
    const final = await history();
    assert.equal(answer(final, firstTurn), answer(first, firstTurn));
    assert.equal(answer(final, retry.turnId), answer(retried, retry.turnId));
    assert.equal(answer(final, second.turnId), '');
    assert.deepEqual(final.events.filter((event) => event.type === 'turn.started').map((event) => event.turnId), [firstTurn, second.turnId, retry.turnId]);
    assert.equal(final.events.filter((event) => event.type === 'turn.done').length, 3);
    assert.equal(final.hasOlder, false);
    assert.equal(new Set(final.events.map((event) => event.seq)).size, final.events.length);
    const threads = await json('/api/threads');
    assert.equal(threads.filter((thread) => thread.loopx?.goalId === goalId).length, 1);
    const finalSession = await json(`/api/threads/${threadId}`);
    assert.equal(finalSession.agentSessionId, session.agentSessionId);
    assert.deepEqual(finalSession.loopx, session.loopx);
    const state = await json(`/api/loopx/goal/${threadId}?refresh=1`);
    assert.equal(state.freshness, 'fresh');
    assert.equal(state.binding.goalId, goalId);
    assert.equal(state.state.goalId, goalId);
    const finalGoal = core('status', '--goal-id', goalId, '--limit', '1').run_history.goals[0];
    assert.equal(finalGoal.id, goalId);
    assert.equal(finalGoal.registry_member, true);
    process.stdout.write(JSON.stringify({ goalId, threadId, sessionId: session.agentSessionId,
      completedResults: 2, interruptedTurns: 1, restarts: 2 }));
  } catch (error) {
    console.error(daemon?.log());
    throw error;
  } finally {
    await daemon?.stop();
  }
}
