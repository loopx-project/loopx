/** Owner cadence policy and schedule projection; independent of hint resets. */
import {readFile} from "node:fs/promises";
import {createHash} from "node:crypto";
import type {JsonObject} from "../effect_program.ts";
import {atomicWriteJson, withFileMutationLock} from "../effect_runtime_io.ts";
import {EffectRuntimeConflictError, EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireJsonObject, requireNonEmptyString, requireInteger, requireStringLiteral} from "../runtime_decode.ts";
import {schedulerStatePath} from "../scheduler/state_store.ts";

const LEGACY_SCHEMA = "automation_cadence_store_v1";
const SCHEMA = "automation_cadence_store_v2";
const RESULT = "automation_cadence_result_v1";
type Scope = {agent_id: string | null; automation_id: string | null};
type Rule = Scope & {min_interval_minutes: number; revision: number; owner_reference: string};
/** `reserved` is a start whose host may still be unstarted; `started` is a start
 * whose host attempt is already durable in the Turn journal. Only the former can
 * be resumed by the same request identity, and a record without this field is
 * read as `started` so an older or hand-edited store fails closed. */
type StartState = "reserved" | "started";
type Start = Scope & {started_at_ms: number; trigger_at_ms: number; request_id: string;
  manual_reason: string | null; state: StartState};
type Store = {schema_version: typeof SCHEMA; goal_id: string; revision: number; rules: Rule[]; starts: Start[]};
const fail = (message: string): never => {throw new EffectRuntimeRequestError(message, "automation_cadence_invalid");};
/** Stale configuration intent is a typed conflict, never a rejected request. */
const conflict = (message: string): never => {
  throw new EffectRuntimeConflictError(message, "automation_cadence_revision_conflict");
};
function text(value: unknown, name: string): string {
  const s = requireNonEmptyString(value, name).trim();
  if (!s || s.length > 256 || /[\u0000-\u001f]/.test(s)) fail(`${name} is invalid`);
  return s;
}
function integer(value: unknown, name: string): number {
  const n = requireInteger(value, name);
  if (!Number.isSafeInteger(n) || n < 0) fail(`${name} must be a non-negative safe integer`);
  return n;
}
function minutes(value: unknown): number {
  const n = integer(value, "min_interval_minutes");
  if (n > 525600) fail("min_interval_minutes exceeds one year");
  return n;
}
function scope(p: JsonObject): Scope {
  const agent_id = p.agent_id == null ? null : text(p.agent_id, "agent_id");
  const automation_id = p.automation_id == null ? null : text(p.automation_id, "automation_id");
  if (automation_id && !agent_id) fail("automation scope requires agent_id");
  return {agent_id, automation_id};
}
const key = (s: Scope): string => createHash("sha256").update(JSON.stringify([s.agent_id, s.automation_id])).digest("hex");
function decode(value: unknown, goal: string): Store {
  const p = requireJsonObject(value, "cadence store");
  if ((p.schema_version !== SCHEMA && p.schema_version !== LEGACY_SCHEMA) || p.goal_id !== goal)
    fail("cadence store identity/schema mismatch");
  const revision = integer(p.revision, "revision");
  if (!Array.isArray(p.rules)) fail("cadence rules must be an array");
  const seen = new Set<string>();
  const rules = (p.rules as unknown[]).map(raw => {
    const r = requireJsonObject(raw, "rule"), s = scope(r), k = key(s);
    if (seen.has(k)) fail("duplicate cadence scope");
    seen.add(k);
    const v = integer(r.revision, "rule revision");
    if (v > revision) fail("rule revision exceeds configuration revision");
    return {...s, min_interval_minutes: minutes(r.min_interval_minutes), revision: v, owner_reference: text(r.owner_reference, "owner_reference")};
  });
  if (p.starts !== undefined && !Array.isArray(p.starts)) fail("cadence starts must be an array");
  const startKeys = new Set<string>();
  const starts = ((p.starts ?? []) as unknown[]).map(raw => {
    const r = requireJsonObject(raw, "start"), s = scope(r), k = key(s);
    if (!s.agent_id || startKeys.has(k)) fail("cadence start identity is invalid or duplicated");
    startKeys.add(k);
    const started_at_ms = integer(r.started_at_ms, "started_at_ms");
    const trigger_at_ms = integer(r.trigger_at_ms, "trigger_at_ms");
    if (trigger_at_ms > started_at_ms) fail("cadence start trigger follows its start");
    return {...s, started_at_ms, trigger_at_ms, request_id: text(r.request_id, "request_id"),
      manual_reason: r.manual_reason == null ? null : text(r.manual_reason, "manual_reason"),
      state: r.state === undefined ? "started"
        : requireStringLiteral(r.state, ["reserved", "started"] as const, "start state")};
  });
  return {schema_version: SCHEMA, goal_id: goal, revision, rules, starts};
}
export function cadenceStorePath(runtimeRoot: string, goalId: string): string {
  return schedulerStatePath(runtimeRoot, {goalId, agentId: "owner-policy", surface: "quota", stateKey: "automation-cadence-v1"});
}
async function load(path: string, goal: string): Promise<Store> {
  try {return decode(JSON.parse(await readFile(path, "utf8")), goal);}
  catch (e) {
    if ((e as NodeJS.ErrnoException).code !== "ENOENT") throw e;
    return {schema_version: SCHEMA, goal_id: goal, revision: 0, rules: [], starts: []};
  }
}
function applicable(r: Rule, s: Scope): boolean {
  return r.agent_id === null || (r.agent_id === s.agent_id && (r.automation_id === null || r.automation_id === s.automation_id));
}
function projection(store: Store, s: Scope, nowMs = Date.now()): JsonObject {
  const rules = store.rules.filter(r => applicable(r, s));
  const floor = Math.max(0, ...rules.map(r => r.min_interval_minutes));
  const due = s.agent_id ? rules.filter(rule => rule.min_interval_minutes > 0).flatMap(rule => {
    const start = store.starts.find(item => item.agent_id === s.agent_id &&
      item.automation_id === rule.automation_id);
    return start ? [start.started_at_ms + rule.min_interval_minutes * 60_000] : [];
  }) : [];
  const next = due.length ? Math.max(...due) : null;
  return {
    schema_version: RESULT, ok: true, enabled: floor > 0, goal_id: store.goal_id, ...s,
    configuration_revision: store.revision, min_interval_minutes: floor,
    sources: rules,
    reason: floor === 0 ? "unconfigured" : "owner_minimum_interval",
    next_eligible_at_ms: next, eligible_now: next === null || nowMs >= next,
    enforcement: floor === 0 ? "scheduler_recommendation" : "managed_turn_atomic_admission_and_schedule_recommendation",
    pre_model_admission: floor === 0 ? "not_qualified" : "managed_turn_only",
  };
}

/** Reserve a managed-host start under the same lock as policy changes.
 *
 * A reserved start whose host attempt never became durable may be resumed by the
 * same request identity, so a crash between reservation and the first journal
 * attempt cannot strand the Turn. A start whose host attempt is already durable
 * stays fail-closed, and the caller must confirm the reservation once the
 * attempt is recorded. */
export async function admitAutomationStart(p: JsonObject): Promise<JsonObject> {
  const goal = text(p.goal_id, "goal_id"), s = scope(p);
  if (!s.agent_id) fail("admission requires agent_id");
  const path = cadenceStorePath(requireNonEmptyString(p.runtime_root, "runtime_root"), goal);
  const now = integer(p.now_ms, "now_ms"), trigger = integer(p.trigger_at_ms, "trigger_at_ms");
  if (trigger > now) fail("trigger_at_ms cannot be in the future");
  const request = text(p.request_id, "request_id");
  const manual = p.manual_reason == null ? null : text(p.manual_reason, "manual_reason");
  // An absent policy does not create a store or lock file on the default-off path.
  const snapshot = await load(path, goal);
  const initial = projection(snapshot, s, now);
  if (initial.enabled !== true) return {...initial, admitted: true, reserved: false};
  return withFileMutationLock(path, async () => {
    const store = await load(path, goal), current = projection(store, s, now);
    if (current.enabled !== true) return {...current, admitted: true, reserved: false};
    const activeScopes = new Set(store.rules.filter(rule => rule.min_interval_minutes > 0 && applicable(rule, s))
      .map(rule => rule.automation_id));
    const prior = store.starts.filter(start => start.agent_id === s.agent_id &&
      activeScopes.has(start.automation_id));
    const held = prior.find(start => start.request_id === request);
    if (prior.some(start => trigger < start.trigger_at_ms) || (held !== undefined && held.state === "started")) {
      return {...current, admitted: false, reserved: false, reason: "duplicate_or_stale_trigger"};
    }
    if (held !== undefined) {
      // Same identity, no durable host attempt: keep the original interval anchor.
      return {...current, admitted: true, reserved: true, resumed: true,
        reason: "resumed_unstarted_reservation"};
    }
    if (manual === null && current.eligible_now !== true) {
      return {...current, admitted: false, reserved: false, reason: "minimum_interval_wait"};
    }
    const scopes: Scope[] = [{agent_id: s.agent_id, automation_id: null}];
    if (s.automation_id && activeScopes.has(s.automation_id)) scopes.push(s);
    for (const item of scopes) {
      store.starts = store.starts.filter(start => key(start) !== key(item));
      store.starts.push({...item, started_at_ms: now, trigger_at_ms: trigger, request_id: request,
        manual_reason: manual, state: "reserved"});
    }
    await atomicWriteJson(path, store);
    return {...projection(store, s, now), admitted: true, reserved: true,
      reason: manual === null ? "admitted" : "explicit_manual_interval_bypass"};
  });
}

/** Mark a reservation as an attempted host start once the Turn journal is durable.
 *
 * Confirmation is idempotent and never creates a store, so the default-off path
 * stays free of new files. Until it lands, the record stays resumable; after it
 * lands, the same request identity is rejected fail-closed. */
export async function confirmAutomationStart(p: JsonObject): Promise<JsonObject> {
  const goal = text(p.goal_id, "goal_id"), s = scope(p);
  if (!s.agent_id) fail("confirmation requires agent_id");
  const path = cadenceStorePath(requireNonEmptyString(p.runtime_root, "runtime_root"), goal);
  const request = text(p.request_id, "request_id");
  const record = async (store: Store): Promise<JsonObject> => {
    const matches = store.starts.filter(start => start.agent_id === s.agent_id && start.request_id === request);
    const pending = matches.some(start => start.state === "reserved");
    if (pending) {
      store.starts = store.starts.map(start => start.agent_id === s.agent_id && start.request_id === request
        ? {...start, state: "started"} : start);
      await atomicWriteJson(path, store);
    }
    return {schema_version: RESULT, ok: true, goal_id: goal, ...s, request_id: request, confirmed: true,
      reason: pending ? "start_confirmed" : "already_confirmed"};
  };
  // An absent or empty reservation must not create a store or lock file.
  const snapshot = await load(path, goal);
  if (!snapshot.starts.some(start => start.agent_id === s.agent_id && start.request_id === request)) {
    return {schema_version: RESULT, ok: true, goal_id: goal, ...s, request_id: request, confirmed: false,
      reason: "reservation_missing"};
  }
  return withFileMutationLock(path, async () => record(await load(path, goal)));
}
/** Pure typed calculation shared by scheduler adapters; no policy mutation. */
export function projectCadenceProgression(p: JsonObject): JsonObject {
  const floor = minutes(p.min_interval_minutes);
  if (!Array.isArray(p.progression) || p.progression.length === 0) fail("progression is required");
  const progression = [...new Set((p.progression as unknown[]).map(v => Math.max(1, floor, integer(v, "interval"))))];
  return {progression, effective_interval_minutes: progression[0], min_interval_minutes: floor,
    reason: floor > 0 ? "owner_floor_applied_after_backoff" : "unconfigured"};
}
/** Apply one policy snapshot to both scheduler projections in one transport call. */
export function projectCadenceSchedule(p: JsonObject): JsonObject {
  const floor = minutes(p.min_interval_minutes);
  return {
    local: projectCadenceProgression({progression: p.local, min_interval_minutes: floor}).progression,
    app: projectCadenceProgression({progression: p.app, min_interval_minutes: floor}).progression,
    local_max: Math.max(integer(p.local_max, "local_max"), floor),
    app_max: Math.max(integer(p.app_max, "app_max"), floor),
    floor,
    guarantee: {
      pre_model_atomic_admission: "not_qualified",
      model_wakeup_tokens_prevented: false,
      schedule_readback_required: true,
      unsupported_schedule_action: "pause_affected_automation",
      boundary: "schedule recommendation only; App hook coverage is not qualified",
      on_apply_failure: "pause_affected_automation_do_not_shorten_interval",
    },
  };
}

/** Local owner CLI boundary. Same-UID filesystem access is not an authentication sandbox. */
export async function manageAutomationCadence(p: JsonObject): Promise<JsonObject> {
  const operation = requireStringLiteral(p.operation, ["read", "configure"] as const, "operation");
  const goal = text(p.goal_id, "goal_id"), s = scope(p);
  const path = cadenceStorePath(requireNonEmptyString(p.runtime_root, "runtime_root"), goal);
  const act = async (): Promise<JsonObject> => {
    const store = await load(path, goal);
    if (operation === "read") return projection(store, s);
    if (operation === "configure") {
      if (integer(p.expected_revision, "expected_revision") !== store.revision)
        conflict("configuration revision conflict; read current policy before changing it");
      const value = minutes(p.min_interval_minutes), reference = text(p.owner_reference, "owner_reference");
      const old = store.rules.find(r => key(r) === key(s));
      if (value < (old?.min_interval_minutes ?? 0) && p.approve_reduction !== true) fail("lowering or disabling the floor requires explicit owner-approved reduction");
      const rule = {...s, min_interval_minutes: value, revision: store.revision + 1, owner_reference: reference};
      store.rules = [...store.rules.filter(r => key(r) !== key(s)), rule];
      store.revision++;
      if (p.execute === true) await atomicWriteJson(path, store);
      return {...projection(store, s), written: p.execute === true, preview: p.execute !== true};
    }
    return fail("unsupported cadence operation");
  };
  // Read-only policy inspection never creates directories or lock files.
  return operation === "read" || p.execute !== true ? act() : withFileMutationLock(path, act);
}
