import type {ScopedYardstickFidelity, YardstickFidelityStatus} from "../../../../../../loopx/control_plane/capabilities/yardstick_fidelity.js";
import type {WorkYardstickCapture, WorkYardstickProfile} from "../../../../../../loopx/control_plane/work_items/yardstick_decision.js";

function fidelityStatusLabel(status: YardstickFidelityStatus, zh: boolean): string {
  const labels: Record<YardstickFidelityStatus, string> = zh ? {
    direct_only: "直接评估",
    insufficient_paired_evidence: "配对样本不足",
    noisy_indistinguishable: "噪声无法区分",
    aligned_in_tested_scope: "当前范围一致",
    frontier_bias_cliff: "前沿偏置断崖",
    order_reversed: "代理顺序反转",
    mixed_or_flat: "局部平坦或混杂",
  } : {
    direct_only: "Direct evaluation",
    insufficient_paired_evidence: "Insufficient paired points",
    noisy_indistinguishable: "Within noise floor",
    aligned_in_tested_scope: "Aligned in scope",
    frontier_bias_cliff: "Frontier bias cliff",
    order_reversed: "Order reversed",
    mixed_or_flat: "Mixed or flat",
  };
  return labels[status] ?? status;
}

/** A source record, never an acceptance or permission indicator. */
export function WorkYardstickReadback({
  capture,
  fidelity,
  zh,
}: {
  capture: WorkYardstickCapture;
  fidelity?: ScopedYardstickFidelity | null;
  zh: boolean;
}) {
  const {decision, binding} = capture;
  const profile = (value: WorkYardstickProfile) => <>{value.metric} ({value.unit}) {value.higher_is_better ? "↑" : "↓"}
    <br/><code>{value.comparison_scope} · {value.input_revision} · {value.evaluation_ref}</code></>;
  const showFidelity = Boolean(
    fidelity && fidelity.proxy !== null && (
      fidelity.counts.paired_candidates >= 2 ||
      fidelity.audit_candidates.unconfirmed_proxy_winners.length > 0
    ),
  );
  const summaryBadge = showFidelity && fidelity
    ? ` · ${fidelityStatusLabel(fidelity.fidelity_status, zh)} (${
        zh ? `${fidelity.counts.paired_candidates} 组配对` : `${fidelity.counts.paired_candidates} paired`
      })`
    : "";
  const fmtSigned = (value: number | null | undefined) =>
    typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : "—";

  return <details className="explore-reader-details work-yardstick-readback">
    <summary>{(zh ? "目标与衡量标准" : "Target and yardstick") + summaryBadge}</summary>
    <dl className="research-finding-sections">
      {showFidelity && fidelity ? <>
        <dt>{zh ? "代理校准" : "Proxy calibration"}</dt>
        <dd>
          <strong>{fidelityStatusLabel(fidelity.fidelity_status, zh)}</strong>
          {" · "}
          {zh
            ? `${fidelity.counts.paired_candidates} 组配对`
            : `${fidelity.counts.paired_candidates} paired`}
          {fidelity.rank_concordance ? <>
            {" · "}τ<sub>b</sub> {fmtSigned(fidelity.rank_concordance.kendall_tau_b)}
          </> : null}
          {fidelity.frontier_neighborhood && fidelity.calibration_tier === "multi_point" ? <>
            {" · "}{zh ? "前沿" : "frontier"} τ<sub>b</sub> {fmtSigned(fidelity.frontier_neighborhood.frontier_kendall_tau_b)}
          </> : null}
          {fidelity.audit_candidates.unconfirmed_proxy_winners.length ? <>
            <br/>
            <span>{zh ? "待核实代理领先候选：" : "Unconfirmed proxy winners: "}</span>
            {fidelity.audit_candidates.unconfirmed_proxy_winners.map((item, idx) => (
              <span key={item.candidate_ref}>{idx > 0 ? ", " : ""}<code>{item.candidate_ref}</code></span>
            ))}
          </> : null}
        </dd>
      </> : null}
      <dt>{zh ? "目标" : "Target"}</dt><dd>{decision.target.summary}</dd>
      <dt>{zh ? "适用范围" : "Scope"}</dt><dd>{decision.target.scope}</dd>
      <dt>{zh ? "成果指标" : "Outcome metric"}</dt><dd>{profile(decision.authoritative)}</dd>
      <dt>{zh ? "代理指标" : "Proxy metric"}</dt><dd>{decision.proxy ? profile(decision.proxy) : zh ? "直接评估" : "Direct evaluation"}</dd>
      {decision.counterevidence_refs.length ? <><dt>{zh ? "代理反证" : "Proxy counterevidence"}</dt><dd>{decision.counterevidence_refs.map(ref => <code key={ref}>{ref}</code>)}</dd></> : null}
      <dt>{zh ? "下一步" : "Next action"}</dt><dd>{decision.next_action.reason}<br/><code>{decision.next_action.method_id}</code></dd>
      <dt>{zh ? "成本边界" : "Cost bound"}</dt><dd>{decision.next_action.cost_bound}</dd>
      <dt>{zh ? "验收来源" : "Acceptance source"}</dt><dd><code>{decision.target.acceptance_ref}</code></dd>
      <dt>{zh ? "任务" : "Task"}</dt><dd><code>{binding.todo_id}</code></dd>
      {decision.prior_finding_id ? <><dt>{zh ? "上次判断" : "Previous decision"}</dt><dd><code>{decision.prior_finding_id}</code></dd></> : null}
    </dl>
    <p>{zh ? "这是记录者的研究判断；代理有效性与成果验收仍需独立证据。" : "This records the author's research decision; proxy fidelity and outcome acceptance still require independent evidence."}</p>
  </details>;
}
