import { z } from "zod";

const nullableText = z.string().nullable();
// Readback only: the Goal owner supplies association and verification states.
export const oracleEvidenceSchema = z.object({source_id: z.string(), source_digest: z.string(), checked_at: z.string(),
  input_versions: z.array(z.object({ref: z.string(), sha256: z.string()})),
  output_versions: z.array(z.object({ref: z.string(), sha256: z.string()}))});
export type OracleEvidence = z.infer<typeof oracleEvidenceSchema>;
export const goalAcceptanceContractSchema = z.discriminatedUnion("enabled", [
  z.object({ enabled: z.literal(false) }),
  z.object({
    enabled: z.literal(true), revision: z.number().int().positive(), digest: z.string().min(1), objective: z.string(),
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
  }),
]);
export type GoalAcceptanceContract = z.infer<typeof goalAcceptanceContractSchema>;

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
export function projectGoalAcceptanceCriteria(contract: Extract<NonNullable<GoalAcceptanceObservation["goal_acceptance_contract"]>, {enabled: true}>) {
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
export function goalAcceptanceDisplayStatus(contract: Extract<GoalAcceptanceContract, {enabled: true}>) {
  if (contract.status !== "accepted" && contract.status !== "partial") return contract.status;
  const criteria = projectGoalAcceptanceCriteria(contract);
  if (criteria.some(item => item.state === "failed")) return "failed";
  if (criteria.some(item => item.state === "stale")) return "stale";
  if (criteria.some(item => item.state === "unverified")) return "unverified";
  return contract.status;
}
