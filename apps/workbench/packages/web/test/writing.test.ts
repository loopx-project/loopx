import { test } from 'node:test';
import assert from 'node:assert/strict';
import { rehypeArticleMarks } from '../src/article.js';
import { ApiError } from '../src/api.js';
import { setLocale } from '../src/i18n/index.js';
import { BODY_DRAFTS_KEY, bodyName, conflictHash, MAX_BODY_DRAFTS, readBodyDraft, relativePicture, stageLabel, WRITING_STAGES, writeBodyDraft } from '../src/writing.js';

const memory = (initial: Record<string, string> = {}) => {
  const data = new Map(Object.entries(initial));
  return { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => void data.set(k, v), data };
};

test('the stages come in writing order, each with a name in every language', () => {
  assert.deepEqual(WRITING_STAGES, ['draft', 'second', 'third', 'figures', 'polish']);
  assert.deepEqual(WRITING_STAGES.map(stageLabel), ['初稿', '二稿', '三稿', '配图', '润色']);
  setLocale('en');
  try {
    assert.equal(stageLabel('figures'), 'Figures');
  } finally {
    setLocale('zh');
  }
});

test('the bar names the body by its file name', () => {
  assert.equal(bodyName('writing/思考是人类的事情.md'), '思考是人类的事情.md');
  assert.equal(bodyName('a.md'), 'a.md');
});

test('a relative picture is found beside the Markdown file; web and non-pictures are not', () => {
  const md = '/work/blog/writing/essay.md';
  assert.equal(relativePicture(md, 'essay/fig1.png'), '/work/blog/writing/essay/fig1.png');
  assert.equal(relativePicture(md, './a.JPG'), '/work/blog/writing/a.JPG');
  assert.equal(relativePicture(md, '../shared/logo.svg'), '/work/blog/shared/logo.svg');
  assert.equal(relativePicture(md, 'essay/%E5%9B%BE.webp'), '/work/blog/writing/essay/图.webp');
  assert.equal(relativePicture(md, 'https://example.com/a.png'), null);
  assert.equal(relativePicture(md, 'data:image/png;base64,AAAA'), null);
  assert.equal(relativePicture(md, 'notes.txt'), null);
  assert.equal(relativePicture(md, undefined), null);
});

test('a refused save hands back the hash on disk; other failures do not', () => {
  assert.equal(conflictHash(new ApiError('changed', { error: 'changed', hash: 'abc' }, 409)), 'abc');
  assert.equal(conflictHash(new ApiError('forbidden', {}, 403)), null);
  assert.equal(conflictHash(new ApiError('changed', {}, 409)), null);
  assert.equal(conflictHash(new Error('offline')), null);
});

test('unsaved body text is kept per thread and file, and forgotten once saved', () => {
  const s = memory();
  writeBodyDraft(s, 't1', '/w/writing/a.md', { text: 'half', baseHash: 'h1' }, 1);
  writeBodyDraft(s, 't1', '/w/writing/b.md', { text: 'other', baseHash: 'h2' }, 2);
  writeBodyDraft(s, 't2', '/w/writing/a.md', { text: 'elsewhere', baseHash: 'h3' }, 3);
  assert.deepEqual(readBodyDraft(s, 't1', '/w/writing/a.md'), { text: 'half', baseHash: 'h1', at: 1 });
  assert.deepEqual(readBodyDraft(s, 't2', '/w/writing/a.md'), { text: 'elsewhere', baseHash: 'h3', at: 3 });
  writeBodyDraft(s, 't1', '/w/writing/a.md', null);
  assert.equal(readBodyDraft(s, 't1', '/w/writing/a.md'), null);
  assert.ok(readBodyDraft(s, 't1', '/w/writing/b.md'));
});

test('an emptied body is still a draft: deleting everything is an edit', () => {
  const s = memory();
  writeBodyDraft(s, 't', 'p', { text: '', baseHash: 'h' }, 1);
  assert.deepEqual(readBodyDraft(s, 't', 'p'), { text: '', baseHash: 'h', at: 1 });
});

test('only the most recent body drafts are kept, and junk in storage is ignored', () => {
  const s = memory({ [BODY_DRAFTS_KEY]: JSON.stringify({ 'x:y': { text: 1 }, 'a:b': { text: 'ok', baseHash: 'h', at: 0 } }) });
  assert.equal(readBodyDraft(s, 'x', 'y'), null);
  assert.ok(readBodyDraft(s, 'a', 'b'));
  for (let i = 1; i <= MAX_BODY_DRAFTS + 3; i++) writeBodyDraft(s, `t${i}`, 'p', { text: 'x', baseHash: 'h' }, i);
  assert.equal(Object.keys(JSON.parse(s.data.get(BODY_DRAFTS_KEY)!)).length, MAX_BODY_DRAFTS);
  assert.equal(readBodyDraft(s, 't1', 'p'), null);
  assert.ok(readBodyDraft(s, `t${MAX_BODY_DRAFTS + 3}`, 'p'));
  assert.equal(readBodyDraft(memory({ [BODY_DRAFTS_KEY]: 'not json' }), 'a', 'b'), null);
});


test('article marks leave code and Mermaid source unchanged', () => {
  const code = { type: 'element', tagName: 'code', children: [{ type: 'text', value: 'A["【待补：例子】"] --> B' }] };
  const tree = { type: 'root', children: [{ type: 'element', tagName: 'pre', children: [code] }, code] };
  const before = structuredClone(tree);
  rehypeArticleMarks()(tree);
  assert.deepEqual(tree, before);
});
