import { useCallback, useEffect, useState } from 'react';
import type { Api, PersonalPlan, PlanRange, PlanTodo } from '../api';
import { PLAN_RANGE_KEY, PLAN_RANGES, PLAN_SHOWN, planIsEmpty, planWhenOptions, readPlanRange } from '../plan';
import { formatDateTime, t } from '../i18n';

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

interface Props {
  api: Api;
  refreshKey: number;
  onOpenTodo: (id: string) => void;
  onOpenThread: (id: string) => void;
}

/** One section's rows: five, then the rest one tap away. */
function Rows<T>({ items, row }: { items: readonly T[]; row: (item: T) => React.ReactNode }) {
  const [all, setAll] = useState(false);
  const shown = all ? items : items.slice(0, PLAN_SHOWN);
  return <>
    {shown.map(row)}
    {items.length > shown.length && <li><button className="plan-more" onClick={() => setAll(true)}>{t('horizon.more', items.length - shown.length)}</button></li>}
  </>;
}

/**
 * What lies ahead in hours, today, this week or this month. Each range keeps its last reading while a
 * newer one loads, so switching back and refreshing never empties the panel; unknown is never zero.
 */
export function PlanView({ api, refreshKey, onOpenTodo, onOpenThread }: Props) {
  const [range, setRange] = useState<PlanRange>(() => readPlanRange(localStorage.getItem(PLAN_RANGE_KEY)));
  const [plans, setPlans] = useState<Partial<Record<PlanRange, PersonalPlan>>>({});
  const [errors, setErrors] = useState<Partial<Record<PlanRange, string>>>({});
  const [busy, setBusy] = useState(false);
  const load = useCallback((which: PlanRange, signal?: AbortSignal) => {
    if (api.demo) return;
    setBusy(true);
    void api.personalPlan(which, signal)
      .then(plan => { setPlans(now => ({ ...now, [which]: plan })); setErrors(({ [which]: _gone, ...rest }) => rest); })
      .catch(e => { if (!signal?.aborted) setErrors(now => ({ ...now, [which]: message(e) })); })
      .finally(() => { if (!signal?.aborted) setBusy(false); });
  }, [api]);
  useEffect(() => {
    const ctrl = new AbortController();
    load(range, ctrl.signal);
    return () => ctrl.abort();
  }, [load, range, refreshKey]);

  const pick = (next: PlanRange) => { setRange(next); localStorage.setItem(PLAN_RANGE_KEY, next); };
  const plan = plans[range];
  const error = errors[range];
  const when = (at: string) => formatDateTime(at, planWhenOptions(range));
  const todoRow = (todo: PlanTodo) => <li key={todo.id}><button onClick={() => onOpenTodo(todo.id)}>
    <span className="plan-text">{todo.text}</span>
    <span className="plan-meta">
      {todo.horizon && <span>{t(`horizon.goal.${todo.horizon}`)}</span>}
      {todo.stage === 'answer' && <span className="warn">{t('proactive.status.asking')}</span>}
      {todo.dueAt && <span>{when(todo.dueAt)}</span>}
    </span>
  </button></li>;

  return <section className="plan-view" aria-busy={busy}>
    <div className="plan-ranges" role="group" aria-label={t('horizon.label')}>
      {PLAN_RANGES.map(item => <button key={item} aria-pressed={range === item} onClick={() => pick(item)}>{t(`horizon.range.${item}`)}</button>)}
    </div>
    {error && <p className="plan-state warn" role="alert">{error} <button className="ghost" onClick={() => load(range)}>{t('common.retry')}</button></p>}
    {!plan && !error && <ul className="plan-list plan-skeleton" aria-label={t('common.loading')}>{[0, 1, 2].map(i => <li key={i}><span /></li>)}</ul>}
    {plan && (planIsEmpty(plan) ? <p className="plan-state">{t('horizon.empty')}</p> : <>
      {(plan.meetings.state !== 'ok' || plan.meetings.items.length > 0) && <div className="plan-group">
        <h4>{t('horizon.meetings')}</h4>
        {plan.meetings.state === 'off' && <p className="plan-state">{t('horizon.calendarOff')}</p>}
        {plan.meetings.state === 'unavailable' && <p className="plan-state warn">{t('horizon.calendarFailed', plan.meetings.error ?? '')}</p>}
        {plan.meetings.items.length > 0 && <ul className="plan-list"><Rows items={plan.meetings.items} row={meeting => <li key={`${meeting.id}@${meeting.start}`}>
          {meeting.url
            ? <a href={meeting.url} target="_blank" rel="noreferrer"><span className="plan-text">{meeting.title}</span><span className="plan-meta"><span>{when(meeting.start)}</span></span></a>
            : <div className="plan-row"><span className="plan-text">{meeting.title}</span><span className="plan-meta"><span>{when(meeting.start)}</span></span></div>}
        </li>} /></ul>}
      </div>}
      {plan.needsYou.length > 0 && <div className="plan-group">
        <h4 className="warn">{t('horizon.needsYou')}</h4>
        <ul className="plan-list"><Rows items={plan.needsYou} row={item => <li key={item.id}><button onClick={() => onOpenThread(item.id)}>
          <span className="plan-text">{item.title}</span>
          {item.line && <span className="plan-meta"><span>{item.line}</span></span>}
        </button></li>} /></ul>
      </div>}
      {plan.urgent.length > 0 && <div className="plan-group"><h4 className="warn">{t('horizon.urgent')}</h4><ul className="plan-list"><Rows items={plan.urgent} row={todoRow} /></ul></div>}
      {plan.due.length > 0 && <div className="plan-group"><h4>{t('horizon.due')}</h4><ul className="plan-list"><Rows items={plan.due} row={todoRow} /></ul></div>}
      {plan.goals.length > 0 && <div className="plan-group"><h4>{t('horizon.goals')}</h4><ul className="plan-list"><Rows items={plan.goals} row={todoRow} /></ul></div>}
    </>)}
  </section>;
}
