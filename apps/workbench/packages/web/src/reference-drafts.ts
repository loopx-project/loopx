import { attachedReferences, mergeReferences, recoverNoteReferences, splitReferences, withReferences, type ContextReference } from './context-references';
import type { PastedPointer } from './clipboard';
import { splitNotes, withNotes, type Note } from './timeline';

/** A free-text field owns every visible attachment, including the ones carried by its notes. */
export interface ReferenceDraft {
  text: string;
  notes: Note[];
  references: ContextReference[];
}

export function parseReferenceDraft(text: string): ReferenceDraft {
  // Queues and stored displays may put metadata before the notes; drafts put it after them.
  const referenced = splitReferences(text);
  const noted = splitNotes(referenced.body);
  return { text: noted.body, ...recoverNoteReferences(noted.notes, referenced.references) };
}

export function referenceDraftText(draft: ReferenceDraft): string {
  return withNotes(draft.notes, withReferences(attachedReferences(draft.references, draft.notes), draft.text));
}

export function referenceDraftIds(draft: ReferenceDraft): string[] {
  return attachedReferences(draft.references, draft.notes).filter(reference => !reference.pending).map(reference => reference.id);
}

export function addDraftReference(draft: ReferenceDraft, reference: ContextReference, pointer?: PastedPointer): ReferenceDraft {
  if (pointer?.quote !== undefined && reference.kind === 'thread' && reference.sourceId && reference.turnId) {
    return { ...draft, notes: [...draft.notes, { quote: pointer.quote, comment: '', reference,
      source: { threadId: reference.sourceId, turnId: reference.turnId, elsewhere: true } }] };
  }
  return { ...draft, references: mergeReferences(draft.references, [reference]) };
}

/** Compact answer traces show the person's words and source labels, never protocol envelopes. */
export function referenceDraftPreview(text: string): string {
  const draft = parseReferenceDraft(text);
  return [...draft.notes.map(note => [note.quote, note.comment].filter(Boolean).join(' · ')),
    ...draft.references.map(reference => reference.title), draft.text].filter(Boolean).join(' · ');
}
