/** Bounded descriptors for replaying existing global Explore result pages. */
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {PLANNING_TODO_ID} from "../work_items/planning_relations.ts";

export const EXPLORE_RESULT_PAGE_MAX = 20;

export function normalizeExploreResultPages(value: unknown): JsonObject[] {
  if (!Array.isArray(value) || !value.length || value.length > 3) {
    throw new EffectRuntimeRequestError("explore_review.result_pages requires one to three distinct result pages");
  }
  const seen = new Set<string>();
  return value.map(item => {
    const row = requireJsonObject(item, "Explore result page");
    if (Object.hasOwn(row, "todo_id") && (typeof row.todo_id !== "string" || !PLANNING_TODO_ID.test(row.todo_id))) {
      throw new EffectRuntimeRequestError("Explore result page todo_id requires the task identity returned by that read");
    }
    if (Object.keys(row).some(key => !["revision", "offset", "limit", "todo_id"].includes(key)) ||
        typeof row.revision !== "string" || !BARE_SHA256_PATTERN.test(row.revision) ||
        !Number.isSafeInteger(row.offset) || Number(row.offset) < 0 ||
        !Number.isSafeInteger(row.limit) || Number(row.limit) < 1 || Number(row.limit) > EXPLORE_RESULT_PAGE_MAX) {
      throw new EffectRuntimeRequestError("Explore result page requires revision, nonnegative offset and limit 1..20; node-filtered pages are unsupported");
    }
    const page = {revision: row.revision, offset: row.offset, limit: row.limit,
      ...(row.todo_id === undefined ? {} : {todo_id: row.todo_id})};
    const key = JSON.stringify(page);
    if (seen.has(key)) throw new EffectRuntimeRequestError("Explore result pages must be distinct");
    seen.add(key);
    return page;
  });
}
