/** Repository-relative scope identity. Unknown historical grants stay global.
 * This is a Goal-local logical mutex, not a physical filesystem alias fence. */
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {normalizeTodoRepository} from "../todos/work_requirements.ts";

export function leaseWriteRepository(value: unknown): string | null {
  if (value == null) return null;
  const repository = normalizeTodoRepository(value, "lease.write_repository");
  if (repository === null || repository !== value) {
    throw new EffectRuntimeRequestError("lease.write_repository must be a canonical repository identity or null");
  }
  return repository;
}

export function repositoryScopesMayOverlap(left: unknown, right: unknown): boolean {
  const a = leaseWriteRepository(left), b = leaseWriteRepository(right);
  // Case aliases must not create a second lock on case-insensitive hosts.
  return a === null || b === null || a.toLowerCase() === b.toLowerCase();
}

export function leaseRepositoryRejection(todo: {task_repository?: unknown} | null | undefined,
  lease: {write_repository?: unknown} | null | undefined): string | null {
  const frozen = leaseWriteRepository(lease?.write_repository);
  if (frozen === null) return null;
  const current = normalizeTodoRepository(todo?.task_repository);
  return current !== null && frozen.toLowerCase() === current.toLowerCase()
    ? null : "lease_repository_divergence";
}

/** A released owner's grant is immutable history during inert administration.
 * Actor/claim admission and any explicit execution proof are checked by the
 * caller. Execution, foreign history and unreleased drift keep the strict fence. */
export function ownerAdministrativeRepositoryRejection(
  todo: {task_repository?: unknown; claimed_by?: unknown},
  lease: {write_repository?: unknown; status?: unknown; owner?: unknown},
  actorAgentId: string | null,
): string | null {
  const rejection = leaseRepositoryRejection(todo, lease); // Validate history before any exception.
  return rejection !== null && lease.status === "released" && actorAgentId !== null &&
    lease.owner === actorAgentId && todo.claimed_by === actorAgentId ? null : rejection;
}
