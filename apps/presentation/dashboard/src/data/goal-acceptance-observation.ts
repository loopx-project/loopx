import { z } from "zod";

const nullableText = z.string().nullable();
// Readback only: the Goal owner supplies association and verification states.
export const oracleEvidenceSchema = z.object({source_id: z.string(), source_digest: z.string(), checked_at: z.string(),
  input_versions: z.array(z.object({ref: z.string(), sha256: z.string()})),
  output_versions: z.array(z.object({ref: z.string(), sha256: z.string()}))});
export type OracleEvidence = z.infer<typeof oracleEvidenceSchema>;
export const outcomeRequirementsSchema = z.object({schema_version: z.literal("loopx_goal_outcomes_v0"),
  revision: z.number().int().positive(), current_lifetime: z.boolean(), execution_authority_granted: z.literal(false), goal_completion_assessed: z.literal(false),
  outcomes: z.array(z.object({id: z.string(), owner_agent_id: z.string(), objective: z.string(), revision: z.number().int().positive(),
    requirements: z.array(z.object({id: z.string(), description: z.string(),
      agent_review: z.object({id: z.string(), conclusion: z.enum(["met", "unmet", "uncertain"]), evidence_refs: z.array(z.string()), note: z.string(), basis_current: z.boolean(), independently_verified: z.literal(false)}).optional(),
      planning_satisfied: z.boolean(), status: z.enum(["unverified", "stale", "failed", "partial", "accepted", "held"])})),
    todo_ids: z.array(z.string()), sources: z.array(z.object({ref: z.string(), summary: z.string()})),
    work: z.array(z.object({todo_id: z.string(), status: z.string()})),
  })),
});
export type OutcomeRequirements = z.infer<typeof outcomeRequirementsSchema>;
export const goalObjectiveSchema = z.object({revision: z.number().int().positive(), digest: z.string(), objective: z.string(),
  original_objective: z.string(), reason: z.string(), source_ref: z.string(),
  peer_adoption: z.array(z.object({agent_id: z.string(), revision: z.number().nullable(), current: z.boolean()})),
  acceptance_gaps: z.array(z.record(z.string(), z.unknown())),
});
// Both configured checks and the default outcome projection retain the native
// lifetime identity. A cached editor must not follow a reused Goal id.
const goalAcceptanceSource = {
  goal_objective: goalObjectiveSchema.optional(),
  lifecycle_state: z.string().optional(),
  goal_ref: z.object({goal_id: z.string(), goal_instance_id: z.string()}).nullable().optional(),
};
const goalAcceptanceChecksSchema = z.object({
    ...goalAcceptanceSource,
    outcome_requirements: outcomeRequirementsSchema.optional(),
    outcome_id: z.string().optional(),
    revision: z.number().int().positive(), digest: z.string().min(1), objective: z.string(),
    scope: z.discriminatedUnion("kind", [
      z.object({ kind: z.literal("all_advancement") }).strict(),
      z.object({ kind: z.literal("selected_work"), todo_ids: z.array(z.string().min(1)).min(1) }).strict(),
    ]).optional(),
    non_goals: z.array(z.string()), held_todo_ids: z.array(z.string()),
    status: z.enum(["unverified", "stale", "failed", "partial", "accepted", "held"]),
    criteria: z.array(z.object({ id: z.string(), description: z.string(), oracle: z.object({
      kind: z.literal("owner_pinned_oracle"), source_id: z.string(), source_digest: z.string(),
      input_refs: z.array(z.string()), output_refs: z.array(z.string()),
      state: z.enum(["unverified", "recorded", "current", "stale", "failed", "unavailable"]),
    }).optional() })),
    tasks: z.array(z.object({
      todo_id: z.string(), state: z.enum(["ready", "unbound", "stale"]),
      criterion_ids: z.array(z.string()), reason: z.string().optional(),
      reason_code: z.string().optional(), applicable: z.boolean().optional(),
    })),
    verification: z.object({
      operation_id: z.string(), contract_revision: z.number().int().positive(), contract_digest: z.string(),
      todo_id: z.string().nullable(),
      results: z.array(z.object({ criterion_id: z.string(), passed: z.boolean(), exit_code: z.number().int().nullable(), oracle_evidence: oracleEvidenceSchema.optional() })),
    }).nullable(),
  });
export type GoalAcceptanceChecks = z.infer<typeof goalAcceptanceChecksSchema>;
export const goalAcceptanceContractSchema = z.preprocess(value => {
  // Read retained pre-upgrade snapshots at the boundary, never as a feature flag.
  if (!value || typeof value !== "object" || !("enabled" in value)) return value;
  const legacy = value as Record<string, unknown>;
  if (typeof legacy.enabled !== "boolean") return null;
  if (!legacy.enabled) return {outcome_requirements: legacy.outcome_requirements,
    goal_ref: legacy.goal_ref, lifecycle_state: legacy.lifecycle_state};
  const {enabled: _oldSwitch, ...current} = legacy;
  return current;
}, z.union([goalAcceptanceChecksSchema, z.object({
  criteria: z.never().optional(), outcome_requirements: outcomeRequirementsSchema.optional(),
  ...goalAcceptanceSource,
}).strict()]));
export type GoalAcceptanceContract = z.infer<typeof goalAcceptanceContractSchema>;

export function hasConfiguredGoalChecks(value: GoalAcceptanceContract | undefined): value is GoalAcceptanceChecks {
  return value?.criteria !== undefined;
}

export const goalAcceptanceObservationSchema = z.object({
  schema_version: z.literal("goal_acceptance_observation_projection_v0"),
  goal_id: z.string(),
  read_only: z.literal(true),
  acceptance_assessed: z.literal(false),
  coverage: z.enum(["partial", "unavailable"]),
  missing_sources: z.array(z.string()),
  truncated: z.boolean(),
  historical_progress: z.array(z.object({ kind: z.string(), observed_at: nullableText, source: z.string(), evidence_refs: z.array(z.string()) })),
  acceptance_gaps: z.array(z.object({
    kind: z.string(), owner: nullableText, reason: nullableText, evidence_required: nullableText, observed_at: nullableText, source: z.string(),
    reason_code: z.string().optional(), resolution_hint: z.string().optional(),
    component_checks: z.object({
      checkpoint_satisfied: z.boolean(), checkpoint_fresh: z.boolean(),
      path_outcome_valid: z.boolean(), evidence_refs_present: z.boolean(),
      final_outcome_claim_present: z.boolean(), no_reported_outcome_gap: z.boolean(),
    }).optional(),
  })),
  guards: z.array(z.object({ kind: z.string(), todo_id: nullableText, blocks_agent: nullableText, owner: nullableText, reason: nullableText, evidence_required: nullableText, decision_scope: nullableText })),
  next_action: nullableText,
  next_action_source: nullableText,
  goal_acceptance_contract: goalAcceptanceContractSchema.optional(),
});
export type GoalAcceptanceObservation = z.infer<typeof goalAcceptanceObservationSchema>;

/** Present the current owner observation without promoting retained checks.
 * Equal contract revisions alone cannot establish unchanged work. */
export function projectGoalAcceptanceCriteria(contract: GoalAcceptanceChecks) {
  const receipt = contract.verification;
  const sameContract = receipt?.contract_revision === contract.revision && receipt.contract_digest === contract.digest;
  const results = new Map(receipt?.results.map(result => [result.criterion_id, result.passed]));
  return contract.criteria.map(criterion => {
    let state: typeof contract.status = "unverified";
    if (contract.status === "held") state = "held";
    else if (contract.status !== "unverified" && receipt && results.has(criterion.id)) {
      if (contract.status === "stale" || !sameContract) state = "stale";
      else if (results.get(criterion.id) === false) state = "failed";
      else if (receipt.todo_id !== null || contract.status === "partial") state = "partial";
      else state = "accepted";
    }
    if ((state === "accepted" || state === "partial") && criterion.oracle) {
      if (criterion.oracle.state === "stale") state = "stale";
      else if (criterion.oracle.state === "failed") state = "failed";
      else if (criterion.oracle.state !== "current") state = "unverified";
    }
    return {...criterion, state};
  });
}

/** Display freshness only; this does not alter canonical acceptance or gates. */
export function goalAcceptanceDisplayStatus(contract: GoalAcceptanceChecks) {
  if (contract.status !== "accepted" && contract.status !== "partial") return contract.status;
  const criteria = projectGoalAcceptanceCriteria(contract);
  if (criteria.some(item => item.state === "failed")) return "failed";
  if (criteria.some(item => item.state === "stale")) return "stale";
  if (criteria.some(item => item.state === "unverified")) return "unverified";
  return contract.status;
}
