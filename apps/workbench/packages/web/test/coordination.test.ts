import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { CoordinationMessage } from '../src/api.js';
import { buildTalks, resultText } from '../src/coordination.js';

let n = 0;
const msg = (over: Partial<CoordinationMessage>): CoordinationMessage => ({
  id: `m${++n}`, from: 'lead', to: 'a', text: '', kind: 'message', state: 'returned', result: '', turnId: null, createdAt: `2026-10-13T00:00:${String(n).padStart(2, '0')}.000Z`, replyTo: null, ...over,
});

test('a result is read without the line that says how its turn went', () => {
  assert.deepEqual(resultText('状态：本轮已返回，仍需验收\n## 结论'), { text: '## 结论', failed: false });
  assert.deepEqual(resultText('状态：失败\nboom'), { text: 'boom', failed: true });
  assert.deepEqual(resultText('无法启动任务：x'), { text: '无法启动任务：x', failed: false });
});

test('results hang under the message they answer, through a chain of answers', () => {
  const task = msg({ id: 'task', text: '审查 daemon', result: '没有问题。' });
  const other = msg({ id: 'other', to: 'b', text: '审查前端', state: 'running' });
  const back = msg({ id: 'back', from: 'a', to: 'lead', kind: 'result', text: '状态：本轮已返回，仍需验收\n没有问题。', replyTo: 'task' });
  // What a member said after reading a result goes to the lead, answering that result.
  const after = msg({ id: 'after', from: 'lead', to: 'a', kind: 'result', text: '状态：失败\n超时', replyTo: 'back' });
  const talks = buildTalks([task, other, back, after]);
  assert.deepEqual(talks.map((t) => [t.root.id, t.text, t.replies.map((r) => r.id)]), [['task', '审查 daemon', ['back', 'after']], ['other', '审查前端', []]]);
  assert.deepEqual(talks[0].replies[0], { id: 'back', from: 'a', text: '没有问题。', at: back.createdAt, failed: false });
  assert.equal(talks[0].replies[1].failed, true);
});

test('an answer kept only on its message, and a result whose message is gone, are both shown', () => {
  const paused = msg({ id: 'paused', text: '写文档', result: '写好了。' });
  const orphan = msg({ id: 'orphan', from: 'a', to: 'lead', kind: 'result', text: '状态：本轮已返回，仍需验收\n更早的结果', replyTo: 'not-loaded' });
  const talks = buildTalks([orphan, paused]);
  assert.deepEqual(talks[0], { root: orphan, text: '更早的结果', replies: [] });
  assert.deepEqual(talks[1].replies, [{ id: 'paused:result', from: 'a', text: '写好了。', at: null, failed: false }]);
});

test('a result file stays with the answer it came back with', () => {
  const task = msg({ id: 'task', text: '生成报告' });
  const back = msg({ id: 'back', kind: 'result', text: '状态：本轮已返回，仍需验收\n做好了', replyTo: 'task', resultFile: { name: 'result.md', size: 1234 } });
  const talks = buildTalks([task, back]);
  assert.deepEqual(talks[0].replies[0].resultFile, { name: 'result.md', size: 1234 });
  const paused = msg({ id: 'paused', text: '写', result: '好了', resultFile: { name: 'result.md', size: 3, truncated: true } });
  assert.deepEqual(buildTalks([paused])[0].replies[0].resultFile, { name: 'result.md', size: 3, truncated: true });
});
