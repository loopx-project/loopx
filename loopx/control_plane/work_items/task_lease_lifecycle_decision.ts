import {leaseOwnerRejection as ownerRejection} from "./task_lease_eligibility.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { type JsonObject } from "../effect_program.ts";
import { requireJsonObject } from "../runtime_decode.ts";
import {leaseEpoch, utcIsoformat, type LeaseRecord} from "./task_lease_acquire.ts";
import {normalizeTodoRepository} from "../todos/work_requirements.ts";
import {leaseWriteRepository, leaseRepositoryRejection} from "./task_lease_repository.ts";

export const TASK_LEASE_LIFECYCLE_DECISION_OPERATIONS = [
  "renew",
  "transfer",
  "release",
] as const;
export type TaskLeaseLifecycleDecisionOperation =
  (typeof TASK_LEASE_LIFECYCLE_DECISION_OPERATIONS)[number];
export type TaskLeaseLifecycleDecisionOutcome =
  | "apply"
  | "no_change"
  | "conflict"
  | "rejected";

export interface TaskLeaseLifecycleDecisionTodo {
  task_repository?: string | null;
  todo_id: string;
  status: string;
  claimed_by: string | null;
  excluded_agents: readonly string[];
}

export interface TaskLeaseLifecycleDecisionLease {
  write_repository?: string | null;
  present: boolean;
  active: boolean;
  status: string | null;
  owner: string | null;
  idempotency_key: string | null;
  version: number;
  lease_epoch: number;
  write_scopes: readonly string[];
  acquire_ttl_seconds: number | null;
}

export interface TaskLeaseLifecycleDecisionCommand {
  operation: TaskLeaseLifecycleDecisionOperation;
  owner: string;
  idempotency_key: string;
  expected_version: number | null;
  ttl_seconds: number | null;
  new_owner: string | null;
  new_idempotency_key: string | null;
}

export interface TaskLeaseLifecycleDecisionInput {
  handoff_mode: string;
  registered_agents: readonly string[];
  todo: TaskLeaseLifecycleDecisionTodo | null;
  lease: TaskLeaseLifecycleDecisionLease | null;
  command: TaskLeaseLifecycleDecisionCommand;
}

export interface TaskLeaseLifecycleDecision extends JsonObject {
  outcome: TaskLeaseLifecycleDecisionOutcome;
  code: string;
  idempotent: boolean;
  next_lease: TaskLeaseLifecycleDecisionLease | null;
}

function stringValue(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new EffectRuntimeRequestError(`${label} must be a non-empty string`);
  }
  return value;
}

function nullableString(value: unknown, label: string): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string") {
    throw new EffectRuntimeRequestError(`${label} must be a string or null`);
  }
  return value;
}

function booleanValue(value: unknown, label: string): boolean {
  if (typeof value !== "boolean") {
    throw new EffectRuntimeRequestError(`${label} must be a boolean`);
  }
  return value;
}

function integerValue(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new EffectRuntimeRequestError(
      `${label} must be a non-negative safe integer`,
    );
  }
  return value;
}

function optionalInteger(value: unknown, label: string): number | null {
  if (value === null || value === undefined) return null;
  return integerValue(value, label);
}

function stringArray(value: unknown, label: string): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new EffectRuntimeRequestError(`${label} must be an array of strings`);
  }
  return [...value] as string[];
}

function operationValue(value: unknown): TaskLeaseLifecycleDecisionOperation {
  if (
    typeof value !== "string" ||
    !TASK_LEASE_LIFECYCLE_DECISION_OPERATIONS.includes(
      value as TaskLeaseLifecycleDecisionOperation,
    )
  ) {
    throw new EffectRuntimeRequestError(
      "command.operation has an unsupported lifecycle operation",
    );
  }
  return value as TaskLeaseLifecycleDecisionOperation;
}

function decodeTodo(value: unknown): TaskLeaseLifecycleDecisionTodo | null {
  if (value === null || value === undefined) return null;
  const todo = requireJsonObject(value, "task lease lifecycle decision todo");
  return {
    todo_id: stringValue(todo.todo_id, "todo.todo_id"),
    task_repository: normalizeTodoRepository(todo.task_repository),
    status: stringValue(todo.status, "todo.status"),
    claimed_by: nullableString(todo.claimed_by, "todo.claimed_by"),
    excluded_agents: stringArray(todo.excluded_agents, "todo.excluded_agents"),
  };
}

function decodeLease(value: unknown): TaskLeaseLifecycleDecisionLease | null {
  if (value === null || value === undefined) return null;
  const lease = requireJsonObject(value, "task lease lifecycle decision lease");
  return {
    present: booleanValue(lease.present, "lease.present"),
    active: booleanValue(lease.active, "lease.active"),
    status: nullableString(lease.status, "lease.status"),
    owner: nullableString(lease.owner, "lease.owner"),
    idempotency_key: nullableString(
      lease.idempotency_key,
      "lease.idempotency_key",
    ),
    version: integerValue(lease.version, "lease.version"),
    lease_epoch: integerValue(lease.lease_epoch, "lease.lease_epoch"),
    write_scopes: stringArray(lease.write_scopes, "lease.write_scopes"),
    ...(lease.write_repository == null ? {} : {write_repository: leaseWriteRepository(lease.write_repository)}),
    acquire_ttl_seconds: optionalInteger(
      lease.acquire_ttl_seconds,
      "lease.acquire_ttl_seconds",
    ),
  };
}

function decodeInput(value: unknown): TaskLeaseLifecycleDecisionInput {
  const input = requireJsonObject(value, "task lease lifecycle decision");
  const command = requireJsonObject(
    input.command,
    "task lease lifecycle decision command",
  );
  return {
    handoff_mode: stringValue(input.handoff_mode, "handoff_mode"),
    registered_agents: stringArray(
      input.registered_agents,
      "registered_agents",
    ),
    todo: decodeTodo(input.todo),
    lease: decodeLease(input.lease),
    command: {
      operation: operationValue(command.operation),
      owner: stringValue(command.owner, "command.owner"),
      idempotency_key: stringValue(
        command.idempotency_key,
        "command.idempotency_key",
      ),
      expected_version: optionalInteger(
        command.expected_version,
        "command.expected_version",
      ),
      ttl_seconds: optionalInteger(command.ttl_seconds, "command.ttl_seconds"),
      new_owner: nullableString(command.new_owner, "command.new_owner"),
      new_idempotency_key: nullableString(
        command.new_idempotency_key,
        "command.new_idempotency_key",
      ),
    },
  };
}

function result(
  outcome: TaskLeaseLifecycleDecisionOutcome,
  code: string,
  options: {
    idempotent?: boolean;
    nextLease?: TaskLeaseLifecycleDecisionLease | null;
  } = {},
): TaskLeaseLifecycleDecision {
  return {
    outcome,
    code,
    idempotent: options.idempotent ?? false,
    next_lease: options.nextLease ?? null,
  };
}

/**
 * Pure lifecycle decision shared by the local file transaction and every
 * provider-neutral coordination executor. Persistence, clocks, provider CAS,
 * and durable receipts remain execution-layer responsibilities.
 */
export function decideTaskLeaseLifecycle(
  input: TaskLeaseLifecycleDecisionInput,
): TaskLeaseLifecycleDecision {
  const { command, lease } = input;
  if (lease !== null && lease.active && (!lease.present || lease.status === "released")) {
    return result("rejected", "invalid_lease_snapshot");
  }
  if (command.operation !== "release" && input.handoff_mode === "soft_claim") {
    return result("rejected", "handoff_mode_forbids_lease");
  }
  if (command.expected_version === null) {
    return result("rejected", "version_required");
  }
  if (command.operation !== "release") {
    if (
      command.operation === "transfer" &&
      !input.registered_agents.includes(command.owner)
    ) {
      return result("rejected", "owner_not_registered");
    }
    const rejection = ownerRejection(
      input.todo,
      command.operation === "transfer" ? command.new_owner : command.owner,
      input.registered_agents,
    );
    if (rejection !== null) return result("rejected", rejection);
    const repositoryRejection = leaseRepositoryRejection(input.todo, lease);
    if (repositoryRejection !== null) return result("rejected", repositoryRejection);
  }
  const actualVersion = lease !== null && lease.present ? lease.version : 0;
  if (actualVersion !== command.expected_version) {
    return result("conflict", "version_mismatch");
  }
  if (command.operation === "release") {
    if (lease === null || !lease.present) {
      return result("no_change", "lease_missing", { idempotent: true });
    }
    if (
      lease.owner !== command.owner ||
      lease.idempotency_key !== command.idempotency_key
    ) {
      return result("rejected", "lease_cas_mismatch");
    }
    if (lease.status === "released") {
      return result("no_change", "lease_release_replay", {
        idempotent: true,
        nextLease: lease,
      });
    }
    return result("apply", "lease_release", {
      nextLease: { ...lease, active: false, status: "released" },
    });
  }
  if (lease === null || !lease.present || !lease.active) {
    return result("rejected", "lease_not_active");
  }
  if (
    lease.owner !== command.owner ||
    lease.idempotency_key !== command.idempotency_key
  ) {
    return result("rejected", "lease_cas_mismatch");
  }
  if (command.operation === "transfer") {
    if (
      command.new_owner === null || command.new_idempotency_key === null ||
      command.new_idempotency_key === command.idempotency_key
    ) {
      return result("rejected", "idempotency_key_reuse");
    }
    if (!Number.isSafeInteger(lease.version + 1) || !Number.isSafeInteger(lease.lease_epoch + 1)) {
      return result("rejected", "lease_generation_exhausted");
    }
    return result("apply", "lease_transfer", {
      nextLease: {
        ...lease,
        owner: command.new_owner,
        idempotency_key: command.new_idempotency_key,
        version: lease.version + 1,
        lease_epoch: lease.lease_epoch + 1,
      },
    });
  }
  if (!Number.isSafeInteger(lease.version + 1)) {
    return result("rejected", "lease_generation_exhausted");
  }
  return result("apply", "lease_renew", {
    nextLease: { ...lease, version: lease.version + 1 },
  });
}

/** Shared record materialization; storage adapters cannot redefine generations
 * or timestamps. Release retains the fence generation as historical evidence. */
export function releasedTaskLeaseRecord(lease: LeaseRecord, at: Date): LeaseRecord {
  return {...lease, lease_epoch: leaseEpoch(lease), status: "released",
    released_at: utcIsoformat(at), updated_at: utcIsoformat(at)};
}

export function materializeTaskLeaseLifecycle(lease: LeaseRecord,
  command: TaskLeaseLifecycleDecisionCommand, decision: TaskLeaseLifecycleDecision,
  at: Date): LeaseRecord {
  if (decision.outcome !== "apply" || decision.next_lease === null) {
    throw new EffectRuntimeRequestError("only an applied lease decision may change its record");
  }
  if (command.operation === "release") return releasedTaskLeaseRecord(lease, at);
  const next = decision.next_lease;
  return {...lease,
    ...(command.operation === "transfer" ? {owner: next.owner,
      idempotency_key: next.idempotency_key, lease_epoch: next.lease_epoch} : {}),
    version: next.version, updated_at: utcIsoformat(at),
    expires_at: utcIsoformat(new Date(at.valueOf() + command.ttl_seconds! * 1000))};
}

export function evaluateTaskLeaseLifecycleDecision(
  value: unknown,
): TaskLeaseLifecycleDecision {
  return decideTaskLeaseLifecycle(decodeInput(value));
}
