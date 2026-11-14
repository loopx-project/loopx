import test from 'node:test';
import assert from 'node:assert/strict';
import { ApiError } from '../src/api.js';
import { t } from '../src/i18n/index.js';

test('missing backend endpoints explain the upgrade while domain and other HTTP errors retain their meaning', () => {
  for (const message of ['not found', 'HTTP 404']) {
    const error = new ApiError(message, {}, 404);
    assert.equal(String(error), t('api.updateBackend'));
    assert.equal(error.status, 404);
  }
  assert.equal(new ApiError('thread not found', {}, 404).message, 'thread not found');
  assert.equal(new ApiError('not found', {}, 500).message, 'not found');
  assert.equal(new ApiError('unauthorized', {}, 401).message, 'unauthorized');
});
