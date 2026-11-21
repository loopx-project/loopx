/** Goal notifications: the Lark group each Goal can be bound to, and the browser's own alert when something waits on you. */
import { localeTag } from '../i18n';
import type { LoopxAttention } from './status';
import { needsYouGoalIds } from './status';

export interface NotifyTarget { enabled: boolean; provider: string; targetName: string }
export interface NotifyResult { ok: boolean; message?: string }

const GOAL_CHANNEL = '/api/loopx/api/chat/goal-channel';

async function call<T>(token: string | undefined, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${GOAL_CHANNEL}/${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { Authorization: `Bearer ${token ?? ''}`, 'Accept-Language': localeTag(), ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const json = await res.json().catch(() => ({})) as T & { error?: string };
  if (!res.ok) throw new Error(json.error ?? `HTTP ${res.status}`);
  return json;
}

export const fetchNotifyTargets = (token: string | undefined): Promise<NotifyTarget[]> =>
  call<{ targets?: { enabled: boolean; provider: string; target_name: string }[] }>(token, 'targets')
    .then(r => (r.targets ?? []).map(x => ({ enabled: x.enabled, provider: x.provider, targetName: x.target_name })));

interface RawOperation { ok?: boolean; blocker?: string; public_summary?: string }
const asResult = (raw: RawOperation): NotifyResult => ({ ok: raw.ok === true, message: raw.public_summary ?? raw.blocker });

/** Bind a Goal to a group. `execute: false` only previews; the core answers with what it would do. */
export const setupGoalChannel = (token: string | undefined, goalId: string, target: string, execute: boolean) =>
  call<RawOperation>(token, 'setup', { goal_id: goalId, target, execute }).then(asResult);

export const configureAutoNotify = (token: string | undefined, goalId: string, kind: 'human_gate' | 'blocked_notice', on: boolean) =>
  call<RawOperation>(token, 'configure', { goal_id: goalId, ...(kind === 'blocked_notice' ? { auto_notify_blocked_notices: on } : { auto_notify_human_gates: on }) }).then(asResult);

/** Goals that started waiting on the person since the last read. The first read only sets the baseline, so a reload is not a flood. */
export function newlyWaiting(previous: Set<string> | null, attention: LoopxAttention[]): { now: Set<string>; fresh: string[] } {
  const now = needsYouGoalIds(attention);
  if (previous === null) return { now, fresh: [] };
  return { now, fresh: [...now].filter(id => !previous.has(id)) };
}

const KEY = 'loochat.notify.browser';
export const browserAlertsWanted = (): boolean => {
  try { return localStorage.getItem(KEY) === '1'; } catch { return false; }
};
export const setBrowserAlertsWanted = (on: boolean): void => {
  try { localStorage.setItem(KEY, on ? '1' : '0'); } catch { /* storage may be off */ }
};
/** 'unsupported' covers a browser without the API and a page the browser will not allow it on (plain http off localhost). */
export const browserPermission = (): NotificationPermission | 'unsupported' =>
  typeof Notification === 'undefined' ? 'unsupported' : Notification.permission;
