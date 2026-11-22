#!/usr/bin/env node
if (process.argv.includes('--version')) { process.stdout.write('fixture 1.2.3\n'); process.exit(0); }
// Antigravity ACP fixture plus the account-query and login CLI entry points.
import { existsSync, mkdirSync, readFileSync, writeFileSync, appendFileSync, symlinkSync } from 'node:fs';
import { createInterface } from 'node:readline';
import { agyUsage } from './live-usage.mjs';
import { coordinationFixture } from './coordination.mjs';
import { join } from 'node:path';
const args = process.argv.slice(2);

if (args[0] === 'acp' && args[1] === '--login') {
  const directory = join(process.env.HOME, '.gemini', 'antigravity-cli');
  if (process.env.LOOCHAT_AGY_HOME !== directory || process.env.ANTIGRAVITY_REMOTE_OOB_AUTH !== '1') process.exit(2);
  // Ignore unrelated links and wait for a complete authorization URL across output chunks.
  process.stdout.write('https://accounts.google.com.evil.invalid/o/oauth2/auth\nhttps://accounts.google.com/o/oauth2/auth?redirect_uri=https%3A%2F%2Fevil.invalid\n');
  process.stdout.write('Open this link in a browser to log in with Google:\nhttps://accounts.google.com/o/oauth2/');
  setTimeout(() => process.stdout.write('auth?redirect_uri=https%3A%2F%2Fantigravity.google%2Foauth-callback&state=fixture\nThen paste the authorization code or full URL:\n'), 20);
  createInterface({ input: process.stdin }).on('line', code => {
    const valid = code === 'test-agy-code' || code === 'https://antigravity.google/oauth-callback?code=test-agy-code&state=fixture';
    if (!valid) process.exit(1);
    mkdirSync(directory, { recursive: true });
    if (process.env.FAKE_AGY_AUTH_BAD_STATUS !== '1') writeFileSync(join(directory, 'antigravity-oauth-token'), JSON.stringify({
      token: { access_token: 'fixture-agy-access', refresh_token: 'fixture-agy-refresh' }, id_token_email: 'agy-new@example.com', auth_method: 'oauth-personal',
    }), { mode: 0o600 });
    process.stdout.write('Logged in as agy-new@example.com.\n');
    process.exit(0);
  });
  await new Promise(() => {});
}

if (process.env.FAKE_AGY_ACCOUNT_CHECK === '1') {
  const directory = join(process.env.HOME, '.gemini', 'antigravity-cli');
  if (directory !== process.env.LOOCHAT_AGY_HOME || process.env.GOOGLE_API_KEY || process.env.GEMINI_API_KEY ||
      process.env.GOOGLE_APPLICATION_CREDENTIALS || !existsSync(join(directory, 'antigravity-oauth-token'))) process.exit(2);
}

if (args[0] === 'models') {
  if (process.env.FAKE_AGY_MODELS_ERROR) { process.stderr.write(process.env.FAKE_AGY_MODELS_ERROR + '\n'); process.exit(1); }
  if (process.env.FAKE_AGY_LOGIN_REQUIRED === '1' && !existsSync(join(process.env.HOME, '.gemini', 'antigravity-cli', 'antigravity-oauth-token'))) {
    process.stderr.write('Fetching available models...\nError: Please sign in to view available models. Launch the CLI without arguments to sign in.\n'); process.exit(1);
  }

  if (process.env.FAKE_AGY_MODELS_FILE) {
    const catalog = JSON.parse(readFileSync(process.env.FAKE_AGY_MODELS_FILE, 'utf8'));
    if (catalog.startedFile) writeFileSync(catalog.startedFile, 'started');
    if (catalog.delayMs) await new Promise(resolve => setTimeout(resolve, catalog.delayMs));
    if (catalog.error) { process.stderr.write('fixture model catalog unavailable\n'); process.exit(1); }
    for (const model of catalog.models) process.stdout.write(`${model.id}\t${model.label}\n`);
    process.exit(0);
  }
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

if (args[0] !== 'acp') throw new Error('expected agy acp');
let conversation = 'agy-conv-1', model = 'default', effort, mode = 'default';
let fullAccess = false, pending, waiting = false;
let stepIndex = 0;
const out = obj => process.stdout.write(JSON.stringify(obj) + '\n');
const update = u => out({ jsonrpc: '2.0', method: 'session/update', params: { sessionId: conversation, update: u } });
const reply = (id, result) => out({ jsonrpc: '2.0', id, result });
const started = new Set();
const step = s => {
  if (s.step_type === 'agent_response' && s.text_delta) update({ sessionUpdate: 'agent_message_chunk', content: { type: 'text', text: s.text_delta } });
  if (!['tool', 'subagent'].includes(s.step_type)) return;
  const id = `agy-step-${s.step_index}`;
  const sub = s.subagent_info?.subagents?.[0];
  const name = s.tool_name ?? s.tool_info?.name ?? (sub ? 'invoke_subagent' : 'tool');
  if (!started.has(id)) {
    started.add(id);
    update({ sessionUpdate: 'tool_call', toolCallId: id, name, title: s.tool_info?.parameters?.CommandLine ?? name,
      kind: name === 'run_command' ? 'execute' : 'other', status: 'in_progress',
      rawInput: sub ? { TypeName: sub.type_name } : s.tool_info?.parameters });
  }
  if (['DONE', 'ERROR'].includes(s.state)) update({ sessionUpdate: 'tool_call_update', toolCallId: id,
    status: s.state === 'ERROR' ? 'failed' : 'completed', rawOutput: s.tool_info?.error?.message ?? s.tool_info?.output ?? '' });
};
const result = fields => {
  waiting = false;
  if (fields.status !== 'SUCCESS') {
    if (fields.error === 'interrupted') reply(pending, { stopReason: 'cancelled' });
    else out({ jsonrpc: '2.0', id: pending, error: { code: -32000, message: fields.error } });
  } else {
    update({ sessionUpdate: 'usage_update', used: 1234, size: 1048576 });
    reply(pending, { stopReason: 'end_turn', ...(fields.usage ? { usage: fields.usage } : {}) });
  }
};
const config = () => [{ id: 'model', category: 'model', currentValue: model, options: [{ value: 'fast' }, { value: 'big-thinking' }] },
  { id: 'thought_level', category: 'thought_level', currentValue: effort ?? 'high', options: [{ value: 'low' }, { value: 'high' }] }];
const info = () => ({ sessionId: conversation, configOptions: config(), modes: { currentModeId: mode,
  availableModes: ['default', 'accept-edits', 'auto', 'plan'].map(id => ({ id, name: id })) } });

let advanceIndex = 0;
let advanceScript = '';
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
  const body = text.replace(/<loochat-context>[\s\S]*?<\/loochat-context>/g, '').split(/<loochat-(?:coordination|team|tools|todo|progress)>/)[0].trim();
  // FAKE_AGY_ADVANCE names a script file consumed one line per turn, in order: "wait:<what>[:<minutes>]"
  // ends the reply with a `[进度] 等外部` line, "doing:<note>" with `[进度] 进行中`, "review" / "blocked"
  // with their stages, "error:<message>" fails the turn, "plain:<text>" answers with no progress
  // line, and "slow" stalls until interrupted. Rewriting the file starts it over, so a test can
  // script the replies that are still to come.
  if (process.env.FAKE_AGY_ADVANCE) {
    const text = readFileSync(process.env.FAKE_AGY_ADVANCE, 'utf8');
    const entries = text.split('\n').map(l => l.trim()).filter(Boolean);
    if (text !== advanceScript) { advanceScript = text; advanceIndex = 0; }
    if (advanceIndex >= entries.length) advanceIndex = 0;
    const entry = entries[advanceIndex++];
    if (entry !== undefined) {
      const colon = entry.indexOf(':');
      const kind = colon >= 0 ? entry.slice(0, colon) : entry;
      const rest = colon >= 0 ? entry.slice(colon + 1) : '';
      if (kind === 'slow') { waiting = true; return; }
      if (kind === 'error') { result({ status: 'ERROR', error: rest || 'boom' }); return; }
      // A trailing ":<n>" on a wait is the minutes the note should promise.
      const waitNote = (() => {
        const m = /^(.*?)(?::(\d+))?$/.exec(rest);
        return m && m[2] ? `${m[1]}，约 ${m[2]} 分钟` : rest;
      })();
      const reply = kind === 'wait' ? `还没好。\n[进度] 等外部：${waitNote}`
        : kind === 'doing' ? `在忙。\n[进度] 进行中：${rest || '还在做'}`
        : kind === 'review' ? `做完了。\n[进度] 待验收：${rest || '请检查'}`
        : kind === 'blocked' ? `卡住了。\n[进度] 需要你：${rest || '缺信息'}`
        : rest;
      step({ step_index: stepIndex++, state: 'DONE', step_type: 'agent_response', text_delta: reply });
      result({ status: 'SUCCESS', response: reply, num_turns: 1 });
      return;
    }
  }
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
  // A quota error that promises its own reset in the wording the subscriptions use, for the
  // resume watch's message-parsed hint; FAKE_HANDOVER_RESET_IN controls how far out it points.
  if (body === 'quota-exhausted-reset') {
    const in_ = process.env.FAKE_HANDOVER_RESET_IN ?? '61s';
    result({ status: 'ERROR', error: `Individual quota reached. Please upgrade your subscription to increase your limits. Resets in ${in_}.` });
    return;
  }
  // A transient provider failure, worded like the real gateways (no capacity on the server). With
  // FAKE_TRANSIENT_ONCE naming a marker file it happens once per conversation, so a test can watch
  // the daemon retry the same ask; without it every matching ask fails, until the retries give up.
  if (body === 'no-capacity') {
    const transientFail = () => result({ status: 'ERROR', error: 'API error (attempt 2): UNAVAILABLE (code 503): No capacity available for model gemini-3.8-flash-high on the server' });
    const marker = process.env.FAKE_TRANSIENT_ONCE;
    if (!marker) { transientFail(); return; }
    const seen = existsSync(marker) ? readFileSync(marker, 'utf8') : '';
    if (!seen.includes(conversation)) {
      writeFileSync(marker, seen + conversation + '\n');
      transientFail();
      return;
    }
  }
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
    setTimeout(() => result({ status: 'SUCCESS', response: 'live usage', num_turns: 1, usage: { inputTokens: 100, cachedReadTokens: 50, cachedWriteTokens: 0, outputTokens: 20, totalTokens: 170 } }), 2400);
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
  const index = stepIndex++;
  const reply = `echo:${text};model:${model};${effort ? `effort:${effort};` : ''}mode:${mode};conv:${conversation}`;
  step({ step_index: index, state: 'ACTIVE', step_type: 'agent_response', text_delta: reply });
  step({ step_index: index, state: 'DONE', step_type: 'agent_response', text_delta: '\n' });
  result({ status: 'SUCCESS', response: reply + '\n', num_turns: 1 });
}

const approvals = new Map();
let approvalId = 1000;
createInterface({ input: process.stdin }).on('line', async line => {
  const msg = JSON.parse(line), p = msg.params ?? {};
  if (process.env.FAKE_AGY_RPC_LOG) appendFileSync(process.env.FAKE_AGY_RPC_LOG, JSON.stringify({ pid: process.pid, ...msg }) + '\n');
  if (!msg.method) { approvals.get(msg.id)?.(msg.result); approvals.delete(msg.id); return; }
  switch (msg.method) {
    case 'initialize': reply(msg.id, { protocolVersion: 1, agentCapabilities: { loadSession: true, sessionCapabilities: { resume: {} } } }); break;
    case 'session/new':
    case 'session/resume':
    case 'session/load':
      conversation = p.sessionId ?? (process.env.FAKE_HANDOVER_EXHAUST_ONCE || process.env.FAKE_TRANSIENT_ONCE ? `agy-conv-${process.pid}` : 'agy-conv-1');
      reply(msg.id, info());
      update({ sessionUpdate: 'available_commands_update', availableCommands: [
        { name: 'compact', description: 'Compact the conversation' }, { name: 'goal', description: 'Set a native goal' },
        { name: 'make-video', description: 'Turn a brief into a video' }] });
      update({ sessionUpdate: 'usage_update', used: 0, size: 1048576 });
      break;
    case 'session/set_mode': mode = p.modeId; fullAccess = mode === 'auto';
      update({ sessionUpdate: 'current_mode_update', currentModeId: mode }); reply(msg.id, {}); break;
    case 'session/set_config_option':
      if (p.configId === 'model') model = p.value;
      else if (p.configId === 'thought_level') effort = p.value;
      else { out({ jsonrpc: '2.0', id: msg.id, error: { code: -32602, message: 'unknown config' } }); break; }
      reply(msg.id, { configOptions: config() }); break;
    case 'session/prompt': {
      pending = msg.id;
      let text = p.prompt.filter(c => c.type === 'text').map(c => c.text).join('');
      if (p.prompt.some(c => c.type === 'image')) text += ';image:embedded';
      if (/^\/compact(?:\s|$)/.test(text)) {
        if (text === '/compact fail') { result({ status: 'ERROR', error: 'compact failed' }); break; }
        if (text === '/compact slow') { waiting = true; break; }
        // agy 1.5.12 reports occupancy and a normal prompt result, no compaction entity.
        update({ sessionUpdate: 'usage_update', used: 250, size: 1048576 });
        reply(msg.id, { stopReason: 'end_turn' }); break;
      }
      if (text === 'usage-drop') { update({ sessionUpdate: 'usage_update', used: 10, size: 1048576 }); reply(msg.id, { stopReason: 'end_turn' }); break; }
      if (text.includes('run a command')) {
        const id = ++approvalId;
        const answer = new Promise(resolve => approvals.set(id, resolve));
        out({ jsonrpc: '2.0', id, method: 'session/request_permission', params: { sessionId: conversation,
          toolCall: { toolCallId: 'approval-command', title: 'Run command' },
          options: [{ optionId: 'allow', kind: 'allow_once', name: 'Allow' }, { optionId: 'reject', kind: 'reject_once', name: 'Reject' }] } });
        const res = await answer;
        if (res.outcome?.optionId !== 'allow') { reply(msg.id, { stopReason: 'cancelled' }); break; }
      }
      runTurn(text); break;
    }
    case 'session/cancel': if (waiting) result({ status: 'ERROR', error: 'interrupted' }); break;
    default: out({ jsonrpc: '2.0', id: msg.id, error: { code: -32601, message: 'Unknown method' } });
  }
});
process.stdin.on('end', () => process.exit(0));
