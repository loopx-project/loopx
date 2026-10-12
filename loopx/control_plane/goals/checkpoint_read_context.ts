/** Read basis for a missing-checkpoint supplement, not an execution/permission lease.
 * The commit effect holds source/index claims and the real provider fence. */
import type {JsonObject} from "../effect_program.ts";
import {jsonObject, requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {claimAllowsAgent} from "../todos/agent_scope.ts";

const RECEIPT_SCHEMA = "checkpoint_read_context_v1";
const FIRST_RECEIPT_SCHEMA = "checkpoint_read_context_v2";
export type CheckpointReadPurpose = "supplement_checkpoint" | "first_delivery" | "delivery_result";
export type CheckpointDecisionScope = "agent_lane" | "goal";

function purpose(request: JsonObject): CheckpointReadPurpose {
  const value = request.purpose ?? "supplement_checkpoint";
  if (value !== "supplement_checkpoint" && value !== "first_delivery" && value !== "delivery_result") {
    throw new EffectRuntimeRequestError("unsupported checkpoint read purpose");
  }
  return value;
}
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

export function checkpointBasisSnapshot(request: JsonObject): JsonObject {
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
  let frontier: JsonObject[] | null = null;
  if (purpose(request) === "first_delivery") {
    const scope = request.decision_scope;
    if (scope !== "agent_lane" && scope !== "goal") {
      throw new EffectRuntimeRequestError("first delivery requires agent_lane or goal decision scope");
    }
    const agent = requireNonEmptyString(identity.agent_id, "agent_id");
    // Recompute the complete membership. The caller cannot omit new work.
    frontier = records.filter(todo => scope === "goal" || todo.role === "user" ||
      claimAllowsAgent({claim: typeof todo.claimed_by === "string" ? todo.claimed_by : null,
        excluded: ids(todo.excluded_agents, "excluded_agents")}, agent));
    frontier = frontier.map(todo => ({todo, key: String(todo.todo_id ?? canonicalAuthoritySha256(todo))}))
      .sort((a, b) => a.key.localeCompare(b.key)).map(({todo}) => todo);
  }
  const selected = new Map<string, JsonObject>();
  const pending = [...ids(request.dependency_todo_ids, "dependency_todo_ids"),
    ...(task ? dependencies(task) : []), ...(frontier ?? []).flatMap(dependencies)];
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
  if (frontier !== null) {
    basis.frontier = {scope: request.decision_scope, todos: frontier};
  }
  if (purpose(request) !== "supplement_checkpoint") {
    basis.execution_lease = facts.execution_lease ?? null;
  }
  return {basis, provider_revision: facts.provider_revision ?? null,
    versions: Object.fromEntries(Object.entries(basis).map(([key, value]) =>
    [key, canonicalAuthoritySha256(value)]))};
}

const rejected = (code: string, changed: string[] = []): JsonObject => ({
  ok: false, error_code: code, reread_required: true, changed_components: changed,
  error: `${code}: run checkpoint-context for the same Goal/Agent/Todo or obligation/Turn, ` +
    "using the original purpose. Recheck the candidate for delivery_result, or judge the direction for first_delivery/supplement_checkpoint. " +
    "Use the returned identity only with that new decision. Retain successful result and quota receipts.",
});

export function evaluateCheckpointReadContext(value: unknown): JsonObject {
  const request = requireJsonObject(value, "checkpoint read context");
  const identity = requireJsonObject(request.identity, "identity");
  const token = request.read_context_id;
  const use = purpose(request);
  if (request.phase === "read") {
    if (use !== "supplement_checkpoint") {
      if (jsonObject(request.receipt)?.commit_attempt != null) {
        return {...rejected("checkpoint_commit_unknown"), reread_required: false,
          error: "A first delivery append may have started; inspect the original Turn and artifacts before another judgment."};
      }
      if (request.prior != null || request.admitted_turn !== true) {
        return rejected("checkpoint_first_delivery_not_admitted");
      }
    } else {
      const prior = requireJsonObject(request.prior, "committed writeback");
      const checkpoint = jsonObject(prior.vision_checkpoint);
      if (checkpoint?.decision !== "missing_required" || checkpoint.satisfied !== false) {
        return rejected("checkpoint_context_not_missing");
      }
    }
    const projected = checkpointBasisSnapshot(request);
    const task = jsonObject(requireJsonObject(projected.basis, "basis").todo);
    if (use === "delivery_result" && task?.status === "done" &&
        [identity.effect_id, identity.turn_instance_id].includes(task.completion_turn_key)) {
      return {...rejected("delivery_result_already_committed"), reread_required: false,
        original_read_context_id: jsonObject(request.receipt)?.read_context_id ?? null,
        error: "This Turn's result is already committed. Keep its original read identity for replay and read first_delivery for the pending direction."};
    }
    return {ok: true, ...projected, receipt: {
      schema_version: use !== "supplement_checkpoint" ? FIRST_RECEIPT_SCHEMA : RECEIPT_SCHEMA,
      ...(use !== "supplement_checkpoint" ? {purpose: use, decision_scope: request.decision_scope} : {}),
      read_context_id: requireNonEmptyString(token, "read_context_id"),
      identity, dependency_todo_ids: ids(request.dependency_todo_ids, "dependency_todo_ids"),
      versions: projected.versions, provider_revision: projected.provider_revision,
    }};
  }
  if (request.phase !== "check") throw new EffectRuntimeRequestError("unknown checkpoint read context phase");
  if (typeof token !== "string" || !token.trim()) return rejected("checkpoint_read_context_required");
  const receipt = jsonObject(request.receipt);
  if (receipt?.schema_version !== (use !== "supplement_checkpoint" ? FIRST_RECEIPT_SCHEMA : RECEIPT_SCHEMA) ||
      receipt.read_context_id !== token ||
      (use !== "supplement_checkpoint" && receipt.purpose !== use)) {
    return rejected("checkpoint_read_context_unknown_or_replaced");
  }
  if (canonicalAuthoritySha256(receipt.identity) !== canonicalAuthoritySha256(identity)) {
    return rejected("checkpoint_read_context_identity_mismatch");
  }
  if (use === "first_delivery" && request.decision_scope != null &&
      request.decision_scope !== receipt.decision_scope) {
    return rejected("checkpoint_read_context_scope_mismatch");
  }
  if (use === "first_delivery" && receipt.commit_attempt != null) {
    return {...rejected("checkpoint_commit_unknown"), reread_required: false,
      error: "The original direction append is uncertain; read back its original Turn before retrying."};
  }
  const projected = checkpointBasisSnapshot({...request, dependency_todo_ids: receipt.dependency_todo_ids,
    ...(use === "first_delivery" ? {decision_scope: receipt.decision_scope} : {})});
  const expected = requireJsonObject(receipt.versions, "receipt versions");
  const current = requireJsonObject(projected.versions, "current versions");
  const changed = Object.keys(current).filter(key => current[key] !== expected[key]);
  if (changed.length) return rejected("checkpoint_read_context_stale", changed);
  return {ok: true, read_context_id: token, versions: current,
    ...(use !== "supplement_checkpoint" ? {purpose: use, decision_scope: receipt.decision_scope} : {})};
}
