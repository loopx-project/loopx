/** A reader for existing Explore evidence. A matched pair reports observed
 * ordering; the independent Goal receipt remains the acceptance source. */
import {workYardstickObservedPair} from "../work_items/yardstick_decision.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import type {ExploreHistoryEvent} from "./explore_history.ts";

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
  const measurements = finding.yardstick_measurements ?? [];
  const exactBaseline = candidate && evidence?.baseline && evidence.baseline.sha256 !== candidate ? evidence.baseline : null;
  const authoritative = candidate && exactBaseline && decision
    ? workYardstickObservedPair(measurements, decision.authoritative, exactBaseline.sha256, candidate) : null;
  const proxy = candidate && exactBaseline && decision?.proxy
    ? workYardstickObservedPair(measurements, decision.proxy, exactBaseline.sha256, candidate) : null;
  const fidelity = authoritative && proxy && decision?.authoritative.input_revision === decision?.proxy?.input_revision
    ? Math.sign(authoritative.delta) === Math.sign(proxy.delta) ? "same_order" : "different_order"
    : "unknown";
  return {decision, evidence, candidate, alternatives, authoritative, proxy, fidelity};
}
