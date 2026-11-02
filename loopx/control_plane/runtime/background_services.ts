/** Process-local background diagnostics. No scheduling or effect admission.
 * This is the shared projection contract; provider-specific health remains an
 * optional typed probe. Thread liveness is never evidence of a completed scan.
 */
import {requireJsonObject, requireBoolean, requireInteger, requireNonEmptyString} from "../runtime_decode.ts";

export const BACKGROUND_STATES = ["disabled", "unavailable", "unverified", "starting",
  "checking", "healthy", "retrying", "overdue", "stopping", "stopped", "completed"] as const;
export type BackgroundState = typeof BACKGROUND_STATES[number];

type Worker = {
  alive: boolean; stop_requested: boolean; terminal: boolean; executing: boolean;
  started_at: number | null; last_progress_at?: number | null; last_success_at: number | null; last_failure_at: number | null;
  next_check_at: number | null; deadline_at: number | null; failure_count: number;
};
type Observation = {
  enabled: boolean | null; available: boolean; reconcile_failed?: boolean;
  worker?: Worker;
  probe?: {state: BackgroundState; checked_at: number | null; next_check_at: number | null};
};
const epoch = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;

export function projectBackgroundTask(input: Observation, now: number) {
  const worker = input.worker;
  let state: BackgroundState;
  if (!input.available) state = "unavailable";
  else if (input.enabled === false) state = "disabled";
  else if (input.enabled !== true) state = "unverified";
  else if (input.probe) state = BACKGROUND_STATES.includes(input.probe.state)
    ? input.probe.state : "unverified";
  else if (!worker) state = "unverified";
  else if (worker.stop_requested) state = worker.alive ? "stopping" : "stopped";
  else if ([worker.started_at, worker.last_progress_at, worker.last_success_at, worker.last_failure_at].some(value => (epoch(value) ?? 0) > now + 5)) state = "unverified";
  else if (!worker.alive) state = worker.terminal && worker.failure_count === 0
    && epoch(worker.last_success_at) !== null ? "completed" : "stopped";
  else if (epoch(worker.started_at) === null) state = "starting";
  else if (epoch(worker.deadline_at) === null) state = "unverified";
  else if (worker.deadline_at! < now) state = "overdue";
  else if (worker.failure_count > 0) state = "retrying";
  else if (worker.executing) state = "checking";
  else state = epoch(worker.last_success_at) !== null ? "healthy" : "unverified";
  if (input.probe && ["healthy", "checking", "retrying"].includes(state)
      && (epoch(input.probe.checked_at) === null || input.probe.checked_at! > now + 5)) state = "unverified";
  // Reconcile failure cannot turn a verified stopped/disabled service green.
  // Preserve a more precise provider or progress fault when one is observed.
  if (input.reconcile_failed && ["healthy", "checking", "starting"].includes(state)) state = "retrying";
  return {state, observed_at: epoch(input.probe?.checked_at)
      ?? (Math.max(epoch(worker?.started_at) ?? 0, epoch(worker?.last_progress_at) ?? 0, epoch(worker?.last_success_at) ?? 0,
                  epoch(worker?.last_failure_at) ?? 0) || null),
    last_success_at: input.probe ? null : epoch(worker?.last_success_at),
    last_failure_at: input.probe ? null : epoch(worker?.last_failure_at),
    next_check_at: epoch(input.probe?.next_check_at ?? worker?.next_check_at),
    failure_count: input.probe ? null : worker?.failure_count ?? 0,
    reconcile_failed: input.reconcile_failed === true};
}

function observation(value: unknown): Observation {
  const row = requireJsonObject(value, "background observation");
  let worker: Worker | undefined;
  if (row.worker !== undefined) {
    const data = requireJsonObject(row.worker, "worker progress");
    const failures = requireInteger(data.failure_count, "failure count");
    if (failures < 0) throw new Error("Invalid failure count");
    worker = {alive: requireBoolean(data.alive, "alive"),
      stop_requested: requireBoolean(data.stop_requested, "stop requested"),
      terminal: requireBoolean(data.terminal, "terminal"), executing: requireBoolean(data.executing, "executing"),
      started_at: epoch(data.started_at), last_progress_at: epoch(data.last_progress_at), last_success_at: epoch(data.last_success_at),
      last_failure_at: epoch(data.last_failure_at), next_check_at: epoch(data.next_check_at),
      deadline_at: epoch(data.deadline_at), failure_count: failures};
  }
  const probe = row.probe === undefined ? undefined : requireJsonObject(row.probe, "provider probe");
  return {available: requireBoolean(row.available, "available"),
    enabled: row.enabled === null ? null : requireBoolean(row.enabled, "enabled"), worker,
    reconcile_failed: row.reconcile_failed === undefined ? false : requireBoolean(row.reconcile_failed, "reconcile failed"),
    ...(probe ? {probe: {state: probe.state as BackgroundState, checked_at: epoch(probe.checked_at),
      next_check_at: epoch(probe.next_check_at)}} : {})};
}

export function projectBackgroundServices(params: Record<string, unknown>) {
  const now = epoch(params.now);
  if (now === null || !Array.isArray(params.tasks)) throw new Error("Invalid background observation");
  return {ok: true as const, schema_version: "background_services_v1" as const, checked_at: now,
    supervisor: projectBackgroundTask(observation(params.supervisor), now),
    tasks: params.tasks.map(value => {
      const task = requireJsonObject(value, "background task");
      return {id: requireNonEmptyString(task.id, "task id"),
        service_id: requireNonEmptyString(task.service_id, "service id"),
        ...projectBackgroundTask(observation(task), now)};
    })};
}

export type BackgroundServicesSnapshot = ReturnType<typeof projectBackgroundServices>;
