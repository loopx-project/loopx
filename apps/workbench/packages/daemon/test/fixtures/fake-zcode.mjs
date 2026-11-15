#!/usr/bin/env node
if (process.argv.includes('--version')) { process.stdout.write('fixture 1.2.3\n'); process.exit(0); }
// ZCode's strict NDJSON protocol: no jsonrpc field, reverse runtime preferences, native events.
import { DatabaseSync } from 'node:sqlite';
import { createInterface } from 'node:readline';
import { appendFileSync, readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import { coordinationFixture } from './coordination.mjs';
const output = (value) => process.stdout.write(JSON.stringify(value) + '\n');
const reply = (id, result) => output({ id, result });
const error = (id, message) => output({ id, error: { code: -32602, message } });
let sessionId = 'sess-zcode-fixture';
const providerId = process.env.FAKE_ZCODE_ACCOUNT_PROVIDER || 'api';
let accountReady = !process.env.FAKE_ZCODE_ACCOUNT_PROVIDER;
let model = { providerId, modelId: 'small' };
let mode = 'build';
let turn = null;
let seq = 0;
let nextId = 10000;
let goal = null;
let goalRun = false;
let accountedTurns = 0;
const usageDb = process.env.FAKE_ZCODE_REQUEST_USAGE ? new DatabaseSync(process.env.ZCODE_SESSION_DB_PATH) : null;
usageDb?.exec(`CREATE TABLE IF NOT EXISTS model_usage (
  id TEXT PRIMARY KEY, session_id TEXT, started_at INTEGER, completed_at INTEGER, input_tokens INTEGER,
  output_tokens INTEGER, reasoning_tokens INTEGER, cache_read_input_tokens INTEGER, cache_creation_input_tokens INTEGER,
  computed_total_tokens INTEGER, provider_total_tokens INTEGER, model_id TEXT, provider_id TEXT, duration_ms INTEGER,
  time_to_first_token_ms INTEGER, status TEXT)`);
function recordNativeUsage() {
  usageDb?.prepare('INSERT INTO model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)').run(
    `request-${accountedTurns}`, sessionId, Date.now(), Date.now(), 120, 30, 10, 60, 0, 150, 150, 'fixture-model', 'fixture-provider', 100, 50, 'completed');
}
let contextUsed = process.env.FAKE_ZCODE_INITIAL_CONTEXT ? Number(process.env.FAKE_ZCODE_INITIAL_CONTEXT) : null;
const pending = new Map();
const request = (method, params) => new Promise((resolve) => { const id = nextId++; pending.set(id, resolve); output({ id, method, params }); });
const emit = (type, payload, turnId = turn?.id) => output({ method: 'session/event', params: { sessionId, eventId: `ev-${++seq}`, seq, turnId, type, payload, deliveryKind: 'desktop-continuous' } });
const snapshot = () => ({ runtime: process.env.FAKE_ZCODE_CONTEXT && contextUsed !== null ? { contextUsage: { used: contextUsed, size: model.modelId === 'large' ? 256000 : 128000, cost: null } } : {}, projection: { totalTokenCount: 900000 }, session: { sessionId, target: process.env.FAKE_ZCODE_GOAL ? { status: 'active' } : goal }, settings: { mode: { current: mode }, model: { current: model, available: process.env.FAKE_ZCODE_NO_MODELS || !accountReady ? [] : [{ ref: { providerId, modelId: 'small' }, label: 'Small', properties: { inputFormat: { supportsImage: true } }, reasoning: { defaultLevel: 'low', levels: [{ value: 'low' }, { value: 'high' }] } }, { ref: { providerId, modelId: 'large' }, label: 'Large', properties: { inputFormat: { supportsImage: false } } }] } } });
const goalSettled = (failed = false) => { goalRun = false; output({ method: 'state.updated', params: { sessionId, reason: failed ? 'goal_continuation_failed' : 'goal_continuation_completed' } }); };
const preferences = async () => {
  const result = await request('session/requestRuntimePreferences', { sessionId, scope: 'runtime-materialization' });
  if (process.env.FAKE_ZCODE_LOG) appendFileSync(process.env.FAKE_ZCODE_LOG, JSON.stringify({ preferences: result }) + '\n');
  return result;
};
function done(resultType = 'success', response = '完成') { if (!turn) return; accountedTurns++; recordNativeUsage(); emit('turn.completed', { resultType, response }); turn = null; }
async function handle(message) {
  if ('jsonrpc' in message) return error(message.id, 'Unexpected jsonrpc field');
  if (!message.method) { pending.get(message.id)?.(message.result); pending.delete(message.id); return; }
  const { id, method, params: p = {} } = message;
  if (process.env.FAKE_ZCODE_LOG) appendFileSync(process.env.FAKE_ZCODE_LOG, JSON.stringify({ method, params: p }) + '\n');
  switch (method) {
    case 'provider/updateAccountConfig': {
      const path = process.env.ZCODE_BUILTIN_PROVIDER_CONFIG_FILE;
      const builtin = JSON.parse(readFileSync(path, 'utf8'));
      const revision = `zcode-builtin:${builtin.revision}:${createHash('sha256').update(resolve(path)).digest('hex')}`;
      accountReady = !process.env.FAKE_ZCODE_ACCOUNT_STALE && p.basedOnZCodeBuiltinRevision === revision && p.providers[providerId]?.access?.entitled === true && p.states[providerId]?.current === true;
      return reply(id, { receivedRevision: p.revision, providerCount: Object.keys(p.providers).length, status: 'received' });
    }
    case 'session/goal': {
      if (process.env.FAKE_ZCODE_NO_GOALS) return error(id, 'goal unsupported');
      if (p.action === 'pause') { if (goal) goal.status = 'paused'; done('cancelled', ''); if (goalRun) goalSettled(); }
      if (p.action === 'clear') goal = null;
      if (p.action === 'set') goal = { objective: p.objective, status: 'active' };
      if (p.action === 'resume' && goal) goal.status = 'active';
      const startedTurn = ['set', 'resume'].includes(p.action) && goal?.status === 'active';
      if (!startedTurn) return reply(id, { snapshot: snapshot(), startedTurn, response: '' });
      goalRun = true;
      const run = async () => {
        for (let round = 1; round <= 2; round++) {
          if (!goalRun) return;
          turn = { id: `goal-${++seq}` };
          emit('turn.started', { inputId: round === 1 ? p.inputId : 'native-continuation' });
          if (goal.objective.includes('crash')) return process.exit(2);
          if (goal.objective.includes('slow')) return;
          if (goal.objective.includes('turn-fail')) {
            emit('turn.failed', { error: { message: '模型请求失败' } }); turn = null;
            await new Promise((r) => setTimeout(r, 20));
            goal.status = 'paused'; goalSettled(true); return;
          }
          if (round === 2 && goal.objective.includes('approve')) {
            const answer = await request('interaction/requestPermission', { sessionId, reason: '目标审批', options: [{ optionId: 'once', name: '允许一次', kind: 'allow_once', response: { decision: 'allow' } }] });
            if (!goalRun) return;
            emit('model.streaming', { kind: 'text_delta', delta: `decision:${answer.decision}` });
          }
          done('success', `目标第 ${round} 轮`);
          await new Promise((r) => setTimeout(r, 20));
        }
        if (!goalRun) return;
        goal.status = 'completed'; goalSettled(goal.objective.includes('fail'));
      };
      void run();
      reply(id, { snapshot: snapshot(), startedTurn, response: '' }); return;
    }
    case 'session/create': case 'session/resume': {
      sessionId = p.sessionId ?? sessionId;
      // Real zcode validates mcpServers with a strict schema: a stdio entry is exactly
      // { name, command, args, env: [{ name, value }] } (args and env required, empty allowed,
      // nothing extra), so the fake rejects the same shapes the real one would.
      for (const server of p.mcpServers ?? []) {
        if (!['name', 'command', 'args', 'env'].every((k) => k in server) || Object.keys(server).some((k) => !['name', 'command', 'args', 'env'].includes(k)))
          return error(id, `Invalid params — mcpServers.${p.mcpServers.indexOf(server)}: Invalid input`);
        if (typeof server.name !== 'string' || !server.name || typeof server.command !== 'string' || !server.command
          || !Array.isArray(server.args) || server.args.some((a) => typeof a !== 'string')
          || !Array.isArray(server.env) || server.env.some((e) => typeof e?.name !== 'string' || !e.name || typeof e?.value !== 'string'))
          return error(id, `Invalid params — mcpServers.${p.mcpServers.indexOf(server)}: Invalid input`);
      }
      if (method === 'session/resume' && process.env.FAKE_ZCODE_CONTEXT) contextUsed = 12000;
      if (!process.env.FAKE_ZCODE_NO_PREFERENCES) await preferences();
      mode = p.mode ?? mode; model = p.model ?? model;
      return reply(id, snapshot());
    }
    case 'session/subscribe': return reply(id, { sessionId, eventSeq: seq, events: [] });
    case 'session/setMode': mode = p.mode; return reply(id, snapshot());
    case 'session/setModel': model = p.model; return reply(id, snapshot());
    case 'session/setThoughtLevel': return reply(id, snapshot());
    case 'session/usage': if (process.env.FAKE_ZCODE_USAGE_HANG) return; if (process.env.FAKE_TELEMETRY) return reply(id, { ...(process.env.FAKE_ZCODE_REQUEST_USAGE ? { inputBaselineBySource: { main_turn: 120 } } : {}), totalTokens: 900000 + accountedTurns * 150, inputTokens: 800000 + accountedTurns * 120, outputTokens: 100000 + accountedTurns * 30, cacheReadTokens: 500000 + accountedTurns * 60, cacheCreationTokens: 0, reasoningTokens: accountedTurns * 10 }); return reply(id, { sessionId, totalTokens: 1500, inputTokens: 1200, outputTokens: 300, reasoningTokens: 100, cacheCreationTokens: 200, cacheReadTokens: 700, modelRequestCount: 2, modelErrorCount: 0, inputBaselineBySource: {} });
    case 'session/read': return process.env.FAKE_ZCODE_USAGE_READ_FAIL ? error(id, 'Statistics unavailable') : reply(id, snapshot());
    case 'session/close': reply(id, { closed: true }); return;
    case 'session/stop': reply(id, { stopped: true }); done('cancelled', ''); if (goalRun) { goal.status = 'paused'; goalSettled(); } return;
    case 'session/send': case 'session/compact': {
      if (method === 'session/compact' && p.instructions === 'already-running') return reply(id, { compact: { state: 'already_running' } });
      if (p.attachments?.length) {
        if (model.modelId === 'large') return error(id, 'Selected model cannot read images');
        for (const image of p.attachments) {
          if (image.kind !== 'image' || !image.filename || !image.mimeType?.startsWith('image/')) return error(id, 'Invalid image attachment');
          const bytes = image.localPath ? readFileSync(image.localPath) : Buffer.from(image.dataBase64 ?? '', 'base64');
          if (!bytes.length) return error(id, 'Empty image attachment');
        }
      }
      turn = { id: `turn-${++seq}`, inputId: p.inputId };
      const activeTurn = turn;
      await request('session/requestRuntimePreferences', { sessionId, scope: 'user-execution' });
      if (process.env.FAKE_ZCODE_ACCOUNT_PROVIDER) {
        const auth = await request('interaction/requestProviderRuntimeHeaders', {
          sessionId, providerId: model.providerId, modelSelection: model,
          accountAccess: { mode: 'individual-coding-plan' }, reason: 'model-request',
        });
        if (!auth.headersApplied) return error(id, auth.errorMessage);
        if (auth.requestAuth?.apiKey !== process.env.FAKE_ZCODE_API_KEY) return error(id, 'Invalid fixture credential');
      }
      if (p.content === 'reject') return error(id, 'submission rejected');
      emit('turn.started', { inputId: p.inputId });
      // Completion may precede the response to send; the adapter must already be listening.
      if (p.content === 'early') { contextUsed = 48000; done('success', '提前完成'); reply(id, { accepted: true }); return; }
      reply(id, method === 'session/compact' ? { compact: { state: 'accepted', inputId: p.inputId } } : { accepted: true });
      if (p.content?.startsWith('auto-compact') || method === 'session/compact') {
        const payload = { operationId: 'compact-1', messageId: 'compact-marker', display: 'separator', trigger: method === 'session/compact' ? 'manual' : 'auto' };
        emit('session.updated', { ...payload, status: 'started' });
        emit('session.updated', { ...payload, status: p.content?.includes('fail') ? 'failed' : p.content?.includes('skip') ? 'skipped' : 'completed' });
      }
      if (method === 'session/compact') {
        await new Promise((resolve) => setTimeout(resolve, Number(process.env.FAKE_COMPACT_DELAY ?? 20)));
        if (turn !== activeTurn) return;
        if (process.env.FAKE_COMPACT_FAIL) { emit('turn.failed', { error: { message: 'compaction failed' } }); turn = null; return; }
      }
      if (process.env.FAKE_LIVE_USAGE && p.content === 'live usage') {
        accountedTurns++; recordNativeUsage();
        await new Promise(r => setTimeout(r, 2400));
        if (turn === activeTurn) done();
        return;
      }
      const planCommand = typeof p.content === 'string' ? p.content.split('\n')[0] : '';
      if (planCommand === 'plan-question' || planCommand === 'plan-approval') {
        const plan = planCommand === 'plan-approval';
        const question = plan ? 'Review this implementation plan.' : '优先支持哪一端？';
        const answer = await request('interaction/requestUserInput', { sessionId, requestId: 'question-1', prompt: question, questions: [{ question, options: plan ? [{ value: 'approve', label: 'Approve' }] : [{ value: '桌面和手机', label: '桌面和手机' }, { value: '先做桌面', label: '先做桌面' }] }], schema: plan ? { interaction: 'plan_approval' } : {}, input: plan ? { plan: '## 实施计划\n\n1. 阅读代码\n2. 完成验证' } : {} });
        if (turn !== activeTurn) return;
        if (plan && answer.content?.answers?.[question] === 'approve') { mode = 'build'; emit('session.updated', { mode: 'build', planEnabled: false }); }
        done('success', `answer:${JSON.stringify(answer)};mode:${mode}`); return;
      }
      if (p.content === 'plan-draft') { done('success', `mode:${mode}`); return; }
      if (p.content?.startsWith('CALLI_') || p.content?.startsWith('[协作消息')) {
        const answer = coordinationFixture(p.content);
        if (answer !== null) { done('success', answer); return; }
      }
      if (p.content === 'crash') return process.exit(7);
      if (p.content === 'slow') return;
      if (p.content === 'fail') { emit('turn.failed', { error: { message: '模型请求失败' } }); turn = null; return; }
      if (p.content === 'approve' || p.content === 'invalid-approval') {
        const response = await request('interaction/requestPermission', { sessionId, reason: '运行命令', toolName: 'Bash', input: { command: 'pwd' }, options: [{ optionId: 'once', name: '允许一次', kind: 'allow_once', response: { decision: 'allow' } }, { optionId: 'deny', name: '拒绝', kind: 'reject_once', response: { decision: 'deny' } }] });
        if (turn !== activeTurn) return;
        done('success', response?.decision === 'allow' ? '已批准' : '已拒绝'); return;
      }
      emit('model.streaming', { kind: 'reasoning_delta', delta: '先检查' });
      // Other sessions and old turns must not leak into the active turn.
      emit('model.streaming', { kind: 'text_delta', delta: '不应出现' }, 'old-turn');
      if (p.content === 'tool') {
        emit('tool.updated', { kind: 'scheduled', toolCallId: 'tool-1', toolName: 'Read', input: { file_path: 'README.md' } });
        emit('tool.updated', { kind: 'result', toolCallId: 'tool-1', result: { success: true, output: 'hello' } });
      }
      emit('model.streaming', { kind: 'text_delta', delta: method === 'session/compact' ? '已压缩' : p.attachments?.length ? `已读取 ${p.attachments.length} 张图片` : '你好' });
      // part.delta is the other delivery surface and must not duplicate model.streaming.
      emit('part.delta', { field: 'text', delta: '你好', partId: 'part-1' });
      contextUsed = method === 'session/compact' ? (process.env.FAKE_ZCODE_COMPACT_USAGE_UNKNOWN ? null : 8000) : 48000;
      done(); return;
    }
    default: error(id, `Unsupported method: ${method}`);
  }
}
createInterface({ input: process.stdin }).on('line', (line) => {
  if (!line.trim()) return;
  void handle(JSON.parse(line)).catch((e) => { process.stderr.write(String(e)); process.exit(1); });
});
