// Executable examples for the Verifier Hook contract before history-owner integration.
import { BARE_SHA256_PATTERN, ENVELOPED_SHA256_PATTERN } from "../../../loopx/control_plane/content_digest.ts";
import type {
  HarnessVerifierCapabilities, VerifiedCloseReader, VerifiedCloseRecommendation, VerifiedCloseRequest,
  VerifiedScopeEvidence, VerifiedScopeIdentity, VerifierHookNegotiation, VerifierHookPolicy,
} from "../../../loopx/control_plane/turn_driver/verifier_hook_contract.ts";

export function negotiateVerifierHooks(
  policy: VerifierHookPolicy,
  capabilities: HarnessVerifierCapabilities = {},
): VerifierHookNegotiation | null {
  if (policy.enabled !== true) return null;

  const lineage = capabilities.session_lineage === true && capabilities.boundary_context === true;
  const scoped = capabilities.milestone_observation === true
    && capabilities.isolated_subscope === true && capabilities.verified_return === true;
  return {
    tier: lineage ? (scoped ? "L2" : "L1") : "L0",
    pre_tool_use: policy.pre_tool_use === true && capabilities.pre_tool_use === true,
  };
}

const scopeIdentityFields = [
  "goal_id", "todo_id", "parent_epoch_ref", "child_scope_ref",
  "candidate_sha", "acceptance_digest", "verifier_version",
] as const;

function hasReference(ref: string): boolean {
  return typeof ref === "string" && ref.trim().length > 0;
}

function matchesScope(expected: VerifiedScopeIdentity, observed?: VerifiedScopeIdentity): boolean {
  return observed != null && scopeIdentityFields.every(field => observed[field] === expected[field]);
}

const unavailableFsCodes = new Set(["ENOENT", "EACCES", "EPERM", "EISDIR", "ENOTDIR"]);

function readAvailable<T>(read: () => T | undefined): T | undefined {
  try {
    return read();
  } catch (error) {
    if (
      error instanceof Error
      && "code" in error
      && typeof error.code === "string"
      && unavailableFsCodes.has(error.code)
    ) {
      return undefined;
    }
    throw error;
  }
}

function matchesEvidence(
  reader: VerifiedCloseReader,
  identity: VerifiedScopeIdentity,
  ref: string,
  role: VerifiedScopeEvidence["role"],
): boolean {
  const evidence = hasReference(ref) ? readAvailable(() => reader.readEvidence(ref)) : undefined;
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
    || !ENVELOPED_SHA256_PATTERN.test(expected.candidate_sha)
    || !BARE_SHA256_PATTERN.test(expected.acceptance_digest)
    || !hasReference(request.receipt_ref)) {
    return { action: "KeepRaw" };
  }
  const receipt = readAvailable(() => reader.readReceipt(request.receipt_ref));
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
