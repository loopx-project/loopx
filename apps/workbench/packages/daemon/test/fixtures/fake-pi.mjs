let pendingCompact = null;
if (process.argv.includes('--version')) { process.stdout.write('fixture 1.2.3\n'); process.exit(0); }
// Fake pi RPC process for tests. Speaks pi's JSONL RPC protocol on stdio.
// Prompt text drives behaviour: "slow" waits until abort, "fail" ends with an error.
import { personalFixture } from './personal.mjs';
import { coordinationFixture } from './coordination.mjs';
import { appendFileSync, readFileSync, watchFile, unwatchFile } from 'node:fs';
import { basename } from 'node:path';
import { createInterface } from 'node:readline';

const args = process.argv.slice(2);
import { spawn } from 'node:child_process';
import { once } from 'node:events';

// Model an MCP server which outlives its launcher and ignores graceful termination.
if (process.env.FAKE_PI_CHILDREN && !args.includes('--no-mcp')) {
  const child = spawn(process.execPath, ['-e', "process.on('SIGTERM', () => {}); process.stdout.write('ready'); setInterval(() => {}, 1000);"], {
    stdio: ['ignore', 'pipe', 'ignore'],
    detached: true,
  });
  await once(child.stdout, 'data');
  appendFileSync(process.env.FAKE_PI_CHILDREN, JSON.stringify({ pid: child.pid, kind: args[0] === 'mcp' ? 'mcp' : 'session' }) + '\n');
}
const catalogState = () => process.env.FAKE_PI_CATALOG_FILE ? JSON.parse(readFileSync(process.env.FAKE_PI_CATALOG_FILE, 'utf8')) : {};
if (process.env.FAKE_PI_PROBE_LOG) appendFileSync(process.env.FAKE_PI_PROBE_LOG, JSON.stringify({ kind: args[0] === 'mcp' ? 'mcp' : args.includes('--no-session') ? 'catalog' : 'session', noMcp: args.includes('--no-mcp') }) + '\n');
// `pi mcp list --json` is a shell command, not an RPC session: FAKE_PI_MCP=1 reports the zai vision
// MCP server as connected, anything else no servers at all.
if (args[0] === 'mcp' && args[1] === 'list' && args.includes('--json')) {
  while (catalogState().mcpHold) await new Promise(r => setTimeout(r, 10));
  const servers =
    process.env.FAKE_PI_MCP === '1'
      ? [{ name: 'zai-mcp-server', enabled: true, state: 'connected', tools: ['image_analysis', 'ui_diff_check'] }]
      : [];
  if (process.env.FAKE_PI_PROBE_LOG) appendFileSync(process.env.FAKE_PI_PROBE_LOG, JSON.stringify({ kind: 'mcp-done' }) + '\n');
  process.stdout.write(JSON.stringify({ servers, errors: [] }) + '\n');
  process.exit(0);
}
if (!args.includes('--mode') || args[args.indexOf('--mode') + 1] !== 'rpc') {
  console.error('fake-pi: expected --mode rpc');
  process.exit(2);
}
const ephemeral = args.includes('--no-session');
if (ephemeral && process.env.FAKE_PI_FORBID_PROBE) throw new Error('unexpected model probe');
if (process.env.FAKE_PI_START_DELAY_MS) await new Promise(r => setTimeout(r, Number(process.env.FAKE_PI_START_DELAY_MS)));
const sessionIdx = args.indexOf('--session');
let sessionId = sessionIdx >= 0 ? args[sessionIdx + 1] : 'pi-session-1';
// pi keeps sessions by directory. `--fork <id>` copies one into the directory pi is started in;
// the copy's id says where it lives: `<id>-in-<directory name>`.
const here = basename(process.cwd());
const forkIdx = args.indexOf('--fork');
if (forkIdx >= 0) sessionId = `${args[forkIdx + 1]}-in-${here}`;
// Opening such a copy from any other directory is what the real pi refuses to do without asking.
const home = /-in-([^-]+)(?:-fork-e\d+)?$/.exec(sessionId)?.[1];
if (sessionIdx >= 0 && home && home !== here) {
  console.log('Session found in different project');
  console.log('Fork this session into current directory? [y/N] ');
  console.log('Aborted.');
  process.exit(0);
}
const out = (obj) => process.stdout.write(JSON.stringify(obj) + '\n');
let running = false;
let pendingTeamReview = null;
let contextUnknown = false;

// "think" maps like a real provider: null means unsupported, xhigh/max need an explicit mapping.
const MODELS = [
  {
    id: 'think',
    provider: 'fake',
    name: 'Think',
    reasoning: true,
    input: ['text', 'image'],
    thinkingLevelMap: { off: null, minimal: null, low: 'low', medium: null, high: 'high', xhigh: null, max: 'max' },
  },
  { id: 'plain', provider: 'fake', name: 'Plain', reasoning: false, input: ['text'] },
  { id: 'loose', provider: 'fake', name: 'Loose', reasoning: true, input: ['text'] },
];
if (process.env.FAKE_PI_MANY_MODELS === '1') MODELS.push(...Array.from({ length: 40 }, (_, i) => ({ id: `model-${i + 1}`, provider: 'fake', name: `Model ${String(i + 1).padStart(2, '0')}`, reasoning: false, input: ['text'] })));
let currentModel = MODELS[0];
let stateReads = 0;
let pendingFirstOutput = null;

const HIST_MESSAGES = [
  { role: 'system', content: '' },
  { role: 'user', content: 'run the tests', timestamp: 1 },
  {
    role: 'assistant',
    content: [
      { type: 'thinking', thinking: 'need to run them' },
      { type: 'toolCall', id: 'c9', name: 'bash', arguments: { command: 'npm test' } },
    ],
  },
  { role: 'toolResult', toolCallId: 'c9', toolName: 'bash', content: [{ type: 'text', text: '12 passed' }], isError: false },
  { role: 'assistant', content: [{ type: 'text', text: 'All 12 pass.' }] },
  { role: 'user', content: [{ type: 'text', text: 'and this picture?' }, { type: 'image', data: 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', mimeType: 'image/png' }], timestamp: 2 },
  { role: 'assistant', content: [{ type: 'text', text: 'A single pixel.' }] },
];

const userText = (m) => (typeof m.content === 'string' ? m.content : (m.content ?? []).filter((b) => b.type === 'text').map((b) => b.text).join(''));

/**
 * What a session holds. Tests can script what the "terminal" did to one: a JSON file of messages
 * per session id. A session named there outright uses that script, which is how a copy's own
 * history is scripted after it was made. Otherwise a session made by `fork` holds what its source
 * did before the message it was forked from. Sessions named hist… hold a canned conversation.
 */
function messagesOf(id) {
  const scripted = () => {
    if (!process.env.FAKE_PI_MESSAGES_FILE) return null;
    try {
      const all = JSON.parse(readFileSync(process.env.FAKE_PI_MESSAGES_FILE, 'utf8'));
      return Array.isArray(all[id]) ? all[id] : null;
    } catch {
      return null;
    }
  };
  const own = scripted();
  if (own) return own;
  const forked = /^(.*)-fork-e(\d+)$/.exec(String(id));
  if (forked) {
    const source = messagesOf(forked[1]);
    let seen = 0;
    const cut = source.findIndex((m) => m.role === 'user' && ++seen === Number(forked[2]));
    return cut < 0 ? source : source.slice(0, cut);
  }
  // A copy made into another directory holds what its source holds.
  const moved = /^(.*)-in-[^-]+$/.exec(String(id));
  if (moved) return messagesOf(moved[1]);
  return String(id).startsWith('hist') ? HIST_MESSAGES : [];
}

/** Where scriptedTurn left off reading each session's script, so live turns walk it in order. */
const scriptCursor = new Map();
/**
 * The scripted answer to a live turn: the messages the script holds after the matching user
 * message, up to the next one. Lets FAKE_PI_MESSAGES_FILE drive not just /sync but the live
 * reply too, so a scripted conversation plays out turn for turn. Falls back to the canned
 * answer when the script has no such user message (or no script at all).
 */
function scriptedTurn(id, message) {
  const list = messagesOf(id);
  if (!Array.isArray(list) || list.length === 0) return null;
  let i = scriptCursor.get(id) ?? 0;
  while (i < list.length && !(list[i].role === 'user' && userText(list[i]) === message)) i++;
  if (i >= list.length) return null;
  const blocks = [];
  for (i++; i < list.length && list[i].role !== 'user'; i++) blocks.push(list[i]);
  scriptCursor.set(id, i);
  return blocks;
}

/** The user messages of a session, numbered from one: e1, e2, … */
const forkPoints = (id) =>
  messagesOf(id)
    .filter((m) => m.role === 'user')
    .map((m, i) => ({ entryId: `e${i + 1}`, text: userText(m) }));

const rl = createInterface({ input: process.stdin });
rl.on('line', (line) => {
  const cmd = JSON.parse(line);
  const reply = (data, success = true, error) =>
    out({ id: cmd.id, type: 'response', command: cmd.type, success, ...(success ? { data } : { error }) });

  switch (cmd.type) {
    case 'get_available_models':
      // Probes must not create sessions in pi's session list.
      if (!ephemeral) return reply(null, false, 'probe ran without --no-session');
      if (process.env.FAKE_PI_REQUIRE_NO_MCP === '1' && !args.includes('--no-mcp')) return reply(null, false, 'catalog probe must disable MCP');
      if (process.env.FAKE_PI_CATALOG_FILE) {
        const respond = () => {
          const state = catalogState();
          if (state.hold) return setTimeout(respond, 10);
          if (state.fail) return reply(null, false, 'fixture catalog failure');
          reply({ models: MODELS.map((model, i) => i === 0 && state.label ? { ...model, name: state.label } : model) });
        };
        return respond();
      }
      return reply({ models: MODELS });
    case 'get_commands':
      if (!ephemeral) return reply(null, false, 'probe ran without --no-session');
      return reply({
        commands: [
          { name: 'fix-tests', description: 'Fix failing tests', source: 'prompt' },
          { name: 'skill:pdf', description: 'Work with PDF files', source: 'skill' },
          { name: 'deploy', source: 'extension' },
          // Tests turn on the vision extension (describe_image) with FAKE_PI_VISION=1.
          ...(process.env.FAKE_PI_VISION === '1' ? [{ name: 'vision', description: 'Manage describe_image', source: 'extension' }] : []),
        ],
      });
    case 'set_model':
      currentModel = MODELS.find(m => m.provider === cmd.provider && m.id === cmd.modelId) ?? currentModel;
      return reply(currentModel);
    case 'set_thinking_level': {
      // Some agents refuse a mid-turn change; FAKE_PI_EFFORT_BUSY simulates one, and
      // FAKE_PI_EFFORT_LOG records every attempt so a test can see which reached the agent.
      const entry = JSON.stringify({ level: cmd.level, running }) + '\n';
      if (process.env.FAKE_PI_EFFORT_LOG) appendFileSync(process.env.FAKE_PI_EFFORT_LOG, entry);
      if (process.env.FAKE_PI_EFFORT_BUSY === '1' && running) return reply(null, false, 'a turn is running');
      return reply({});
    }
    case 'get_messages':
      // A slow read lets a test start a hub turn while /sync is still reading the session.
      if (process.env.FAKE_PI_MESSAGES_DELAY_MS) return setTimeout(() => reply({ messages: messagesOf(sessionId) }), Number(process.env.FAKE_PI_MESSAGES_DELAY_MS));
      return reply({ messages: messagesOf(sessionId) });
    // The user messages a copy can be forked from, each with the id `fork` takes.
    case 'get_fork_messages':
      return reply({ messages: forkPoints(sessionId) });
    // A copy that holds everything before one user message; this pi moves over to it.
    case 'fork': {
      const point = forkPoints(sessionId).find((m) => m.entryId === cmd.entryId);
      if (!point) return reply(null, false, `no entry ${cmd.entryId}`);
      sessionId = `${sessionId}-fork-${cmd.entryId}`;
      // A copy can take a moment; FAKE_PI_FORK_DELAY_MS holds one open so a busy state is visible.
      const answer = () => reply({ text: point.text, cancelled: false });
      const delay = Number(process.env.FAKE_PI_FORK_DELAY_MS);
      return delay ? setTimeout(answer, delay) : answer();
    }
    case 'compact':
      if (cmd.customInstructions === 'explode') return reply(null, false, 'nothing to compact');
      out({ type: 'compaction_start', reason: 'manual' });
      contextUnknown = true;
      const complete = () => {
        const result = { summary: `summary ${cmd.customInstructions ?? ''}`.trim(), tokensBefore: 150000, estimatedTokensAfter: 32000 };
        out({ type: 'compaction_end', reason: 'manual', result, aborted: false });
        reply(result);
      };
      const cancelCompact = () => reply(null, false, 'compaction aborted');
      pendingCompact = cancelCompact;
      setTimeout(() => {
        if (pendingCompact !== cancelCompact) return;
        pendingCompact = null;
        if (process.env.FAKE_COMPACT_FAIL) reply(null, false, 'compaction failed');
        else complete();
      }, Number(process.env.FAKE_COMPACT_DELAY ?? 20));
      return;
    case 'get_session_stats':
      return reply({
        sessionId,
        userMessages: 2,
        assistantMessages: 2,
        toolCalls: 3,
        tokens: { input: 900000, output: 300000, cacheRead: 400000, cacheWrite: 100000, total: 1700000 },
        cost: 0.02,
        contextUsage: { tokens: contextUnknown ? null : 1200, contextWindow: 200000, percent: contextUnknown ? null : 0.6 },
      });
    case 'clone':
      if (sessionId === 'uncloneable') return reply({ cancelled: true });
      sessionId = `${sessionId}-clone`;
      // FAKE_PI_FORK_DELAY_MS holds a copy open too, so a busy state is visible.
      {
        const answer = () => reply({ cancelled: false });
        const delay = Number(process.env.FAKE_PI_FORK_DELAY_MS);
        return delay ? setTimeout(answer, delay) : answer();
      }
    case 'get_state':
      if (++stateReads > 1 && process.env.FAKE_PI_IMAGE_CHECK_DELAY_MS) {
        return setTimeout(() => reply({ sessionId, isStreaming: running, model: currentModel }), Number(process.env.FAKE_PI_IMAGE_CHECK_DELAY_MS));
      }
      return reply({ sessionId, isStreaming: running, model: currentModel });
    case 'prompt': {
      if (running) return reply(null, false, 'already streaming');
      if (cmd.message.startsWith('auto-compact')) {
        reply({ disposition: 'started' });
        const prefix = cmd.message.includes('legacy') ? 'auto_' : '';
        for (let i = 0; i < (cmd.message.includes('twice') ? 2 : 1); i++) {
          out({ type: `${prefix}compaction_start`, reason: 'threshold' });
          out({ type: `${prefix}compaction_end`, reason: 'threshold', aborted: cmd.message.includes('cancel'),
            ...(/\bfail\b/.test(cmd.message) ? { errorMessage: 'summary failed' } : cmd.message.includes('cancel') || cmd.message.includes('skip') ? {} : { result: { summary: 'summary', tokensBefore: 150000 } }) });
        }
        out({ type: 'message_update', assistantMessageEvent: { type: 'text_delta', delta: '继续处理。' } });
        out({ type: 'agent_settled', aborted: false });
        return;
      }
      // A file gate lets team tests keep a reviewer working across pause/continue without timing races.
      if (cmd.message.split('<calli-team>')[0].trim().endsWith('TEAM_REVIEW_WAIT')) {
        reply({ disposition: 'started' });
        out({ type: 'agent_start' });
        running = true;
        const file = 'team-review-release';
        const finish = () => {
          if (!pendingTeamReview) return;
          let result;
          try { result = readFileSync(file, 'utf8').trim(); } catch { return; }
          pendingTeamReview(); pendingTeamReview = null;
          if (result === 'error') out({ type: 'message_end', message: { role: 'assistant', stopReason: 'error', errorMessage: 'reviewer exploded' } });
          else out({ type: 'message_update', assistantMessageEvent: { type: 'text_delta', contentIndex: 0, delta: 'Reviewed TEAM_APPROVED' } });
          running = false;
          out({ type: 'agent_settled', aborted: false });
        };
        pendingTeamReview = () => unwatchFile(file, finish);
        watchFile(file, { interval: 20 }, finish);
        finish();
        return;
      }
      const personal = personalFixture(cmd.message);
      if (personal === '__hold__') {
        // A fixture answer that never settles: only a cancellation can end this turn.
        reply({ disposition: 'started' });
        out({ type: 'agent_start' });
        running = true;
        return;
      }
      if (personal === '__explore_error__') {
        reply({ disposition: 'started' });
        out({ type: 'agent_start' });
        out({ type: 'message_end', message: { role: 'assistant', stopReason: 'error', errorMessage: 'explore provider unavailable' } });
        out({ type: 'agent_settled', aborted: false });
        return;
      }
      if (personal !== null) {
        reply({ disposition: 'started' });
        out({ type: 'agent_start' });
        out({ type: 'message_update', assistantMessageEvent: { type: 'text_delta', contentIndex: 0, delta: personal } });
        out({ type: 'agent_settled', aborted: false }); return;
      }
      const collaboration = coordinationFixture(cmd.message);
      if (collaboration !== null) {
        reply({ disposition: 'started' });
        out({ type: 'agent_start' });
        out({ type: 'message_update', assistantMessageEvent: { type: 'text_delta', contentIndex: 0, delta: collaboration } });
        out({ type: 'agent_settled', aborted: false });
        return;
      }

      contextUnknown = false;
      running = true;
      reply({ disposition: 'started' });
      out({ type: 'agent_start' });
      if (cmd.message.includes('wait-first')) {
        out({ type: 'message_start', message: { role: 'assistant', timestamp: Date.now() } });
        pendingFirstOutput = setTimeout(() => {
          pendingFirstOutput = null;
          if (!running) return;
          if (cmd.message.includes('wait-first-fail')) {
            out({ type: 'message_end', message: { role: 'assistant', stopReason: 'error', errorMessage: 'provider unavailable' } });
          } else {
            out({ type: 'message_update', assistantMessageEvent: { type: 'thinking_delta', delta: 'Checking the request.' } });
            out({ type: 'message_update', assistantMessageEvent: { type: 'text_delta', delta: 'Response received.' } });
          }
          running = false;
          out({ type: 'agent_settled', aborted: false });
        }, Number(process.env.FAKE_PI_FIRST_OUTPUT_DELAY_MS ?? 3000));
        return;
      }
      if (cmd.message.includes('inspect-work-mode')) {
        out({ type: 'message_update', assistantMessageEvent: { type: 'text_delta', delta: JSON.stringify({ message: cmd.message, images: cmd.images ?? [] }) } });
        running = false;
        return out({ type: 'agent_settled', aborted: false });
      }
      if (cmd.message.startsWith('[Hub work mode: plan]')) {
        out({ type: 'message_update', assistantMessageEvent: { type: 'text_delta', delta: '设计方案：\n\n1. 阅读相关代码，确认改动范围。\n2. 实现修改并运行测试。\n\n等你要求开始执行。' } });
        running = false;
        return out({ type: 'agent_settled', aborted: false });
      }
      out({ type: 'message_update', assistantMessageEvent: { type: 'thinking_delta', contentIndex: 0, delta: 'hmm' } });
      // "slowthink" spaces the turn out, so a UI test can watch it run. It interleaves tool calls
      // with text on purpose: a group, then text, then another group — the order the view must
      // keep instead of hoisting every step above the answer.
      if (cmd.message.includes('slowthink')) {
        setTimeout(() => {
          out({ type: 'tool_execution_start', toolCallId: 'c1', toolName: 'bash', args: { command: 'ls' } });
          out({ type: 'tool_execution_end', toolCallId: 'c1', toolName: 'bash', result: { content: [{ type: 'text', text: 'a.txt' }] }, isError: false });
        }, 500);
        setTimeout(() => {
          out({ type: 'message_update', assistantMessageEvent: { type: 'text_delta', contentIndex: 0, delta: 'hi there ' + cmd.message } });
        }, 1300);
        setTimeout(() => {
          out({ type: 'tool_execution_start', toolCallId: 'c2', toolName: 'bash', args: { command: 'npm test' } });
        }, 2100);
        setTimeout(() => {
          out({ type: 'tool_execution_end', toolCallId: 'c2', toolName: 'bash', result: { content: [{ type: 'text', text: '12 passed' }] }, isError: false });
          running = false;
          out({ type: 'agent_settled', aborted: false });
        }, 2900);
        return;
      }
      // "longthink" streams one long thought a sentence at a time, so a UI test can watch the live
      // thinking window follow it. The stream runs well past the window's three lines, and each
      // sentence starts short: exactly the writing that used to bounce the page up and down.
      if (cmd.message.includes('longthink')) {
        const sentences = [
          '先把请求里提到的文件找出来，确认它们各自管什么。',
          '路由注册在一个地方，但每个页面的数据是在组件里单独取的。',
          '这样改的话，列表页要跟着动，先把列表页读完再决定。',
          '列表页把筛选条件直接写进了 useState，刷新以后就丢了。',
          '筛选条件得搬到 URL 的查询参数里，刷新和分享才都能保住。',
          '类目是从接口拉的，价格是本地输入，两种参数的编码方式不一样。',
          '中文类目名要 encodeURIComponent，不然分享出去的链接会坏。',
          '价格区间用 min 和 max 两个参数，空的那边不传。',
          '再把默认排序也带上，不传就按后端默认的来。',
          '组件这边，把读参数和写参数封成一个 hook，页面里就干净了。',
          'hook 里用 useSearchParams，配合 Suspense 边界，不然整个页面的渲染都要降级。',
          '改动范围差不多定了：hook 一个文件，列表页一个文件。',
          '最后过一遍现有的测试，看有没有直接断言旧 URL 结构的。',
          '有两处，改完一起更新掉，再补一条带中文类目的用例。',
        ];
        sentences.forEach((s, i) => setTimeout(() => {
          out({ type: 'message_update', assistantMessageEvent: { type: 'thinking_delta', contentIndex: 0, delta: s + ' ' } });
          if (i === sentences.length - 1) {
            setTimeout(() => {
              out({ type: 'message_update', assistantMessageEvent: { type: 'text_delta', contentIndex: 0, delta: '想清楚了：筛选进 URL，封装成 hook，改动两个文件。' } });
              running = false;
              out({ type: 'agent_settled', aborted: false });
            }, 300);
          }
        }, 250 + i * 250));
        return;
      }
      // "manysteps" is a turn that looks around a lot before it says anything: four calls in a
      // row, a remark, then two more with the last one slow. The view folds each finished run of
      // calls into one line that counts them, and keeps the call in flight on screen.
      if (cmd.message.includes('manysteps')) {
        const call = (id, toolName, args, text) => {
          out({ type: 'tool_execution_start', toolCallId: id, toolName, args });
          if (text !== undefined) out({ type: 'tool_execution_end', toolCallId: id, toolName, result: { content: [{ type: 'text', text }] }, isError: false });
        };
        setTimeout(() => {
          call('m1', 'read', { path: 'src/a.ts' }, 'export const a = 1;');
          call('m2', 'read', { path: 'src/b.ts' }, 'export const b = 2;');
          call('m3', 'grep', { pattern: 'TODO' }, 'src/a.ts:3');
          call('m4', 'bash', { command: 'ls src' }, 'a.ts b.ts');
        }, 200);
        setTimeout(() => {
          out({ type: 'message_update', assistantMessageEvent: { type: 'text_delta', contentIndex: 0, delta: 'looked around, now checking: ' } });
        }, 700);
        setTimeout(() => {
          call('m5', 'bash', { command: 'npm run lint' }, 'clean');
          call('m6', 'bash', { command: 'npm test' });
        }, 1200);
        setTimeout(() => {
          out({ type: 'tool_execution_end', toolCallId: 'm6', toolName: 'bash', result: { content: [{ type: 'text', text: '12 passed' }] }, isError: false });
          out({ type: 'message_update', assistantMessageEvent: { type: 'text_delta', contentIndex: 0, delta: 'all good' } });
          running = false;
          out({ type: 'agent_settled', aborted: false });
        }, 4200);
        return;
      }
      // "forkflow" is the shape of a turn watched on a phone: an edit lands (so the turn has
      // diffs), the agent says something on the way, and then a long command runs. While that
      // command is still running the turn is not finished — a summary of changed files must not
      // show yet, and the live steps must stay visible.
      if (cmd.message.includes('forkflow')) {
        setTimeout(() => {
          out({ type: 'tool_execution_start', toolCallId: 'w1', toolName: 'write', args: { path: 'demo.ts', content: 'export const one = 1;\n' } });
          out({ type: 'tool_execution_end', toolCallId: 'w1', toolName: 'write', result: { content: [{ type: 'text', text: 'ok' }] }, isError: false });
        }, 200);
        setTimeout(() => {
          out({ type: 'message_update', assistantMessageEvent: { type: 'text_delta', contentIndex: 0, delta: 'files done, now the long run: ' } });
        }, 1000);
        setTimeout(() => {
          out({ type: 'tool_execution_start', toolCallId: 'c9', toolName: 'bash', args: { command: 'npm test' } });
        }, 1800);
        setTimeout(() => {
          out({ type: 'tool_execution_end', toolCallId: 'c9', toolName: 'bash', result: { content: [{ type: 'text', text: '12 passed' }] }, isError: false });
          running = false;
          out({ type: 'agent_settled', aborted: false });
        }, 6000);
        return;
      }
      // "longrun" makes a turn with more work entries than one work page holds (100) and stays
      // up for a while: opening it mid-run must page through the running work by itself, and a
      // finished reopen keeps the manual Load more. A word after every call keeps each step its
      // own row instead of folding the finished run into one summary line.
      if (cmd.message.includes('longrun')) {
        setTimeout(() => {
          for (let i = 1; i <= 140; i++) {
            out({ type: 'tool_execution_start', toolCallId: 'L' + i, toolName: 'bash', args: { command: 'scan-' + i } });
            out({ type: 'tool_execution_end', toolCallId: 'L' + i, toolName: 'bash', result: { content: [{ type: 'text', text: 'ok ' + i }] }, isError: false });
            out({ type: 'message_update', assistantMessageEvent: { type: 'text_delta', contentIndex: 0, delta: `part ${i} ` } });
          }
        }, 300);
        setTimeout(() => {
          out({ type: 'message_update', assistantMessageEvent: { type: 'text_delta', contentIndex: 0, delta: 'long run done' } });
          running = false;
          out({ type: 'agent_settled', aborted: false });
        }, 12000);
        return;
      }
      // A scripted turn plays out its blocks in order: thinking, tool calls with results, text.
      const script = scriptedTurn(sessionId, cmd.message);
      if (script) {
        for (const m of script) {
          const content = Array.isArray(m.content) ? m.content : [];
          for (const block of content) {
            if (block.type === 'thinking') out({ type: 'message_update', assistantMessageEvent: { type: 'thinking_delta', contentIndex: 0, delta: block.thinking } });
            else if (block.type === 'toolCall') out({ type: 'tool_execution_start', toolCallId: block.id, toolName: block.name, args: block.arguments });
            else if (block.type === 'text') out({ type: 'message_update', assistantMessageEvent: { type: 'text_delta', contentIndex: 0, delta: block.text } });
          }
          if (m.role === 'toolResult') out({ type: 'tool_execution_end', toolCallId: m.toolCallId, toolName: m.toolName, result: { content: m.content }, isError: m.isError === true });
        }
        running = false;
        return out({ type: 'agent_settled', aborted: false });
      }
      // With FAKE_PI_TWO_REQUESTS the turn looks like pi's real shape: one request decides on the
      // tool, the tool runs, a second request writes the answer, each with its own usage — so a
      // session's model segments and tools alternate on the timeline instead of overlapping.
      const twoRequests = process.env.FAKE_PI_TWO_REQUESTS === '1';
      if (twoRequests) {
        out({ type: 'message_start', message: { role: 'assistant', timestamp: Date.now() - 150 } });
        out({ type: 'message_update', assistantMessageEvent: { type: 'text_delta', contentIndex: 0, delta: '先看一眼目录。' } });
        out({ type: 'message_end', message: { role: 'assistant', model: 'fixture-model', provider: 'fixture-provider', stopReason: 'tool_use', durationMs: 300, usage: { input: 80, output: 12, cacheRead: 20, cacheWrite: 5, totalTokens: 117 } } });
        // The tool takes a beat, and the second request takes longer than the first.
        setTimeout(() => {
          out({ type: 'tool_execution_start', toolCallId: 'c1', toolName: 'bash', args: { command: 'ls' } });
          out({ type: 'tool_execution_update', toolCallId: 'c1', toolName: 'bash', partialResult: { content: [{ type: 'text', text: 'partial' }] } });
          out({ type: 'tool_execution_end', toolCallId: 'c1', toolName: 'bash', result: { content: [{ type: 'text', text: 'a.txt' }] }, isError: false });
          setTimeout(() => {
            if (cmd.images?.length) {
              out({ type: 'message_update', assistantMessageEvent: { type: 'text_delta', contentIndex: 0, delta: `images:${cmd.images.length}:${cmd.images.map((i) => i.mimeType).join(',')};` } });
            }
            out({ type: 'message_start', message: { role: 'assistant', timestamp: Date.now() - 200 } });
            out({ type: 'message_update', assistantMessageEvent: { type: 'text_delta', contentIndex: 0, delta: 'hi ' } });
            const marker = process.env.FAKE_PI_LABEL ? `[${process.env.FAKE_PI_LABEL}] ` : '';
            out({ type: 'message_update', assistantMessageEvent: { type: 'text_delta', contentIndex: 0, delta: marker + 'there ' + cmd.message } });
            if (/\bfail\b/.test(cmd.message)) {
              out({ type: 'message_end', message: { role: 'assistant', stopReason: 'error', errorMessage: 'provider exploded' } });
              running = false;
              return out({ type: 'agent_settled', aborted: false });
            }
            if (process.env.FAKE_TELEMETRY) out({ type: 'message_end', message: { role: 'assistant', model: 'fixture-model', provider: 'fixture-provider', stopReason: 'stop', durationMs: 600, usage: { input: 120, output: 20, cacheRead: 40, cacheWrite: 10, reasoning: 5, totalTokens: 175, cost: { total: 0.0123 } } } });
            running = false;
            out({ type: 'agent_settled', aborted: false });
          }, 400);
        }, 300);
        return;
      }
      out({ type: 'tool_execution_start', toolCallId: 'c1', toolName: 'bash', args: { command: 'ls' } });
      out({ type: 'tool_execution_update', toolCallId: 'c1', toolName: 'bash', partialResult: { content: [{ type: 'text', text: 'partial' }] } });
      out({ type: 'tool_execution_end', toolCallId: 'c1', toolName: 'bash', result: { content: [{ type: 'text', text: 'a.txt' }] }, isError: false });
      if (cmd.images?.length) {
        out({ type: 'message_update', assistantMessageEvent: { type: 'text_delta', contentIndex: 0, delta: `images:${cmd.images.length}:${cmd.images.map((i) => i.mimeType).join(',')};` } });
      }
      if (process.env.FAKE_TELEMETRY) out({ type: 'message_start', message: { role: 'assistant', timestamp: Date.now() - 250 } });
      out({ type: 'message_update', assistantMessageEvent: { type: 'text_delta', contentIndex: 0, delta: 'hi ' } });
      const marker = process.env.FAKE_PI_LABEL ? `[${process.env.FAKE_PI_LABEL}] ` : '';
      out({ type: 'message_update', assistantMessageEvent: { type: 'text_delta', contentIndex: 0, delta: marker + 'there ' + cmd.message } });
      // Whole word only: protocol envelopes (the Router's INTRO names push.failure) must not
      // turn an unrelated reply into a failure, while fail markers anywhere (work fail,
      // router-fixture-fail in a signal ref) still do.
      if (/\bfail\b/.test(cmd.message)) {
        out({ type: 'message_end', message: { role: 'assistant', stopReason: 'error', errorMessage: 'provider exploded' } });
        running = false;
        return out({ type: 'agent_settled', aborted: false });
      }
      if (process.env.FAKE_TELEMETRY) out({ type: 'message_end', message: { role: 'assistant', model: 'fixture-model', provider: 'fixture-provider', stopReason: 'stop', durationMs: 1250, usage: { input: 100, output: 20, cacheRead: 40, cacheWrite: 10, reasoning: 5, totalTokens: 170, cost: { total: 0.0123 } } } });
      if (cmd.message.includes('slow')) return;
      running = false;
      return out({ type: 'agent_settled', aborted: false });
    }
    case 'abort':
      if (pendingFirstOutput) { clearTimeout(pendingFirstOutput); pendingFirstOutput = null; }
      if (pendingTeamReview) { pendingTeamReview(); pendingTeamReview = null; }
      if (pendingCompact) { const cancelCompact = pendingCompact; pendingCompact = null; cancelCompact(); }
      reply({});
      if (running) {
        running = false;
        out({ type: 'agent_settled', aborted: true });
      }
      return;
    default:
      return reply(null, false, `unknown command ${cmd.type}`);
  }
});
