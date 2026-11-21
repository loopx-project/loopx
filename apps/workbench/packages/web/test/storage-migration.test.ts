import { test } from 'node:test';
import assert from 'node:assert/strict';
import { migrateStorage } from '../src/storage-migration.js';

function memory(entries: Record<string, string>): Storage & { data: Map<string, string> } {
  const data = new Map(Object.entries(entries));
  return {
    data,
    get length() { return data.size; },
    key: (i: number) => [...data.keys()][i] ?? null,
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => { data.set(key, value); },
    removeItem: (key: string) => { data.delete(key); },
    clear: () => data.clear(),
  };
}

test('keys from before the rename carry over once, without overwriting or removing anything', () => {
  const storage = memory({
    'rah.token': 'old-token', 'rah.ext.board.filters.v1': '{"a":1}', 'rah_onboard_dismissed': '1',
    'rah:plan-skipped': 'x', 'limi.site.appearance': 'dark', 'rah.locale': 'en', 'calli.locale': 'ja', 'other': 'kept',
  });
  migrateStorage(storage);
  assert.equal(storage.getItem('calli.token'), 'old-token', 'the saved token still signs in');
  assert.equal(storage.getItem('calli.ext.board.filters.v1'), '{"a":1}');
  assert.equal(storage.getItem('calli_onboard_dismissed'), '1');
  assert.equal(storage.getItem('calli:plan-skipped'), 'x');
  assert.equal(storage.getItem('calli.site.appearance'), 'dark');
  assert.equal(storage.getItem('calli.locale'), 'ja', 'a value saved under the new name wins');
  assert.equal(storage.getItem('rah.token'), 'old-token', 'old keys stay for an older version');
  assert.equal(storage.getItem('other'), 'kept');

  storage.removeItem('calli.token'); // signed out
  migrateStorage(storage);
  assert.equal(storage.getItem('calli.token'), null, 'a key removed since does not come back');
});
