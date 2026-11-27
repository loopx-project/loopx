import { test } from 'node:test';
import assert from 'node:assert/strict';
import { addDraftReference, parseReferenceDraft, referenceDraftIds, referenceDraftText, referenceDraftPreview } from '../src/reference-drafts';
import { withNotes } from '../src/timeline';
import { withReferences, type ContextReference } from '../src/context-references';

const reference: ContextReference = { id: 'reference', kind: 'thread', sourceId: 'source', turnId: 'turn', machineId: 'machine', title: 'Canonical source', path: '/project', pointer: true, at: '2026-01-01T00:00:00Z' };

test('independent inputs preserve note-owned permissions, and deleting the note removes its attachment', () => {
  const draft = addDraftReference(parseReferenceDraft('My request'), reference, { threadId: 'source', turnId: 'turn', title: 'Clipboard title', quote: 'Quoted passage' });
  assert.equal(draft.text, 'My request');
  assert.equal(draft.notes[0].quote, 'Quoted passage');
  assert.deepEqual(referenceDraftIds(draft), ['reference']);
  const restored = parseReferenceDraft(referenceDraftText(draft));
  assert.deepEqual(restored, draft);
  assert.deepEqual(referenceDraftIds({ ...restored, notes: [] }), []);
});

test('canonical queued displays recover notes before or after metadata without hiding their grants', () => {
  const note = { quote: 'Quoted passage', comment: 'My comment', source: { threadId: 'source', turnId: 'turn', elsewhere: true } };
  const { turnId: _turnId, ...source } = reference;
  const other = { ...source, id: 'other', title: 'Other source' };
  const references = [reference, other];
  const expected = { text: 'My request', notes: [{ ...note, reference }], references: [other] };
  for (const wire of [withNotes([note], withReferences(references, 'My request')), withReferences(references, withNotes([note], 'My request'))]) {
    const restored = parseReferenceDraft(wire);
    assert.deepEqual(restored, expected);
    assert.deepEqual(referenceDraftIds({ ...restored, notes: [] }), ['other'], 'deleting the recovered note removes only its visible attachment');
    assert.deepEqual(parseReferenceDraft(referenceDraftText(restored)), expected);
    assert.equal(referenceDraftPreview(wire), 'Quoted passage · My comment · Other source · My request');
  }
});

test('compact previews retain words, comments and source labels without protocol envelopes', () => {
  const wire = referenceDraftText({ text: 'What I want', notes: [{ quote: 'Quoted passage', comment: 'My comment', reference,
    source: { threadId: 'source', turnId: 'turn', elsewhere: true } }], references: [{ ...reference, id: 'other-reference', title: 'Other source', turnId: undefined }] });
  assert.equal(referenceDraftPreview(wire), 'Quoted passage · My comment · Other source · What I want');
  assert.equal(referenceDraftPreview('An ordinary value'), 'An ordinary value');
  assert.doesNotMatch(referenceDraftPreview(wire), /loochat-references|calli-references|来源：|\[注释|reference/);
  assert.equal(referenceDraftPreview(withReferences([reference], '')), 'Canonical source');
});
