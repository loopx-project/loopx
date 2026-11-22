import { useEffect, useRef, useState } from 'react';
import { fetchGoalBar, goalOf, statusWord, type GoalBarRead } from '../loopx/launch';
import type { LoopxAttention, LoopxGoal } from '../loopx/status';
import type { Thread } from '../api';
import { t } from '../i18n';
import { GoalLifecycle } from './GoalLifecycle';
import { IconCrab } from './icons';

/** The current Goal stays compact and can be managed without leaving the conversation. */
export function LoopxGoalGroup({ token, thread, goal, attention, onChanged, onOpenGoal }: {
  token: string | undefined; thread: Thread; goal?: LoopxGoal; attention?: LoopxAttention;
  onChanged: () => void; onOpenGoal: (goalId: string) => void;
}) {
  const info = thread.loopxInfo;
  const bound = Boolean(goalOf(thread));
  const [read, setRead] = useState<GoalBarRead | null>(null);
  const [revision, setRevision] = useState(0);
  // A scoped effect rejects late reads from a previous conversation or lifecycle refresh.
  useEffect(() => {
    setRead(null);
    if (!info || info.missing || !bound) return;
    let active = true;
    const load = () => { void fetchGoalBar(token, thread.id, revision > 0).then(
      next => { if (active) setRead(next); },
      () => { if (active) setRead({ binding: goalOf(thread)!, state: null, freshness: 'unavailable', observedAt: '' }); },
    ); };
    load();
    const timer = setInterval(load, 20000);
    return () => { active = false; clearInterval(timer); };
  }, [thread.id, token, info?.goalId, info?.missing, bound, revision]);
  const wasRunning = useRef({ id: thread.id, running: thread.status === 'running' });
  useEffect(() => {
    if (wasRunning.current.id === thread.id && wasRunning.current.running && thread.status !== 'running' && bound && !info?.missing) setRevision(value => value + 1);
    wasRunning.current = { id: thread.id, running: thread.status === 'running' };
  }, [thread.id, thread.status, bound, info?.missing]);
  if (!info?.goalId) return null;

  const state = read?.state ?? null;
  const title = goal?.title ?? (state && state.title !== info.goalId ? state.title : info.goalTitle);
  const total = state ? state.agentTodos.total + state.userTodos.total : attention ? attention.agentTodos.total + attention.userTodos.total : null;
  const done = state ? state.agentTodos.total + state.userTodos.total - state.agentTodos.open - state.userTodos.open : attention ? attention.agentTodos.done + attention.userTodos.done : null;
  const summary = state?.summary ?? attention?.topTodo?.text ?? attention?.nextAction;
  const changed = () => { setRevision(value => value + 1); onChanged(); };
  return <section className="overview-group open loopx-group" aria-label={t('loopx.panel.title')}>
    <div className="overview-head">
      <div className="overview-toggle loopx-group-head">
        <span className="overview-head-icon"><IconCrab size={14} /></span>
        <strong className={`loopx-group-name${info.missing ? ' gone' : ''}`} title={title}>{title}</strong>
        <span className="overview-summary">{info.missing ? t('sidebar.loopxDeleted') : goal?.activationState === 'stopped' ? t('loopx.stoppedShort') : state || goal ? statusWord(state?.status ?? goal!.status) : ''}</span>
      </div>
    </div>
    {!info.missing && <div className="overview-body loopx-group-body">
      {summary && <div className="overview-goal-summary" title={summary}>{summary}</div>}
      {total !== null && done !== null && <div className="loopx-group-line">{t('loopx.overview.todoProgress', done, total)}</div>}
      {bound && !read && !goal && <p className="loopx-group-note" role="status">{t('loopx.goal.reading')}</p>}
      {read?.freshness === 'unavailable' && <p className="loopx-group-note" role="status">{t('loopx.goal.stateUnavailable')}</p>}
      <div className="overview-goal-actions">
        {goal && (goal.activationState === 'active' || goal.activationState === 'stopped') && <GoalLifecycle token={token} goal={goal} onChanged={changed} />}
        <button type="button" className="overview-more loopx-group-open" onClick={() => onOpenGoal(info.goalId)}>{t('loopx.panel.openGoal')}</button>
      </div>
    </div>}
  </section>;
}
