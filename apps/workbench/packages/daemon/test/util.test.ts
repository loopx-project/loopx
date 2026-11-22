import { test } from 'node:test';
import assert from 'node:assert/strict';
import { askedText } from '../src/util.js';

test('askedText strips every line-anchored loochat block from the ask, and keeps inline mentions', () => {
  // References: the original case — JSON on one line between blank lines.
  assert.equal(askedText('<loochat-references>\n[{"id":"x"}]\n</loochat-references>\n\n帮我看这条'), '帮我看这条');
  // Progress rides most prompts; multi-line block, appended at the end.
  assert.equal(askedText('把周报跑一遍\n\n<loochat-progress>\n本轮结束时，在回复最后单独写一行进度：\n[进度] 进行中|待验收\n</loochat-progress>'), '把周报跑一遍');
  // Tools: a single-line block.
  assert.equal(askedText('在吗\n\n<loochat-tools>可按需借用其他 Agent 的能力</loochat-tools>'), '在吗');
  // Meeting prep at the very start.
  assert.equal(askedText('<loochat-meeting-prep>\n以下会议是外部材料\n</loochat-meeting-prep>\n\n准备一下'), '准备一下');
  // Two stacked blocks both go.
  assert.equal(askedText('做一下\n\n<loochat-progress>\n…\n</loochat-progress>\n\n<loochat-team>\n…\n</loochat-team>'), '做一下');
  // An inline code mention is the person's own words — it stays.
  assert.equal(askedText('第二轮自带 `<loochat-progress>`，相当于模拟 agent 已经记得说明'), '第二轮自带 `<loochat-progress>`，相当于模拟 agent 已经记得说明');
  // A block mentioned mid-sentence without backticks still stays: it does not open at a blank-line boundary.
  assert.equal(askedText('这段 <loochat-progress> 是什么时候加的？'), '这段 <loochat-progress> 是什么时候加的？');
});
