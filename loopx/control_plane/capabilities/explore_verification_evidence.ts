/** Optional source readback on the existing candidate execution observation.
 * Host names are opaque. Each capability claim and cost belongs to this
 * candidate and cites a retained source. Authority is resolved by the reader.
 */
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {isExploreEvidenceRef} from "./explore_evidence_ref.ts";

export type VerificationEvidenceRef = {ref: string; sha256: string};
export type VerificationProof = VerificationEvidenceRef & {criterion_id: string};
const proofKeys = ["external_verifier", "warm_session_cache", "white_box_hooks", "receipt_compaction"] as const;
const costKeys = ["warm_cache_tokens", "cold_cache_tokens", "verifier_seconds", "human_confirmations", "tool_interceptions"] as const;
export type CandidateVerificationEvidence = {
  baseline?: VerificationEvidenceRef;
  environment?: VerificationEvidenceRef;
  source?: VerificationEvidenceRef;
  host?: string;
  proofs?: Partial<Record<typeof proofKeys[number], VerificationProof>>;
  costs?: Partial<Record<typeof costKeys[number], {value: number; evidence_ref: string}>>;
};

function evidence(value: unknown, allowedRefs: ReadonlySet<string>): VerificationEvidenceRef {
  const row = requireJsonObject(value, "verification evidence reference");
  if (Object.keys(row).length !== 2 || typeof row.ref !== "string" || !allowedRefs.has(row.ref)
      || typeof row.sha256 !== "string" || !BARE_SHA256_PATTERN.test(row.sha256)) {
    throw new EffectRuntimeRequestError("verification evidence requires a captured reference and its exact SHA-256");
  }
  return {ref: row.ref, sha256: row.sha256};
}

export function normalizeCandidateVerificationEvidence(value: unknown, evidenceRefs: readonly string[]): CandidateVerificationEvidence {
  const row = requireJsonObject(value, "candidate verification evidence");
  const allowedRefs = new Set(evidenceRefs);
  const result: CandidateVerificationEvidence = {};
  for (const key of Object.keys(row)) {
    if (!["baseline", "environment", "source", "host", "proofs", "costs"].includes(key)) {
      throw new EffectRuntimeRequestError("unsupported candidate verification evidence field");
    }
  }
  for (const key of ["baseline", "environment", "source"] as const) {
    if (Object.hasOwn(row, key)) result[key] = evidence(row[key], allowedRefs);
  }
  if (Object.hasOwn(row, "host")) {
    if (typeof row.host !== "string" || !isExploreEvidenceRef(row.host) || !/^[a-z0-9][a-z0-9_.-]{0,63}$/.test(row.host)) {
      throw new EffectRuntimeRequestError("verification host requires an opaque identifier");
    }
    result.host = row.host;
  }
  if (Object.hasOwn(row, "proofs")) {
    const proofs = requireJsonObject(row.proofs, "verification proofs");
    result.proofs = {};
    for (const key of Object.keys(proofs)) {
      const kind = proofKeys.find(item => item === key);
      if (!kind) throw new EffectRuntimeRequestError("unsupported verification proof");
      const {criterion_id, ...version} = requireJsonObject(proofs[key], "verification proof");
      if (typeof criterion_id !== "string" || !/^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$/.test(criterion_id)) {
        throw new EffectRuntimeRequestError("verification proof requires its owning acceptance criterion");
      }
      result.proofs[kind] = {...evidence(version, allowedRefs), criterion_id};
    }
  }
  if (Object.hasOwn(row, "costs")) {
    const costs = requireJsonObject(row.costs, "verification costs");
    result.costs = {};
    for (const key of Object.keys(costs)) {
      const kind = costKeys.find(item => item === key);
      if (!kind) throw new EffectRuntimeRequestError("unsupported verification cost");
      const cost = requireJsonObject(costs[key], "verification cost");
      if (Object.keys(cost).length !== 2 || typeof cost.value !== "number" || !Number.isFinite(cost.value) || cost.value < 0
          || (kind !== "verifier_seconds" && !Number.isSafeInteger(cost.value))
          || typeof cost.evidence_ref !== "string" || !allowedRefs.has(cost.evidence_ref)) {
        throw new EffectRuntimeRequestError("verification cost requires a nonnegative measurement and a captured reference");
      }
      result.costs[kind] = {value: cost.value, evidence_ref: cost.evidence_ref};
    }
  }
  return result;
}

/** Independent checks are attached by exact receipt ref and digest. A host name
 * or the mere presence of a producer claim cannot upgrade the verified tier. */
export function verifiedHostDepth(evidence: CandidateVerificationEvidence | undefined, checked: readonly VerificationProof[]) {
  const proofs = evidence?.proofs;
  if (!evidence?.host || !proofs) return null;
  const has = (proof?: VerificationProof) =>
    !!proof && checked.some(ref => ref.ref === proof.ref && ref.sha256 === proof.sha256 && ref.criterion_id === proof.criterion_id);
  if (!has(proofs.external_verifier)) return null;
  if (!has(proofs.warm_session_cache)) return 0;
  return has(proofs.white_box_hooks) && has(proofs.receipt_compaction) ? 2 : 1;
}
