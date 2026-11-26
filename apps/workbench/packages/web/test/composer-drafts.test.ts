import { test } from 'node:test';
import assert from 'node:assert/strict';
import { prefillComposerDraft } from '../src/components/Composer';
import { readDrafts } from '../src/drafts';
import { withNotes } from '../src/timeline';
import { withReferences, type ContextReference } from '../src/context-references';

const reference: ContextReference = { id: 'ref', kind: 'thread', sourceId: 'source', turnId: 'turn', title: 'Source', path: '/project', at: '2026-01-01T00:00:00Z' };

test('prefilled handover drafts restore visible note-owned references before the composer mounts', () => {
  let value = '{}';
  const storage = { getItem: () => value, setItem: (_key: string, next: string) => { value = next; } };
  const previous = globalThis.window;
  globalThis.window = { localStorage: storage } as unknown as Window & typeof globalThis;
  try {
    const note = { quote: 'Selected passage', comment: 'My request', source: { threadId: 'source', turnId: 'turn', elsewhere: true } };
    prefillComposerDraft('prefilled-reference-test', withNotes([note], withReferences([reference], 'Continue here.')));
    const saved = readDrafts(storage)['prefilled-reference-test'];
    assert.equal(saved.text, 'Continue here.');
    assert.deepEqual(saved.notes, [{ ...note, reference }]);
    assert.deepEqual(saved.references ?? [], [], 'the note owns the permission instead of hiding a second attachment');
    prefillComposerDraft('canonical-queue-reference-test', withReferences([reference], withNotes([note], 'Returned from the queue.')));
    const queued = readDrafts(storage)['canonical-queue-reference-test'];
    assert.equal(queued.text, 'Returned from the queue.');
    assert.deepEqual(queued.notes, [{ ...note, reference }]);
    assert.deepEqual(queued.references ?? [], [], 'canonical display metadata cannot hide a note-owned attachment');
  } finally { globalThis.window = previous; }
});
