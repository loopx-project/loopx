import { createHash } from "node:crypto";
import { dirname, isAbsolute, join, resolve } from "node:path";

import type { JsonObject } from "../effect_program.ts";
import {
  EffectRuntimeConflictError,
  EffectRuntimeRequestError,
} from "../effect_runtime_errors.ts";
import {
  claimFileMutationLock,
  mutationLockOwner,
  releaseFileMutationLock,
  releaseFileMutationLockClaim,
  type FileMutationLockClaim,
} from "../effect_runtime_io.ts";
import { decideFirstPartyHostRuntime } from "../goals/first_party_host_runtime.ts";
import {
  parseExactGoalRef,
  type ExactGoalRef,
} from "../goals/goal_instance_identity.ts";
import {
  requireJsonObject,
  requireNonEmptyString,
} from "../runtime_decode.ts";

const QUOTA_SOURCE_ADMISSION_SCHEMA = "loopx_quota_source_admission_v0";
const SOURCE_SESSION_PROFILE_ID = "source_session_v1";

type LockRole = "run_index" | "source_guard";

interface QuotaSourceLock {
  role: LockRole;
  target: string;
  pid: number;
  token: string;
}

interface QuotaSourceAdmission {
  registryPath: string;
  plannedGoalRef: ExactGoalRef;
  authority: unknown;
  locks: readonly [QuotaSourceLock, QuotaSourceLock];
}

export type QuotaAccountingOwner =
  | Readonly<{ kind: "alias" }>
  | Readonly<{
    kind: "exact_source";
    goalRef: ExactGoalRef;
    admission: QuotaSourceAdmission;
  }>;

interface ClaimedLock {
  witness: QuotaSourceLock;
  claim: FileMutationLockClaim;
}

export function quotaAccountingOwnerLocks(
  owner: QuotaAccountingOwner,
): readonly Readonly<QuotaSourceLock>[] {
  return owner.kind === "alias" ? [] : owner.admission.locks;
}

function sha256(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function sourceGuardTarget(registryPath: string, goalId: string): string {
  return join(
    dirname(registryPath),
    ".loopx",
    "lifecycle",
    "goal-instance",
    "guards",
    `${sha256(goalId)}.guard`,
  );
}

function sameGoalRef(left: ExactGoalRef, right: ExactGoalRef): boolean {
  return left.goalId.value === right.goalId.value
    && left.goalInstanceId.value === right.goalInstanceId.value;
}

export function quotaGoalRef(owner: QuotaAccountingOwner): JsonObject | null {
  if (owner.kind === "alias") return null;
  return {
    goal_id: owner.goalRef.goalId.value,
    goal_instance_id: owner.goalRef.goalInstanceId.value,
  };
}

export function requireQuotaOwnerProjection(
  owner: QuotaAccountingOwner,
  value: unknown,
  label: string,
): void {
  if (owner.kind === "alias") {
    if (value === undefined) return;
    throw new EffectRuntimeConflictError(
      `${label} belongs to an exact Goal instance and cannot be consumed without GoalRef admission`,
      "goal_instance_conflict",
    );
  }
  const projected = parseExactGoalRef(value);
  if (
    projected.kind === "invalid"
    || !sameGoalRef(projected.value, owner.goalRef)
  ) {
    throw new EffectRuntimeConflictError(
      `${label} does not belong to the admitted Goal instance`,
      "goal_instance_conflict",
    );
  }
}

export function quotaOwnerOwnsProjection(
  owner: QuotaAccountingOwner,
  value: unknown,
): boolean {
  if (owner.kind === "alias") return value === undefined;
  const projected = parseExactGoalRef(value);
  return projected.kind === "parsed"
    && sameGoalRef(projected.value, owner.goalRef);
}

function quotaSourceLock(
  value: unknown,
  role: LockRole,
  expectedTarget: string,
): QuotaSourceLock {
  const witness = requireJsonObject(value, `${role} lock witness`);
  if (witness.role !== role) {
    throw new EffectRuntimeRequestError(
      `quota source admission ${role} lock role mismatch`,
      "quota_source_admission_invalid",
    );
  }
  const target = requireNonEmptyString(
    witness.target,
    `${role} lock target`,
  );
  if (
    !isAbsolute(target)
    || resolve(target) !== target
    || target !== expectedTarget
  ) {
    throw new EffectRuntimeRequestError(
      `quota source admission ${role} lock target mismatch`,
      "quota_source_admission_invalid",
    );
  }
  if (
    typeof witness.pid !== "number"
    || !Number.isSafeInteger(witness.pid)
    || witness.pid <= 0
  ) {
    throw new EffectRuntimeRequestError(
      `quota source admission ${role} lock owner is invalid`,
      "quota_source_admission_invalid",
    );
  }
  return {
    role,
    target,
    pid: witness.pid,
    token: requireNonEmptyString(witness.token, `${role} lock token`),
  };
}

export function parseQuotaAccountingOwner(
  {
    goalRefValue,
    sourceAdmissionValue,
    runtimeRoot,
    goalId,
  }: {
  goalRefValue: unknown;
  sourceAdmissionValue: unknown;
  runtimeRoot: string;
  goalId: string;
  },
): QuotaAccountingOwner {
  const hasGoalRef = goalRefValue !== undefined;
  const hasAdmission = sourceAdmissionValue !== undefined;
  if (!hasGoalRef && !hasAdmission) return { kind: "alias" };
  if (!hasGoalRef || !hasAdmission) {
    throw new EffectRuntimeRequestError(
      "exact quota GoalRef and source admission must be supplied together",
      "quota_source_admission_invalid",
    );
  }

  const goalRef = parseExactGoalRef(goalRefValue);
  if (
    goalRef.kind === "invalid"
    || goalRef.value.goalId.value !== goalId
  ) {
    throw new EffectRuntimeRequestError(
      "quota GoalRef is invalid or does not match goal_id",
      "quota_source_admission_invalid",
    );
  }
  const admission = requireJsonObject(
    sourceAdmissionValue,
    "quota source admission",
  );
  if (
    admission.schema_version !== QUOTA_SOURCE_ADMISSION_SCHEMA
    || admission.profile_id !== SOURCE_SESSION_PROFILE_ID
  ) {
    throw new EffectRuntimeRequestError(
      "quota source admission is malformed",
      "quota_source_admission_invalid",
    );
  }
  const registryPath = requireNonEmptyString(
    admission.registry_path,
    "quota source admission registry_path",
  );
  if (!isAbsolute(registryPath) || resolve(registryPath) !== registryPath) {
    throw new EffectRuntimeRequestError(
      "quota source admission registry path must be absolute and normalized",
      "quota_source_admission_invalid",
    );
  }
  const plannedGoalRef = parseExactGoalRef(admission.planned_goal_ref);
  if (
    plannedGoalRef.kind === "invalid"
    || !sameGoalRef(plannedGoalRef.value, goalRef.value)
  ) {
    throw new EffectRuntimeRequestError(
      "quota source admission does not match the requested GoalRef",
      "quota_source_admission_invalid",
    );
  }
  if (!Array.isArray(admission.locks) || admission.locks.length !== 2) {
    throw new EffectRuntimeRequestError(
      "quota source admission requires both ordered lock witnesses",
      "quota_source_admission_invalid",
    );
  }
  const indexTarget = resolve(
    join(runtimeRoot, "goals", goalId, "runs", "index.jsonl"),
  );
  const guardTarget = resolve(sourceGuardTarget(registryPath, goalId));
  const runIndex = quotaSourceLock(
    admission.locks[0],
    "run_index",
    indexTarget,
  );
  const sourceGuard = quotaSourceLock(
    admission.locks[1],
    "source_guard",
    guardTarget,
  );
  return {
    kind: "exact_source",
    goalRef: goalRef.value,
    admission: {
      registryPath,
      plannedGoalRef: plannedGoalRef.value,
      authority: admission.authority,
      locks: [runIndex, sourceGuard],
    },
  };
}

export async function withQuotaAccountingOwner<T>(
  owner: QuotaAccountingOwner,
  operation: (indexLockHeld: boolean) => Promise<T>,
): Promise<T> {
  if (owner.kind === "alias") return await operation(false);

  const claims: ClaimedLock[] = [];
  let adopted = false;
  try {
    for (const witness of owner.admission.locks) {
      const claim = await claimFileMutationLock(
        witness.target,
        witness.token,
      );
      if (!claim) {
        throw new EffectRuntimeConflictError(
          "quota source admission lock handoff expired",
          "quota_source_admission_expired",
        );
      }
      claims.push({ witness, claim });
      const current = await mutationLockOwner(witness.target);
      if (
        current?.pid !== witness.pid
        || current.token !== witness.token
      ) {
        throw new EffectRuntimeConflictError(
          "quota source admission lock owner changed",
          "quota_source_admission_expired",
        );
      }
    }
    adopted = true;
    requireCurrentQuotaAccountingOwner(owner);
    return await operation(true);
  } finally {
    for (const entry of claims.reverse()) {
      if (adopted) {
        await releaseFileMutationLock(
          entry.witness.target,
          entry.witness.token,
          entry.claim,
          true,
        );
      } else {
        await releaseFileMutationLockClaim(entry.claim);
      }
    }
  }
}

export function requireCurrentQuotaAccountingOwner(
  owner: QuotaAccountingOwner,
): void {
  if (owner.kind === "alias") return;
  const decision = decideFirstPartyHostRuntime({
    profile_id: SOURCE_SESSION_PROFILE_ID,
    operation: "require_current",
    planned_goal_ref: quotaGoalRef(owner),
    authority: owner.admission.authority,
  });
  if (decision.kind === "reject") {
    throw new EffectRuntimeConflictError(
      `quota source admission rejected: ${decision.code}`,
      decision.code,
    );
  }
  if (decision.kind !== "resume") {
    throw new EffectRuntimeRequestError(
      "quota source admission did not resume the exact GoalRef",
      "quota_source_admission_invalid",
    );
  }
}

/**
 * Verify a Python-owned admission without ending its surrounding transaction.
 *
 * The temporary claims prevent stale-owner reclamation while the callback
 * runs. The caller remains responsible for releasing the underlying locks.
 */
export async function withBorrowedQuotaAccountingOwner<T>(
  owner: QuotaAccountingOwner,
  operation: (indexLockHeld: boolean) => Promise<T>,
): Promise<T> {
  if (owner.kind === "alias") return await operation(false);

  const claims: FileMutationLockClaim[] = [];
  try {
    for (const witness of owner.admission.locks) {
      const claim = await claimFileMutationLock(
        witness.target,
        witness.token,
      );
      if (!claim) {
        throw new EffectRuntimeConflictError(
          "quota source admission lock handoff expired",
          "quota_source_admission_expired",
        );
      }
      claims.push(claim);
      const current = await mutationLockOwner(witness.target);
      if (
        current?.pid !== witness.pid
        || current.token !== witness.token
      ) {
        throw new EffectRuntimeConflictError(
          "quota source admission lock owner changed",
          "quota_source_admission_expired",
        );
      }
    }
    requireCurrentQuotaAccountingOwner(owner);
    return await operation(true);
  } finally {
    for (const claim of claims.reverse()) {
      await releaseFileMutationLockClaim(claim);
    }
  }
}
