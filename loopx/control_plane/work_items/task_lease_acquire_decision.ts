import {leaseWorkspace, sameLeaseWorkspace, independentLeaseWorktrees, type LeaseWorkspace} from "./task_lease_workspace.ts";
/** Shared acquire/reclaim admission. IO and durable receipts belong to callers. */
import {leaseOwnerRejection as ownerRejection} from "./task_lease_eligibility.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import type {JsonObject} from "../effect_program.ts";
import type {TodoFact, LeaseRecord} from "./task_lease_acquire.ts";
import {normalizeTodoRepository} from "../todos/work_requirements.ts";
import {leaseWriteRepository, leaseRepositoryRejection, repositoryScopesMayOverlap} from "./task_lease_repository.ts";

export interface AcquireDecisionLease {
  present: boolean;
  active: boolean;
  status: string | null;
  owner: string | null;
  idempotency_key: string | null;
  version: number;
  lease_epoch: number;
  write_scopes: readonly string[];
  write_repository?: string | null;
  write_workspace?: LeaseWorkspace | null;
  acquire_ttl_seconds: number | null;
}

export interface AcquireDecisionOtherLease {
  todo_id: string;
  owner?: string;
  active: boolean;
  effective: boolean;
  write_scopes: readonly string[];
  write_repository?: string | null;
  write_workspace?: LeaseWorkspace | null;
}

export interface AcquireDecisionInput {
  handoff_mode: string;
  registered_agents: readonly string[];
  todo: TodoFact | null;
  lease: AcquireDecisionLease | null;
  other_leases: readonly AcquireDecisionOtherLease[];
  command: {
    owner: string;
    idempotency_key: string;
    ttl_seconds: number;
    write_scopes: readonly string[];
    expected_version: number | null;
    write_workspace?: LeaseWorkspace | null;
  };
}

export interface AcquireDecision extends JsonObject {
  outcome: "apply" | "no_change" | "conflict" | "rejected";
  code: string;
  idempotent: boolean;
  next_lease: JsonObject | null;
  conflict_indexes: number[];
  overlap_advisory_indexes?: number[];
}

function stringValue(value: unknown, label: string): string {
  if (typeof value !== "string") {
    throw new EffectRuntimeRequestError(`${label} must be a string`);
  }
  return value;
}

function optionalInteger(value: unknown, label: string): number | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "number" || !Number.isInteger(value)) {
    throw new EffectRuntimeRequestError(`${label} must be an integer or null`);
  }
  return value;
}

function classMatch(
  pattern: string,
  start: number,
  value: string,
): { end: number; matches: boolean } | null {
  let end = start + 1;
  if (pattern[end] === "!") end += 1;
  if (pattern[end] === "]") end += 1;
  end = pattern.indexOf("]", end);
  if (end < 0) return null;
  let body = pattern.slice(start + 1, end);
  const negated = body.startsWith("!");
  if (negated) body = body.slice(1);
  let matches = false;
  for (let index = 0; index < body.length; index += 1) {
    if (index + 2 < body.length && body[index + 1] === "-") {
      if (body[index] <= value && value <= body[index + 2]) matches = true;
      index += 2;
    } else if (body[index] === value) {
      matches = true;
    }
  }
  return { end, matches: negated ? !matches : matches };
}

function fnmatchcase(value: string, pattern: string): boolean {
  const memo = new Map<string, boolean>();
  const match = (valueIndex: number, patternIndex: number): boolean => {
    const key = `${valueIndex}:${patternIndex}`;
    const cached = memo.get(key);
    if (cached !== undefined) return cached;
    let result: boolean;
    if (patternIndex === pattern.length) {
      result = valueIndex === value.length;
    } else if (pattern[patternIndex] === "*") {
      result = match(valueIndex, patternIndex + 1) ||
        (valueIndex < value.length && match(valueIndex + 1, patternIndex));
    } else if (valueIndex === value.length) {
      result = false;
    } else if (pattern[patternIndex] === "?") {
      result = match(valueIndex + 1, patternIndex + 1);
    } else if (pattern[patternIndex] === "[") {
      const characterClass = classMatch(pattern, patternIndex, value[valueIndex]);
      result = characterClass === null
        ? value[valueIndex] === "[" && match(valueIndex + 1, patternIndex + 1)
        : characterClass.matches && match(valueIndex + 1, characterClass.end + 1);
    } else {
      result = value[valueIndex] === pattern[patternIndex] &&
        match(valueIndex + 1, patternIndex + 1);
    }
    memo.set(key, result);
    return result;
  };
  return match(0, 0);
}

function scopeLiteralPrefix(scope: string): string {
  const indexes = ["*", "?", "["]
    .map((token) => scope.indexOf(token))
    .filter((index) => index >= 0);
  return indexes.length > 0 ? scope.slice(0, Math.min(...indexes)) : scope;
}

function scopePairOverlaps(left: string, right: string): boolean {
  if (left === right) return true;
  if (["*", "**", "./"].includes(left) || ["*", "**", "./"].includes(right)) {
    return true;
  }
  const leftGlob = ["*", "?", "["].some((token) => left.includes(token));
  const rightGlob = ["*", "?", "["].some((token) => right.includes(token));
  if (leftGlob && !rightGlob) {
    const prefix = scopeLiteralPrefix(left);
    return fnmatchcase(right, left) ||
      (prefix.endsWith("/") && right.replace(/\/$/u, "") === prefix.replace(/\/$/u, ""));
  }
  if (rightGlob && !leftGlob) {
    const prefix = scopeLiteralPrefix(right);
    return fnmatchcase(left, right) ||
      (prefix.endsWith("/") && left.replace(/\/$/u, "") === prefix.replace(/\/$/u, ""));
  }
  if (leftGlob && rightGlob) {
    const leftPrefix = scopeLiteralPrefix(left);
    const rightPrefix = scopeLiteralPrefix(right);
    return !leftPrefix || !rightPrefix || leftPrefix.startsWith(rightPrefix) ||
      rightPrefix.startsWith(leftPrefix);
  }
  const leftRoot = left.replace(/\/$/u, "");
  const rightRoot = right.replace(/\/$/u, "");
  return (left.endsWith("/") && right.startsWith(`${leftRoot}/`)) ||
    (right.endsWith("/") && left.startsWith(`${rightRoot}/`));
}

function writeScopesOverlap(left: readonly string[], right: readonly string[]): boolean {
  if (left.length === 0 || right.length === 0) return false;
  return left.some((a) => right.some((b) => scopePairOverlaps(a, b)));
}

function decisionStringArray(value: unknown, label: string): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new EffectRuntimeRequestError(`${label} must be an array of strings`);
  }
  return [...value] as string[];
}

export function evaluateTaskLeaseWriteScopesOverlap(value: unknown): JsonObject {
  const input = requireJsonObject(value, "task lease write-scope overlap");
  return {
    overlap: writeScopesOverlap(
      decisionStringArray(input.left, "left"),
      decisionStringArray(input.right, "right"),
    ),
  };
}

function decisionBoolean(value: unknown, label: string): boolean {
  if (typeof value !== "boolean") {
    throw new EffectRuntimeRequestError(`${label} must be a boolean`);
  }
  return value;
}

function decisionInteger(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new EffectRuntimeRequestError(`${label} must be a non-negative safe integer`);
  }
  return value;
}

function decisionNullableString(value: unknown, label: string): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string") {
    throw new EffectRuntimeRequestError(`${label} must be a string or null`);
  }
  return value;
}

function decodeDecisionTodo(value: unknown): TodoFact | null {
  if (value === null || value === undefined) return null;
  const todo = requireJsonObject(value, "task lease acquire decision todo");
  return {
    todo_id: stringValue(todo.todo_id, "todo.todo_id"),
    status: stringValue(todo.status, "todo.status"),
    claimed_by: decisionNullableString(todo.claimed_by, "todo.claimed_by"),
    excluded_agents: decisionStringArray(todo.excluded_agents, "todo.excluded_agents"),
    task_repository: normalizeTodoRepository(todo.task_repository),
  };
}

function decodeDecisionLease(value: unknown): AcquireDecisionLease | null {
  if (value === null || value === undefined) return null;
  const lease = requireJsonObject(value, "task lease acquire decision lease");
  // Accept old callers without trusting their derived eligibility hint.
  if (lease.effective !== undefined) decisionBoolean(lease.effective, "lease.effective");
  return {
    present: decisionBoolean(lease.present, "lease.present"),
    active: decisionBoolean(lease.active, "lease.active"),
    status: decisionNullableString(lease.status, "lease.status"),
    owner: decisionNullableString(lease.owner, "lease.owner"),
    idempotency_key: decisionNullableString(
      lease.idempotency_key,
      "lease.idempotency_key",
    ),
    version: decisionInteger(lease.version, "lease.version"),
    lease_epoch: decisionInteger(lease.lease_epoch, "lease.lease_epoch"),
    write_scopes: decisionStringArray(lease.write_scopes, "lease.write_scopes"),
    write_repository: leaseWriteRepository(lease.write_repository),
    write_workspace: leaseWorkspace(lease.write_workspace),
    acquire_ttl_seconds: optionalInteger(
      lease.acquire_ttl_seconds,
      "lease.acquire_ttl_seconds",
    ),
  };
}

function decodeAcquireDecisionInput(value: unknown): AcquireDecisionInput {
  const input = requireJsonObject(value, "task lease acquire decision");
  const command = requireJsonObject(input.command, "task lease acquire decision command");
  const rawOtherLeases = input.other_leases;
  if (!Array.isArray(rawOtherLeases)) {
    throw new EffectRuntimeRequestError("other_leases must be an array");
  }
  const otherLeases = rawOtherLeases.map((raw, index) => {
    const lease = requireJsonObject(raw, `other_leases[${index}]`);
    return {
      todo_id: stringValue(lease.todo_id, `other_leases[${index}].todo_id`),
      active: decisionBoolean(lease.active, `other_leases[${index}].active`),
      effective: decisionBoolean(lease.effective, `other_leases[${index}].effective`),
      write_repository: leaseWriteRepository(lease.write_repository),
      write_workspace: leaseWorkspace(lease.write_workspace),
      write_scopes: decisionStringArray(
        lease.write_scopes,
        `other_leases[${index}].write_scopes`,
      ),
    };
  });
  return {
    handoff_mode: stringValue(input.handoff_mode, "handoff_mode"),
    registered_agents: decisionStringArray(
      input.registered_agents,
      "registered_agents",
    ),
    todo: decodeDecisionTodo(input.todo),
    lease: decodeDecisionLease(input.lease),
    other_leases: otherLeases,
    command: {
      owner: stringValue(command.owner, "command.owner"),
      idempotency_key: stringValue(
        command.idempotency_key,
        "command.idempotency_key",
      ),
      ttl_seconds: decisionInteger(command.ttl_seconds, "command.ttl_seconds"),
      write_scopes: decisionStringArray(
        command.write_scopes,
        "command.write_scopes",
      ),
      write_workspace: leaseWorkspace(command.write_workspace),
      expected_version: optionalInteger(
        command.expected_version,
        "command.expected_version",
      ),
    },
  };
}

function acquireDecisionResult(
  outcome: AcquireDecision["outcome"],
  code: string,
  options: {
    idempotent?: boolean;
    nextLease?: JsonObject | null;
    conflictIndexes?: number[];
    advisoryIndexes?: number[];
  } = {},
): AcquireDecision {
  return {
    outcome,
    code,
    idempotent: options.idempotent ?? false,
    next_lease: options.nextLease ?? null,
    conflict_indexes: options.conflictIndexes ?? [],
    ...(options.advisoryIndexes?.length ? {overlap_advisory_indexes: options.advisoryIndexes} : {}),
  };
}

/**
 * Canonical pure decision for both local file acquire and shared coordination.
 * Locking, source revalidation, persistence, provider CAS, and receipts stay in
 * their respective execution layers.
 */
export function evaluateTaskLeaseAcquireDecision(value: unknown): AcquireDecision {
  return decideTaskLeaseAcquire(decodeAcquireDecisionInput(value));
}

export function decideTaskLeaseAcquire(input: AcquireDecisionInput): AcquireDecision {
  const { command, lease } = input;
  if (lease !== null && lease.active && (!lease.present || lease.status === "released")) {
    return acquireDecisionResult("rejected", "invalid_lease_snapshot");
  }
  if (input.handoff_mode === "soft_claim") {
    return acquireDecisionResult("rejected", "handoff_mode_forbids_lease");
  }
  const rejection = ownerRejection(
    input.todo ?? undefined,
    command.owner || null,
    input.registered_agents,
  );
  if (rejection !== null) {
    return acquireDecisionResult("rejected", rejection);
  }
  const actualVersion = lease !== null && lease.present ? lease.version : 0;
  if (
    command.expected_version !== null && command.expected_version !== actualVersion
  ) {
    return acquireDecisionResult("conflict", "version_mismatch");
  }
  // The old wire effective hint is not authority over the supplied owner facts.
  if (lease !== null && lease.present && lease.active &&
      ownerRejection(input.todo, lease.owner, input.registered_agents) === null) {
    const repositoryRejection = leaseRepositoryRejection(input.todo, lease);
    if (repositoryRejection !== null) return acquireDecisionResult("rejected", repositoryRejection);
    if (
      lease.owner === command.owner &&
      lease.idempotency_key === command.idempotency_key
    ) {
      const scopesMatch = equalScopeSets(lease.write_scopes, command.write_scopes);
      const ttlMatches = lease.acquire_ttl_seconds === null ||
        lease.acquire_ttl_seconds === command.ttl_seconds;
      if (!scopesMatch || !ttlMatches || !sameLeaseWorkspace(lease.write_workspace, command.write_workspace)) {
        return acquireDecisionResult("rejected", "idempotency_key_reuse");
      }
      return acquireDecisionResult("no_change", "lease_acquire_replay", {
        idempotent: true,
      });
    }
    return acquireDecisionResult("conflict", "todo_lease_conflict");
  }
  if (
    lease !== null && lease.present &&
    lease.idempotency_key === command.idempotency_key
  ) {
    return acquireDecisionResult("rejected", "idempotency_key_reuse");
  }
  const repository = normalizeTodoRepository(input.todo?.task_repository);
  const workspace = leaseWorkspace(command.write_workspace);
  if (workspace && workspace.repository.toLowerCase() !== repository?.toLowerCase()) {
    return acquireDecisionResult("rejected", "lease_workspace_repository_mismatch");
  }
  const advisoryIndexes: number[] = [], conflictIndexes: number[] = [];
  for (const [index, other] of input.other_leases.entries()) {
    if (!other.active || !other.effective || !repositoryScopesMayOverlap(repository, other.write_repository) ||
        !writeScopesOverlap(command.write_scopes, other.write_scopes)) continue;
    if (independentLeaseWorktrees(workspace, other.write_workspace)) advisoryIndexes.push(index);
    else conflictIndexes.push(index);
  }
  if (conflictIndexes.length > 0) {
    return acquireDecisionResult("conflict", "write_scope_conflict", {
      conflictIndexes,
    });
  }
  if (actualVersion >= Number.MAX_SAFE_INTEGER || (lease?.lease_epoch ?? 0) >= Number.MAX_SAFE_INTEGER) {
    return acquireDecisionResult("rejected", "lease_generation_exhausted");
  }
  return acquireDecisionResult("apply", "lease_acquire", {
    advisoryIndexes,
    nextLease: {
      present: true,
      active: true,
      status: "active",
      owner: command.owner,
      idempotency_key: command.idempotency_key,
      version: actualVersion + 1,
      lease_epoch: (lease?.lease_epoch ?? 0) + 1,
      write_scopes: [...command.write_scopes],
      ...(workspace ? {write_workspace: workspace} : {}),
      ...(repository === null ? {} : {write_repository: repository}),
      acquire_ttl_seconds: command.ttl_seconds,
    },
  });
}

function equalScopeSets(left: readonly string[], right: readonly string[]): boolean {
  const a = [...new Set(left)].sort((first, second) => first.localeCompare(second));
  const b = [...new Set(right)].sort((first, second) => first.localeCompare(second));
  return a.length === b.length && a.every((value, index) => value === b[index]);
}

/** Materialize the one admitted generation for standalone and atomic Todo claim. */
export function materializeTaskLeaseAcquire(identity: {goal_id: string; todo_id: string},
  command: AcquireDecisionInput["command"], decision: AcquireDecision, now: Date): LeaseRecord {
  if (decision.outcome !== "apply" || decision.next_lease === null) {
    throw new EffectRuntimeRequestError("lease materialization requires an apply decision");
  }
  const at = now.toISOString().replace(/\.\d{3}Z$/u, "Z");
  return {schema_version: "task_lease_v0", goal_id: identity.goal_id, todo_id: identity.todo_id,
    owner: command.owner, idempotency_key: command.idempotency_key,
    ...(decision.next_lease.write_workspace ? {write_workspace: leaseWorkspace(decision.next_lease.write_workspace)} : {}),
    write_scopes: [...command.write_scopes], acquire_ttl_seconds: command.ttl_seconds,
    ...(decision.next_lease.write_repository == null ? {} :
      {write_repository: leaseWriteRepository(decision.next_lease.write_repository)}),
    version: decisionInteger(decision.next_lease.version, "next_lease.version"),
    lease_epoch: decisionInteger(decision.next_lease.lease_epoch, "next_lease.lease_epoch"),
    acquired_at: at, updated_at: at,
    expires_at: new Date(now.valueOf() + command.ttl_seconds * 1000).toISOString().replace(/\.\d{3}Z$/u, "Z"),
    status: "active"};
}
