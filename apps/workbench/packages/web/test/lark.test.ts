import { test } from 'node:test';
import assert from 'node:assert/strict';
import { larkHealth, larkSourceState, meetingEventCoverage, POST_MEETING_EVENTS, realtimeLine, signalDestination, withPostMeetingEvents } from '../src/lark.js';

const channel = { enabled: true, people: [], chats: [], intervalMin: 30 };
const status = { cli: true, loggedIn: true, lastRunAt: null, nextRunAt: null, lastError: null, lastSignals: 0 };

test('the channel reads as off, missing, logged out, failing or fine', () => {
  assert.equal(larkHealth(null, null), 'off');
  assert.equal(larkHealth({ ...channel, enabled: false }, status), 'off');
  assert.equal(larkHealth(channel, null), 'off');
  assert.equal(larkHealth(channel, { ...status, cli: false }), 'no-cli');
  assert.equal(larkHealth(channel, { ...status, loggedIn: false }), 'no-login');
  assert.equal(larkHealth(channel, { ...status, lastError: 'cli_timeout' }), 'failed');
  assert.equal(larkHealth(channel, { ...status, lastError: 'cli_missing' }), 'no-cli');
  assert.equal(larkHealth(channel, { ...status, lastError: 'lark_auth_failed' }), 'no-login');
  assert.equal(larkHealth(channel, { ...status, lastError: 'personal agent is not configured' }), 'no-agent');
  assert.equal(larkHealth(channel, status), 'ok');
});

test('the realtime line is quiet when live, warns when events are not coming in, and is absent while it settles', () => {
  assert.deepEqual(realtimeLine('live'), { text: '实时', warn: false });
  assert.deepEqual(realtimeLine('busy-elsewhere'), { text: '事件通道被其他机器占用，10 分钟自动重试', warn: true });
  assert.deepEqual(realtimeLine('console-off'), { text: '飞书后台未开启所需事件', warn: true });
  assert.deepEqual(realtimeLine('unavailable'), { text: 'lark-cli 未登录或缺失', warn: true });
  assert.deepEqual(realtimeLine('error'), { text: '实时通道出错，自动重连中', warn: true });
  assert.equal(realtimeLine('connecting'), null);
  assert.equal(realtimeLine('off'), null);
  assert.equal(realtimeLine(undefined), null, 'an older hub says nothing about it');
});

test('source loading, unknown and failure remain distinct without changing health null semantics', () => {
  assert.equal(larkSourceState(null, null, true, ''), 'loading');
  assert.equal(larkSourceState(null, null, false, ''), 'unknown');
  assert.equal(larkSourceState(channel, null, false, ''), 'unknown');
  assert.equal(larkSourceState(channel, status, false, 'network failed'), 'load-failed');
  assert.equal(larkSourceState({ ...channel, enabled: false }, status, false, ''), 'off');
  assert.equal(larkSourceState({ ...channel, enabled: false }, null, false, ''), 'off');
  assert.equal(larkSourceState(channel, status, false, ''), 'ready');
  assert.equal(larkHealth(channel, null), 'off');
});

test('meeting coverage counts the three exact event keys, not duplicates or unrelated events', () => {
  assert.deepEqual(POST_MEETING_EVENTS, ['vc.meeting.participant_meeting_ended_v1', 'vc.note.generated_v1', 'minutes.minute.generated_v1']);
  assert.equal(meetingEventCoverage(undefined), 'unknown');
  assert.equal(meetingEventCoverage([]), 'none');
  assert.equal(meetingEventCoverage(['im.message.receive_v1', 'vc.note.generated_v1.extra']), 'none');
  assert.equal(meetingEventCoverage([POST_MEETING_EVENTS[0], POST_MEETING_EVENTS[0]]), 'partial');
  assert.equal(meetingEventCoverage(POST_MEETING_EVENTS.slice(0, 2)), 'partial');
  assert.equal(meetingEventCoverage([...POST_MEETING_EVENTS, 'im.message.receive_v1']), 'all');
});

test('meeting settings edits preserve other keys and the global switch, without replacing unknown lists', () => {
  const other = ['im.message.receive_v1', 'fictional.other_v1', 'fictional.other_v1'];
  const initial = { ...channel, enabled: false, events: [other[0], POST_MEETING_EVENTS[1], ...other.slice(1)] };
  const enabled = withPostMeetingEvents(initial, true);
  assert.equal(enabled.enabled, false, 'configuring events never turns the source on');
  assert.deepEqual(enabled.events, [...initial.events, POST_MEETING_EVENTS[0], POST_MEETING_EVENTS[2]]);
  assert.deepEqual(initial.events, [other[0], POST_MEETING_EVENTS[1], ...other.slice(1)], 'the draft is not mutated');
  assert.equal(withPostMeetingEvents(enabled, true), enabled);
  const disabled = withPostMeetingEvents(enabled, false);
  assert.deepEqual(disabled.events, other);
  assert.equal(disabled.enabled, false);
  assert.equal(withPostMeetingEvents(disabled, false), disabled);
  assert.deepEqual(withPostMeetingEvents({ ...channel, events: [] }, true).events, POST_MEETING_EVENTS);
  assert.equal(withPostMeetingEvents(channel, true), channel, 'a missing list stays missing');
  assert.equal(withPostMeetingEvents(channel, false), channel);
  assert.equal(Object.hasOwn(channel, 'events'), false);
});

test('a signal says where it went, and why it was filtered', () => {
  assert.equal(signalDestination({ outcome: 'delivered' }), '已直投给工作线程');
  assert.equal(signalDestination({ outcome: 'router', reason: 'legacy-seen' }), '送了助手');
  assert.equal(signalDestination({ outcome: 'filtered', reason: 'not-a-request' }), '被过滤：不是在找你');
  assert.equal(signalDestination({ outcome: 'filtered', reason: 'bot-message' }), '被过滤：bot-message', 'a reason this client has no words for still shows');
  assert.equal(signalDestination({ outcome: 'filtered' }), '被过滤');
});
