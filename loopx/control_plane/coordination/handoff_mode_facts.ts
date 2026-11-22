/** Quiescence is a decision over complete ownership facts, never a display page. */
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {parseIsoTimestamp} from "../runtime_timestamp.ts";
import {leaseIsActive, TASK_LEASE_SCHEMA_VERSION} from "../work_items/task_lease_acquire.ts";
import {type HandoffMode} from "./handoff_mode_policy.ts";

export type PersistedHandoffMode =
  | {kind: "valid"; value: HandoffMode}
  | {kind: "invalid"; value: string};

export interface HandoffQuiescence {
  claimed_todos: JsonObject[];
  active_leases: JsonObject[];
}

export function handoffQuiescence(todos: readonly JsonObject[], leases: readonly JsonObject[],
  observedAt: string): HandoffQuiescence {
  const now = parseIsoTimestamp(observedAt);
  if (!now) throw new Error("observed_at must be a valid ISO timestamp");
  const claimed: JsonObject[] = [];
  for (const value of todos) {
    const todo = requireJsonObject(value, "handoff Todo");
    if (todo.archive_state === "archive" || todo.done === true) continue;
    if (todo.claimed_by == null || todo.claimed_by === "") continue;
    if (typeof todo.claimed_by !== "string") throw new Error("Todo claimed_by must be a string or null");
    const owner = todo.claimed_by.trim();
    if (owner) claimed.push({todo_id: todo.todo_id ?? null, claimed_by: owner, status: todo.status ?? null});
  }
  const active: JsonObject[] = [];
  for (const value of leases) {
    const lease = requireJsonObject(value, "handoff lease");
    if (lease.schema_version !== TASK_LEASE_SCHEMA_VERSION) throw new Error("lease schema mismatch");
    if (leaseIsActive(lease, now)) active.push({todo_id: lease.todo_id ?? null,
      owner: lease.owner ?? null, expires_at: lease.expires_at ?? null,
      ...(typeof lease.lease_path === "string" ? {lease_path: lease.lease_path} : {})});
  }
  return {claimed_todos: claimed, active_leases: active};
}
