import { t } from '../i18n';
import type { LoopxAttention, LoopxGoal } from '../loopx/status';
import { failureLabel } from './GoalWorkPage';

/**
 * The LoopX steward page, in the place Calli's assistant home used to be. The conversation itself
 * lives in the Python manager service; batch A shows the brief from the status projection and the
 * honest state until the manager conversation view is wired.
 */
export function StewardPage({ goals, attention, failure, onOpenGoal }: {
  goals: LoopxGoal[];
  attention: LoopxAttention[];
  failure: Parameters<typeof failureLabel>[0];
  onOpenGoal: (goalId: string) => void;
}) {
  const waitingOnUser = attention.filter(item =>
    item.userTodos.open > 0 || item.waitingOn === 'user' || item.waitingOn === 'owner');
  const active = goals.filter(goal => goal.activationState === 'active');
  return <div className="steward-page">
    <header className="steward-head"><h1>{t('loopx.steward')}</h1></header>
    {failure ? <p role="alert" className="goal-failure">{failureLabel(failure)}</p> : null}
    <section className="steward-conversation" aria-label={t('loopx.stewardConversation')}>
      <p className="steward-hint">{t('loopx.stewardHint')}</p>
    </section>
    <section className="steward-brief" aria-label={t('loopx.stewardBrief')}>
      <h2>{t('loopx.needsYou')}</h2>
      {waitingOnUser.length ? waitingOnUser.map(item => {
        const goal = goals.find(g => g.goalId === item.goalId);
        if (!goal) return null;
        return <button key={item.goalId} type="button" className="steward-row" onClick={() => onOpenGoal(item.goalId)}>
          <strong>{item.recommendedAction ?? goal.title}</strong>
          <small>{goal.title}</small>
        </button>;
      }) : <p className="goal-empty">{failure ? '' : t('loopx.needsYouEmpty')}</p>}
      <h2>{t('loopx.running')}</h2>
      {active.length ? active.map(goal => (
        <button key={goal.goalId} type="button" className="steward-row" onClick={() => onOpenGoal(goal.goalId)}>
          <strong>{goal.title}</strong><small>{goal.status}</small>
        </button>
      )) : <p className="goal-empty">{t('loopx.runningEmpty')}</p>}
    </section>
  </div>;
}
