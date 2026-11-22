import type { ThreadSummary } from '@calli/extension-api';
import type { Locale } from '@calli/extension-api/web';
import { t } from './i18n';

/** Goal state is stored work state, never evidence of a running agent. */
export function goalStatus(status: string | null, locale: Locale): string | null {
  return status === 'stopped' || status === 'paused' ? t(locale, 'goalPaused')
    : status === 'active_state_user_gate' || status === 'active_state_user_todo' ? t(locale, 'waiting')
    : status === 'connected_without_run' ? t(locale, 'goalReady')
    : status === 'active_state_agent_todo' ? t(locale, 'goalWork')
    : status === 'state_refreshed' ? t(locale, 'goalRefreshed')
    : status === 'state_incomplete' || status === 'state_projection_gap' ? t(locale, 'goalIncomplete')
    : status === 'run_artifact_missing' ? t(locale, 'goalMissingResult')
    : status === 'unknown' ? t(locale, 'goalUnknown')
    : status?.replace(/_/g, ' ') || null;
}

export function goalLabel(goal: ThreadSummary['goal'], locale: Locale): string | null {
  if (!goal?.goalId) return null;
  const word = goalStatus(goal.status, locale);
  return `${goal.title || goal.goalId}${word ? ` · ${word}` : ''}`;
}
