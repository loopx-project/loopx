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
