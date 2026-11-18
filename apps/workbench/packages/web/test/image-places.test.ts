import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement, type ComponentProps } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { placeImages, stripPaths } from '../src/image-places.js';
import { imagePlaces } from '../src/timeline.js';

/** The answer as drawn, with each strip of pictures written out as [paths] and line breaks left out. */
const draw = (text: string) => renderToStaticMarkup(createElement(Markdown, {
  remarkPlugins: [remarkGfm, placeImages(imagePlaces(text))],
  components: { div: (props: ComponentProps<'div'>) => createElement('i', null, `[${stripPaths(props as { className?: string; 'data-paths'?: unknown })?.join(',')}]`) },
}, text)).replace(/\n/g, '');

test('pictures follow the passage that names them, inside its list item, and take its words', () => {
  const html = draw('Intro.\n\n- light: /tmp/shots/light.png\n- dark: /tmp/shots/dark.png\n\nThe end.');
  assert.equal(html, '<p>Intro.</p><ul><li>light:<i>[/tmp/shots/light.png]</i></li><li>dark:<i>[/tmp/shots/dark.png]</i></li></ul><p>The end.</p>');
});

test('pictures in one paragraph share a strip; tables and code blocks get theirs after them', () => {
  assert.match(draw('Before ![a](a.png) and b.png here.\n\nAfter.'), /^<p>Before <img[^>]*\/> and\s+here\.<\/p><i>\[a\.png,b\.png\]<\/i><p>After\.<\/p>$/);
  assert.match(draw('| shot |\n| --- |\n| c.png |\n\nNext.'), /<\/table><i>\[c\.png\]<\/i><p>Next\.<\/p>/);
  // A path inside a code block is part of what was run: it stays, and still gets its picture.
  assert.match(draw('```\nsaved d.png\n```\n\nNext.'), /saved d\.png<\/code><\/pre><i>\[d\.png\]<\/i><p>Next\.<\/p>/);
});

test('a picture named twice is shown once, where it is first named; no places, no strips', () => {
  const html = draw('See e.png.\n\nAgain e.png.');
  assert.equal(html.match(/\[e\.png\]/g)?.length, 1);
  assert.equal(html, '<p>See </p><i>[e.png]</i><p>Again </p>');
  assert.equal(draw('Nothing to show.'), '<p>Nothing to show.</p>');
  assert.equal(stripPaths({ className: 'note', 'data-paths': '["x.png"]' }), null);
});

test('a line that said nothing but the paths goes; the pictures are its words now', () => {
  const html = draw('截图如下：\n\n- `/tmp/shots/light.png`、`/tmp/shots/dark.png`\n\n都绿了。');
  assert.equal(html, '<p>截图如下：</p><ul><li><i>[/tmp/shots/light.png,/tmp/shots/dark.png]</i></li></ul><p>都绿了。</p>');
});

test('a link whose words are the path gives way; one with a label of its own stays', () => {
  const html = draw('See [shot.png](shot.png) and [the shot](kept.png).');
  assert.equal(html, '<p>See  and <a href="kept.png">the shot</a>.</p><i>[shot.png,kept.png]</i>');
});

test('a path mid-sentence is cut with the space it leaned on; a longer word around it is left', () => {
  assert.match(draw('Report at /tmp/r.png ready.'), /^<p>Report at\s+ready\.<\/p>/);
  assert.equal(draw('Not x/tmp/r.pngx but /tmp/r.png.'), '<p>Not x/tmp/r.pngx but </p><i>[/tmp/r.png]</i>');
});
