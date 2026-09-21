import { createHash } from "node:crypto";
import { readFile, realpath, stat } from "node:fs/promises";
import { isAbsolute, join } from "node:path";

import type { JsonObject } from "../effect_program.ts";
import { atomicWriteJson, withFileMutationLock } from "../effect_runtime_io.ts";
import { requireJsonObject } from "../runtime_decode.ts";
import { readShadowBootstrapSourcePath, requireShadowPrimaryWriteAllowed, ShadowManagementError, shadowMaintenanceLockPath } from "./shadow_management.ts";
import { legacyCoordinationTodoLockPath, legacyCoordinationLeaseLockPath, taskLeaseLockPath } from "./legacy_writer_lock_paths.ts";
export { legacyCoordinationTodoLockPath, legacyCoordinationLeaseLockPath,
  LEGACY_COORDINATION_TODO_LOCK_KEY, LEGACY_COORDINATION_LEASE_LOCK_KEY } from "./legacy_writer_lock_paths.ts";

import {
  canonicalAuthorityBytes,
  canonicalAuthorityObject,
  requireAuthorityStoreId,
} from "./authority_store_codec.ts";
import {
  LEGACY_COORDINATION_WRITER_FENCE_ENGAGE_REQUEST_SCHEMA,
  LEGACY_COORDINATION_WRITER_FENCE_RESULT_SCHEMA,
  LEGACY_COORDINATION_WRITER_FENCE_SCHEMA,
  LEGACY_COORDINATION_WRITE_CHECK_REQUEST_SCHEMA,
  LEGACY_COORDINATION_WRITE_CHECK_RESULT_SCHEMA,
} from "./coordination_state_contract.generated.ts";

export {
  LEGACY_COORDINATION_WRITER_FENCE_ENGAGE_REQUEST_SCHEMA,
  LEGACY_COORDINATION_WRITER_FENCE_RESULT_SCHEMA,
  LEGACY_COORDINATION_WRITER_FENCE_SCHEMA,
  LEGACY_COORDINATION_WRITE_CHECK_REQUEST_SCHEMA,
  LEGACY_COORDINATION_WRITE_CHECK_RESULT_SCHEMA,
};

// Caller adapter: remediation is rendered here, never inside
// checkLegacyCoordinationWriteAllowed, which owns only the stable typed reason
// and the fence binding facts.  Tokens are substituted in one pass, so a data
// value is never re-scanned for tokens.  Keep byte-identical with the Python
// LEGACY_WRITER_FENCED_REMEDIATION.
export const LEGACY_WRITER_FENCED_REMEDIATION =
  "legacy coordination writer is fenced; use the promoted canonical authority ({authority_mode}) for goal {goal_id}; fence {fence_id}; the primary record was not changed";

function guardText(value: unknown, fallback: string): string {
  return typeof value === "string" && value !== "" ? value : fallback;
}

/** Operator-facing text for a non-allowed write check; the machine reason stays `reason_code`. */
export function legacyCoordinationWriteRemediation(goalId: string, guard: JsonObject): string {
  if (guard.status !== "blocked") {
    return guardText(guard.reason, "legacy coordination writer fence check failed");
  }
  const values: Record<string, string> = {
    authority_mode: guardText(guard.authority_mode, "unknown_fail_closed"),
    goal_id: goalId,
    fence_id: guardText(guard.fence_id, "unknown"),
  };
  return LEGACY_WRITER_FENCED_REMEDIATION.replace(
    /\{(authority_mode|goal_id|fence_id)\}/g,
    (_match, token: string) => values[token],
  );
}

export class LegacyCoordinationWriteError extends Error {
  code: string;
  write_check: JsonObject;
  payload: JsonObject;
  constructor(code: string, writeCheck: JsonObject, message: string) {
    super(message);
    this.code = code;
    this.write_check = writeCheck;
    // The complete check result travels under its contract name; spreading it
    // into a caller envelope must never overwrite the envelope's own keys.
    this.payload = { write_check: writeCheck };
  }
}

/** Call under the existing primary lock, before receipts, capture or bytes. */
export async function requireLegacyCoordinationPrimaryWriteAllowed(root: string, goalId: string): Promise<void> {
  await requireShadowPrimaryWriteAllowed(root, goalId);
  const guard = await checkLegacyCoordinationWriteAllowed({
    schema_version: LEGACY_COORDINATION_WRITE_CHECK_REQUEST_SCHEMA,
    runtime_root: root, goal_id: goalId,
  });
  if (guard.status !== "allowed") {
    throw new LegacyCoordinationWriteError(
      String(guard.reason_code ?? "legacy_writer_fence_check_failed"),
      guard,
      legacyCoordinationWriteRemediation(goalId, guard),
    );
  }
}

function runtimeRoot(value: unknown): string {
  if (typeof value !== "string" || value.trim() !== value || !isAbsolute(value)) {
    throw new Error("runtime_root must be an absolute path");
  }
  return value;
}

export function legacyCoordinationWriterFencePath(root: string, goalId: string): string {
  const digest = createHash("sha256").update(goalId, "utf8").digest("hex").slice(0, 16);
  return join(root, "authority-transition", "file-v0", `legacy-writer-fence-${digest}.json`);
}

export function decodeLegacyCoordinationWriterFence(value: unknown): JsonObject {
  const fence = canonicalAuthorityObject(value, "legacy coordination writer fence");
  if (
    fence.schema_version !== LEGACY_COORDINATION_WRITER_FENCE_SCHEMA ||
    fence.state !== "engaged"
  ) throw new Error("legacy coordination writer fence must be engaged");
  return canonicalAuthorityObject({
    schema_version: LEGACY_COORDINATION_WRITER_FENCE_SCHEMA,
    state: "engaged",
    goal_id: requireAuthorityStoreId(fence.goal_id, "writer fence goal id"),
    fence_id: requireAuthorityStoreId(fence.fence_id, "writer fence id"),
    source_version: requireAuthorityStoreId(
      fence.source_version,
      "writer fence source version",
    ),
    source_projection_sha256: requireAuthorityStoreId(
      fence.source_projection_sha256,
      "writer fence source projection sha256",
    ),
    expected_shadow_provider_revision: requireAuthorityStoreId(
      fence.expected_shadow_provider_revision,
      "writer fence expected shadow provider revision",
    ),
  }, "legacy coordination writer fence");
}

export async function loadLegacyCoordinationWriterFence(
  root: string,
  goalId: string,
): Promise<{ status: "missing" } | { status: "loaded"; fence: JsonObject } | {
  status: "failed";
  reason_code: string;
  reason: string;
}> {
  try {
    const raw = await readFile(legacyCoordinationWriterFencePath(runtimeRoot(root), goalId), "utf8");
    const fence = decodeLegacyCoordinationWriterFence(JSON.parse(raw));
    if (fence.goal_id !== goalId) throw new Error("legacy writer fence goal mismatch");
    return { status: "loaded", fence };
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return { status: "missing" };
    const path = (error as NodeJS.ErrnoException).path;
    const reason = error instanceof Error ? error.message : "legacy writer fence read failed";
    return {
      status: "failed",
      reason_code: "legacy_writer_fence_read_failed",
      reason: typeof path === "string" ? reason.replace(` '${path}'`, "") : reason,
    };
  }
}

/** Persist one exact fence while the caller already owns maintenance/source locks. */
export async function engageLegacyCoordinationWriterFenceUnderLocks(
  root: string,
  goalId: string,
  statePath: string,
  fence: JsonObject,
): Promise<JsonObject> {
  const binding = await requireShadowPrimaryWriteAllowed(root, goalId);
  if (
    binding !== null &&
    await realpath(await readShadowBootstrapSourcePath(root, goalId, binding)) !== await realpath(statePath)
  ) throw new ShadowManagementError("shadow_source_state_path_mismatch");
  const path = legacyCoordinationWriterFencePath(root, goalId);
  return await withFileMutationLock(path, async () => {
    const existing = await loadLegacyCoordinationWriterFence(root, goalId);
    if (existing.status === "loaded") {
      const matched = canonicalAuthorityBytes(existing.fence).equals(
        canonicalAuthorityBytes(fence),
      );
      return {
        schema_version: LEGACY_COORDINATION_WRITER_FENCE_RESULT_SCHEMA,
        status: matched ? "replayed" : "conflict",
        ...(matched ? { fence } : {
          reason_code: "legacy_writer_fence_identity_mismatch",
          reason: "a different legacy writer fence is already engaged",
        }),
      };
    }
    if (existing.status === "failed") return {
      schema_version: LEGACY_COORDINATION_WRITER_FENCE_RESULT_SCHEMA,
      ...existing,
    };
    await atomicWriteJson(path, fence);
    const readback = await loadLegacyCoordinationWriterFence(root, goalId);
    if (
      readback.status !== "loaded" ||
      !canonicalAuthorityBytes(readback.fence).equals(canonicalAuthorityBytes(fence))
    ) throw new Error("legacy writer fence readback mismatch");
    return {
      schema_version: LEGACY_COORDINATION_WRITER_FENCE_RESULT_SCHEMA,
      status: "applied",
      fence,
    };
  });
}

/** Persist the fail-closed marker that every legacy writer must inspect. */
export async function engageLegacyCoordinationWriterFence(value: unknown): Promise<JsonObject> {
  try {
    const input = requireJsonObject(value, "legacy coordination writer fence request");
    if (input.schema_version !== LEGACY_COORDINATION_WRITER_FENCE_ENGAGE_REQUEST_SCHEMA) {
      throw new Error("legacy coordination writer fence request schema mismatch");
    }
    const root = runtimeRoot(input.runtime_root);
    const goalId = requireAuthorityStoreId(input.goal_id, "goal id");
    if (typeof input.state_path !== "string" || input.state_path.trim() !== input.state_path
        || !isAbsolute(input.state_path) || input.state_path.includes("\0")) {
      throw new Error("state_path must be the absolute source state file path");
    }
    const statePath = await realpath(input.state_path);
    if (!(await stat(statePath)).isFile()) throw new Error("state_path must identify an existing source state file");
    const fence = decodeLegacyCoordinationWriterFence(input.fence);
    if (fence.goal_id !== goalId) throw new Error("legacy writer fence goal mismatch");
    return await withFileMutationLock(shadowMaintenanceLockPath(root, goalId), async () => {
      return withFileMutationLock(legacyCoordinationTodoLockPath(root, goalId), () =>
        withFileMutationLock(statePath, () =>
          withFileMutationLock(legacyCoordinationLeaseLockPath(root, goalId), () =>
            withFileMutationLock(taskLeaseLockPath({runtime_root: root, goal_id: goalId}), () =>
              engageLegacyCoordinationWriterFenceUnderLocks(root, goalId, statePath, fence),
            ),
          ),
        ),
      );
    });
  } catch (error) {
    return {
      schema_version: LEGACY_COORDINATION_WRITER_FENCE_RESULT_SCHEMA,
      status: "failed",
      reason_code: error instanceof ShadowManagementError ? error.reason_code : "invalid_legacy_writer_fence_request",
      reason: error instanceof Error ? error.message : "invalid writer fence request",
    };
  }
}

/**
 * Shared guard for legacy Todo/task-lease writers. Missing means legacy mode;
 * a valid engaged marker blocks the write; unreadable state fails closed.
 */
export async function checkLegacyCoordinationWriteAllowed(value: unknown): Promise<JsonObject> {
  try {
    const input = requireJsonObject(value, "legacy coordination write check request");
    if (input.schema_version !== LEGACY_COORDINATION_WRITE_CHECK_REQUEST_SCHEMA) {
      throw new Error("legacy coordination write check request schema mismatch");
    }
    const root = runtimeRoot(input.runtime_root);
    const goalId = requireAuthorityStoreId(input.goal_id, "goal id");
    const result = await loadLegacyCoordinationWriterFence(root, goalId);
    if (result.status === "missing") return {
      schema_version: LEGACY_COORDINATION_WRITE_CHECK_RESULT_SCHEMA,
      status: "allowed",
      authority_mode: "legacy_canonical",
    };
    if (result.status === "loaded") return {
      schema_version: LEGACY_COORDINATION_WRITE_CHECK_RESULT_SCHEMA,
      status: "blocked",
      reason_code: "legacy_coordination_writer_fenced",
      authority_mode: "file_v0",
      fence_id: result.fence.fence_id,
    };
    return {
      schema_version: LEGACY_COORDINATION_WRITE_CHECK_RESULT_SCHEMA,
      ...result,
      status: "failed",
      authority_mode: "unknown_fail_closed",
    };
  } catch (error) {
    return {
      schema_version: LEGACY_COORDINATION_WRITE_CHECK_RESULT_SCHEMA,
      status: "failed",
      reason_code: "invalid_legacy_coordination_write_check",
      reason: error instanceof Error ? error.message : "invalid legacy write check",
      authority_mode: "unknown_fail_closed",
    };
  }
}
