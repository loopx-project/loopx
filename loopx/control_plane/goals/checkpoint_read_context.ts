/** Read basis for a missing decision/qualification checkpoint, not an execution/permission lease.
 * The commit effect holds source/index claims and the real provider fence. */
import type {JsonObject} from "../effect_program.ts";
import {settlementIdentity} from "../effect_program.ts";
import {jsonObject, requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {isUnqualifiedBlockedReplanWriteback} from "../quota/settlement_phase.ts";

const RECEIPT_SCHEMA = "checkpoint_read_context_v1";
// Exact presentation fields only. Unknown future fields remain part of the basis.
const DISPLAY_FIELDS = new Set(["index", "source_section", "schema_version"]);
const todoFacts = (todo: JsonObject): JsonObject => Object.fromEntries(
  Object.entries(todo).filter(([key]) => !DISPLAY_FIELDS.has(key)),
);

function ids(value: unknown, label: string): string[] {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) throw new EffectRuntimeRequestError(`${label} must be an array`);
  return [...new Set(value.map(item => requireNonEmptyString(item, label)))].sort();
}

function dependencies(todo: JsonObject): string[] {
  const result = ids(todo.depends_on_todo_ids, "depends_on_todo_ids");
  if (todo.depends_on_todo_id != null) result.push(requireNonEmptyString(todo.depends_on_todo_id, "depends_on_todo_id"));
  // These are typed resume tokens, never a search through task prose.
  if (typeof todo.resume_when === "string") {
    const match = /^(?:todo_done|monitor_changed):([^:]+)$/.exec(todo.resume_when);
    if (match) result.push(match[1]);
  }
  return [...new Set(result)].sort();
}

function snapshot(request: JsonObject): JsonObject {
  const identity = requireJsonObject(request.identity, "identity");
  const facts = requireJsonObject(request.facts, "facts");
  if (!Array.isArray(facts.todos)) throw new EffectRuntimeRequestError("todos must be complete source records");
  const records = facts.todos.map(value => todoFacts(requireJsonObject(value, "todo")));
  const byId = new Map<string, JsonObject>();
  for (const todo of records) {
    // Anonymous legacy rows cannot be dependencies; retain them in an obligation frontier.
    if (todo.todo_id == null) continue;
    const id = requireNonEmptyString(todo.todo_id, "todo_id");
    if (byId.has(id)) throw new EffectRuntimeRequestError("checkpoint basis has duplicate Todo identities");
    byId.set(id, todo);
  }
  const todoId = identity.todo_id;
  const task = typeof todoId === "string" ? byId.get(todoId) : null;
  if (typeof todoId === "string" && !task) throw new EffectRuntimeRequestError("checkpoint Todo is absent; restore its authoritative record before rereading");
  const selected = new Map<string, JsonObject>();
  const pending = [...ids(request.dependency_todo_ids, "dependency_todo_ids"), ...(task ? dependencies(task) : [])];
  while (pending.length) {
    const id = pending.shift()!;
    if (selected.has(id) || id === todoId) continue;
    const todo = byId.get(id);
    if (!todo) throw new EffectRuntimeRequestError(`checkpoint dependency ${id} is absent`);
    selected.set(id, todo);
    pending.push(...dependencies(todo));
  }
  const metadata = requireJsonObject(facts.frontmatter, "frontmatter");
  const goal = {
    frontmatter: Object.fromEntries(Object.entries(metadata).filter(([key]) => key !== "updated_at")),
    prose: facts.goal_prose,
    acceptance: facts.acceptance,
    user_todos: records.filter(todo => todo.role === "user"),
  };
  const basis: JsonObject = {
    todo: task ?? records,
    goal,
    dependencies: [...selected.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([, todo]) => todo),
    agent_vision: facts.agent_vision,
    source: facts.source,
  };
  return {basis, provider_revision: facts.provider_revision ?? null,
    versions: Object.fromEntries(Object.entries(basis).map(([key, value]) =>
    [key, canonicalAuthoritySha256(value)]))};
}

const rejected = (code: string, changed: string[] = []): JsonObject => ({
  ok: false, error_code: code, reread_required: code !== "checkpoint_context_not_missing", changed_components: changed,
  error: code === "checkpoint_context_not_missing"
    ? `${code}: this committed writeback has no missing checkpoint for this recovery. ` +
      "Preserve the committed writeback and quota receipt; do not repeat this recovery or quota spend. " +
      "For later authorized work, inspect current state through the normal guard with a fresh Turn identity. " +
      "This rejection grants no work or completion authority."
    : `${code}: run checkpoint-context for the same Goal/Agent/Todo or obligation/Turn, ` +
    "read the returned state and judge again; submit its --checkpoint-read-context with only the vision decision. " +
    "Do not repeat implementation, state mutations, or quota spend.",
});

export function evaluateCheckpointReadContext(value: unknown): JsonObject {
  const request = requireJsonObject(value, "checkpoint read context");
  const identity = requireJsonObject(request.identity, "identity");
  const token = request.read_context_id;
  if (request.phase === "read") {
    const prior = requireJsonObject(request.prior, "committed writeback");
    const checkpoint = jsonObject(prior.vision_checkpoint);
    const missingQualification = request.writeback_committed === true && request.quota_spend_committed === false &&
      typeof identity.replan_obligation_id === "string" &&
      jsonObject(request.semantic_replan_guard)?.selected_obligation_id === identity.replan_obligation_id &&
      isUnqualifiedBlockedReplanWriteback(prior, settlementIdentity({
        goal_id: requireNonEmptyString(identity.goal_id, "goal_id"),
        agent_id: requireNonEmptyString(identity.agent_id, "agent_id"),
        turn_instance_id: requireNonEmptyString(identity.turn_instance_id, "turn_instance_id"),
        replan_obligation_id: identity.replan_obligation_id,
      }));
    if ((checkpoint?.decision !== "missing_required" || checkpoint.satisfied !== false) && !missingQualification) {
      return rejected("checkpoint_context_not_missing");
    }
    const projected = snapshot(request);
    return {ok: true, ...projected, receipt: {
      schema_version: RECEIPT_SCHEMA, read_context_id: requireNonEmptyString(token, "read_context_id"),
      identity, dependency_todo_ids: ids(request.dependency_todo_ids, "dependency_todo_ids"),
      versions: projected.versions, provider_revision: projected.provider_revision,
    }};
  }
  if (request.phase !== "check") throw new EffectRuntimeRequestError("unknown checkpoint read context phase");
  if (typeof token !== "string" || !token.trim()) return rejected("checkpoint_read_context_required");
  const receipt = jsonObject(request.receipt);
  if (receipt?.schema_version !== RECEIPT_SCHEMA || receipt.read_context_id !== token) {
    return rejected("checkpoint_read_context_unknown_or_replaced");
  }
  if (canonicalAuthoritySha256(receipt.identity) !== canonicalAuthoritySha256(identity)) {
    return rejected("checkpoint_read_context_identity_mismatch");
  }
  const projected = snapshot({...request, dependency_todo_ids: receipt.dependency_todo_ids});
  const expected = requireJsonObject(receipt.versions, "receipt versions");
  const current = requireJsonObject(projected.versions, "current versions");
  const changed = Object.keys(current).filter(key => current[key] !== expected[key]);
  if (changed.length) return rejected("checkpoint_read_context_stale", changed);
  return {ok: true, read_context_id: token, versions: current};
}
