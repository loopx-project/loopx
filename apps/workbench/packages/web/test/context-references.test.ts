import { test } from 'node:test';
import assert from 'node:assert/strict';
import { attachFallbackReference, attachedReferences, mergeReferences, recoverNoteReferences, referenceThreads, replaceAutomaticReference, referencePreview, splitReferences, withReferences, type ContextReference } from '../src/context-references';
import { conversationPointer, copiedQuoteText, pastedPointer } from '../src/clipboard';
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
  for (const text of ['hello', '<loochat-references>\nnot json\n</loochat-references>']) assert.deepEqual(splitReferences(text), { references: [], body: text });
});

test('combined queued messages recover every reference without exposing wire data', () => {
  const other = { ...reference, id: 'other', title: 'other folder' };
  const text = withReferences([reference], 'First question.') + '\n\n' + withReferences([other], 'Second question.');
  const split = splitReferences(text);
  assert.deepEqual(split.references, [reference, other]);
  assert.match(split.body, /First question\./);
  assert.match(split.body, /Second question\./);
  assert.ok(!split.body.includes('loochat-references'));
  assert.ok(!referencePreview(text).includes('loochat-references'));
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

test('new thread here replaces only its automatic source and a plain new thread clears it', () => {
  const a = { ...reference, id: 'A', kind: 'thread' as const, sourceId: 'A', pointer: true };
  const c = { ...a, id: 'C', sourceId: 'C' };
  const first = replaceAutomaticReference([reference], a);
  assert.deepEqual(replaceAutomaticReference(first, c), [reference, { ...c, automatic: true }]);
  assert.deepEqual(replaceAutomaticReference(first, null), [reference]);
});

test('a note owns its reference through reload and queued-message recovery, with no grant left after deletion', () => {
  const permission: ContextReference = { ...reference, kind: 'thread', sourceId: 'source', turnId: 'turn', pointer: true };
  const note = { quote: 'passage', comment: 'explain', source: { threadId: 'source', turnId: 'turn', elsewhere: true }, reference: permission };
  let value = '{}';
  const storage = { getItem: () => value, setItem: (_key: string, next: string) => { value = next; } };
  writeDraft(storage, 'draft', { text: '', notes: [note], references: [] });
  assert.deepEqual(readDrafts(storage).draft.notes, [note]);
  const wire = withNotes([note], withReferences(attachedReferences([], [note]), 'question'));
  const split = splitNotes(wire);
  const recovered = recoverNoteReferences(split.notes, splitReferences(split.body).references);
  assert.deepEqual(recovered.references, []);
  assert.deepEqual(attachedReferences(recovered.references, recovered.notes), [permission]);
  assert.deepEqual(attachedReferences(recovered.references, []), []);
});

test('reference picker retains the source and all same-directory threads, and searches beyond eight matches', () => {
  const threads = Array.from({ length: 20 }, (_, i) => ({ id: String(i), title: `discussion ${i}`, cwd: i < 12 ? '/elsewhere' : '/project' }));
  const source = { cwd: '/project', sourceId: '11', threadId: '19' };
  const defaults = referenceThreads(threads, source, '');
  assert.equal(defaults[0].id, '11');
  assert.deepEqual(defaults.slice(1, 8).map(thread => thread.id), ['12', '13', '14', '15', '16', '17', '18']);
  assert.equal(referenceThreads(threads, source, 'discussion').length, 19);
  assert.ok(!defaults.some(thread => thread.id === '19'));
});

test('portable pointers carry hub ids and round-trip quotes without device storage', () => {
  const thread = { id: 'hub-id', title: '原对话 / "title"' };
  assert.deepEqual(pastedPointer(conversationPointer(thread)), { threadId: 'hub-id', title: thread.title });
  assert.deepEqual(pastedPointer(copiedQuoteText({ text: 'first\n第二行', threadId: 'hub-id', turnId: 'turn-id' }, thread.title)),
    { threadId: 'hub-id', turnId: 'turn-id', title: thread.title, quote: 'first\n第二行' });
  assert.equal(pastedPointer('ordinary text'), null);
  assert.equal(pastedPointer('Calli: missing\ncalli://thread/%ZZ'), null);
});

test('portable pointers preserve their machine scope and reject ambiguous query parameters', () => {
  const thread = { id: 'same-hub-id', title: 'Source' };
  assert.deepEqual(pastedPointer(conversationPointer(thread, undefined, 'machine-A')),
    { threadId: thread.id, title: thread.title, machineId: 'machine-A' });
  assert.deepEqual(pastedPointer(copiedQuoteText({ text: 'quoted', threadId: thread.id, turnId: 'turn', machineId: 'machine-A' }, thread.title)),
    { threadId: thread.id, title: thread.title, turnId: 'turn', machineId: 'machine-A', quote: 'quoted' });
  for (const query of ['machine=A&machine=B', 'machine=', 'turn=', 'machine=A&unknown=B']) {
    assert.equal(pastedPointer(`Calli: Source\ncalli://thread/id?${query}`), null);
  }
});

test('attachFallbackReference preserves leading notes, deduplicates by sourceId and respects the 12-reference limit', () => {
  const source: ContextReference = { id: 'ref-source', kind: 'thread', title: 'Source thread', path: '/project', sourceId: 'thread-1', snapshotSeq: 9, at: '2026-01-01T00:00:00Z' };
  const notes = [{ quote: 'Selected passage', comment: '' }];
  const noted = withNotes(notes, 'Explain this');
  const attached = attachFallbackReference(noted, source);
  const parsedNotes = splitNotes(attached);
  assert.deepEqual(parsedNotes.notes, notes);
  assert.deepEqual(splitReferences(parsedNotes.body), { references: [source], body: 'Explain this' });
  assert.equal(attachFallbackReference(attached, { ...source, id: 'ref-duplicate' }), attached);
  const twelve = Array.from({ length: 12 }, (_, i): ContextReference => ({ ...reference, id: `dir-${i}` }));
  const full = withNotes(notes, withReferences(twelve, 'Full'));
  assert.equal(attachFallbackReference(full, source), full);
  assert.equal(attachFallbackReference(noted, null), noted);
});
