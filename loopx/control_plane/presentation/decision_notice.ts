import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {todoRequestContent} from "./todo_request_content.ts";

/** Exact references are a delivery obligation, not a prose quality score.
 * Todo identifiers admit ASCII letters, digits, underscores and hyphens;
 * surrounding punctuation/Markdown is presentation, while those characters
 * extend the reference. Keep this rule shared by fresh and cached notices. */
export function validateDecisionNoticeReferences(input: JsonObject): JsonObject {
  const text = requireNonEmptyString(input.text, "decision_notice.text");
  const requests = input.requests === undefined ? [] : input.requests;
  if (!Array.isArray(requests)) throw new TypeError("decision_notice.requests must be an array");
  const missing: string[] = [];
  for (const raw of requests) {
    const request = requireJsonObject(raw, "decision_notice.requests[]");
    if (request.request_id === undefined || request.request_id === null || request.request_id === "") continue;
    const id = requireNonEmptyString(request.request_id, "decision_notice.request_id");
    const escaped = id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const reference = new RegExp("(^|[^A-Za-z0-9_-])" + escaped + "(?=$|[^A-Za-z0-9_-])");
    if (!reference.test(text)) missing.push(id);
  }
  return {valid: missing.length === 0, missing_request_ids: missing};
}

/** Read-only content selection. Callers supply public-safe, bounded projection
 * fields; this neither resolves a gate nor authorizes an operation. */
export function projectDecisionNotice(input: JsonObject): JsonObject {
  const items: JsonObject[] = [];
  const incomplete: JsonObject[] = [];
  const text = (value: unknown): string => typeof value === "string" ? value.trim() : "";
  const requests = Array.isArray(input.requests) ? input.requests : [];
  for (const raw of requests.slice(0, 3)) {
    const request = requireJsonObject(raw, "decision_notice.requests[]");
    if (request.content_redacted === true) {
      incomplete.push({request_id: text(request.request_id), reason_code: "content_redacted"});
      continue;
    }
    let content = {text: text(request.text), note: text(request.reason), evidence: text(request.evidence)};
    if (input.request_snapshot !== undefined) {
      const snapshot = requireJsonObject(input.request_snapshot, "decision_notice.request_snapshot");
      const enriched = todoRequestContent(request, {
        goal_id: text(snapshot.goal_id),
        items: (Array.isArray(snapshot.items) ? snapshot.items : []).map(item => requireJsonObject(item, "request_snapshot.items[]")),
      }, text(input.goal_id));
      if (!enriched) {
        incomplete.push({request_id: text(request.request_id), reason_code: "source_mismatch"});
        continue;
      }
      content = {text: enriched.text, note: enriched.note ?? "", evidence: enriched.evidence ?? ""};
    }
    const body = content.text;
    if (!body) continue;
    // Bounds constrain the transport, not the meaning of a decision. Never
    // silently drop the expiry/risk clause at the end of a complete request.
    if (Array.from(body).length > 900 || Array.from(content.note).length > 450
        || Array.from(content.evidence).length > 450) {
      incomplete.push({request_id: text(request.request_id), reason_code: "content_overflow"});
      continue;
    }
    items.push({
      request_id: text(request.request_id), text: body,
      reason: content.note, evidence: content.evidence,
    });
    if (items.length === 3) break;
  }
  return {source: items.length ? "request_items" : "unavailable", items,
    ...(incomplete.length ? {incomplete} : {})};
}
