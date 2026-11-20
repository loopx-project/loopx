/** Frozen administrative closeout record. Reading history never loads current lease authority. */
import {isDeepStrictEqual} from "node:util";
import type {JsonObject} from "../effect_program.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {parseTodoTimestampMicros} from "../runtime_timestamp.ts";
import {heartbeatPreparedLease} from "./heartbeat_receipt_identity.ts";

export const EXECUTION_LOSS_CLOSEOUT_SCHEMA = "quota_execution_loss_closeout_v0";

/** Historical readback validates the frozen qualification, not today's lease. */
export function isExecutionLossCloseout(value: unknown, todoId: string | null): boolean {
  const closeout = typeof value === "object" && value !== null && !Array.isArray(value) ? value as JsonObject : null;
  if (!closeout || closeout.schema_version !== EXECUTION_LOSS_CLOSEOUT_SCHEMA || !todoId
      || closeout.todo_id !== todoId || closeout.source !== "turn_settlement"
      || Object.keys(closeout).sort().join("|") !== ["schema_version", "effect_id", "todo_id", "source",
        "observed_at", "original_receipt_event_id", "original_prepared", "current_control_writer",
        "observation_fingerprint"].sort().join("|")) return false;
  try {
    const original = requireJsonObject(closeout.original_prepared, "original preparation");
    const prepared = heartbeatPreparedLease(original);
    const writer = requireJsonObject(closeout.current_control_writer, "control-writer witness");
    if (typeof closeout.observed_at !== "string"
        || parseTodoTimestampMicros(closeout.observed_at, true) === null) return false;
    return !!prepared && prepared.prepared_lease_epoch !== undefined
      && isDeepStrictEqual(original, prepared)
      && Object.keys(writer).sort().join("|") === ["lease_key_sha256", "version", "lease_epoch", "owner",
        "store_identity", "provider_revision"].sort().join("|")
      && typeof closeout.effect_id === "string" && !!closeout.effect_id
      && BARE_SHA256_PATTERN.test(String(writer.lease_key_sha256))
      && writer.lease_key_sha256 !== prepared.prepared_lease_key_sha256
      && typeof writer.version === "number" && Number.isSafeInteger(writer.version)
      && writer.version > prepared.prepared_lease_version
      && typeof writer.lease_epoch === "number" && Number.isSafeInteger(writer.lease_epoch)
      && writer.lease_epoch > prepared.prepared_lease_epoch
      && typeof closeout.original_receipt_event_id === "string" && !!closeout.original_receipt_event_id
      && typeof writer.owner === "string" && !!writer.owner
      && typeof writer.store_identity === "string" && !!writer.store_identity
      && typeof writer.provider_revision === "string" && !!writer.provider_revision
      && /^[a-f0-9]{16}$/u.test(String(closeout.observation_fingerprint));
  } catch {return false;}
}

