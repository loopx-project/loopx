import type {JsonObject} from "../effect_program.ts";
import {requireStringLiteral} from "../runtime_decode.ts";
import {HANDOFF_MODES} from "./handoff_mode_policy.ts";
import {planHandoffPolicyMigration, type HandoffPolicyMigrationPlan} from "./handoff_policy_migration.ts";

// The historical implicit strategy is retained only for saved v0 plans.
// Current CLI previews explicitly select preserve, including registration facts.
export const PROMOTION_HANDOFF_MODE_MIGRATIONS = [
  "require_existing_hard_lease", "preserve", "hard_lease",
] as const;
export type PromotionHandoffModeMigration = typeof PROMOTION_HANDOFF_MODE_MIGRATIONS[number];
export interface PromotionHandoffMigrationPlan extends HandoffPolicyMigrationPlan {
  readonly strategy: PromotionHandoffModeMigration;
}
export function normalizePromotionHandoffModeMigration(value: unknown): PromotionHandoffModeMigration {
  return requireStringLiteral(value ?? "require_existing_hard_lease", PROMOTION_HANDOFF_MODE_MIGRATIONS,
    "handoff_mode_migration");
}
export function planPromotionHandoffMigration(head: JsonObject, goalId: string, strategyValue: unknown,
  registeredAgents: readonly string[], observedAt: Date): PromotionHandoffMigrationPlan {
  const strategy = normalizePromotionHandoffModeMigration(strategyValue);
  const previous = requireStringLiteral(head.handoff_mode ?? "legacy", HANDOFF_MODES, "source handoff_mode");
  // Saved v0 hard-only plans did not carry registration facts. Preserve their
  // original decision; new CLI plans always take the validated explicit branch.
  if (strategy === "require_existing_hard_lease") {
    const plan = planHandoffPolicyMigration(head, goalId, "hard_lease", registeredAgents, observedAt, false);
    if (previous !== "hard_lease") return {...plan, strategy, ready: false,
      reason_code: "local_authority_promotion_requires_hard_lease",
      reason: "saved v0 promotion requires an existing hard_lease source",
      conflicts: [{kind: "source_mode_mismatch", reason_code: "local_authority_promotion_requires_hard_lease",
        previous_mode: previous, required_mode: "hard_lease"}, ...plan.conflicts]};
    return {...plan, strategy};
  }
  return {...planHandoffPolicyMigration(head, goalId,
    strategy === "hard_lease" ? "hard_lease" : previous, registeredAgents, observedAt), strategy};
}
export function publicPromotionHandoffMigrationPlan(
  plan: PromotionHandoffMigrationPlan,
): JsonObject {
  return {
    schema_version: "loopx_promotion_handoff_migration_plan_v0",
    ready: plan.ready,
    ...(plan.reason_code === undefined ? {} : {reason_code: plan.reason_code}),
    ...(plan.reason === undefined ? {} : {reason: plan.reason}),
    strategy: plan.strategy,
    previous_mode: plan.previous_mode,
    target_mode: plan.target_mode,
    changed: plan.changed,
    preserved_claim_count: plan.preserved_claims.length,
    preserved_claims: [...plan.preserved_claims],
    lease_dispositions: [...plan.lease_dispositions],
    conflicts: [...plan.conflicts],
    target_projection_sha256: plan.target_projection_sha256,
  };
}
