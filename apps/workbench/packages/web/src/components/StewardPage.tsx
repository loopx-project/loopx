import { t } from '../i18n';
import { StewardConversation } from './StewardConversation';
import type { LoopxAttention, LoopxGoal, LoopxStatusFailure } from '../loopx/status';

/**
 * The LoopX steward page, in the place Calli's assistant home used to be: the manager's own
 * conversation on top, the brief (what waits on the person, what is running) below it. Both read
 * the same core the sidebar does.
 */
export function StewardPage({ token, goals, attention, failure, onOpenGoal }: {
  token: string | undefined;
  goals: LoopxGoal[];
  attention: LoopxAttention[];
  failure: LoopxStatusFailure | null;
  onOpenGoal: (goalId: string) => void;
}) {
  const waitingOnUser = attention.filter(item =>
    item.userTodos.open > 0 || item.waitingOn === 'user' || item.waitingOn === 'owner');
  const active = goals.filter(goal => goal.activationState === 'active');
  return <div className="steward-page">
    <header className="steward-head"><h1>{t('loopx.steward')}</h1></header>
    <StewardConversation token={token} failure={failure} />
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
