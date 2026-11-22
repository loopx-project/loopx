import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { Thread } from '../api';
import { t } from '../i18n';
import { joinGoalConversation } from '../loopx/join';
import { goalConversation, hasGoalConversations, type GoalRequest, type GoalSection } from '../loopx/goal-navigation';
import { needsYouGoalIds, type LoopxAttention, type LoopxGoal, type LoopxStatusFailure } from '../loopx/status';
import { GoalDetails } from './GoalDetails';
import { GoalLifecycle } from './GoalLifecycle';
import { IconChevron, IconChevronRight, IconHand, IconPlay, IconRefresh, IconStop, IconCrab } from './icons';

/** The steward reads and manages Goals in the same paper overview as a conversation. */
export function StewardOverview({ token, goals, attention, failure, loading, onChanged, onRetry, threads, onOpenThread, defaultAgent, pickDirectory, onOpenAll, onBrowseFiles, focusedGoal }: {
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
  onBrowseFiles?: (threadId: string) => void;
  focusedGoal?: GoalRequest;
}) {
  const [expandedGoalId, setExpandedGoalId] = useState(focusedGoal?.goalId ?? null);
  useEffect(() => { setExpandedGoalId(focusedGoal?.goalId ?? null); }, [focusedGoal]);
  const needsYou = needsYouGoalIds(attention);
  const stopped = goals.filter(goal => goal.activationState === 'stopped');
  const needs = goals.filter(goal => goal.activationState !== 'stopped' && needsYou.has(goal.goalId));
  const active = goals.filter(goal => goal.activationState === 'active' && !needsYou.has(goal.goalId));
  const unknown = goals.filter(goal => goal.activationState !== 'active' && goal.activationState !== 'stopped' && !needsYou.has(goal.goalId));
  const cannotJoin = !defaultAgent && [...needs, ...active].some(goal => !goalConversation(goal.goalId, threads));
  const canOpenBoard = !failure && hasGoalConversations(goals, threads);
  const row = (goal: LoopxGoal, waiting = false) => <GoalRow key={goal.goalId} token={token} goal={goal} waiting={waiting}
    attention={attention.find(item => item.goalId === goal.goalId)} onChanged={onChanged} threads={threads}
    onOpenThread={onOpenThread} defaultAgent={defaultAgent} pickDirectory={pickDirectory} onBrowseFiles={onBrowseFiles} focused={focusedGoal?.goalId === goal.goalId} focusSignal={focusedGoal} section={focusedGoal?.goalId === goal.goalId ? focusedGoal.section : null} expanded={expandedGoalId === goal.goalId} onToggle={() => setExpandedGoalId(current => current === goal.goalId ? null : goal.goalId)} />;

  return <div className="thread-overview steward-overview" data-ready="true" aria-busy={loading}>
    <div className="overview-content">
      {!failure && cannotJoin && <p className="overview-empty" role="status">{t('loopx.overview.noAgent')}</p>}
      {failure ? <p className="overview-empty" role="alert">{t('loopx.goal.stateUnavailable')}</p> : <>
        {needs.length > 0 && <GoalGroup focusSignal={focusedGoal} focusOpen={needs.some(goal => goal.goalId === expandedGoalId)} label={t('loopx.needsYou')} count={needs.length} icon={<IconHand size={14} />} waiting>
          {needs.map(goal => row(goal, true))}
        </GoalGroup>}
        {active.length > 0 && <GoalGroup focusSignal={focusedGoal} focusOpen={active.some(goal => goal.goalId === expandedGoalId)} label={t('loopx.running')} count={active.length} icon={<IconPlay size={14} />}>
          {active.map(goal => row(goal))}
        </GoalGroup>}
        {stopped.length > 0 && <GoalGroup focusSignal={focusedGoal} focusOpen={stopped.some(goal => goal.goalId === expandedGoalId)} label={t('loopx.stoppedShort')} count={stopped.length} icon={<IconStop size={14} />} initiallyOpen={false}>
          {stopped.map(goal => row(goal))}
        </GoalGroup>}
        {unknown.length > 0 && <GoalGroup focusSignal={focusedGoal} focusOpen={unknown.some(goal => goal.goalId === expandedGoalId)} label={t('loopx.goal.status.unknown')} count={unknown.length} icon={<IconCrab size={14} />} initiallyOpen={false}>
          {unknown.map(goal => row(goal))}
        </GoalGroup>}
        {focusedGoal?.goalId && !goals.some(goal => goal.goalId === focusedGoal.goalId) && <div className="overview-goal" data-goal-id={focusedGoal.goalId}><span className="overview-goal-title">{focusedGoal.goalId}</span><p className="overview-empty" role="status">{t(loading ? 'common.loading' : 'loopx.goalGone')}</p></div>}
        {!goals.length && !focusedGoal?.goalId && <p className="overview-empty" role="status">{t(loading ? 'common.loading' : 'loopx.overview.empty')}</p>}
      </>}
    </div>
    <div className="overview-foot">
      <span className="overview-readings">{!failure && (goals.length > 0 || !loading) ? t('loopx.overview.summary', goals.filter(goal => goal.activationState === 'active').length, goals.length) : ''}</span>
      <button type="button" className="icon-btn overview-refresh" aria-label={t(failure ? 'common.retry' : 'overview.refresh')} aria-busy={loading} disabled={loading} onClick={onRetry}><IconRefresh size={14} /></button>
      {onOpenAll && canOpenBoard && <button type="button" className="overview-trace" onClick={onOpenAll}>{t('goals.conversationsBoard')}<IconChevronRight size={12} /></button>}
    </div>
  </div>;
}

function GoalGroup({ label, count, icon, waiting = false, initiallyOpen = true, focusOpen = false, focusSignal, children }: {
  label: string; count: number; icon: ReactNode; waiting?: boolean; initiallyOpen?: boolean; children: ReactNode; focusOpen?: boolean; focusSignal?: { goalId: string | null };
}) {
  const [open, setOpen] = useState(initiallyOpen || focusOpen);
  useEffect(() => { if (focusOpen) setOpen(true); }, [focusOpen, focusSignal]);
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

function GoalRow({ token, goal, attention, waiting, onChanged, threads, onOpenThread, defaultAgent, pickDirectory, onBrowseFiles, expanded, onToggle, focused, focusSignal, section }: {
  token: string | undefined; goal: LoopxGoal; attention?: LoopxAttention; waiting: boolean; onChanged: () => void;
  threads: Thread[]; onOpenThread: (threadId: string) => void; defaultAgent: string; pickDirectory?: () => Promise<string | null>; onBrowseFiles?: (threadId: string) => void; expanded: boolean; onToggle: () => void; focused?: boolean; focusSignal?: GoalRequest; section?: GoalSection | null;
}) {
  const rowRef = useRef<HTMLDivElement>(null);
  useEffect(() => { if (focused && expanded) rowRef.current?.scrollIntoView({ block: 'nearest' }); }, [focused, expanded, focusSignal]);
  const [joining, setJoining] = useState(false);
  const joiningRef = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const bound = goalConversation(goal.goalId, threads);
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
  return <div ref={rowRef} className="overview-goal" data-goal-id={goal.goalId}>
    <div className="overview-goal-heading"><span className="overview-goal-title" title={goal.title}>{goal.title}</span></div>
    {summary && <div className="overview-goal-summary" title={summary}>{summary}</div>}
    <div className="overview-goal-actions">
      {goal.activationState !== 'stopped' && <button type="button" className="overview-more" disabled={joining || (!defaultAgent && !bound)} aria-busy={joining} onClick={() => void open()}>{t('loopx.overview.openConversation')}</button>}
      <button type="button" className="overview-more" aria-expanded={expanded} onClick={onToggle}>{t(expanded ? 'loopx.panel.closeGoal' : 'loopx.panel.openGoal')}</button>
      {(goal.activationState === 'active' || goal.activationState === 'stopped') && <GoalLifecycle token={token} goal={goal} onChanged={onChanged} />}
    </div>
    {expanded && <GoalDetails token={token} goalId={goal.goalId} goal={goal} threads={threads} section={section} onBrowseFiles={onBrowseFiles ?? (() => {})} onOpenConversation={bound ? () => onOpenThread(bound.id) : undefined} />}
    {error && <p className="overview-empty overview-attention" role="alert">{error}</p>}
  </div>;
}
