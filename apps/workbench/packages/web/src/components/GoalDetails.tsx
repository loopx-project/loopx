import { useEffect, useState, type ReactNode } from 'react';
import type { Thread } from '../api';
import { failureLabel, loopxFailureOf, type LoopxGoal } from '../loopx/status';
import { fetchGoalDetail, type LoopxGoalDetail } from '../loopx/goal-detail';
import { goalConversation, type GoalSection } from '../loopx/goal-navigation';
import { statusWord } from '../loopx/launch';
import { t } from '../i18n';
import { GoalTasks } from './GoalTasks';
import { GoalFiles } from './GoalFiles';
import { GoalAcceptance } from './GoalAcceptance';
import { IconChevron } from './icons';

/** Heavy readers mount only after their section is opened, and stop reading when it closes. */
function GoalDetailSection({ label, children, initiallyOpen = false }: { label: string; children: ReactNode; initiallyOpen?: boolean }) {
  const [open, setOpen] = useState(initiallyOpen);
  return <section className="goal-detail-section">
    <button type="button" className="goal-detail-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
      <IconChevron size={12} open={open} /><span>{label}</span>
    </button>
    {open && <div className="goal-detail-body">{children}</div>}
  </section>;
}

/** The scoped overview is read only after its disclosure opens. */
function GoalOverviewRead({ token, goalId, goal }: { token?: string; goalId: string; goal?: LoopxGoal }) {
  const [read, setRead] = useState<{ detail: LoopxGoalDetail | null; error: string | null }>({ detail: null, error: null });
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    setRead({ detail: null, error: null });
    void fetchGoalDetail(token, goalId, { signal: controller.signal }).then(
      detail => { if (active) setRead({ detail, error: null }); },
      cause => { if (active && !controller.signal.aborted) setRead({ detail: null, error: failureLabel(loopxFailureOf(cause)) }); },
    );
    return () => { active = false; controller.abort(); };
  }, [token, goalId, revision]);
  if (read.error) return <p className="goal-failure" role="alert">{read.error} <button type="button" className="ghost" onClick={() => setRevision(value => value + 1)}>{t('common.retry')}</button></p>;
  if (!read.detail) return <p className="goal-empty" role="status">{t('common.loading')}</p>;
  const detail = read.detail;
  return <div className="goal-detail-overview">
    <p>{statusWord(detail.status ?? goal?.status ?? 'unknown')}</p>
    {goal?.objective && <p className="goal-detail-objective">{goal.objective}</p>}
    <p>{t('loopx.overview.todoProgress', detail.userTodos.done + detail.agentTodos.done, detail.userTodos.total + detail.agentTodos.total)}</p>
    {detail.recommendedAction && <p>{detail.recommendedAction}</p>}
  </div>;
}

/** Shared in-place Goal details; an unbound Core Goal is a read-only reader. */
export function GoalDetails({ token, goalId, goal, threads, refreshKey = 0, onBrowseFiles, onOpenConversation, section = null }: {
  token?: string; goalId: string; goal?: LoopxGoal; threads: Thread[]; refreshKey?: number;
  onBrowseFiles: (threadId: string) => void; onOpenConversation?: () => void;
  /** The section a link named, open from the start. */
  section?: GoalSection | null;
}) {
  const bound = goalConversation(goalId, threads);
  return <div className="goal-details" data-goal-detail={goalId}>
    <GoalDetailSection label={t('loopx.tabOverview')} initiallyOpen={section === 'overview'}><GoalOverviewRead token={token} goalId={goalId} goal={goal} /></GoalDetailSection>
    <GoalDetailSection label={t('loopx.tabTasks')} initiallyOpen={section === 'tasks'}><GoalTasks token={token} goalId={goalId} refreshKey={refreshKey} /></GoalDetailSection>
    <GoalDetailSection label={t('loopx.tabFiles')} initiallyOpen={section === 'files'}><GoalFiles token={token} goalId={goalId} threads={threads} onBrowse={onBrowseFiles} onOpenConversations={bound ? onOpenConversation : undefined} /></GoalDetailSection>
    <GoalDetailSection label={t('loopx.accept.title')} initiallyOpen={section === 'acceptance'}>{goal ? <GoalAcceptance token={token} goal={goal} readOnly={!bound} /> : <p className="goal-empty" role="status">{t('loopx.goal.stateUnavailable')}</p>}</GoalDetailSection>
  </div>;
}
