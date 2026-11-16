import { t } from '../i18n';
import { Popover } from './Popover';
import { StewardConversation } from './StewardConversation';
import { IconClipboard, IconHand, IconPlay } from './icons';
import type { LoopxAttention, LoopxGoal, LoopxStatusFailure } from '../loopx/status';

/**
 * The LoopX steward page, chat-first: entering it is entering the conversation. The chat owns the
 * viewport with the composer pinned at its bottom, as a thread is; the app topbar carries the back
 * button and the title. Everything else the steward knows sits behind the buttons at the head's
 * right edge, in the manner of the assistant's header slot: what waits on the person (badged with
 * its count when it is not empty), what is running, and the brief's numbers. Each opens over the
 * chat; nothing takes the person out of the conversation.
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
  const stopped = goals.filter(goal => goal.activationState === 'stopped');
  // A failed status read knows nothing; the counts say unknown rather than zero.
  const unknown = failure !== null;
  return <div className="steward-page is-chat">
    <header className="steward-head">
      <div className="steward-head-actions">
        <Popover label={t('loopx.needsYou')} align="end" placement="below"
          triggerClassName="icon-btn steward-head-btn" triggerLabel={waitingOnUser.length ? t('loopx.needsYouCount', waitingOnUser.length) : t('loopx.needsYou')}
          trigger={<>
            <IconHand size={18} />
            {waitingOnUser.length ? <span className="steward-head-badge" aria-hidden="true">{waitingOnUser.length > 9 ? '9+' : waitingOnUser.length}</span> : null}
          </>}>
          {close => <div className="steward-brief-panel">
            {waitingOnUser.length ? waitingOnUser.map(item => {
              const goal = goals.find(g => g.goalId === item.goalId);
              if (!goal) return null;
              return <button key={item.goalId} type="button" className="steward-row" onClick={() => { close(); onOpenGoal(item.goalId); }}>
                <strong>{item.recommendedAction ?? goal.title}</strong>
                <small>{goal.title}</small>
              </button>;
            }) : <p className="goal-empty">{unknown ? '' : t('loopx.needsYouEmpty')}</p>}
          </div>}
        </Popover>
        <Popover label={t('loopx.running')} align="end" placement="below"
          triggerClassName="icon-btn steward-head-btn" triggerLabel={t('loopx.running')}
          trigger={<IconPlay size={18} />}>
          {close => <div className="steward-brief-panel">
            {active.length ? active.map(goal => (
              <button key={goal.goalId} type="button" className="steward-row" onClick={() => { close(); onOpenGoal(goal.goalId); }}>
                <strong>{goal.title}</strong><small>{goal.status}</small>
              </button>
            )) : <p className="goal-empty">{unknown ? '' : t('loopx.runningEmpty')}</p>}
          </div>}
        </Popover>
        <Popover label={t('loopx.stewardBrief')} align="end" placement="below"
          triggerClassName="icon-btn steward-head-btn" triggerLabel={t('loopx.stewardBrief')}
          trigger={<IconClipboard size={18} />}>
          {() => <div className="steward-brief-panel">
            <dl className="steward-brief-stats">
              <div><dt>{t('loopx.running')}</dt><dd>{unknown ? '?' : active.length}</dd></div>
              <div><dt>{t('loopx.needsYou')}</dt><dd>{unknown ? '?' : waitingOnUser.length}</dd></div>
              <div><dt>{t('loopx.stoppedShort')}</dt><dd>{unknown ? '?' : stopped.length}</dd></div>
            </dl>
          </div>}
        </Popover>
      </div>
    </header>
    <StewardConversation token={token} failure={failure} />
  </div>;
}
