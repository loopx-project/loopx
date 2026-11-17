import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createElement, type ComponentProps } from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { Composer } from '../src/components/Composer.js';
import { readDrafts, writeDraft } from '../src/drafts.js';

for (const streamed of [false, true]) test(`a new conversation keeps ownership of its first send (streamed=${streamed})`, async () => {
  const globals = ['window', 'document', 'requestAnimationFrame', 'cancelAnimationFrame'] as const;
  const previous = globals.map(key => Object.getOwnPropertyDescriptor(globalThis, key));
  const data = new Map<string, string>();
  const storage = { getItem: (key: string) => data.get(key) ?? null, setItem: (key: string, value: string) => { data.set(key, value); } };
  Object.assign(globalThis, {
    window: Object.assign(new EventTarget(), { localStorage: storage, matchMedia: () => Object.assign(new EventTarget(), { matches: false }), setTimeout, clearTimeout }),
    document: new EventTarget(), requestAnimationFrame: () => 0, cancelAnimationFrame() {}, IS_REACT_ACT_ENVIRONMENT: true,
  });
  const text = 'First message';
  const draftKey = `created-${streamed}`;
  writeDraft(storage, 'new', { text, notes: [] });
  let resolve!: () => void;
  const sending = new Promise<void>(done => { resolve = done; });
  const props: ComponentProps<typeof Composer> = {
    draftKey: 'new', running: false, placeholder: '', onSend: () => sending, onStop() {}, header: null, left: null, right: null,
    commands: [], actions: [], hiddenCommands: [], onAction() {}, attachBlocked: null, onError() {}, queued: [], onRemoveQueued() {},
    onEditQueued: async () => ({ text: '', images: [] }), onSendQueuedNow() {}, restore: null, note: null,
  };
  let renderer: ReactTestRenderer | undefined;
  try {
    await act(async () => { renderer = create(createElement(Composer, props), { createNodeMock: () => ({ style: {}, focus() {}, contains: () => false, getBoundingClientRect: () => ({ height: 0 }) }) }); });
    await act(async () => { renderer!.root.findByProps({ className: 'send-btn' }).props.onClick({ detail: 0, preventDefault() {} }); });
    await act(async () => { renderer!.update(createElement(Composer, { ...props, draftKey })); });
    assert.equal(renderer!.root.findByType('textarea').props.value, text, 'the pending upload retains its first-message draft');
    if (streamed) {
      await act(async () => { renderer!.update(createElement(Composer, { ...props, draftKey, lastSent: { turnId: 'first-turn', text } })); });
      assert.equal(renderer!.root.findByType('textarea').props.value, '', 'streamed acceptance reconciles the carried draft');
    }
    await act(async () => { resolve(); await sending; });
    assert.equal(renderer!.root.findByType('textarea').props.value, '', 'successful first send is not left as an unsent draft');
    assert.equal(readDrafts(storage).new, undefined);
    assert.equal(readDrafts(storage)[draftKey], undefined);
  } finally {
    await act(async () => renderer?.unmount());
    globals.forEach((key, index) => { const descriptor = previous[index]; if (descriptor) Object.defineProperty(globalThis, key, descriptor); else Reflect.deleteProperty(globalThis, key); });
  }
});
