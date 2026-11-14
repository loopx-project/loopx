import type { Item, PlanEntry } from '../timeline';
import { t } from '../i18n';
import { IconCheck } from './icons';
import { Popover } from './Popover';

export type PlanItem = Extract<Item, { kind: 'plan' }>;
type EntryState = 'completed' | 'active' | 'pending';

/** Once the turn is over nothing is "in progress" any more; what was not finished is simply not done. */
export const planEntryState = (entry: PlanEntry, done: boolean): EntryState =>
  entry.status === 'completed' ? 'completed' : entry.status === 'in_progress' && !done ? 'active' : 'pending';

export function planProgress(item: PlanItem): { total: number; completed: number; current?: PlanEntry } {
  const total = item.entries.length;
  const completed = item.entries.filter((e) => e.status === 'completed').length;
  const current = item.done ? undefined : item.entries.find((e) => planEntryState(e, item.done) === 'active');
  return { total, completed, current };
}

/**
 * The plan of the newest turn, if nothing has been sent since. That one lives in the top bar; plans
 * of earlier turns stay where they were made, in the conversation.
 */
export function currentPlan(items: Item[]): PlanItem | null {
  for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i];
    if (item.kind === 'plan') return item;
    if (item.kind === 'user') return null;
  }
  return null;
}

/** How much of a plan is done, as a ring filling clockwise from the top. */
export function PlanRing({ done, size = 14 }: { done: number; size?: number }) {
  const filled = Math.max(0, Math.min(1, done)) * 100;
  return (
    <svg className="plan-ring" width={size} height={size} viewBox="0 0 16 16" aria-hidden="true">
      <circle className="plan-ring-track" cx="8" cy="8" r="6" fill="none" strokeWidth={2.4} />
      {filled > 0 && (
        <circle className="plan-ring-fill" cx="8" cy="8" r="6" fill="none" strokeWidth={2.4} strokeLinecap={filled < 100 ? 'round' : 'butt'}
          pathLength={100} strokeDasharray={`${filled} 100`} transform="rotate(-90 8 8)" />
      )}
    </svg>
  );
}

export function PlanList({ item }: { item: PlanItem }) {
  return (
    <ol className="plan-list">
      {item.entries.map((entry, i) => {
        const state = planEntryState(entry, item.done);
        return (
          <li key={i} className={`plan-entry ${state}`}>
            <span className="plan-mark" aria-hidden="true">
              {state === 'completed' && <IconCheck size={12} />}
            </span>
            <span className="plan-text">{entry.content}</span>
            <span className="sr-only">{state === 'completed' ? t('view.entryDone') : state === 'active' ? t('view.entryActive') : t('view.entryPending')}</span>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * The current plan as one top-bar button: the ring and the count say how far along it is at a
 * glance (a ring alone reads as a spinner), and the steps open in a panel below.
 */
export function PlanButton({ item }: { item: PlanItem }) {
  const { total, completed } = planProgress(item);
  const label = t('view.planProgress', completed, total);
  return (
    <Popover
      label={label}
      placement="below"
      align="end"
      triggerClassName="icon-btn topbar-plan"
      triggerLabel={label}
      triggerTitle={label}
      panelClassName="plan-panel"
      trigger={<><PlanRing done={total ? completed / total : 0} size={16} /><span className="plan-count">{completed}/{total}</span></>}
    >
      {() => (
        <>
          <div className="plan-panel-head">{label}</div>
          <PlanList item={item} />
        </>
      )}
    </Popover>
  );
}
