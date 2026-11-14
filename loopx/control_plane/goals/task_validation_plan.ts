/** Read-only composition of checks for a canonical Todo. The host must read the
 * basis and private declaration from their current owners before calling this
 * pure plan; caller/model input is not authority and the plan grants no execution
 * or write permission. */
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {acceptanceValidationEffects, type AcceptanceCompletionRequirements} from "./acceptance_contract.ts";
import {goalAcceptanceTodoDigest} from "./acceptance_work_basis.ts";
import {normalizeTodoCompletionValidationDeclaration} from "../todos/completion_validation_declaration.ts";

function requireThat(ok: unknown, message: string): asserts ok {
  if (!ok) throw new EffectRuntimeRequestError(message);
}

/** An explicit null requirement means owner-resolved out-of-scope/disabled
 * checks. Missing, unbound or stale owner acceptance must never fall back to a
 * Todo-only declaration. Required Todo checks remain cumulative with Goal checks. */
export function canonicalTaskValidationPlan(params: JsonObject): JsonObject {
  const basis = requireJsonObject(params.basis, "canonical validation basis");
  const todo = requireJsonObject(basis.todo, "canonical task Todo");
  requireThat(typeof params.todo_id === "string" && params.todo_id.length > 0 && params.todo_id.length <= 4096
    && basis.status === "loaded" && typeof basis.provider_revision === "string"
    && basis.provider_revision.length > 0 && basis.provider_revision.length <= 4096
    && todo.todo_id === params.todo_id, "canonical task requires a current matching canonical Todo");
  requireThat(Object.hasOwn(basis, "completion_requirements"), "canonical acceptance scope required");
  const requirements = basis.completion_requirements === null ? null
    : requireJsonObject(basis.completion_requirements, "canonical acceptance requirements");
  if (requirements !== null) requireThat(requirements.todo_id === todo.todo_id
    && Array.isArray(requirements.criteria) && requirements.criteria.length > 0,
  "canonical task requires matching owner acceptance criteria");
  const unavailable = (reason: string) => ({todo_id: todo.todo_id, state: "unbound",
    source: null, reason, effects: [], canonical_done: false});
  const effects: JsonObject[] = requirements === null ? []
    : acceptanceValidationEffects(requirements as AcceptanceCompletionRequirements, todo)
      .map(row => ({...requireJsonObject(row.effect, "acceptance validation effect"), criterion_id: row.criterion_id}));
  if (todo.completion_validation_required === true) {
    if (params.declaration === null) return unavailable("completion_validation_declaration_unavailable");
    const declaration = requireJsonObject(params.declaration, "private validation declaration");
    const normalized = normalizeTodoCompletionValidationDeclaration(declaration, {
      strict_fields: true, require_command: true, require_canonical_input: true,
    });
    if (!normalized.ok || canonicalAuthoritySha256(declaration) !== todo.completion_validation_sha256)
      return unavailable("completion_validation_declaration_mismatch");
    effects.push({kind: "caller_validation", validation_command: normalized.value.validation_command,
      validation_argv: normalized.value.validation_command_argv,
      validation_label: normalized.value.validation_label,
      validation_timeout_seconds: normalized.value.validation_timeout_seconds,
      validation_declaration_sha256: todo.completion_validation_sha256,
      task_repository: todo.task_repository ?? null});
  } else {
    requireThat(params.declaration === null && todo.completion_validation_sha256 == null,
      "Todo without canonical validation authority cannot supply a declaration");
    if (requirements === null) return unavailable("no_declaration");
  }
  const source = requirements === null ? "todo_validation" : "goal_acceptance";
  // Definition identity only; host checks must pass before this observation is
  // exposed as a current check. Complete requirements and effects stay hashed.
  const observation = {source,
    basis_sha256: canonicalAuthoritySha256({todo_id: todo.todo_id, requirements, effects}),
    check_count: effects.length,
    pinned_file_count: effects.reduce((count, effect) => count
      + (Array.isArray(effect.validation_files) ? effect.validation_files.length : 0), 0)};
  // The acceptance owner classifies work. Unknown future work fields remain
  // inputs; progress/provider changes do not replace the task, while the current
  // claim and lifecycle fence this particular validation attempt.
  const task_basis_sha256 = canonicalAuthoritySha256({work: goalAcceptanceTodoDigest(todo),
    claimed_by: todo.claimed_by ?? null, role: todo.role ?? null,
    status: todo.status, done: todo.done, archive_state: todo.archive_state ?? null});
  return {todo_id: todo.todo_id, state: "ready", source, observation, task_basis_sha256,
    effects, canonical_done: todo.done === true && todo.status === "done"};
}
