import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { join } from 'node:path';
import { referencedFile, referencesFile } from '../src/artifacts.js';

test('artifact references match whole paths, including encoded links, spaces and source lines', () => {
  const path = '/tmp/report/截图 (1).png';
  for (const text of [
    `[截图](<${path}>)`,
    `[截图](file://${encodeURI(path)})`,
    `[截图](${encodeURI(path)} "Caption")`,
    `\`${path}\``,
    `"${path}"`,
  ]) assert.equal(referencesFile(text, path), true, text);
  assert.equal(referencesFile('[日志](/tmp/report/log.txt:12)', '/tmp/report/log.txt'), true);
  assert.equal(referencesFile('[日志](/tmp/report/log.txt#L12-L20)', '/tmp/report/log.txt'), true);
  assert.equal(referencesFile('图片在：/tmp/report/chart.png。', '/tmp/report/chart.png'), true);
  assert.equal(referencesFile('`/tmp/report/a%20b.png`', '/tmp/report/a%20b.png'), true);
});

test('mentions do not grant path prefixes, web URLs, relative paths or double-decoded paths', () => {
  for (const text of [
    '/tmp/report/chart.png.bak', '/tmp/report/chart.png/child',
    '[web](https://example.com/tmp/report/chart.png)',
    'https://example.com/tmp/report/chart.png',
    'tmp/report/chart.png', '/tmp/report/',
    '[file](file://remote/tmp/report/chart.png)',
    '[file](/tmp/report/%2563hart.png)',
  ]) assert.equal(referencesFile(text, '/tmp/report/chart.png'), false, text);
});

test('home-relative references grant only the exact named file', () => {
  const path = join(homedir(), 'out/chart.png');
  for (const text of ['~/out/chart.png', '`~/out/chart.png`', '[chart](~/out/chart.png)', '[chart](%7E/out/chart.png#L1)']) {
    assert.equal(referencesFile(text, path), true, text);
    assert.equal(referencesFile(text, join(homedir(), 'out/other.png')), false);
  }
  assert.equal(referencesFile('`~/out/my report.md`', join(homedir(), 'out/my report.md')), true);
  for (const text of ['~/out/chart.png.bak', '~/out/', '~', 'file://~/out/chart.png', 'https://example.com/~/out/chart.png']) {
    assert.equal(referencesFile(text, path), false, text);
  }
});

test('a named file resolves anywhere, but links beneath a temp folder are refused', () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-referenced-'));
  try {
    const file = join(dir, 'notes.txt');
    writeFileSync(file, 'notes');
    symlinkSync(file, join(dir, 'link.txt'));
    assert.equal(referencedFile(file), realpathSync(file));
    assert.equal(referencedFile('/etc/passwd'), realpathSync('/etc/passwd'));
    for (const path of [join(dir, 'link.txt'), join(dir, 'gone.txt'), dir, '/etc', 'notes.txt', '//host/notes.txt']) {
      assert.equal(referencedFile(path), null, path);
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
