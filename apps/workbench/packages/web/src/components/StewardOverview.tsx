import { useRef, useState, type ReactNode } from 'react';
import type { Thread } from '../api';
import { t } from '../i18n';
import { joinGoalConversation } from '../loopx/join';
import { goalOf } from '../loopx/launch';
import { needsYouGoalIds, type LoopxAttention, type LoopxGoal, type LoopxStatusFailure } from '../loopx/status';
import { GoalLifecycle } from './GoalLifecycle';
import { IconChevron, IconChevronRight, IconHand, IconPlay, IconRefresh, IconStop, IconCrab } from './icons';

/** The steward reads and manages Goals in the same paper overview as a conversation. */
export function StewardOverview({ token, goals, attention, failure, loading, onChanged, onRetry, threads, onOpenThread, defaultAgent, pickDirectory, onOpenAll }: {
  token: string | undefined;
  goals: LoopxGoal[];
  attention: LoopxAttention[];
  failure: LoopxStatusFailure | null;
  loading: boolean;
  onChanged: () => void;
  onRetry: () => void;
  threads: Thread[];
  onOpenThread: (threadId: string) => void;
  defaultAgent: string;
  pickDirectory?: () => Promise<string | null>;
  onOpenAll?: () => void;
}) {
  const needsYou = needsYouGoalIds(attention);
  const stopped = goals.filter(goal => goal.activationState === 'stopped');
  const needs = goals.filter(goal => goal.activationState !== 'stopped' && needsYou.has(goal.goalId));
  const active = goals.filter(goal => goal.activationState === 'active' && !needsYou.has(goal.goalId));
  const unknown = goals.filter(goal => goal.activationState !== 'active' && goal.activationState !== 'stopped' && !needsYou.has(goal.goalId));
  const cannotJoin = !defaultAgent && [...needs, ...active].some(goal => !threads.some(thread => !thread.loopxInfo?.missing && (goalOf(thread)?.goalId ?? thread.loopxInfo?.goalId) === goal.goalId));
  const row = (goal: LoopxGoal, waiting = false) => <GoalRow key={goal.goalId} token={token} goal={goal} waiting={waiting}
    attention={attention.find(item => item.goalId === goal.goalId)} onChanged={onChanged} threads={threads}
    onOpenThread={onOpenThread} defaultAgent={defaultAgent} pickDirectory={pickDirectory} />;

  return <div className="thread-overview steward-overview" data-ready="true" aria-busy={loading}>
    <div className="overview-content">
      {!failure && cannotJoin && <p className="overview-empty" role="status">{t('loopx.overview.noAgent')}</p>}
      {failure ? <p className="overview-empty" role="alert">{t('loopx.goal.stateUnavailable')}</p> : <>
        {needs.length > 0 && <GoalGroup label={t('loopx.needsYou')} count={needs.length} icon={<IconHand size={14} />} waiting>
          {needs.map(goal => row(goal, true))}
        </GoalGroup>}
        {active.length > 0 && <GoalGroup label={t('loopx.running')} count={active.length} icon={<IconPlay size={14} />}>
          {active.map(goal => row(goal))}
        </GoalGroup>}
        {stopped.length > 0 && <GoalGroup label={t('loopx.stoppedShort')} count={stopped.length} icon={<IconStop size={14} />} initiallyOpen={false}>
          {stopped.map(goal => row(goal))}
        </GoalGroup>}
        {unknown.length > 0 && <GoalGroup label={t('loopx.goal.status.unknown')} count={unknown.length} icon={<IconCrab size={14} />} initiallyOpen={false}>
          {unknown.map(goal => row(goal))}
        </GoalGroup>}
        {!goals.length && <p className="overview-empty" role="status">{t(loading ? 'common.loading' : 'loopx.overview.empty')}</p>}
      </>}
    </div>
    <div className="overview-foot">
      <span className="overview-readings">{!failure && (goals.length > 0 || !loading) ? t('loopx.overview.summary', goals.filter(goal => goal.activationState === 'active').length, goals.length) : ''}</span>
      <button type="button" className="icon-btn overview-refresh" aria-label={t(failure ? 'common.retry' : 'overview.refresh')} aria-busy={loading} disabled={loading} onClick={onRetry}><IconRefresh size={14} /></button>
      {onOpenAll && <button type="button" className="overview-trace" onClick={onOpenAll}>{t('goals.allGoals')}<IconChevronRight size={12} /></button>}
    </div>
  </div>;
}

function GoalGroup({ label, count, icon, waiting = false, initiallyOpen = true, children }: {
  label: string; count: number; icon: ReactNode; waiting?: boolean; initiallyOpen?: boolean; children: ReactNode;
}) {
  const [open, setOpen] = useState(initiallyOpen);
  return <section className={`overview-group${open ? ' open' : ''}${waiting ? ' needs-you' : ''}`} aria-label={label}>
    <div className="overview-head">
      <button type="button" className="overview-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="overview-chevron"><IconChevron size={12} open={false} /></span>
        <span className="overview-head-icon">{icon}</span><strong>{label}</strong><span className="overview-summary">{count}</span>
      </button>
    </div>
    {open && <div className="overview-body">{children}</div>}
  </section>;
}

function GoalRow({ token, goal, attention, waiting, onChanged, threads, onOpenThread, defaultAgent, pickDirectory }: {
  token: string | undefined; goal: LoopxGoal; attention?: LoopxAttention; waiting: boolean; onChanged: () => void;
  threads: Thread[]; onOpenThread: (threadId: string) => void; defaultAgent: string; pickDirectory?: () => Promise<string | null>;
}) {
  const [joining, setJoining] = useState(false);
  const joiningRef = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const bound = threads.find(thread => !thread.loopxInfo?.missing && (goalOf(thread)?.goalId ?? thread.loopxInfo?.goalId) === goal.goalId);
  const summary = attention?.topTodo?.text ?? (waiting ? attention?.recommendedAction : attention?.nextAction) ?? null;
  const open = async () => {
    if (joiningRef.current) return;
    setError(null);
    if (bound) { onOpenThread(bound.id); return; }
    joiningRef.current = true;
    setJoining(true);
    try {
      const cwd = pickDirectory ? await pickDirectory() : undefined;
      if (cwd === null) return;
      const result = await joinGoalConversation(token, { goalId: goal.goalId, agent: defaultAgent, ...(cwd ? { cwd } : {}) });
      onOpenThread(result.threadId);
    } catch (cause) {
      setError(t('loopx.overview.openFailed', cause instanceof Error ? cause.message : String(cause)));
    } finally {
      joiningRef.current = false;
      setJoining(false);
    }
  };
  return <div className="overview-goal" data-goal-id={goal.goalId}>
    <div className="overview-goal-heading"><span className="overview-goal-title" title={goal.title}>{goal.title}</span></div>
    {summary && <div className="overview-goal-summary" title={summary}>{summary}</div>}
    <div className="overview-goal-actions">
      {goal.activationState !== 'stopped' && <button type="button" className="overview-more" disabled={joining || (!defaultAgent && !bound)} aria-busy={joining} onClick={() => void open()}>{t('loopx.overview.openConversation')}</button>}
      {(goal.activationState === 'active' || goal.activationState === 'stopped') && <GoalLifecycle token={token} goal={goal} onChanged={onChanged} />}
    </div>
    {error && <p className="overview-empty overview-attention" role="alert">{error}</p>}
  </div>;
}
