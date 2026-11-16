import { useEffect, useMemo, useState } from 'react';
import { t } from '../i18n';
import { IconChevron, IconPin } from './icons';
import {
  fetchLoopxStatus, loopxFailureOf, needsYouGoalIds, sortGoalTree,
  type LoopxAttention, type LoopxGoal, type LoopxStatus, type LoopxStatusFailure,
} from '../loopx/status';
import { sidebarGoals, useGoalPins } from '../loopx/goal-picks';

/** One shared status read for the sidebar, the badge and the pages that only need the list. */
export function useLoopxStatus(token: string | undefined, refreshMs = 15000) {
  const [read, setRead] = useState<{ loading: boolean; status: LoopxStatus | null; failure: LoopxStatusFailure | null; at: number }>(
    { loading: true, status: null, failure: null, at: 0 },
  );
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const readOnce = async (signal: AbortSignal) => {
      try {
        const status = await fetchLoopxStatus({ token, signal });
        if (active) setRead({ loading: false, status, failure: null, at: Date.now() });
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') return;
        if (active) setRead({ loading: false, status: null, failure: loopxFailureOf(error), at: Date.now() });
      } finally {
        if (active) timer = setTimeout(() => void readOnce(new AbortController().signal), refreshMs);
      }
    };
    const controller = new AbortController();
    void readOnce(controller.signal);
    return () => { active = false; clearTimeout(timer); controller.abort(); };
  }, [token, refreshMs, nonce]);
  return { ...read, reload: () => setNonce(value => value + 1) };
}

/**
 * The sidebar's Goals group: five at most — the pinned ones in front, the rest by 「需要你 → 最近
 * moved lately"), with the needs-you count on a Goal and the pin on a choice of this device. Everything
 * else is behind the all-goals entry.
 */
export function SidebarGoals({ goals, attention, activeGoalId, onOpenGoal, onOpenGoalTasks, onOpenAllGoals }: {
  goals: LoopxGoal[];
  attention: LoopxAttention[];
  activeGoalId: string | null;
  onOpenGoal: (goalId: string) => void;
  onOpenGoalTasks?: (goalId: string) => void;
  onOpenAllGoals?: () => void;
}) {
  const [open, setOpen] = useState(true);
  const [pins] = useGoalPins();
  const needsYou = useMemo(() => needsYouGoalIds(attention), [attention]);
  const picked = useMemo(() => sidebarGoals(goals, attention, pins), [goals, attention, pins]);
  const total = sortGoalTree(goals).length;
  if (!goals.length) return null;
  const openTodos = (goalId: string) => attention.find(item => item.goalId === goalId)?.userTodos.open ?? 0;
  const row = (goal: LoopxGoal) => (
    <div className="sidebar-goal-row" key={goal.goalId}>
      <button
        type="button"
        className={`sidebar-goal${activeGoalId === goal.goalId ? ' active' : ''}`}
        aria-current={activeGoalId === goal.goalId ? 'page' : undefined}
        style={{ ['--depth' as string]: String(depthOf(goal, goals)) }}
        onClick={() => onOpenGoal(goal.goalId)}
      >
        <span className={`sidebar-goal-dot is-${goal.activationState}`} aria-hidden="true" />
        <span className="sidebar-goal-title">{goal.title}</span>
        {needsYou.has(goal.goalId) && openTodos(goal.goalId) > 0
          ? <span className="sidebar-goal-badge">{openTodos(goal.goalId)}</span>
          : null}
        {pins.includes(goal.goalId) ? <span className="sidebar-goal-pin" aria-hidden="true"><IconPin size={12} /></span> : null}
      </button>
      {onOpenGoalTasks ? <button type="button" className="sidebar-goal-tasks" title={t('loopx.tasks.openBoard')} aria-label={`${t('loopx.tasks.openBoard')}: ${goal.title}`} onClick={() => onOpenGoalTasks(goal.goalId)}>
        <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><rect x="2" y="3" width="3.2" height="10" rx="1" fill="none" stroke="currentColor" strokeWidth="1.3" /><rect x="6.4" y="3" width="3.2" height="6" rx="1" fill="none" stroke="currentColor" strokeWidth="1.3" /><rect x="10.8" y="3" width="3.2" height="8" rx="1" fill="none" stroke="currentColor" strokeWidth="1.3" /></svg>
      </button> : null}
    </div>
  );
  return <div className="sidebar-goals" role="group" aria-label={t('loopx.goalsGroup')}>
    <button type="button" className="sidebar-goals-head" aria-expanded={open} onClick={() => setOpen(value => !value)}>
      <span className="sidebar-goal-title">{t('loopx.goalsGroup')}</span>
      <IconChevron size={14} open={open} />
    </button>
    {open ? picked.map(row) : null}
    {open && onOpenAllGoals ? <button type="button" className="sidebar-goals-all" onClick={onOpenAllGoals}>
      <span className="sidebar-goal-title">{t('goals.allGoals')}</span>
      <span className="sidebar-goals-all-count">{total} →</span>
    </button> : null}
  </div>;
}

function depthOf(goal: LoopxGoal, goals: LoopxGoal[]): number {
  let depth = 0;
  let current = goal;
  while (current.parentId && depth < 8) {
    const parent = goals.find(g => g.goalId === current.parentId);
    if (!parent) break;
    current = parent;
    depth += 1;
  }
  return depth;
}

export { needsYouGoalIds };
