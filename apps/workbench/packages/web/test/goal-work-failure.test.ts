import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createElement } from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { failureLabel } from '../src/loopx/status.js';
import { useLoopxStatus } from '../src/loopx/use-status.js';
import { t } from '../src/i18n/index.js';

const tick = () => new Promise<void>(resolve => setImmediate(resolve));

test('a failed Goal list stays an error until Retry reads a successful list', async () => {
  const originalFetch = globalThis.fetch;
  let statusReads = 0;
  let release!: (response: Response) => void;
  const first = new Promise<Response>(resolve => { release = resolve; });
  globalThis.fetch = async input => {
    if (String(input) === '/api/loopx/status.json') {
      statusReads++;
      return statusReads === 1 ? first : Response.json({ run_history: { goals: [] }, attention_queue: { items: [] } });
    }
    return Response.json({ error: 'detail unavailable' }, { status: 502 });
  };
  function Page() {
    const read = useLoopxStatus('test-token');
    if (read.loading) return createElement('div', { className: 'loading-note' }, t('common.loading'));
    if (read.failure) return createElement('div', {}, createElement('p', { role: 'alert' }, failureLabel(read.failure)), createElement('button', { onClick: read.reload }, t('common.retry')));
    return createElement('p', {}, t('loopx.goalGone'));
  }
  let renderer: ReactTestRenderer | undefined;
  try {
    await act(async () => { renderer = create(createElement(Page)); await tick(); });
    assert.equal(renderer!.root.findAllByProps({ className: 'loading-note' }).length, 1);
    await act(async () => { release(Response.json({ error: 'loopx_core_unavailable' }, { status: 502 })); await tick(); });
    assert.equal(renderer!.root.findAllByProps({ role: 'alert' }).length, 1, 'a failed list is not evidence the Goal was deleted');
    assert.doesNotMatch(JSON.stringify(renderer!.toJSON()), new RegExp(t('loopx.goalGone')));
    await act(async () => { renderer!.root.findByType('button').props.onClick(); await tick(); });
    assert.equal(statusReads, 2, 'Retry invokes the real status hook reload');
    assert.equal(renderer!.root.findAllByProps({ role: 'alert' }).length, 0);
    assert.match(JSON.stringify(renderer!.toJSON()), new RegExp(t('loopx.goalGone')), 'only a successful list confirms absence');
  } finally {
    await act(async () => renderer?.unmount());
    globalThis.fetch = originalFetch;
  }
});
