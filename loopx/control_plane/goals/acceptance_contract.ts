import {NON_WORK_FIELDS, goalAcceptanceTodoDigest} from "./acceptance_work_basis.ts";
export {goalAcceptanceTodoDigest} from "./acceptance_work_basis.ts";
import {goalOutcomeCheckBasis, projectGoalOutcomes} from "./outcome_requirements.ts";
import {projectGoalObjective, currentGoalObjective} from "./goal_objective.ts";
/** Owner-configured acceptance basis. This is neither a permission grant nor
 * the full shared Goal intent/amendment authority. The Todo manifest is unchanged. */
import type {JsonObject} from "../effect_program.ts";
import {AuthorityStoreProtocolError, authorityUnicodeCompare, canonicalAuthorityBytes,
  canonicalAuthorityObject, canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {CoordinationProjectionRead, indexCoordinationProjectionTodos} from "../coordination/coordination_projection.ts";
import {COMPLETION_VALIDATION_BINDING_RECEIPT_SCHEMA,
  completionValidationRevisionHistory} from "../todos/completion_validation_revision.ts";
import { BARE_SHA256_PATTERN } from "../content_digest.ts";
import {parseWireExactGoalRef, readGoalAcceptanceLifecycle, sameExactGoalRef, type GoalAcceptanceLifecycle,
  type WireExactGoalRef} from "./acceptance_lifecycle.ts";

import {normalizeAcceptanceOracle, normalizeOracleEvidence, projectAcceptanceOracle, validateOracleResults, type AcceptanceOracle} from "./acceptance_oracle.ts";

export const GOAL_ACCEPTANCE_SCHEMA = "loopx_goal_acceptance_v2";
export interface AcceptanceCriterion extends JsonObject {
  id: string;
  description: string;
  validation_argv: string[];
  validation_timeout_seconds: number;
  validation_files: {path: string; sha256: string}[];
  oracle?: AcceptanceOracle;
}
export type AcceptanceScope =
  | {kind: "all_advancement"}
  | {kind: "selected_work"; todo_ids: string[]};
export interface AcceptanceDocument extends JsonObject {
  outcome_id?: string;
  scope?: AcceptanceScope;
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
  owner_goal_ref: WireExactGoalRef | null;
  revision: number;
  digest: string;
  document: AcceptanceDocument;
  bindings: AcceptanceBinding[];
  verification: AcceptanceVerification | null;
}
export type OwnedAcceptanceState = AcceptanceState & {owner_goal_ref: WireExactGoalRef};
export type GoalAcceptanceAuthority =
  | Readonly<{kind: "legacy"; state: AcceptanceState | null; configuration_revision: number}>
  | Readonly<{kind: "exact"; lifecycle: GoalAcceptanceLifecycle; state: AcceptanceState | null;
      configuration_revision: number; owner_matches: boolean}>;

/** Removing checks retains the revision fence, without a feature switch or
 * dormant command document. Immutable operation history keeps old evidence. */
export function clearedGoalAcceptance(revision: number, goalRef: WireExactGoalRef | null): JsonObject {
  return {schema_version: GOAL_ACCEPTANCE_SCHEMA, owner_goal_ref: goalRef, revision,
    digest: null, document: null, bindings: [], verification: null};
}

/** Current readback has no enable flag. This boundary also reads retained
 * pre-upgrade snapshots; consumers only ask whether actual checks exist. */
export function configuredGoalChecks(value: JsonObject | null | undefined): JsonObject | null {
  return value && value.enabled !== false && Array.isArray(value.criteria) ? value : null;
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

/** Shared by terminal completion and read-only delegation validation. */
export function acceptanceValidationEffects(requirements: AcceptanceCompletionRequirements, todo: JsonObject): JsonObject[] {
  return requirements.criteria.map(criterion => ({criterion_id: criterion.id, effect: {
    kind: "caller_validation", validation_command: null, validation_argv: criterion.validation_argv,
    validation_label: criterion.id, validation_timeout_seconds: criterion.validation_timeout_seconds,
    ...(criterion.validation_files == null ? {} : {validation_files: criterion.validation_files}),
    ...(criterion.oracle === undefined ? {} : {oracle: criterion.oracle}),
    task_repository: todo.task_repository ?? null,
  }}));
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
function criterionId(value: unknown): string {
  // Outcome requirements use opaque IDs. Preserve their identity through
  // configured checks and receipts; they are never executable paths.
  const result = acceptanceText(value, "acceptance criterion identifier", 128);
  acceptanceRequire(result === value && /^[A-Za-z0-9][A-Za-z0-9_.:@-]*$/.test(result),
    "acceptance criterion identifier must be an opaque token");
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

/** Scope is owner-authored identity, never inferred from prose, bindings or
 * mutable Todo role/class. Omission preserves the original Goal-wide contract
 * and its canonical bytes; an explicit subset may still contain unbound work. */
function normalizeAcceptanceScope(value: unknown): AcceptanceScope {
  const scope = canonicalAuthorityObject(value, "acceptance scope");
  if (scope.kind === "all_advancement") {
    acceptanceKeys(scope, ["kind"]);
    return {kind: "all_advancement"};
  }
  acceptanceRequire(scope.kind === "selected_work", "unknown acceptance scope kind");
  acceptanceKeys(scope, ["kind", "todo_ids"]);
  return {kind: "selected_work", todo_ids: unique(
    list(scope.todo_ids, "acceptance scope Todos", 4096, 1).map(id), "acceptance scope Todos")};
}
function acceptanceIncludes(scope: AcceptanceScope | undefined, todoId: string): boolean {
  return scope?.kind !== "selected_work" || scope.todo_ids.includes(todoId);
}

export function normalizeGoalAcceptanceDocument(value: unknown): AcceptanceDocument {
  const raw = canonicalAuthorityObject(value, "acceptance document");
  acceptanceKeys(raw, ["objective", "non_goals", "criteria", "bindings"], ["scope", "outcome_id"]);
  const scope = raw.scope === undefined ? undefined : normalizeAcceptanceScope(raw.scope);
  acceptanceRequire(canonicalAuthorityBytes(raw).length <= 262144, "acceptance document exceeds byte limit");
  const criteria = list(raw.criteria, "acceptance criteria", 64, 1).map(value => {
    const item = canonicalAuthorityObject(value, "acceptance criterion");
    acceptanceKeys(item, ["id", "description", "validation_argv"], ["validation_timeout_seconds", "validation_files", "oracle"]);
    const argv = list(item.validation_argv, "validation argv", 128, 1).map(arg => {
      acceptanceRequire(typeof arg === "string" && arg.length > 0 && arg.length <= 8192 && !arg.includes("\0"), "validation argv contains an invalid argument");
      return arg;
    });
    acceptanceRequire(argv[0].trim().length > 0, "validation argv requires an executable");
    const timeout = item.validation_timeout_seconds ?? 5;
    acceptanceRequire(Number.isSafeInteger(timeout) && Number(timeout) >= 1 && Number(timeout) <= 25, "validation timeout must be 1..25 seconds");
    const criterion = {id: criterionId(item.id), description: acceptanceText(item.description, "criterion description"),
      validation_argv: argv, validation_timeout_seconds: Number(timeout),
      validation_files: validationFiles(item.validation_files === undefined ? [] : item.validation_files)};
    return {...criterion, ...(item.oracle === undefined ? {} : {oracle: normalizeAcceptanceOracle(item.oracle, criterion)})};
  }).sort((a, b) => authorityUnicodeCompare(a.id, b.id));
  acceptanceRequire(criteria.reduce((total, item) => total + item.validation_timeout_seconds, 0) <= 25,
    "acceptance validation timeouts must total at most 25 seconds (default 5 seconds per criterion); lower the timeouts or run longer evaluations outside the completion wrapper");
  const criterionIds = unique(criteria.map(item => item.id), "criteria");
  const bindings = list(raw.bindings, "acceptance bindings", 4096).map(value => {
    const item = canonicalAuthorityObject(value, "acceptance binding");
    acceptanceKeys(item, ["todo_id", "criterion_ids"]);
    const criterion_ids = unique(list(item.criterion_ids, "binding criteria", 64, 1).map(criterionId), "binding criteria");
    acceptanceRequire(criterion_ids.every(key => criterionIds.includes(key)), "binding references an unknown criterion");
    return {todo_id: id(item.todo_id), criterion_ids};
  }).sort((a, b) => authorityUnicodeCompare(a.todo_id, b.todo_id));
  unique(bindings.map(item => item.todo_id), "bindings");
  acceptanceRequire(bindings.every(binding => acceptanceIncludes(scope, binding.todo_id)),
    "acceptance binding is outside the declared scope");
  return {...(raw.outcome_id === undefined ? {} : {outcome_id: acceptanceText(raw.outcome_id, "outcome id", 128)}),
    ...(scope === undefined ? {} : {scope}), objective: acceptanceText(raw.objective, "acceptance objective"),
    non_goals: list(raw.non_goals, "non-goals", 64).map(item => acceptanceText(item, "non-goal", 2048)), criteria, bindings};
}

/** Compact authoring references retained requirements instead of duplicating
 * objective, descriptions and work. Expanded snapshots remain readable on disk. */
export function resolveGoalAcceptanceDocument(head: JsonObject, goalId: string, value: unknown): AcceptanceDocument {
  const raw = canonicalAuthorityObject(value, "acceptance document");
  if (raw.outcome_id === undefined) return normalizeGoalAcceptanceDocument(raw);
  acceptanceKeys(raw, ["outcome_id", "criteria"]);
  const basis = goalOutcomeCheckBasis(head, goalId, raw.outcome_id);
  const requirements = basis.requirements as {id: string; description: string}[];
  const todoIds = basis.todo_ids as string[];
  acceptanceRequire(todoIds.length > 0, "link outcome work before preparing executable checks");
  const criteria = list(raw.criteria, "acceptance criteria", 64, 1).map(value => {
    const check = canonicalAuthorityObject(value, "requirement check");
    acceptanceKeys(check, ["id", "validation_argv"], ["validation_timeout_seconds", "validation_files", "oracle"]);
    const requirement = requirements.find(r => r.id === check.id);
    acceptanceRequire(requirement, "check references an unknown outcome requirement");
    return {...check, id: requirement.id, description: requirement.description};
  });
  return normalizeGoalAcceptanceDocument({outcome_id: basis.outcome_id, objective: basis.objective, non_goals: [],
    scope: {kind: "selected_work", todo_ids: todoIds}, criteria,
    bindings: todoIds.map(todo_id => ({todo_id, criterion_ids: criteria.map(c => c.id)}))});
}

function acceptanceDigestMatches(todo: JsonObject, boundDigest: string): boolean {
  if (goalAcceptanceTodoDigest(todo) === boundDigest) return true;
  // Before completion checkpoints were classified as observations, an owner
  // could confirm work that already contained one. Preserve that exact binding.
  return Object.hasOwn(todo, "completion_receipt_id") &&
    canonicalAuthoritySha256(Object.fromEntries(Object.entries(todo).filter(([key]) =>
      !NON_WORK_FIELDS.has(key) || key === "completion_receipt_id"))) === boundDigest;
}
/** Existing owner bindings persist the v0 digest, including fields later used
 * for validator revision bookkeeping and successor links. Keep that digest
 * format so previously ready bindings stay ready. When it differs, check only
 * historical states that the current append-only metadata can reconstruct;
 * changing the Todo's work declaration still requires owner confirmation. */
function acceptanceBindingMatches(todo: JsonObject, boundDigest: string): boolean {
  if (acceptanceDigestMatches(todo, boundDigest)) return true;

  // Adding a wait condition changes when existing work can resume, not which
  // owner-confirmed Goal criterion it serves. Only the absent -> present case
  // is reconstructible from the current Todo; changing an existing condition
  // remains stale because its previous value cannot be proven here.
  const scheduleVariants: JsonObject[] = [todo];
  if (Object.hasOwn(todo, "resume_when")) {
    const withoutResume = {...todo};
    delete withoutResume.resume_when;
    scheduleVariants.push(withoutResume);
  }

  const revision = todo.completion_validation_revision;
  let history: JsonObject[] = [];
  let revisionPrefixes: number[] = [];
  if (Number.isSafeInteger(revision) && Number(revision) >= 1 && Number(revision) <= 32 &&
      todo.completion_validation_required === true &&
      typeof todo.completion_validation_sha256 === "string") {
    try {
      history = completionValidationRevisionHistory(todo.completion_validation_revision_history,
        Number(revision), todo.completion_validation_sha256);
      revisionPrefixes = Array.from({length: Number(revision)}, (_, index) => index);
    } catch (error) {
      // Malformed history cannot prove any prior owner-confirmed declaration.
      if (!(error instanceof AuthorityStoreProtocolError)) throw error;
    }
  }

  for (const scheduleVariant of scheduleVariants) {
    const successors = scheduleVariant.successor_todo_ids;
    const successorVariants: JsonObject[] = [scheduleVariant];
    if (Array.isArray(successors) && successors.length <= 32 &&
        successors.every(value => typeof value === "string")) {
      for (let count = successors.length - 1; count >= 0; count--) {
        successorVariants.push({...scheduleVariant, successor_todo_ids: successors.slice(0, count)});
      }
      const withoutSuccessors = {...scheduleVariant};
      delete withoutSuccessors.successor_todo_ids;
      successorVariants.push(withoutSuccessors);
    }
    for (const successorVariant of successorVariants) {
      if (successorVariant !== todo && acceptanceDigestMatches(successorVariant, boundDigest)) return true;
      for (const priorRevision of revisionPrefixes) {
        const previous: JsonObject = {...successorVariant, completion_validation_revision: priorRevision,
          completion_validation_revision_history: history.slice(0, priorRevision)};
        if (priorRevision === 0 && history[0]?.schema_version === COMPLETION_VALIDATION_BINDING_RECEIPT_SCHEMA) {
          delete previous.completion_validation_required;
          delete previous.completion_validation_revision;
          delete previous.completion_validation_revision_history;
          Object.assign(previous, history[0].previous_validation_authority);
          if (acceptanceDigestMatches(previous, boundDigest)) return true;
          continue;
        }
        if (acceptanceDigestMatches(previous, boundDigest)) return true;
        if (priorRevision === 0) {
          delete previous.completion_validation_revision;
          delete previous.completion_validation_revision_history;
          if (acceptanceDigestMatches(previous, boundDigest)) return true;
        }
      }
    }
  }
  return false;
}
function advancement(todo: JsonObject): boolean {
  return todo.role === "agent" && (todo.task_class == null || todo.task_class === "advancement_task");
}
export function acceptanceApplies(todo: JsonObject): boolean {
  return advancement(todo) && todo.archive_state === "active" &&
    (todo.status === "open" || todo.status === "blocked") && todo.done === false;
}
export function acceptanceTodos(head: JsonObject, goalId: string): ReadonlyMap<string, JsonObject> {
  const read = new CoordinationProjectionRead(head, goalId);
  read.validateTodoReadModel();
  return read.todoIndex.todos;
}
export function goalAcceptanceWorkDigest(head: JsonObject, goalId: string, scope?: AcceptanceScope): string {
  // This semantic fingerprint is independent of provider row representation.
  // Projection and mutation callers separately validate the canonical read model.
  const todos = indexCoordinationProjectionTodos(head, goalId).todos;
  return acceptanceWorkDigest(todos, scope, head);
}
function acceptanceWorkDigest(todos: ReadonlyMap<string, JsonObject>, scope: AcceptanceScope | undefined,
  head: JsonObject): string {
  const objective = currentGoalObjective(head)?.revisions.at(-1)?.digest;
  const digest = (work: unknown) => canonicalAuthoritySha256(objective === undefined ? work : {work, objective});
  if (scope?.kind === "selected_work") return digest(scope.todo_ids.map(todo_id => ({
    todo_id, digest: todos.has(todo_id) ? goalAcceptanceTodoDigest(todos.get(todo_id)!) : null,
  })));
  return digest([...todos.values()].filter(advancement)
    .sort((left, right) => authorityUnicodeCompare(String(left.todo_id), String(right.todo_id)))
    .map(todo => ({todo_id: todo.todo_id, digest: goalAcceptanceTodoDigest(todo)})));
}

/** Absent checks need no command guard; malformed present state fails closed. */
export function readGoalAcceptanceAuthority(head: JsonObject, goalId: string): GoalAcceptanceAuthority {
  const lifecycle = readGoalAcceptanceLifecycle(head, goalId);
  if (!Object.hasOwn(head, "goal_acceptance")) {
    return lifecycle === null
      ? {kind: "legacy", state: null, configuration_revision: 0}
      : {kind: "exact", lifecycle, state: null, configuration_revision: 0, owner_matches: false};
  }
  acceptanceRequire(head.goal_id === goalId, "acceptance Goal identity mismatch");
  const state = canonicalAuthorityObject(head.goal_acceptance, "goal_acceptance");
  // Normalize old storage once at this reader; all Goals share the same
  // current runtime model. The old boolean never reaches execution logic.
  const legacy = state.schema_version === "loopx_goal_acceptance_v0" || state.schema_version === "loopx_goal_acceptance_v1";
  const owned = legacy ? state.schema_version === "loopx_goal_acceptance_v1" : state.owner_goal_ref !== null;
  acceptanceKeys(state, ["schema_version", "revision", "digest", "document", "bindings", "verification",
    ...(legacy ? ["enabled"] : []), ...(!legacy || owned ? ["owner_goal_ref"] : [])]);
  acceptanceRequire((legacy || state.schema_version === GOAL_ACCEPTANCE_SCHEMA) &&
    (!legacy || typeof state.enabled === "boolean") && Number.isSafeInteger(state.revision) && Number(state.revision) > 0,
    "invalid acceptance state version");
  acceptanceRequire((lifecycle === null) === !owned,
    "goal acceptance state and lifecycle versions must be migrated atomically");
  const ownerGoalRef = owned ? parseWireExactGoalRef(state.owner_goal_ref, "goal acceptance owner_goal_ref") : null;
  if (ownerGoalRef) acceptanceRequire(ownerGoalRef.goal_id === goalId, "acceptance owner Goal identity mismatch");
  const authority = (active: AcceptanceState | null): GoalAcceptanceAuthority => lifecycle === null
    ? {kind: "legacy", state: active, configuration_revision: Number(state.revision)}
    : {kind: "exact", lifecycle, state: active, configuration_revision: ownerGoalRef !== null && sameExactGoalRef(lifecycle.goal_ref, ownerGoalRef) ? Number(state.revision) : 0,
        owner_matches: ownerGoalRef !== null && sameExactGoalRef(lifecycle.goal_ref, ownerGoalRef)};
  if (!legacy && state.document === null) {
    acceptanceRequire(state.digest === null && Array.isArray(state.bindings) && state.bindings.length === 0 && state.verification === null,
      "removed checks must not retain active commands or verification");
    return authority(null);
  }
  const document = normalizeGoalAcceptanceDocument(state.document);
  acceptanceRequire(state.digest === canonicalAuthoritySha256(document), "acceptance contract digest mismatch");
  const declaredBindings = new Map(document.bindings.map(binding => [binding.todo_id, binding]));
  const bindings = list(state.bindings, "canonical bindings", 4096).map(value => {
    const binding = canonicalAuthorityObject(value, "canonical binding");
    acceptanceKeys(binding, ["todo_id", "todo_semantic_digest", "revision", "criterion_ids", "confirmed_by"]);
    const declared = typeof binding.todo_id === "string" ? declaredBindings.get(binding.todo_id) : undefined;
    acceptanceRequire(declared && binding.confirmed_by === "owner" && binding.revision === state.revision &&
      typeof binding.todo_semantic_digest === "string" && BARE_SHA256_PATTERN.test(binding.todo_semantic_digest) &&
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
      [receipt.contract_digest, receipt.work_digest].every(value => typeof value === "string" && BARE_SHA256_PATTERN.test(value)),
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
    if (receipt.contract_revision === state.revision) validateOracleResults(document.criteria, verification.results);
  }
  return authority(legacy && state.enabled === false ? null : {
    schema_version: GOAL_ACCEPTANCE_SCHEMA, owner_goal_ref: ownerGoalRef,
    revision: Number(state.revision), digest: String(state.digest), document, bindings, verification,
  });
}

export function readGoalAcceptance(head: JsonObject, goalId: string): AcceptanceState | null {
  const authority = readGoalAcceptanceAuthority(head, goalId);
  if (authority.kind === "legacy") return authority.state;
  return authority.owner_matches ? authority.state : null;
}

/** Private same-snapshot host inputs for read-only freshness observation.
 * They contain only already pinned files and oracle refs, never commands or
 * a new result. Public summaries keep using the acceptance projection. */
export function goalAcceptanceOracleObservationBasis(head: JsonObject, goalId: string): JsonObject | null {
  return new GoalAcceptanceRead(head, goalId).oracleBasis();
}

export function bindGoalAcceptanceStateOwner(
  state: AcceptanceState,
  ownerGoalRef: WireExactGoalRef,
): OwnedAcceptanceState {
  if (state.owner_goal_ref !== null) {
    acceptanceRequire(sameExactGoalRef(state.owner_goal_ref, ownerGoalRef),
      "goal acceptance state belongs to another Goal instance");
    return {...state, owner_goal_ref: ownerGoalRef};
  }
  return {
    ...state,
    schema_version: GOAL_ACCEPTANCE_SCHEMA,
    owner_goal_ref: ownerGoalRef,
  };
}

export function acceptanceTask(todoId: string, todo: JsonObject | undefined, state: AcceptanceState): AcceptanceTask {
  const binding = state.bindings.find(item => item.todo_id === todoId);
  return acceptanceTaskForBinding(todoId, todo, state, binding);
}
function acceptanceTaskForBinding(todoId: string, todo: JsonObject | undefined, state: AcceptanceState,
  binding: AcceptanceBinding | undefined): AcceptanceTask {
  const reason_code = !binding ? "goal_acceptance_unbound" : !todo || !acceptanceBindingMatches(todo, binding.todo_semantic_digest)
    ? "goal_acceptance_stale" : "goal_acceptance_ready";
  return {todo_id: todoId, state: !binding ? "unbound" : reason_code === "goal_acceptance_stale" ? "stale" : "ready",
    criterion_ids: binding?.criterion_ids ?? [], reason_code,
    reason: !binding ? "Owner confirmation is required for this work's acceptance association."
      : reason_code === "goal_acceptance_stale" ? "Work changed after owner confirmation; confirm its current acceptance association."
      : "The owner confirmed this work's current acceptance association.",
    applicable: acceptanceIncludes(state.document.scope, todoId) && (state.document.scope?.kind === "selected_work"
      ? todo === undefined || (todo.archive_state === "active" && todo.done === false &&
        (todo.status === "open" || todo.status === "blocked"))
      : todo !== undefined && acceptanceApplies(todo))};
}

export function normalizeAcceptanceResults(value: unknown, expectedIds?: readonly string[]): AcceptanceResult[] {
  const results = list(value, "verification results", 64, 1).map(value => {
    const item = canonicalAuthorityObject(value, "verification result");
    // Existing caller-validation runner metadata is accepted at the host seam
    // but deliberately not persisted or projected (labels can contain context).
    acceptanceKeys(item, ["criterion_id", "passed", "exit_code"], ["schema_version", "command_label", "status", "summary",
      "stdout_captured", "stderr_captured", "local_path_captured", "oracle_evidence"]);
    for (const key of ["stdout_captured", "stderr_captured", "local_path_captured"]) {
      acceptanceRequire(item[key] === undefined || item[key] === false, "validation result must not capture private output or paths");
    }
    acceptanceRequire(typeof item.passed === "boolean" && (item.exit_code === null ||
      (Number.isSafeInteger(item.exit_code) && Number(item.exit_code) >= 0 && Number(item.exit_code) <= 255)), "invalid verification result");
    acceptanceRequire(item.passed === (item.exit_code === 0), "criterion pass/fail must agree with exit code zero");
    return {criterion_id: criterionId(item.criterion_id), passed: item.passed, exit_code: item.exit_code as number | null,
      ...(item.oracle_evidence === undefined ? {} : {oracle_evidence: normalizeOracleEvidence(item.oracle_evidence)})};
  }).sort((a, b) => authorityUnicodeCompare(a.criterion_id, b.criterion_id));
  const ids = unique(results.map(item => item.criterion_id), "verification results");
  if (expectedIds) acceptanceRequire(canonicalAuthoritySha256(ids) === canonicalAuthoritySha256([...expectedIds].sort(authorityUnicodeCompare)),
    "verification results must cover exactly the required criteria");
  return results;
}

/** Reuse only within one loaded provider head; every public read starts fresh. */
export class GoalAcceptanceRead {
  private readonly head: JsonObject;
  private readonly goalId: string;
  private readonly read: CoordinationProjectionRead;
  private authority: GoalAcceptanceAuthority | undefined;
  private todoReadModel: JsonObject | undefined;
  private bindings: ReadonlyMap<string, AcceptanceBinding> | undefined;
  private readonly tasks = new Map<string, AcceptanceTask>();

  constructor(head: JsonObject, goalId: string, read = new CoordinationProjectionRead(head, goalId)) {
    this.head = head;
    this.goalId = goalId;
    this.read = read;
  }

  private readAuthority(): GoalAcceptanceAuthority {
    return this.authority ??= readGoalAcceptanceAuthority(this.head, this.goalId);
  }

  validateTodoReadModel(): JsonObject {
    this.todoReadModel ??= this.read.validateTodoReadModel();
    return structuredClone(this.todoReadModel);
  }

  private bindingIndex(state: AcceptanceState): ReadonlyMap<string, AcceptanceBinding> {
    if (this.bindings === undefined) {
      const bindings = new Map<string, AcceptanceBinding>();
      for (const binding of state.bindings) {
        if (!bindings.has(binding.todo_id)) bindings.set(binding.todo_id, binding);
      }
      this.bindings = bindings;
    }
    return this.bindings;
  }

  private task(todoId: string, todo: JsonObject | undefined, state: AcceptanceState): AcceptanceTask {
    let task = this.tasks.get(todoId);
    if (task === undefined) {
      task = acceptanceTaskForBinding(todoId, todo, state, this.bindingIndex(state).get(todoId));
      this.tasks.set(todoId, task);
    }
    return task;
  }

  oracleBasis(): JsonObject | null {
    const authority = this.readAuthority();
    const state = authority.kind === "legacy" ? authority.state
      : authority.owner_matches && authority.lifecycle.state === "active" ? authority.state : null;
    const criteria = state?.document.criteria.filter(criterion => criterion.oracle !== undefined)
      .map(criterion => ({id: criterion.id, validation_files: criterion.validation_files, oracle: criterion.oracle!})) ?? [];
    return state && criteria.length ? structuredClone({revision: state.revision, digest: state.digest, criteria}) : null;
  }

  private checks(): JsonObject {
    const authority = this.readAuthority();
    const exactProjection = authority.kind === "exact"
      ? {lifecycle_state: authority.lifecycle.state, goal_ref: authority.lifecycle.goal_ref}
      : {};
    const state = authority.kind === "legacy" ? authority.state
      : authority.owner_matches ? authority.state : null;
    if (!state || authority.kind === "exact" && authority.lifecycle.state !== "active") {
      return {...exactProjection};
    }
    this.validateTodoReadModel();
    const todos = this.read.todoIndex.todos;
    const taskIds = new Set(state.document.scope?.kind === "selected_work" ? state.document.scope.todo_ids
      : [...todos.values()].filter(advancement).map(todo => String(todo.todo_id)));
    for (const binding of state.bindings) taskIds.add(binding.todo_id);
    const tasks = [...taskIds].sort(authorityUnicodeCompare).map(key => this.task(key, todos.get(key), state));
    const held = tasks.filter(task => task.applicable && task.state !== "ready");
    const receipt = state.verification;
    let status = "unverified";
    if (receipt) {
      if (receipt.contract_revision !== state.revision || receipt.contract_digest !== state.digest ||
          receipt.work_digest !== acceptanceWorkDigest(todos, state.document.scope, this.head)) status = "stale";
      else if (receipt.results.some(item => !item.passed)) status = "failed";
      else if (receipt.todo_id !== null) status = "partial";
      else status = "accepted";
    }
    if (held.length) status = "held";
    return {...exactProjection, ...(state.document.scope === undefined ? {} : {scope: state.document.scope}),
      ...(state.document.outcome_id === undefined ? {} : {outcome_id: state.document.outcome_id}),
      revision: state.revision, digest: state.digest, objective: state.document.objective,
      non_goals: state.document.non_goals, criteria: state.document.criteria.map(criterion => ({id: criterion.id, description: criterion.description,
        ...(criterion.oracle === undefined ? {} : {oracle: projectAcceptanceOracle(criterion, receipt?.results.find(row => row.criterion_id === criterion.id), status === "stale" || status === "held")})})),
      tasks, held_todo_ids: held.map(task => task.todo_id), status,
      verification: receipt ? {operation_id: receipt.operation_id, contract_revision: receipt.contract_revision,
        contract_digest: receipt.contract_digest, todo_id: receipt.todo_id, results: receipt.results} : null};
  }

  contract(registeredAgents?: unknown): JsonObject {
    const checks = structuredClone(this.checks());
    const outcomes = projectGoalOutcomes(this.head, this.goalId, checks);
    const objective = projectGoalObjective(this.head, registeredAgents);
    return {...checks, ...(outcomes === null ? {} : {outcome_requirements: outcomes}),
      ...(objective === null ? {} : {goal_objective: objective})};
  }

  workGuards(todoIds: readonly string[]): Record<string, JsonObject> {
    const authority = this.readAuthority();
    const state = authority.kind === "legacy" ? authority.state
      : authority.owner_matches ? authority.state : null;
    if (!state) return {};
    this.validateTodoReadModel();
    const todos = this.read.todoIndex.todos, bindings = this.bindingIndex(state);
    const retiring = authority.kind === "exact" && authority.lifecycle.state === "retiring";
    return Object.fromEntries(todoIds.flatMap(todoId => {
      const todo = todos.get(todoId);
      if (!acceptanceRequired(state, todoId, todo, bindings.get(todoId) ?? null)) return [];
      const task = this.task(todoId, todo, state);
      return [[todoId, {allowed: !retiring && task.state === "ready", ...task,
        criterion_ids: [...task.criterion_ids],
        ...(retiring ? {reason_code: "goal_acceptance_goal_retiring",
          reason: "The Goal instance is retiring; acceptance cannot authorize more work."} : {}),
        revision: state.revision, digest: state.digest}]];
    }));
  }
}

/** Outcomes extend the existing acceptance read, even before checks are configured.
 * No declaration preserves the original projection byte-for-byte. */
export function projectGoalAcceptance(head: JsonObject, goalId: string, registeredAgents?: unknown): JsonObject {
  return new GoalAcceptanceRead(head, goalId).contract(registeredAgents);
}

/** Explicit selection and confirmed bindings survive mutable work fields.
 * The guarded Agent cannot escape acceptance by changing role/class/status. */
function acceptanceRequired(state: AcceptanceState, todoId: string, todo: JsonObject | undefined,
  binding: AcceptanceBinding | null = state.bindings.find(binding => binding.todo_id === todoId) ?? null): boolean {
  return acceptanceIncludes(state.document.scope, todoId) &&
    (state.document.scope?.kind === "selected_work" || binding !== null ||
      todo === undefined || acceptanceApplies(todo));
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
  return new GoalAcceptanceRead(head, goalId).workGuards(todoIds);
}

/** Trusted execution adapter only. Run these commands at the inspected provider
 * revision and commit fresh results with completion in that same provider CAS. */
export function acceptanceCompletionRequirements(head: JsonObject, goalId: string, todoId: string): AcceptanceCompletionRequirements | null {
  const authority = readGoalAcceptanceAuthority(head, goalId);
  const state = authority.kind === "legacy" ? authority.state
    : authority.owner_matches ? authority.state : null;
  if (!state) return null;
  const todo = acceptanceTodos(head, goalId).get(todoId);
  acceptanceRequire(todo, "acceptance completion Todo is missing");
  // Applicability follows the owner's binding, not the Todo's current shape:
  // `acceptanceApplies` reads task_class and status, which the guarded party
  // may rewrite. Its remaining job is to decide which *unbound* work must be
  // held, so it stays as the fallback for Todos the owner never bound.
  if (!acceptanceRequired(state, todoId, todo)) return null;
  acceptanceRequire(authority.kind !== "exact" || authority.lifecycle.state === "active",
    "goal_acceptance_goal_retiring");
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
