import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mergeReferences, referencePreview, splitReferences, withReferences, type ContextReference } from '../src/context-references';
import { splitNotes, withNotes } from '../src/timeline';
import { readDrafts, writeDraft } from '../src/drafts';

const reference: ContextReference = { id: 'example', kind: 'directory', title: 'a "quoted" folder', path: '/project/a "quoted" folder', at: '2026-01-01T00:00:00Z' };

test('references coexist with numbered comments, preserve quoted paths and round-trip through drafts', () => {
  const notes = [{ quote: 'first passage\nsecond line', comment: 'change this', file: 'src/main.ts' }, { quote: 'whole reply', comment: '' }];
  const message = withNotes(notes, withReferences([reference], 'Keep my words.'));
  const noted = splitNotes(message);
  assert.deepEqual(noted.notes, notes);
  assert.deepEqual(splitReferences(noted.body), { references: [reference], body: 'Keep my words.' });
  assert.match(message, /\[注释 2\]/);
  assert.equal(referencePreview(withReferences([reference], 'Compare.')), reference.title + '\nCompare.');
  let value = '{}';
  const storage = { getItem: () => value, setItem: (_key: string, next: string) => { value = next; } };
  writeDraft(storage, 'thread', { text: '', notes: [], references: [reference] });
  assert.deepEqual(readDrafts(storage).thread.references, [reference], 'a references-only draft survives a reload');
  writeDraft(storage, 'thread', { text: 'words', notes, references: [reference] });
  assert.deepEqual(readDrafts(storage).thread.notes, notes);
  assert.deepEqual(mergeReferences([reference], [reference]), [reference]);
  writeDraft(storage, 'thread', null);
  assert.deepEqual(readDrafts(storage), {});
});

test('ordinary messages and malformed blocks are not silently removed', () => {
  for (const text of ['hello', '<calli-references>\nnot json\n</calli-references>']) assert.deepEqual(splitReferences(text), { references: [], body: text });
});

test('combined queued messages recover every reference without exposing wire data', () => {
  const other = { ...reference, id: 'other', title: 'other folder' };
  const text = withReferences([reference], 'First question.') + '\n\n' + withReferences([other], 'Second question.');
  const split = splitReferences(text);
  assert.deepEqual(split.references, [reference, other]);
  assert.match(split.body, /First question\./);
  assert.match(split.body, /Second question\./);
  assert.ok(!split.body.includes('calli-references'));
  assert.ok(!referencePreview(text).includes('calli-references'));
});

test('message context snapshots stay separate from notes when a draft is reloaded', () => {
  const message: ContextReference = { ...reference, kind: 'message', sourceId: 'source-thread', turnId: 'source-turn' };
  let value = '{}';
  const storage = { getItem: () => value, setItem: (_key: string, next: string) => { value = next; } };
  const notes = [{ quote: 'A selected passage', comment: 'Explain this.' }];
  writeDraft(storage, 'thread', { text: 'My question', notes, references: [message] });
  assert.deepEqual(readDrafts(storage).thread.references, [message]);
  assert.deepEqual(readDrafts(storage).thread.notes, notes);
  assert.deepEqual(splitReferences(withReferences([message], 'My question')), { references: [message], body: 'My question' });
});
