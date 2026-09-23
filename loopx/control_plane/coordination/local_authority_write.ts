import {withFileMutationLock} from "../effect_runtime_io.ts";
import {requireShadowPrimaryWriteAllowed, shadowMaintenanceLockPath} from "./shadow_management.ts";

// File-v0 still verifies and republishes its retained history on each write.
// Give a competing canonical writer a bounded wait for that real critical
// section; the provider CAS and hard lease remain the decision authority.
const CANONICAL_WRITER_LOCK_TIMEOUT_MS = 30_000;

/** Canonical command writers share the maintenance guard; provider CAS owns state. */
export async function withCanonicalWriter<T>(root: string, goalId: string, dryRun: boolean, write: () => Promise<T>): Promise<T> {
  if (dryRun) return await write();
  return await withFileMutationLock(shadowMaintenanceLockPath(root, goalId), async () => {
    await requireShadowPrimaryWriteAllowed(root, goalId);
    return await write();
  }, CANONICAL_WRITER_LOCK_TIMEOUT_MS);
}
