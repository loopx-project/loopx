import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject} from "../runtime_decode.ts";

/** Read-only content selection. Callers supply public-safe, bounded projection
 * fields; this neither resolves a gate nor authorizes an operation. */
export function projectDecisionNotice(input: JsonObject): JsonObject {
  const items: JsonObject[] = [];
  const text = (value: unknown): string => typeof value === "string" ? value.trim() : "";
  const requests = Array.isArray(input.requests) ? input.requests : [];
  for (const raw of requests) {
    const request = requireJsonObject(raw, "decision_notice.requests[]");
    const body = text(request.text);
    if (!body) continue;
    items.push({
      request_id: text(request.request_id), text: body,
      reason: text(request.reason), evidence: text(request.evidence),
    });
    if (items.length === 3) break;
  }
  return {source: items.length ? "request_items" : "unavailable", items};
}
