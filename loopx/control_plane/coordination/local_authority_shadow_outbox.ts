import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { join } from "node:path";

import type { JsonObject } from "../effect_program.ts";
import { parseExactGoalRef } from "../goals/goal_instance_identity.ts";
import { outboxEntryIdentity } from "./local_authority_shadow_identity.ts";
import {
  LOCAL_AUTHORITY_SHADOW_BINDING_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_EXACT_BINDING_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_DRAIN_CURSOR_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_OUTBOX_COMMIT_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_OUTBOX_ENTRY_SCHEMA,
} from "./coordination_state_contract.generated.ts";
import { ENVELOPED_SHA256_PATTERN } from "../content_digest.ts";

/**
 * Lease-partition side of the local authority shadow outbox.
 *
 * The task-lease writers already hold the goal's lease lock when they persist
 * a record; this module lets them append a two-phase outbox entry for exactly
 * that partition inside the same lock. Missing cursor recovery reads validated
 * candidate history without taking M. Preparation failures are returned to the
 * primary owner, which enforces the active capture obligation before writing.
 */

export {
  outboxEntryIdentity,
  LOCAL_AUTHORITY_SHADOW_BINDING_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_EXACT_BINDING_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_DRAIN_CURSOR_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_OUTBOX_COMMIT_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_OUTBOX_ENTRY_SCHEMA,
};

export interface LegacyLocalAuthorityShadowBinding {
  schema_version: typeof LOCAL_AUTHORITY_SHADOW_BINDING_SCHEMA;
  provider: "file_v0";
}
export interface ExactLocalAuthorityShadowBinding {
  schema_version: typeof LOCAL_AUTHORITY_SHADOW_EXACT_BINDING_SCHEMA;
  provider: "file_v0";
  goal_ref: JsonObject;
}
export type LocalAuthorityShadowBinding =
  | LegacyLocalAuthorityShadowBinding
  | ExactLocalAuthorityShadowBinding;

/** Decode the optional per-request binding; anything but the exact contract is "absent". */
export function decodeLocalAuthorityShadowBinding(
  value: unknown,
): LocalAuthorityShadowBinding | null {
  if (value === null || value === undefined || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const record = value as Record<string, unknown>;
  const keys = Object.keys(record);
  if (record.schema_version === LOCAL_AUTHORITY_SHADOW_BINDING_SCHEMA) {
    return keys.length === 2 && record.provider === "file_v0"
      ? { schema_version: LOCAL_AUTHORITY_SHADOW_BINDING_SCHEMA, provider: "file_v0" }
      : null;
  }
  const goalRef = parseExactGoalRef(record.goal_ref);
  if (
    keys.length !== 3 ||
    record.schema_version !== LOCAL_AUTHORITY_SHADOW_EXACT_BINDING_SCHEMA ||
    record.provider !== "file_v0" ||
    goalRef.kind !== "parsed"
  ) {
    return null;
  }
  return {
    schema_version: LOCAL_AUTHORITY_SHADOW_EXACT_BINDING_SCHEMA,
    provider: "file_v0",
    goal_ref: {
      goal_id: goalRef.value.goalId.value,
      goal_instance_id: goalRef.value.goalInstanceId.value,
    },
  };
}

export function sha256Digest(input: Uint8Array | string): string {
  return `sha256:${createHash("sha256").update(input).digest("hex")}`;
}

export function outboxPartitionDirectory(
  runtimeRoot: string,
  goalId: string,
  partition: string,
): string {
  return join(runtimeRoot, "authority-shadow", "outbox", goalId, partition);
}

export function outboxEntryFileName(seq: number, entryId: string, phase: "prepared" | "committed"): string {
  return `${String(seq).padStart(10, "0")}-${entryId}.${phase}.json`;
}

function isMissing(error: unknown): boolean {
  return (error as NodeJS.ErrnoException | null)?.code === "ENOENT";
}

const CURSOR_FIELDS = [
  "schema_version", "partition", "last_seq", "last_entry_id", "last_partition_digest",
  "last_cursor", "last_provider_revision", "updated_at",
] as const;
export const MAX_OUTBOX_SEQUENCE = 9_999_999_999;

export class OutboxCursorError extends Error {
  readonly code: "outbox_file_invalid" | "outbox_file_unavailable";
  constructor(code: "outbox_file_invalid" | "outbox_file_unavailable") {
    super(code === "outbox_file_invalid" ? "drain cursor binding is invalid" : "drain cursor cannot be read");
    this.code = code;
  }
}

export async function readOutboxCursor(directory: string, partition: string): Promise<JsonObject | null> {
  let bytes: Uint8Array;
  try {
    bytes = await readFile(join(directory, "drain-cursor.json"));
  } catch (error) {
    if (isMissing(error)) return null;
    throw new OutboxCursorError("outbox_file_unavailable");
  }
  try {
    return decodeOutboxCursor(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)), partition);
  } catch {
    throw new OutboxCursorError("outbox_file_invalid");
  }
}

/** Shared wire validation; even a valid cursor still needs an exact receipt. */
export function decodeOutboxCursor(value: unknown, partition: string): JsonObject {
  const invalid = (): Error => new OutboxCursorError("outbox_file_invalid");
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw invalid();
  const record = value as JsonObject;
  if (Object.keys(record).length !== CURSOR_FIELDS.length ||
      CURSOR_FIELDS.some((key) => !Object.hasOwn(record, key))) throw invalid();
  if (record.schema_version !== LOCAL_AUTHORITY_SHADOW_DRAIN_CURSOR_SCHEMA ||
      !["todos", "leases"].includes(partition) || record.partition !== partition ||
      typeof record.last_seq !== "number" || !Number.isInteger(record.last_seq) ||
      record.last_seq < 1 || record.last_seq > MAX_OUTBOX_SEQUENCE ||
      typeof record.last_entry_id !== "string" ||
      !/^local-shadow-tx-[0-9a-f]{64}$/u.test(record.last_entry_id) ||
      (record.last_partition_digest !== null &&
       (typeof record.last_partition_digest !== "string" || !ENVELOPED_SHA256_PATTERN.test(record.last_partition_digest))) ||
      [record.last_cursor, record.last_provider_revision].some((part) => typeof part !== "string" || part.trim().length === 0)) {
    throw invalid();
  }
  const timestamp = record.updated_at;
  if (typeof timestamp !== "string" ||
      !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/u.test(timestamp)) throw invalid();
  const parsed = new Date(timestamp);
  if (!Number.isFinite(parsed.getTime()) || parsed.getUTCFullYear() < 1 ||
      parsed.toISOString().slice(0, 19) !== timestamp.slice(0, 19)) throw invalid();
  return record;
}
