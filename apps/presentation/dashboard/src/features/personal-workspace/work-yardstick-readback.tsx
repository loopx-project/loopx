import type {WorkYardstickCapture, WorkYardstickProfile} from "../../../../../../loopx/control_plane/work_items/yardstick_decision.js";
import {ContextHelp} from "./context-help";

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

/** Both current findings and append-only history read the same recorded facts. */
export function ExecutionObservationReadback({observation, zh}: {
  observation: import("../../../../../../loopx/control_plane/runtime_decode.js").JsonObject; zh: boolean;
}) {
  const measured = (value: unknown) => value === null ? (zh ? "未测量" : "Not measured") : String(value);
  return <details className="explore-reader-details">
    <summary><span>{zh ? "实际执行与资源观测" : "Execution and resource observations"}</span> <ContextHelp label={zh ? "执行观测说明" : "Execution observation guide"}><p>{zh ? "来源记录的执行观测；不等于方法有效或当前候选已通过验收。" : "Source-recorded execution observations; not proof of method benefit or current-candidate acceptance."}</p></ContextHelp></summary>
    <dl className="research-finding-sections">
      <dt>{zh ? "计划方法 → 实际方法" : "Planned → executed method"}</dt><dd>{String(observation.planned_method_id)} → {String(observation.executed_method_id)}</dd>
      {observation.change_reason ? <><dt>{zh ? "改向原因" : "Reason for change"}</dt><dd>{String(observation.change_reason)}</dd></> : null}
      <dt>{zh ? "运行秒数 / 上限秒数" : "Elapsed / limit seconds"}</dt><dd>{measured(observation.elapsed_seconds)} / {measured(observation.limit_seconds)}</dd>
      <dt>{zh ? "搜索次数" : "Search iterations"}</dt><dd>{measured(observation.search_iterations)}</dd>
      <dt>{zh ? "结束原因" : "Termination reason"}</dt><dd>{measured(observation.stop_reason)}</dd>
      <dt>{zh ? "输入版本" : "Input revision"}</dt><dd><code>{String(observation.input_revision)}</code></dd>
      <dt>{zh ? "执行候选 SHA-256" : "Executed candidate SHA-256"}</dt><dd><code>{String(observation.candidate_sha256)}</code></dd>
      {observation.result_ref ? <><dt>{zh ? "该候选的结果引用" : "Result for this candidate"}</dt><dd><code>{String(observation.result_ref)}</code></dd></> : null}
    </dl>
  </details>;
}
