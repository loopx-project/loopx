/** Diagnostic cycle pairing shared by every Host; never admission or settlement authority. */
import { readFile, chmod } from "node:fs/promises";
import { atomicWriteJson } from "../effect_runtime_io.ts";
import type { JsonObject } from "../effect_program.ts";
import { object } from "./usage_statistics_contract.ts";
import { hostCategory } from "./usage_statistics_goal_contract.ts";
import type { GoalObservation } from "./usage_statistics_goal_contract.ts";
import { readCodexTiming } from "./usage_statistics_codex.ts";
import type { CodexCursor } from "./usage_statistics_codex.ts";
export type CycleObservation = { key: string; lane: string; turn: string | null; phase: "start" | "spend"; at: number; host: string; codex?: { path: string; id: string } };
type Cycle = { id: string; start?: number; end?: number; touched: number; exact: boolean; host: string; floor?: number };
type State = { generation: string; cycles: Cycle[]; cursors: Record<string, CodexCursor> };
const WEEK = 7 * 86400000;
export function validCycle(value: unknown, now: number): value is CycleObservation {
  return object(value) && [value.key, value.lane].every(v => typeof v === "string" && /^[a-f0-9]{64}$/.test(v))
    && (value.turn === null || typeof value.turn === "string" && /^[a-f0-9]{64}$/.test(value.turn))
    && ["start", "spend"].includes(String(value.phase)) && Number.isSafeInteger(value.at)
    && Number(value.at) <= now + 1000 && Number(value.at) >= now - 86400000 && typeof value.host === "string";
}
export async function cycleObservations(path: string, generation: string, now: number, input: CycleObservation): Promise<GoalObservation[]> {
  let state: State = { generation, cycles: [], cursors: {} };
  try {
    const text = await readFile(path, "utf8");
    if (text.length > 256000) throw new Error("usage_cycle_limit");
    const prior = JSON.parse(text);
    if (prior.generation === generation && Array.isArray(prior.cycles) && prior.cycles.length <= 128 && object(prior.cursors) && Object.keys(prior.cursors).length <= 64) state = prior;
  } catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
  state.cycles = state.cycles.filter(c => now - c.touched <= WEEK);
  const id = `${input.key}:${input.lane}:${input.turn ?? "lane"}`;
  let cycle = state.cycles.find(c => c.id === id);
  if (!cycle && input.phase === "start" && state.cycles.length >= 128) {
    // Completed diagnostics cannot crowd out new work forever. An execution
    // replay never supplies a fresh spend marker at the CLI boundary.
    const closed = state.cycles.filter(c => c.end !== undefined).sort((a,b)=>a.touched-b.touched)[0];
    if (closed) state.cycles = state.cycles.filter(c => c !== closed);
  }
  if (!cycle && state.cycles.length < 128) {
    cycle = { id, touched: input.at, exact: input.turn !== null, host: hostCategory(input.host) }; state.cycles.push(cycle);
  }
  const observations: GoalObservation[] = [];
  const host = hostCategory(input.host);
  if (cycle) {
    if (!cycle.exact && input.phase === "start" && cycle.end !== undefined && input.at > cycle.end) { cycle.floor = cycle.end; delete cycle.start; delete cycle.end; cycle.host = host; }
    if (input.at > (cycle.floor ?? 0)) {
      if (input.phase === "start") cycle.start = Math.min(cycle.start ?? input.at, input.at);
      else cycle.end = Math.min(cycle.end ?? input.at, input.at);
      cycle.touched = Math.max(cycle.touched, input.at);
    }
    if (cycle.start !== undefined && cycle.end !== undefined && cycle.end >= cycle.start && cycle.end - cycle.start <= WEEK)
      observations.push({ key: input.key, start: cycle.start, end: cycle.end, measurement: "quota_cycle", host: hostCategory(cycle.host) });
  }
  if (input.codex && typeof input.codex.path === "string" && typeof input.codex.id === "string") {
    // Keys and paths stay machine-local. No transcript strings enter state or wire data.
    const cursorKey = `${input.key}:${input.lane}:${input.codex.id}`;
    state.cursors = Object.fromEntries(Object.entries(state.cursors).filter(([, c])=>now-c.seen<=WEEK));
    if (!state.cursors[cursorKey] && Object.keys(state.cursors).length >= 64) {
      const oldest = Object.entries(state.cursors).sort((a,b)=>a[1].seen-b[1].seen)[0][0];
      delete state.cursors[oldest];
    }
    try {
      const timing = await readCodexTiming(input.codex.path, input.codex.id, state.cursors[cursorKey], now, input.key, host);
      state.cursors[cursorKey] = timing.cursor;
      observations.push(...timing.observations);
    } catch { /* Finer Host timing is optional; the universal cycle still works. */ }
  }
  await atomicWriteJson(path, state as unknown as JsonObject); await chmod(path, 0o600);
  return observations;
}
