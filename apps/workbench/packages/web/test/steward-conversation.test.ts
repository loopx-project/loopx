import assert from 'node:assert/strict';
import { test } from 'node:test';
import { registerHooks } from 'node:module';
import { createElement } from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';

// Node has no asset loader; the component and its network/event handling stay real.
registerHooks({ load(url, context, next) {
  return url.endsWith('.svg') ? { format: 'module', source: 'export default "icon.svg"', shortCircuit: true } : next(url, context);
} });
const { StewardConversation } = await import('../src/components/StewardConversation.js');
const tick = () => new Promise<void>(resolve => setImmediate(resolve));
const json = (value: unknown) => new Response(JSON.stringify(value));

for (const concurrent of [false, true]) for (const failed of [false, true]) {
  test(`an older history ${failed ? 'failure' : 'response'} cannot replace the next turn (concurrent=${concurrent})`, async () => {
    const originalFetch = globalThis.fetch;
    const oldLocation = Object.getOwnPropertyDescriptor(globalThis, 'location');
    Object.defineProperty(globalThis, 'location', { configurable: true, value: { origin: 'http://localhost' } });
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    let renderer: ReactTestRenderer | undefined;
    let reads = 0, sends = 0;
    let finishHistory!: (value: Response) => void;
    const delayedHistory = new Promise<Response>(resolve => { finishHistory = resolve; });
    const streams = new Map<number, ReadableStreamDefaultController<Uint8Array>>();
    const event = (turn: number, kind: string, payload = {}) => streams.get(turn)!.enqueue(new TextEncoder().encode(`event: ${kind}\ndata: ${JSON.stringify(payload)}\n\n`));
    globalThis.fetch = async input => {
      const path = new URL(String(input), 'http://localhost').pathname;
      if (path.endsWith('/sessions')) return json({ session_id: 'session' });
      if (path.endsWith('/turns')) { sends++; return json({ turn_id: `t${sends}`, events_url: `/api/chat/sessions/session/turns/t${sends}/events` }); }
      if (path.endsWith('/events')) {
        const turn = Number(/\/t(\d+)\//.exec(path)![1]);
        return new Response(new ReadableStream({ start(controller) { streams.set(turn, controller); } }));
      }
      if (path.endsWith('/sessions/session')) {
        reads++;
        if (reads === 2) return delayedHistory;
        return json({ messages: reads === 1 ? [] : [{ role: 'agent', text: 'Second stored answer', turn_id: 't2' }] });
      }
      throw new Error(`unexpected request: ${path}`);
    };
    try {
      await act(async () => { renderer = create(createElement(StewardConversation, { token: 'test-token', failure: null }), { unstable_isConcurrent: concurrent, createNodeMock: () => ({ scrollIntoView() {} }) } as Parameters<typeof create>[1]); await tick(); });
      const send = async (text: string) => {
        await act(async () => { renderer!.root.findByType('textarea').props.onChange({ target: { value: text } }); });
        await act(async () => { renderer!.root.findByType('form').props.onSubmit({ preventDefault() {} }); await tick(); });
      };
      await send('First question');
      await act(async () => { event(1, 'answer.delta', { text: 'First answer' }); event(1, 'turn.completed'); await tick(); });
      assert.equal(reads, 2, 'the first history read remains outstanding');
      await send('Second question');
      await act(async () => { event(2, 'answer.delta', { text: 'Second live answer' }); await tick(); });
      await act(async () => { finishHistory(failed ? new Response('unavailable', { status: 502 }) : json({ messages: [{ role: 'agent', text: 'First stored answer', turn_id: 't1' }] })); await tick(); });
      assert.match(JSON.stringify(renderer!.toJSON()), /Second live answer/);
      assert.equal(renderer!.root.findAllByProps({ className: 'send-btn stop' }).length, 1, 'the second turn remains stoppable');
      await act(async () => { event(2, 'answer.delta', { text: ' continues' }); await tick(); });
      assert.match(JSON.stringify(renderer!.toJSON()), /Second live answer continues/);
      await act(async () => { event(2, 'turn.completed'); await tick(); });
      assert.equal(reads, 3, 'the second turn still reads its final history');
      assert.match(JSON.stringify(renderer!.toJSON()), /Second stored answer/);
    } finally {
      await act(async () => renderer?.unmount());
      globalThis.fetch = originalFetch;
      if (oldLocation) Object.defineProperty(globalThis, 'location', oldLocation); else Reflect.deleteProperty(globalThis, 'location');
    }
  });
}

test('a pending turn submission holds synchronous ownership and late acceptance cannot overwrite a newer turn', async () => {
  const originalFetch = globalThis.fetch;
  const oldLocation = Object.getOwnPropertyDescriptor(globalThis, 'location');
  Object.defineProperty(globalThis, 'location', { configurable: true, value: { origin: 'http://localhost' } });
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  let renderer: ReactTestRenderer | undefined;
  let sends = 0;
  let sessions = 0;
  let resolveFirstTurnPost!: (value: Response) => void;
  const firstTurnPost = new Promise<Response>(resolve => { resolveFirstTurnPost = resolve; });
  const streams = new Map<string, ReadableStreamDefaultController<Uint8Array>>();
  const event = (turnId: string, kind: string, payload = {}) =>
    streams.get(turnId)!.enqueue(new TextEncoder().encode(`event: ${kind}\ndata: ${JSON.stringify(payload)}\n\n`));
  globalThis.fetch = async input => {
    const path = new URL(String(input), 'http://localhost').pathname;
    if (path.endsWith('/sessions')) {
      sessions++;
      return json({ session_id: `session-${sessions}` });
    }
    if (path.endsWith('/turns')) {
      sends++;
      if (sends === 1) return firstTurnPost;
      return json({ turn_id: `t${sends}`, events_url: `/api/chat/sessions/session-${sessions}/turns/t${sends}/events` });
    }
    if (path.endsWith('/events')) {
      const turnId = /\/turns\/([^/]+)\/events$/.exec(path)![1];
      return new Response(new ReadableStream({ start(controller) { streams.set(turnId, controller); } }));
    }
    if (/\/sessions\/session-\d+$/.test(path)) return json({ messages: [] });
    throw new Error(`unexpected request: ${path}`);
  };
  try {
    await act(async () => {
      renderer = create(createElement(StewardConversation, { token: 'token-1', failure: null }), { createNodeMock: () => ({ scrollIntoView() {} }) });
      await tick();
    });
    // Submit T1 while POST /turns stays unresolved: input must lock immediately and reject duplicate submit.
    await act(async () => { renderer!.root.findByType('textarea').props.onChange({ target: { value: 'First question' } }); });
    await act(async () => { renderer!.root.findByType('form').props.onSubmit({ preventDefault() {} }); await tick(); });
    assert.equal(renderer!.root.findByType('textarea').props.disabled, true, 'input stays disabled while POST acceptance is pending');
    await act(async () => { renderer!.root.findByType('textarea').props.onChange({ target: { value: 'Duplicate while pending' } }); });
    await act(async () => { renderer!.root.findByType('form').props.onSubmit({ preventDefault() {} }); await tick(); });
    assert.equal(sends, 1, 'synchronous sending owner prevents concurrent second submit');

    // Reopen session under a new token and start T2, then release delayed T1 202 response.
    await act(async () => {
      renderer!.update(createElement(StewardConversation, { token: 'token-2', failure: null }));
      await tick();
    });
    assert.equal(renderer!.root.findByType('textarea').props.disabled, false);
    await act(async () => { renderer!.root.findByType('textarea').props.onChange({ target: { value: 'Second question' } }); });
    await act(async () => { renderer!.root.findByType('form').props.onSubmit({ preventDefault() {} }); await tick(); });
    await act(async () => { event('t2', 'answer.delta', { text: 'Active T2 answer' }); await tick(); });
    assert.equal(renderer!.root.findAllByProps({ className: 'send-btn stop' }).length, 1);

    await act(async () => {
      resolveFirstTurnPost(json({ turn_id: 't1', events_url: '/api/chat/sessions/session-1/turns/t1/events' }));
      await tick();
    });
    assert.match(JSON.stringify(renderer!.toJSON()), /Active T2 answer/, 'late T1 202 response must not replace T2 answer');
    assert.equal(renderer!.root.findAllByProps({ className: 'send-btn stop' }).length, 1, 'T2 stop button remains intact');
    assert.equal(streams.has('t1'), false, 'late T1 response never opens an obsolete stream');
  } finally {
    await act(async () => renderer?.unmount());
    globalThis.fetch = originalFetch;
    if (oldLocation) Object.defineProperty(globalThis, 'location', oldLocation); else Reflect.deleteProperty(globalThis, 'location');
  }
});
