import { useState, type ReactNode } from 'react';
import type { Thread } from '../api';
import type { LoopxGoal } from '../loopx/status';
import { t } from '../i18n';
import { GoalTasks } from './GoalTasks';
import { GoalFiles } from './GoalFiles';
import { GoalAcceptance } from './GoalAcceptance';
import { IconChevron } from './icons';

/** Heavy readers mount only after their section is opened, and stop reading when it closes. */
function GoalDetailSection({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return <section className="goal-detail-section">
    <button type="button" className="goal-detail-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
      <IconChevron size={12} open={open} /><span>{label}</span>
    </button>
    {open && <div className="goal-detail-body">{children}</div>}
  </section>;
}

/** Shared in-place Goal details for the steward and a bound conversation. */
export function GoalDetails({ token, goalId, goal, threads, refreshKey = 0, onBrowseFiles, onOpenConversation }: {
  token?: string; goalId: string; goal?: LoopxGoal; threads: Thread[]; refreshKey?: number;
  onBrowseFiles: (threadId: string) => void; onOpenConversation: () => void;
}) {
  return <div className="goal-details" data-goal-detail={goalId}>
    {goal?.objective && <p className="goal-detail-objective">{goal.objective}</p>}
    <GoalDetailSection label={t('loopx.tabTasks')}><GoalTasks token={token} goalId={goalId} refreshKey={refreshKey} /></GoalDetailSection>
    <GoalDetailSection label={t('loopx.tabFiles')}><GoalFiles token={token} goalId={goalId} threads={threads} onBrowse={onBrowseFiles} onOpenConversations={onOpenConversation} /></GoalDetailSection>
    <GoalDetailSection label={t('loopx.accept.title')}>{goal ? <GoalAcceptance token={token} goal={goal} /> : <p className="goal-empty" role="status">{t('loopx.goal.stateUnavailable')}</p>}</GoalDetailSection>
  </div>;
}
