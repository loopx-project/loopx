import { test } from 'node:test';
import assert from 'node:assert/strict';
import { VoiceConversation, type VoiceMessage } from '../src/voice-conversation.js';
import type { VoiceSignal } from '../src/voice-sessions.js';

const speech = (itemId: string, role: 'user' | 'assistant', text: string, final = false): VoiceSignal =>
  ({ type: 'transcript', itemId, role, text, final });

test('speech streams without database writes, then commits corrected final text once per item', () => {
  let seq = 7;
  const saved: VoiceMessage[] = [];
  const conversation = new VoiceConversation(() => seq, m => saved.push(m));
  const first = conversation.accept(speech('one', 'user', 'Find '));
  seq = 10;
  conversation.accept(speech('one', 'user', 'newz'));
  conversation.accept(speech('two', 'assistant', 'Looking'));
  assert.equal(saved.length, 0);
  const final = conversation.accept(speech('one', 'user', 'Find news', true));
  conversation.accept(speech('one', 'user', 'Find news', true));
  assert.equal(saved.length, 1);
  assert.equal(saved[0].text, 'Find news');
  assert.equal(saved[0].afterSeq, 7);
  assert.equal(first.type === 'transcript' && first.itemId, final.type === 'transcript' && final.itemId);
  conversation.accept(speech('two', 'assistant', '**News**\n\n[Source](https://example.com)', true));
  assert.equal(saved[1].afterSeq, 10);
  assert.equal(saved[1].role, 'assistant');
  assert.match(saved[1].text, /\*\*News\*\*/);
  conversation.close();
  assert.equal(saved.length, 2);
});

test('interrupted speech is retained once, without inventing a finished message or a task', () => {
  const saved: VoiceMessage[] = [];
  const conversation = new VoiceConversation(() => 0, m => saved.push(m));
  conversation.accept(speech('draft', 'user', 'Could you'));
  conversation.close();
  conversation.close();
  conversation.accept(speech('late', 'assistant', 'Late', true));
  assert.equal(saved.length, 1);
  assert.equal(saved[0].text, 'Could you');
  assert.equal(saved[0].final, false);
});

test('session namespaces, roles and canonical replacements remain distinct', () => {
  const saved: VoiceMessage[] = [];
  const a = new VoiceConversation(() => 0, m => saved.push(m));
  const b = new VoiceConversation(() => 0, m => saved.push(m));
  a.accept(speech('same-native-id', 'user', 'first', true));
  b.accept(speech('same-native-id', 'user', 'second', true));
  assert.notEqual(saved[0].itemId, saved[1].itemId);
  a.accept(speech('same-native-id', 'user', 'corrected', true));
  assert.equal(saved[2].itemId, saved[0].itemId);
  assert.equal(saved[2].text, 'corrected');
  a.accept(speech('same-native-id', 'assistant', 'wrong role', true));
  a.accept(speech('same-native-id', 'user', 'late delta'));
  assert.equal(saved.length, 3);
  a.accept(speech('same-native-id', 'user', '', true));
  assert.equal(saved[3].text, '');
  assert.equal(saved[3].itemId, saved[0].itemId);
});

test('unknown speakers are never guessed and long calls have bounded draft memory', () => {
  const saved: VoiceMessage[] = [];
  const conversation = new VoiceConversation(() => 0, m => saved.push(m));
  conversation.accept({ type: 'transcript', itemId: 'unknown', text: 'opaque', final: true });
  assert.equal(saved.length, 0);
  for (let i = 0; i < 300; i++) conversation.accept(speech(`item${i}`, 'user', 'text'));
  assert.equal(saved.length, 44);
  conversation.close();
  assert.equal(saved.length, 300);
  assert.equal(new Set(saved.map(m => m.itemId)).size, 300);
});
