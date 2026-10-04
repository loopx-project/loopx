/** Deterministic history-to-replan policy. No IO, writes, or model authority. */
import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  jsonObject, requireJsonObject, requireBoolean, requireNonEmptyString,
  requireStringArray, requireStringLiteral, optionalNonEmptyString,
} from "../runtime_decode.ts";
import { projectTodoResumePlanning } from "../todos/resume_planning.ts";

interface Progress {
  fingerprint: string;
  result: "advanced" | "unchanged" | "blocked" | "exploration_exhausted" | "no_followup";
  payload: JsonObject;
}
interface Monitor {
  target: string | null;
  mode: string | null;
  material: boolean | null;
  frontier: string | null;
  todo: string | null;
  key: string | null;
}
interface Run {
  agent: string | null;
  publicAgent: string | null;
  monitorAgent: string | null;
  classification: string;
  generatedAt: string;
  observedAt: number | null;
  turn: string | null;
  ack: boolean;
  progress: Progress | null;
  monitor: Monitor;
}
interface MonitorTodo {
  id: string | null;
  claim: string | null;
  publicClaim: string | null;
  target: string;
  count: number;
  due: number | null;
  expiry: number | null;
}
interface Todos {
  monitors: readonly MonitorTodo[];
  advancements: readonly { status: string; taskClass: string; claim: string | null }[];
  resume: JsonObject | null;
}
interface Request {
  operation: "all" | "progress" | "periodic" | "monitor_streak";
  agent: string | null;
  monitorAgent: string | null;
  runs: readonly Run[];
  neutral: ReadonlySet<string>;
  stall: number;
  periodic: number;
  monitor: number;
  streak: number;
  monitorSchema: string;
  todos: Todos;
}

type Trigger = { run_count: number; threshold: number; agent_id: string | null } & (
  | { kind: "typed_progress_repeat"; schema_version: "typed_progress_observation_v0";
      progress_baseline: JsonObject; progress_fingerprint: string;
      latest_generated_at: string; oldest_counted_generated_at: string }
  | { kind: "periodic_review_due"; section: "run_history"; text: string;
      latest_generated_at: string; oldest_counted_generated_at: string }
  | { kind: "blocked_successor_no_progress_repeat"; section: "run_history";
      frontier_identity: string; monitor_target_id: string; latest_generated_at: string }
  | { kind: "dead_monitor_repeat"; schema_version: string; section: "run_history";
      monitor_target_id: string; latest_generated_at: string }
  | { kind: "monitor_no_change_streak"; schema_version: string; section: "agent_todos";
      monitor_target_id: string; text: string }
);

function text(value: unknown, label: string): string {
  if (typeof value !== "string") throw new EffectRuntimeRequestError(`${label} must be a string`);
  return value;
}
function integer(value: unknown, label: string, minimum: number): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum) {
    throw new EffectRuntimeRequestError(`${label} must be a safe integer >= ${minimum}`);
  }
  return value;
}
function time(value: unknown, label: string): number | null {
  if (value === null) return null;
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new EffectRuntimeRequestError(`${label} must be a finite timestamp or null`);
  }
  return value;
}
function rows<T>(value: unknown, label: string, decode: (row: JsonObject) => T): T[] {
  if (!Array.isArray(value)) throw new EffectRuntimeRequestError(`${label} must be an array`);
  return value.map(row => decode(requireJsonObject(row, label)));
}
function decodeProgress(value: unknown): Progress | null {
  if (value === null) return null;
  const payload = requireJsonObject(value, "progress");
  requireStringLiteral(payload.schema_version, ["typed_progress_observation_v0"], "progress schema");
  return {
    payload, fingerprint: requireNonEmptyString(payload.fingerprint, "fingerprint"),
    result: requireStringLiteral(payload.result_class,
      ["advanced", "unchanged", "blocked", "exploration_exhausted", "no_followup"], "result_class"),
  };
}
function decode(value: unknown): Request {
  const raw = requireJsonObject(value, "replan history");
  requireStringLiteral(raw.schema_version, ["replan_history_request_v0"], "replan history schema");
  const todo = requireJsonObject(raw.todos, "todos");
  return {
    operation: requireStringLiteral(raw.operation, ["all", "progress", "periodic", "monitor_streak"], "operation"),
    agent: optionalNonEmptyString(raw.agent_id, "agent_id"),
    monitorAgent: optionalNonEmptyString(raw.monitor_agent_id, "monitor_agent_id"),
    neutral: new Set(requireStringArray(raw.neutral_classifications, "neutral_classifications")),
    stall: integer(raw.stall_threshold, "stall_threshold", 2),
    periodic: integer(raw.periodic_threshold, "periodic_threshold", 1),
    monitor: integer(raw.monitor_threshold, "monitor_threshold", 1),
    streak: integer(raw.streak_threshold, "streak_threshold", 1),
    monitorSchema: requireNonEmptyString(raw.monitor_schema, "monitor_schema"),
    runs: rows(raw.runs, "runs", row => {
      const monitor = requireJsonObject(row.monitor, "monitor");
      return {
        agent: optionalNonEmptyString(row.agent_id, "run.agent_id"),
        publicAgent: optionalNonEmptyString(row.public_agent_id, "public_agent_id"),
        monitorAgent: optionalNonEmptyString(row.monitor_agent_id, "run.monitor_agent_id"),
        classification: text(row.classification, "classification"),
        generatedAt: text(row.generated_at, "generated_at"), observedAt: time(row.observed_at, "observed_at"),
        turn: optionalNonEmptyString(row.turn_id, "turn_id"), ack: requireBoolean(row.accepted_ack, "accepted_ack"),
        progress: decodeProgress(row.progress),
        monitor: {
          target: optionalNonEmptyString(monitor.target_id, "target_id"),
          mode: optionalNonEmptyString(monitor.mode, "mode"),
          material: monitor.material_change == null
            ? null : requireBoolean(monitor.material_change, "material_change"),
          frontier: optionalNonEmptyString(monitor.frontier, "frontier"),
          todo: optionalNonEmptyString(monitor.todo_id, "todo_id"),
          key: optionalNonEmptyString(monitor.target_key, "target_key"),
        },
      };
    }),
    todos: {
      resume: todo.resume === null ? null : requireJsonObject(todo.resume, "resume"),
      monitors: rows(todo.monitors, "monitors", row => ({
        id: optionalNonEmptyString(row.id, "monitor.id"), claim: optionalNonEmptyString(row.claim, "claim"),
        publicClaim: optionalNonEmptyString(row.public_claim, "public_claim"),
        target: text(row.target, "monitor.target"),
        count: integer(row.no_change_count, "no_change_count", Number.MIN_SAFE_INTEGER),
        due: time(row.due_at, "due_at"), expiry: time(row.expires_at, "expires_at"),
      })),
      advancements: rows(todo.advancements, "advancements", row => ({
        status: text(row.status, "status"), taskClass: text(row.task_class, "task_class"),
        claim: optionalNonEmptyString(row.claim, "claim"),
      })),
    },
  };
}

/** Scope before ACK: a peer's acknowledgement cannot discharge this lane. */
function historyWindow(request: Request): Run[] {
  const agent = request.agent ?? (request.operation === "all"
    ? request.runs.find(run => !request.neutral.has(run.classification) && run.agent)?.agent : null);
  const result: Run[] = [];
  for (const run of request.runs) {
    if (agent && run.agent && run.agent !== agent) continue;
    if (run.ack) break;
    if (request.neutral.has(run.classification)) continue;
    result.push(run);
  }
  return result;
}

/** De-duplicate accepted evidence, not arbitrary records sharing its turn.
 * A leading untyped row cannot erase an older typed observation from that turn.
 * Null legacy identities remain independent; neutral rows were already removed.
 */
function* distinctTurns(runs: readonly Run[], counts: (run: Run) => boolean = () => true): Generator<Run, void, unknown> {
  const seen = new Set<string>();
  // Historical goal-level rows can omit attribution. Within a single scoped
  // lane, keep the original turn identity across an attributed/unattributed retry.
  const lane = sole(runs.map(run => run.agent));
  for (const run of runs) {
    const key = run.turn ? JSON.stringify([run.agent ?? lane, run.turn]) : null;
    if (key && seen.has(key)) continue;
    if (key && counts(run)) seen.add(key);
    yield run;
  }
}
function sole(values: readonly (string | null)[]): string | null {
  const unique = new Set(values.filter((value): value is string => value !== null));
  return unique.size === 1 ? [...unique][0]! : null;
}
function progressTrigger(runs: readonly Run[], request: Request): Trigger | null {
  const observed: (Run & { progress: Progress })[] = [];
  for (const run of distinctTurns(runs, row => row.progress !== null)) {
    if (run.progress === null) {
      if (observed.length) break;
      continue;
    }
    observed.push({ ...run, progress: run.progress });
    if (observed.length >= request.stall) break;
  }
  if (observed.length < request.stall) return null;
  const first = observed[0]!;
  if (!sole(observed.map(row => row.progress.fingerprint)) ||
      !["unchanged", "blocked"].includes(first.progress.result)) return null;
  return {
    kind: "typed_progress_repeat", schema_version: "typed_progress_observation_v0",
    agent_id: request.agent, run_count: request.stall, threshold: request.stall,
    progress_fingerprint: first.progress.fingerprint, progress_baseline: first.progress.payload,
    latest_generated_at: first.generatedAt, oldest_counted_generated_at: observed.at(-1)!.generatedAt,
  };
}
function periodicTrigger(runs: readonly Run[], request: Request): Trigger | null {
  const durable: Run[] = [];
  // Monitor liveness receipts are durable, but only a material transition is
  // work for the periodic direction review. Monitor-specific triggers still
  // inspect every poll in the unfiltered history window.
  for (const run of distinctTurns(runs.filter(row => row.classification &&
      (row.classification !== "quota_monitor_poll" || row.monitor.material === true)))) {
    durable.push(run);
    if (durable.length >= request.periodic) break;
  }
  if (durable.length < request.periodic) return null;
  return {
    kind: "periodic_review_due", section: "run_history",
    text: `latest ${durable.length} durable public run records since last autonomous replan reached periodic review threshold ${request.periodic}`,
    run_count: durable.length, threshold: request.periodic,
    latest_generated_at: durable[0]!.generatedAt, oldest_counted_generated_at: durable.at(-1)!.generatedAt,
    agent_id: sole(durable.map(run => run.publicAgent)),
  };
}
function futureBlockingMonitor(request: Request, signals: readonly Run[]): boolean {
  const observedAt = signals[0]!.observedAt;
  const agent = sole(signals.map(run => run.monitorAgent)) ?? request.monitorAgent;
  if (observedAt === null || !agent || !request.todos.resume) return false;
  const monitors = new Map(request.todos.monitors.filter(row => row.id && (!row.claim || row.claim === agent))
    .map(row => [row.id!, row]));
  if (!monitors.size) return false;
  // Compose the established planner here: no nested process/bridge call and no
  // second Python copy of Todo resume or ownership rules.
  const plan = projectTodoResumePlanning(request.todos.resume);
  const items = [...plan.monitor_blocked_items as JsonObject[], ...plan.deferred_items as JsonObject[]];
  for (const row of items) {
    if (row.claimed_by && row.claimed_by !== agent) continue;
    const condition = jsonObject(row.resume_condition);
    const ids = [row.blocking_monitor_todo_id ?? condition?.target_todo_id ?? condition?.target,
      ...(Array.isArray(row.successor_todo_ids) ? row.successor_todo_ids : [])];
    for (const id of ids) {
      const monitor = typeof id === "string" ? monitors.get(id) : undefined;
      if (monitor?.due !== null && monitor?.due !== undefined && monitor.due > observedAt &&
          (monitor.expiry === null || (monitor.expiry > observedAt && monitor.expiry > monitor.due))) return true;
    }
  }
  return false;
}
function monitorTrigger(runs: readonly Run[], request: Request): Trigger | null {
  const signals: Run[] = [];
  for (const run of distinctTurns(runs)) {
    if (run.classification !== "quota_monitor_poll" || !run.monitor.target || !run.monitor.mode) break;
    signals.push(run);
    if (signals.length >= Math.max(request.stall, request.monitor)) break;
  }
  if (signals.length < request.stall) return null;
  const blocked = signals.slice(0, request.stall);
  if (blocked.every(run => run.monitor.mode === "blocked_successor_wait_without_material_transition")) {
    if (futureBlockingMonitor(request, blocked)) return null;
    const target = sole(blocked.map(run => run.monitor.target));
    const frontier = sole(blocked.map(run => run.monitor.frontier));
    if (!target || !frontier) return null;
    return {
      kind: "blocked_successor_no_progress_repeat", section: "run_history",
      run_count: blocked.length, threshold: request.stall, monitor_target_id: target,
      frontier_identity: frontier, latest_generated_at: blocked[0]!.generatedAt,
      agent_id: sole(blocked.map(run => run.agent)),
    };
  }
  const repeated = signals.filter(run => (run.monitor.todo || run.monitor.key) &&
    ["due_monitor_observed_without_material_transition", "external_monitor_observed_without_material_transition"]
      .includes(run.monitor.mode!)).slice(0, request.monitor);
  const target = sole(repeated.map(run => run.monitor.target));
  if (repeated.length < request.monitor || !target) return null;
  return {
    kind: "dead_monitor_repeat", schema_version: request.monitorSchema, section: "run_history",
    run_count: repeated.length, threshold: request.monitor, monitor_target_id: target,
    latest_generated_at: repeated[0]!.generatedAt, agent_id: sole(repeated.map(run => run.agent)),
  };
}
function monitorStreak(request: Request): Trigger | null {
  const stalled = request.todos.monitors.filter(row => row.count >= request.streak)
    .sort((a, b) => b.count - a.count)[0];
  if (!stalled || request.todos.advancements.some(row => row.status === "open" &&
      row.taskClass === "advancement_task" && (!row.claim || row.claim === stalled.publicClaim))) return null;
  return {
    kind: "monitor_no_change_streak", schema_version: request.monitorSchema, section: "agent_todos",
    text: `monitor ${stalled.target} recorded ${stalled.count} consecutive unchanged polls without runnable advancement`,
    run_count: stalled.count, threshold: request.streak,
    monitor_target_id: stalled.target, agent_id: stalled.publicClaim,
  };
}

export function projectReplanHistory(value: unknown): JsonObject {
  const request = decode(value);
  const runs = historyWindow(request);
  let trigger: Trigger | null;
  switch (request.operation) {
    case "all": trigger = progressTrigger(runs, request) ?? monitorTrigger(runs, request) ?? periodicTrigger(runs, request); break;
    case "progress": trigger = progressTrigger(runs, request); break;
    case "periodic": trigger = periodicTrigger(runs, request); break;
    case "monitor_streak": trigger = monitorStreak(request); break;
  }
  return { schema_version: "replan_history_result_v0", trigger };
}
