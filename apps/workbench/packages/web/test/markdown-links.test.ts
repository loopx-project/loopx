import { test } from 'node:test';
import assert from 'node:assert/strict';
import remarkGfm from 'remark-gfm';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import Markdown from 'react-markdown';
import { trimAutolinkJunk } from '../src/markdown-links.js';

const render = (text: string) =>
  renderToStaticMarkup(React.createElement(Markdown, { remarkPlugins: [remarkGfm, trimAutolinkJunk] }, text));

test('autolinks do not swallow emphasis markers or CJK punctuation', () => {
  const html = render('打开（或刷新）\n**http://192.0.2.10:8899/**（PID 3174724）：');
  assert.ok(!/%EF%BC%88/.test(html), 'no percent-encoded CJK inside the link: ' + html);
  assert.ok(html.includes('href="http://192.0.2.10:8899/"'), 'the link is the plain address: ' + html);
  assert.ok(html.includes('<strong>'), 'the emphasis is restored: ' + html);
  assert.ok(!/\*\*/.test(html), 'no literal markers remain: ' + html);
});

test('a plain autolink is left alone', () => {
  const html = render('see https://example.com/a for more');
  assert.ok(html.includes('href="https://example.com/a"'), html);
  assert.ok(html.includes('>https://example.com/a</a> for more'), html);
});

test('trailing CJK punctuation is handed back to the text', () => {
  const html = render('见 http://10.0.0.1:8899/，然后继续。');
  assert.ok(html.includes('href="http://10.0.0.1:8899/"'), html);
  assert.ok(html.includes('8899/</a>，然后继续。'), html);
});

test('an explicit document link keeps its full label and HTTPS destination', () => {
  const label = '项目 Advisor / SystemOne 实验交接：换机器恢复与继续操作';
  const html = render(`交接文档：[${label}](https://docs.example.com/docx/handover)。`);
  assert.equal(html, `<p>交接文档：<a href="https://docs.example.com/docx/handover">${label}</a>。</p>`);
});

test('explicit links keep punctuation, stars, titles, and file destinations', () => {
  for (const [label, destination] of [
    ['报告（第一版），继续。', 'https://docs.example.com/report'],
    ['Download!', 'https://docs.example.com/download'],
    ['file_name*', './notes.md'],
    ['文件：第 12 行', '/work/notes.md#L12'],
  ]) {
    assert.equal(render(`[${label}](${destination} "Details")`), `<p><a href="${destination}" title="Details">${label}</a></p>`);
  }
});

test('an explicit link whose label equals its URL is not trimmed', () => {
  const address = 'https://example.com/报告，第一版。';
  assert.equal(render(`[${address}](${address})`), `<p><a href="https://example.com/%E6%8A%A5%E5%91%8A%EF%BC%8C%E7%AC%AC%E4%B8%80%E7%89%88%E3%80%82">${address}</a></p>`);
});

test('angle-bracket autolinks retain explicitly delimited punctuation', () => {
  const address = 'https://example.com/report!';
  assert.equal(render(`<${address}>`), `<p><a href="${address}">${address}</a></p>`);
});

test('reference links keep their full labels and destinations', () => {
  assert.equal(render('[文档：恢复步骤][doc]\n\n[doc]: https://docs.example.com/handover'), '<p><a href="https://docs.example.com/handover">文档：恢复步骤</a></p>');
});

test('www autolinks keep their HTTP prefix when punctuation is trimmed', () => {
  assert.equal(render('www.example.com/report，继续。'), '<p><a href="http://www.example.com/report">www.example.com/report</a>，继续。</p>');
});

test('literal autolink cleanup stays isolated from explicit links in the same paragraph', () => {
  assert.equal(render('[文档：恢复](https://docs.example.com/handover) 和 **https://example.com/report**（进程）'), '<p><a href="https://docs.example.com/handover">文档：恢复</a> 和 <strong><a href="https://example.com/report">https://example.com/report</a></strong>（进程）</p>');
});
