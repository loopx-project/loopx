import { readDrafts, writeDraft } from '../src/drafts';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pastedQuote, quoteMarkdown, rememberCopiedQuote } from '../src/clipboard';
import { splitNotes, withNotes } from '../src/timeline';

function withStorage(run: () => void) {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  const data = new Map<string, string>();
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
    getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => { data.set(k, v); },
  } });
  try { run(); } finally {
    if (previous) Object.defineProperty(globalThis, 'localStorage', previous);
    else Reflect.deleteProperty(globalThis, 'localStorage');
  }
}

test('a message copied as a quote pastes back as that quote, with where it came from', () => withStorage(() => {
  const text = '第一行\n\n第二行';
  rememberCopiedQuote({ text, threadId: 'T1', turnId: 'U1' });
  assert.deepEqual(pastedQuote(quoteMarkdown(text)), { text, threadId: 'T1', turnId: 'U1' });
  // Windows line ends and stray blank space around the paste do not matter.
  assert.deepEqual(pastedQuote(`\n${quoteMarkdown(text).replace(/\n/g, '\r\n')}\n`), { text, threadId: 'T1', turnId: 'U1' });
}));

test('any pasted Markdown quote becomes a quote; mixed or plain text stays text', () => withStorage(() => {
  assert.deepEqual(pastedQuote('> from Lark\n>\n> more'), { text: 'from Lark\n\nmore' });
  assert.equal(pastedQuote('> quoted\nand my words'), null);
  assert.equal(pastedQuote('plain words'), null);
  assert.equal(pastedQuote('   '), null);
}));

test('a note from another thread says so and reads back the same', () => {
  const sent = withNotes([{ quote: 'passage', comment: '', source: { threadId: 'T1', turnId: 'U1', elsewhere: true } }], 'body');
  assert.match(sent, /^\[注释 1\]\n来源：另一个会话，原文和上下文用 loochat history T1 --turn U1 读\n> passage/);
  assert.deepEqual(splitNotes(sent).notes[0].source, { threadId: 'T1', turnId: 'U1', elsewhere: true });
  const chain = withNotes([{ quote: 'p', comment: '', source: { threadId: 'T2', turnId: 'U2' } }], '');
  assert.deepEqual(splitNotes(chain).notes[0].source, { threadId: 'T2', turnId: 'U2' });
});

test('reloading a pasted-note draft preserves its cross-conversation source kind', () => withStorage(() => {
  const note = { quote: 'passage', comment: '', source: { threadId: 'T1', turnId: 'U1', elsewhere: true } };
  writeDraft(localStorage, 'T2', { text: 'my draft', notes: [note] });
  const draft = readDrafts(localStorage).T2;
  assert.equal(draft.text, 'my draft');
  assert.deepEqual(draft.notes, [note]);
  assert.match(withNotes(draft.notes, draft.text), /来源：另一个会话/);
}));
