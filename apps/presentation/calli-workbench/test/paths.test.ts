import { test } from 'node:test';
import assert from 'node:assert/strict';
import { folderName, linkTarget, relativePath, shortenPaths } from '../src/paths.js';

test('folderName is the last segment', () => {
  assert.equal(folderName('/work/code'), 'code');
  assert.equal(folderName('/work/code/'), 'code');
  assert.equal(folderName(''), '');
});

test('relativePath strips the thread directory, in whichever spelling the agent used', () => {
  const roots = ['/home/me/code', '/data/home/me/code']; // the second is what the symlink resolves to
  assert.equal(relativePath('/home/me/code/src/a.ts', roots), 'src/a.ts');
  assert.equal(relativePath('/data/home/me/code/src/a.ts', roots), 'src/a.ts');
  assert.equal(relativePath('/home/me/code', roots), '.');
  assert.equal(relativePath('/elsewhere/file.txt', roots), '/elsewhere/file.txt');
  assert.equal(relativePath('src/already-relative.ts', roots), 'src/already-relative.ts');
  // A sibling directory that merely starts with the same letters is not inside it.
  assert.equal(relativePath('/home/me/code-old/x.ts', roots), '/home/me/code-old/x.ts');
  assert.equal(relativePath('/home/me/code/x.ts', ['', '/']), '/home/me/code/x.ts');
});

test('shortenPaths removes the directory only where a path starts', () => {
  const roots = ['/home/me/code', '/data/home/me/code'];
  assert.equal(shortenPaths('Write /home/me/code/notes.txt', roots), 'Write notes.txt');
  assert.equal(shortenPaths('Write /data/home/me/code/notes.txt', roots), 'Write notes.txt');
  assert.equal(shortenPaths('cat "/home/me/code/a.ts" /home/me/code/b.ts', roots), 'cat "a.ts" b.ts');
  assert.equal(shortenPaths('Edit /home/me/code/a.ts', []), 'Edit /home/me/code/a.ts');
});

test('shortenPaths never cuts a piece out of the middle of a longer path', () => {
  // The thread directory is spelled /home/me/code, but the agent reports the resolved path.
  // Removing "/home/me/code/" from inside it would leave "/datanotes.txt".
  assert.equal(shortenPaths('Write /data/home/me/code/notes.txt', ['/home/me/code']), 'Write /data/home/me/code/notes.txt');
  assert.equal(shortenPaths('see /backup/home/me/code/a.ts', ['/home/me/code']), 'see /backup/home/me/code/a.ts');
});

test('special characters in the directory name are matched literally', () => {
  assert.equal(shortenPaths('Read /w/my.app (v2)/a+b.ts', ['/w/my.app (v2)']), 'Read a+b.ts');
  assert.equal(shortenPaths('Read /w/myXapp (v2)/a.ts', ['/w/my.app (v2)']), 'Read /w/myXapp (v2)/a.ts');
});

import { nameAttachments } from '../src/paths.js';

test('nameAttachments names the pictures the user sent instead of showing their storage path', () => {
  const id = '2085c159-172e-4508-ab0d-14578970e049';
  const img = '4e0325b1-2b38-4ee2-83ee-5f6ef27433b5';
  assert.equal(nameAttachments(`Read /srv/hub/data/attachments/${id}/${img}.png`), 'Read 发送的图片');
  assert.equal(nameAttachments(`view_file "/x/attachments/${id}/${img}.JPG" and /x/attachments/${id}/${img}.webp`), 'view_file "发送的图片" and 发送的图片');
  assert.equal(nameAttachments('Read /work/app/attachments/logo.png'), 'Read /work/app/attachments/logo.png', 'only the hub\'s own layout');
});

test('linkTarget: web addresses open elsewhere, files on the host are never navigated to', () => {
  assert.deepEqual(linkTarget('https://example.com/a?b=1#c'), { kind: 'web', url: 'https://example.com/a?b=1#c' });
  assert.deepEqual(linkTarget('mailto:me@example.com'), { kind: 'web', url: 'mailto:me@example.com' });
  assert.deepEqual(linkTarget('file:///work/app/dino.svg'), { kind: 'file', path: '/work/app/dino.svg' });
  assert.deepEqual(linkTarget('FILE:///work/a%20b/%E5%9B%BE.png'), { kind: 'file', path: '/work/a b/图.png' });
  assert.deepEqual(linkTarget('out/chart.png'), { kind: 'file', path: 'out/chart.png' });
  assert.deepEqual(linkTarget('/work/app/src/main.ts:12'), { kind: 'file', path: '/work/app/src/main.ts:12' });
  assert.deepEqual(linkTarget('main.ts:12'), { kind: 'file', path: 'main.ts:12' }, 'a line number is not a scheme');
  assert.deepEqual(linkTarget('100%.txt'), { kind: 'file', path: '100%.txt' }, 'a name that is not encoded');
  assert.deepEqual(linkTarget('#user-content-fn-1'), { kind: 'anchor' });
  for (const href of ['', '  ', 'javascript:alert(1)', 'JavaScript:alert(1)', 'data:text/html,<script>', 'vscode://file/x', 'ftp://host/x', 'file://']) {
    assert.deepEqual(linkTarget(href), { kind: 'none' }, href);
  }
});
