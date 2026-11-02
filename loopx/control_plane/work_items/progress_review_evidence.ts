/** Criterion selection from authorized current facts; observation grants no authority. */
import {isDeepStrictEqual} from "node:util";
import {inspectLocalGoalAcceptance} from "../goals/acceptance_authority.ts";
import type {JsonObject} from "../effect_program.ts";
import {jsonObject, requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
export function progressReviewCriterionBasis(params: JsonObject): JsonObject {
  function requireThat(condition: unknown, message: string): asserts condition {
    if (!condition) throw new EffectRuntimeRequestError(message);
  }
  const requested = params.criterion_ids;
  if (params.requirements === null) {
    requireThat(Array.isArray(params.acceptance) && params.acceptance.length > 0 && params.acceptance.length <= 128
      && params.acceptance.every(row => typeof row === "string" && row.length > 0 && row.length <= 32768),
    "bounded operator study criteria required");
    return {acceptance: params.acceptance, binding: {origin: "operator_study",
      criteria_sha256: canonicalAuthoritySha256(params.acceptance)}};
  }
  const basis = requireJsonObject(params.requirements, "current acceptance requirements");
  requireThat(Array.isArray(requested) && requested.length > 0 && requested.length <= 128
    && requested.every(row => typeof row === "string" && row.length > 0)
    && new Set(requested).size === requested.length && Array.isArray(basis.criteria),
  "explicit current criterion selection required");
  const criteria = basis.criteria.map(raw => requireJsonObject(raw, "canonical acceptance criterion"))
    .filter(row => requested.includes(row.id));
  requireThat(criteria.length === requested.length && new Set(criteria.map(row => row.id)).size === criteria.length,
    "selected criteria must belong to this current task acceptance scope");
  requireThat(criteria.every(row => typeof row.description === "string" && row.description.length > 0
    && row.description.length <= 32768), "bounded canonical criterion descriptions required");
  requireThat(typeof basis.contract_digest === "string" && Number.isInteger(basis.contract_revision)
    && typeof basis.todo_id === "string" && typeof basis.todo_semantic_digest === "string",
  "current canonical acceptance identity required");
  requireThat(typeof params.goal_id === "string" && params.goal_id.length > 0
    && typeof params.agent_id === "string" && params.agent_id.length > 0, "canonical goal and agent identity required");
  return {acceptance: criteria.map(row => row.description), binding: {origin: "goal_acceptance",
    goal_id: params.goal_id, agent_id: params.agent_id,
    todo_id: basis.todo_id, contract_digest: basis.contract_digest, contract_revision: basis.contract_revision,
    todo_semantic_digest: basis.todo_semantic_digest, criterion_ids: criteria.map(row => row.id),
    criteria_sha256: canonicalAuthoritySha256(criteria)}};
}

/** Decode path-free observation metadata; this does not verify model answers. */
export function progressReviewEvidenceScope(params: JsonObject): JsonObject {
  const scope = requireJsonObject(params.scope, "progress-review evidence scope");
  const binding = requireJsonObject(scope.criterion_binding, "progress-review criterion binding");
  const fail = (ok: boolean) => {
    if (!ok) throw new EffectRuntimeRequestError("invalid bounded progress-review evidence scope");
  };
  fail(scope.coverage === "declared_file_net_change" && Array.isArray(scope.files) && scope.files.length <= 32
    && scope.files.every(ref => typeof ref === "string" && ref.length > 0 && ref.length <= 1024
      && !ref.startsWith("/") && !ref.includes("\\") && !ref.split("/").includes("..")));
  fail(binding.origin === "operator_study" || binding.origin === "goal_acceptance");
  fail(typeof binding.criteria_sha256 === "string" && BARE_SHA256_PATTERN.test(binding.criteria_sha256));
  const normalized: JsonObject = {origin: binding.origin, criteria_sha256: binding.criteria_sha256};
  if (binding.origin === "goal_acceptance") {
    fail(typeof binding.goal_id === "string" && binding.goal_id.length > 0 && binding.goal_id.length <= 200
      && typeof binding.agent_id === "string" && binding.agent_id.length > 0 && binding.agent_id.length <= 200
      && typeof binding.todo_id === "string" && binding.todo_id.length > 0 && binding.todo_id.length <= 200
      && typeof binding.contract_digest === "string" && BARE_SHA256_PATTERN.test(binding.contract_digest)
      && Number.isInteger(binding.contract_revision) && Number(binding.contract_revision) >= 1
      && typeof binding.todo_semantic_digest === "string" && BARE_SHA256_PATTERN.test(binding.todo_semantic_digest)
      && Array.isArray(binding.criterion_ids) && binding.criterion_ids.length > 0 && binding.criterion_ids.length <= 128
      && binding.criterion_ids.every(id => typeof id === "string" && id.length > 0 && id.length <= 200));
    const run = requireJsonObject(params.run, "criterion-bound run identity");
    fail(params.goal_id === binding.goal_id && run.agent_id === binding.agent_id && run.todo_id === binding.todo_id);
    for (const key of ["goal_id", "agent_id", "todo_id", "contract_digest", "contract_revision",
      "todo_semantic_digest", "criterion_ids"]) {
      normalized[key] = binding[key];
    }
  }
  return {scope: {criterion_binding: normalized, coverage: scope.coverage, files: scope.files}};
}

/**
 * Qualify a batch in the Python loader's newest-first order. Its
 * progress_review_receipt_order_key is the single ordering owner; the first
 * qualified receipt is newest, including stable ties. Per-Todo reads are
 * isolated, not an atomic snapshot.
 */
export async function qualifyProgressReviewContext(params: JsonObject): Promise<JsonObject> {
  const goalId = requireNonEmptyString(params.goal_id, "progress-review goal_id");
  const root = requireNonEmptyString(params.runtime_root, "progress-review runtime_root");
  if (!Array.isArray(params.receipts)) {
    throw new EffectRuntimeRequestError("progress-review receipts must be an array");
  }
  if (params.contract_revision !== null && typeof params.contract_revision !== "string") {
    throw new EffectRuntimeRequestError("progress-review contract_revision must be a string or null");
  }
  const currentByTodo = new Map<string, unknown>();
  const selected: JsonObject[] = [];
  const selectedIndices: number[] = [];
  let newest: JsonObject | null = null;
  let stale = 0;
  const pinned = params.contract_revision;
  for (const [index, raw] of params.receipts.entries()) {
    let receipt = requireJsonObject(raw, "loaded progress-review receipt");
    const binding = jsonObject(jsonObject(receipt.evidence_scope)?.criterion_binding);
    if (binding?.origin === "goal_acceptance") {
      const todoId = requireNonEmptyString(binding.todo_id, "criterion binding todo_id");
      if (!currentByTodo.has(todoId)) {
        let requirements: unknown = null;
        try {
          const observation = await inspectLocalGoalAcceptance({goal_id: goalId, runtime_root: root, todo_id: todoId,
            ...(params.goal_instance_id == null ? {} : {
              goal_ref: {goal_id: goalId, goal_instance_id: params.goal_instance_id},
            })});
          requirements = observation.completion_requirements ?? null;
        } catch {
          // One unavailable Todo must not suppress other current observations.
        }
        currentByTodo.set(todoId, requirements);
      }
      let current = false;
      try {
        const requirements = currentByTodo.get(todoId);
        if (requirements !== null) {
          const selected = progressReviewCriterionBasis({requirements, criterion_ids: binding.criterion_ids,
            acceptance: [], goal_id: goalId, agent_id: requireJsonObject(receipt.run, "receipt run").agent_id}).binding;
          current = isDeepStrictEqual(selected, binding);
        }
      } catch {
        // Removed criteria and unavailable bases withdraw both drift signals.
      }
      receipt = {...receipt, criterion_current: current};
      if (!current) {
        receipt = {...receipt, status: "stale", reason: "criterion_basis_unavailable",
          drift_signal: {noul: null, choice: null}};
      }
    }
    if (newest === null) newest = receipt;
    if (!pinned || receipt.contract_revision === pinned) {
      selected.push(receipt);
      selectedIndices.push(index);
      if (receipt.criterion_current === false) stale += 1;
    } else {
      stale += 1;
    }
  }
  return {selected, selected_indices: selectedIndices, stale,
    newest_contract_revision: newest?.contract_revision ?? null};
}
