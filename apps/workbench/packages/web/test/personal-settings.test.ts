import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pickAgent, startsNewConversation } from '../src/personal-settings.js';
import { t } from '../src/i18n/index.js';

const value = { agent: 'pi', model: 'fake/plain', directory: '/d' };

test('picking the same agent keeps the model; only a real change resets it', () => {
  assert.equal(pickAgent(value, 'pi'), value);
  assert.deepEqual(pickAgent(value, 'codex'), { agent: 'codex', model: null, directory: '/d' });
});

test('the warning shows only when saving would really start a new conversation', () => {
  const initial = { ...value, routerThreadId: 'r1' };
  assert.equal(startsNewConversation(initial, value), false);
  assert.equal(startsNewConversation(initial, pickAgent(value, 'pi')), false, 'picking the same agent again is no change');
  assert.equal(startsNewConversation(initial, { ...value, model: 'fake/loose' }), true);
  assert.equal(startsNewConversation(initial, pickAgent(value, 'codex')), true);
  assert.equal(startsNewConversation(initial, { ...value, directory: '/other' }), true, 'the conversation is tied to its directory too');
  assert.equal(startsNewConversation({ ...value, routerThreadId: null }, { ...value, model: 'fake/loose' }), false, 'no conversation yet: nothing to leave');
  assert.equal(startsNewConversation(value, { ...value, model: 'fake/loose' }), false);
  assert.equal(t('personal.newConversation'), '保存后，下一条消息会开一个新对话');
});
