import { createHash } from "node:crypto";
import {readFileSync} from "node:fs";
import { basename, dirname, isAbsolute, join, resolve } from "node:path";

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
import {legacyGoalRegistrationIsActive} from "../goals/first_party_host_runtime.ts";
import {normalizeRegisteredTodoAgents} from "../coordination/todo_agents.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {jsonObject} from "../runtime_decode.ts";
import {
  parseExactGoalRef,
  type ExactGoalRef,
} from "../goals/goal_instance_identity.ts";
import {
  requireJsonObject,
  requireNonEmptyString,
} from "../runtime_decode.ts";

const QUOTA_SOURCE_ADMISSION_SCHEMA = "loopx_quota_source_admission_v0";
export const QUOTA_NATIVE_ADMISSION_SCHEMA = "loopx_quota_native_admission_v0";
const SOURCE_SESSION_PROFILE_ID = "source_session_v1";

type LockRole = "run_index" | "source_guard" | "registry_source" | "authority_maintenance" | "heartbeat_receipt";

interface QuotaSourceLock {
  role: LockRole;
  target: string;
  pid: number;
  token: string;
}

interface QuotaSourceAdmission {
  kind?: "source_lifetime";
  registryPath: string;
  plannedGoalRef: ExactGoalRef;
  authority: unknown;
  locks: readonly [QuotaSourceLock, QuotaSourceLock];
}

interface QuotaNativeAdmission {
  kind: "native_runtime";
  registryPath: string;
  plannedGoalRef: ExactGoalRef | null;
  goalId: string;
  runtimeRoot: string;
  agentId: string | null;
  registrySources: readonly {path: string; sha256: string}[];
  locks: readonly QuotaSourceLock[];
}

export type QuotaAccountingOwner =
  | Readonly<{ kind: "alias"; admission?: QuotaNativeAdmission }>
  | Readonly<{
    kind: "exact_source";
    goalRef: ExactGoalRef;
    admission: QuotaSourceAdmission | QuotaNativeAdmission;
  }>;

interface ClaimedLock {
  witness: QuotaSourceLock;
  claim: FileMutationLockClaim;
}

export function quotaAccountingOwnerLocks(
  owner: QuotaAccountingOwner,
): readonly Readonly<QuotaSourceLock>[] {
  return owner.admission?.locks ?? [];
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

function parseNativeQuotaOwner(admission: JsonObject, goalRef: ExactGoalRef | null,
  runtimeRoot: string, goalId: string): QuotaAccountingOwner {
  const invalid = (message: string): never => {
    throw new EffectRuntimeRequestError(message, "quota_source_admission_invalid");
  };
  if (Object.keys(admission).some(key => !["schema_version", "registry_path", "planned_goal_ref",
      "runtime_root", "agent_id", "registry_sources", "locks"].includes(key))) {
    invalid("Native quota admission has an unsupported resource field.");
  }
  const registryPath = requireNonEmptyString(admission.registry_path, "native registry_path");
  const parsed = admission.planned_goal_ref === null ? null : parseExactGoalRef(admission.planned_goal_ref);
  if (goalRef === null ? parsed !== null : parsed?.kind !== "parsed" || !sameGoalRef(parsed.value, goalRef)) {
    return invalid("Native quota admission must preserve its original alias or exact GoalRef boundary.");
  }
  const planned = parsed?.kind === "parsed" ? parsed.value : null;
  if (!isAbsolute(registryPath) || resolve(registryPath) !== registryPath
      || admission.runtime_root !== runtimeRoot || !Array.isArray(admission.registry_sources)
      || admission.registry_sources.length < 1 || admission.registry_sources.length > 2) {
    invalid("Native quota admission does not match its exact Goal and registered runtime resources.");
  }
  const registrySources = (admission.registry_sources as unknown[]).map(value => {
    const witness = requireJsonObject(value, "native registry source witness");
    const path = requireNonEmptyString(witness.path, "native registry source path");
    const digest = requireNonEmptyString(witness.sha256, "native registry source digest");
    if (Object.keys(witness).length !== 2 || !isAbsolute(path) || resolve(path) !== path
        || !BARE_SHA256_PATTERN.test(digest)) invalid("Native registry source witness is malformed.");
    return {path, sha256: digest};
  });
  if (registrySources.at(-1)!.path !== registryPath
      || new Set(registrySources.map(source => source.path)).size !== registrySources.length
      || !Array.isArray(admission.locks)
      || ![registrySources.length + 1, registrySources.length + 3].includes(admission.locks.length)) {
    invalid("Native quota admission must retain its exact ordered registry and execution resources.");
  }
  const rawLocks = admission.locks as unknown[];
  const locks = [quotaSourceLock(rawLocks[0], "run_index",
    resolve(join(runtimeRoot, "goals", goalId, "runs", "index.jsonl"))),
  ...registrySources.map((source, index) => quotaSourceLock(rawLocks[index + 1],
    "registry_source", source.path)),
  ...(rawLocks.length === registrySources.length + 1 ? [] : [
    quotaSourceLock(rawLocks[registrySources.length + 1], "authority_maintenance",
      requireNonEmptyString(requireJsonObject(rawLocks[registrySources.length + 1], "native maintenance witness").target,
        "native maintenance target")),
    quotaSourceLock(rawLocks[registrySources.length + 2], "heartbeat_receipt",
      join(runtimeRoot, "goals", goalId, "rollout-event-log.jsonl"))])];
  const native: QuotaNativeAdmission = {kind: "native_runtime", registryPath,
    plannedGoalRef: planned, goalId, runtimeRoot, agentId: admission.agent_id === undefined ? null
      : requireNonEmptyString(admission.agent_id, "native agent_id"),
    registrySources, locks};
  return goalRef === null ? {kind: "alias", admission: native} : {kind: "exact_source", goalRef, admission: native};
}

async function requireNativeQuotaResourceTargets(admission: QuotaNativeAdmission): Promise<void> {
  if (new Set(admission.locks.map(lock => lock.pid)).size !== 1) {
    throw new EffectRuntimeRequestError("Native quota resources must belong to the same current host invocation.",
      "quota_source_admission_invalid");
  }
  if (admission.locks.length === admission.registrySources.length + 1) return;
  // Keep the large maintenance owner outside ordinary settlement-read closure.
  // Exact M validation still precedes adopting any native resource or proof.
  const {shadowMaintenanceLockPath} = await import("../coordination/shadow_management.ts");
  if (admission.locks[admission.registrySources.length + 1]!.target
      !== shadowMaintenanceLockPath(admission.runtimeRoot, admission.goalId)) {
    throw new EffectRuntimeRequestError("Native quota maintenance lock does not protect its canonical Goal.",
      "quota_source_admission_invalid");
  }
}

function requireCurrentNativeQuotaOwner(goalRef: ExactGoalRef | null, admission: QuotaNativeAdmission): void {
  function reject(message: string): never {
    throw new EffectRuntimeConflictError(message, "goal_instance_conflict");
  }
  const registrations = admission.registrySources.map((source, index) => {
    const bytes = readFileSync(source.path);
    if (createHash("sha256").update(bytes).digest("hex") !== source.sha256) {
      reject("Native Goal registration changed during admission.");
    }
    const document: unknown = JSON.parse(bytes.toString("utf8"));
    // The existing project codec decoded these locked bytes in the host. As
    // canonical creation does, bind that exact source before inspecting its
    // registration; this does not activate Source's generic runtime profile.
    const registry = jsonObject(Array.isArray(document) && document.length === 2 ? document[1] : document);
    const role = registry?.registry_role ?? registry?.role;
    if (registry === null || registry.profile_id === SOURCE_SESSION_PROFILE_ID
        || (role !== undefined && !["project-local", "global-local"].includes(String(role)))) {
      reject("Native quota admission requires the existing ordinary runtime registry role.");
    }
    const matches = Array.isArray(registry.goals) ? registry.goals.map(jsonObject)
      .filter(goal => goal?.id === admission.goalId) : [];
    const goal = matches.length === 1 ? matches[0] : null;
    const activation = jsonObject(goal?.activation);
    if (goal === null || (goalRef !== null && goal.goal_instance_id !== goalRef.goalInstanceId.value)
        || !legacyGoalRegistrationIsActive(goal.status) || Object.hasOwn(goal, "activation_state")
        || (activation !== null && (activation.schema_version !== "loopx_goal_activation_v1" || activation.state !== "active"))) {
      reject("Native quota admission lost its persisted active Goal instance.");
    }
    const registered = [jsonObject(goal.coordination)?.registered_agents, goal.registered_agents,
      jsonObject(goal.spawn_policy)?.registered_agents].flatMap(value => Array.isArray(value)
        ? normalizeRegisteredTodoAgents(value as string[]) : []);
    if (admission.agentId !== null && !registered.includes(admission.agentId)) {
      reject("Native admission Agent is not currently registered.");
    }
    const declaredRoot = registry.common_runtime_root;
    const projectRoot = basename(dirname(source.path)) === ".loopx" ? dirname(dirname(source.path)) : dirname(source.path);
    if (index === 0 && (typeof declaredRoot !== "string" || !declaredRoot
        || resolve(projectRoot, declaredRoot) !== admission.runtimeRoot)) {
      reject("Native admission does not own the registered canonical runtime root.");
    }
    return {path: source.path, goal};
  });
  const invoked = registrations.at(-1)!;
  const sourceRoute = invoked.goal.source_registry;
  if (registrations.length === 1 ? sourceRoute != null && sourceRoute !== ""
    : typeof sourceRoute !== "string" || !isAbsolute(sourceRoute)
      || resolve(sourceRoute) !== registrations[0]!.path
      || registrations[0]!.goal.source_registry != null && registrations[0]!.goal.source_registry !== "") {
    reject("Native admission registry sources differ from the existing owning route.");
  }
  if (goalRef === null ? admission.plannedGoalRef !== null
    : admission.plannedGoalRef === null || !sameGoalRef(admission.plannedGoalRef, goalRef)) {
    reject("Native GoalRef changed inside admission.");
  }
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
  if (!hasGoalRef && jsonObject(sourceAdmissionValue)?.schema_version === QUOTA_NATIVE_ADMISSION_SCHEMA) {
    return parseNativeQuotaOwner(requireJsonObject(sourceAdmissionValue, "native quota admission"), null, runtimeRoot, goalId);
  }
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
  if (admission.schema_version === QUOTA_NATIVE_ADMISSION_SCHEMA) {
    return parseNativeQuotaOwner(admission, goalRef.value, runtimeRoot, goalId);
  }
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
      kind: "source_lifetime",
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
  if (owner.admission?.kind === "native_runtime") {
    return await withBorrowedQuotaAccountingOwner(owner, operation);
  }
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

export function requireNativeQuotaWorkAgent(owner: QuotaAccountingOwner, agentId: string,
  publication: boolean): void {
  if (owner.admission?.kind !== "native_runtime" || owner.admission.agentId !== agentId
      || (publication && owner.admission.locks.length !== owner.admission.registrySources.length + 3)) {
    throw new EffectRuntimeRequestError("Fresh native work requires its registered Agent and retained native resource admission.",
      "quota_source_admission_invalid");
  }
  requireCurrentNativeQuotaOwner(owner.kind === "alias" ? null : owner.goalRef, owner.admission);
}

export function requireCurrentQuotaAccountingOwner(
  owner: QuotaAccountingOwner,
): void {
  if (owner.admission?.kind === "native_runtime") {
    requireCurrentNativeQuotaOwner(owner.kind === "alias" ? null : owner.goalRef, owner.admission);
    return;
  }
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
  const admission = owner.admission;
  if (admission === undefined) return await operation(false);
  if (admission.kind === "native_runtime") await requireNativeQuotaResourceTargets(admission);

  const claims: FileMutationLockClaim[] = [];
  try {
    for (const witness of admission.locks) {
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
