import { test } from 'node:test';
import assert from 'node:assert/strict';
import { deliveredPreview, firstSentence, progressState, withoutProgress } from '../src/timeline.js';

test('the state a report ends with is read from its [进度] line, and nowhere else', () => {
  assert.equal(progressState('接口和测试都交回。\n\n[进度] 待验收：图表颜色已核对'), 'review');
  assert.equal(progressState('[进度] 需要你：深色下的取值需要确认'), 'need');
  assert.equal(progressState('[进度] 进行中'), 'progress');
  // The word carries meaning only as the protocol's last line, not anywhere in the body.
  assert.equal(progressState('待验收的材料都齐了'), null);
  assert.equal(progressState('[进度] 待验收\n后面又补了一句'), null);
  // What came before, or nothing at all, does not make a state.
  assert.equal(progressState('已处理。'), null);
  assert.equal(progressState(''), null);
  // A half-written line is not the protocol yet.
  assert.equal(progressState('[进度]'), null);
});

test('the first sentence is the plain words of the body, past the Markdown and the [进度] line', () => {
  assert.equal(firstSentence('图表颜色已核对。其余照旧。'), '图表颜色已核对。');
  // Marks, heads and links go; the words a person reads stay.
  assert.equal(firstSentence('## 概况'), '概况');
  assert.equal(firstSentence('- **接口** 已核对，测试补齐'), '接口 已核对，测试补齐');
  assert.equal(firstSentence('见 [说明文档](docs/README.md)，颜色已核对！'), '见 说明文档，颜色已核对！');
  assert.equal(firstSentence('`out/report.html` 写好了。'), 'out/report.html 写好了。');
  // A quoted or listed line still counts as words.
  assert.equal(firstSentence('> 摘要：颜色已核对'), '摘要：颜色已核对');
  assert.equal(firstSentence('1. 第一步做完了；第二步还没'), '第一步做完了；第二步还没');
  // The report's own bookkeeping is skipped over: tables, rules, the [进度] line.
  assert.equal(firstSentence('| 列 | 表 |\n| --- | --- |\n[进度] 待验收：下面这句才算\n颜色已核对。'), '颜色已核对。');
  assert.equal(firstSentence('[进度] 待验收：颜色已核对'), '');
  assert.equal(firstSentence('---\n\n[进度] 需要你：确认取值'), '');
  // A sentence that runs past the cut keeps its beginning rather than the whole paragraph.
  const long = '很长的一句话'.repeat(60) + '。';
  assert.equal(firstSentence(long).length, 200);
  // The end marks only these three count; a comma does not end a sentence.
  assert.equal(firstSentence('接口好了，测试也好了'), '接口好了，测试也好了');
  // A sentence that hands over with a colon is not done saying what it means: the next line's
  // opening is spliced on with a space, a list item continuing after its mark is gone.
  assert.equal(firstSentence('核对结果如下：\n- 接口约定全部对齐，测试通过。'), '核对结果如下： 接口约定全部对齐，测试通过。');
  assert.equal(firstSentence('done this round:\n1. all tests pass'), 'done this round: all tests pass');
  // The splice runs only until a sentence lands.
  assert.equal(firstSentence('结果如下：\n颜色已核对。其余照旧。'), '结果如下： 颜色已核对。');
});

test('the folded card previews the words of a report, its protocol line left out', () => {
  assert.equal(deliveredPreview('截图都齐了，深色取值等你确认。\n[进度] 需要你：深色下的取值需要确认'), '截图都齐了，深色取值等你确认。');
  assert.equal(deliveredPreview('## 概况\n- 颜色已核对\n[进度] 待验收：交回'), '概况 颜色已核对');
});

test('the body the row opens leaves the [进度] bookkeeping out, words and all', () => {
  assert.equal(withoutProgress('已处理。\n\n[进度] 待验收：接口和测试都交回'), '已处理。');
  // A marked or quoted copy of the line goes too; the words around it stay.
  assert.equal(withoutProgress('先说结论。\n> [进度] 需要你：确认取值\n\n后面还有。'), '先说结论。\n\n后面还有。');
  // What only looks like the line — another word in the brackets, or none of the three states — stays.
  assert.equal(withoutProgress('[进度] 已完成：全部交回'), '[进度] 已完成：全部交回');
  assert.equal(withoutProgress('接口好了，测试也好了'), '接口好了，测试也好了');
  // Nothing left to say: the row falls back to its own "no text" words.
  assert.equal(withoutProgress('[进度] 进行中'), '');
});
