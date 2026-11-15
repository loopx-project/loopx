import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { StoredEvent } from '../src/events.js';
import { handoverBrief, handoverPreview, handoverPrompt, synthesizeBrief } from '../src/handover.js';

let seq = 0;
const ev = (turnId: string | null, type: StoredEvent['type'], data: Record<string, unknown> = {}): StoredEvent => ({
  threadId: 't',
  seq: ++seq,
  turnId,
  type,
  ts: new Date().toISOString(),
  data,
});

test('the handover prompt tells the agent what its successor can and cannot see', () => {
  const prompt = handoverPrompt('Claude Code 2');
  assert.match(prompt, /Claude Code 2/);
  assert.match(prompt, /看不到这段对话/);
  assert.match(prompt, /## 任务/);
  // What was checked, what was not, and what is half-done: the successor cannot tell from the diff.
  assert.match(prompt, /## 验证情况 — .*还没验证.*改到一半/);
});

test('the brief handover points at the source and tells the successor to align before going on', () => {
  const head = handoverBrief({ threadId: 'src', from: 'Claude Code 3', cwd: '/work', worktree: { root: '/work', branch: 'feat' }, lead: true });
  assert.match(head, /calli history src/);
  assert.match(head, /分支 feat/);
  assert.match(head, /主控/);
  assert.doesNotMatch(handoverBrief({ threadId: 'src', from: 'pi', cwd: '/work' }), /主控/);
  // The successor reads the source itself; what remains is checking the tree, not instructions.
  assert.match(head, /git status、git diff 对齐现状再继续/);
});

test('a synthesized brief assembles what the log knows, each thing once', () => {
  const events = [
    ev('u1', 'turn.started', { text: '把登录页改成深色' }),
    ev('u1', 'tool.start', { id: 'a', title: 'edit', content: [{ type: 'diff', path: 'src/login.ts' }] }),
    ev('u1', 'tool.end', { id: 'a', content: [{ type: 'diff', path: 'src/login.ts' }] }),
    ev('u1', 'tool.start', { id: 'b', title: 'edit', content: [{ type: 'diff', path: 'src/theme.ts' }, { type: 'diff', path: 'src/login.ts' }] }),
    ev('u1', 'message.delta', { text: '改好了两个文件' }),
    ev('u1', 'turn.done', {}),
    ev('u2', 'turn.started', { text: '测试也补一下' }),
    ev('u2', 'plan.updated', { entries: [{ content: '改登录页', status: 'completed' }, { content: '补测试', status: 'pending' }] }),
    ev('u2', 'message.delta', { text: '还差测试' }),
    ev('u2', 'turn.error', { code: 'rate_limited', error: 'quota exceeded' }),
  ];
  const text = synthesizeBrief(events);
  assert.match(text, /## 进度（hub 从记录整理，可能不全）/);
  assert.match(text, /最近的要求：把登录页改成深色/, 'the failed last ask is not repeated here; the caller carries it');
  assert.doesNotMatch(text, /测试也补一下/);
  assert.match(text, /最后的回答：还差测试/, 'the failed turn\u2019s partial answer is the last one');
  assert.match(text, /未完成的计划：补测试/);
  assert.doesNotMatch(text, /改登录页/, 'a completed plan entry is not unfinished');
  assert.match(text, /改过的文件：src\/login\.ts、src\/theme\.ts/, 'files appear once each, in order');
  assert.match(text, /中断原因：rate_limited：quota exceeded/);
});

test('a handover or imported turn is not listed as the thread\u2019s last ask or answer', () => {
  const events = [
    ev('u1', 'turn.started', { text: '做点事' }),
    ev('u1', 'message.delta', { text: '做完了' }),
    ev('u1', 'turn.done', {}),
    ev('h1', 'turn.started', { text: '请写一份交接摘要', handover: 'claude-2' }),
    ev('h1', 'message.delta', { text: '## 任务' }),
    ev('h1', 'turn.done', {}),
  ];
  const text = synthesizeBrief(events);
  assert.match(text, /最近的要求：做点事/);
  assert.doesNotMatch(text, /请写一份交接摘要/);
  assert.match(text, /最后的回答：做完了/);
  assert.doesNotMatch(text, /## 任务/, 'the handover turn\u2019s own text is not the thread\u2019s last answer');
});

test('the brief handover points at the source and keeps progress to a few lines', () => {
  const head = handoverBrief({ threadId: 'src', from: 'Claude Code 3', cwd: '/work', worktree: { root: '/work', branch: 'feat' }, lead: true });
  assert.match(head, /calli history src/);
  assert.match(head, /分支 feat/);
  assert.match(head, /主控/);
  assert.doesNotMatch(handoverBrief({ threadId: 'src', from: 'pi', cwd: '/work' }), /主控/);
  const files = Array.from({ length: 12 }, (_, i) => ({ type: 'diff', path: `f${i}.ts` }));
  const text = synthesizeBrief([
    ev('u1', 'turn.started', { text: 'with hub additions', displayText: '先做第一步' }),
    ev('u1', 'tool.start', { id: 'a', content: files }),
    ev('u1', 'message.delta', { text: '做完了\n第一步' }),
    ev('u1', 'turn.done', {}),
    ev('u2', 'turn.started', { text: '再重跑一次' }),
    ev('u2', 'turn.error', { code: 'plan_exhausted', error: 'limit' }),
  ]);
  assert.match(text, /最近的要求：先做第一步/, 'the failed ask is carried over separately, and hub additions are left out');
  assert.doesNotMatch(text, /再重跑一次/);
  assert.match(text, /f7\.ts 等 12 个/);
  assert.match(text, /中断原因：plan_exhausted：limit/);
  assert.equal(synthesizeBrief([]), '');
});

test('a folded handover previews the task, not the lead-in every brief opens with', () => {
  const brief = handoverBrief({ threadId: 'abc', from: 'Claude Code', cwd: '/work', lead: true });
  const written = '## 进度（由 agy2 从记录整理）\n\n## 任务\n梳理企微 **Agent** 现状，产出一份文章。\n\n## 已完成\n1. 画了图';
  assert.equal(handoverPreview(`${brief}\n\n${written}`), '梳理企微 Agent 现状，产出一份文章。 画了图');
  const assembled = '## 进度（hub 从记录整理，可能不全）\n- 最近的要求：把图调暗\n- 中断原因：plan_exhausted';
  assert.equal(handoverPreview(`${brief}\n\n${assembled}`), '最近的要求：把图调暗 中断原因：plan_exhausted');
  // Text that is not a brief (a resent message) is previewed as it is, and cut at the limit.
  assert.equal(handoverPreview('请继续\n看看评论'), '请继续 看看评论');
  assert.equal(handoverPreview('字'.repeat(300), 10), `${'字'.repeat(10)}…`);
  assert.equal(handoverPreview(''), '');
});
