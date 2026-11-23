import type {ExploreMeasurement, ExploreMeasurementFeedback} from "../../../../../../loopx/control_plane/capabilities/explore_measurements.js";
import {Fragment} from "react";
import {ContextHelp} from "./context-help";
import "./explore-measurements.css";

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
  const rows = measurements.map(measurement => ({measurement, comparison: feedback.find(item => item.metric === measurement.metric && item.unit === measurement.unit
    && item.higher_is_better === measurement.higher_is_better && item.comparison_scope === measurement.comparison_scope
    && item.input_revision === measurement.input_revision && item.evaluation_ref === measurement.evaluation_ref)}));
  // Share identical display provenance only; comparability remains owned by
  // Explore's feedback and is never inferred from this grouping.
  const sources = new Map<string, typeof rows>();
  for (const row of rows) {
    const m = row.measurement;
    const key = JSON.stringify([m.comparison_scope, m.input_revision, m.evaluation_ref, m.candidate_ref, m.evidence_ref]);
    const group = sources.get(key);
    if (group) group.push(row); else sources.set(key, [row]);
  }
  return <section className="explore-measurements" aria-label={label}>
    <div className="explore-measurement-heading"><h4>{label}</h4><ContextHelp label={zh ? "指标说明" : "About these measurements"}>
      <p>{zh ? "↑ 越高越好，↓ 越低越好。未测量与零值分别保留。" : "↑ Higher is better; ↓ lower is better. Unmeasured values remain distinct from zero."}</p>
      {feedback.length ? <p>{zh ? "历史最佳和变化仅比较同一范围、输入、评测及单位的来源记录；历史最佳不代表当前保留版本或验收通过。" : "Best and change compare source records with the same scope, input, evaluation and unit. Historical best does not establish the retained version or acceptance."}</p> : null}
    </ContextHelp></div>
    <dl className="explore-measurement-values">{rows.map(({measurement, comparison}, index) => {
      const delta = comparison?.improvement;
      return <div className="explore-measurement-row" key={index}>
        <dt>{measurement.metric} <span className="explore-measurement-direction" role="img" aria-label={measurement.higher_is_better ? (zh ? "越高越好" : "Higher is better") : (zh ? "越低越好" : "Lower is better")}>{measurement.higher_is_better ? "↑" : "↓"}</span></dt>
        <dd className="explore-measurement-reading"><strong>{number(measurement.value)}</strong><span>{measurement.unit}</span></dd>
        {comparison ? <dd className="explore-measurement-comparison"><span>{zh ? "历史最佳" : "Historical best"} <b>{number(comparison.best?.value ?? null)}</b></span>
          {delta !== null && delta !== undefined ? <span>{zh ? "较先前最佳" : "Over prior best"} <b>{delta > 0 ? "+" : ""}{number(delta)}</b></span> : null}</dd> : null}
      </div>;
    })}</dl>
    <details className="explore-measurement-sources"><summary>{zh ? "评测范围与来源" : "Evaluation scope and sources"}</summary>
      {[...sources.entries()].map(([key, group]) => {
        const measurement = group[0]!.measurement;
        return <section key={key}>
        {sources.size > 1 ? <h5>{group.map(({measurement: m}) => `${m.metric} · ${m.unit}`).join(zh ? "、" : ", ")}</h5> : null}<dl>
          <div><dt>{zh ? "比较范围" : "Comparison scope"}</dt><dd><code>{measurement.comparison_scope}</code></dd></div>
          <div><dt>{zh ? "输入" : "Input"}</dt><dd><code>{measurement.input_revision}</code></dd></div>
          <div><dt>{zh ? "评测" : "Evaluation"}</dt><dd><code>{measurement.evaluation_ref}</code></dd></div>
          <div><dt>{zh ? "候选" : "Candidate"}</dt><dd><code>{measurement.candidate_ref}</code></dd></div>
          <div><dt>{zh ? "结果引用" : "Result reference"}</dt><dd><code>{measurement.evidence_ref}</code></dd></div>
          {group.map(({measurement: m, comparison}) => comparison?.best ? <div key={JSON.stringify([m.metric, m.unit, m.higher_is_better])}>
            <dt>{zh ? "最佳来源" : "Best source"}{group.length > 1 ? ` · ${m.metric}` : ""}</dt>
            <dd><code>{comparison.best.candidate_ref}</code><code>{comparison.best.evidence_ref}</code></dd>
          </div> : null)}
        </dl>
      </section>;
      })}
    </details>
  </section>;
}

/** The question reader shows only leaders from comparable, loaded measurements. */
export function ExploreCandidateLeaders({feedback, complete, zh}: {
  feedback: ExploreMeasurementFeedback[]; complete: boolean; zh: boolean;
}) {
  if (!feedback.length) return null;
  const label = complete ? (zh ? "各指标的最佳记录" : "Best recorded by metric")
    : (zh ? "已读记录中的最佳表现" : "Best in loaded records");
  const contextFields = ["evaluation_ref", "comparison_scope", "input_revision"] as const;
  type ContextField = typeof contextFields[number];
  const contextsByMetric = new Map<string, Record<ContextField, Set<string>>>();
  for (const item of feedback) {
    let contexts = contextsByMetric.get(item.metric);
    if (!contexts) {
      contexts = {evaluation_ref: new Set(), comparison_scope: new Set(), input_revision: new Set()};
      contextsByMetric.set(item.metric, contexts);
    }
    for (const field of contextFields) contexts[field].add(item[field]);
  }
  const row = (item: ExploreMeasurementFeedback) => {
    const contexts = contextsByMetric.get(item.metric)!;
    const qualifier = contextFields.filter(field => contexts[field].size > 1).map(field => item[field]).join(" · ");
    const leaders = item.candidate_leaders?.leaders ?? [];
    const count = item.candidate_leaders?.measured_candidates ?? 0;
    const lead = leaders[0];
    const key = JSON.stringify([item.metric, item.unit, item.higher_is_better, item.comparison_scope, item.input_revision, item.evaluation_ref]);
    return <details className="research-candidate-row" key={key}>
      <summary>
        <span>{item.metric} {item.higher_is_better ? "↑" : "↓"}{qualifier && ` · ${qualifier}`}</span>
        <strong>{!lead ? (zh ? "未测量" : "Not measured") : leaders.length > 1
          ? (zh ? `${leaders.length} 个候选并列` : `${leaders.length} candidates tied`) : lead.candidate_ref}</strong>
        {lead ? <span>{formatExploreMeasurementValue(lead.value, zh)} {item.unit} · {zh ? `${count} 个实测候选` : `${count} measured candidate${count === 1 ? "" : "s"}`}</span> : null}
      </summary>
      <dl className="research-finding-sections">
        <dt>{zh ? "比较范围" : "Comparison scope"}</dt><dd>{item.comparison_scope}</dd>
        <dt>{zh ? "输入版本" : "Input revision"}</dt><dd>{item.input_revision}</dd>
        <dt>{zh ? "评测" : "Evaluation"}</dt><dd>{item.evaluation_ref}</dd>
        {leaders.map(point => <Fragment key={point.candidate_ref}><dt>{point.candidate_ref}</dt><dd><code>{point.evidence_ref}</code></dd></Fragment>)}
      </dl>
    </details>;
  };
  return <section className="research-candidate-leaders" aria-label={label}>
    <h4>{label}</h4>{feedback.slice(0, 3).map(row)}
    {feedback.length > 3 ? <details><summary>{zh ? `其余 ${feedback.length - 3} 项指标` : `Show ${feedback.length - 3} more metrics`}</summary>
      {feedback.slice(3).map(row)}</details> : null}
  </section>;
}
