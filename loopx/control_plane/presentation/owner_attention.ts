/** Notification discovery owns no Todo lifecycle or execution authority.
 * Complete native snapshots select subjects; provider journals prove delivery. */
import type {JsonObject} from "../effect_program.ts";
import {requireInteger, requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {canonicalAuthoritySha256 as digest} from "../coordination/authority_store_codec.ts";

const records = (value: unknown, name: string): JsonObject[] => {
  if (!Array.isArray(value)) throw new TypeError(`${name} must be a complete array`);
  return value.map(row => requireJsonObject(row, name));
};
const active = (row: JsonObject) => row.role === "user" && row.done !== true
  && !row.superseded_by && ["open", "blocked"].includes(String(row.status))
  && ["user_action", "user_gate"].includes(String(row.task_class));

/** Local provider-journal vocabulary; never canonical Todo/Run status. */
function readState(raw: unknown): JsonObject {
  const state = requireJsonObject(raw, "state");
  if (state.schema_version !== "lark_owner_attention_v0" || typeof state.enabled !== "boolean"
      || typeof state.initialized !== "boolean" || requireInteger(state.revision, "revision") < 0)
    throw new Error("owner notification state is unavailable");
  records(state.subjects, "subjects");
  for (const row of records(state.outbox, "outbox")) {
    if (!["pending", "sending", "unconfirmed", "delivered", "failed", "superseded"].includes(String(row.status)))
      throw new Error("unknown notification receipt state");
  }
  return state;
}

export function ownerAttentionHealth(input: JsonObject): JsonObject {
  const state = readState(input.state);
  const rows = records(state.outbox, "outbox");
  return {available: true, enabled: state.enabled, revision: state.revision,
    last_checked_at: state.last_checked_at ?? null, error_code: state.error_code ?? null,
    pending_count: rows.filter(row => row.status === "pending").length,
    unconfirmed_count: rows.filter(row => row.status === "sending" || row.status === "unconfirmed").length,
    failed_count: rows.filter(row => row.status === "failed").length};
}

export function planOwnerAttention(input: JsonObject): JsonObject {
  const state = readState(input.state);
  if (input.source_complete !== true) throw new Error("owner attention source is incomplete");
  const previous = records(state.subjects, "subjects");
  const outbox = records(state.outbox, "outbox").map(row => ({...row}));
  const current = new Map<string, JsonObject>();
  for (const goal of records(input.goals, "goals")) {
    const goalId = requireNonEmptyString(goal.goal_id, "goal_id");
    for (const todo of records(goal.todos, "todos")) {
      if (!active(todo)) continue;
      if (todo.goal_id != null && todo.goal_id !== goalId) throw new Error("foreign owner Todo");
      const todoId = requireNonEmptyString(todo.todo_id, "todo_id");
      const text = requireNonEmptyString(todo.text, "todo.text");
      const terms: JsonObject = {text};
      for (const key of ["note", "reason", "evidence", "decision_scope", "required_decision_scopes"]) {
        if (todo[key] != null && todo[key] !== "") terms[key] = todo[key];
      }
      const key = `${goalId}/${todoId}`;
      if (current.has(key)) throw new Error("duplicate owner Todo");
      current.set(key, {key, goal_id: goalId, todo_id: todoId, terms, digest: digest(terms), active: true});
    }
  }
  const subjects: JsonObject[] = [...current.values()].map(row => {
    const old = previous.find(item => item.key === row.key);
    const changed = !old || old.active !== true || old.digest !== row.digest;
    const generation = Number(old?.generation ?? 0) + Number(changed);
    if (state.initialized === true && changed) {
      const id = digest([row.key, generation, row.digest]);
      const terms = row.terms as JsonObject;
      const body = [`待你处理 · ${row.goal_id}`, String(terms.text),
        ...["note", "reason", "evidence", "decision_scope", "required_decision_scopes"].flatMap(key =>
          terms[key] == null ? [] : [`${({note: "说明", reason: "原因", evidence: "依据",
            decision_scope: "决定范围", required_decision_scopes: "决定范围"} as Record<string, string>)[key]}：${
            typeof terms[key] === "string" ? terms[key] : JSON.stringify(terms[key])}`]),
        "可在原管家对话中查看或答复这项待办。"].join("\n\n");
      outbox.push({id, key: row.key, generation, digest: row.digest, goal_id: row.goal_id,
        todo_id: row.todo_id, body, status: Buffer.byteLength(body, "utf8") > 20000 ? "failed" : "pending",
        error_code: Buffer.byteLength(body, "utf8") > 20000 ? "notice_content_too_large" : null,
        attempts: 0, next_attempt_at: 0});
    }
    const {terms: _terms, ...subject} = row;
    return {...subject, generation};
  });
  for (const row of previous) {
    if (!current.has(String(row.key))) subjects.push({...row, active: false});
  }
  for (const row of outbox) {
    const subject = subjects.find(item => item.key === row.key);
    const obsolete = subject?.active !== true || subject.digest !== row.digest || subject.generation !== row.generation;
    // A crash after starting a write is ambiguous even without a locator.
    if (row.status === "sending") {row.status = "unconfirmed"; row.error_code = "send_outcome_unknown";}
    if (obsolete && ["pending", "failed"].includes(String(row.status))) row.status = "superseded";
    row.source_current = !obsolete;
  }
  return {...state, initialized: true, subjects, outbox};
}

/** Fold provider observations, preserving frozen intent and ambiguous writes. */
export function observeOwnerAttention(input: JsonObject): JsonObject {
  const state = readState(input.state);
  const id = requireNonEmptyString(input.id, "id");
  const event = requireNonEmptyString(input.event, "event");
  const outbox = records(state.outbox, "outbox").map(row => ({...row}));
  const row = outbox.find(item => item.id === id);
  if (!row) throw new Error("unknown owner notice");
  const now = Number(input.now);
  if (!Number.isFinite(now) || now < 0) throw new Error("invalid notice observation time");
  if (event === "write_started") {
    if (row.status !== "pending" || row.source_current !== true || Number(row.next_attempt_at) > now)
      throw new Error("notice is not ready for delivery");
    row.intent = requireJsonObject(input.intent, "intent");
    row.status = "sending"; row.attempts = Number(row.attempts) + 1; row.error_code = null;
  } else if (event === "spawn_failed") {
    if (row.status !== "sending") throw new Error("notice write was not started");
    row.status = Number(row.attempts) >= 3 ? "failed" : "pending";
    row.error_code = "provider_spawn_failed";
    row.next_attempt_at = now + 30 * Number(row.attempts);
  } else if (event === "write_observed") {
    if (row.status !== "sending") throw new Error("notice write was not started");
    row.status = "unconfirmed";
    row.message_id = typeof input.message_id === "string" && input.message_id ? input.message_id : null;
    row.error_code = row.message_id ? "readback_pending" : "send_outcome_unknown";
    row.next_attempt_at = now;
  } else if (event === "readback_verified" || event === "readback_pending") {
    if (row.status !== "unconfirmed" || !row.message_id) throw new Error("notice has no provider locator");
    row.status = event === "readback_verified" ? "delivered" : "unconfirmed";
    row.error_code = event === "readback_verified" ? null : "readback_pending";
    row.next_attempt_at = now + 60;
    if (event === "readback_verified") {
      row.readback = requireJsonObject(input.readback, "readback");
      row.verified_at = now;
    }
  } else throw new Error("unknown notice observation");
  return {...state, outbox};
}
