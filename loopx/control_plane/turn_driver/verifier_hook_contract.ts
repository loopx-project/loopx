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

export type VerifiedScopeEvidence = Readonly<{
  ref: string;
  role: "oracle_evidence" | "source" | "summary" | "counterexample";
  identity: VerifiedScopeIdentity;
}>;

export type VerifiedCloseReader = Readonly<{
  readReceipt: (ref: string) => VerifierScopeReceipt | undefined;
  readEvidence: (ref: string) => VerifiedScopeEvidence | undefined;
}>;

const scopeIdentityFields = [
  "goal_id", "todo_id", "parent_epoch_ref", "child_scope_ref", "candidate_sha", "verifier_version",
] as const;

function hasReference(ref: string): boolean {
  return typeof ref === "string" && ref.trim().length > 0;
}

function matchesScope(expected: VerifiedScopeIdentity, observed?: VerifiedScopeIdentity): boolean {
  return observed != null && scopeIdentityFields.every(field => observed[field] === expected[field]);
}

function matchesEvidence(
  reader: VerifiedCloseReader,
  identity: VerifiedScopeIdentity,
  ref: string,
  role: VerifiedScopeEvidence["role"],
): boolean {
  const evidence = hasReference(ref) ? reader.readEvidence(ref) : undefined;
  return evidence != null && evidence.ref === ref && evidence.role === role && matchesScope(identity, evidence.identity);
}

// The adapter resolves receipts and evidence from their independent stores.
// Evidence describes its recorded role and scope and is absent when unreadable.
export function recommendVerifiedClose(
  request: VerifiedCloseRequest,
  reader: VerifiedCloseReader,
): VerifiedCloseRecommendation {
  const expected = request.identity;
  if (!expected || !scopeIdentityFields.every(field => hasReference(expected[field]))
    || !hasReference(request.receipt_ref)) {
    return { action: "KeepRaw" };
  }
  const receipt = reader.readReceipt(request.receipt_ref);
  if (!receipt || !matchesScope(expected, receipt.identity)) {
    return { action: "KeepRaw" };
  }
  if (receipt.verdict === "failed") {
    if (matchesEvidence(reader, expected, receipt.counterexample_ref, "counterexample")) {
      return { action: "KeepRaw", counterexample_ref: receipt.counterexample_ref };
    }
    return { action: "KeepRaw" };
  }
  if (receipt.verdict !== "passed" || request.open_obligations !== false || request.overlap !== "low"
    || !matchesEvidence(reader, expected, receipt.oracle_evidence_ref, "oracle_evidence")
    || !matchesEvidence(reader, expected, receipt.source_ref, "source")
    || !matchesEvidence(reader, expected, receipt.summary_ref, "summary")) {
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
