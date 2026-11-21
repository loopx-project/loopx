import { t } from '../i18n';
import { Popover } from './Popover';
import { StewardConversation } from './StewardConversation';
import { IconClipboard, IconHand, IconPlay } from './icons';
import type { LoopxAttention, LoopxGoal, LoopxStatusFailure } from '../loopx/status';
import type { GoalsFilter } from './GoalsPage';

/**
 * The LoopX steward page, chat-first: entering it is entering the conversation. The chat owns the
 * viewport with the composer pinned at its bottom, as a thread is; the app topbar carries the title
 * and, at its right edge, {@link StewardActions}: what waits on the person, what is running and the
 * brief's numbers, each opening over the chat so nothing takes the person out of the conversation.
 */
export function StewardPage({ token, failure }: { token: string | undefined; failure: LoopxStatusFailure | null }) {
  return <div className="steward-page is-chat">
    <StewardConversation token={token} failure={failure} />
  </div>;
}

type Row = { goalId: string; title: string; sub: string | null };

/** One of the steward's menus: a heading with its count, a row per Goal, and the way to all of them. */
function StewardMenu({ title, count, rows, empty, dot, filter, onOpenGoal, onOpenAll, close }: {
  title: string; count: number | null; rows: Row[]; empty: string; dot: 'warn' | 'ok'; filter: GoalsFilter;
  onOpenGoal: (goalId: string) => void; onOpenAll?: (filter?: GoalsFilter) => void; close: () => void;
}) {
  const heading = <><span>{title}</span>{count !== null && <span className="steward-menu-count">{count}</span>}</>;
  return <div className="steward-menu">
    {/* The heading opens All Goals on this group. */}
    {onOpenAll
      ? <button type="button" className="steward-menu-head" onClick={() => { close(); onOpenAll(filter); }}>{heading}<span className="steward-menu-go" aria-hidden="true">→</span></button>
      : <div className="steward-menu-head">{heading}</div>}
    {rows.length ? <div className="steward-menu-list">
      {rows.map(row => <button key={row.goalId} type="button" className="steward-menu-row" title={row.title} onClick={() => { close(); onOpenGoal(row.goalId); }}>
        <span className={`steward-menu-dot ${dot}`} aria-hidden="true" />
        <span className="steward-menu-text">
          <span className="steward-menu-title">{row.title}</span>
          {row.sub && <span className="steward-menu-sub">{row.sub}</span>}
        </span>
      </button>)}
    </div> : <p className="steward-menu-empty">{empty}</p>}
    {onOpenAll && <button type="button" className="steward-menu-foot" onClick={() => { close(); onOpenAll('all'); }}>{t('goals.allGoals')} →</button>}
  </div>;
}

/** The steward's buttons for the topbar's right edge. */
export function StewardActions({ goals, attention, failure, onOpenGoal, onOpenAll }: {
  goals: LoopxGoal[];
  attention: LoopxAttention[];
  failure: LoopxStatusFailure | null;
  onOpenGoal: (goalId: string) => void;
  onOpenAll?: (filter?: GoalsFilter) => void;
}) {
  const waitingOnUser = attention.filter(item =>
    item.userTodos.open > 0 || item.waitingOn === 'user' || item.waitingOn === 'owner');
  const active = goals.filter(goal => goal.activationState === 'active');
  const stopped = goals.filter(goal => goal.activationState === 'stopped');
  // A failed status read knows nothing; the counts say unknown rather than zero.
  const unknown = failure !== null;
  const needs: Row[] = waitingOnUser.flatMap(item => {
    const goal = goals.find(g => g.goalId === item.goalId);
    return goal ? [{ goalId: goal.goalId, title: goal.title, sub: item.recommendedAction ?? null }] : [];
  });
  // What a running Goal is on: its first open todo, else the core's next step, else nothing rather than a status word.
  const doing = (goalId: string) => { const a = attention.find(item => item.goalId === goalId); return a?.topTodo?.text ?? a?.nextAction ?? null; };
  const running: Row[] = active.map(goal => ({ goalId: goal.goalId, title: goal.title, sub: doing(goal.goalId) }));
  const stats: { label: string; value: number; filter: GoalsFilter }[] = [
    { label: t('loopx.needsYou'), value: waitingOnUser.length, filter: 'needs-you' },
    { label: t('loopx.running'), value: active.length, filter: 'active' },
    { label: t('loopx.stoppedShort'), value: stopped.length, filter: 'stopped' },
  ];
  return <div className="steward-actions">
    <Popover label={t('loopx.needsYou')} align="end" placement="below"
      triggerClassName="icon-btn steward-head-btn" triggerLabel={waitingOnUser.length ? t('loopx.needsYouCount', waitingOnUser.length) : t('loopx.needsYou')}
      trigger={<>
        <IconHand size={18} />
        {waitingOnUser.length ? <span className="steward-head-badge" aria-hidden="true">{waitingOnUser.length > 9 ? '9+' : waitingOnUser.length}</span> : null}
      </>}>
      {close => <StewardMenu title={t('loopx.needsYou')} count={unknown ? null : needs.length} rows={needs} dot="warn" filter="needs-you"
        empty={unknown ? t('loopx.goal.stateUnavailable') : t('loopx.needsYouEmpty')} onOpenGoal={onOpenGoal} onOpenAll={onOpenAll} close={close} />}
    </Popover>
    <Popover label={t('loopx.running')} align="end" placement="below"
      triggerClassName="icon-btn steward-head-btn" triggerLabel={t('loopx.running')}
      trigger={<IconPlay size={18} />}>
      {close => <StewardMenu title={t('loopx.running')} count={unknown ? null : running.length} rows={running} dot="ok" filter="active"
        empty={unknown ? t('loopx.goal.stateUnavailable') : t('loopx.runningEmpty')} onOpenGoal={onOpenGoal} onOpenAll={onOpenAll} close={close} />}
    </Popover>
    <Popover label={t('loopx.stewardBrief')} align="end" placement="below"
      triggerClassName="icon-btn steward-head-btn" triggerLabel={t('loopx.stewardBrief')}
      trigger={<IconClipboard size={18} />}>
      {close => <div className="steward-menu">
        <div className="steward-menu-head"><span>{t('loopx.stewardBrief')}</span></div>
        <div className="steward-brief-stats">
          {stats.map(stat => {
            const body = <><span className="steward-brief-num">{unknown ? '–' : stat.value}</span><span className="steward-brief-label">{stat.label}</span></>;
            return onOpenAll
              ? <button key={stat.label} type="button" className="steward-brief-stat" onClick={() => { close(); onOpenAll(stat.filter); }}>{body}</button>
              : <div key={stat.label} className="steward-brief-stat">{body}</div>;
          })}
        </div>
        {onOpenAll && <button type="button" className="steward-menu-foot" onClick={() => { close(); onOpenAll('all'); }}>{t('goals.allGoals')} →</button>}
      </div>}
    </Popover>
  </div>;
}
