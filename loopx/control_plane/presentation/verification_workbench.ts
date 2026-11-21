/** A reader for existing Explore evidence. A matched pair reports observed
 * ordering; the independent Goal receipt remains the acceptance source. */
import type {ExploreMeasurement} from "../capabilities/explore_measurements.ts";
import type {VerificationEvidenceRef} from "../capabilities/explore_verification_evidence.ts";
import type {WorkYardstickProfile} from "../work_items/yardstick_decision.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import type {ExploreHistoryEvent} from "./explore_history.ts";

function observedPair(
  measurements: readonly ExploreMeasurement[],
  profile: WorkYardstickProfile,
  baseline: VerificationEvidenceRef,
  candidate: string,
) {
  const matching = measurements.filter(row => row.metric === profile.metric && row.unit === profile.unit
    && row.higher_is_better === profile.higher_is_better && row.comparison_scope === profile.comparison_scope
    && row.input_revision === profile.input_revision && row.evaluation_ref === profile.evaluation_ref);
  const point = (sha: string) => {
    const items = matching.filter(row => row.candidate_ref === `sha256:${sha}` && row.value !== null);
    return items.length === 1 ? items[0] : undefined;
  };
  const before = point(baseline.sha256), after = point(candidate);
  if (!before || !after || before.value === null || after.value === null) return null;
  const delta = (after.value - before.value) * (profile.higher_is_better ? 1 : -1);
  return Number.isFinite(delta) ? {before, after, delta} : null;
}

export function projectVerificationWorkbench(
  finding: ExploreHistoryEvent | undefined,
  findings: readonly ExploreHistoryEvent[],
) {
  if (!finding || finding.event_kind !== "finding") return null;
  const decision = finding.work_yardstick?.decision;
  const execution = finding.execution_observation;
  const evidence = execution?.verification_evidence;
  const candidate = typeof execution?.candidate_sha256 === "string" && BARE_SHA256_PATTERN.test(execution.candidate_sha256)
    ? execution.candidate_sha256 : null;
  const alternatives = findings.filter(row => row.goal_id === finding.goal_id && row.node_id === finding.node_id
    && row.event_kind === "finding" && row.event_id !== finding.event_id && row.status === "refuted"
    && Boolean(finding.node_id));
  const cited = new Set([...finding.evidence_refs, ...(execution?.evidence_refs as string[] | undefined ?? [])]);
  // Candidate identity and explicit citations allow use across findings on
  // this question. Chronology and a shared host provide no such binding.
  const measurements = findings.flatMap(row => row.goal_id === finding.goal_id && row.node_id === finding.node_id
    && row.event_kind === "finding" && (row.event_id === finding.event_id || row.status === "confirmed")
    ? row.measurements?.filter(item => cited.has(item.evidence_ref)) ?? [] : []);
  const exactBaseline = candidate && evidence?.baseline && evidence.baseline.sha256 !== candidate ? evidence.baseline : null;
  const authoritative = candidate && exactBaseline && decision
    ? observedPair(measurements, decision.authoritative, exactBaseline, candidate) : null;
  const proxy = candidate && exactBaseline && decision?.proxy
    ? observedPair(measurements, decision.proxy, exactBaseline, candidate) : null;
  const fidelity = authoritative && proxy && decision?.authoritative.input_revision === decision?.proxy?.input_revision
    ? Math.sign(authoritative.delta) === Math.sign(proxy.delta) ? "same_order" : "different_order"
    : "unknown";
  return {decision, evidence, candidate, alternatives, authoritative, proxy, fidelity};
}
