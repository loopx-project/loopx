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

export function negotiateVerifierHooks(
  policy: VerifierHookPolicy,
  capabilities: HarnessVerifierCapabilities = {},
): { tier: "L0" | "L1" | "L2"; pre_tool_use: boolean } | null {
  if (policy.enabled !== true) return null;

  const lineage = capabilities.session_lineage === true && capabilities.boundary_context === true;
  const scoped = capabilities.milestone_observation === true
    && capabilities.isolated_subscope === true && capabilities.verified_return === true;
  return {
    tier: lineage ? (scoped ? "L2" : "L1") : "L0",
    pre_tool_use: policy.pre_tool_use === true && capabilities.pre_tool_use === true,
  };
}

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
  candidate_sha: string;
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

export type VerifiedCloseReader = Readonly<{
  readReceipt: (ref: string) => VerifierScopeReceipt | undefined;
  canReadEvidence: (ref: string) => boolean;
}>;

function hasReference(ref: string): boolean {
  return typeof ref === "string" && ref.trim().length > 0;
}

// The adapter binds both reads to their authoritative stores and current scope.
// It reports absent, inaccessible or mismatched evidence as unreadable.
export function recommendVerifiedClose(
  request: VerifiedCloseRequest,
  reader: VerifiedCloseReader,
): VerifiedCloseRecommendation {
  const receipt = hasReference(request.receipt_ref) ? reader.readReceipt(request.receipt_ref) : undefined;
  const expected = request.identity;
  const observed = receipt?.identity;
  if (!receipt || !observed
    || observed.goal_id !== expected.goal_id || observed.todo_id !== expected.todo_id
    || observed.parent_epoch_ref !== expected.parent_epoch_ref
    || observed.child_scope_ref !== expected.child_scope_ref
    || observed.candidate_sha !== expected.candidate_sha
    || observed.verifier_version !== expected.verifier_version) {
    return { action: "KeepRaw" };
  }
  if (receipt.verdict === "failed") {
    if (hasReference(receipt.counterexample_ref) && reader.canReadEvidence(receipt.counterexample_ref) === true) {
      return { action: "KeepRaw", counterexample_ref: receipt.counterexample_ref };
    }
    return { action: "KeepRaw" };
  }
  if (receipt.verdict !== "passed" || request.open_obligations !== false || request.overlap !== "low"
    || ![receipt.oracle_evidence_ref, receipt.source_ref, receipt.summary_ref].every(
      ref => hasReference(ref) && reader.canReadEvidence(ref) === true
    )) {
    return { action: "KeepRaw" };
  }
  return {
    action: "FoldToReceipt",
    receipt_ref: request.receipt_ref,
    source_ref: receipt.source_ref,
    summary_ref: receipt.summary_ref,
  };
}

export type DomainPluginCandidate = Readonly<{
  artifact_digest: string;
  capability_ref: string;
  kind: "yardstick" | "diagnostic_probe" | "verification_recipe";
}>;

// These observations index the existing portfolio and verifier owners. The
// solver can propose staging only. Activated versions are read from configuration.
export type DomainPluginTrial =
  | { stage: "staging"; candidate: DomainPluginCandidate }
  | { stage: "trial"; candidate: DomainPluginCandidate; trial_receipt_ref: string }
  | { stage: "adopted"; candidate: DomainPluginCandidate; adoption_receipt_ref: string }
  | { stage: "retired"; candidate: DomainPluginCandidate; retirement_receipt_ref: string };
