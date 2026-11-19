import type {WorkYardstickCapture, WorkYardstickProfile} from "../../../../../../loopx/control_plane/work_items/yardstick_decision.js";

/** A source record, never an acceptance or permission indicator. */
export function WorkYardstickReadback({capture, zh}: {capture: WorkYardstickCapture; zh: boolean}) {
  const {decision, binding} = capture;
  const profile = (value: WorkYardstickProfile) => <>{value.metric} ({value.unit}) {value.higher_is_better ? "↑" : "↓"}
    <br/><code>{value.comparison_scope} · {value.input_revision} · {value.evaluation_ref}</code></>;
  return <details className="explore-reader-details work-yardstick-readback">
    <summary>{zh ? "目标与衡量标准" : "Target and yardstick"}</summary>
    <dl className="research-finding-sections">
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
