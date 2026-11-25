import { test } from 'node:test';
import assert from 'node:assert/strict';
import React, { type ComponentProps } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { rehypeArticleMarks } from '../src/article.js';
import { placeImages, stripPaths } from '../src/image-places.js';
import { MARKDOWN_REHYPE_PLUGINS, preserveNonHtmlTags } from '../src/markdown-html.js';
import { threadLinkIndex, threadMentions, trimAutolinkJunk } from '../src/markdown-links.js';
import { imagePlaces } from '../src/timeline.js';

const keepUrl = (url: string) => url;

const render = (text: string) =>
  renderToStaticMarkup(
    React.createElement(Markdown, {
      remarkPlugins: [remarkGfm, trimAutolinkJunk, preserveNonHtmlTags],
      rehypePlugins: MARKDOWN_REHYPE_PLUGINS,
      urlTransform: keepUrl,
    }, text),
  );

test('renders <br>, <br/>, and <br /> as real line breaks in tables and prose', () => {
  const table = render('| 列1 | 列2 |\n| --- | --- |\n| 第一行<br/>第二行<br>第三行 | A<br />B |');
  assert.ok(table.includes('<td>第一行<br/>第二行<br/>第三行</td>'), table);
  assert.ok(table.includes('<td>A<br/>B</td>'), table);
  assert.ok(!table.includes('&lt;br'), table);

  const prose = render('段落一<br/>段落二<BR>段落三');
  assert.equal(prose, '<p>段落一<br/>段落二<br/>段落三</p>');
});

test('renders GFM allowlisted HTML elements such as details, kbd, sub, sup, mark, and u', () => {
  const html = render(
    '<details>\n<summary>展开详情</summary>\n\n按 <kbd>⌘</kbd>+<kbd>K</kbd>，水是 H<sub>2</sub>O，x<sup>2</sup>，<mark>重点</mark> 和 <u>下划线</u>。\n</details>',
  );
  assert.ok(html.includes('<details>'), html);
  assert.ok(html.includes('<summary>展开详情</summary>'), html);
  assert.ok(html.includes('<kbd>⌘</kbd>+<kbd>K</kbd>'), html);
  assert.ok(html.includes('H<sub>2</sub>O'), html);
  assert.ok(html.includes('x<sup>2</sup>'), html);
  assert.ok(html.includes('<mark>重点</mark>'), html);
  assert.ok(html.includes('<u>下划线</u>'), html);
});

test('leaves <br/> and HTML untouched inside inline code and fenced code blocks', () => {
  const inline = render('用 `<br/>` 换行，或者 `<details><summary>x</summary></details>`。');
  assert.ok(inline.includes('<code>&lt;br/&gt;</code>'), inline);
  assert.ok(inline.includes('<code>&lt;details&gt;&lt;summary&gt;x&lt;/summary&gt;&lt;/details&gt;</code>'), inline);

  const block = render('```html\n<div><br/></div>\n```');
  assert.ok(block.includes('&lt;div&gt;&lt;br/&gt;&lt;/div&gt;'), block);
});

test('sanitizes dangerous tags, event handlers, and unsafe URL protocols', () => {
  const xss = render(
    'Safe <script>alert(1)</script><img src="https://example.com/a.png" onerror="alert(1)" /><a href="javascript:alert(1)">bad</a><iframe src="https://evil.example"></iframe> end',
  );
  assert.ok(!xss.includes('<script'), xss);
  assert.ok(!xss.includes('alert(1)'), xss);
  assert.ok(!xss.includes('onerror'), xss);
  assert.ok(!xss.includes('javascript:'), xss);
  assert.ok(!xss.includes('<iframe'), xss);
  assert.ok(xss.includes('Safe'), xss);
  assert.ok(xss.includes('end'), xss);
});

test('preserves non-HTML prose placeholders and generics instead of swallowing them', () => {
  const inline = render('请填入 <user_id> 并检查 Promise<T> 和 Map<K, V> 的返回。');
  assert.ok(inline.includes('&lt;user_id&gt;'), inline);
  assert.ok(inline.includes('Promise&lt;T&gt;'), inline);

  const standalone = render('<calli-progress>\n\n正常段落');
  assert.ok(standalone.includes('<p>&lt;calli-progress&gt;</p>'), standalone);
  assert.ok(standalone.includes('<p>正常段落</p>'), standalone);
});

test('handles unfinished streaming HTML tags gracefully without losing text', () => {
  const partialTag = render('正在输出第一行<br');
  assert.equal(partialTag, '<p>正在输出第一行&lt;br</p>');

  const openDetails = render('<details><summary>标题</summary>\n\n流式内容还在继续');
  assert.ok(openDetails.includes('<details>'), openDetails);
  assert.ok(openDetails.includes('流式内容还在继续'), openDetails);
});

test('keeps app-specific file://, thread://, relative links, image strips, and article marks intact', () => {
  const index = threadLinkIndex([
    { id: '37958237-dd35-413c-8981-7d0b9ce55234', title: '测试会话' },
  ]);
  const text = [
    '见 [notes.md](file:///tmp/notes.md)、[rel](./doc.md) 和「测试会话」（37958237）。',
    '',
    '截图：/tmp/shots/demo.png',
  ].join('\n');

  const html = renderToStaticMarkup(
    React.createElement(Markdown, {
      remarkPlugins: [remarkGfm, trimAutolinkJunk, preserveNonHtmlTags, threadMentions(index), placeImages(imagePlaces(text))],
      rehypePlugins: MARKDOWN_REHYPE_PLUGINS,
      urlTransform: keepUrl,
      components: {
        div: (props: ComponentProps<'div'>) =>
          React.createElement('i', null, `[${stripPaths(props as { className?: string; 'data-paths'?: unknown })?.join(',') ?? ''}]`),
      },
    }, text),
  );

  assert.ok(html.includes('href="file:///tmp/notes.md"'), html);
  assert.ok(html.includes('href="./doc.md"'), html);
  assert.ok(html.includes('href="thread://37958237-dd35-413c-8981-7d0b9ce55234"'), html);
  assert.ok(html.includes('<i>[/tmp/shots/demo.png]</i>'), html);

  const articleHtml = renderToStaticMarkup(
    React.createElement(Markdown, {
      remarkPlugins: [remarkGfm, preserveNonHtmlTags],
      rehypePlugins: [...MARKDOWN_REHYPE_PLUGINS, rehypeArticleMarks],
    }, '〔补〕第一行<br/>第二行 【待补：数据】'),
  );
  assert.ok(articleHtml.includes('class="writing-added"'), articleHtml);
  assert.ok(articleHtml.includes('<br/>'), articleHtml);
  assert.ok(articleHtml.includes('<span class="writing-todo">待补：数据</span>'), articleHtml);
});
