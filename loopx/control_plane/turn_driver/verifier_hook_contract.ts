// Host-neutral contract for the proposed verifier Hook adapter. The C1,
// working-set and capability-portfolio owners still admit every effect.
import type { QuotaGoalBoundaryReference } from "../quota/turn_envelope.ts";
import type { WorkYardstickProfile } from "../work_items/yardstick_decision.ts";

export type HarnessVerifierCapabilities = Readonly<{
  session_lineage?: boolean;
  boundary_context?: boolean;
  milestone_observation?: boolean;
  isolated_subscope?: boolean;
  verified_return?: boolean;
  pre_tool_use?: boolean;
}>;

export type VerifierHookPolicy = Readonly<{
  enabled: boolean;
  pre_tool_use?: boolean;
}>;

export type VerifierHookNegotiation = Readonly<{
  tier: "L0" | "L1" | "L2";
  pre_tool_use: boolean;
}>;

export type GoalVerifierProfile = Readonly<{
  oracle_ref: string;
  verifier_version: string;
  yardstick?: WorkYardstickProfile;
}>;

export type VerifierSolverView = Readonly<{
  objective: string;
  acceptance_refs: readonly string[];
  goal_boundary_ref: QuotaGoalBoundaryReference;
  verifier: GoalVerifierProfile;
  incumbent_ref?: string;
  falsification_refs: readonly string[];
}>;

export type VerifierTransition =
  | { kind: "completion_claim"; candidate_sha: string }
  | { kind: "yardstick_plateau_or_regression"; evidence_ref: string }
  | { kind: "proxy_order_reversed"; evidence_ref: string }
  | { kind: "protected_boundary_touch"; isolation_receipt_ref: string };

export type VerifierHookEvent = Readonly<{
  goal_id: string;
  todo_id: string;
  session_ref: string;
  epoch_ref: string;
  turn_ref?: string;
}> & (
  | { boundary: "start"; view: VerifierSolverView }
  | { boundary: "milestone_probe"; observation_ref: string }
  | { boundary: "compaction_or_subscope"; source_ref: string }
  | { boundary: "stop"; transition: Extract<VerifierTransition, { kind: "completion_claim" }> }
);

export type VerifiedScopeIdentity = Readonly<{
  goal_id: string;
  todo_id: string;
  parent_epoch_ref: string;
  child_scope_ref: string;
  /** Canonical SHA-256 digest of the candidate submitted to the verifier. */
  candidate_sha: string;
  /** Canonical SHA-256 digest of the Goal acceptance contract (outcome_context.acceptance_digest). */
  acceptance_digest: string;
  verifier_version: string;
}>;

export type VerifierScopeReceipt = Readonly<{
  identity: VerifiedScopeIdentity;
  oracle_evidence_ref: string;
  yardstick_evidence_ref?: string;
  source_ref: string;
  falsification_refs: readonly string[];
}> & (
  | { verdict: "passed"; summary_ref: string }
  | { verdict: "failed"; counterexample_ref: string }
  | { verdict: "unknown" }
);

export type VerifiedCloseRequest = Readonly<{
  identity: VerifiedScopeIdentity;
  receipt_ref: string;
  overlap: "high" | "low" | "unknown";
  open_obligations: boolean;
}>;

export type VerifiedCloseRecommendation =
  | { action: "KeepRaw"; counterexample_ref?: string }
  | { action: "FoldToReceipt"; receipt_ref: string; source_ref: string; summary_ref: string };

export type VerifiedScopeEvidence = Readonly<{
  ref: string;
  role: "oracle_evidence" | "source" | "summary" | "counterexample";
  identity: VerifiedScopeIdentity;
}>;

// Readers return undefined for unavailable authority records.
export type VerifiedCloseReader = Readonly<{
  readReceipt: (ref: string) => VerifierScopeReceipt | undefined;
  readEvidence: (ref: string) => VerifiedScopeEvidence | undefined;
}>;
