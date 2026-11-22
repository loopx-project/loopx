import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createElement, type ComponentProps } from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { Composer, prefillComposerDraft } from '../src/components/Composer.js';
import { readDrafts, writeDraft, SendCancelled } from '../src/drafts.js';

for (const prefix of ['', 'host:remote:']) for (const createdFirst of [false, true]) {
  test(`a first send never borrows B's draft (prefix=${prefix}, createdFirst=${createdFirst})`, async () => {
    const globals = ['window', 'document', 'requestAnimationFrame', 'cancelAnimationFrame'] as const;
    const previous = globals.map(key => Object.getOwnPropertyDescriptor(globalThis, key));
    const data = new Map<string, string>();
    const storage = { getItem: (key: string) => data.get(key) ?? null, setItem: (key: string, value: string) => { data.set(key, value); } };
    Object.assign(globalThis, {
      window: Object.assign(new EventTarget(), { localStorage: storage, matchMedia: () => Object.assign(new EventTarget(), { matches: false }), setTimeout, clearTimeout }),
      document: new EventTarget(), requestAnimationFrame: () => 0, cancelAnimationFrame() {}, IS_REACT_ACT_ENVIRONMENT: true,
    });
    const newKey = `${prefix}new`, b = `${prefix}saved-b-${createdFirst}`, created = `${prefix}created-${createdFirst}`;
    const note = { quote: 'B reference passage', comment: 'B question' };
    writeDraft(storage, newKey, { text: 'First request', notes: [] });
    writeDraft(storage, b, { text: 'Unsent B text', notes: [note] });
    let resolve!: () => void;
    let onCreated: ((key: string) => void) | undefined;
    const sending = new Promise<void>(done => { resolve = done; });
    const props: ComponentProps<typeof Composer> = {
      draftKey: newKey, running: false, placeholder: '',
      onSend: (_text, _images, _mentions, _videos, created) => { onCreated = created; return sending; },
      onStop() {}, header: null, left: null, right: null,
      commands: [], actions: [], hiddenCommands: [], onAction() {}, attachBlocked: null, onError() {}, queued: [], onRemoveQueued() {},
      onEditQueued: async () => ({ text: '', images: [] }), onSendQueuedNow() {}, restore: null, note: null,
    };
    let renderer: ReactTestRenderer | undefined;
    try {
      await act(async () => { renderer = create(createElement(Composer, props), { createNodeMock: () => ({ style: {}, focus() {}, contains: () => false, getBoundingClientRect: () => ({ height: 0 }) }) }); });
      await act(async () => { renderer!.root.findByProps({ className: 'send-btn' }).props.onClick({ preventDefault() {} }); });
      if (createdFirst) await act(async () => { onCreated?.(created); });
      await act(async () => { renderer!.update(createElement(Composer, { ...props, draftKey: b })); });
      assert.equal(renderer!.root.findByType('textarea').props.value, 'Unsent B text');
      if (!createdFirst) await act(async () => { onCreated?.(created); });
      assert.equal(readDrafts(storage)[created]?.text, 'First request');
      assert.deepEqual(readDrafts(storage)[created]?.notes, [], 'only the sending draft migrates to the created thread');
      assert.equal(readDrafts(storage)[b]?.sending, undefined, 'B never acquires this send identity');
      await act(async () => { resolve(); await sending; });
      assert.equal(readDrafts(storage)[b]?.text, 'Unsent B text', 'completion must not remove B storage');
      assert.deepEqual(readDrafts(storage)[b]?.notes, [note]);
      assert.equal(renderer!.root.findByType('textarea').props.value, 'Unsent B text');
      assert.match(JSON.stringify(renderer!.toJSON()), /B reference passage/);
      assert.equal(readDrafts(storage)[created], undefined, 'the sent draft is cleared only from its created thread');
      await act(async () => { renderer!.update(createElement(Composer, { ...props, draftKey: created })); });
      assert.equal(renderer!.root.findByType('textarea').props.value, '', 'created thread must not inherit B text');
      assert.doesNotMatch(JSON.stringify(renderer!.toJSON()), /B reference passage/);
      await act(async () => { renderer!.update(createElement(Composer, { ...props, draftKey: b })); });
      assert.equal(renderer!.root.findByType('textarea').props.value, 'Unsent B text');
      assert.match(JSON.stringify(renderer!.toJSON()), /B reference passage/);
    } finally {
      await act(async () => renderer?.unmount());
      globals.forEach((key, index) => { const descriptor = previous[index]; if (descriptor) Object.defineProperty(globalThis, key, descriptor); else Reflect.deleteProperty(globalThis, key); });
    }
  });
}

for (const cancelled of [false, true]) test(`settling a send cannot overwrite a newer unmarked draft (cancelled=${cancelled})`, async () => {
  const globals = ['window', 'document', 'requestAnimationFrame', 'cancelAnimationFrame'] as const;
  const previous = globals.map(key => Object.getOwnPropertyDescriptor(globalThis, key));
  const data = new Map<string, string>();
  const storage = { getItem: (key: string) => data.get(key) ?? null, setItem: (key: string, value: string) => { data.set(key, value); } };
  Object.assign(globalThis, {
    window: Object.assign(new EventTarget(), { localStorage: storage, matchMedia: () => Object.assign(new EventTarget(), { matches: false }), setTimeout, clearTimeout }),
    document: new EventTarget(), requestAnimationFrame: () => 0, cancelAnimationFrame() {}, IS_REACT_ACT_ENVIRONMENT: true,
  });
  const owner = `replaced-${cancelled}`;
  writeDraft(storage, owner, { text: 'Original send', notes: [] });
  let settle!: () => void;
  const sending = new Promise<void>((resolve, reject) => { settle = () => cancelled ? reject(new SendCancelled()) : resolve(); });
  const props: ComponentProps<typeof Composer> = {
    draftKey: owner, running: false, placeholder: '', onSend: () => sending, onStop() {}, header: null, left: null, right: null,
    commands: [], actions: [], hiddenCommands: [], onAction() {}, attachBlocked: null, onError() {}, queued: [], onRemoveQueued() {},
    onEditQueued: async () => ({ text: '', images: [] }), onSendQueuedNow() {}, restore: null, note: null,
  };
  let renderer: ReactTestRenderer | undefined;
  try {
    await act(async () => { renderer = create(createElement(Composer, props), { createNodeMock: () => ({ style: {}, focus() {}, contains: () => false, getBoundingClientRect: () => ({ height: 0 }) }) }); });
    await act(async () => { renderer!.root.findByProps({ className: 'send-btn' }).props.onClick({ preventDefault() {} }); });
    await act(async () => { renderer!.update(createElement(Composer, { ...props, draftKey: `other-${cancelled}` })); });
    prefillComposerDraft(owner, 'A newer unsent draft');
    await act(async () => { settle(); await sending.catch(() => {}); });
    assert.equal(readDrafts(storage)[owner]?.text, 'A newer unsent draft');
    assert.equal(readDrafts(storage)[owner]?.sending, undefined);
    await act(async () => { renderer!.update(createElement(Composer, props)); });
    assert.equal(renderer!.root.findByType('textarea').props.value, 'A newer unsent draft');
  } finally {
    await act(async () => renderer?.unmount());
    globals.forEach((key, index) => { const descriptor = previous[index]; if (descriptor) Object.defineProperty(globalThis, key, descriptor); else Reflect.deleteProperty(globalThis, key); });
  }
});

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

test('switching A -> B -> C while A is still sending preserves B unsent text and notes', async () => {
  const globals = ['window', 'document', 'requestAnimationFrame', 'cancelAnimationFrame'] as const;
  const previous = globals.map(key => Object.getOwnPropertyDescriptor(globalThis, key));
  const data = new Map<string, string>();
  const storage = { getItem: (key: string) => data.get(key) ?? null, setItem: (key: string, value: string) => { data.set(key, value); } };
  const windowMock = Object.assign(new EventTarget(), { localStorage: storage, matchMedia: () => Object.assign(new EventTarget(), { matches: false }), setTimeout, clearTimeout });
  Object.assign(globalThis, { window: windowMock, document: new EventTarget(), requestAnimationFrame: () => 0, cancelAnimationFrame() {}, IS_REACT_ACT_ENVIRONMENT: true });
  const a = 'multi-a', b = 'multi-b', c = 'multi-c';
  const note = { quote: 'Keep B quote', comment: 'Keep B note' };
  writeDraft(storage, a, { text: 'Send A', notes: [] });
  writeDraft(storage, b, { text: 'Unsent B draft', notes: [note] });
  writeDraft(storage, c, { text: 'Unsent C draft', notes: [] });
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
    assert.equal(renderer!.root.findByType('textarea').props.value, 'Unsent B draft');
    assert.match(JSON.stringify(renderer!.toJSON()), /Keep B quote/);
    // Switch away from B to C while A's send promise is still unresolved.
    await act(async () => { renderer!.update(createElement(Composer, { ...props, draftKey: c })); });
    assert.equal(renderer!.root.findByType('textarea').props.value, 'Unsent C draft');
    assert.equal(readDrafts(storage)[b]?.text, 'Unsent B draft', 'leaving B mid-send must not wipe B in storage');
    assert.deepEqual(readDrafts(storage)[b]?.notes, [note], 'leaving B mid-send must keep B notes in storage');
    // Finish A while viewing C, then return to B and A.
    await act(async () => { resolve(); await sending; });
    assert.equal(readDrafts(storage)[a], undefined, 'completed A draft is cleared');
    assert.equal(readDrafts(storage)[b]?.text, 'Unsent B draft');
    await act(async () => { renderer!.update(createElement(Composer, { ...props, draftKey: b })); });
    assert.equal(renderer!.root.findByType('textarea').props.value, 'Unsent B draft');
    assert.match(JSON.stringify(renderer!.toJSON()), /Keep B quote/, 'returning to B restores both text and notes');
    await act(async () => { renderer!.update(createElement(Composer, { ...props, draftKey: a })); });
    assert.equal(renderer!.root.findByType('textarea').props.value, '', 'returning to A shows cleared box');
  } finally {
    await act(async () => renderer?.unmount());
    globals.forEach((key, index) => { const descriptor = previous[index]; if (descriptor) Object.defineProperty(globalThis, key, descriptor); else Reflect.deleteProperty(globalThis, key); });
  }
});
