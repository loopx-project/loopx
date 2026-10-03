import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { requireBoolean, requireInteger, requireJsonObject } from "../runtime_decode.ts";

// A read lens over canonical records, shared by global and Goal conversations.
// These are existing Todo facts, not dependency evaluation or execution grants.
const CONTEXT_FIELDS = [
  "todo_id", "role", "status", "priority", "task_class", "claimed_by",
  "bound_agent", "goal_bound", "blocks_agent", "global_gate", "unblocks_todo_id",
  "action_kind", "next_due_at", "expires_at", "resume_when", "resume_ready",
  "successor_todo_ids", "decision_scope", "required_decision_scopes",
] as const;

/** Select before redaction; never convert nested scopes or booleans to prose. */
export function projectTodoContextPage(input: JsonObject): JsonObject {
  if (!Array.isArray(input.records)) throw new EffectRuntimeRequestError("Todo context records must be an array");
  const records = input.records.map(value => requireJsonObject(value, "Todo context record"));
  const owner = requireBoolean(input.owner_scope, "owner_scope");
  const offset = requireInteger(input.offset, "offset");
  const limit = requireInteger(input.limit, "limit");
  if (offset < 0 || limit < 1 || limit > 48) throw new EffectRuntimeRequestError("Todo context page is out of range");
  const exact = input.todo_id !== undefined && input.todo_id !== null;
  if (exact && (typeof input.todo_id !== "string" || !input.todo_id)) {
    throw new EffectRuntimeRequestError("todo_id must be a non-empty string");
  }
  // Exact reads can recover a completed referent, without resurrecting work.
  const active = records.filter(row => exact ? row.todo_id === input.todo_id
    : row.status === "open" || row.status === "blocked" || row.status === "deferred");
  active.sort((a, b) => Number(a.role !== "user") - Number(b.role !== "user")
    || (String(a.priority ?? "Z") < String(b.priority ?? "Z") ? -1
      : String(a.priority ?? "Z") > String(b.priority ?? "Z") ? 1 : 0));
  const todos = active.slice(offset, offset + limit).map(record => {
    const row: JsonObject = {};
    for (const key of CONTEXT_FIELDS) {
      if (record[key] !== undefined && record[key] !== null) row[key] = record[key];
    }
    let truncated = false;
    const text = (value: unknown, cap: number): string => {
      const full = typeof value === "string" ? value : "";
      const characters = Array.from(full);
      if (exact || characters.length <= cap) return full;
      truncated = true;
      return characters.slice(0, cap - 3).join("") + "...";
    };
    row.title = text(record.title || record.text, 420);
    if (owner) row.continuation = text(record.note || record.continuation_hint, 280);
    row.content_truncated = truncated;
    return row;
  });
  return {todos, coverage: {active: active.length, included: todos.length,
    omitted: Math.max(0, active.length - todos.length)}};
}
