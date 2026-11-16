import { t } from '../i18n';
import { Popover } from './Popover';
import { StewardConversation } from './StewardConversation';
import type { LoopxAttention, LoopxGoal, LoopxStatusFailure } from '../loopx/status';

/**
 * The LoopX steward page, chat-first: entering it is entering the conversation — the chat owns the
 * viewport and the composer is pinned at its bottom, as a thread is. Everything else the steward
 * knows arrives on demand: the brief (what waits on the person, what is running) sits behind one
 * button in the header, badged when something needs the person.
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
  return <div className="steward-page is-chat">
    <header className="steward-head">
      <h1>{t('loopx.steward')}</h1>
      <Popover label={t('loopx.stewardBrief')} triggerClassName="steward-brief-btn" align="end"
        triggerLabel={waitingOnUser.length ? t('loopx.stewardBriefCount', waitingOnUser.length) : t('loopx.stewardBrief')}>
        {close => <div className="steward-brief-panel">
          <h2>{t('loopx.needsYou')}</h2>
          {waitingOnUser.length ? waitingOnUser.map(item => {
            const goal = goals.find(g => g.goalId === item.goalId);
            if (!goal) return null;
            return <button key={item.goalId} type="button" className="steward-row" onClick={() => { close(); onOpenGoal(item.goalId); }}>
              <strong>{item.recommendedAction ?? goal.title}</strong>
              <small>{goal.title}</small>
            </button>;
          }) : <p className="goal-empty">{failure ? '' : t('loopx.needsYouEmpty')}</p>}
          <h2>{t('loopx.running')}</h2>
          {active.length ? active.map(goal => (
            <button key={goal.goalId} type="button" className="steward-row" onClick={() => { close(); onOpenGoal(goal.goalId); }}>
              <strong>{goal.title}</strong><small>{goal.status}</small>
            </button>
          )) : <p className="goal-empty">{t('loopx.runningEmpty')}</p>}
        </div>}
      </Popover>
    </header>
    <StewardConversation token={token} failure={failure} />
  </div>;
}
