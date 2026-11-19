/** Integrity of registered Goal source observations, shared by delivery and exact reuse. */
import {createHash} from "node:crypto";
import type {JsonObject} from "../effect_program.ts";
import {jsonObject} from "../runtime_decode.ts";
import {ENVELOPED_SHA256_PATTERN} from "../content_digest.ts";

/** The registered file adapter reuses the writer's strict region parser.
 * No heading/prose classification here: its exact complement is Goal context,
 * and canonical Todo readers retain task authority. A complete original file
 * needs no exclusions; metadata alone retains the progressive read. */
export function completeGoalNarrative(content: JsonObject): boolean {
  if (Object.hasOwn(content, "read_complete") && content.read_complete !== true) return false;
  if (Object.hasOwn(content, "full_source")) {
    return content.full_source === true && typeof content.text === "string"
      && typeof content.state_file === "string" && content.state_file.length > 0
      && content.revision === `sha256:${createHash("sha256").update(content.text, "utf8").digest("hex")}`
      && !Object.hasOwn(content, "excluded_todo_regions")
      && !Object.hasOwn(content, "excluded_monitor_receipts");
  }
  if (!Array.isArray(content.excluded_todo_regions) || !content.excluded_todo_regions.length
      || typeof content.text !== "string" || typeof content.state_file !== "string" || !content.state_file
      || typeof content.revision !== "string" || !ENVELOPED_SHA256_PATTERN.test(content.revision)) return false;
  let end = 0;
  if (!content.excluded_todo_regions.every(value => {
    const region = jsonObject(value);
    if (!region || !["user", "agent", "archive"].includes(String(region.role))
        || !Number.isSafeInteger(region.start_line) || !Number.isSafeInteger(region.end_line)
        || Number(region.start_line) <= end || Number(region.end_line) < Number(region.start_line)
        || typeof region.revision !== "string" || !ENVELOPED_SHA256_PATTERN.test(region.revision)) return false;
    end = Number(region.end_line);
    return true;
  })) return false;
  if (!Object.hasOwn(content, "excluded_monitor_receipts")) return true;
  const receipts = content.excluded_monitor_receipts;
  const compact = jsonObject(receipts);
  let ranges: unknown[];
  let expectedCount: number;
  if (compact) {
    if (Object.keys(compact).some(key => !["count", "line_ranges"].includes(key))
        || !Number.isSafeInteger(compact.count) || Number(compact.count) <= 0
        || !Array.isArray(compact.line_ranges)) return false;
    ranges = compact.line_ranges;
    expectedCount = Number(compact.count);
  } else {
    // Already captured source observations retain their original readback.
    if (!Array.isArray(receipts) || !receipts.length) return false;
    ranges = [];
    for (const value of receipts) {
      const region = jsonObject(value);
      if (!region || region.start_line !== region.end_line || typeof region.revision !== "string"
          || !ENVELOPED_SHA256_PATTERN.test(region.revision)) return false;
      ranges.push([region.start_line, region.end_line]);
    }
    expectedCount = receipts.length;
  }
  end = 0;
  let count = 0;
  return ranges.length > 0 && ranges.every(range => {
    if (!Array.isArray(range) || range.length !== 2 || !range.every(Number.isSafeInteger)
        || range[0] <= end || range[1] < range[0]
        || (content.excluded_todo_regions as JsonObject[]).some(todo =>
          range[0] <= Number(todo.end_line) && range[1] >= Number(todo.start_line))) return false;
    end = range[1];
    count += range[1] - range[0] + 1;
    return true;
  }) && Number.isSafeInteger(count) && count === expectedCount;
}
