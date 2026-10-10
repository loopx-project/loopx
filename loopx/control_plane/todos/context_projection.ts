import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { requireBoolean, requireInteger, requireJsonObject } from "../runtime_decode.ts";
import { canonicalAuthorityJson } from "../coordination/authority_store_codec.ts";

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
  if (input.list_payload !== undefined) return projectTodoListPayload(input);
  if (input.detail_payload !== undefined) return projectTodoDetailPayload(input);
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
    // Native exact reads restore text; title may still be a derived summary.
    row.title = text(exact ? record.text || record.title : record.title || record.text, 420);
    if (owner) row.continuation = text(record.note || record.continuation_hint, 280);
    row.content_truncated = truncated;
    return row;
  });
  return {todos, coverage: {active: active.length, included: todos.length,
    omitted: Math.max(0, active.length - todos.length)}};
}

/** Lossless CLI presentation only; the full decision read model stays intact. */
function projectTodoListPayload(input: JsonObject): JsonObject {
  if (Object.keys(input).some(key => key !== "list_payload")) {
    throw new EffectRuntimeRequestError("Todo list and detail/page inputs cannot be combined");
  }
  const payload = requireJsonObject(input.list_payload, "Todo list payload");
  if (payload.ok !== true || payload.command !== "list" || !Array.isArray(payload.todos)
      || payload.todo_id_filter !== undefined || payload.thin) {
    throw new EffectRuntimeRequestError("Todo list references require an ordinary successful inventory");
  }
  const records = new Map<string, string>();
  const key = (row: JsonObject): string => JSON.stringify(canonicalAuthorityJson(row));
  // Keep selection order, counts and source records, including ambiguous ids.
  payload.todos.forEach((value, index) => {
    const row = requireJsonObject(value, "Todo list record");
    const identity = key(row);
    if (!records.has(identity)) records.set(identity, `#/todos/${index}`);
  });
  const result: JsonObject = {...payload};
  let replaced = 0;
  for (const role of ["user_todos", "agent_todos"]) {
    if (payload[role] === undefined) continue;
    const summary = requireJsonObject(payload[role], "Todo role summary");
    result[role] = Object.fromEntries(Object.entries(summary).map(([lane, values]) => {
      if (!Array.isArray(values)) return [lane, values];
      const pointer = lane.replace(/~/g, "~0").replace(/\//g, "~1");
      return [lane, values.map((value, index) => {
        if (!value || typeof value !== "object" || Array.isArray(value)
            || typeof (value as JsonObject).todo_id !== "string") return value;
        const row = value as JsonObject;
        const identity = key(row);
        const reference = records.get(identity);
        if (reference !== undefined) {
          replaced += 1;
          return {$ref: reference};
        }
        // A lane may include a record outside --limit, or distinct derived facts.
        // Keep its first complete projection; never merge by id or body text.
        records.set(identity, `#/${role}/${pointer}/${index}`);
        return row;
      })];
    }));
  }
  result.todo_list_record_references = {
    replaced_view_records: replaced,
    readback: "Resolve $ref at its JSON pointer within this response to read the identical record. "
      + "Distinct projected facts remain separate. Existing overview excerpts do not replace "
      + "an exact Todo read or fresh execution admission.",
  };
  return result;
}

/** Lossless lens over an already scoped exact CLI read; never selects work. */
function projectTodoDetailPayload(input: JsonObject): JsonObject {
  if (Object.keys(input).some(key => key !== "detail_payload")) {
    throw new EffectRuntimeRequestError("Todo detail and context page inputs cannot be combined");
  }
  const payload = requireJsonObject(input.detail_payload, "Todo detail payload");
  const id = payload.todo_id_filter;
  if (typeof id !== "string" || !id) {
    throw new EffectRuntimeRequestError("Todo detail requires an exact Todo identity");
  }
  if (!Array.isArray(payload.todos) || payload.todos.length > 1 || payload.ambiguous) {
    throw new EffectRuntimeRequestError("Todo detail refuses ambiguous source records");
  }
  const todo = payload.todos.length ? requireJsonObject(payload.todos[0], "Todo source row") : null;
  if (requireBoolean(payload.matched, "matched") !== (todo !== null)
      || (todo && (todo.todo_id !== id || typeof todo.text !== "string"))) {
    throw new EffectRuntimeRequestError("Todo detail source identity or body is inconsistent");
  }
  const omitted = ["todos", "agent_todos", "user_todos"];
  const result: JsonObject = {
    ...Object.fromEntries(Object.entries(payload).filter(([key]) => !omitted.includes(key))), todo,
  };
  result.todo_detail_projection = {
    schema_version: "todo_detail_projection_v0", body_path: "todo.text",
    source_complete: todo !== null, omitted_views: omitted,
  };
  return result;
}
