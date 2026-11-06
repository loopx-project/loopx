/** Human review is a canonical User action, never report generation or Goal acceptance. */
import type {JsonObject} from "../effect_program.ts";
import {requireNonEmptyString} from "../runtime_decode.ts";
import {canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {reportReviewDigest} from "./report_review_binding.ts";

export function planReportReview(input: JsonObject): JsonObject {
  const digest = requireNonEmptyString(input.bundle_sha256, "bundle_sha256");
  if (!/^[a-f0-9]{64}$/.test(digest)) throw new Error("Invalid report bundle digest");
  const goal = requireNonEmptyString(input.goal_id, "goal_id");
  const agent = requireNonEmptyString(input.agent_id, "agent_id");
  const title = requireNonEmptyString(input.title, "report title");
  if (title.length > 200) throw new Error("Report title exceeds its existing limit");
  const existing = input.todos == null ? [] : (projectReportReviews({goal_id: goal, todos: input.todos}).items as JsonObject[])
    .filter(row => row.bundle_sha256 === digest && row.created_by === agent);
  if (existing.length > 1) throw new Error("Ambiguous report review identity");
  return {role: "user", task_class: "user_action", action_kind: "review_periodic_report",
    priority: "P1", text: title,
    existing_todo_id: existing[0]?.todo_id ?? null,
    capability_binding_ref: `periodic-report-review:r${digest}`, bound_agent: agent,
    // Different producers and Goals never share a review decision.
    operation_id: `report-review:${canonicalAuthoritySha256([goal, agent, digest])}`};
}

/** Only native bindings select rows. Titles and prose never classify review work. */
export function projectReportReviews(input: JsonObject): JsonObject {
  if (!Array.isArray(input.todos)) throw new Error("Report review Todo source is unavailable");
  const goal = requireNonEmptyString(input.goal_id, "goal_id");
  const items = input.todos.flatMap(raw => {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error("Invalid Todo source");
    const todo = raw as JsonObject;
    const digest = reportReviewDigest(todo);
    if (!digest) return [];
    if (todo.goal_id != null && todo.goal_id !== goal) throw new Error("Foreign report review");
    return [{todo_id: requireNonEmptyString(todo.todo_id, "todo_id"), bundle_sha256: digest,
      title: requireNonEmptyString(todo.text, "report title"), status: todo.status,
      created_by: todo.created_by ?? null,
      bound_agent: todo.bound_agent ?? null, created_at: todo.created_at ?? null,
      updated_at: todo.updated_at ?? null, completed_at: todo.completed_at ?? null,
      superseded_by: todo.superseded_by ?? null}];
  });
  return {items: items.sort((a, b) => String(b.updated_at ?? "").localeCompare(String(a.updated_at ?? "")))};
}
