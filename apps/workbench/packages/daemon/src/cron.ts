/**
 * Standard five-field cron expressions (minute hour day-of-month month day-of-week).
 * Supports `*`, lists, ranges and steps, the JAN–DEC / SUN–SAT names, and the usual
 * vixie rule that a day restricted in both fields matches on either one.
 * When a timezone is given (e.g. "Asia/Shanghai"), times are matched in that IANA timezone;
 * otherwise the daemon's local timezone is used.
 */

export interface CronFields {
  minutes: number[];
  hours: number[];
  daysOfMonth: number[];
  months: number[];
  /** 0–6, 0 = Sunday. */
  daysOfWeek: number[];
  /** Whether the day-of-month field was left `*` (so only the week decides). */
  domAny: boolean;
  /** Whether the day-of-week field was left `*`. */
  dowAny: boolean;
}

export interface ZonedParts {
  year: number;
  month: number; // 1-12
  date: number; // 1-31
  day: number; // 0-6, 0 = Sunday
  hours: number; // 0-23
  minutes: number; // 0-59
}

const MONTH_NAMES = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];
const DAY_NAMES = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];
const DOW_SHORT: Record<string, number> = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };

export function isValidTimezone(tz: string): boolean {
  if (!tz || typeof tz !== 'string') return false;
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

export function zonedParts(at: Date, tz?: string | null): ZonedParts {
  if (!tz) {
    return {
      year: at.getFullYear(),
      month: at.getMonth() + 1,
      date: at.getDate(),
      day: at.getDay(),
      hours: at.getHours(),
      minutes: at.getMinutes(),
    };
  }
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone: tz,
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    weekday: 'short',
    hour: 'numeric',
    minute: 'numeric',
    hourCycle: 'h23',
  });
  const parts = fmt.formatToParts(at);
  let year = 0;
  let month = 0;
  let date = 0;
  let day = 0;
  let hours = 0;
  let minutes = 0;
  for (const p of parts) {
    if (p.type === 'year') year = Number(p.value);
    else if (p.type === 'month') month = Number(p.value);
    else if (p.type === 'day') date = Number(p.value);
    else if (p.type === 'weekday') day = DOW_SHORT[p.value] ?? 0;
    else if (p.type === 'hour') hours = Number(p.value);
    else if (p.type === 'minute') minutes = Number(p.value);
  }
  return { year, month, date, day, hours, minutes };
}

/** A word or a number, with the field's names accepted for the word. */
function fieldValue(token: string, what: string, names: string[] | null): number {
  const upper = token.toUpperCase();
  const byName = names?.indexOf(upper) ?? -1;
  if (byName >= 0) return byName + (names === MONTH_NAMES ? 1 : 0);
  if (!/^\d+$/.test(token)) throw new Error(`${what} is not a number: ${token}`);
  return Number(token);
}

function parseField(raw: string, what: string, min: number, max: number, names: string[] | null): { values: Set<number>; any: boolean } {
  const out = new Set<number>();
  for (const part of raw.split(',')) {
    if (!part) throw new Error(`${what} has an empty value`);
    const [range, stepRaw] = part.split('/');
    if (stepRaw !== undefined && !/^\d+$/.test(stepRaw)) throw new Error(`${what} step is not a number: ${stepRaw}`);
    const step = stepRaw === undefined ? 1 : Number(stepRaw);
    if (step < 1) throw new Error(`${what} step must be at least 1`);
    let lo: number;
    let hi: number;
    if (range === '*' || range === '') {
      lo = min;
      hi = max;
    } else if (range.includes('-')) {
      const pieces = range.split('-');
      if (pieces.length !== 2) throw new Error(`${what} range is malformed: ${range}`);
      lo = fieldValue(pieces[0], what, names);
      hi = fieldValue(pieces[1], what, names);
    } else {
      lo = hi = fieldValue(range, what, names);
    }
    for (let v = lo; v <= hi; v += step) {
      if (v < min || v > max) throw new Error(`${what} is out of range: ${v}`);
      out.add(v);
    }
  }
  if (out.size === 0) throw new Error(`${what} matches nothing`);
  return { values: out, any: raw === '*' };
}

/** Parses an expression, throwing an error a person can read when it is malformed. */
export function parseCron(expr: string): CronFields {
  const fields = expr.trim().split(/\s+/);
  if (fields.length !== 5) throw new Error('cron takes five fields: minute hour day-of-month month day-of-week');
  const minute = parseField(fields[0], 'minute', 0, 59, null);
  const hour = parseField(fields[1], 'hour', 0, 23, null);
  const dom = parseField(fields[2], 'day of month', 1, 31, null);
  const month = parseField(fields[3], 'month', 1, 12, MONTH_NAMES);
  const dowRaw = parseField(fields[4], 'day of week', 0, 7, DAY_NAMES);
  if (dowRaw.values.has(7)) {
    dowRaw.values.delete(7);
    dowRaw.values.add(0);
  }
  const sorted = (s: Set<number>) => [...s].sort((a, b) => a - b);
  return {
    minutes: sorted(minute.values),
    hours: sorted(hour.values),
    daysOfMonth: sorted(dom.values),
    months: sorted(month.values),
    daysOfWeek: sorted(dowRaw.values),
    domAny: dom.any,
    dowAny: dowRaw.any,
  };
}

function dayMatchesParts(f: CronFields, p: ZonedParts): boolean {
  if (!f.months.includes(p.month)) return false;
  const domOk = f.daysOfMonth.includes(p.date);
  const dowOk = f.daysOfWeek.includes(p.day);
  if (f.domAny && f.dowAny) return true;
  if (f.domAny) return dowOk;
  if (f.dowAny) return domOk;
  return domOk || dowOk;
}

/** Whether the minute that begins at `at` (seconds are ignored) matches. */
export function cronMatches(f: CronFields, at: Date, tz?: string | null): boolean {
  const p = zonedParts(at, tz);
  if (!f.hours.includes(p.hours)) return false;
  if (!f.minutes.includes(p.minutes)) return false;
  return dayMatchesParts(f, p);
}

/** The first matching minute strictly after `after`, or null when nothing matches for years. */
export function nextCron(f: CronFields, after: Date, tz?: string | null): Date | null {
  const t = new Date(after.getTime());
  t.setSeconds(0, 0);
  t.setTime(t.getTime() + 60_000);

  // Search up to 2 years ahead.
  for (let dayCount = 0; dayCount < 366 * 2; dayCount++) {
    const p = zonedParts(t, tz);
    if (!dayMatchesParts(f, p)) {
      // Advance to the start of the next day in the target timezone.
      const minsToNextDay = (23 - p.hours) * 60 + (60 - p.minutes);
      t.setTime(t.getTime() + minsToNextDay * 60_000);
      continue;
    }

    // Day matches, find matching hour and minute today.
    let found = false;
    for (const h of f.hours) {
      if (h < p.hours) continue;
      for (const m of f.minutes) {
        if (h === p.hours && m < p.minutes) continue;
        const diffMins = (h - p.hours) * 60 + (m - p.minutes);
        t.setTime(t.getTime() + diffMins * 60_000);
        // Verify timezone parts in case of DST jump.
        const verified = zonedParts(t, tz);
        if (verified.hours === h && verified.minutes === m && dayMatchesParts(f, verified)) {
          return t;
        }
        // If DST distorted the time, advance minute by minute until match.
        while (!cronMatches(f, t, tz)) {
          t.setTime(t.getTime() + 60_000);
          if (t.getTime() - after.getTime() > 366 * 2 * 86_400_000) return null;
        }
        return t;
      }
    }

    if (!found) {
      // No more matching hours today; advance to next day.
      const minsToNextDay = (23 - p.hours) * 60 + (60 - p.minutes);
      t.setTime(t.getTime() + minsToNextDay * 60_000);
    }
  }
  return null;
}

/**
 * How many times the expression fires in (`from`, `to`), for saying what was missed while the
 * daemon was down. -1 when the window is too wide to count (a year and more): "many".
 */
export function countCronFires(f: CronFields, from: Date, to: Date, tz?: string | null): number {
  if (to.getTime() - from.getTime() > 366 * 86_400_000) return -1;
  let count = 0;
  let cur = from;
  while (cur.getTime() < to.getTime()) {
    const next = nextCron(f, cur, tz);
    if (!next || next.getTime() >= to.getTime()) break;
    count++;
    cur = next;
    if (count > 999) return 999;
  }
  return count;
}

const pad = (n: number) => String(n).padStart(2, '0');
const WEEK_LABELS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

function timeList(f: CronFields): string {
  return f.hours.map((h) => `${pad(h)}:${pad(f.minutes[0])}`).join('、');
}

/**
 * A person-readable summary of the common shapes the page's presets generate. Empty for anything
 * else; the caller shows the raw expression then.
 */
export function describeCron(f: CronFields): string {
  const daily = f.domAny && f.dowAny && f.months.length === 12;
  const sameMinute = f.minutes.length === 1;
  if (f.minutes.length === 60 && f.hours.length === 24) return '每分钟';
  if (f.hours.length === 24 && f.minutes.length > 1) {
    const step = f.minutes[1] - f.minutes[0];
    if (f.minutes[0] === 0 && f.minutes.every((m, i) => i === 0 || m - f.minutes[i - 1] === step) && 60 % step === 0) {
      return `每 ${step} 分钟`;
    }
    return `每小时 ${f.minutes.map((m) => `${m}`).join('、')} 分`;
  }
  if (f.hours.length === 24 && sameMinute) return `每小时 ${f.minutes[0]} 分`;
  if (daily && sameMinute && f.hours.length === 1) return `每天 ${pad(f.hours[0])}:${pad(f.minutes[0])}`;
  if (daily && sameMinute) return `每天 ${timeList(f)}`;
  if (sameMinute && f.hours.length === 1) {
    const at = `${pad(f.hours[0])}:${pad(f.minutes[0])}`;
    if (f.domAny && f.months.length === 12) {
      if (f.daysOfWeek.length === 5 && [1, 2, 3, 4, 5].every((d, i) => f.daysOfWeek[i] === d)) return `每工作日 ${at}`;
      if (f.daysOfWeek.length === 1) return `每${WEEK_LABELS[f.daysOfWeek[0]]} ${at}`;
      if (f.daysOfWeek.length === 7) return `每天 ${at}`;
      return `每${f.daysOfWeek.map((d) => WEEK_LABELS[d]).join('、')} ${at}`;
    }
    if (f.dowAny && f.months.length === 12 && f.daysOfMonth.length === 1) return `每月 ${f.daysOfMonth[0]} 日 ${at}`;
    if (f.domAny && f.dowAny && f.months.length === 1) return `每年 ${f.months[0]} 月 ${at}`;
  }
  return '';
}
