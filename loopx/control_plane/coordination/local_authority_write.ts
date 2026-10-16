import {withFileMutationLock} from "../effect_runtime_io.ts";
import type {JsonObject} from "../effect_program.ts";
import {loadLegacyCoordinationWriterFence} from "./legacy_writer_fence.ts";
import {requireShadowPrimaryWriteAllowed, shadowMaintenanceLockPath} from "./shadow_management.ts";

// File-v0 still verifies and republishes its retained history on each write.
// Give a competing canonical writer a bounded wait for that real critical
// section; the provider CAS and hard lease remain the decision authority.
const CANONICAL_WRITER_LOCK_TIMEOUT_MS = 30_000;

export type CanonicalBusinessWrite =
  | "todo_claim"
  | "todo_create"
  | "todo_update"
  | "todo_terminal"
  | "todo_archive"
  | "monitor_poll"
  | "team_plan"
  | "handoff_mode_set"
  | "task_lease_acquire"
  | "task_lease_renew"
  | "task_lease_transfer"
  | "task_lease_release"
  | "goal_create";

export class CanonicalWriteRouteError extends Error {
  readonly code: "canonical_authority_required" | "canonical_authority_fence_invalid";
  readonly payload: JsonObject;

  constructor(code: CanonicalWriteRouteError["code"], goalId: string, operation: CanonicalBusinessWrite, reason: string) {
    super(reason);
    this.name = "CanonicalWriteRouteError";
    this.code = code;
    this.payload = {
      status: "failed",
      reason_code: code,
      goal_id: goalId,
      operation,
      write_admitted: false,
      decision_read_from_provider: false,
      legacy_fallback_used: false,
      recovery: {
        action: code === "canonical_authority_required"
          ? "migrate_goal_authority"
          : "repair_canonical_authority_fence",
        backup_required: true,
      },
    };
  }
}

export function canonicalWriteRouteFailure(error: unknown): JsonObject {
  return error instanceof CanonicalWriteRouteError ? {...error.payload, reason: error.message} : {};
}

interface CanonicalWriterOptions {
  businessWrite?: CanonicalBusinessWrite;
}

/** Call only while holding the Goal maintenance lock. */
export async function requireCanonicalBusinessWrite(
  root: string,
  goalId: string,
  operation: CanonicalBusinessWrite,
): Promise<void> {
  const fence = await loadLegacyCoordinationWriterFence(root, goalId);
  if (fence.status === "missing") {
    throw new CanonicalWriteRouteError(
      "canonical_authority_required",
      goalId,
      operation,
      "Canonical authority is required; back up and migrate this Goal before retrying",
    );
  }
  if (fence.status === "failed") {
    throw new CanonicalWriteRouteError(
      "canonical_authority_fence_invalid",
      goalId,
      operation,
      fence.reason,
    );
  }
}

/** Canonical command writers share the maintenance guard; provider CAS owns state. */
export async function withCanonicalWriter<T>(
  root: string,
  goalId: string,
  _dryRun: boolean,
  write: () => Promise<T>,
  options: CanonicalWriterOptions = {},
): Promise<T> {
  return await withFileMutationLock(shadowMaintenanceLockPath(root, goalId), async () => {
    await requireShadowPrimaryWriteAllowed(root, goalId);
    if (options.businessWrite !== undefined) {
      await requireCanonicalBusinessWrite(root, goalId, options.businessWrite);
    }
    return await write();
  }, CANONICAL_WRITER_LOCK_TIMEOUT_MS);
}
