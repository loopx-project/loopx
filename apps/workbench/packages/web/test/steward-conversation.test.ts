import assert from 'node:assert/strict';
import { test } from 'node:test';
import { registerHooks } from 'node:module';
import * as React from 'react';
import { createElement } from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';

(globalThis as unknown as { React: typeof React }).React = React;

// Node has no asset loader; the component and its network/event handling stay real.
registerHooks({ load(url, context, next) {
  return url.endsWith('.svg') ? { format: 'module', source: 'export default "icon.svg"', shortCircuit: true } : next(url, context);
} });
const { StewardConversation } = await import('../src/components/StewardConversation.js');
const { switchManagerAgent } = await import('../src/loopx/manager.js');
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

test('when the steward default endpoint is missing, the failure view offers available agents and switches on click', async () => {
  const originalFetch = globalThis.fetch;
  const oldLocation = Object.getOwnPropertyDescriptor(globalThis, 'location');
  Object.defineProperty(globalThis, 'location', { configurable: true, value: { origin: 'http://localhost' } });
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  let renderer: ReactTestRenderer | undefined;
  const sessionBodies: Array<Record<string, unknown>> = [];
  const configCalls: Array<{ path: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = async (input, init) => {
    const path = new URL(String(input), 'http://localhost').pathname;
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : {};
    if (path.endsWith('/api/chat/sessions')) {
      sessionBodies.push(body);
      if (!body.agent_id) {
        return new Response(JSON.stringify({
          ok: false,
          error: "The Agent endpoint 'codex' cannot serve this request: executable_not_found.",
          error_code: 'agent_endpoint_unavailable',
        }), { status: 424 });
      }
      return json({ session_id: 'session-claude' });
    }
    if (path.endsWith('/api/chat/capabilities')) {
      return json({
        ok: true,
        manager: {
          runtime: { runtime_profile: 'trusted_owner' },
          channel_binding: { executor_endpoint: 'codex', selection_policy: 'preferred', eligible_endpoints: [] },
        },
        adapters: [
          { agent_id: 'codex', display_name: 'Codex', available: false, unavailable_reason: 'executable_not_found', executable: 'codex' },
          { agent_id: 'claude-code', display_name: 'Claude Code', available: true, executable: 'claude' },
        ],
      });
    }
    if (path.endsWith('/api/chat/machine-configuration/preview')) {
      configCalls.push({ path, body });
      return json({ ok: true, plan_revision: 'sha256:plan1' });
    }
    if (path.endsWith('/api/chat/machine-configuration/apply')) {
      configCalls.push({ path, body });
      return json({ ok: true });
    }
    if (path.endsWith('/sessions/session-claude')) return json({ messages: [] });
    throw new Error(`unexpected request: ${path}`);
  };
  try {
    await act(async () => {
      renderer = create(createElement(StewardConversation, { token: 'token-1', failure: null }), { createNodeMock: () => ({ scrollIntoView() {} }) });
      await tick();
      await tick();
    });
    const buttons = renderer!.root.findAllByType('button');
    assert.equal(buttons.length, 2, 'renders Retry and Use Claude Code');
    assert.match(JSON.stringify(renderer!.toJSON()), /Codex/);
    assert.match(JSON.stringify(renderer!.toJSON()), /Claude Code/);

    await act(async () => {
      buttons[1].props.onClick();
      await tick();
      await tick();
    });
    assert.equal(configCalls.length, 2, 'Restricts manager_runtime before opening a read-only steward adapter');
    assert.equal(configCalls[0].body.namespace, 'manager_runtime');
    assert.deepEqual(sessionBodies, [
      { context_kind: 'manager' },
      { context_kind: 'manager', agent_id: 'claude-code' },
    ]);
    assert.equal(renderer!.root.findByType('textarea').props.disabled, false, 'steward composer opens after switching agent');
  } finally {
    await act(async () => renderer?.unmount());
    globalThis.fetch = originalFetch;
    if (oldLocation) Object.defineProperty(globalThis, 'location', oldLocation); else Reflect.deleteProperty(globalThis, 'location');
  }
});

test('when an alternate steward session fails to open, the restricted manager_runtime change is rolled back', async () => {
  const originalFetch = globalThis.fetch;
  const oldLocation = Object.getOwnPropertyDescriptor(globalThis, 'location');
  Object.defineProperty(globalThis, 'location', { configurable: true, value: { origin: 'http://localhost' } });
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  let renderer: ReactTestRenderer | undefined;
  let runtimeProfile: 'trusted_owner' | 'restricted' = 'trusted_owner';
  const rollbacks: Array<Record<string, unknown>> = [];
  globalThis.fetch = async (input, init) => {
    const path = new URL(String(input), 'http://localhost').pathname;
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : {};
    if (path.endsWith('/api/chat/sessions')) {
      const agent = typeof body.agent_id === 'string' ? body.agent_id : 'codex';
      return new Response(JSON.stringify({
        ok: false,
        error: `The Agent endpoint '${agent}' cannot serve this request: executable_not_found.`,
        error_code: 'agent_endpoint_unavailable',
      }), { status: 424 });
    }
    if (path.endsWith('/api/chat/capabilities')) {
      return json({
        ok: true,
        manager: {
          runtime: { runtime_profile: runtimeProfile },
          channel_binding: { executor_endpoint: 'codex', selection_policy: 'preferred', eligible_endpoints: [] },
        },
        adapters: [
          { agent_id: 'codex', display_name: 'Codex', available: false, unavailable_reason: 'executable_not_found', executable: 'codex' },
          { agent_id: 'claude-code', display_name: 'Claude Code', available: true, executable: 'claude' },
        ],
      });
    }
    if (path.endsWith('/api/chat/machine-configuration/preview')) {
      return json({ ok: true, plan_revision: 'sha256:plan-restrict' });
    }
    if (path.endsWith('/api/chat/machine-configuration/apply')) {
      runtimeProfile = 'restricted';
      return json({ ok: true, transaction_id: 'mc-tx-1' });
    }
    if (path.endsWith('/api/chat/machine-configuration/rollback')) {
      rollbacks.push(body);
      if (body.execute === true) runtimeProfile = 'trusted_owner';
      return json({ ok: true, rollback_allowed: true, plan_revision: 'sha256:plan-rollback' });
    }
    throw new Error(`unexpected request: ${path}`);
  };
  try {
    await act(async () => {
      renderer = create(createElement(StewardConversation, { token: 'token-1', failure: null }), { createNodeMock: () => ({ scrollIntoView() {} }) });
      await tick();
      await tick();
    });
    const buttons = renderer!.root.findAllByType('button');
    await act(async () => {
      buttons[1].props.onClick();
      await tick();
      await tick();
      await tick();
    });
    assert.equal(runtimeProfile, 'trusted_owner', 'failed alternate session restores prior trusted_owner profile');
    assert.deepEqual(rollbacks, [
      { transaction_id: 'mc-tx-1', execute: false },
      { transaction_id: 'mc-tx-1', execute: true, expected_plan_revision: 'sha256:plan-rollback' },
    ]);
  } finally {
    await act(async () => renderer?.unmount());
    globalThis.fetch = originalFetch;
    if (oldLocation) Object.defineProperty(globalThis, 'location', oldLocation); else Reflect.deleteProperty(globalThis, 'location');
  }
});

test('when apply encounters a transport failure without a transaction receipt, switchManagerAgent propagates error without triggering compensating write', async () => {
  const originalFetch = globalThis.fetch;
  const oldLocation = Object.getOwnPropertyDescriptor(globalThis, 'location');
  Object.defineProperty(globalThis, 'location', { configurable: true, value: { origin: 'http://localhost' } });
  let runtimeProfile: 'trusted_owner' | 'restricted' = 'trusted_owner';
  let applyCalls = 0;
  globalThis.fetch = async (input) => {
    const path = new URL(String(input), 'http://localhost').pathname;
    if (path.endsWith('/api/chat/capabilities')) {
      return json({
        ok: true,
        manager: {
          runtime: { runtime_profile: runtimeProfile },
          channel_binding: { executor_endpoint: 'codex', selection_policy: 'preferred', eligible_endpoints: [] },
        },
        adapters: [
          { agent_id: 'codex', display_name: 'Codex', available: false, unavailable_reason: 'executable_not_found', executable: 'codex' },
          { agent_id: 'claude-code', display_name: 'Claude Code', available: true, executable: 'claude' },
        ],
      });
    }
    if (path.endsWith('/api/chat/machine-configuration/preview')) {
      return json({ ok: true, plan_revision: 'sha256:plan-rev' });
    }
    if (path.endsWith('/api/chat/machine-configuration/apply')) {
      applyCalls++;
      // Transport drops before apply reaches Core or response arrives;
      // independently user saved restricted in Settings:
      runtimeProfile = 'restricted';
      throw new TypeError('Failed to fetch');
    }
    throw new Error(`unexpected request: ${path}`);
  };
  try {
    let thrownError: unknown = null;
    try {
      await switchManagerAgent('token-1', 'claude-code', {
        selectedAgentId: 'codex',
        runtimeProfile: 'trusted_owner',
        selectionPolicy: 'preferred',
        eligibleEndpoints: [],
        adapters: [],
        availableAlternatives: [],
      });
    } catch (e) {
      thrownError = e;
    }
    assert.ok(thrownError !== null, 'propagates apply error');
    assert.equal((thrownError as Error).message, 'Failed to fetch');
    assert.equal(runtimeProfile, 'restricted', 'Settings confirmed restricted mode is preserved without overwrite');
    assert.equal(applyCalls, 1, 'no compensating write is triggered without a confirmed transaction receipt');
  } finally {
    globalThis.fetch = originalFetch;
    if (oldLocation) Object.defineProperty(globalThis, 'location', oldLocation); else Reflect.deleteProperty(globalThis, 'location');
  }
});

test('when prior profile was restricted and fallback session fails, concurrent trusted_owner setting is preserved', async () => {
  const originalFetch = globalThis.fetch;
  const oldLocation = Object.getOwnPropertyDescriptor(globalThis, 'location');
  Object.defineProperty(globalThis, 'location', { configurable: true, value: { origin: 'http://localhost' } });
  let runtimeProfile: 'trusted_owner' | 'restricted' = 'restricted';
  let applyCalls = 0;
  globalThis.fetch = async (input) => {
    const path = new URL(String(input), 'http://localhost').pathname;
    if (path.endsWith('/api/chat/sessions')) {
      // While session open is in flight, user concurrently saved trusted_owner in Settings:
      runtimeProfile = 'trusted_owner';
      return json({ ok: false, error: 'agent unavailable' });
    }
    if (path.endsWith('/api/chat/machine-configuration/preview') || path.endsWith('/api/chat/machine-configuration/apply')) {
      applyCalls++;
      return json({ ok: true });
    }
    throw new Error(`unexpected request: ${path}`);
  };
  try {
    let thrownError: unknown = null;
    try {
      await switchManagerAgent('token-1', 'claude-code', {
        selectedAgentId: 'codex',
        runtimeProfile: 'restricted',
        selectionPolicy: 'preferred',
        eligibleEndpoints: [],
        adapters: [],
        availableAlternatives: [],
      });
    } catch (e) {
      thrownError = e;
    }
    assert.ok(thrownError !== null, 'session open failure is propagated');
    assert.equal(runtimeProfile, 'trusted_owner', 'concurrent user setting trusted_owner is preserved');
    assert.equal(applyCalls, 0, 'no machine configuration modification attempted since no transaction was created by switch');
  } finally {
    globalThis.fetch = originalFetch;
    if (oldLocation) Object.defineProperty(globalThis, 'location', oldLocation); else Reflect.deleteProperty(globalThis, 'location');
  }
});

test('when fallback session fails but configuration was updated concurrently, rollback is blocked and preserves newer setting', async () => {
  const originalFetch = globalThis.fetch;
  const oldLocation = Object.getOwnPropertyDescriptor(globalThis, 'location');
  Object.defineProperty(globalThis, 'location', { configurable: true, value: { origin: 'http://localhost' } });
  let runtimeProfile: 'trusted_owner' | 'restricted' = 'trusted_owner';
  let rollbackExecuteCalls = 0;
  globalThis.fetch = async (input, init) => {
    const path = new URL(String(input), 'http://localhost').pathname;
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : {};
    if (path.endsWith('/api/chat/machine-configuration/preview')) {
      return json({ ok: true, plan_revision: 'sha256:plan-restrict' });
    }
    if (path.endsWith('/api/chat/machine-configuration/apply')) {
      runtimeProfile = 'restricted';
      return json({ ok: true, transaction_id: 'mc-tx-1' });
    }
    if (path.endsWith('/api/chat/sessions')) {
      // Concurrently user updated setting to trusted_owner in Settings:
      runtimeProfile = 'trusted_owner';
      return json({ ok: false, error: 'session open failed' });
    }
    if (path.endsWith('/api/chat/machine-configuration/rollback')) {
      if (body.execute === true) {
        rollbackExecuteCalls++;
        return json({ ok: true });
      }
      // Core detects current_revision != applied_revision and disallows rollback
      return json({ ok: true, rollback_allowed: false, plan_revision: 'sha256:plan-blocked' });
    }
    throw new Error(`unexpected request: ${path}`);
  };
  try {
    let thrownError: unknown = null;
    try {
      await switchManagerAgent('token-1', 'claude-code', {
        selectedAgentId: 'codex',
        runtimeProfile: 'trusted_owner',
        selectionPolicy: 'preferred',
        eligibleEndpoints: [],
        adapters: [],
        availableAlternatives: [],
      });
    } catch (e) {
      thrownError = e;
    }
    assert.ok(thrownError !== null, 'session open failure is propagated');
    assert.equal(runtimeProfile, 'trusted_owner', 'newer setting is preserved');
    assert.equal(rollbackExecuteCalls, 0, 'rollback execution was skipped because rollback_allowed was false');
  } finally {
    globalThis.fetch = originalFetch;
    if (oldLocation) Object.defineProperty(globalThis, 'location', oldLocation); else Reflect.deleteProperty(globalThis, 'location');
  }
});

test('when apply receives 409 conflict because Settings updated concurrently, switchManagerAgent preserves confirmed setting and does not write', async () => {
  const originalFetch = globalThis.fetch;
  const oldLocation = Object.getOwnPropertyDescriptor(globalThis, 'location');
  Object.defineProperty(globalThis, 'location', { configurable: true, value: { origin: 'http://localhost' } });
  let runtimeProfile: 'trusted_owner' | 'restricted' = 'trusted_owner';
  let applyCalls = 0;
  globalThis.fetch = async (input) => {
    const path = new URL(String(input), 'http://localhost').pathname;
    if (path.endsWith('/api/chat/capabilities')) {
      return json({
        ok: true,
        manager: {
          runtime: { runtime_profile: runtimeProfile },
          channel_binding: { executor_endpoint: 'codex', selection_policy: 'preferred', eligible_endpoints: [] },
        },
        adapters: [
          { agent_id: 'codex', display_name: 'Codex', available: false, unavailable_reason: 'executable_not_found', executable: 'codex' },
          { agent_id: 'claude-code', display_name: 'Claude Code', available: true, executable: 'claude' },
        ],
      });
    }
    if (path.endsWith('/api/chat/machine-configuration/preview')) {
      return json({ ok: true, plan_revision: 'sha256:plan-rev-1' });
    }
    if (path.endsWith('/api/chat/machine-configuration/apply')) {
      applyCalls++;
      // Concurrently user committed restricted in Settings before our apply was processed:
      runtimeProfile = 'restricted';
      // Core rejects stale preview with 409 Conflict:
      return new Response(JSON.stringify({
        ok: false,
        error_code: 'machine_configuration_preview_stale',
        message: 'machine-configuration plan revision changed; preview again',
      }), { status: 409, headers: { 'Content-Type': 'application/json' } });
    }
    throw new Error(`unexpected request: ${path}`);
  };
  try {
    let thrownError: unknown = null;
    try {
      await switchManagerAgent('token-1', 'claude-code', {
        selectedAgentId: 'codex',
        runtimeProfile: 'trusted_owner',
        selectionPolicy: 'preferred',
        eligibleEndpoints: [],
        adapters: [],
        availableAlternatives: [],
      });
    } catch (e) {
      thrownError = e;
    }
    assert.ok(thrownError !== null, 'propagates 409 error');
    assert.equal((thrownError as { status?: number }).status, 409);
    assert.equal(runtimeProfile, 'restricted', 'Settings confirmed restricted mode is preserved without overwrite');
    assert.equal(applyCalls, 1, 'no second compensating write was made after 409 conflict');
  } finally {
    globalThis.fetch = originalFetch;
    if (oldLocation) Object.defineProperty(globalThis, 'location', oldLocation); else Reflect.deleteProperty(globalThis, 'location');
  }
});

test('a Goal-reader entry opens no session until the first explicit message and rejects duplicate sends', async () => {
  const saved = globalThis.fetch;
  const oldLocation = Object.getOwnPropertyDescriptor(globalThis, 'location');
  Object.defineProperty(globalThis, 'location', { configurable: true, value: { origin: 'http://localhost' } });
  let renderer: ReactTestRenderer | undefined;
  let sessions = 0, sends = 0;
  let release!: (response: Response) => void;
  const opening = new Promise<Response>(resolve => { release = resolve; });
  globalThis.fetch = async (input, init) => {
    const path = new URL(String(input), 'http://localhost').pathname;
    if (path.endsWith('/sessions')) { sessions++; return opening; }
    if (path.endsWith('/sessions/reader')) return json({ messages: [] });
    if (path.endsWith('/turns')) { sends++; assert.equal(JSON.parse(String(init?.body)).message, 'Explicit question'); return json({ turn_id: 't-reader', events_url: '/api/chat/sessions/reader/turns/t-reader/events' }); }
    if (path.endsWith('/events')) return new Response('event: turn.completed\ndata: {}\n\n');
    throw new Error(`unexpected request: ${path}`);
  };
  try {
    await act(async () => { renderer = create(createElement(StewardConversation, { token: 'reader-token', failure: null, deferSessionOpen: true }), { createNodeMock: () => ({ scrollIntoView() {} }) }); await tick(); });
    assert.equal(sessions, 0);
    assert.equal(renderer!.root.findByType('textarea').props.disabled, false);
    await act(async () => renderer!.root.findByType('textarea').props.onChange({ target: { value: 'Explicit question' } }));
    await act(async () => { renderer!.root.findByType('form').props.onSubmit({ preventDefault() {} }); renderer!.root.findByType('form').props.onSubmit({ preventDefault() {} }); await tick(); });
    assert.equal(sessions, 1);
    assert.equal(sends, 0);
    assert.equal(renderer!.root.findByType('textarea').props.disabled, true);
    await act(async () => { release(json({ session_id: 'reader' })); await tick(); });
    assert.equal(sends, 1);
  } finally {
    await act(async () => renderer?.unmount()); globalThis.fetch = saved;
    if (oldLocation) Object.defineProperty(globalThis, 'location', oldLocation); else Reflect.deleteProperty(globalThis, 'location');
  }
});

test('a delayed session open from the prior token never sends into the new owner context', async () => {
  const saved = globalThis.fetch;
  const oldLocation = Object.getOwnPropertyDescriptor(globalThis, 'location');
  Object.defineProperty(globalThis, 'location', { configurable: true, value: { origin: 'http://localhost' } });
  let renderer: ReactTestRenderer | undefined;
  let release!: (response: Response) => void;
  let sessions = 0;
  const messages: string[] = [];
  globalThis.fetch = async (input, init) => {
    const path = new URL(String(input), 'http://localhost').pathname;
    if (path.endsWith('/sessions')) return ++sessions === 1 ? new Promise<Response>(resolve => { release = resolve; }) : json({ session_id: 'new-reader' });
    if (/\/sessions\/[^/]+$/.test(path)) return json({ messages: [] });
    if (path.endsWith('/turns')) { messages.push(JSON.parse(String(init?.body)).message); return json({ turn_id: 'new-turn', events_url: '/api/chat/sessions/new-reader/turns/new-turn/events' }); }
    if (path.endsWith('/events')) return new Response('event: turn.completed\ndata: {}\n\n');
    throw new Error(`unexpected request: ${path}`);
  };
  try {
    await act(async () => { renderer = create(createElement(StewardConversation, { token: 'old-token', failure: null, deferSessionOpen: true }), { createNodeMock: () => ({ scrollIntoView() {} }) }); });
    await act(async () => renderer!.root.findByType('textarea').props.onChange({ target: { value: 'Old question' } }));
    await act(async () => { renderer!.root.findByType('form').props.onSubmit({ preventDefault() {} }); await tick(); });
    await act(async () => { renderer!.update(createElement(StewardConversation, { token: 'new-token', failure: null, deferSessionOpen: true })); await tick(); });
    assert.equal(renderer!.root.findByType('textarea').props.value, '');
    await act(async () => renderer!.root.findByType('textarea').props.onChange({ target: { value: 'New question' } }));
    await act(async () => { renderer!.root.findByType('form').props.onSubmit({ preventDefault() {} }); await tick(); });
    await act(async () => { release(json({ session_id: 'old-reader' })); await tick(); });
    assert.deepEqual(messages, ['New question']);
  } finally {
    await act(async () => renderer?.unmount()); globalThis.fetch = saved;
    if (oldLocation) Object.defineProperty(globalThis, 'location', oldLocation); else Reflect.deleteProperty(globalThis, 'location');
  }
});

test('pending waiting Goals float in a quiet orb chip above the steward composer and open a bounded tray on demand', async () => {
  let renderer: ReactTestRenderer | undefined;
  const openedGoals: string[] = [];
  const openedThreads: string[] = [];
  let overviewClicks = 0;
  const waitingGoals = Array.from({ length: 6 }, (_, i) => ({
    goalId: `g-${i + 1}`,
    title: `Waiting Goal ${i + 1}`,
    summary: `Action ${i + 1}`,
    threadId: i === 0 ? 'thread-bound-1' : null,
  }));
  try {
    await act(async () => {
      renderer = create(createElement(StewardConversation, {
        token: 'orb-token',
        failure: null,
        deferSessionOpen: true,
        waitingGoals,
        onOpenGoal: (id: string) => { openedGoals.push(id); },
        onOpenThread: (id: string) => { openedThreads.push(id); },
        onOpenGoalsOverview: () => { overviewClicks++; },
      }), { createNodeMock: () => ({ scrollIntoView() {} }) });
    });
    const orb = renderer!.root.findByProps({ className: 'steward-ask-orb' });
    assert.equal(orb.props['aria-expanded'], false);
    assert.equal(renderer!.root.findAllByProps({ className: 'steward-tray' }).length, 0, 'tray stays closed until tapped');

    await act(async () => orb.props.onClick());
    assert.equal(renderer!.root.findByProps({ className: 'steward-ask-orb' }).props['aria-expanded'], true);
    const rows = renderer!.root.findAllByProps({ className: 'steward-tray-row' });
    assert.equal(rows.length, 5, 'tray caps visible items at 5 with an overview overflow button');

    await act(async () => rows[0]!.props.onClick());
    assert.deepEqual(openedThreads, ['thread-bound-1']);
    assert.equal(renderer!.root.findAllByProps({ className: 'steward-tray' }).length, 0, 'clicking a row closes the tray');

    await act(async () => renderer!.root.findByProps({ className: 'steward-ask-orb' }).props.onClick());
    await act(async () => renderer!.root.findAllByProps({ className: 'steward-tray-row' })[1]!.props.onClick());
    assert.deepEqual(openedGoals, ['g-2']);

    await act(async () => renderer!.root.findByProps({ className: 'steward-ask-orb' }).props.onClick());
    await act(async () => renderer!.root.findByProps({ className: 'steward-tray-more' }).props.onClick());
    assert.equal(overviewClicks, 1);
  } finally {
    await act(async () => renderer?.unmount());
  }
});
