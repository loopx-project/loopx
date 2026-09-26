/** Local, lossy duration observations; never a Goal lifecycle or billing authority. */
import { readFile, chmod } from "node:fs/promises";
import { atomicWriteJson } from "../effect_runtime_io.ts";
import type { JsonObject } from "../effect_program.ts";

import { object } from "./usage_statistics_contract.ts";
import { GOAL_SCHEMA, HOSTS, MEASUREMENTS, goalDuration, validGoalObservation } from "./usage_statistics_goal_contract.ts";
import type { GoalAggregate, GoalObservation, GoalCount, Measurement, Host } from "./usage_statistics_goal_contract.ts";
type Interval = [number, number];
type MeasuredGoal = { key: string; measurement: Measurement; host: Host; first: number; last: number; intervals: Interval[]; total: number; watermark: number; day: string; reported?: string };
type GoalState = { generation: string; goals: MeasuredGoal[] };
const DAY = 86400000;
const MAX_GOALS = 64;
const MAX_INTERVALS = 512;
/** Interval union is idempotent, order-independent and excludes inter-Turn idle time. */
export function union(intervals: Interval[], next: Interval): Interval[] {
  const result: Interval[] = [];
  for (const [start, end] of [...intervals, next].sort((a, b) => a[0] - b[0])) {
    const tail = result.at(-1);
    if (tail && start <= tail[1]) tail[1] = Math.max(tail[1], end);
    else result.push([start, end]);
  }
  return result;
}
async function load(path: string, generation: string): Promise<GoalState> {
  try {
    const text = await readFile(path, "utf8");
    if (text.length > 4 * 1024 * 1024) throw new Error("goal_usage_too_large");
    const value = JSON.parse(text) as GoalState;
    if (value.generation !== generation) return { generation, goals: [] };
    if (!Array.isArray(value.goals) || value.goals.length > MAX_GOALS || value.goals.some(g => !object(g)
      || !MEASUREMENTS.includes(g.measurement) || !HOSTS.includes(g.host)
      || typeof g.key !== "string" || !/^[a-f0-9]{64}$/.test(g.key)
      || ![g.first, g.last, g.total, g.watermark].every(n => Number.isSafeInteger(n) && n >= 0)
      || g.first > g.last || typeof g.day !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(g.day)
      || !Array.isArray(g.intervals) || g.intervals.length > MAX_INTERVALS
      || g.intervals.some((v, i) => !Array.isArray(v) || v.length !== 2 || !v.every(Number.isSafeInteger)
        || v[0] < g.first || v[1] > g.last || v[0] > v[1] || (i > 0 && v[0] <= g.intervals[i - 1][1])))) throw new Error("goal_usage_invalid");
    return value;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return { generation, goals: [] };
    throw error;
  }
}
function snapshot(goals: MeasuredGoal[]): GoalAggregate | null {
  const rows = new Map<string, GoalCount>();
  for (const goal of goals) {
    const span = goalDuration(goal.last - goal.first);
    const duration = goalDuration(goal.total + goal.intervals.reduce((sum, [a, b]) => sum + b - a, 0));
    const key = `${goal.measurement}:${goal.host}:${span}:${duration}`;
    const row = rows.get(key) ?? { measurement: goal.measurement, host: goal.host, span, duration, count: 0 };
    row.count++; rows.set(key, row);
  }
  return rows.size ? { schema: GOAL_SCHEMA, counters: [...rows.values()] } : null;
}
export async function goalPreview(path: string, generation: string): Promise<GoalAggregate | null> {
  return snapshot((await load(path, generation)).goals.filter(g => g.reported !== g.day));
}
/** Caller holds the common consent lock. Claims before sending: no retry identifiers. */
export async function recordGoalUsage(path: string, generation: string, now: number, observation?: GoalObservation | GoalObservation[]): Promise<GoalAggregate | null> {
  const state = await load(path, generation);
  const today = new Date(now).toISOString().slice(0, 10);
  // A quiet Goal is not reported again each day. Keep observed cumulative time
  // for up to 90 days of inactivity, then restart measurement if it returns.
  state.goals = state.goals.filter(g => now - g.last < 90 * DAY);
  const observations = (Array.isArray(observation) ? observation : observation ? [observation] : []).filter(item => validGoalObservation(item, now));
  function apply(items: GoalObservation[]) {
    for (const observation of items) {
      const observedDay = new Date(observation.end).toISOString().slice(0, 10);
      let goal = state.goals.find(g => g.key === observation.key && g.measurement === observation.measurement && g.host === observation.host);
      if (!goal && state.goals.length < MAX_GOALS) {
        goal = { key: observation.key, measurement: observation.measurement, host: observation.host, first: observation.start, last: observation.end, intervals: [], total: 0, watermark: 0, day: observedDay };
        state.goals.push(goal);
      }
      if (goal && observation.start >= goal.watermark && goal.day <= observedDay && goal.reported !== observedDay) {
        // Compact only intervals older than the accepted late-observation window.
        const boundary = now - 14 * DAY;
        const retired = goal.intervals.filter(([, b]) => b < boundary);
        goal.total += retired.reduce((sum, [a, b]) => sum + b - a, 0);
        goal.intervals = goal.intervals.filter(([, b]) => b >= boundary);
        goal.watermark = Math.max(goal.watermark, boundary);
        const intervals = union(goal.intervals, [observation.start, observation.end]);
        if (intervals.length <= MAX_INTERVALS) {
          goal.intervals = intervals;
          goal.first = Math.min(goal.first, observation.start); goal.last = Math.max(goal.last, observation.end);
          goal.day = observedDay;
        }
      }
    }
  }
  // Apply late terminal evidence before claiming that day's snapshot. Current
  // day's intervals are applied afterwards so they cannot erase a closed day.
  apply(observations.filter(item => new Date(item.end).toISOString().slice(0, 10) < today));
  const ready = state.goals.filter(g => g.day < today && g.reported !== g.day && now - Date.parse(g.day) < 8 * DAY);
  const payload = snapshot(ready);
  for (const goal of ready) goal.reported = goal.day;
  apply(observations.filter(item => new Date(item.end).toISOString().slice(0, 10) === today));
  await atomicWriteJson(path, state as unknown as JsonObject); await chmod(path, 0o600);
  return payload;
}
