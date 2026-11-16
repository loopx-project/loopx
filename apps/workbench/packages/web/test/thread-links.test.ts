import { test } from 'node:test';
import assert from 'node:assert/strict';
import remarkGfm from 'remark-gfm';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import Markdown from 'react-markdown';
import { threadLinkIndex, threadMentions, trimAutolinkJunk } from '../src/markdown-links.js';
import { linkTarget } from '../src/paths.js';

const THREADS = [
  { id: '37958237-dd35-413c-8981-7d0b9ce55234', title: '🧪调取Bjev resolver' },
  { id: '0de90e6b-578f-4ca8-b4fb-34bd0ca29051', title: '🗂️盘点Calli会话未完事项与待办' },
  { id: '11111111-2222-3333-4444-555555555555', title: '重复标题' },
  { id: '66666666-7777-3333-4444-555555555555', title: '重复标题' },
];
const index = threadLinkIndex(THREADS);
// Same promise the thread view makes: every address is kept for the link component to judge.
const keepUrl = (url: string) => url;
const render = (text: string, withThreads = true) =>
  renderToStaticMarkup(React.createElement(Markdown, {
    remarkPlugins: [remarkGfm, trimAutolinkJunk, ...(withThreads ? [threadMentions(index)] : [])],
    urlTransform: keepUrl,
  }, text));

test('a thread address parses whole ids, short prefixes and case, and refuses empty ones', () => {
  assert.deepEqual(linkTarget('thread://37958237-dd35-413c-8981-7d0b9ce55234'), { kind: 'thread', id: '37958237-dd35-413c-8981-7d0b9ce55234' });
  assert.deepEqual(linkTarget('thread://37958237'), { kind: 'thread', id: '37958237' });
  assert.deepEqual(linkTarget('THREAD://37958237'), { kind: 'thread', id: '37958237' });
  assert.deepEqual(linkTarget('thread://37958237/'), { kind: 'thread', id: '37958237' });
  assert.deepEqual(linkTarget('thread://'), { kind: 'none' });
  assert.deepEqual(linkTarget('app://whatever'), { kind: 'none' });
  assert.deepEqual(linkTarget('https://example.com'), { kind: 'web', url: 'https://example.com' });
});

test('「标题」（id 前缀） becomes a jump link when both name the same thread', () => {
  assert.equal(
    render('是会话「🧪调取Bjev resolver」（37958237）。'),
    '<p>是会话<a href="thread://37958237-dd35-413c-8981-7d0b9ce55234" title="🧪调取Bjev resolver">「🧪调取Bjev resolver」（37958237）</a>。</p>',
  );
});

test('half-width parentheses around the id prefix link as well', () => {
  assert.equal(
    render('见「🧪调取Bjev resolver」(37958237) 的分支'),
    '<p>见<a href="thread://37958237-dd35-413c-8981-7d0b9ce55234" title="🧪调取Bjev resolver">「🧪调取Bjev resolver」(37958237)</a> 的分支</p>',
  );
});

test('a whole thread id in prose becomes a jump link', () => {
  assert.equal(
    render('接手 hub 对话 0de90e6b-578f-4ca8-b4fb-34bd0ca29051 的任务'),
    '<p>接手 hub 对话 <a href="thread://0de90e6b-578f-4ca8-b4fb-34bd0ca29051" title="🗂️盘点Calli会话未完事项与待办">0de90e6b-578f-4ca8-b4fb-34bd0ca29051</a> 的任务</p>',
  );
});

test('a title alone or an id that disagrees with it stays plain text', () => {
  // The id prefix belongs to another thread: a document or PR quoted the same way is not rewired.
  assert.equal(render('看文档「🧪调取Bjev resolver」（12345678）'), '<p>看文档「🧪调取Bjev resolver」（12345678）</p>');
  // No thread carries this title.
  assert.equal(render('是会话「不存在的标题」（37958237）'), '<p>是会话「不存在的标题」（37958237）</p>');
  // Too short to be a thread prefix at all.
  assert.equal(render('PR（379582）合并了'), '<p>PR（379582）合并了</p>');
});

test('titles that repeat link only when the id prefix picks one out', () => {
  assert.equal(
    render('「重复标题」（11111111）'),
    '<p><a href="thread://11111111-2222-3333-4444-555555555555" title="重复标题">「重复标题」（11111111）</a></p>',
  );
  // The same title on two threads and nothing to tell them apart: leave it alone.
  assert.equal(render('「重复标题」（99999999）'), '<p>「重复标题」（99999999）</p>');
});

test('a link of its own keeps its label and destination', () => {
  const label = '会话 0de90e6b-578f-4ca8-b4fb-34bd0ca29051 的记录';
  assert.equal(
    render(`[${label}](https://docs.example.com/log)`),
    `<p><a href="https://docs.example.com/log">${label}</a></p>`,
  );
});

test('two mentions in one message each become their own link', () => {
  assert.equal(
    render('先看「🧪调取Bjev resolver」（37958237），再看 0de90e6b-578f-4ca8-b4fb-34bd0ca29051。'),
    '<p>先看<a href="thread://37958237-dd35-413c-8981-7d0b9ce55234" title="🧪调取Bjev resolver">「🧪调取Bjev resolver」（37958237）</a>，'
      + '再看 <a href="thread://0de90e6b-578f-4ca8-b4fb-34bd0ca29051" title="🗂️盘点Calli会话未完事项与待办">0de90e6b-578f-4ca8-b4fb-34bd0ca29051</a>。</p>',
  );
});

test('without threads to match, the text stays as it was written', () => {
  assert.equal(render('是会话「🧪调取Bjev resolver」（37958237）。', false), '<p>是会话「🧪调取Bjev resolver」（37958237）。</p>');
});
