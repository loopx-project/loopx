import assert from 'node:assert/strict';
import test from 'node:test';
import { setLocale } from '../src/i18n/index.js';
import { statusWord } from '../src/loopx/launch.js';

const CODES = ['connected_without_run', 'active_state_agent_todo', 'active_state_user_gate', 'active_state_user_todo', 'state_refreshed', 'state_incomplete', 'state_projection_gap', 'run_artifact_missing', 'unknown'];

test('every status the core projects reads as a word in each language, never as its code', () => {
  for (const id of ['zh', 'en', 'ja'] as const) {
    setLocale(id);
    for (const code of CODES) {
      const word = statusWord(code);
      assert.ok(word && !word.includes('_') && !word.startsWith('loopx.'), `${id} ${code} -> ${word}`);
    }
  }
  setLocale('zh');
});

test('a status without a word still loses its underscores', () => {
  assert.equal(statusWord('some_new_status'), 'some new status');
});
