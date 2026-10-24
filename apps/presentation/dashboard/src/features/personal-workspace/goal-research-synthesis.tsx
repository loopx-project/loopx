import {useId, useState, type ReactNode} from "react";
import {ArrowUpRight} from "lucide-react";
import type {AssessmentCitation, TrajectoryResearchSynthesis} from "../../../../../../loopx/control_plane/capabilities/trajectory_assessment_types.js";
import "./goal-research-synthesis.css";

type Props = {synthesis: TrajectoryResearchSynthesis; zh: boolean; detail?: boolean;
  onExpand?: (origin: HTMLButtonElement) => void;
  onReviewEpisodes?: (ids: string[], origin: HTMLButtonElement) => void;
  onCitation: (citation: AssessmentCitation, origin?: HTMLButtonElement) => void};

/** One saved result, progressively disclosed. Never a new inference or executor. */
export function ResearchSynthesis({synthesis: s, zh, detail = false, onExpand, onReviewEpisodes, onCitation}: Props) {
  const id = useId();
  const [experiments, setExperiments] = useState(false);
  const citations = (rows: AssessmentCitation[]) => <div className="research-synthesis-citations">{rows.map((c, i) =>
    <button type="button" key={`${c.source_id}:${c.first_line}:${c.last_line}`} title={`${c.source_id} · ${c.first_line}–${c.last_line}`}
      aria-label={`${zh ? "证据" : "Evidence"} ${i + 1}: ${c.source_id} · ${c.first_line}–${c.last_line}`}
      onClick={event => onCitation(c, event.currentTarget)}>[{i + 1}]<ArrowUpRight size={12} aria-hidden/></button>)}</div>;
  const alternatives = (items: string[]) => items.length ? <><h6>{zh ? "竞争解释" : "Competing explanations"}</h6><ul>{items.map((text, n) => <li key={n}>{text}</li>)}</ul></> : null;
  const episodeLink = (ids: string[]) => onReviewEpisodes && ids.length ? <button type="button" className="research-review-episodes"
    onClick={event => onReviewEpisodes(ids, event.currentTarget)}>{zh ? "复核相关片段" : "Review related episodes"} · {ids.length}<ArrowUpRight size={14} aria-hidden/></button> : null;
  const controls = (experiment: TrajectoryResearchSynthesis["next_experiments"][number]) => <dl>
    <dt>{zh ? "最小干预" : "Smallest intervention"}</dt><dd>{experiment.intervention}</dd>
    <dt>{zh ? "固定条件" : "Controls"}</dt><dd>{experiment.controls}</dd>
  </dl>;
  const predictions = (experiment: TrajectoryResearchSynthesis["next_experiments"][number]) => <dl>
    <dt>{zh ? "预期区分" : "Distinguishing prediction"}</dt><dd>{experiment.prediction}</dd>
    <dt>{zh ? "推翻解释的结果" : "Disconfirming result"}</dt><dd>{experiment.disconfirming_result}</dd>
  </dl>;
  let body: ReactNode;
  if (!detail) {
    body = <>
      <ol className="research-report-findings">{s.findings.map((finding, i) => <li key={i}><details className="research-synthesis-finding">
        <summary>{finding.title}</summary><p>{finding.observation}</p><p>{finding.interpretation}</p>
        {alternatives(finding.alternative_explanations)}{citations(finding.citations)}
      </details></li>)}</ol>
      {s.next_experiments[0] ? <p className="research-report-next"><strong>{zh ? "优先研究" : "Investigate first"}</strong> · {s.next_experiments[0].question}</p> : null}
    </>;
  } else {
    body = <>
      <div className="research-report-tabs" role="tablist" aria-label={zh ? "汇报阅读" : "Report reading"}>
        {[false, true].map(next => <button type="button" role="tab" key={String(next)} id={`${id}-${next}-tab`}
          aria-selected={experiments === next} aria-controls={`${id}-body`} tabIndex={experiments === next ? 0 : -1}
          onClick={() => setExperiments(next)} onKeyDown={event => {
            if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
            event.preventDefault(); const target = event.key === "Home" ? false : event.key === "End" ? true : !experiments;
            setExperiments(target); document.getElementById(`${id}-${target}-tab`)?.focus();
          }}>{next ? (zh ? "下一步实验" : "Next experiments") : (zh ? "结论与解释" : "Findings and explanations")} <span>{next ? s.next_experiments.length : s.findings.length}</span></button>)}
      </div>
      <div id={`${id}-body`} role="tabpanel" aria-labelledby={`${id}-${experiments}-tab`}>
        {experiments ? <div className="research-report-experiments">
          {!s.next_experiments.length ? <p>{zh ? "现有证据不足以提出有区分力的下一步实验。" : "The evidence does not justify a discriminating next experiment."}</p> : null}
          {s.next_experiments.map((experiment, i) => <article key={i} className="research-report-experiment">
            <header><h5><span>{i + 1}.</span> {experiment.question}</h5><p>{experiment.why_priority}</p></header>
            <div className="research-report-comparison">{controls(experiment)}{predictions(experiment)}</div>
            {citations(experiment.citations)}
            {episodeLink(experiment.episode_ids)}
          </article>)}
        </div> : <div className="research-report-matrix">
          {!!s.findings.length && <div className="research-report-columns" aria-hidden><span>{zh ? "观察与依据" : "Observation and evidence"}</span><span>{zh ? "解释与竞争解释" : "Interpretation and alternatives"}</span></div>}
          {!s.findings.length ? <p>{zh ? "现有证据不足以形成进一步结论。" : "The evidence does not justify further findings."}</p> : null}
          {s.findings.map((finding, i) => <article key={i} className="research-report-finding">
            <h5><span>{i + 1}.</span> {finding.title}</h5>
            <div className="research-report-comparison"><div><p>{finding.observation}</p>{citations(finding.citations)}</div>
              <div><p>{finding.interpretation}</p>{alternatives(finding.alternative_explanations)}</div></div>
            {episodeLink(finding.episode_ids)}
          </article>)}
        </div>}
      </div>
    </>;
  }
  return <section className={`research-synthesis${detail ? " research-synthesis-detail" : ""}`} aria-label={zh ? "研究汇报" : "Research report"}>
    <header>{!detail ? <h3>{zh ? "研究汇报" : "Research report"}</h3> : null}<span>{zh ? "Agent 分析 · 所选片段" : "Agent analysis · selected episodes"}</span>
      {!detail && onExpand ? <button type="button" className="research-report-expand" onClick={event => onExpand(event.currentTarget)}>{zh ? "深入解读" : "Read deeper"}<ArrowUpRight size={14} aria-hidden/></button> : null}
    </header>
    <p className="research-synthesis-summary">{s.summary.text}</p>
    <details className="research-report-summary-sources"><summary>{zh ? "概览来源" : "Summary sources"} · {s.summary.citations.length}</summary>{citations(s.summary.citations)}</details>
    {body}
  </section>;
}
