import { test } from 'node:test';
import assert from 'node:assert/strict';
import { applyModelPreference, readModelPreferences } from '../../daemon/src/model-preferences.js';

test('switching models restores independent effort and speed, including explicit defaults', () => {
  let choice = applyModelPreference(undefined, { model: 'a', effort: 'high', speed: 'priority' });
  choice = applyModelPreference(choice, { model: 'b' });
  assert.equal(choice.effort, null);
  choice = applyModelPreference(choice, { effort: 'low' });
  choice = applyModelPreference(choice, { model: 'a' });
  assert.equal(choice.effort, 'high');
  assert.equal(choice.speed, 'priority');
  choice = applyModelPreference(choice, { effort: null });
  choice = applyModelPreference(choice, { model: 'b' });
  assert.equal(choice.effort, 'low');
  choice = applyModelPreference(choice, { model: 'a' });
  assert.equal(choice.effort, null);
});

test('legacy server choices remain readable without consulting model availability', () => {
  const raw = { pi: { model: 'temporarily-unavailable', effort: 'high', speed: null } };
  const saved = readModelPreferences(raw);
  assert.equal(saved.pi.model, 'temporarily-unavailable');
  assert.deepEqual(saved.pi.perModel, { 'temporarily-unavailable': { effort: 'high', speed: null } });
  assert.deepEqual(raw, { pi: { model: 'temporarily-unavailable', effort: 'high', speed: null } });
  assert.deepEqual(readModelPreferences(null), {});
  assert.deepEqual(readModelPreferences({ pi: [], claude: false }), {});
});
