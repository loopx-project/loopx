/** Bounded machine-local daily interval union; uses the common consent lock. */
import { readFile, chmod } from "node:fs/promises";
import { atomicWriteJson } from "../effect_runtime_io.ts";
import type { JsonObject } from "../effect_program.ts";
import { object, MAX_COUNT } from "./usage_statistics_contract.ts";
import { union } from "./usage_statistics_goals.ts";
import { validGoalObservation, MEASUREMENTS } from "./usage_statistics_goal_contract.ts";
import type { GoalObservation, Measurement } from "./usage_statistics_goal_contract.ts";
import { INSTALLATION_SCHEMA, validInstallationDay } from "./usage_statistics_installation_contract.ts";
import type { InstallationDay, InstallationUsage, ProfileFeature } from "./usage_statistics_installation_contract.ts";

const DAY = 86400000, MAX_INTERVALS = 1024;
type LocalDay = InstallationDay & { intervals: Partial<Record<Measurement, [number, number][]>>; attempted?: number };
type LocalState = { generation: string; days: LocalDay[]; last_attempt?: number };
function utc(ms: number) { return new Date(ms).toISOString().slice(0, 10); }
async function load(path: string, generation: string): Promise<LocalState> {
  try {
    const text = await readFile(path, "utf8");
    if (text.length > 1024 * 1024) throw new Error("installation_usage_too_large");
    const value: unknown = JSON.parse(text);
    if (!object(value)) throw new Error("installation_usage_invalid");
    if (value.generation !== generation) return { generation, days: [] };
    if (!Array.isArray(value.days) || value.days.length > 8
      || (value.last_attempt !== undefined && (!Number.isSafeInteger(value.last_attempt) || Number(value.last_attempt) < 0))
      || value.days.some(raw => {
        if (!object(raw)) return true;
        const { intervals, attempted, ...profile } = raw;
        if (!validInstallationDay(profile) || !object(intervals)
          || (attempted !== undefined && (!Number.isSafeInteger(attempted) || Number(attempted) < 0 || Number(attempted) > profile.revision))) return true;
        return Object.entries(intervals).some(([measurement, rows]) => {
          if (!(MEASUREMENTS as readonly string[]).includes(measurement) || !Array.isArray(rows) || rows.length > MAX_INTERVALS) return true;
          const start = Date.parse(profile.activity_day);
          return rows.some((row, index) => !Array.isArray(row) || row.length !== 2 || !row.every(Number.isSafeInteger)
            || row[0] < start || row[1] > start + DAY || row[0] > row[1] || (index > 0 && row[0] <= rows[index - 1][1]));
        });
      }) || new Set(value.days.map(raw => raw.activity_day)).size !== value.days.length) throw new Error("installation_usage_invalid");
    return value as LocalState;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return { generation, days: [] };
    throw error;
  }
}
function publicDay(row: LocalDay): InstallationDay {
  const { intervals: _intervals, attempted: _attempted, ...profile } = row;
  return profile;
}
export async function installationPreview(path: string, generation: string, installId: string): Promise<InstallationUsage | null> {
  const profiles = (await load(path, generation)).days.filter(row => row.attempted !== row.revision).map(publicDay);
  return profiles.length ? { schema: INSTALLATION_SCHEMA, install_id: installId, profiles } : null;
}
export async function recordInstallation(path: string, generation: string, installId: string, now: number,
  version: string, context: InstallationDay["context"], feature: ProfileFeature | undefined,
  observations: GoalObservation[]): Promise<InstallationUsage | null> {
  const state = await load(path, generation);
  const today = utc(now), oldest = Date.parse(today) - 7 * DAY;
  state.days = state.days.filter(row => Date.parse(row.activity_day) >= oldest);
  const getDay = (activityDay: string): LocalDay => {
    let row = state.days.find(item => item.activity_day === activityDay);
    if (!row) {
      row = { activity_day: activityDay, version, context, revision: 1, cli: [], runtime: [], truncated: false, intervals: {} };
      state.days.push(row);
    }
    return row;
  };
  // A daily profile's context/version is frozen on first observation, not relabeled on settings changes.
  const current = getDay(today);
  if (feature) {
    const row = current.cli.find(item => item.feature === feature);
    if (row && row.count < MAX_COUNT) row.count++;
    else if (row) current.truncated = true;
    else current.cli.push({ feature, count: 1 });
    current.revision++;
  }
  for (const observation of observations.filter(item => validGoalObservation(item, now))) {
    const start = Math.max(observation.start, oldest);
    for (let boundary = Date.parse(utc(start)); boundary < observation.end; boundary += DAY) {
      const row = getDay(utc(boundary));
      const before = row.intervals[observation.measurement] ?? [];
      const after = union(before, [Math.max(start, boundary), Math.min(observation.end, boundary + DAY)]);
      if (JSON.stringify(after) === JSON.stringify(before)) continue; // replay never extends measured time
      if (after.length > MAX_INTERVALS) row.truncated = true;
      else {
        row.intervals[observation.measurement] = after;
        const minutes = Math.floor(after.reduce((sum, [a, b]) => sum + b - a, 0) / 60000);
        const measured = row.runtime.find(item => item.measurement === observation.measurement);
        if (measured) measured.observed_minutes = minutes;
        else row.runtime.push({ measurement: observation.measurement, observed_minutes: minutes });
      }
      row.revision++;
    }
  }
  let payload: InstallationUsage | null = null;
  if (state.days.some(row => row.cli.length > 0 || row.runtime.length > 0 || row.activity_day < today)
    && (state.last_attempt === undefined || now - state.last_attempt >= 15 * 60000)) {
    const pending = state.days.filter(row => row.attempted !== row.revision);
    if (pending.length) {
      payload = { schema: INSTALLATION_SCHEMA, install_id: installId, profiles: pending.map(publicDay) };
      for (const row of pending) row.attempted = row.revision;
      state.last_attempt = now; // lossy claim, never retry or consume on settings reads
    }
  }
  await atomicWriteJson(path, state as unknown as JsonObject); await chmod(path, 0o600);
  return payload;
}
