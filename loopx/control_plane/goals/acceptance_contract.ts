/** Owner-configured acceptance basis. This is neither a permission grant nor
 * the full shared Goal intent/amendment authority. The Todo manifest is unchanged. */
import type {JsonObject} from "../effect_program.ts";
import {AuthorityStoreProtocolError, authorityUnicodeCompare, canonicalAuthorityBytes,
  canonicalAuthorityObject, canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {indexCoordinationProjectionTodos, validateCoordinationTodoReadModel} from "../coordination/coordination_projection.ts";

export const GOAL_ACCEPTANCE_SCHEMA = "loopx_goal_acceptance_v0";
export interface AcceptanceCriterion extends JsonObject {
  id: string;
  description: string;
  validation_argv: string[];
  validation_timeout_seconds: number;
  validation_files: {path: string; sha256: string}[];
}
export interface AcceptanceDocument extends JsonObject {
  objective: string;
  non_goals: string[];
  criteria: AcceptanceCriterion[];
  bindings: {todo_id: string; criterion_ids: string[]}[];
}
export interface AcceptanceBinding extends JsonObject {
  todo_id: string;
  todo_semantic_digest: string;
  revision: number;
  criterion_ids: string[];
  confirmed_by: "owner";
}
export interface AcceptanceResult extends JsonObject {
  criterion_id: string;
  passed: boolean;
  exit_code: number | null;
}
export interface AcceptanceVerification extends JsonObject {
  operation_id: string;
  contract_revision: number;
  contract_digest: string;
  work_digest: string;
  todo_id: string | null;
  results: AcceptanceResult[];
}
export interface AcceptanceState extends JsonObject {
  schema_version: typeof GOAL_ACCEPTANCE_SCHEMA;
  enabled: boolean;
  revision: number;
  digest: string;
  document: AcceptanceDocument;
  bindings: AcceptanceBinding[];
  verification: AcceptanceVerification | null;
}
export type AcceptanceBindingState = "ready" | "unbound" | "stale";
export interface AcceptanceTask extends JsonObject {
  todo_id: string;
  state: AcceptanceBindingState;
  criterion_ids: string[];
  reason: string;
  reason_code: string;
  applicable: boolean;
}
export interface AcceptanceCompletionRequirements extends JsonObject {
  contract_revision: number;
  contract_digest: string;
  todo_id: string;
  todo_semantic_digest: string;
  criterion_ids: string[];
  criteria: AcceptanceCriterion[];
}

export function acceptanceRequire(condition: unknown, message: string): asserts condition {
  if (!condition) throw new AuthorityStoreProtocolError(message);
}
export function acceptanceKeys(value: JsonObject, required: readonly string[], optional: readonly string[] = []): void {
  acceptanceRequire(required.every(key => Object.hasOwn(value, key)) &&
    Object.keys(value).every(key => required.includes(key) || optional.includes(key)), "acceptance fields are missing or unsupported");
}
export function acceptanceText(value: unknown, label: string, limit = 4096): string {
  acceptanceRequire(typeof value === "string" && value.trim().length > 0 &&
    value.length <= limit && !value.includes("\0"), `${label} must be nonempty bounded text`);
  return value.trim();
}
function id(value: unknown): string {
  const result = acceptanceText(value, "acceptance identifier", 128);
  acceptanceRequire(result === value && /^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(result), "acceptance identifier must be a safe token");
  return result;
}
function list(value: unknown, label: string, max: number, min = 0): unknown[] {
  acceptanceRequire(Array.isArray(value) && value.length >= min && value.length <= max, `${label} has invalid size`);
  return value;
}
function unique(values: string[], label: string): string[] {
  acceptanceRequire(new Set(values).size === values.length, `${label} contains duplicate identifiers`);
  return values.sort(authorityUnicodeCompare);
}

/** Explicit file pins only. The host checks repository containment and bytes
 * before/after execution; pins do not attest transitive imports or dependencies. */
function validationFiles(value: unknown): {path: string; sha256: string}[] {
  const files = list(value, "validation files", 16).map(value => {
    const file = canonicalAuthorityObject(value, "validation file");
    acceptanceKeys(file, ["path", "sha256"]);
    const path = acceptanceText(file.path, "validation file path", 1024);
    acceptanceRequire(path === file.path && /^[A-Za-z0-9._-]+(?:\/[A-Za-z0-9._-]+)*$/.test(path) &&
      path.split("/").every(part => part !== "." && part !== ".."),
      "validation file path must be repository-relative with safe path segments; absolute paths, backslashes and dot segments are forbidden");
    acceptanceRequire(typeof file.sha256 === "string" && /^[a-fA-F0-9]{64}$/.test(file.sha256),
      "validation file sha256 must contain exactly 64 hexadecimal characters");
    return {path, sha256: file.sha256.toLowerCase()};
  });
  unique(files.map(file => file.path), "validation file paths");
  return files.sort((left, right) => authorityUnicodeCompare(left.path, right.path));
}

export function normalizeGoalAcceptanceDocument(value: unknown): AcceptanceDocument {
  const raw = canonicalAuthorityObject(value, "acceptance document");
  acceptanceKeys(raw, ["objective", "non_goals", "criteria", "bindings"]);
  acceptanceRequire(canonicalAuthorityBytes(raw).length <= 262144, "acceptance document exceeds byte limit");
  const criteria = list(raw.criteria, "acceptance criteria", 64, 1).map(value => {
    const item = canonicalAuthorityObject(value, "acceptance criterion");
    acceptanceKeys(item, ["id", "description", "validation_argv"], ["validation_timeout_seconds", "validation_files"]);
    const argv = list(item.validation_argv, "validation argv", 128, 1).map(arg => {
      acceptanceRequire(typeof arg === "string" && arg.length > 0 && arg.length <= 8192 && !arg.includes("\0"), "validation argv contains an invalid argument");
      return arg;
    });
    acceptanceRequire(argv[0].trim().length > 0, "validation argv requires an executable");
    const timeout = item.validation_timeout_seconds ?? 5;
    acceptanceRequire(Number.isSafeInteger(timeout) && Number(timeout) >= 1 && Number(timeout) <= 25, "validation timeout must be 1..25 seconds");
    return {id: id(item.id), description: acceptanceText(item.description, "criterion description"),
      validation_argv: argv, validation_timeout_seconds: Number(timeout),
      validation_files: validationFiles(item.validation_files === undefined ? [] : item.validation_files)};
  }).sort((a, b) => authorityUnicodeCompare(a.id, b.id));
  acceptanceRequire(criteria.reduce((total, item) => total + item.validation_timeout_seconds, 0) <= 25,
    "acceptance validation timeouts must total at most 25 seconds (default 5 seconds per criterion); lower the timeouts or run longer evaluations outside the completion wrapper");
  const criterionIds = unique(criteria.map(item => item.id), "criteria");
  const bindings = list(raw.bindings, "acceptance bindings", 4096).map(value => {
    const item = canonicalAuthorityObject(value, "acceptance binding");
    acceptanceKeys(item, ["todo_id", "criterion_ids"]);
    const criterion_ids = unique(list(item.criterion_ids, "binding criteria", 64, 1).map(id), "binding criteria");
    acceptanceRequire(criterion_ids.every(key => criterionIds.includes(key)), "binding references an unknown criterion");
    return {todo_id: id(item.todo_id), criterion_ids};
  }).sort((a, b) => authorityUnicodeCompare(a.todo_id, b.todo_id));
  unique(bindings.map(item => item.todo_id), "bindings");
  return {objective: acceptanceText(raw.objective, "acceptance objective"),
    non_goals: list(raw.non_goals, "non-goals", 64).map(item => acceptanceText(item, "non-goal", 2048)), criteria, bindings};
}

// Exact field classification, never prose/substring relevance. Unknown future
// domain fields remain digest inputs by default. Execution observations and
// presentation must not revoke a confirmed work declaration.
const NON_WORK_FIELDS = new Set([
  "schema_version", "source_section", "index", "title", "priority", "status", "done", "archive_state",
  "claimed_by", "created_by", "last_actor_agent_id", "updated_at", "completed_at", "completion_turn_key",
  "completion_validation_sha256", "completion_recovery", "completion_continuation", "no_followup", "decision_outcome",
  "decision_scope_outcomes", "note", "evidence", "reason", "handoff_note", "resume_ready",
  "resume_monitor_generation", "last_checked_at", "result_hash", "consecutive_no_change",
  "material_change", "material_change_generation", "monitor_effect_id",
]);
export function goalAcceptanceTodoDigest(todo: JsonObject): string {
  return canonicalAuthoritySha256(Object.fromEntries(Object.entries(todo).filter(([key]) => !NON_WORK_FIELDS.has(key))));
}
function advancement(todo: JsonObject): boolean {
  return todo.role === "agent" && (todo.task_class == null || todo.task_class === "advancement_task");
}
export function acceptanceApplies(todo: JsonObject): boolean {
  return advancement(todo) && todo.archive_state === "active" &&
    (todo.status === "open" || todo.status === "blocked") && todo.done === false;
}
export function acceptanceTodos(head: JsonObject, goalId: string): ReadonlyMap<string, JsonObject> {
  validateCoordinationTodoReadModel(head, goalId);
  return indexCoordinationProjectionTodos(head, goalId).todos;
}
export function goalAcceptanceWorkDigest(head: JsonObject, goalId: string): string {
  // This semantic fingerprint is independent of provider row representation.
  // Projection and mutation callers separately validate the canonical read model.
  return canonicalAuthoritySha256([...indexCoordinationProjectionTodos(head, goalId).todos.values()].filter(advancement)
    .sort((left, right) => authorityUnicodeCompare(String(left.todo_id), String(right.todo_id)))
    .map(todo => ({todo_id: todo.todo_id, digest: goalAcceptanceTodoDigest(todo)})));
}

/** Absent is the sole legacy/off shortcut; malformed present state fails closed. */
export function readGoalAcceptance(head: JsonObject, goalId: string): AcceptanceState | null {
  if (!Object.hasOwn(head, "goal_acceptance")) return null;
  acceptanceRequire(head.goal_id === goalId, "acceptance Goal identity mismatch");
  const state = canonicalAuthorityObject(head.goal_acceptance, "goal_acceptance");
  acceptanceKeys(state, ["schema_version", "enabled", "revision", "digest", "document", "bindings", "verification"]);
  acceptanceRequire(state.schema_version === GOAL_ACCEPTANCE_SCHEMA && typeof state.enabled === "boolean" &&
    Number.isSafeInteger(state.revision) && Number(state.revision) > 0, "invalid acceptance state version");
  const document = normalizeGoalAcceptanceDocument(state.document);
  acceptanceRequire(state.digest === canonicalAuthoritySha256(document), "acceptance contract digest mismatch");
  const bindings = list(state.bindings, "canonical bindings", 4096).map(value => {
    const binding = canonicalAuthorityObject(value, "canonical binding");
    acceptanceKeys(binding, ["todo_id", "todo_semantic_digest", "revision", "criterion_ids", "confirmed_by"]);
    const declared = document.bindings.find(item => item.todo_id === binding.todo_id);
    acceptanceRequire(declared && binding.confirmed_by === "owner" && binding.revision === state.revision &&
      typeof binding.todo_semantic_digest === "string" && /^[a-f0-9]{64}$/.test(binding.todo_semantic_digest) &&
      canonicalAuthoritySha256(binding.criterion_ids) === canonicalAuthoritySha256(declared.criterion_ids), "invalid owner-confirmed acceptance binding");
    return binding as AcceptanceBinding;
  });
  unique(bindings.map(binding => binding.todo_id), "canonical bindings");
  acceptanceRequire(bindings.length === document.bindings.length, "canonical bindings omit declared work");
  let verification: AcceptanceVerification | null = null;
  if (state.verification !== null) {
    const receipt = canonicalAuthorityObject(state.verification, "acceptance verification");
    acceptanceKeys(receipt, ["operation_id", "contract_revision", "contract_digest", "work_digest", "todo_id", "results"]);
    const operationId = acceptanceText(receipt.operation_id, "verification operation id", 256);
    acceptanceRequire(operationId === receipt.operation_id, "verification operation id must be trimmed");
    acceptanceRequire(Number.isSafeInteger(receipt.contract_revision) && Number(receipt.contract_revision) > 0 &&
      Number(receipt.contract_revision) <= Number(state.revision) &&
      [receipt.contract_digest, receipt.work_digest].every(value => typeof value === "string" && /^[a-f0-9]{64}$/.test(value)),
      "invalid verification basis");
    const todoId = receipt.todo_id === null ? null : id(receipt.todo_id);
    // Historical receipts can name retired criteria. Current acceptance checks
    // require exact coverage even for failed or task-scoped verification.
    let expectedIds: string[] | undefined;
    if (receipt.contract_revision === state.revision) {
      acceptanceRequire(receipt.contract_digest === state.digest, "current verification contract digest mismatch");
      expectedIds = todoId === null ? document.criteria.map(item => item.id)
        : bindings.find(binding => binding.todo_id === todoId)?.criterion_ids;
      acceptanceRequire(expectedIds, "current verification references an unbound Todo");
    }
    verification = {...receipt, operation_id: operationId, todo_id: todoId,
      results: normalizeAcceptanceResults(receipt.results, expectedIds)} as AcceptanceVerification;
  }
  return {...state, document, bindings, verification} as AcceptanceState;
}

export function acceptanceTask(todoId: string, todo: JsonObject | undefined, state: AcceptanceState): AcceptanceTask {
  const binding = state.bindings.find(item => item.todo_id === todoId);
  const reason_code = !binding ? "goal_acceptance_unbound" : !todo || binding.todo_semantic_digest !== goalAcceptanceTodoDigest(todo)
    ? "goal_acceptance_stale" : "goal_acceptance_ready";
  return {todo_id: todoId, state: !binding ? "unbound" : reason_code === "goal_acceptance_stale" ? "stale" : "ready",
    criterion_ids: binding?.criterion_ids ?? [], reason_code,
    reason: !binding ? "Owner confirmation is required for this work's acceptance association."
      : reason_code === "goal_acceptance_stale" ? "Work changed after owner confirmation; confirm its current acceptance association."
      : "The owner confirmed this work's current acceptance association.",
    applicable: todo !== undefined && acceptanceApplies(todo)};
}

export function normalizeAcceptanceResults(value: unknown, expectedIds?: readonly string[]): AcceptanceResult[] {
  const results = list(value, "verification results", 64, 1).map(value => {
    const item = canonicalAuthorityObject(value, "verification result");
    // Existing caller-validation runner metadata is accepted at the host seam
    // but deliberately not persisted or projected (labels can contain context).
    acceptanceKeys(item, ["criterion_id", "passed", "exit_code"], ["schema_version", "command_label", "status", "summary",
      "stdout_captured", "stderr_captured", "local_path_captured"]);
    for (const key of ["stdout_captured", "stderr_captured", "local_path_captured"]) {
      acceptanceRequire(item[key] === undefined || item[key] === false, "validation result must not capture private output or paths");
    }
    acceptanceRequire(typeof item.passed === "boolean" && (item.exit_code === null ||
      (Number.isSafeInteger(item.exit_code) && Number(item.exit_code) >= 0 && Number(item.exit_code) <= 255)), "invalid verification result");
    acceptanceRequire(item.passed === (item.exit_code === 0), "criterion pass/fail must agree with exit code zero");
    return {criterion_id: id(item.criterion_id), passed: item.passed, exit_code: item.exit_code as number | null};
  }).sort((a, b) => authorityUnicodeCompare(a.criterion_id, b.criterion_id));
  const ids = unique(results.map(item => item.criterion_id), "verification results");
  if (expectedIds) acceptanceRequire(canonicalAuthoritySha256(ids) === canonicalAuthoritySha256([...expectedIds].sort(authorityUnicodeCompare)),
    "verification results must cover exactly the required criteria");
  return results;
}

export function projectGoalAcceptance(head: JsonObject, goalId: string): JsonObject {
  const state = readGoalAcceptance(head, goalId);
  if (!state?.enabled) return {enabled: false};
  const todos = acceptanceTodos(head, goalId);
  const taskIds = new Set([...todos.values()].filter(advancement).map(todo => String(todo.todo_id)));
  for (const binding of state.bindings) taskIds.add(binding.todo_id);
  const tasks = [...taskIds].sort(authorityUnicodeCompare).map(key => acceptanceTask(key, todos.get(key), state));
  const held = tasks.filter(task => task.applicable && task.state !== "ready");
  const receipt = state.verification;
  let status = "unverified";
  if (receipt) {
    if (receipt.contract_revision !== state.revision || receipt.contract_digest !== state.digest ||
        receipt.work_digest !== goalAcceptanceWorkDigest(head, goalId)) status = "stale";
    else if (receipt.results.some(item => !item.passed)) status = "failed";
    else if (receipt.todo_id !== null) status = "partial";
    else status = "accepted";
  }
  if (held.length) status = "held";
  return {enabled: true, revision: state.revision, digest: state.digest, objective: state.document.objective,
    non_goals: state.document.non_goals, criteria: state.document.criteria.map(({id, description}) => ({id, description})),
    tasks, held_todo_ids: held.map(task => task.todo_id), status,
    verification: receipt ? {operation_id: receipt.operation_id, contract_revision: receipt.contract_revision,
      contract_digest: receipt.contract_digest, todo_id: receipt.todo_id, results: receipt.results} : null};
}

/** An owner-confirmed binding governs its work until the owner changes it.
 * Mutable Todo fields can make a binding stale; they must not make it
 * inapplicable, or the guarded party could edit its way out of the guard. */
function acceptanceBound(state: AcceptanceState, todoId: string): boolean {
  return state.bindings.some(binding => binding.todo_id === todoId);
}

export function acceptanceWorkGuard(head: JsonObject, goalId: string, todoId: string): JsonObject | null {
  return projectGoalAcceptanceWorkGuards(head, goalId, [todoId])[todoId] ?? null;
}

/** Project many work guards from one validated acceptance/read-model snapshot.
 * Collection reads must not reparse the contract and rebuild the Todo index
 * once per Todo: that turns a bounded provider read into quadratic work. */
export function projectGoalAcceptanceWorkGuards(
  head: JsonObject,
  goalId: string,
  todoIds: readonly string[],
): Record<string, JsonObject> {
  const state = readGoalAcceptance(head, goalId);
  if (!state?.enabled) return {};
  const todos = acceptanceTodos(head, goalId);
  return Object.fromEntries(todoIds.flatMap(todoId => {
    const todo = todos.get(todoId);
    if (todo && !acceptanceApplies(todo) && !acceptanceBound(state, todoId)) return [];
    const task = acceptanceTask(todoId, todo, state);
    return [[todoId, {allowed: task.state === "ready", ...task,
      revision: state.revision, digest: state.digest}]];
  }));
}

/** Trusted execution adapter only. Run these commands at the inspected provider
 * revision and commit fresh results with completion in that same provider CAS. */
export function acceptanceCompletionRequirements(head: JsonObject, goalId: string, todoId: string): AcceptanceCompletionRequirements | null {
  const state = readGoalAcceptance(head, goalId);
  if (!state?.enabled) return null;
  const todo = acceptanceTodos(head, goalId).get(todoId);
  acceptanceRequire(todo, "acceptance completion Todo is missing");
  // Applicability follows the owner's binding, not the Todo's current shape:
  // `acceptanceApplies` reads task_class and status, which the guarded party
  // may rewrite. Its remaining job is to decide which *unbound* work must be
  // held, so it stays as the fallback for Todos the owner never bound.
  if (!acceptanceApplies(todo) && !acceptanceBound(state, todoId)) return null;
  const task = acceptanceTask(todoId, todo, state);
  acceptanceRequire(task.state === "ready", task.reason_code);
  return {contract_revision: state.revision, contract_digest: state.digest, todo_id: todoId,
    todo_semantic_digest: goalAcceptanceTodoDigest(todo), criterion_ids: task.criterion_ids,
    criteria: state.document.criteria.filter(item => task.criterion_ids.includes(item.id))};
}

/** This validates fresh host execution evidence, not a saved success receipt.
 * Host identity and the pre-execution provider CAS are enforced by the caller. */
export function validateAcceptanceCompletion(head: JsonObject, goalId: string, todoId: string, value: unknown): JsonObject | null {
  const requirements = acceptanceCompletionRequirements(head, goalId, todoId);
  if (!requirements) return null;
  const receipt = canonicalAuthorityObject(value, "acceptance completion evidence");
  acceptanceKeys(receipt, ["contract_revision", "contract_digest", "todo_id", "todo_semantic_digest", "results"]);
  acceptanceRequire(["contract_revision", "contract_digest", "todo_id", "todo_semantic_digest"].every(key => receipt[key] === requirements[key]),
    "acceptance completion basis changed");
  const results = normalizeAcceptanceResults(receipt.results, requirements.criterion_ids);
  acceptanceRequire(results.every(item => item.passed), "acceptance completion criteria failed");
  return {...receipt, results};
}
