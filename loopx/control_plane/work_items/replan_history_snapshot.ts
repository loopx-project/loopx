/** Local IO adapter for complete replan facts that exceed the RPC wire budget.
 * No history selection or policy here: both transports call the same reducer.
 */
import { createHash } from "node:crypto";
import { constants } from "node:fs";
import { open } from "node:fs/promises";
import { isAbsolute } from "node:path";
import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { requireJsonObject, requireNonEmptyString, requireStringLiteral } from "../runtime_decode.ts";
import { projectSettledReplanHistory } from "./replan_history_settlement.ts";
import { BARE_SHA256_PATTERN } from "../content_digest.ts";

export async function projectReplanHistorySnapshot(value: unknown): Promise<JsonObject> {
  return projectSettledReplanHistory(await readReplanSnapshot(value));
}

/** Shared local transport; the requesting typed owner still interprets the facts. */
export async function readReplanSnapshot(value: unknown): Promise<unknown> {
  const request = requireJsonObject(value, "replan history snapshot");
  requireStringLiteral(request.schema_version, ["replan_history_snapshot_v0"], "snapshot schema");
  const path = requireNonEmptyString(request.path, "snapshot path");
  const digest = requireNonEmptyString(request.sha256, "snapshot sha256");
  const size = request.byte_count;
  if (!isAbsolute(path) || !BARE_SHA256_PATTERN.test(digest) ||
      typeof size !== "number" || !Number.isSafeInteger(size) || size <= 0) {
    throw new EffectRuntimeRequestError("invalid replan history snapshot reference");
  }
  let payload: unknown;
  try {
    // No symlinks, device files or cross-user reads. Digest is over the bytes
    // actually parsed, so path replacement or concurrent edits cannot change
    // the decision basis silently. This is not a new remote file-access API.
    const file = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
    try {
      const stat = await file.stat();
      if (!stat.isFile() || stat.size !== size ||
          (process.getuid && (stat.uid !== process.getuid() || (stat.mode & 0o077) !== 0))) {
        throw new Error("snapshot metadata mismatch");
      }
      const bytes = await file.readFile();
      if (bytes.length !== size || createHash("sha256").update(bytes).digest("hex") !== digest) {
        throw new Error("snapshot digest mismatch");
      }
      payload = JSON.parse(bytes.toString("utf8"));
    } finally {
      await file.close();
    }
  } catch {
    // Keep private paths/content out of public errors. Never fall back to a
    // partial history or a Python decision after an unverifiable snapshot.
    throw new EffectRuntimeRequestError("replan history snapshot unavailable or invalid; regenerate from the source");
  }
  return payload;
}
