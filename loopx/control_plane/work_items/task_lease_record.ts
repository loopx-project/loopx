import type {JsonObject} from "../effect_program.ts";
import {parseIsoTimestamp} from "../runtime_timestamp.ts";

export const TASK_LEASE_SCHEMA_VERSION = "task_lease_v0";

export interface LeaseRecord extends JsonObject {
  schema_version?: unknown;
  goal_id?: unknown;
  todo_id?: unknown;
  owner?: unknown;
  idempotency_key?: unknown;
  write_scopes?: unknown;
  write_repository?: unknown;
  acquire_ttl_seconds?: unknown;
  version?: unknown;
  lease_epoch?: unknown;
  status?: unknown;
  acquired_at?: unknown;
  updated_at?: unknown;
  expires_at?: unknown;
}

export class TaskLeaseAcquireError extends Error {
  readonly code: string;
  readonly payload: JsonObject;

  constructor(message: string, code: string, payload: JsonObject = {}) {
    super(message);
    this.name = "TaskLeaseAcquireError";
    this.code = code;
    this.payload = payload;
  }
}

/** Preserve observed event order for receipt-bound recovery. Lease deadlines
 * keep their existing whole-second wire semantics. */
export function leaseEventTimestamp(at: Date): string {
  return at.toISOString().replace(/\.000Z$/u, "Z");
}

export function leaseInteger(
  lease: LeaseRecord | null,
  field: "version" | "lease_epoch" | "acquire_ttl_seconds",
): number | null {
  const raw = lease?.[field];
  if (raw === null || raw === undefined) return null;
  let number = Number.NaN;
  if (typeof raw === "number") {
    number = raw;
  } else if (typeof raw === "string" && /^-?\d+$/u.test(raw)) {
    number = Number(raw);
  }
  const minimum = field === "version" ? 0 : 1;
  if (
    typeof raw === "boolean" || !Number.isSafeInteger(number) || number < minimum
  ) {
    let message = "lease acquire_ttl_seconds must be a positive integer";
    if (field === "lease_epoch") {
      message = "lease epoch must be a positive integer";
    } else if (field === "version") {
      message = "lease version must be a non-negative integer";
    }
    throw new TaskLeaseAcquireError(message, "corrupt_lease", { [field]: raw ?? null });
  }
  return number;
}

export function leaseVersion(lease: LeaseRecord | null): number {
  return leaseInteger(lease, "version") ?? 0;
}

export function leaseEpoch(lease: LeaseRecord | null): number {
  if (lease === null) return 0;
  return leaseInteger(lease, "lease_epoch") ?? 1;
}

export function leaseIsActive(lease: LeaseRecord | null, at: Date): boolean {
  if (
    lease?.schema_version !== TASK_LEASE_SCHEMA_VERSION ||
    lease.status !== "active"
  ) {
    return false;
  }
  if (typeof lease.expires_at !== "string") {
    throw new TaskLeaseAcquireError(
      "active lease expires_at must be a valid timestamp",
      "corrupt_lease",
      { expires_at: lease.expires_at ?? null },
    );
  }
  const expiresAt = parseIsoTimestamp(lease.expires_at);
  if (expiresAt === null) {
    throw new TaskLeaseAcquireError(
      "active lease expires_at must be a valid timestamp",
      "corrupt_lease",
      { expires_at: lease.expires_at },
    );
  }
  return expiresAt.valueOf() > at.valueOf();
}
