import { test } from 'node:test';
import assert from 'node:assert/strict';
import { feishuLine, feishuLoginPending, feishuReason, feishuResult, feishuSetupCopy } from '../src/feishu.js';
import type { FeishuStatus } from '../src/api.js';

const status = (patch: Partial<FeishuStatus> = {}): FeishuStatus => ({
  cli: true, configured: true, botReady: true, realtime: 'live', reason: null, pendingOwner: null,
  setup: { state: 'idle', kind: null, url: null, reason: null },
  records: [], dropped: 0, ...patch,
});

test('the line beside the switch: quiet while messages can arrive, a warning when a person can fix something', () => {
  assert.equal(feishuLine(null), null, 'nothing known yet, not a zero reading');
  assert.deepEqual(feishuLine(status({ cli: false })), { text: '找不到 lark-cli', warn: true });
  assert.deepEqual(feishuLine(status({ botReady: false })), { text: '机器人尚未就绪', warn: true });
  // The daemon's own reason says the same thing even when the flags have not caught up.
  assert.deepEqual(feishuLine(status({ cli: true, reason: 'cli_missing' })), { text: '找不到 lark-cli', warn: true });
  assert.deepEqual(feishuLine(status({ botReady: true, reason: 'bot_not_ready' })), { text: '机器人尚未就绪', warn: true });
  assert.deepEqual(feishuLine(status()), { text: '已连接', warn: false });
  assert.deepEqual(feishuLine(status({ realtime: 'connecting' })), { text: '连接中', warn: false });
  assert.deepEqual(feishuLine(status({ realtime: 'off' })), { text: '未连接', warn: false });
  assert.deepEqual(feishuLine(status({ realtime: 'console-off' })), { text: '需要在开放平台开启事件订阅', warn: true });
  assert.deepEqual(feishuLine(status({ realtime: 'busy-elsewhere' })), { text: '收消息连接被其他机器占用，稍后自动重试', warn: true });
  assert.deepEqual(feishuLine(status({ realtime: 'unavailable' })), { text: '收消息连接不可用，稍后自动重试', warn: true });
  assert.deepEqual(feishuLine(status({ realtime: 'error' })), { text: '收消息连接出错，自动重连中', warn: true });
});

test('a realtime value a newer daemon might send is neither off nor zero: the line just stays blank', () => {
  assert.equal(feishuLine(status({ realtime: 'paused' as FeishuStatus['realtime'] })), null);
});

test('reason codes become words; an unknown code passes through instead of hiding', () => {
  assert.equal(feishuReason(null), null);
  assert.equal(feishuReason(''), null);
  assert.equal(feishuReason('cli_missing'), '找不到 lark-cli');
  assert.equal(feishuReason('bot_not_ready'), '机器人尚未就绪');
  assert.equal(feishuReason('timeout'), '等待超时');
  assert.equal(feishuReason('owner_missing'), '尚未绑定主人');
  assert.equal(feishuReason('cli_failed'), 'lark-cli 出错');
  assert.equal(feishuReason('not_owner'), '非主人发送，已忽略');
  assert.equal(feishuReason('rate_limit'), '发送太频繁');
  assert.equal(feishuReason('console_off'), '开放平台未开启事件订阅');
  assert.equal(feishuReason('unsupported'), '不支持的消息类型');
  assert.equal(feishuReason('new_code'), 'new_code');
});

test('a record outcome reads as one short word', () => {
  assert.equal(feishuResult('received'), '已收到');
  assert.equal(feishuResult('sent'), '已发出');
  assert.equal(feishuResult('failed'), '失败');
  assert.equal(feishuResult('dropped'), '已丢弃');
});

test('pairing copy follows the kind: the sign-in never speaks of creating a bot', () => {
  const create = feishuSetupCopy('create');
  const login = feishuSetupCopy('login');
  assert.ok(create.hint.includes('创建'), 'the create pane introduces the bot');
  assert.ok(!login.hint.includes('创建'), 'the sign-in pane never says create');
  assert.ok(login.hint.includes('身份'));
  assert.equal(login.waiting, '在飞书里打开下面的链接并同意登录，完成后自动填入你的账号。');
  assert.equal(login.failed, '登录失败');
  assert.equal(login.failed !== create.failed, true);
  assert.equal(feishuSetupCopy(null).waiting, create.waiting, 'a missing kind reads as the original bot creation');
  assert.equal(feishuSetupCopy(undefined).failed, create.failed);
});

test('a sign-in panel is up only while the login is out or failed', () => {
  assert.equal(feishuLoginPending(null), false);
  assert.equal(feishuLoginPending(status()), false, 'idle setup is no panel');
  assert.equal(feishuLoginPending(status({ setup: { state: 'waiting', kind: 'login', url: 'u', reason: null } })), true);
  assert.equal(feishuLoginPending(status({ setup: { state: 'failed', kind: 'login', url: null, reason: 'timeout' } })), true);
  assert.equal(feishuLoginPending(status({ setup: { state: 'done', kind: 'login', url: null, reason: null } })), false);
  assert.equal(feishuLoginPending(status({ setup: { state: 'waiting', kind: 'create', url: 'u', reason: null } })), false, 'a bot creation belongs to the unconfigured page');
});
