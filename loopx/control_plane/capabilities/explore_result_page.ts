/** Bounded descriptors for replaying existing global Explore result pages. */
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";

export function normalizeExploreResultPages(value: unknown): JsonObject[] {
  if (!Array.isArray(value) || !value.length || value.length > 3) {
    throw new EffectRuntimeRequestError("explore_review.result_pages requires one to three distinct result pages");
  }
  const seen = new Set<string>();
  return value.map(item => {
    const row = requireJsonObject(item, "Explore result page");
    if (Object.keys(row).some(key => !["revision", "offset", "limit"].includes(key)) ||
        typeof row.revision !== "string" || !BARE_SHA256_PATTERN.test(row.revision) ||
        !Number.isSafeInteger(row.offset) || Number(row.offset) < 0 ||
        !Number.isSafeInteger(row.limit) || Number(row.limit) < 1 || Number(row.limit) > 20) {
      throw new EffectRuntimeRequestError("Explore result page requires revision, nonnegative offset and limit 1..20; node-filtered pages are unsupported");
    }
    const page = {revision: row.revision, offset: row.offset, limit: row.limit};
    const key = JSON.stringify(page);
    if (seen.has(key)) throw new EffectRuntimeRequestError("Explore result pages must be distinct");
    seen.add(key);
    return page;
  });
}
