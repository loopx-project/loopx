#!/usr/bin/env node
// The public Doubao CLI surface: one -p turn, a text reply, and a stderr session announcement.
import { appendFileSync, fstatSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { coordinationFixture } from './coordination.mjs';

const args = process.argv.slice(2);
if (process.env.FAKE_DOUBAO_LAUNCHER && !process.env.FAKE_DOUBAO_NATIVE_CHILD) {
  const child = spawn(process.execPath, [process.argv[1], ...args], { stdio: 'inherit', env: { ...process.env, FAKE_DOUBAO_NATIVE_CHILD: '1' } });
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
  child.once('exit', code => process.exit(code ?? 1));
  await new Promise(() => {});
}

if (args.includes('--version')) { console.log('doubao 2.31.2'); process.exit(0); }
if (args[0] === 'login') { console.log('Doubao fixture login'); process.exit(0); }
const flags = new Map();
const values = new Set(['-C', '-p', '--resume', '--model', '--effort', '--permission-mode']);
const booleans = new Set(['--list', '--dangerously-bypass-approvals-and-sandbox']);
function fail(text, code = 1) { console.error(text); process.exit(code); }
for (let index = 0; index < args.length; index++) {
  const arg = args[index], at = arg.indexOf('='), name = at < 0 ? arg : arg.slice(0, at);
  if (flags.has(name)) fail('invalid_arguments: Duplicate CLI option.');
  if (values.has(name)) flags.set(name, at < 0 ? args[++index] : arg.slice(at + 1));
  else if (booleans.has(name)) flags.set(name, true);
  else fail('invalid_arguments: Unknown CLI argument.');
}
if (flags.has('--list')) {
  const runtime = process.env.FAKE_DOUBAO_RUNTIME;
  const source = runtime && readFileSync(join(dirname(runtime), 'resources/js_app/entry-cli-router/main.js'), 'utf8');
  if (!source?.includes('type:"calli_models"')) { console.log('fixture-session  Fixture conversation'); process.exit(0); }
  if (process.env.FAKE_DOUBAO_MODEL_LOG) appendFileSync(process.env.FAKE_DOUBAO_MODEL_LOG, JSON.stringify({ args, runtime, marker: process.env.FAKE_DOUBAO_MARKER }) + '\n');
  if (process.env.FAKE_DOUBAO_IGNORE_SIGINT) process.on('SIGINT', () => undefined);
  await delay(Number(process.env.FAKE_DOUBAO_MODEL_DELAY ?? 0));
  if (process.env.FAKE_DOUBAO_MODEL_LARGE) { await new Promise(resolve => process.stdout.write('x'.repeat(1024 * 1024 + 1), resolve)); process.exit(0); }
  if (process.env.FAKE_DOUBAO_MODEL_ERROR) fail(process.env.FAKE_DOUBAO_MODEL_ERROR);
  if (process.env.FAKE_DOUBAO_MODEL_OUTPUT) { console.log(process.env.FAKE_DOUBAO_MODEL_OUTPUT); process.exit(0); }
  console.log(JSON.stringify({ type: 'calli_models', models: [
    { modelId: 'native-default', name: 'Doubao Default', available: true, isDefault: true, effortAdjustable: true, efforts: [{ level: 'none', name: 'None' }, { level: 'low', name: 'Low' }, { level: 'high', name: 'High' }] },
    { modelId: 'native-fast', name: 'Doubao Fast', description: 'Fast task model', available: true, isDefault: false, effortAdjustable: false, efforts: [] },
    { modelId: 'native-locked', name: 'Doubao Locked', available: false, isDefault: false, effortAdjustable: true, efforts: [] },
  ] }));
  process.exit(0);
}
if (!flags.get('-p')?.trim()) fail('invalid_arguments: A prompt must immediately follow -p.');
if (flags.get('-C') !== process.cwd()) fail('invalid_arguments: Wrong working directory.');
if (flags.has('--permission-mode') && flags.has('--dangerously-bypass-approvals-and-sandbox')) fail('invalid_arguments: Conflicting permission flags.');
if (process.env.FAKE_DOUBAO_LOG) appendFileSync(process.env.FAKE_DOUBAO_LOG, JSON.stringify({
  args, pid: process.pid, cwd: process.cwd(), marker: process.env.FAKE_DOUBAO_MARKER,
  ready: Boolean(process.env.CALLI_TEAM_SOCKET && process.env.CALLI_TEAM_KEY),
  path: process.env.PATH, runtime: process.env.FAKE_DOUBAO_RUNTIME,
}) + '\n');

// Native startup rejects a pipe closed by the client before the task connects its input.
if (fstatSync(0).isFIFO()) fail('internal_error: Unable to connect standard input.');
// The adapter supplies null input: print mode has no stdin protocol or login/update answer.
process.stdin.resume();
await new Promise(resolve => process.stdin.on('end', resolve));
const text = flags.get('-p');
const stream = process.env.FAKE_DOUBAO_RUNTIME && readFileSync(join(dirname(process.env.FAKE_DOUBAO_RUNTIME), 'resources/js_app/entry-cli-router/main.js'), 'utf8').includes('output_format:"stream-json"');
if (stream) {
  const body = text.split(/<calli-(?:tools|coordination|team)>/)[0].trim();
  const id = body === 'wrong-session' ? '38450000000000002' : flags.get('--resume') ?? '38450000000000001';
  const out = (record) => process.stdout.write(JSON.stringify({ session_id: id, ...record }) + '\n');
  const messageId = 'fake-message';
  const delta = (text) => out({ type: 'stream_event', message_id: messageId, event: { type: 'content_block_delta', delta: { type: 'text_delta', text } } });
  const tools = body === 'stream-tool' || body === 'stream-unknown-tool' || body === 'slow';
  if (body === 'start-error') fail('Error: Signed out.');
  if (body !== 'no-session-id') out({ type: 'system', subtype: 'init' });
  if (body === 'stream-invalid') { console.log('not-json'); await delay(60_000); }
  if (body === 'fail') { out({ type: 'result', is_error: true, subtype: 'error_during_execution', errors: ['native stream failure'], error: { code: 'internal_error' } }); process.exit(1); }
  if (tools) {
    delta('Inspecting a file.');
    const step = { key: 'fake-read', title: 'Read fixture', finished: false, ...(body !== 'stream-unknown-tool' ? { call: { id: 'call-1', name: 'Read', input: { file_path: 'fixture.txt' } } } : {}) };
    out({ type: 'calli_bridge_tool', message_id: messageId, phase: 'started', step });
    await delay(25);
    if (body !== 'slow') out({ type: 'calli_bridge_tool', message_id: messageId, phase: 'finished', step: { ...step, finished: true, ...(step.call ? { call: { ...step.call, result: { isError: false, text: 'fixture contents' } } } : {}) } });
  }
  if (body === 'slow') {
    delta('working');
    process.on('SIGINT', () => { out({ type: 'result', is_error: true, subtype: 'error_during_execution', errors: ['Cancelled.'], error: { code: 'cancelled' } }); process.exit(130); });
    await delay(60_000);
  }
  const answer = coordinationFixture(text) ?? (tools ? 'Tool task complete.' : `doubao:${body}`);
  const chars = [...answer], split = Math.floor(chars.length / 2);
  delta(chars.slice(0, split).join(''));
  await delay(Number(process.env.FAKE_DOUBAO_STREAM_DELAY ?? 30));
  delta(chars.slice(split).join(''));
  out({ type: 'assistant', message: { id: messageId, role: 'assistant', content: [{ type: 'text', text: answer }] } });
  await delay(25);
  if (body !== 'stream-incomplete') out({ type: 'result', subtype: 'success', is_error: false, message_id: messageId, result: { type: 'text', content: answer } });
  process.exit(0);
}

if (text === 'start-error') fail('Error: Signed out. Run `doubao login` and try again.');
if (text === 'slow-before-id') await delay(60_000);
const sessionId = flags.get('--resume') ?? '38450000000000001';
if (text !== 'no-session-id') {
  const announced = text === 'wrong-session' ? '38450000000000002' : sessionId;
  const record = JSON.stringify({ type: 'session', session_id: announced });
  const at = Math.floor(record.length / 2);
  process.stderr.write(record.slice(0, at));
  await delay(5);
  process.stderr.write(record.slice(at) + (text === 'no-newline' ? '' : '\r\n'));
}
if (text === 'fail') fail('Error: A native task failed.');
if (text === 'no-output') process.exit(0);
if (text.startsWith('slow') || text === 'wrong-session') {
  if (process.env.FAKE_DOUBAO_IGNORE_SIGINT) process.on('SIGINT', () => undefined);
  else process.on('SIGINT', () => fail('Error: Cancelled.', 130));
  process.stdout.write('working');
  await delay(60_000);
}
if (text === 'stderr-noise') process.stderr.write('diagnostic '.repeat(3_000) + '\n');
const body = text.split(/<calli-(?:tools|coordination|team)>/)[0].trim();
const answer = coordinationFixture(text) ?? (text === 'json-text'
  ? '{"type":"session","session_id":"reply-not-metadata"}'
  : `doubao:${body}`);
// Deliberately split UTF-8 across writes, including a multibyte character on some inputs.
const bytes = Buffer.from(answer + '\n'), split = Math.floor(bytes.length / 2);
process.stdout.write(bytes.subarray(0, split));
await delay(5);
process.stdout.write(bytes.subarray(split));
