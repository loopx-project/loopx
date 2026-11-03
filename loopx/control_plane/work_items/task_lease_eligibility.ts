/** Owner eligibility is shared by acquisition, renewal, transfer and observation.
 * It is not a lease grant: time, mode, proof, CAS and release remain with callers. */

export interface LeaseEligibilityTodo {
  readonly status: string;
  readonly claimed_by: string | null;
  readonly excluded_agents: readonly string[];
  readonly task_class?: string | null;
}

export type LeaseOwnerRejection = "todo_not_found" | "todo_not_open" | "invalid_owner" |
  "owner_not_registered" | "owner_excluded_from_todo" | "owner_conflicts_with_claim" | "todo_not_executable";

/** User actions are administrative reminders. Retained lease history cannot
 * turn them into executable work; exact release remains a separate operation. */
export function leaseExecutionTargetRejection(todo: {readonly task_class?: unknown}):
  "todo_not_executable" | null {
  return todo.task_class === "user_action" ? "todo_not_executable" : null;
}

export function leaseOwnerRejection(todo: LeaseEligibilityTodo | null | undefined,
  owner: string | null, registered: readonly string[]): LeaseOwnerRejection | null {
  if (todo == null) return "todo_not_found";
  if (todo.status !== "open") return "todo_not_open";
  if (!owner) return "invalid_owner";
  if (!registered.includes(owner)) return "owner_not_registered";
  if (todo.excluded_agents.includes(owner)) return "owner_excluded_from_todo";
  if (todo.claimed_by && todo.claimed_by !== owner) return "owner_conflicts_with_claim";
  return leaseExecutionTargetRejection(todo);
}

/** Diagnostics share the exact rejection precedence used by mutation admission. */
export function leaseOwnerConstraint(todo: LeaseEligibilityTodo | null | undefined,
  owner: string | null, registered: readonly string[]) {
  const reason = leaseOwnerRejection(todo, owner, registered);
  switch (reason) {
    case null: return {effective: true as const};
    case "todo_not_open":
      return {effective: false as const, reason, todo_status: todo?.status || "unknown"};
    case "owner_not_registered":
      return {effective: false as const, reason, registered_agents: [...registered]};
    case "owner_excluded_from_todo":
      return {effective: false as const, reason, excluded_agents: [...(todo?.excluded_agents ?? [])]};
    case "owner_conflicts_with_claim":
      return {effective: false as const, reason, claimed_by: todo?.claimed_by ?? null};
    default: return {effective: false as const, reason};
  }
}
