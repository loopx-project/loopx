#!/usr/bin/env node
if (process.argv.includes('--version')) { process.stdout.write('fixture 1.2.3\n'); process.exit(0); }
// Fake Codex app-server for tests. Speaks newline-delimited JSON-RPC on stdio, the way
// `codex app-server` does. Prompt text drives behaviour: "tool" runs a command, "edit" changes a
// file, "approve" asks for a command approval, "deny" offers a plain decline, "slow" waits until
// interrupted, "crash" exits. Settings ride with turn/start the way the adapter sends them.
import { coordinationFixture } from './coordination.mjs';
import { appendFileSync, readFileSync, writeFileSync, cpSync, existsSync, mkdirSync } from 'node:fs';
import { basename, dirname, join } from 'node:path';
import { createInterface } from 'node:readline';

// Optional on-disk generated memory, read by the adapter without an RPC or a new turn.
if (process.env.FAKE_CODEX_MEMORY_FIXTURE === '1' && process.env.CODEX_HOME) {
  const memories = join(process.env.CODEX_HOME, 'memories');
  if (!existsSync(memories)) {
    mkdirSync(process.env.CODEX_HOME, { recursive: true });
    cpSync(new URL('./codex-memory/', import.meta.url), memories, { recursive: true });
  }
}

const rl = createInterface({ input: process.stdin });
const out = (obj) => process.stdout.write(JSON.stringify(obj) + '\n');

let threadId = process.env.FAKE_CODEX_THREAD ?? 'codex-thread-1';
// A rollout file under this account's CODEX_HOME, the way the real CLI keeps one, so the copy-in
// flow has something on disk to read: session meta first, one line per turn after it.
const rolloutPath = () => {
  if (!process.env.CODEX_HOME) return null;
  const day = new Date().toISOString().slice(0, 10).split('-');
  return join(process.env.CODEX_HOME, 'sessions', day[0], day[1], day[2], `rollout-fixture-${threadId}.jsonl`);
};
const ensureRollout = () => {
  const path = rolloutPath();
  if (!path || existsSync(path)) return;
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify({ timestamp: new Date().toISOString(), type: 'session_meta', payload: { id: threadId, cwd: '/tmp/one' } }) + '\n');
};
const appendRollout = (text) => {
  ensureRollout();
  const path = rolloutPath();
  if (path) appendFileSync(path, JSON.stringify({ timestamp: new Date().toISOString(), type: 'event_msg', payload: { type: 'user_message', message: text } }) + '\n');
};
let nextTurnSeq = 1;
let billedTokens = 900000;
let nextServerRequestId = 1000;
let voiceAfterPrompt = Boolean(process.env.FAKE_CODEX_VOICE_AFTER_PROMPT);
let activeTurn = null; // { id }
let voiceInstructions = '';
let speechSequence = 0;
// Settings as the adapter sets them: thread/start takes kebab-case sandbox, turn/start camelCase.
let currentWorkMode = 'default';
const awaitingInput = new Map();
const planResult = (turnId) => {
  notify('item/plan/delta', { turnId, itemId: 'proposal', delta: '## 实施计划\n\n正在整理…' });
  notify('item/completed', { turnId, item: { type: 'plan', id: 'proposal', text: '## 实施计划\n\n1. 阅读现有实现，列出所有调用点\n2. 完成改动并验证\n3. 跑一遍接口测试，记录结果\n4. 补齐两处文案的中英文\n5. 更新 README 的说明段落\n6. 回归一遍登录与支付两条主链路\n7. 清理迁移期间留下的临时开关\n8. 把已知限制写进迁移说明的末尾\n9. 检查一遍日志里的新告警\n10. 交给下一个人验收\n\n保留当前会话和权限设置。' } });
};
let currentModel = 'gpt-big';
let currentEffort = 'high';
let currentSpeed = null;
let currentSandbox = 'read-only';
let currentApproval = 'on-request';
let currentApprovalsReviewer = 'user';
let activeApprovalsReviewer = null;
let currentWebSearch = null;
let dynamicTools = [];
const awaitingTool = new Map();
let goal = process.env.FAKE_CODEX_ACTIVE_GOAL ? { objective: 'Saved target', status: 'active' } : null;
let goalGeneration = 0;

const runGoal = (generation, round = 1) => {
  if (generation !== goalGeneration || goal?.status !== 'active') return;
  const turnId = `goal-${nextTurnSeq++}`;
  activeTurn = turnId;
  activeApprovalsReviewer = currentApprovalsReviewer;
  notify('turn/started', { turn: { id: turnId, status: 'inProgress' } });
  if (goal.objective.includes('crash')) return process.exit(2);
  if (goal.objective.includes('slow')) return;
  setTimeout(() => {
    if (generation !== goalGeneration || activeTurn !== turnId) return;
    runTurn(round === 2 && goal.objective.includes('approve') ? 'approve' : `目标第 ${round} 轮`, turnId, () => {
      if (activeTurn !== turnId) return;
      activeTurn = null;
      if (round === 2) {
        goal = { ...goal, status: goal.objective.includes('blocked') ? 'blocked' : 'complete', tokensUsed: 42 };
        // Both notification orders occur across server versions.
        if (process.env.FAKE_CODEX_GOAL_LATE) {
          finishTurn(turnId, 'completed');
          notify('thread/goal/updated', { goal });
        } else {
          notify('thread/goal/updated', { goal });
          finishTurn(turnId, 'completed');
        }
      } else {
        finishTurn(turnId, 'completed');
        setTimeout(() => runGoal(generation, 2), 30);
      }
    });
  }, 20);
};

const sandboxName = () => ({ readOnly: 'read-only', workspaceWrite: 'workspace-write', dangerFullAccess: 'danger-full-access' })[currentSandbox] ?? currentSandbox;

const notify = (method, params) => out({ jsonrpc: '2.0', method, params: { threadId, ...params } });

const MODELS = [
  {
    id: 'gpt-big',
    model: 'gpt-big',
    displayName: 'GPT Big',
    description: 'For hard work',
    isDefault: true,
    ...(process.env.FAKE_CODEX_SPEED ? { serviceTiers: [{ id: 'priority', name: 'Fast', description: '2x speed, increased usage' }, { id: 'ultrafast', name: 'Ultrafast', description: 'More speed' }] } : {}),
    inputModalities: ['text', 'image'],
    supportedReasoningEfforts: [{ reasoningEffort: 'low' }, { reasoningEffort: 'high' }, { reasoningEffort: 'max' }, { reasoningEffort: 'ultra' }],
  },
  {
    id: 'gpt-small',
    model: 'gpt-small',
    displayName: 'GPT Small',
    description: '',
    inputModalities: ['text'],
    supportedReasoningEfforts: [{ reasoningEffort: 'low' }, { reasoningEffort: 'high' }],
  },
  { id: 'gpt-hidden', model: 'gpt-hidden', displayName: 'Hidden', hidden: true, inputModalities: ['text'], supportedReasoningEfforts: [] },
  ...(process.env.FAKE_CODEX_PRICE_MODELS ? ['gpt-6-astra', 'openrouter-3o', 'openrouter-4o', 'Doubao-Seed-Evolving', 'seed-evol', 'unlisted-model']
    .map(model => ({ id: model, model, displayName: model, inputModalities: ['text'], supportedReasoningEfforts: [] })) : []),
];

// Every conversation the fake has on record, two per page so the adapter's paging is exercised.
const THREADS = process.env.FAKE_CODEX_SESSIONS_FILE ? JSON.parse(readFileSync(process.env.FAKE_CODEX_SESSIONS_FILE, 'utf8')) : [
  { id: 'codex-thread-1', cwd: '/tmp/one', name: 'Named chat', preview: '', updatedAt: 1791648439 },
  { id: 'codex-thread-2', cwd: '/tmp/two', name: null, preview: 'How do I…', updatedAt: 1791648000 },
  { id: 'codex-thread-3', cwd: '/tmp/three', name: 'Third', preview: '', updatedAt: 1791647000 },
];

/** A past conversation, as thread/turns/list reports it. */
const historyTurns = () => [
  {
    id: 'turn-1',
    status: 'completed',
    items: [
      { type: 'userMessage', id: 'u1', content: [{ type: 'text', text: 'first question' }] },
      { type: 'reasoning', id: 'r1', content: [{ type: 'text', text: 'let me look' }], summary: [] },
      { type: 'commandExecution', id: 'exec-1', command: 'ls', cwd: '/tmp/one', status: 'completed', exitCode: 0, aggregatedOutput: 'a.txt' },
      { type: 'fileChange', id: 'fc-1', status: 'completed', changes: [{ path: '/tmp/one/notes.md', kind: { type: 'update' }, diff: '@@ -1 +1 @@\n-a\n+b\n' }] },
      { type: 'agentMessage', id: 'm1', text: 'first answer', phase: 'final_answer' },
    ],
  },
  {
    id: 'turn-2',
    status: 'completed',
    items: [
      { type: 'userMessage', id: 'u2', content: [{ type: 'text', text: 'second question' }] },
      { type: 'plan', id: 'p1', text: '- [x] Read\n- [ ] Write\n' },
      { type: 'agentMessage', id: 'm2', text: 'second answer', phase: 'final_answer' },
    ],
  },
];

const applyTurnOverrides = (p) => {
  if ('serviceTier' in p) currentSpeed = p.serviceTier;
  if (typeof p.model === 'string') currentModel = p.model;
  if (typeof p.effort === 'string') currentEffort = p.effort;
  if (typeof p.approvalPolicy === 'string') currentApproval = p.approvalPolicy;
  if (typeof p.approvalsReviewer === 'string') currentApprovalsReviewer = p.approvalsReviewer;
  if (p.sandboxPolicy?.type) currentSandbox = p.sandboxPolicy.type;
};

const say = (text) => {
  const itemId = `msg-${nextTurnSeq}`;
  notify('item/started', { item: { type: 'agentMessage', id: itemId, text: '', phase: 'final_answer' } });
  for (const part of text.match(/.{1,4}/g) ?? []) notify('item/agentMessage/delta', { itemId, delta: part });
  notify('item/completed', { item: { type: 'agentMessage', id: itemId, text, phase: 'final_answer' } });
};

/** The scripted part of one turn. `done` finishes it. */
const runTurn = (text, turnId, done) => {
  const approvalsReviewer = activeTurn === turnId ? activeApprovalsReviewer ?? currentApprovalsReviewer : currentApprovalsReviewer;
  const userItem = { type: 'userMessage', id: `user-${nextTurnSeq}`, content: [{ type: 'text', text }] };
  notify('item/started', { item: userItem, turnId });
  notify('item/completed', { item: userItem, turnId });

  if (text.startsWith('[语音结束总结]')) {
    say('## 这次语音里定下的事\n\n已根据本次对话整理。\n\n### 任务结果\n请参阅线程中的任务结果。\n\n### 下一步\n继续处理待办。');
    done(); return;
  }
  if (text === 'voice-final-news' || text === 'completed-only') {
    const search = { type: 'webSearch', id: 'voice-search', query: 'latest technology news' };
    notify('item/started', { turnId, item: search });
    notify('item/completed', { turnId, item: { ...search, status: 'completed' } });
    const item = { type: 'agentMessage', id: 'news-answer', text: '', phase: 'final_answer' };
    notify('item/started', { turnId, item });
    if (text !== 'completed-only') notify('item/agentMessage/delta', { turnId, itemId: item.id, delta: 'Here are the newz' });
    const final = { ...item, text: '## 科技新闻\n\n**测试新闻标题**\n\n这是排版后的最终回答。\n\n[来源](https://example.com/news)' };
    notify('item/completed', { turnId, item: final });
    notify('item/completed', { turnId, item: final });
    done();
    return;
  }

  if (text.startsWith('auto-compact')) {
    const tool = (id) => {
      const item = { type: 'commandExecution', id, command: 'pwd', status: 'completed', exitCode: 0 };
      notify('item/started', { turnId, item }); notify('item/completed', { turnId, item });
    };
    tool('before-compact');
    tool('before-compact-2');
    const item = { type: 'contextCompaction', id: 'auto-compact-1' };
    notify('item/started', { turnId: 'unrelated-turn', item: { ...item, id: 'wrong-turn' } });
    notify('item/completed', { turnId, threadId: 'unrelated-thread', item: { ...item, id: 'wrong-thread' } });
    notify('item/started', { turnId, item });
    if (text.includes('pending')) return;
    setTimeout(() => {
      notify('item/completed', { turnId, item });
      notify('item/completed', { turnId, item }); // Duplicate delivery must not add a second row.
      notify('thread/compacted', { turnId }); // Deprecated alias of the same completion.
      tool('after-compact');
      tool('after-compact-2');
      say('压缩后继续处理。');
      done();
    }, Number(process.env.FAKE_AUTO_COMPACT_DELAY ?? 20));
    return;
  }

  if (text.startsWith('plan-question')) {
    const id = nextServerRequestId++;
    awaitingInput.set(id, { turnId, done });
    out({ jsonrpc: '2.0', id, method: 'item/tool/requestUserInput', params: { threadId, turnId, itemId: 'question', questions: [{ id: 'scope', header: '范围', question: '优先支持哪一端？', options: [{ label: '桌面和手机', description: '同时覆盖两种屏幕' }, { label: '先做桌面', description: '先完成桌面流程' }], isOther: true }] } });
    if (text.endsWith('expired')) setTimeout(() => { awaitingInput.delete(id); notify('serverRequest/resolved', { requestId: id }); done(); }, 120);
    return;
  }
  if (text === 'speed?') { say(`model:${currentModel};effort:${currentEffort};speed:${currentSpeed};mode:${currentWorkMode};approval:${currentApproval}`); done(); return; }
  if (text === 'plan-draft') { say(`mode:${currentWorkMode};model:${currentModel};effort:${currentEffort};approval:${currentApproval}`); planResult(turnId); done(); return; }
  const collaboration = coordinationFixture(text);
  if (collaboration !== null) { say(collaboration); done(); return; }

  if (process.env.FAKE_CODEX_COMPUTER && text.startsWith('Use calli_computer')) {
    const mode = process.env.FAKE_CODEX_COMPUTER;
    if (!dynamicTools.some(t => t.type === 'function' && t.name === 'calli_computer')) { say('Missing dynamic tool.'); done(); return; }
    const url = /navigating to (\S+)/.exec(text)?.[1];
    const actions = mode === 'no-evidence' ? [] : [
      { action: 'navigate', url }, { action: 'click', x: 150, y: 120 },
      { action: 'type', text: 'Calli' }, { action: 'click', x: 100, y: 180 }, { action: 'finish' },
    ];
    void (async () => {
      for (const args of actions) {
        const id = nextServerRequestId++, callId = `computer-${id}`;
        const item = { type: 'dynamicToolCall', id: callId, tool: 'calli_computer', arguments: args, status: 'inProgress' };
        notify('item/started', { turnId, item });
        const response = new Promise(resolve => awaitingTool.set(id, resolve));
        out({ jsonrpc: '2.0', id, method: 'item/tool/call', params: { threadId, turnId: mode === 'wrong-turn' ? 'wrong-turn' : turnId, callId, tool: 'calli_computer', arguments: args } });
        const result = await response;
        if (activeTurn !== turnId) return;
        notify('item/completed', { turnId, item: { ...item, status: result?.success ? 'completed' : 'failed', ...result } });
        if (!result?.success) { say('Browser action failed.'); done(); return; }
        if (!result.contentItems.some(c => c.type === 'inputImage' && c.imageUrl.startsWith('data:image/png;base64,'))) throw new Error('Missing visual feedback');
        if (mode === 'slow') return;
      }
      say('The browser submitted the form.'); done();
    })();
    return;
  }
  if (process.env.FAKE_CODEX_WEB && (text.startsWith('Search the web using') || text.startsWith('Read this web page using'))) {
    const mode = process.env.FAKE_CODEX_WEB;
    if (currentWebSearch !== 'live') { say('Live native web search was not configured.'); done(); return; }
    const reading = text.startsWith('Read this');
    const action = { type: mode === 'wrong-action' ? (reading ? 'search' : 'openPage') : (reading ? 'openPage' : 'search'),
      ...(reading ? { url: 'https://example.com/' } : { query: 'example domains' }) };
    const item = { type: 'webSearch', id: 'borrowed-web', query: 'example domains' };
    if (mode !== 'no-evidence') {
      notify('item/started', { turnId, item: { ...item, action: { type: 'other' } } });
      notify('item/completed', { turnId, item: { ...item, action,
        results: [{ type: 'text_result', title: 'Example Domain', url: 'https://example.com/', snippet: 'For documentation.' }] } });
    }
    say('For documentation. [Example Domain](https://example.com/)');
    done(); return;
  }
  if (text.includes('crash')) {
    setTimeout(() => process.exit(1), 20);
    return;
  }
  if (text.includes('tool')) {
    const id = 'exec-1';
    const output = text.includes('longoutput') ? Array.from({ length: 120 }, (_, i) => `Output line ${i + 1}: partial out`).join('\n') : 'partial out';
    notify('item/started', { turnId, item: { type: 'commandExecution', id, command: 'npm test', cwd: '/tmp/one', status: 'inProgress' } });
    notify('item/commandExecution/outputDelta', { turnId, itemId: id, delta: 'par' });
    notify('item/commandExecution/outputDelta', { turnId, itemId: id, delta: 'tial' });
    notify('item/completed', { turnId, item: { type: 'commandExecution', id, command: 'npm test', cwd: '/tmp/one', status: 'completed', exitCode: 0, aggregatedOutput: output, durationMs: 12 } });
  }
  if (text.includes('fail')) {
    const id = 'exec-9';
    notify('item/started', { turnId, item: { type: 'commandExecution', id, command: 'false', cwd: '/tmp/one', status: 'inProgress' } });
    notify('item/completed', { turnId, item: { type: 'commandExecution', id, command: 'false', cwd: '/tmp/one', status: 'completed', exitCode: 1, aggregatedOutput: 'boom', durationMs: 3 } });
  }
  if (text.includes('emptyfailure')) {
    const item = { type: 'dynamicToolCall', id: 'empty-failure', namespace: 'fixture', tool: 'no-detail' };
    notify('item/started', { turnId, item: { ...item, status: 'inProgress' } });
    notify('item/completed', { turnId, item: { ...item, status: 'failed', success: false } });
  }
  if (text.includes('emptysuccess')) {
    const item = { type: 'dynamicToolCall', id: 'empty-success', namespace: 'fixture', tool: 'no-output' };
    notify('item/started', { turnId, item: { ...item, status: 'inProgress' } });
    notify('item/completed', { turnId, item: { ...item, status: 'completed', success: true } });
  }
  if (text.includes('livecommand')) {
    notify('item/started', { turnId, item: { type: 'commandExecution', id: 'live-command', command: 'npm run build', status: 'inProgress' } });
    notify('item/commandExecution/outputDelta', { turnId, itemId: 'live-command', delta: 'Compiling…' });
  }
  if (text.includes('edit')) {
    const id = 'fc-1';
    const changes = [{ path: '/tmp/one/src/app.ts', kind: { type: 'update' }, diff: '@@ -1,2 +1,2 @@\n const a = 1;\n-const b = 2;\n+const b = 3;\n' }];
    notify('item/started', { turnId, item: { type: 'fileChange', id, status: 'inProgress', changes: [changes[0]] } });
    // Like the real server: an added file arrives as its plain content, without + markers.
    changes.push({ path: '/tmp/one/src/new.ts', kind: { type: 'add' }, diff: 'export const x = 1;\n' });
    notify('item/fileChange/patchUpdated', { turnId, itemId: id, changes });
    notify('item/completed', { turnId, item: { type: 'fileChange', id, status: 'completed', changes } });
  }
  if (text.includes('reason')) {
    const id = 'reason-1';
    notify('item/started', { turnId, item: { type: 'reasoning', id, content: [], summary: [] } });
    notify('item/reasoning/summaryTextDelta', { turnId, itemId: id, summaryIndex: 0, delta: 'thinking ' });
    notify('item/reasoning/summaryTextDelta', { turnId, itemId: id, summaryIndex: 0, delta: 'hard' });
  }
  if (text.includes('modelqueue')) {
    notify('queue/status', { turnId, state: 'queued', operation: null, position: null, message: null });
    notify('queue/status', {
      turnId,
      state: 'waiting',
      operation: null,
      position: 330,
      message: 'Too many current requests. Your queue position is 330. Please wait for a while.',
    });
  }
  if (text.includes('longplan')) {
    notify('turn/plan/updated', { turnId, plan: [
      { step: 'Confirm the query entry point, the production filters and the current skill names', status: 'completed' },
      { step: 'Pull every root request of the day with its model, skill, subagent and MCP spans', status: 'inProgress' },
      { step: 'Deduplicate by parent span and total tokens, turns and time per skill', status: 'pending' },
      { step: 'Sample the P50, P95 and P99 traces to check parallel runs, retries and recovery', status: 'pending' },
      { step: 'Summarise the ranking, the data limits and what to optimise first', status: 'pending' },
    ] });
  } else if (text.includes('plan')) {
    notify('turn/plan/updated', { turnId, plan: [{ step: 'Read the code', status: 'completed' }, { step: 'Write the fix', status: 'inProgress' }] });
  }
  if (text.includes('model?')) {
    say(`model:${currentModel};effort:${currentEffort};sandbox:${sandboxName()};approval:${currentApproval};reviewer:${approvalsReviewer}`);
    return done();
  }
  if ((text.includes('approve') || text.includes('deny')) && currentApproval !== 'never' && approvalsReviewer === 'auto_review') {
    const id = 'exec-2';
    const reviewId = `review-${nextServerRequestId++}`;
    const denied = text.includes('deny');
    const command = 'rm -rf /tmp/one/build';
    const action = { type: 'command', source: 'shell', command, cwd: '/tmp/one' };
    notify('item/started', { turnId, item: { type: 'commandExecution', id, command, cwd: '/tmp/one', status: 'inProgress' } });
    notify('item/autoApprovalReview/started', { turnId, reviewId, startedAtMs: 1, targetItemId: id,
      review: { status: 'inProgress', riskLevel: null, userAuthorization: null, rationale: null }, action });
    notify('item/autoApprovalReview/completed', { turnId, reviewId, startedAtMs: 1, completedAtMs: 2,
      targetItemId: id, decisionSource: 'agent', review: { status: denied ? 'denied' : 'approved',
        riskLevel: denied ? 'critical' : 'low', userAuthorization: 'high', rationale: denied ? 'too risky' : 'authorized' }, action });
    notify('item/completed', { turnId, item: { type: 'commandExecution', id, command, cwd: '/tmp/one',
      status: denied ? 'failed' : 'completed', exitCode: denied ? 1 : 0, aggregatedOutput: denied ? 'auto-review denied' : 'gone', durationMs: 5 } });
    say(`auto-review:${denied ? 'denied' : 'approved'}`);
    done(); return;
  }
  if ((text.includes('approve') || text.includes('deny')) && currentApproval !== 'never') {
    const id = 'exec-2';
    const command = text.includes('long-approve')
      ? `python3 - <<'PY'\n${Array.from({ length: 60 }, (_, i) => `print("permission detail ${i + 1}")`).join('\n')}\nPY\n# command end`
      : 'rm -rf /tmp/one/build';
    notify('item/started', { turnId, item: { type: 'commandExecution', id, command, cwd: '/tmp/one', status: 'inProgress' } });
    const requestId = nextServerRequestId++;
    const available = text.includes('deny')
      ? ['accept', 'acceptForSession', 'decline', 'cancel']
      : ['accept', { acceptWithExecpolicyAmendment: { execpolicy_amendment: ['rm'] } }, 'cancel'];
    out({
      jsonrpc: '2.0',
      id: requestId,
      method: 'item/commandExecution/requestApproval',
      params: {
        threadId,
        turnId,
        itemId: id,
        kind: 'command',
        command,
        cwd: '/tmp/one',
        proposedExecpolicyAmendment: ['rm'],
        availableDecisions: available,
      },
    });
    // The decision comes back as a response; until then the turn waits.
    awaitingApproval.set(requestId, { itemId: id, command, turnId, done });
    return;
  }
  if (text === 'delayed') { setTimeout(done, 5000); return; }
  if (text.includes('slow')) return; // only turn/interrupt ends this
  say(`echo:${text}`);
  if (process.env.FAKE_TELEMETRY) billedTokens += 150;
  const tokenUsage = process.env.FAKE_TELEMETRY ? { total: { totalTokens: billedTokens, inputTokens: billedTokens * .8, outputTokens: billedTokens * .2, cachedInputTokens: billedTokens * .4 }, last: { totalTokens: 150, inputTokens: 120, outputTokens: 30, cachedInputTokens: 60, reasoningOutputTokens: 10 }, modelContextWindow: 100000 } : text.includes('usage')
    ? {
        total: { totalTokens: text.includes('compacted') ? 1900000 : 1798110 },
        ...(text.includes('missing') ? {} : { last: { totalTokens: text.includes('compacted') ? 12000 : 183510 } }),
        modelContextWindow: 258400,
      }
    : { total: { totalTokens: 500 }, last: { totalTokens: 500 }, modelContextWindow: 100000 };
  notify('thread/tokenUsage/updated', { turnId, tokenUsage });
  done();
};

const awaitingApproval = new Map(); // request id -> the turn waiting for its decision

const finishTurn = (turnId, status, error = null) => {
  notify('turn/completed', { turn: { id: turnId, items: [], itemsView: 'notLoaded', status, error, startedAt: 1, completedAt: 2, durationMs: 1 } });
};

rl.on('line', (line) => {
  const msg = JSON.parse(line);
  if (process.env.FAKE_CODEX_RPC_LOG && msg.method) appendFileSync(process.env.FAKE_CODEX_RPC_LOG, JSON.stringify(msg) + '\n');

  // A decision for an approval we asked about.
  if (msg.id !== undefined && msg.method === undefined) {
    const tool = awaitingTool.get(msg.id);
    if (tool) { awaitingTool.delete(msg.id); tool(msg.result); return; }
    const input = awaitingInput.get(msg.id);
    if (input) {
      awaitingInput.delete(msg.id);
      if (activeTurn !== input.turnId) return;
      say(`answer:${JSON.stringify(msg.result?.answers ?? {})}`);
      notify('serverRequest/resolved', { requestId: msg.id });
      planResult(input.turnId); input.done(); return;
    }
    const pending = awaitingApproval.get(msg.id);
    if (!pending) return;
    awaitingApproval.delete(msg.id);
    const decision = msg.result?.decision;
    const option = typeof decision === 'string' ? decision : Object.keys(decision ?? {})[0];
    notify('serverRequest/resolved', { requestId: msg.id });
    if (option === 'accept' || option === 'acceptWithExecpolicyAmendment' || option === 'acceptForSession') {
      notify('item/completed', {
        turnId: pending.turnId,
        item: { type: 'commandExecution', id: pending.itemId, command: pending.command, cwd: '/tmp/one', status: 'completed', exitCode: 0, aggregatedOutput: 'gone', durationMs: 5 },
      });
      say(`approved:${option}`);
      pending.done();
    } else if (option === 'decline') {
      notify('item/completed', {
        turnId: pending.turnId,
        item: { type: 'commandExecution', id: pending.itemId, command: pending.command, cwd: '/tmp/one', status: 'failed', exitCode: 1, aggregatedOutput: 'declined', durationMs: 5 },
      });
      say('denied');
      pending.done();
    } else {
      // cancel (or no answer): the turn is interrupted.
      activeTurn = null;
      finishTurn(pending.turnId, 'interrupted', { message: 'interrupted' });
    }
    return;
  }

  switch (msg.method) {
    case 'account/read':
      return out({ id: msg.id, result: { account: { type: process.env.FAKE_CODEX_VOICE_API_KEY ? 'apiKey' : 'chatgpt' } } });
    case 'thread/realtime/listVoices':
      return out(process.env.FAKE_CODEX_NO_VOICE
        ? { id: msg.id, error: { code: -32601, message: 'unsupported' } }
        : { id: msg.id, result: { voices: { v1: [], v2: [] } } });
    case 'thread/realtime/appendText': {
      out({ id: msg.id, result: {} });
      notify('thread/realtime/item/completed', { item: { type: 'transcriptSegment', id: `typed-${nextTurnSeq++}`, role: 'user', text: msg.params.text } });
      break;
    }
    case 'thread/realtime/start': {
      voiceInstructions = msg.params.realtimeStartInstructions ?? '';
      out({ id: msg.id, result: {} });
      notify('thread/realtime/started', {});
      notify('thread/realtime/sdp', { sdp: 'fixture-answer' });
      if (process.env.FAKE_CODEX_VOICE_CONVERSATION) {
        const transcript = (id, role, text, final = true) => {
          // A legacy stream without identity must not create a second conversation entry.
          notify('thread/realtime/transcript/delta', { role, delta: text });
          notify('thread/realtime/transcript/done', { role, text });
          const item = { type: 'transcriptSegment', id, realtimeSessionId: 'fixture-voice', role, text: '' };
          notify('thread/realtime/item/started', { item });
          notify('thread/realtime/item/transcript/delta', { itemId: id, delta: text });
          if (final) notify('thread/realtime/item/completed', { item: { ...item, text } });
        };
        // Delay past the HTTP response so product tests observe drafts over the live SSE.
        setTimeout(() => {
          transcript('speech-user-one', 'user', '你能听到我说话吗？');
          transcript('speech-assistant-one', 'assistant', '能，我在听，你说。');
          transcript('speech-user-two', 'user', '帮我整理最新的新闻。', false);
          setTimeout(() => notify('thread/realtime/item/completed', {
            item: { type: 'transcriptSegment', id: 'speech-user-two', realtimeSessionId: 'fixture-voice',
              role: 'user', text: '帮我整理最新的科技新闻。' },
          }), Number(process.env.FAKE_CODEX_VOICE_TRANSCRIPT_DELAY ?? 250));
          transcript('speech-assistant-two', 'assistant', '好，我来查看主要新闻。');
        }, 80);
      } else {
        notify('thread/realtime/item/started', { item: { type: 'transcriptSegment', id: 'caption', realtimeSessionId: 'fixture-voice', role: 'user', text: '' } });
        notify('thread/realtime/item/transcript/delta', { itemId: 'caption', delta: 'temporary caption' });
      }
      const startVoiceTask = () => {
        if (!process.env.FAKE_CODEX_VOICE_TASK || activeTurn) return;
        const id = `voice-${nextTurnSeq++}`;
        activeTurn = id;
        notify('turn/started', { turn: { id, status: 'inProgress' } });
        setTimeout(() => {
          if (activeTurn !== id) return;
          let task = process.env.FAKE_CODEX_VOICE_TASK;
          if (task === 'voice-delegate') {
            if (!voiceInstructions.includes('<calli-coordination>')) { say('Missing voice collaboration instructions'); activeTurn = null; finishTurn(id, 'completed'); return; }
            const target = /calli send ([a-f0-9-]{36}) --message-file/.exec(voiceInstructions)?.[1];
            const args = target ? ['send', target] : ['spawn', '--agent', process.env.FAKE_CODEX_VOICE_AGENT ?? 'worker', '--name', 'voice-worker'];
            const command = { args: [...args, '--message-file', '-', '--request-id', 'voice-first'], text: process.env.FAKE_CODEX_VOICE_MESSAGE ?? 'CALLI_ENV' };
            coordinationFixture(`CALLI_COMMAND:${Buffer.from(JSON.stringify(command)).toString('base64')}`);
            say('已派发任务，结果会自动返回。'); activeTurn = null; finishTurn(id, 'completed'); return;
          }
          runTurn(task, id, () => {
            activeTurn = null;
            finishTurn(id, 'completed');
          });
        }, 30);
      };
      if (process.env.FAKE_CODEX_VOICE_CONVERSATION) setTimeout(startVoiceTask, Number(process.env.FAKE_CODEX_VOICE_TRANSCRIPT_DELAY ?? 250) + 250);
      else startVoiceTask();
      return;
    }
    case 'thread/realtime/stop':
      out({ id: msg.id, result: {} });
      if (!process.env.FAKE_CODEX_VOICE_NO_CLOSE) notify('thread/realtime/closed', {});
      return;
    case 'thread/realtime/appendSpeech':
      out({ id: msg.id, result: {} });
      if (process.env.FAKE_CODEX_VOICE_RELAY) notify('thread/realtime/item/completed', {
        item: { type: 'transcriptSegment', id: `speech-${++speechSequence}`, role: 'assistant', text: msg.params.text },
      });
      return;

    case 'thread/settings/update':
      applyTurnOverrides(msg.params);
      return out({ jsonrpc: '2.0', id: msg.id, result: {} });
    case 'turn/settings/update':
      if (activeTurn !== msg.params?.turnId) return out({ jsonrpc: '2.0', id: msg.id, result: { status: 'targetUnavailable' } });
      // The native endpoint can change only the reviewer of the current turn.
      if (typeof msg.params?.approvalsReviewer === 'string') activeApprovalsReviewer = msg.params.approvalsReviewer;
      return out({ jsonrpc: '2.0', id: msg.id, result: { status: 'applied' } });
    case 'thread/goal/get':
      if (process.env.FAKE_CODEX_NO_GOALS) return out({ jsonrpc: '2.0', id: msg.id, error: { code: -32601, message: 'goals not supported' } });
      return out({ jsonrpc: '2.0', id: msg.id, result: { goal } });
    case 'thread/goal/clear':
      goal = null; goalGeneration++;
      out({ jsonrpc: '2.0', id: msg.id, result: { cleared: true } });
      notify('thread/goal/cleared', {}); return;
    case 'thread/goal/set': {
      if (process.env.FAKE_CODEX_NO_GOALS) return out({ jsonrpc: '2.0', id: msg.id, error: { code: -32601, message: 'goals not supported' } });
      if (!goal && !msg.params.objective) return out({ jsonrpc: '2.0', id: msg.id, error: { code: -32602, message: 'no goal exists' } });
      goal = { threadId, tokensUsed: 0, ...goal, ...msg.params };
      const generation = ++goalGeneration;
      const start = () => { notify('thread/goal/updated', { goal }); if (goal.status === 'active') runGoal(generation); };
      if (process.env.FAKE_CODEX_GOAL_EARLY) start();
      out({ jsonrpc: '2.0', id: msg.id, result: { goal } });
      if (!process.env.FAKE_CODEX_GOAL_EARLY) start();
      return;
    }
    case 'initialize':
      return out({ jsonrpc: '2.0', id: msg.id, result: { userAgent: 'fake-codex/0.160', codexHome: '/tmp/codex' } });
    case 'collaborationMode/list':
      return out({ jsonrpc: '2.0', id: msg.id, ...(process.env.FAKE_CODEX_NO_PLAN ? { error: { code: -32601, message: 'not supported' } } : { result: { data: [{ mode: 'plan', name: 'Plan', model: null, reasoning_effort: null }, { mode: 'default', name: 'Default', model: null, reasoning_effort: null }] } } ) });
    case 'model/list':
      if (process.env.FAKE_DIAGNOSTIC_ERROR) return out({ jsonrpc: '2.0', id: msg.id, error: { code: -32000, message: process.env.FAKE_DIAGNOSTIC_ERROR } });
      return out({ jsonrpc: '2.0', id: msg.id, result: { data: process.env.FAKE_CODEX_NO_EFFORT_MODEL
        ? [...MODELS, { id: 'gpt-basic', model: 'gpt-basic', displayName: 'GPT Basic', supportedReasoningEfforts: [] }] : MODELS, nextCursor: null } });
    case 'account/rateLimits/read': {
      if (process.env.FAKE_CODEX_QUOTA_ERROR) return out({ jsonrpc: '2.0', id: msg.id, error: { code: -32000, message: 'ChatGPT authentication required' } });
      if (process.env.FAKE_CODEX_WEEKLY_ONLY) {
        const rateLimits = { limitId: 'codex', primary: { usedPercent: 16, windowDurationMins: 10080, resetsAt: 1792000000 }, secondary: null };
        return out({ jsonrpc: '2.0', id: msg.id, result: { rateLimits, rateLimitsByLimitId: { codex: rateLimits } } });
      }
      const rateLimits = { limitId: 'codex', planType: process.env.FAKE_CODEX_PLAN ?? 'prolite', primary: { usedPercent: 25, windowDurationMins: 300, resetsAt: 1791800000 }, secondary: { usedPercent: 82, windowDurationMins: 10080, resetsAt: 1792000000 } };
      return out({ jsonrpc: '2.0', id: msg.id, result: { rateLimits, rateLimitsByLimitId: { codex: rateLimits, review: { limitName: '代码审查', primary: { usedPercent: 10, windowDurationMins: 10080 } } } } });
    }
    case 'skills/list': {
      if (process.env.FAKE_CODEX_NO_SKILLS) return out({ jsonrpc: '2.0', id: msg.id, error: { code: -32601, message: 'not found' } });
      const cwd = msg.params?.cwds?.[0];
      const skills = [
        { name: 'inspect', path: `${cwd}/.agents/skills/inspect/SKILL.md`, description: `检查项目 ${basename(cwd)}`, enabled: true },
        { name: 'disabled', path: `${cwd}/disabled/SKILL.md`, description: 'disabled', enabled: false },
        { name: 'review', path: `${cwd}/review/SKILL.md`, description: 'shadowed by built-in', enabled: true },
      ];
      return out({ jsonrpc: '2.0', id: msg.id, result: { data: [{ cwd, skills, errors: [] }] } });
    }
    case 'mcpServerStatus/list':
      return out({ jsonrpc: '2.0', id: msg.id, result: msg.params?.cursor
        ? { data: [{ name: 'docs', tools: {}, authStatus: 'unsupported' }], nextCursor: null }
        : { data: [{ name: 'repo', tools: { search: {} }, authStatus: 'oAuth' }], nextCursor: 'page-2' } });
    case 'review/start': {
      const turnId = `turn-${nextTurnSeq++}`;
      activeTurn = turnId;
      out({ jsonrpc: '2.0', id: msg.id, result: { turn: { id: turnId }, reviewThreadId: threadId } });
      setTimeout(() => {
        notify('item/completed', { item: { type: 'exitedReviewMode', id: 'review-result', review: `审查完成：${msg.params.target.instructions ?? msg.params.target.type}` } });
        activeTurn = null;
        finishTurn(turnId, 'completed');
      }, 15);
      return;
    }
    case 'thread/start':
      dynamicTools = msg.params?.dynamicTools ?? [];
      currentWebSearch = msg.params?.config?.web_search ?? null;
      if (typeof msg.params?.sandbox === 'string') currentSandbox = msg.params.sandbox.replace(/-([a-z])/g, (_, c) => c.toUpperCase()).replace('Fullaccess', 'FullAccess');
      if (typeof msg.params?.approvalPolicy === 'string') currentApproval = msg.params.approvalPolicy;
      if (typeof msg.params?.approvalsReviewer === 'string') currentApprovalsReviewer = msg.params.approvalsReviewer;
      if (typeof msg.params?.model === 'string') currentModel = msg.params.model;
      ensureRollout();
      return out({ jsonrpc: '2.0', id: msg.id, result: { thread: { id: threadId, cwd: '/tmp/one' }, model: currentModel, reasoningEffort: currentEffort, sandbox: { type: 'readOnly' }, approvalPolicy: currentApproval, approvalsReviewer: currentApprovalsReviewer } });
    case 'thread/resume':
      currentWebSearch = msg.params?.config?.web_search ?? null;
      threadId = msg.params?.threadId ?? threadId;
      if (typeof msg.params?.sandbox === 'string') currentSandbox = msg.params.sandbox.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
      if (typeof msg.params?.approvalPolicy === 'string') currentApproval = msg.params.approvalPolicy;
      if (typeof msg.params?.approvalsReviewer === 'string') currentApprovalsReviewer = msg.params.approvalsReviewer;
      return out({ jsonrpc: '2.0', id: msg.id, result: { thread: { id: msg.params?.threadId ?? threadId, cwd: '/tmp/one' }, model: currentModel, reasoningEffort: currentEffort, sandbox: { type: 'readOnly' }, approvalPolicy: currentApproval, approvalsReviewer: currentApprovalsReviewer } });
    case 'turn/start': {
      if (activeTurn) return out({ jsonrpc: '2.0', id: msg.id, error: { code: -32602, message: 'ActiveTurnNotSteerable' } });
      applyTurnOverrides(msg.params ?? {});
      if (msg.params?.collaborationMode) {
        currentWorkMode = msg.params.collaborationMode.mode;
        const settings = msg.params.collaborationMode.settings;
        if (!settings?.model || settings.developer_instructions !== null) throw new Error('Invalid native collaboration settings');
        currentModel = settings.model;
        if (settings.reasoning_effort) currentEffort = settings.reasoning_effort;
      }
      const turnId = `turn-${nextTurnSeq++}`;
      const text = (msg.params?.input ?? []).filter((i) => i.type === 'text').map((i) => i.text).join('');
      appendRollout(text);
      const images = (msg.params?.input ?? []).filter((i) => i.type === 'localImage');
      const inlineImages = (msg.params?.input ?? []).filter((i) => i.type === 'image');
      const skills = (msg.params?.input ?? []).filter((i) => i.type === 'skill');
      if (['batch-completed', 'batch-item-first', 'batch-completed-only', 'batch-failed', 'batch-rejected', 'batch-late-previous', 'batch-late-no-start', 'batch-mismatched-start', 'batch-noise'].includes(text)) {
        const failed = text === 'batch-failed';
        const batch = [];
        const event = (method, params) => batch.push({ jsonrpc: '2.0', method, params: { threadId, ...params } });
        const stale = (id) => {
          event('thread/tokenUsage/updated', { turnId: id, tokenUsage: { last: { totalTokens: 999999 }, modelContextWindow: 1000000 } });
          event('turn/started', { turn: { id, status: 'inProgress' } });
          event('item/agentMessage/delta', { turnId: id, itemId: 'stale-answer', delta: 'Stale answer' });
          event('turn/completed', { turn: { id, status: 'failed', error: { message: 'stale failure' } } });
        };
        if (text.startsWith('batch-late-')) stale(`turn-${nextTurnSeq - 2}`);
        if (text === 'batch-mismatched-start') stale('unrelated-turn');
        if (text === 'batch-noise') for (let i = 0; i < 250; i++) event('item/agentMessage/delta', { turnId: `noise-${i}`, itemId: 'noise', delta: 'Noise' });
        batch.push({ jsonrpc: '2.0', id: msg.id, ...(text === 'batch-rejected'
          ? { error: { code: -32603, message: 'fixture start rejected' } }
          : { result: { turn: { id: turnId, status: 'inProgress' } } }) });
        if (text !== 'batch-rejected') {
          if (text === 'batch-completed' || text === 'batch-late-previous') event('turn/started', { turn: { id: turnId, status: 'inProgress' } });
          if (!['batch-completed-only', 'batch-failed'].includes(text)) {
            event('item/started', { turnId, item: { id: 'batch-answer', type: 'agentMessage', text: '' } });
            event('item/agentMessage/delta', { turnId, itemId: 'batch-answer', delta: 'Immediate answer' });
            event('item/completed', { turnId, item: { id: 'batch-answer', type: 'agentMessage', text: 'Immediate answer' } });
          }
          if (failed) event('error', { turnId, error: { message: 'fixture turn failed' }, willRetry: false });
          event('turn/completed', { turn: { id: turnId, status: failed ? 'failed' : 'completed', error: null } });
        }
        // One write makes notifications arrive before the awaiting request's continuation runs.
        process.stdout.write(batch.map(row => JSON.stringify(row)).join('\n') + '\n');
        break;
      }
      out({ jsonrpc: '2.0', id: msg.id, result: { turn: { id: turnId, items: [], itemsView: 'notLoaded', status: 'inProgress', error: null } } });
      activeTurn = turnId;
      activeApprovalsReviewer = currentApprovalsReviewer;
      notify('turn/started', { turn: { id: turnId, items: [], itemsView: 'notLoaded', status: 'inProgress', error: null } });
      if (text === 'quota-exhausted' && !process.env.FAKE_CODEX_PLAN_LEFT) { activeTurn = null; finishTurn(turnId, 'failed', { message: "You've hit your usage limit for this model. Switch to another model now, or try again later." }); break; }
      // Reproduce a backend rejecting uploaded image references while accepting image bytes.
      if (images.length && process.env.FAKE_CODEX_REJECT_LOCAL_IMAGES) {
        activeTurn = null; finishTurn(turnId, 'failed', { message: 'stream disconnected before completion: Invalid image' }); break;
      }
      if (inlineImages.length) {
        const types = inlineImages.map((image) => {
          const match = /^data:(image\/(?:png|jpeg|gif|webp));base64,([A-Za-z0-9+/]+={0,2})$/.exec(image.url);
          if (!match || !Buffer.from(match[2], 'base64').length) throw new Error('Invalid inline image');
          return match[1];
        });
        say(`inline-images:${types.join(',')};`);
      }
      if (images.length) say(`images:${images.map((i) => i.path).join(',')}`);
      if (skills.length) say(`skills:${skills.map((s) => `${s.name}@${s.path}`).join(',')};`);
      setTimeout(() => runTurn(text, turnId, () => {
        activeTurn = null; finishTurn(turnId, 'completed');
        if (voiceAfterPrompt) {
          voiceAfterPrompt = false;
          const id = `voice-${nextTurnSeq++}`;
          activeTurn = id;
          notify('turn/started', { turn: { id, status: 'inProgress' } });
          runTurn('approve', id, () => { activeTurn = null; finishTurn(id, 'completed'); });
        }
        if (goal?.status === 'active') setTimeout(() => runGoal(goalGeneration), 15);
      }), Number(process.env.FAKE_CODEX_TURN_DELAY ?? 15));
      return;
    }
    case 'turn/interrupt':
      if (activeTurn) {
        const turnId = activeTurn;
        activeTurn = null;
        finishTurn(turnId, 'interrupted', { message: 'interrupted' });
      }
      return out({ jsonrpc: '2.0', id: msg.id, result: {} });
    case 'thread/compact/start': {
      if (process.env.FAKE_CODEX_COMPACT_REJECT) return out({ jsonrpc: '2.0', id: msg.id, error: { code: -32602, message: 'compact rejected' } });
      const turnId = `compact-${nextTurnSeq++}`;
      const delay = Number(process.env.FAKE_CODEX_COMPACT_DELAY ?? 20);
      const start = () => {
        activeTurn = turnId;
        notify('turn/started', { turn: { id: turnId, status: 'inProgress' } });
        notify('item/started', { turnId, item: { id: 'compact-item', type: 'contextCompaction' } });
        notify('turn/completed', { threadId: 'another-thread', turn: { id: turnId, status: 'completed' } });
        notify('turn/completed', { turn: { id: 'stale-turn', status: 'completed' } });
        if (process.env.FAKE_CODEX_COMPACT_CRASH) return process.exit(2);
        setTimeout(() => {
          if (activeTurn !== turnId) return;
          notify('item/completed', { turnId, item: { id: 'compact-item', type: 'contextCompaction' } });
        }, delay);
        setTimeout(() => {
          if (activeTurn !== turnId) return;
          activeTurn = null;
          finishTurn(turnId, process.env.FAKE_CODEX_COMPACT_FAIL ? 'failed' : 'completed', process.env.FAKE_CODEX_COMPACT_FAIL ? { message: 'compaction failed' } : null);
        }, delay * 2);
      };
      if (process.env.FAKE_CODEX_COMPACT_EARLY) start();
      out({ jsonrpc: '2.0', id: msg.id, result: {} });
      if (!process.env.FAKE_CODEX_COMPACT_EARLY) setTimeout(start, delay);
      return;
    }
    case 'thread/list': {
      if (!msg.params?.useStateDbOnly) {
        return out({ jsonrpc: '2.0', id: msg.id, error: { code: -32602, message: 'listing must not scan rollouts' } });
      }
      const start = msg.params?.cursor ? Number(msg.params.cursor) : 0;
      const size = process.env.FAKE_CODEX_SESSIONS_FILE ? Math.min(msg.params.limit ?? 2, 100) : 2;
      const page = THREADS.slice(start, start + size);
      const next = process.env.FAKE_CODEX_REPEAT_CURSOR ? 'repeat' : start + size < THREADS.length ? String(start + size) : null;
      return out({ jsonrpc: '2.0', id: msg.id, result: { data: page, nextCursor: next } });
    }
    case 'thread/read': {
      if (msg.params?.includeTurns !== false) return out({ jsonrpc: '2.0', id: msg.id, error: { code: -32602, message: 'metadata read must not hydrate turns' } });
      const thread = THREADS.find((t) => t.id === msg.params.threadId);
      return thread
        ? out({ jsonrpc: '2.0', id: msg.id, result: { thread: { source: 'cli', ...thread, turns: [] } } })
        : out({ jsonrpc: '2.0', id: msg.id, error: { code: -32602, message: 'unknown thread' } });
    }
    case 'thread/turns/list':
      if (String(msg.params?.threadId).startsWith('hist-')) return out({ jsonrpc: '2.0', id: msg.id, result: { data: historyTurns(), nextCursor: null } });
      return out({ jsonrpc: '2.0', id: msg.id, error: { code: -32602, message: 'unknown thread' } });
    case 'thread/fork': {
      // Fork by path: the rollout file is read from disk, `threadId` is then ignored (the schema
      // still requires it; calli passes an empty one). A missing file is the caller's error.
      if (typeof msg.params?.path === 'string' && msg.params.path) {
        let first = '';
        try { first = readFileSync(msg.params.path, 'utf8').split('\n')[0] ?? ''; } catch { first = ''; }
        if (!first.startsWith('{')) return out({ jsonrpc: '2.0', id: msg.id, error: { code: -32602, message: 'no rollout found for path' } });
        return out({ jsonrpc: '2.0', id: msg.id, result: { thread: { id: `imported-${basename(msg.params.path, '.jsonl')}`, cwd: msg.params?.cwd ?? '/tmp/one' }, model: currentModel } });
      }
      // `lastTurnId`: the copy goes through that turn and leaves out what came after.
      const last = msg.params?.lastTurnId;
      if (last && !historyTurns().some((t) => t.id === last)) {
        return out({ jsonrpc: '2.0', id: msg.id, error: { code: -32602, message: `unknown turn ${last}` } });
      }
      // `cwd`: where the copy will work. Its record says so, and here its id does too.
      const into = msg.params?.cwd ? `-in-${basename(msg.params.cwd)}` : '';
      return out({
        jsonrpc: '2.0',
        id: msg.id,
        result: { thread: { id: `${msg.params?.threadId}-fork${last ? `-at-${last}` : ''}${into}`, cwd: msg.params?.cwd ?? '/tmp/one' }, model: currentModel },
      });
    }
    case 'thread/delete':
      if (process.env.FAKE_CODEX_DELETE_LOG) appendFileSync(process.env.FAKE_CODEX_DELETE_LOG, msg.params?.threadId + '\n');
      return out({ jsonrpc: '2.0', id: msg.id, result: {} });
    default:
      if (msg.id !== undefined) out({ jsonrpc: '2.0', id: msg.id, error: { code: -32601, message: 'not found' } });
  }
});
