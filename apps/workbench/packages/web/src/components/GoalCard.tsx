import { useEffect, useState } from 'react';
import type { GoalView } from '../api';
import { t } from '../i18n';
import { clockLabel } from '../timeline';
import { IconChevron, IconPause, IconPlay, IconTrash } from './icons';

/** Which quick actions the card offers, from what the agent supports right now. */
export type GoalCardAction = 'pause' | 'resume' | 'clear';

export interface GoalCardProps {
  goal: GoalView;
  /** A turn is running: only the agent's running controls may act. */
  running: boolean;
  actions?: Array<'set' | 'show' | 'pause' | 'resume' | 'clear'>;
  controls?: Array<'show' | 'pause' | 'clear'>;
  onAction: (action: GoalCardAction) => void;
}

/** The card's heading for a status, in the agent's own vocabulary. */
export function goalCardLabel(status: string): string {
  const labels: Record<string, string> = {
    active: t('goal.card.active'),
    paused: t('goal.card.paused'),
    blocked: t('goal.card.blocked'),
    complete: t('goal.card.complete'),
    achieved: t('goal.card.complete'),
    completed: t('goal.card.complete'),
    usageLimited: t('goal.card.limited'),
    budgetLimited: t('goal.card.limited'),
    failed: t('goal.card.failed'),
  };
  return labels[status] ?? t('goal.card.other');
}

/** Whether the dot next to the heading should say "working" (green) or rest. */
export const goalIsActive = (status: string): boolean => status === 'active';

/** Statuses where the work stopped but could pick up where it left off. */
const resumable = new Set(['paused', 'blocked', 'usageLimited', 'budgetLimited']);

/** Elapsed active work, ms, counting on from the view's timestamp while the goal is active. */
export function goalElapsedMs(goal: GoalView, now = Date.now()): number {
  return goal.activeMs + (goalIsActive(goal.status) ? Math.max(0, now - Date.parse(goal.at)) : 0);
}

/** Which quick actions apply to the goal right now. Unavailable ones stay off the card. */
export function goalCardActions(goal: GoalView, running: boolean, actions?: GoalCardProps['actions'], controls?: GoalCardProps['controls']): GoalCardAction[] {
  const supported = (action: GoalCardAction) =>
    running ? (controls ?? []).includes(action as 'pause') : (actions ?? []).includes(action);
  const out: GoalCardAction[] = [];
  if (goalIsActive(goal.status) && supported('pause')) out.push('pause');
  if (resumable.has(goal.status) && supported('resume')) out.push('resume');
  if (supported('clear')) out.push('clear');
  return out;
}

/** Like the work blocks' durations: whole seconds first, then minutes with the remainder. */
function elapsedLabel(ms: number): string {
  const secs = Math.max(0, Math.round(ms / 1000));
  if (secs < 60) return t('duration.seconds', secs);
  if (secs < 3600) return t('duration.minutes', Math.floor(secs / 60), secs % 60);
  return t('duration.hours', Math.floor(secs / 3600), Math.floor(secs % 3600 / 60), secs % 60);
}

/**
 * The native goal as a band above the input: one line for what is running and for how long, the
 * quick controls beside it, and the details behind a click.
 */
export function GoalCard({ goal, running, actions, controls, onAction }: GoalCardProps) {
  const [now, setNow] = useState(() => Date.now());
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!goalIsActive(goal.status)) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [goal.status]);

  const available = goalCardActions(goal, running, actions, controls);
  return (
    <div className={`goal-card${goalIsActive(goal.status) ? ' active' : ''}`} aria-label={t('goal.card.other')}>
      <div className="goal-card-row">
        <span className={`goal-dot${goalIsActive(goal.status) ? ' on' : ''}`} aria-hidden="true" />
        <span className="goal-heading">{goalCardLabel(goal.status)}</span>
        <span className="goal-card-objective" title={goal.objective}>{goal.objective}</span>
        <span className="goal-elapsed">{elapsedLabel(goalElapsedMs(goal, now))}</span>
        {available.map((action) => (
          <button
            key={action}
            type="button"
            className="icon-btn goal-action"
            aria-label={t(`goal.${action}`)}
            title={t(`goal.${action}`)}
            onClick={() => onAction(action)}
          >
            {action === 'pause' ? <IconPause size={15} /> : action === 'resume' ? <IconPlay size={15} /> : <IconTrash size={15} />}
          </button>
        ))}
        <button
          type="button"
          className="icon-btn goal-action"
          aria-label={t('goal.details')}
          title={t('goal.details')}
          aria-expanded={open}
          onClick={() => setOpen(!open)}
        >
          <IconChevron size={14} open={open} />
        </button>
      </div>
      {open && (
        <div className="goal-card-details">
          <p className="goal-full">{goal.objective}</p>
          <dl>
            <div>
              <dt>{t('goal.status')}</dt>
              <dd>{goalCardLabel(goal.status)}</dd>
            </div>
            <div>
              <dt>{t('goal.started')}</dt>
              <dd>{clockLabel(goal.startedAt)}</dd>
            </div>
            {goal.tokensUsed !== undefined && (
              <div>
                <dt>{t('goal.tokens')}</dt>
                <dd>{t('goal.tokensUsed', goal.tokensUsed, goal.tokenBudget)}</dd>
              </div>
            )}
          </dl>
        </div>
      )}
    </div>
  );
}
