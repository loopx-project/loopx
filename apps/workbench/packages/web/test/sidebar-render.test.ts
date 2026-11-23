import assert from 'node:assert/strict';
import { test } from 'node:test';
import { registerHooks } from 'node:module';
import * as React from 'react';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import type { Thread } from '../src/api.js';
import { t } from '../src/i18n/index.js';

(globalThis as unknown as { React: typeof React }).React = React;
registerHooks({ load(url, context, next) {
  return url.endsWith('.svg') ? { format: 'module', source: 'export default "icon.svg"', shortCircuit: true } : next(url, context);
} });
const { Sidebar } = await import('../src/components/Sidebar.js');

test('the sidebar keeps conversation context, bell and search, with no Goal list or legacy LoopX filter', () => {
  const storage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { getItem(key: string) { return key.endsWith('.loopx') ? '1' : null; } } });
  try {
    const thread = (id: string, bound = false) => ({ id, title: id, agent: 'codex', cwd: '/workspace/project', cwdReal: '/workspace/project', status: 'idle', queued: [], createdAt: '2026-01-01', updatedAt: '2026-01-01', ...(bound ? { loopxInfo: { goalId: 'goal', goalTitle: 'Bound goal', missing: false } } : {}) } as Partial<Thread>) as Thread;
    const html = renderToStaticMarkup(createElement(Sidebar, {
      threads: [thread('Plain conversation'), thread('Goal conversation', true)], agents: [], sections: [], selectedId: null,
      needsYouGoals: new Set(['goal']), onSelect() {}, onNew() {}, onNewIn() {}, onThreadMenu() {}, onSectionMenu() {}, onNewSection() {}, onOpenSettings() {},
    }));
    assert.match(html, /Plain conversation/, 'a saved old filter does not hide ordinary conversations');
    assert.match(html, /Goal conversation/);
    assert.match(html, /thread-sub-goal/);
    assert.match(html, /Bound goal/);
    assert.match(html, /activity-btn/);
    assert.match(html, new RegExp(t('sidebar.search')));
    assert.doesNotMatch(html, /sidebar-goals|sidebar-goal-tasks|loopx-btn|loopx-count/);
    assert.doesNotMatch(html, new RegExp(t('goals.allGoals')));
  } finally { if (storage) Object.defineProperty(globalThis, 'localStorage', storage); else Reflect.deleteProperty(globalThis, 'localStorage'); }
});
