import type { PersonalPlan, PlanRange } from './api';

export const PLAN_RANGES: readonly PlanRange[] = ['hour', 'day', 'week', 'month'];
/** Rows a section shows before "N more". */
export const PLAN_SHOWN = 5;
export const PLAN_RANGE_KEY = 'calli.planRange';

/** The range last picked on this device; anything unknown reads as today. */
export const readPlanRange = (stored: string | null | undefined): PlanRange =>
  PLAN_RANGES.includes(stored as PlanRange) ? stored as PlanRange : 'day';

/**
 * Nothing in the range, known for certain: every list empty and the calendar read and empty.
 * A calendar that is off or failed is not "nothing planned", so it never counts as empty.
 */
export const planIsEmpty = (plan: PersonalPlan): boolean =>
  plan.meetings.state === 'ok' && !plan.meetings.items.length
  && !plan.urgent.length && !plan.due.length && !plan.goals.length && !plan.needsYou.length;

/** Times inside one day read as clock times; a week or a month needs the day too. */
export const planWhenOptions = (range: PlanRange): Intl.DateTimeFormatOptions =>
  range === 'hour' || range === 'day' ? { hour: '2-digit', minute: '2-digit', hour12: false } : { month: 'numeric', day: 'numeric', weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false };
