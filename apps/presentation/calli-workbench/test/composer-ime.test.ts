import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isImeComposing } from '../src/components/Composer.js';

const event = (isComposing: boolean, keyCode: number) => ({
  nativeEvent: { isComposing, keyCode },
}) as Parameters<typeof isImeComposing>[0];

test('IME composition covers native composition state and Safari key code 229', () => {
  assert.equal(isImeComposing(event(true, 13)), true);
  assert.equal(isImeComposing(event(false, 229)), true);
  assert.equal(isImeComposing(event(false, 13)), false);
});
