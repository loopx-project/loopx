import type { LarkChannelSettings, LarkChannelStatus, LarkRealtime, MatterSignal } from './api';
import { t } from './i18n';

/** How the channel looks from the page, once what the hub reports is read together. */
export type LarkHealth = 'off' | 'no-cli' | 'no-login' | 'no-agent' | 'failed' | 'ok';
export function larkHealth(channel: LarkChannelSettings | null, status: LarkChannelStatus | null): LarkHealth {
  if (!channel?.enabled || !status) return 'off';
  // The hub's last error is a stable code; the ones a person can fix themselves get their own way out.
  if (!status.cli || status.lastError === 'cli_missing') return 'no-cli';
  if (!status.loggedIn || status.lastError === 'lark_not_logged_in' || status.lastError === 'lark_auth_failed') return 'no-login';
  if (status.lastError === 'personal agent is not configured') return 'no-agent';
  return status.lastError ? 'failed' : 'ok';
}

/** Null status still means off to larkHealth; loading and failed reads are separate page states. */
export type LarkSourceState = 'loading' | 'unknown' | 'load-failed' | 'off' | 'ready';
export function larkSourceState(channel: LarkChannelSettings | null, status: LarkChannelStatus | null, loading: boolean, loadError: string): LarkSourceState {
  if (loadError) return 'load-failed';
  if (loading) return 'loading';
  if (!channel) return 'unknown';
  if (!channel.enabled) return 'off';
  return status ? 'ready' : 'unknown';
}

export const LARK_ROLES = ['boss', 'pm', 'other'] as const;
export const LARK_INTERVALS = [15, 30, 60, 120];
export const POST_MEETING_EVENTS = ['vc.meeting.participant_meeting_ended_v1', 'vc.note.generated_v1', 'minutes.minute.generated_v1'] as const;
export type MeetingEventCoverage = 'unknown' | 'none' | 'partial' | 'all';

/** Configuration only, not evidence that the realtime channel receives these events. */
export function meetingEventCoverage(events: readonly string[] | undefined): MeetingEventCoverage {
  if (!Array.isArray(events)) return 'unknown';
  const count = POST_MEETING_EVENTS.filter(event => events.includes(event)).length;
  return count === POST_MEETING_EVENTS.length ? 'all' : count > 0 ? 'partial' : 'none';
}

/** A draft edit: keep other event keys and the global switch, and never invent an unknown list. */
export function withPostMeetingEvents(channel: LarkChannelSettings, enabled: boolean): LarkChannelSettings {
  const current = channel.events;
  if (!Array.isArray(current)) return channel;
  const events = enabled
    ? [...current, ...POST_MEETING_EVENTS.filter(event => !current.includes(event))]
    : current.filter(event => !(POST_MEETING_EVENTS as readonly string[]).includes(event));
  return events.length === current.length ? channel : { ...channel, events };
}

/** The realtime line: quiet when live, a warning when events are not coming in, nothing while it settles. */
export function realtimeLine(realtime: LarkRealtime | undefined): { text: string; warn: boolean } | null {
  switch (realtime) {
    case 'live': return { text: t('lark.realtime.live'), warn: false };
    case 'busy-elsewhere':
    case 'console-off':
    case 'unavailable':
    case 'error': return { text: t(`lark.realtime.${realtime}`), warn: true };
    default: return null;
  }
}

const FILTER_REASONS = ['deleted', 'outside-watch-scope', 'not-a-request'] as const;

/** Where a kept signal went, in words. */
export function signalDestination({ outcome, reason }: MatterSignal['disposition']): string {
  if (outcome === 'delivered') return t('desk.signal.delivered');
  if (outcome === 'router') return t('desk.signal.router');
  const known = FILTER_REASONS.find((item) => item === reason);
  return known ? t('desk.signal.filteredBecause', t(`desk.signal.reason.${known}`)) : reason ? t('desk.signal.filteredBecause', reason) : t('desk.signal.filtered');
}
