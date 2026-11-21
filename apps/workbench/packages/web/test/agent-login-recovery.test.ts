import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { AgentLoginRecovery, needsAgentLogin } from '../src/components/AgentLoginRecovery.js';
import { ApiError, type Api, type AgentLogin, type ModelOption } from '../src/api.js';

test('only typed agent authentication errors offer login recovery', () => {
  assert.equal(needsAgentLogin(new ApiError('signed out', { code: 'agent_login_required' }, 409)), true);
  assert.equal(needsAgentLogin(new Error('Please sign in to view available models.')), false);
  assert.equal(needsAgentLogin(new ApiError('connection failed', {}, 500)), false);
});

test('successful authorization retries a failed catalog read without starting OAuth again', async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  let state: AgentLogin = { id: 'login-1', status: 'waiting', input: 'code-or-url', url: 'https://accounts.google.com/o/oauth2/auth' };
  let starts = 0, reads = 0;
  let recovered: ModelOption[] | undefined;
  const models = [{ id: 'restored', label: 'Restored model' }];
  const api = {
    startAgentLogin: async (id: string) => { assert.equal(id, 'secondary'); starts++; return state; },
    agentLogin: async () => state,
    submitAgentLogin: async (_agent: string, _id: string, code: string) => {
      assert.equal(code, 'test-code'); state = { id: 'login-1', status: 'success' };
      return { id: 'login-1', status: 'submitting' } as AgentLogin;
    },
    models: async (id: string, refresh: boolean) => {
      assert.equal(id, 'secondary'); assert.equal(refresh, true); reads++;
      if (reads === 1) throw new Error('temporary catalog failure');
      return models;
    },
  } as unknown as Api;
  let view!: ReactTestRenderer;
  const button = (label: string) => view.root.findAllByType('button').find(node => node.children.includes(label))!;
  try {
    await act(async () => { view = create(createElement(AgentLoginRecovery, { api, agent: { id: 'secondary', label: 'Antigravity' }, onRecovered: list => { recovered = list; } })); });
    assert.equal(starts, 0);
    await act(async () => { await button('去登录').props.onClick(); });
    await act(async () => { view.root.findByType('input').props.onChange({ target: { value: 'test-code' } }); });
    await act(async () => { await button('提交验证码').props.onClick(); });
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 1100)); });
    assert.match(JSON.stringify(view.toJSON()), /temporary catalog failure/);
    assert.equal(recovered, undefined);
    await act(async () => { await button('重试').props.onClick(); });
    assert.deepEqual(recovered, models);
    assert.equal(starts, 1);
    assert.equal(reads, 2);
  } finally { await act(async () => view?.unmount()); }
});
