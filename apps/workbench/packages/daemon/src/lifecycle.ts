/**
 * One reading of the event log for everything that asks "how did that turn end" and "is this thread
 * waiting for a person": scheduled tasks, collaboration and the extension host. The cases are the
 * table in docs/extensions-design.md, in the section on unified event semantics.
 */
import type { Attention, TurnOrigin, TurnOutcome } from '@calli/extension-api';
import type { StoredEvent } from './events.js';
import type { ThreadView } from './threads.js';

export interface TurnEnd {
  outcome: TurnOutcome;
  /** Failure code such as `plan_exhausted`, `permission_refused` or `interrupted`. */
  code: string | null;
  /** What the failure said. Null when the turn did not fail. */
  message: string | null;
  /** Copied in from the agent's own history: not new work. */
  imported: boolean;
  /** Run in a terminal and synced in afterwards: not new work. */
  fromTerminal: boolean;
}

/**
 * How a turn ended, or null when the event does not end one. A failed turn has only `turn.error`,
 * never a `turn.done` too; a stop (even before the agent started) is a `turn.done` that says
 * `cancelled`; a restart cuts a running turn off with `turn.error` and code `interrupted`.
 */
export function turnOutcome(ev: Pick<StoredEvent, 'type' | 'data'>): TurnEnd | null {
  const history = { imported: ev.data.imported === true, fromTerminal: ev.data.fromTerminal === true };
  if (ev.type === 'turn.done') {
    return { outcome: ev.data.stopReason === 'cancelled' ? 'cancelled' : 'ok', code: null, message: null, ...history };
  }
  if (ev.type === 'turn.error') {
    const code = typeof ev.data.code === 'string' ? ev.data.code : null;
    return {
      outcome: code === 'interrupted' ? 'interrupted' : 'error',
      code,
      message: String(ev.data.message ?? 'agent error'),
      ...history,
    };
  }
  return null;
}

/** Where a turn came from, read off its `turn.started`. Three separate dimensions, see TurnOrigin. */
export function turnOrigin(started: Pick<StoredEvent, 'data'>): TurnOrigin {
  const d = started.data;
  const imported = d.imported === true;
  const fromTerminal = d.fromTerminal === true;
  const initiator: TurnOrigin['initiator'] = d.coordination
    ? 'coordination'
    : typeof d.handover === 'string' && d.handover
      ? 'handover'
      : d.scheduled === true
        ? 'schedule'
        : imported || fromTerminal
          ? 'unknown'
          : 'user';
  return { initiator, imported, fromTerminal, control: d.control === true };
}

/**
 * Whether a thread wants a person, most pressing first: an approval, a question, a plan to confirm,
 * a failed turn nobody has looked at, a finished one nobody has looked at. The daemon has no
 * screen, so `unread` is the thread's own flag; the page decides per device (web sidebar.ts,
 * threadAttention), with the same order.
 */
export function attentionOf(view: Pick<ThreadView, 'waiting' | 'waitingReason' | 'unread' | 'status'>): Attention | null {
  if (view.waiting) return { kind: view.waitingReason ?? 'approval' };
  if (view.unread) return { kind: view.status === 'error' ? 'error' : 'unread' };
  return null;
}
