import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { AgentLoginPanel } from '../src/components/AgentLoginPanel.js';
import type { Api, AgentLogin } from '../src/api.js';

test('device login displays the provider code without asking the user to paste it back', async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const initial: AgentLogin = { id: 'device', status: 'waiting', input: 'none', url: 'https://example.com/login', userCode: 'TEST-CODE' };
  let view!: ReactTestRenderer;
  try {
    await act(async () => { view = create(createElement(AgentLoginPanel, {
      api: { agentLogin: async () => initial } as unknown as Api, agent: { id: 'qwen', label: 'Qwen Code' }, initial,
      onState() {}, onReload() {}, onCollapse() {}, onDismiss() {},
    })); });
    assert.equal(view.root.findAllByType('input').length, 0);
    assert.equal(view.root.findByType('code').children.join(''), 'TEST-CODE');
    assert.equal(view.root.findByType('a').props.href, 'https://example.com/login');
  } finally { await act(async () => view?.unmount()); }
});
