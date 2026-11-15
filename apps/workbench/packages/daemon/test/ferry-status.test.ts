import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { ferryModelKey, readFerryQueueObservations } from '../src/adapters/ferry.js';

const now = 1_793_800_000_000;

test('Ferry cache retains only valid observed positions and explicit nonqueued records', async t => {
  // A missing/invalid number is unknown; only Ferry's explicit position-free record means nonqueued.
  const home = mkdtempSync(join(tmpdir(), 'calli-ferry-cache-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  t.mock.method(Date, 'now', () => now);
  writeFileSync(join(home, 'queue-seen.json'), JSON.stringify({
    'GPT-6.1-Sol': { at: now - 120_000, position: 550 },
    'gpt-6-astra': { at: now },
    'openrouter-4o': { at: now - 31 * 60_000, position: 99 },
    'stale-nonqueued': { at: now - 31 * 60_000 },
    'future': { at: now + 1, position: 3 },
    'string-time': { at: String(now), position: 3 },
    'zero-time': { at: 0, position: 3 },
    'string-position': { at: now, position: '3' },
    'fractional-position': { at: now, position: 1.5 },
    'zero-position': { at: now, position: 0 },
    'negative-position': { at: now, position: -1 },
    'null-position': { at: now, position: null },
    'missing-time': { position: 3 },
    'bad-record': [now, 3],
    'null-record': null,
  }));
  const observations = await readFerryQueueObservations({ FERRY_HOME: home });
  assert.equal(observations.size, 4);
  assert.deepEqual(observations.get(ferryModelKey('claude-gpt-6-1-sol')), { at: now - 120_000, position: 550 });
  assert.equal(ferryModelKey('claude-gpt-6-1-sol-max[1m]'), ferryModelKey('GPT-6.1-Sol'));
  assert.equal(ferryModelKey('claude-gpt-6-1-sol[1m]'), ferryModelKey('GPT-6.1-Sol'));
  assert.deepEqual(observations.get('gpt6astra'), { at: now, position: 0 });
  assert.deepEqual(observations.get('openrouter4o'), { at: now - 31 * 60_000, position: null });
  assert.deepEqual(observations.get('stalenonqueued'), { at: now - 31 * 60_000, position: null });
});

test('missing Ferry, malformed caches and unreadable cache paths never reject metadata reads', async t => {
  // Optional local files must not turn healthy gateway discovery into an error or a fabricated zero.
  const home = mkdtempSync(join(tmpdir(), 'calli-ferry-missing-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  assert.equal((await readFerryQueueObservations({ HOME: home })).size, 0);
  assert.equal((await readFerryQueueObservations({ FERRY_HOME: join(home, 'not-installed') })).size, 0);
  const file = join(home, 'queue-seen.json');
  for (const content of ['invalid JSON', 'null', '[]', '42', '"text"', '{}', JSON.stringify({ model: false }), ' '.repeat(1024 * 1024 + 1)]) {
    writeFileSync(file, content);
    assert.equal((await readFerryQueueObservations({ FERRY_HOME: home })).size, 0);
  }
  assert.equal((await readFerryQueueObservations({ FERRY_HOME: file })).size, 0, 'a file cannot be a cache directory');
  rmSync(file);
  mkdirSync(file);
  assert.equal((await readFerryQueueObservations({ FERRY_HOME: home })).size, 0, 'a cache directory is not a regular file');
});

test('Ferry cache uses instance home defaults and an explicit FERRY_HOME takes precedence', async t => {
  // Never inspect the developer's Ferry cache; exercise the platform path with isolated directories.
  const home = mkdtempSync(join(tmpdir(), 'calli-ferry-path-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  t.mock.method(Date, 'now', () => now);
  const defaultDirectory = process.platform === 'darwin'
    ? join(home, 'Library', 'Application Support', 'ferry')
    : process.platform === 'win32' ? join(home, 'AppData', 'Local', 'ferry') : join(home, '.local', 'share', 'ferry');
  mkdirSync(defaultDirectory, { recursive: true });
  writeFileSync(join(defaultDirectory, 'queue-seen.json'), JSON.stringify({ main: { at: now, position: 5 } }));
  assert.equal((await readFerryQueueObservations({ HOME: home })).get('main')?.position, 5);
  const override = join(home, 'override');
  mkdirSync(override);
  writeFileSync(join(override, 'queue-seen.json'), JSON.stringify({ main: { at: now, position: 12 } }));
  assert.equal((await readFerryQueueObservations({ HOME: home, FERRY_HOME: override })).get('main')?.position, 12);
  if (process.platform !== 'darwin' && process.platform !== 'win32') {
    const xdg = join(home, 'xdg');
    mkdirSync(join(xdg, 'ferry'), { recursive: true });
    writeFileSync(join(xdg, 'ferry', 'queue-seen.json'), JSON.stringify({ main: { at: now } }));
    assert.equal((await readFerryQueueObservations({ HOME: home, XDG_DATA_HOME: xdg })).get('main')?.position, 0);
  }
});
