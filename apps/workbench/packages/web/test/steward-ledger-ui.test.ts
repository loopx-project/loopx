import assert from 'node:assert/strict';
import { test } from 'node:test';
import { registerHooks } from 'node:module';
import * as React from 'react';
import { createElement } from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';

(globalThis as unknown as { React: typeof React }).React = React;

registerHooks({
  load(url, context, next) {
    return url.endsWith('.svg') ? { format: 'module', source: 'export default "icon.svg"', shortCircuit: true } : next(url, context);
  },
});

const { StewardConversation } = await import('../src/components/StewardConversation.js');
const { StewardOverview } = await import('../src/components/StewardOverview.js');
const { GeneralSettingsDialog } = await import('../src/components/GeneralSettingsDialog.js');
import type { Api, GeneralSettings, StewardLedgerItem, StewardLedgerSnapshot, StewardSessionFindHit } from '../src/api.js';

const tick = () => new Promise<void>(resolve => setImmediate(resolve));
const json = (value: unknown) => new Response(JSON.stringify(value));

test('@ session mention searches local sessions and opens peek without sending a turn or importing', async () => {
  const originalFetch = globalThis.fetch;
  const oldLocation = Object.getOwnPropertyDescriptor(globalThis, 'location');
  Object.defineProperty(globalThis, 'location', { configurable: true, value: { origin: 'http://localhost' } });
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

  let renderer: ReactTestRenderer | undefined;
  let turnSends = 0;
  let importCalls = 0;
  const findQueries: string[] = [];
  const peeked: Array<{ target: StewardLedgerItem['jumpTarget']; title?: string }> = [];

  globalThis.fetch = async (input) => {
    const path = new URL(String(input), 'http://localhost').pathname;
    if (path.endsWith('/sessions')) return json({ session_id: 'steward-session' });
    if (path.endsWith('/sessions/steward-session')) return json({ messages: [] });
    if (path.endsWith('/turns')) {
      turnSends++;
      return json({ turn_id: 't1', events_url: '/api/chat/sessions/steward-session/turns/t1/events' });
    }
    throw new Error(`unexpected fetch: ${path}`);
  };

  const sampleHits: StewardSessionFindHit[] = [
    {
      id: 'thread:t-pay',
      kind: 'workbench_thread',
      title: '支付网关重构',
      agent: 'codex',
      cwd: '/workspace/pay-gateway',
      updatedAt: '2026-11-24T01:00:00Z',
      phase: 'needs_user',
      goalId: null,
      snippet: 'npm run build',
      jumpTarget: { kind: 'thread', threadId: 't-pay' },
    },
    {
      id: 'local:codex:ext-cat',
      kind: 'local_session',
      title: '排查黑猫内存泄漏',
      agent: 'codex',
      cwd: '/workspace/cat-service',
      updatedAt: '2026-11-24T01:05:00Z',
      phase: 'running',
      goalId: null,
      snippet: '黑猫 OOM',
      jumpTarget: { kind: 'local_session', agent: 'codex', sessionId: 'ext-cat', cwd: '/workspace/cat-service' },
    },
  ];

  const fakeApi = {
    async stewardFindSessions(query: string) {
      findQueries.push(query);
      return {
        query,
        revision: 'rev-1',
        indexStatus: 'ready' as const,
        sourceHealth: {
          workbenchThreads: { status: 'ok' as const, itemCount: 1 },
          localSessions: { status: 'ok' as const, itemCount: 1 },
        },
        hits: sampleHits.filter(h => !query || h.title.includes(query) || h.snippet.includes(query)),
      };
    },
    async importSession() {
      importCalls++;
      throw new Error('must not auto-import on @ selection');
    },
  } as unknown as Api;

  try {
    await act(async () => {
      renderer = create(
        createElement(StewardConversation, {
          token: 'tok',
          failure: null,
          api: fakeApi,
          ledgerCapture: true,
          onPeekSession: (target, title) => peeked.push({ target, title }),
        }),
        { createNodeMock: () => ({ scrollIntoView() {} }) },
      );
      await tick();
    });

    // Type "看看 @猫" into composer textarea
    const textarea = renderer!.root.findByType('textarea');
    await act(async () => {
      textarea.props.onChange({ target: { value: '看看 @猫', selectionStart: 5, selectionEnd: 5 } });
      await tick();
      await tick();
    });

    assert.deepEqual(findQueries, ['猫']);
    const options = renderer!.root.findAllByProps({ role: 'option' });
    assert.equal(options.length, 1);
    assert.match(JSON.stringify(renderer!.toJSON()), /排查黑猫内存泄漏/);

    // Press Enter while @ popover is open: selects session for peek, strips "@猫", sends 0 turns!
    await act(async () => {
      textarea.props.onKeyDown({
        key: 'Enter',
        shiftKey: false,
        nativeEvent: { isComposing: false },
        preventDefault() {},
      });
      await tick();
    });

    assert.equal(turnSends, 0, 'selecting an @ session must not send a steward turn');
    assert.equal(importCalls, 0, 'selecting an @ session must not auto-import external sessions');
    assert.equal(renderer!.root.findByType('textarea').props.value, '看看 ');
    assert.deepEqual(peeked, [
      {
        target: { kind: 'local_session', agent: 'codex', sessionId: 'ext-cat', cwd: '/workspace/cat-service' },
        title: '排查黑猫内存泄漏',
      },
    ]);

    // When ledgerCapture=false, @ button and popover are completely hidden.
    await act(async () => {
      renderer!.update(
        createElement(StewardConversation, {
          token: 'tok',
          failure: null,
          api: fakeApi,
          ledgerCapture: false,
          onPeekSession: (target, title) => peeked.push({ target, title }),
        }),
      );
      await tick();
    });
    assert.equal(renderer!.root.findAllByProps({ className: 'steward-mention-btn' }).length, 0);
  } finally {
    await act(async () => renderer?.unmount());
    globalThis.fetch = originalFetch;
    if (oldLocation) Object.defineProperty(globalThis, 'location', oldLocation);
    else Reflect.deleteProperty(globalThis, 'location');
  }
});

test('StewardOverview renders non-Goal local sessions, triggers peek on click, and surfaces LOOCHAT_LOCAL_SCAN=off gap', async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  let renderer: ReactTestRenderer | undefined;
  const peeked: Array<StewardLedgerItem['jumpTarget']> = [];

  const ledger: StewardLedgerSnapshot = {
    revision: 'r1',
    generatedAt: '2026-11-24T01:10:00Z',
    ledgerCapture: true,
    sourceHealth: {
      workbenchThreads: { status: 'ok', itemCount: 1 },
      localSessions: { status: 'unavailable', reason: 'local_scan_disabled' },
    },
    coverage: { workbenchThreads: 'complete', localSessions: 'unavailable' },
    counts: { running: 0, needs_user: 1, blocked: 0, idle_open: 0, done_recent: 0 },
    items: [
      {
        id: 'thread:th-1',
        kind: 'workbench_thread',
        phase: 'needs_user',
        title: '等待确认发布脚本',
        agent: 'codex',
        cwd: '/workspace/release',
        updatedAt: '2026-11-24T01:09:00Z',
        goalId: null,
        goalTitle: null,
        summary: '确认执行数据库迁移',
        chainIds: ['th-1'],
        jumpTarget: { kind: 'thread', threadId: 'th-1' },
      },
    ],
    nextCursor: null,
  };

  try {
    await act(async () => {
      renderer = create(
        createElement(StewardOverview, {
          token: 'tok',
          goals: [],
          attention: [],
          failure: null,
          loading: false,
          onChanged() {},
          onRetry() {},
          threads: [],
          onOpenThread() {},
          defaultAgent: 'codex',
          ledger,
          onPeekSession: (target) => peeked.push(target),
        }),
      );
      await tick();
    });

    const text = JSON.stringify(renderer!.toJSON());
    assert.match(text, /等待确认发布脚本/);
    assert.match(text, /LOOCHAT_LOCAL_SCAN=off/);

    const row = renderer!.root.findByProps({ className: 'steward-local-row' });
    await act(async () => {
      row.props.onClick();
      await tick();
    });
    assert.deepEqual(peeked, [{ kind: 'thread', threadId: 'th-1' }]);
  } finally {
    await act(async () => renderer?.unmount());
  }
});

test('GeneralSettingsDialog loads and saves ledgerCapture setting', async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  let renderer: ReactTestRenderer | undefined;
  let stored: GeneralSettings = {
    startupPage: 'new-thread',
    autoArchiveDays: null,
    autoImportLocal: true,
    ledgerCapture: true,
  };
  const savedSnapshots: GeneralSettings[] = [];
  const fakeApi = {
    async generalSettings() {
      return stored;
    },
    async saveGeneralSettings(next: GeneralSettings) {
      stored = next;
      savedSnapshots.push(next);
      return stored;
    },
  } as unknown as Api;

  try {
    await act(async () => {
      renderer = create(
        createElement(GeneralSettingsDialog, {
          api: fakeApi,
          onSave() {},
          onClose() {},
        }),
      );
      await tick();
    });
    assert.match(JSON.stringify(renderer!.toJSON()), /管家账本收录本机会话/);
  } finally {
    await act(async () => renderer?.unmount());
  }
});
