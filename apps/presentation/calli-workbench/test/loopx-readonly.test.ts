/**
 * Path scans below match generic local absolute-path patterns; they quote no specific machine path.
 *
 * Zero-write enforcement:
 *  1. no non-GET fetch method may appear anywhere in src/,
 *  2. no storage APIs (localStorage is the web app's display-pref territory),
 *  3. every Calli write method throws ReadOnlyError,
 *  4. unknown stream paths fail loudly.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { LoopXApiAdapter, ReadOnlyError } from '../src/loopx/index.ts';
function tsFiles(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...tsFiles(full));
    else if (full.endsWith('.ts')) out.push(full);
  }
  return out;
}

const SRC = join(import.meta.dirname, '..', 'src', 'loopx');

/**
 * Strip comments so prose in docblocks cannot trip the scanners. Block
 * comments are only stripped when they START a line: a string literal like
 * 'M2: /api/chat/* approval surfaces' contains the sequence mid-line, and a
 * naive strip would swallow real code after it (a scanner blind spot).
 */
function codeOnly(text: string): string {
  return text
    .replace(/^[ \t]*\/\*[\s\S]*?\*\//gm, '')
    .replace(/^[ \t]*\/\/.*$/gm, '');
}

test('no non-GET fetch method anywhere in src/', () => {
  const offenders: string[] = [];
  for (const file of tsFiles(SRC)) {
    const text = codeOnly(readFileSync(file, 'utf8'));
    if (/method\s*:\s*['"`](POST|PUT|PATCH|DELETE)/i.test(text)) offenders.push(file);
    if (/['"`](POST|PUT|PATCH|DELETE)['"`]\s*,?\s*\)/.test(text)) offenders.push(`${file} (method literal)`);
  }
  assert.deepEqual(offenders, []);
});

test('no storage APIs in src/ (display prefs belong to the web app)', () => {
  const offenders = tsFiles(SRC).filter((f) => /localStorage|sessionStorage|indexedDB/.test(codeOnly(readFileSync(f, 'utf8'))));
  assert.deepEqual(offenders, []);
});

const LOCAL_PATH = /\/(home|Users|root)\/|\/data\d*\//;

test('no local absolute paths or runtime_root in the view-model layer', () => {
  // runtime_root is only tolerated inside the raw-payload contract file; it
  // must never be read by mappers or the adapter into a Calli view model.
  for (const file of tsFiles(SRC)) {
    const text = codeOnly(readFileSync(file, 'utf8'));
    if (file.endsWith(join('contracts', 'status-schema.ts'))) {
      assert.ok(!LOCAL_PATH.test(text), `local path literal in ${file}`);
      continue;
    }
    assert.ok(!text.includes('runtime_root'), `runtime_root must not be used in ${file} (privacy ruling)`);
    assert.ok(!LOCAL_PATH.test(text), `local absolute path in ${file}`);
  }
});

test('every Calli write method refuses with ReadOnlyError', async () => {
  const api = new LoopXApiAdapter();
  const writes: Array<[string, unknown[]]> = [
    ['startTurn', ['t', 'hi']],
    ['cancel', ['t']],
    ['decide', ['a', {}]],
    ['answerInput', ['i', { cancelled: true }]],
    ['createThread', [{}]],
    ['updateThread', ['t', {}]],
    ['deleteThread', ['t']],
    ['createTodo', ['x']],
    ['updateTodo', ['1', {}]],
    ['deleteTodo', ['1']],
    ['moveTodo', ['1', {}]],
    ['linkTodo', ['1', 't']],
    ['unlinkTodo', ['1', 't']],
    ['createSection', ['x']],
    ['renameSection', ['s', 'x']],
    ['deleteSection', ['s']],
    ['addWorkdir', ['/x']],
    ['removeWorkdir', ['/x']],
    ['saveGeneralSettings', [{ startupPage: 'new-thread', autoArchiveDays: null }]],
  ];
  for (const [method, args] of writes) {
    await assert.rejects(
      (api as unknown as Record<string, (...a: unknown[]) => Promise<unknown>>)[method](...args),
      (e: unknown) => e instanceof ReadOnlyError && e.method === method,
      `${method} must throw ReadOnlyError naming itself`,
    );
  }
});

test('stream rejects paths with no LoopX counterpart instead of pretending', async () => {
  const api = new LoopXApiAdapter();
  await assert.rejects(
    api.stream('/api/voice/events', () => {}, new AbortController().signal),
    /does not serve stream path/,
  );
});
