#!/usr/bin/env node
if (process.argv.includes('--version')) { process.stdout.write('fixture 1.2.3\n'); process.exit(0); }
// A stand-in for the Antigravity CLI in headless stream-json mode, for tests.
// Prompt text drives behaviour: "tool" runs a command, "command" needs a permission that headless
// mode cannot ask for, "slow" waits until interrupted, "crash" exits.
import { existsSync, readFileSync, writeFileSync, symlinkSync } from 'node:fs';
import { agyUsage } from './live-usage.mjs';
import { coordinationFixture } from './coordination.mjs';
import { join } from 'node:path';
const args = process.argv.slice(2);

if (args[0] === 'models') {
  if (process.env.FAKE_HANDOVER_QUOTA) { process.stdout.write('gemini-pro\tGemini Pro\nclaude-sonnet\tClaude Sonnet\n'); process.exit(0); }
  process.stderr.write('Fetching available models...\n');
  process.stdout.write('fast-low\tFast (Low)\nfast-high\tFast (High)\nnot a model line\nbig-thinking\tBig (Thinking)\n');
  process.exit(0);
}

// One-off print mode with a prompt of its own: how agy answers the commands it handles itself.
const promptIndex = args.indexOf('-p');
if (promptIndex >= 0 && args[promptIndex + 1] !== undefined) {
  const text = args[promptIndex + 1];
  if (text === '/help') {
    if (process.env.FAKE_AGY_GOAL) process.stdout.write('/goal\tSet a native goal\n');
    process.stdout.write('/config (settings)\tOpen settings panel\n/effort\tSet the reasoning effort\n/model\tSet a model\n/skills\tList available skills\n/usage (quota)\tView model quota usage\n/broken\tAlways fails\n');
  } else if (text === '/skills') {
    process.stdout.write('make-video\tTurn a brief into a video\nreview\tReview the current change\n');
  } else if (text.startsWith('/usage') || text.startsWith('/quota')) {
    if (process.env.FAKE_HANDOVER_QUOTA) {
      // Each scope is the used percent, or { used, resetsAt } when a test needs the reset time.
      const used = JSON.parse(readFileSync(process.env.FAKE_HANDOVER_QUOTA, 'utf8'));
      for (const [scope, value] of Object.entries(used)) {
        const v = typeof value === 'number' ? { used: value } : value;
        process.stdout.write(`${scope}\tWeekly Limit Remaining\t${100 - v.used}%\t${v.resetsAt ?? '2099-01-01T00:00:00Z'}\n`);
      }
      process.exit(0);
    }
    process.stdout.write('Fast models\tWeekly Limit Remaining\t97%\t2026-10-16T11:59:18Z\nSlow | models\tFive Hour Limit Remaining\t40%\t2026-10-10T12:14:28Z\n');
  } else if (text === '/config') {
    process.stdout.write('colorScheme\tterminal\nallowNonWorkspaceAccess\tfalse\n');
  } else if (text === '/broken') {
    process.stderr.write('error: /broken failed: not available here\n');
    process.exit(1);
  } else {
    process.stdout.write(`print:${text}\n`);
  }
  process.exit(0);
}

const flag = (name) => (args.includes(name) ? args[args.indexOf(name) + 1] : undefined);
if (!args.includes('-p=') || flag('--input-format') !== 'stream-json' || flag('--output-format') !== 'stream-json') {
  process.stderr.write('fake-agy: expected stream-json in both directions and -p=\n');
  process.exit(2);
}

const conversation = flag('--conversation') ?? (process.env.FAKE_HANDOVER_EXHAUST_ONCE ? `agy-conv-${process.pid}` : 'agy-conv-1');
const model = flag('--model') ?? 'default';
const fullAccess = args.includes('--dangerously-skip-permissions');
const mode = fullAccess ? 'always-proceed' : (flag('--mode') ?? 'request-review');

const out = (obj) => process.stdout.write(JSON.stringify(obj) + '\n');
let stepIndex = 0;
const step = (fields) => out({ event: 'step_update', step_update: { conversation_id: conversation, ...fields } });
const result = (fields) => out({ event: 'result', result: { conversation_id: conversation, ...fields } });

setTimeout(() => out({ event: 'init', conversation_id: conversation, init: { cwd: process.cwd(), tools: [], permission_mode: mode } }), 10);

let waiting = false;
process.on('SIGINT', () => {
  if (waiting) result({ status: 'ERROR', response: 'partial', error: 'interrupted' });
  process.exit(1);
});

function runTurn(text) {
  step({ step_index: stepIndex++, state: 'DONE', step_type: 'user_input' });
  if (process.env.FAKE_AGY_WEB && text.startsWith('Search the web using')) {
    const index = stepIndex++;
    if (process.env.FAKE_AGY_WEB !== 'no-evidence') {
      const tool_info = { name: 'search_web', parameters: { query: 'example domains' } };
      step({ step_index: index, state: 'ACTIVE', step_type: 'tool', tool_info });
      step({ step_index: index, state: process.env.FAKE_AGY_WEB === 'failed' ? 'ERROR' : 'DONE', step_type: 'tool', tool_info });
    }
    const reply = 'For documentation. [Example Domain](https://example.com/)';
    step({ step_index: stepIndex++, state: 'DONE', step_type: 'agent_response', text_delta: reply });
    result({ status: 'SUCCESS', response: reply }); return;
  }
  if (process.env.FAKE_AGY_IMAGE && text.startsWith('Generate exactly one image')) {
    const mode = process.env.FAKE_AGY_IMAGE;
    if (mode === 'slow') { waiting = true; return; }
    const path = join(process.cwd(), 'generated image.png');
    if (mode === 'outside') symlinkSync(process.env.FAKE_AGY_OUTSIDE, path);
    else if (mode !== 'missing') writeFileSync(path, mode === 'text' ? 'not an image' : Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX2kAAAAASUVORK5CYII=', 'base64'));
    if (mode !== 'no-evidence') {
      const index = stepIndex++;
      const info = mode === 'subagent' || mode === 'wrong-subagent'
        ? { name: 'invoke_subagent', parameters: { TypeName: mode === 'subagent' ? 'image-generator' : 'coder' } }
        : { name: 'generate_image', parameters: { Prompt: text } };
      if (mode === 'subagent-info') {
        const subagent_info = { subagents: [{ role: 'Image Generator', type_name: 'image-generator', initial_prompt: text }] };
        step({ step_index: index, state: 'ACTIVE', step_type: 'subagent', subagent_info });
        step({ step_index: index, state: 'DONE', step_type: 'subagent', subagent_info });
      } else {
        step({ step_index: index, state: 'ACTIVE', step_type: 'tool', tool_info: info });
        step({ step_index: index, state: mode === 'failed' ? 'ERROR' : 'DONE', step_type: 'tool',
          tool_info: { ...info, output: mode === 'markdown-title' ? '' : `Image created: "${path}"`, ...(mode === 'failed' ? { error: { message: 'image generation unavailable' } } : {}) } });
      }
    }
    const reply = `![Generated image](${path}${mode === 'markdown-title' ? ' "Paper Fox"' : ''})`;
    step({ step_index: stepIndex++, state: 'DONE', step_type: 'agent_response', text_delta: reply });
    result({ status: 'SUCCESS', response: reply });
    return;
  }
  const body = text.replace(/<calli-context>[\s\S]*?<\/calli-context>/g, '').split(/<calli-(?:coordination|team|tools|todo|progress)>/)[0].trim();
  if (body.startsWith('provider-error:')) {
    result({ status: 'ERROR', error: body.slice('provider-error:'.length).trim() });
    return;
  }
  const exhaustTrigger = model !== 'claude-sonnet' && (body === 'quota-exhausted' || body.startsWith('[协作消息') && body.endsWith('quota-exhausted'));
  // FAKE_HANDOVER_EXHAUST_ONCE names a marker file of conversation ids: the quota error happens
  // once per conversation, so a test can watch the daemon retry the same ask after the "reset".
  // FAKE_HANDOVER_EXHAUST_DELAY_MS stalls the failure so a follow-up can queue behind it.
  const exhaustFail = () => result({ status: 'ERROR', error: 'You have exhausted your quota on this model.' });
  const exhaustDelay = Number(process.env.FAKE_HANDOVER_EXHAUST_DELAY_MS ?? 0);
  const exhaustNow = () => { if (exhaustDelay) setTimeout(exhaustFail, exhaustDelay); else exhaustFail(); };
  if (exhaustTrigger && process.env.FAKE_HANDOVER_EXHAUST_ONCE) {
    const marker = process.env.FAKE_HANDOVER_EXHAUST_ONCE;
    const seen = existsSync(marker) ? readFileSync(marker, 'utf8') : '';
    if (seen.includes(conversation)) {
      step({ step_index: stepIndex++, state: 'DONE', step_type: 'agent_response', text_delta: `print:${body}` });
      result({ status: 'SUCCESS', response: `print:${body}`, num_turns: 1 });
      return;
    }
    writeFileSync(marker, seen + conversation + '\n');
    exhaustNow();
    return;
  }
  if (exhaustTrigger) { exhaustNow(); return; }
  if ((body === 'quota-near' || body.startsWith('[协作消息') && body.endsWith('quota-near')) && process.env.FAKE_HANDOVER_QUOTA) writeFileSync(process.env.FAKE_HANDOVER_QUOTA, JSON.stringify({ 'Gemini Models': 95, 'Claude Models': 20 }));
  if (text.startsWith('请写一份交接摘要') && process.env.FAKE_HANDOVER_SUMMARY_FAIL) return result({ status: 'ERROR', error: 'summary failed' });
  const coordinated = coordinationFixture(text);
  if (coordinated !== null) {
    step({ step_index: stepIndex++, state: 'DONE', step_type: 'agent_response', text_delta: coordinated });
    result({ status: 'SUCCESS', response: coordinated, num_turns: 1 });
    return;
  }
  if (process.env.FAKE_LIVE_USAGE && text.includes('live usage')) {
    agyUsage(conversation, stepIndex++);
    if (text.includes('slow')) { waiting = true; return; }
    setTimeout(() => result({ status: 'SUCCESS', response: 'live usage', num_turns: 1 }), 2400);
    return;
  }
  if (text.includes('crash')) {
    process.stderr.write('fake-agy: something broke\n');
    process.exit(7);
  }
  if (text.includes('slow')) {
    step({ step_index: stepIndex, state: 'ACTIVE', step_type: 'agent_response', text_delta: 'working' });
    waiting = true;
    return;
  }
  if (text.includes('tool')) {
    const index = stepIndex++;
    const info = { name: 'run_command', parameters: { CommandLine: 'echo hi' } };
    step({ step_index: index, state: 'ACTIVE', step_type: 'tool', tool_name: 'run_command', tool_info: info });
    step({ step_index: index, state: 'DONE', step_type: 'tool', tool_name: 'run_command', tool_info: { ...info, output: 'hi\r\n' } });
  }
  if (text.includes('command') && !fullAccess) {
    const index = stepIndex++;
    const info = { name: 'run_command', parameters: { CommandLine: 'make test' } };
    step({ step_index: index, state: 'ACTIVE', step_type: 'tool', tool_name: 'run_command', tool_info: info });
    step({ step_index: index, state: 'ERROR', step_type: 'tool', tool_name: 'run_command', tool_info: { ...info, error: { type: 'TOOL_ERROR', message: 'user denied permission to run command' } } });
    process.stderr.write('jetski: no output produced\n');
    return result({ status: 'SUCCESS', response: '', denied_actions: [{ action: 'command', display_name: 'RunCommand' }] });
  }
  const index = stepIndex++;
  const reply = `echo:${text};model:${model};mode:${mode};conv:${conversation}`;
  step({ step_index: index, state: 'ACTIVE', step_type: 'agent_response', text_delta: reply });
  step({ step_index: index, state: 'DONE', step_type: 'agent_response', text_delta: '\n' });
  result({ status: 'SUCCESS', response: reply + '\n', num_turns: 1 });
}

let buffer = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => {
  buffer += chunk;
  let idx;
  while ((idx = buffer.indexOf('\n')) >= 0) {
    const line = buffer.slice(0, idx).trim();
    buffer = buffer.slice(idx + 1);
    if (!line) continue;
    const msg = JSON.parse(line);
    if (msg.event !== 'user') {
      process.stderr.write(`warning: ignoring unsupported stream input message event "${msg.event}"\n`);
      continue;
    }
    runTurn(String(msg.message?.content ?? ''));
  }
});
process.stdin.on('end', () => process.exit(0));
