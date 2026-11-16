import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createElement, type ComponentProps } from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { Composer } from '../src/components/Composer.js';
import { readDrafts, writeDraft } from '../src/drafts.js';

for (const body of ['Unsent B text', '']) test(`a completed send only clears its own draft (${body ? 'text and note' : 'note only'})`, async () => {
  const globals = ['window', 'document', 'requestAnimationFrame', 'cancelAnimationFrame'] as const;
  const previous = globals.map(key => Object.getOwnPropertyDescriptor(globalThis, key));
  const data = new Map<string, string>();
  const storage = { getItem: (key: string) => data.get(key) ?? null, setItem: (key: string, value: string) => { data.set(key, value); } };
  const windowMock = Object.assign(new EventTarget(), { localStorage: storage, matchMedia: () => Object.assign(new EventTarget(), { matches: false }), setTimeout, clearTimeout });
  Object.assign(globalThis, { window: windowMock, document: new EventTarget(), requestAnimationFrame: () => 0, cancelAnimationFrame() {}, IS_REACT_ACT_ENVIRONMENT: true });
  const a = `a-${body}`, b = `b-${body}`;
  const note = { quote: 'A passage for B', comment: 'B question' };
  writeDraft(storage, a, { text: 'Send A', notes: [] });
  writeDraft(storage, b, { text: body, notes: [note] });
  let resolve!: () => void;
  const sending = new Promise<void>(done => { resolve = done; });
  const props: ComponentProps<typeof Composer> = {
    draftKey: a, running: false, placeholder: '', onSend: () => sending, onStop() {}, header: null, left: null, right: null,
    commands: [], actions: [], hiddenCommands: [], onAction() {}, attachBlocked: null, onError() {}, queued: [], onRemoveQueued() {},
    onEditQueued: async () => ({ text: '', images: [] }), onSendQueuedNow() {}, restore: null, note: null,
  };
  let renderer: ReactTestRenderer | undefined;
  try {
    await act(async () => { renderer = create(createElement(Composer, props), { createNodeMock: () => ({ style: {}, focus() {}, contains: () => false, getBoundingClientRect: () => ({ height: 0 }) }) }); });
    await act(async () => { renderer!.root.findByProps({ className: 'send-btn' }).props.onClick({ preventDefault() {} }); });
    await act(async () => { renderer!.update(createElement(Composer, { ...props, draftKey: b })); });
    assert.match(JSON.stringify(renderer!.toJSON()), /A passage for B/, 'B has its own note before A returns');
    await act(async () => { resolve(); await sending; });
    assert.equal(renderer!.root.findByType('textarea').props.value, body);
    assert.match(JSON.stringify(renderer!.toJSON()), /A passage for B/, 'A must not clear B notes from the editor');
    assert.deepEqual(readDrafts(storage)[b].notes, [note], 'A must not overwrite B durable notes');
    assert.equal(readDrafts(storage)[a], undefined, 'the sent A draft is cleared');
    await act(async () => { renderer!.update(createElement(Composer, { ...props, draftKey: a })); });
    await act(async () => { renderer!.update(createElement(Composer, { ...props, draftKey: b })); });
    assert.match(JSON.stringify(renderer!.toJSON()), /A passage for B/, 'B notes survive leaving and returning');
  } finally {
    await act(async () => renderer?.unmount());
    globals.forEach((key, index) => { const descriptor = previous[index]; if (descriptor) Object.defineProperty(globalThis, key, descriptor); else Reflect.deleteProperty(globalThis, key); });
  }
});
