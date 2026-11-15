import { test } from 'node:test';
import assert from 'node:assert/strict';
import { crumbsOf, fileRef, foldersTo, languageOf, resolveRelative, sizeLabel, splitHighlighted } from '../src/files.js';

test('a link to a place in a file keeps the line and drops the rest of the suffix', () => {
  assert.deepEqual(fileRef('src/a.ts:12'), { path: 'src/a.ts', line: 12 });
  assert.deepEqual(fileRef('src/a.ts:12:5'), { path: 'src/a.ts', line: 12 });
  assert.deepEqual(fileRef('/w/a.ts#L7'), { path: '/w/a.ts', line: 7 });
  assert.deepEqual(fileRef('/w/a.ts#L7-L20'), { path: '/w/a.ts', line: 7 });
  assert.deepEqual(fileRef('notes-v2'), { path: 'notes-v2' });
  assert.deepEqual(fileRef('a.ts:0'), { path: 'a.ts' });
  assert.deepEqual(fileRef(':12'), { path: ':12' });
});

test('crumbs run from the thread folder down to the file', () => {
  assert.deepEqual(crumbsOf('/w/code', '/w/code/src/a.ts'), [
    { name: 'code', path: '/w/code' },
    { name: 'src', path: '/w/code/src' },
    { name: 'a.ts', path: '/w/code/src/a.ts' },
  ]);
  assert.deepEqual(crumbsOf('/w/code/', '/w/code'), [{ name: 'code', path: '/w/code' }]);
  assert.deepEqual(crumbsOf('/w/code', '/w/codex/a.ts'), [{ name: 'a.ts', path: '/w/codex/a.ts' }], 'a sibling with a longer name is not inside');
  assert.deepEqual(foldersTo('/w/code', '/w/code/src/b/a.ts'), ['/w/code', '/w/code/src', '/w/code/src/b']);
});

test('links inside a Markdown file are taken from its folder; web links are not files', () => {
  assert.equal(resolveRelative('/w/code/docs', '../src/a.ts'), '/w/code/src/a.ts');
  assert.equal(resolveRelative('/w/code/docs', './x.md#part'), '/w/code/docs/x.md');
  assert.equal(resolveRelative('/w/code/docs', '/etc/hosts'), '/etc/hosts');
  assert.equal(resolveRelative('/w/code', 'https://example.com'), null);
  assert.equal(resolveRelative('/w/code', '#top'), null);
});

test('language by name, plain text when unknown', () => {
  assert.equal(languageOf('/w/App.tsx'), 'typescript');
  assert.equal(languageOf('/w/Dockerfile'), 'dockerfile');
  assert.equal(languageOf('/w/Dockerfile.dev'), 'dockerfile');
  assert.equal(languageOf('/w/README.MD'), 'markdown');
  assert.equal(languageOf('/w/notes.txt'), null);
  assert.equal(languageOf('/w/.hidden'), null);
});

test('highlighted HTML splits into lines that each stand on their own', () => {
  const html = '<span class="a">x\ny</span> z\n<span class="b"><span class="c">p\nq</span></span>';
  assert.deepEqual(splitHighlighted(html), [
    '<span class="a">x</span>',
    '<span class="a">y</span> z',
    '<span class="b"><span class="c">p</span></span>',
    '<span class="b"><span class="c">q</span></span>',
  ]);
  assert.deepEqual(splitHighlighted('a\n'), ['a', '']);
});

test('sizes', () => {
  assert.equal(sizeLabel(900), '900 B');
  assert.equal(sizeLabel(2048), '2.0 KB');
  assert.equal(sizeLabel(50 * 1024), '50 KB');
  assert.equal(sizeLabel(3 * 1024 * 1024), '3.0 MB');
});
