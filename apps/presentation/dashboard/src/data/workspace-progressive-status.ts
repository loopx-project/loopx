import { z } from "zod";
import { parseStatusPayload, type StatusPayload } from "./status";

const directorySchema = z.object({
  ok: z.literal(true),
  schema_version: z.literal("loopx_workspace_directory_v1"),
  registry_revision: z.string(),
  goals: z.array(z.object({
    id: z.string(),
    display_name: z.string(),
    activation_state: z.enum(["active", "stopped"]),
    registry_member: z.literal(true),
  })),
});
export type WorkspaceDirectory = z.infer<typeof directorySchema>;
export type WorkspaceLoadError = "timeout" | "network" | "service" | "access" | "revision" | "scope" | "invalid";
export type WorkspaceReadScope = "all" | "missing";
export type WorkspaceProgress = {
  directory: WorkspaceDirectory;
  snapshots: Record<string, StatusPayload>;
  errors: Record<string, WorkspaceLoadError>;
};

/**
 * Snapshots a same-source read may keep visible while it reconciles.
 *
 * A directory entry is the cheap authoritative signal for "this Goal's
 * lifecycle did not move here". It does not establish Todo freshness: a full
 * refresh still re-reads retained Goals. A partial retry or known action may
 * skip unaffected peers. Touched Goals and entries that moved or left the
 * directory lose their snapshot.
 */
export function reusableGoalSnapshots(
  previous: Pick<WorkspaceProgress, "directory" | "snapshots"> | null,
  directory: WorkspaceDirectory,
  options: { invalidateGoalIds?: Iterable<string> } = {},
): Record<string, StatusPayload> {
  if (!previous) return {};
  const invalidated = new Set(options.invalidateGoalIds ?? []);
  const before = new Map(previous.directory.goals.map((goal) => [goal.id, goal]));
  return Object.fromEntries(directory.goals.flatMap((goal) => {
    const earlier = before.get(goal.id);
    const snapshot = previous.snapshots[goal.id];
    if (!earlier || !snapshot || invalidated.has(goal.id)) return [];
    if (earlier.display_name !== goal.display_name
      || earlier.activation_state !== goal.activation_state) return [];
    return [[goal.id, snapshot]];
  }));
}

/** Display retention and request selection are separate decisions. */
export function workspaceReadPlan(
  previous: Pick<WorkspaceProgress, "directory" | "snapshots"> | null,
  directory: WorkspaceDirectory,
  scope: WorkspaceReadScope = "all",
  options: { invalidateGoalIds?: Iterable<string> } = {},
) {
  const snapshots = reusableGoalSnapshots(previous, directory, options);
  return {
    snapshots,
    requestedDirectory: { ...directory, goals: directory.goals.filter(
      (goal) => scope === "all" || !snapshots[goal.id],
    ) },
  };
}

function queryUrl(url: string, fields: Record<string, string>, base: string) {
  const parsed = new URL(url, base);
  parsed.searchParams.delete("goal_activation");
  parsed.searchParams.delete("goal_id");
  parsed.searchParams.delete("view");
  for (const [key, value] of Object.entries(fields)) parsed.searchParams.set(key, value);
  return parsed.toString();
}

export async function fetchWorkspaceDirectory(url: string, base: string): Promise<WorkspaceDirectory | null> {
  const response = await fetch(queryUrl(url, { view: "workspace-directory" }, base), {
    cache: "no-store", signal: AbortSignal.timeout(5_000),
  });
  // Older/read-only status servers retain their original full-payload path.
  if (!response.ok) return null;
  const result = directorySchema.safeParse(await response.json());
  return result.success ? result.data : null;
}

export function directoryStatusPayload(directory: WorkspaceDirectory): StatusPayload {
  return parseStatusPayload({
    ok: true, registry: "", runtime_root: "", goal_count: directory.goals.length,
    run_count: 0, local_dashboard_api: {},
    contract: { ok: true, summary: { errors: 0, warnings: 0, checks: 0 }, errors: [], warnings: [] },
    attention_queue: { available: false, item_count: 0, needs_user_or_controller: 0,
      needs_codex: 0, watching_external_evidence: 0, items: [] },
    run_history: { available: false, goal_count: directory.goals.length, run_count: 0,
      goals: directory.goals, recent_runs: [] },
  });
}

async function statusFailure(response: Response, signal: AbortSignal): Promise<"access" | "service"> {
  if (!response.body) return "service";
  const reader = response.body.getReader();
  const cancel = () => { void reader.cancel().catch(() => {}); };
  signal.addEventListener("abort", cancel, { once: true });
  // Error bodies may come from older servers or proxies. Never buffer them unboundedly.
  const bytes = new Uint8Array(16 * 1024);
  let size = 0;
  try {
    signal.throwIfAborted();
    while (true) {
      const { done, value } = await reader.read();
      signal.throwIfAborted();
      if (done) break;
      if (size + value.byteLength > bytes.byteLength) return "service";
      bytes.set(value, size);
      size += value.byteLength;
    }
    const payload: unknown = JSON.parse(new TextDecoder().decode(bytes.subarray(0, size)));
    return payload !== null && typeof payload === "object" && !Array.isArray(payload)
      && "error_code" in payload && payload.error_code === "workspace_status_access_denied"
      ? "access" : "service";
  } catch {
    // Parsing/transport errors after 5xx headers stay service errors; aborts keep their meaning.
    signal.throwIfAborted();
    return "service";
  } finally {
    signal.removeEventListener("abort", cancel);
    cancel();
    reader.releaseLock();
  }
}

/** Bounded fan-out: a slow/failed Goal cannot block the directory or its peers. */
export async function loadWorkspaceGoalSnapshots(
  url: string,
  base: string,
  directory: WorkspaceDirectory,
  onGoal: (id: string, payload: StatusPayload | null, error: WorkspaceLoadError | null) => void,
  isCurrent: () => boolean,
  preferredGoal: () => string,
  signal?: AbortSignal,
) {
  const pending = [...directory.goals];
  const attempts = new Map<string, number>();
  async function worker() {
    while (pending.length && isCurrent() && !signal?.aborted) {
      const preferred = pending.findIndex((goal) => goal.id === preferredGoal());
      const index = preferred >= 0 ? preferred : pending.findIndex((goal) => goal.activation_state === "active");
      // Stopped Goal names are already visible. Read their history on selection.
      if (index < 0) return;
      const goal = pending.splice(index, 1)[0];
      const attempt = (attempts.get(goal.id) ?? 0) + 1;
      attempts.set(goal.id, attempt);
      const controller = new AbortController();
      let timedOut = false;
      const cancel = () => controller.abort();
      signal?.addEventListener("abort", cancel, { once: true });
      const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 30_000);
      let failure: WorkspaceLoadError | null = null;
      try {
        const response = await fetch(queryUrl(url, { goal_id: goal.id }, base), {
          cache: "no-store", signal: controller.signal,
        });
        if (!response.ok) {
          failure = response.status === 409 ? "revision" : response.status >= 500
            ? await statusFailure(response, controller.signal) : "scope";
          controller.signal.throwIfAborted();
        } else {
          const raw = await response.json();
          if (raw.workspace_registry_revision !== directory.registry_revision) failure = "revision";
          else {
            const payload = parseStatusPayload(raw);
            if (!payload.run_history.goals.some((item) => item.id === goal.id)
              || payload.run_history.goals.some((item) => item.id !== goal.id)) failure = "scope";
            else if (isCurrent() && !signal?.aborted) onGoal(goal.id, payload, null);
          }
        }
      } catch (error) {
        failure = timedOut ? "timeout" : error instanceof TypeError ? "network" : "invalid";
      } finally {
        clearTimeout(timeout);
        signal?.removeEventListener("abort", cancel);
      }
      if (!isCurrent() || signal?.aborted) return;
      if (failure && ["timeout", "network", "service"].includes(failure) && attempt < 3) {
        // Peers can continue during bounded retry backoff after a restart.
        await new Promise((resolve) => setTimeout(resolve, attempt * 1_000));
        pending.push(goal);
      } else if (failure) onGoal(goal.id, null, failure);
    }
  }
  await Promise.all([worker(), worker()]);
}
