import type {ExploreMeasurement, ExploreMeasurementFeedback} from "../../../../../../loopx/control_plane/capabilities/explore_measurements.js";

export const formatExploreMeasurementValue = (value: number | null, zh: boolean) => value === null
  ? (zh ? "未测量" : "Not measured")
  : new Intl.NumberFormat(zh ? "zh-CN" : "en", {maximumSignificantDigits: 12}).format(value);

/** Values and comparisons come from Explore's shared owner, never a UI scorer. */
export function ExploreMeasurements({measurements, feedback = [], zh, title}: {
  measurements: ExploreMeasurement[]; feedback?: ExploreMeasurementFeedback[]; zh: boolean; title?: string;
}) {
  if (!measurements.length) return null;
  const number = (value: number | null) => formatExploreMeasurementValue(value, zh);
  const label = title ?? (zh ? "量化结果" : "Measured results");
  return <section className="explore-measurements" aria-label={label}>
    <h4>{label}</h4>
    {measurements.map((measurement, index) => {
      const comparison = feedback.find(item => item.metric === measurement.metric && item.unit === measurement.unit
        && item.higher_is_better === measurement.higher_is_better && item.comparison_scope === measurement.comparison_scope
        && item.input_revision === measurement.input_revision && item.evaluation_ref === measurement.evaluation_ref);
      const delta = comparison?.improvement;
      return <div key={index}>
        <p><strong>{measurement.metric} · {number(measurement.value)} {measurement.unit}</strong>
          <small> · {measurement.higher_is_better ? (zh ? "越高越好" : "Higher is better") : (zh ? "越低越好" : "Lower is better")}</small></p>
        {comparison ? <p>{zh ? "同口径 best" : "Best in this scope"}: {number(comparison.best?.value ?? null)}
          {delta !== null && delta !== undefined ? <> · {zh ? "相对先前 best 的改善" : "Improvement over prior best"}: {delta > 0 ? "+" : ""}{number(delta)}</> : null}</p> : null}
        <details><summary>{zh ? "评测范围与来源" : "Evaluation scope and sources"}</summary>
          <p>{zh ? "比较范围" : "Comparison scope"}: {measurement.comparison_scope}<br/>
            {zh ? "输入" : "Input"}: {measurement.input_revision}<br/>
            {zh ? "评测" : "Evaluation"}: {measurement.evaluation_ref}<br/>
            {zh ? "候选" : "Candidate"}: {measurement.candidate_ref}<br/>
            {zh ? "结果引用" : "Result reference"}: {measurement.evidence_ref}</p>
          {comparison?.best ? <p>{zh ? "best 候选 / 来源" : "Best candidate / source"}: {comparison.best.candidate_ref} · {comparison.best.evidence_ref}</p> : null}
          <p>{zh ? "同一输入、评测和预算条件下的来源记录；best 不代表当前保留版本或验收通过。" : "Source-recorded values under the same input, evaluation and budget conditions; best does not establish the retained version or acceptance."}</p>
        </details>
      </div>;
    })}
  </section>;
}
