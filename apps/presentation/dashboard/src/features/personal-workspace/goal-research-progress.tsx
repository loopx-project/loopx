import {ArrowUpRight, ChevronDown, CircleHelp, FileText, TriangleAlert, X} from "lucide-react";
import {useId, useRef, useState} from "react";
import {TRAJECTORY_METRICS} from "../../../../../../loopx/control_plane/capabilities/trajectory_assessment_types.js";
import type {AssessmentCitation, AssessmentVerdict, TrajectoryAssessment, TrajectoryEpisode, TrajectoryInput, TrajectoryMetric, TrajectorySolverUsage} from "../../../../../../loopx/control_plane/capabilities/trajectory_assessment_types.js";
import type {TrajectoryNativeOutcomes} from "../../../../../../loopx/control_plane/capabilities/trajectory_native_outcomes.js";
import {ContextHelp} from "./context-help";
import {ResearchSynthesis} from "./goal-research-synthesis";
import "./goal-research-progress.css";

// Read models from benchmark-toolkit. This view formats existing counts and
// assessments; it never scores, reclassifies an episode or combines runs.
export type ResearchProgressPack = {pack_id: string; title: string; scope: TrajectoryInput["scope"]; selection: TrajectoryInput["selection"];
  episode_count: number; episodes?: TrajectoryEpisode[]; result?: TrajectoryAssessment | null; applicability?: "current" | "historical";
  job?: {status: string; attempt?: number; analysis_wall_seconds?: number | null;
    usage?: {input_tokens?: number; output_tokens?: number} | null; config?: {model: string; effort: string; service_tier: string}} | null;
  solver_usage?: {artifact: string; artifact_sha256: string; projection: TrajectorySolverUsage; applicability: "current" | "historical"; reason_code: string | null};
  native_outcomes?: {artifact: string; artifact_sha256: string; projection: TrajectoryNativeOutcomes; applicability: "current" | "historical"; reason_code: string | null}};
export const metricCopy: Record<TrajectoryMetric, [string, string, string, string]> = {
  goal_alignment: ["目标关联", "Goal alignment", "干预是否关联原目标的真实瓶颈", "Does the intervention address a bottleneck of the original objective?"],
  benefit_test: ["收益检验", "Benefit test", "是否真正检验原目标收益，而非只完成前置诊断", "Was original-objective utility tested, beyond diagnostic prerequisites?"],
  discrimination: ["判别实验", "Discrimination", "实验是否区分了竞争解释", "Did the experiment distinguish competing explanations?"],
  information_gain: ["新增信息", "New information", "是否解决了与决策有关的新不确定性", "Did evidence resolve a new decision-relevant uncertainty?"],
  evidence_adoption: ["证据采用", "Evidence adoption", "后续实际行动是否采用了适用证据", "Did subsequent actions demonstrably use applicable evidence?"],
  redundancy: ["无益重复", "Avoidable repetition", "是否重复已有结论，且无新信息或合理复验目的", "Was settled work repeated without new information or justified replication?"],
};
export function verdictLabel(metric: TrajectoryMetric, verdict: AssessmentVerdict, zh: boolean) {
  if (verdict === "unknown") return zh ? "证据不足" : "Unknown";
  if (metric === "redundancy") return verdict === "yes" ? (zh ? "发现无益重复" : "Repetition found") : (zh ? "未见无益重复" : "No repetition found");
  if (metric === "benefit_test") return verdict === "yes" ? (zh ? "已做收益检验" : "Utility tested") : (zh ? "尚未检验收益" : "Utility not tested");
  return verdict === "yes" ? (zh ? "有依据" : "Supported") : (zh ? "未建立" : "Not established");
}

function LoopXInputShare({usage, zh}: {usage: TrajectorySolverUsage | undefined; zh: boolean}) {
  const attribution = usage?.loopx_input;
  const percent = (n: number) => new Intl.NumberFormat(zh ? "zh-CN" : "en", {style: "percent", maximumFractionDigits: 1}).format(n);
  const measured = attribution && attribution.status !== "unavailable" ? attribution : null;
  const estimated = attribution?.method === "tiktoken_o200k_base_input_estimate_v1";
  const helpLabel = !attribution ? (zh ? "LoopX 输入占比 · 未采集" : "LoopX input share · not captured")
    : attribution.status === "unavailable" ? (zh ? "LoopX 输入占比 · 不可用" : "LoopX input share · unavailable")
    : attribution.status === "partial" ? (zh ? "LoopX 输入占比 · 部分归因" : "LoopX input share · partial attribution")
    : attribution.total_input_tokens === 0 ? (zh ? "LoopX 输入占比 · 不适用" : "LoopX input share · undefined")
    : (zh ? "结构输入占比口径与覆盖" : "Input share basis and coverage");
  return <section className="research-request-coverage" aria-label={zh ? "LoopX 结构开销" : "LoopX input overhead"}>
    <div className="research-usage-heading"><h4>{estimated ? (zh ? "LoopX Turn packet · 估计" : "LoopX Turn packet · estimate") : (zh ? "LoopX 输入占比" : "LoopX input share")}</h4><ContextHelp icon={!attribution || attribution.status !== "complete" ? CircleHelp : undefined} label={helpLabel}>
      <p>{zh ? "同一捕获窗口内，LoopX packet 进入各次模型请求的 token 总量 ÷ 全部请求输入 token 总量，包含缓存输入。重复消费按各次请求计算，同一响应的重复回执只计一次。" : "LoopX packet tokens processed across requests divided by all request input tokens in the same capture, including cached input. Reprocessed context counts in each request; duplicate receipts for one response count once."}</p>
      <p>{estimated ? (zh ? "分子仅计宿主明确记录的 Turn packet 文本，使用 o200k_base 分词估计；分母是同请求的原生输入计数。尚未核验原生模型分词器，不含其他 LoopX 命令输出、封装或压缩摘要，缓存归属未知；这是已记录部分的估计，不是 provider 实测或净效率损失。" : "Only explicitly recorded Turn packet text is estimated with o200k_base; the denominator is native input for the same requests. The provider model's tokenizer is unverified. Other LoopX tool output, framing and compacted summaries are excluded; cache allocation is unknown. This estimates the recorded component, not provider-measured attribution or net efficiency loss.") : (zh ? "需要宿主提供逐请求 token 归因；包字节、字符数和命令次数不能替代。缺失归因保持未知；上下文压缩也不自动全部归为 LoopX。这个比例衡量结构开销，不能单独证明净效率损失或因果影响。" : "Requires host-supplied per-request token attribution, not packet bytes, characters or command counts. Missing attribution stays unknown; compaction is not automatically all attributed to LoopX. This measures input overhead, not net efficiency loss or causal impact.")}</p>
      {!attribution ? <p>{zh ? "未采集 packet 的 token 归因" : "Packet token attribution was not captured"}</p>
      : attribution.status === "unavailable" ? <p className="research-progress-warning" role="status">{estimated ? (zh ? "输入估计暂不可用，占比未知。" : "Input estimate unavailable; share is unknown.") : (zh ? "归因记录未通过核对，占比未知。" : "Attribution records could not be reconciled; share is unknown.")}</p>
      : attribution.status === "partial" ? <p>{estimated ? (zh ? "部分请求已匹配 · 全窗口占比未知" : "Some requests matched · whole-capture share unknown") : (zh ? "部分归因 · 全窗口占比未知" : "Partial attribution · whole-capture share unknown")}</p>
        : attribution.total_input_tokens === 0 ? <p>{zh ? "捕获输入为零，比例不适用。" : "Captured input is zero; the ratio is undefined."}</p> : null}
    </ContextHelp></div>
    <p className="research-input-share-value"><strong data-loopx-input-share>{measured?.input_fraction == null ? "—" : percent(measured.input_fraction)}</strong>
      {measured ? <span>{measured.attributed_requests} / {measured.total_requests} {estimated ? (zh ? "次请求已匹配" : "requests matched") : (zh ? "次请求已归因" : "requests attributed")}</span> : null}
    </p>
    {attribution ? <details className="research-usage-detail"><summary>{estimated ? (zh ? "估计明细" : "Estimate details") : (zh ? "归因明细" : "Attribution details")}</summary>
      {measured ? <dl className="research-usage-basis">
        <dt>{estimated ? (zh ? "已记录 packet 文本估计" : "Recorded packet text estimate") : (zh ? "已归因 LoopX 输入" : "Attributed LoopX input")}</dt><dd>{measured.loopx_input_tokens.toLocaleString()} tokens</dd>
        <dt>{zh ? "全部请求输入" : "All request input"}</dt><dd>{measured.total_input_tokens.toLocaleString()} tokens</dd>
        <dt>{zh ? "已覆盖请求输入" : "Input in attributed requests"}</dt><dd>{measured.attributed_input_tokens.toLocaleString()} tokens</dd>
        <dt>{zh ? "LoopX 输入中的缓存" : "Cached subset of LoopX input"}</dt><dd>{measured.loopx_cached_input_tokens?.toLocaleString() ?? "—"} tokens</dd>
        <dt>{zh ? "占未缓存输入" : "Share of uncached input"}</dt><dd>{measured.uncached_input_fraction == null ? "—" : percent(measured.uncached_input_fraction)}</dd>
        {measured.status === "partial" && measured.input_fraction_bounds ? <><dt>{zh ? "缺失归因的可能范围" : "Possible range with missing attribution"}</dt><dd>{measured.input_fraction_bounds.map(percent).join(" – ")}<p>{zh ? "假定未归因请求全非 LoopX / 全为 LoopX；不是置信区间。" : "Unattributed requests treated as none / all LoopX; not a confidence interval."}</p></dd></> : null}
      </dl> : <p>{attribution.status === "unavailable" ? attribution.reason : null}</p>}
      {measured?.estimate_basis ? <p>{measured.estimate_basis.tokenizer} {measured.estimate_basis.version} · {measured.estimate_basis.encoding}<br/>{measured.estimate_basis.models.join(", ")}</p> : null}
      <p><code>{attribution.method}</code></p>
    </details> : null}
  </section>;
}

// The assessment owner supplies localized questions. Both readers use that
// copy without inventing short titles or translating the original evidence.
export const episodeQuestion = (result: TrajectoryAssessment, id: string, episode?: TrajectoryEpisode) =>
  result.episode_questions?.find(question => question.episode_id === id)?.question ?? episode?.question ?? id;

// Factor only an exact, single occurrence of the declared episode identity.
// This is lossless presentation, not a short-title generator or prose classifier.
export function episodeQuestionGroups<T extends {episode_id: string}>(result: TrajectoryAssessment, records: T[], episodes: TrajectoryEpisode[] | undefined) {
  const groups: Array<{before: string; after: string; exact: boolean; items: Array<{record: T; question: string}>}> = [];
  for (const record of records) {
    const id = record.episode_id;
    const question = episodeQuestion(result, id, episodes?.find(episode => episode.id === id));
    const index = question.indexOf(id);
    const exact = index >= 0 && question.lastIndexOf(id) === index
      && !/[\w./-]/u.test(question[index - 1] ?? "") && !/[\w./-]/u.test(question[index + id.length] ?? "");
    const before = exact ? question.slice(0, index) : question, after = exact ? question.slice(index + id.length) : "";
    const prior = groups.at(-1);
    if (exact && prior?.exact && prior.before === before && prior.after === after) prior.items.push({record, question});
    else groups.push({before, after, exact, items: [{record, question}]});
  }
  // Factor repeated prose only when it outweighs the identities. This also
  // serves a pair of long questions; preserve source order and every verdict.
  return groups.flatMap(group => {
    const shared = group.before.length + group.after.length;
    const identities = group.items.reduce((sum, item) => sum + item.record.episode_id.length, 0);
    return group.items.length >= 2 && shared * (group.items.length - 1) > identities
      ? [{...group, shared: true}]
      : group.items.map(item => ({before: "", after: "", items: [item], shared: false}));
  });
}

// Keep headline values readable without rounding a nonzero measurement to zero.
// Full native precision remains available in the existing evidence/details.
function formatNativeMeasurement(value: number, zh: boolean, signed = false) {
  const magnitude = Math.abs(value);
  const scientific = magnitude > 0 && (magnitude < 0.0001 || magnitude >= 1_000_000);
  return new Intl.NumberFormat(zh ? "zh-CN" : "en", {
    maximumFractionDigits: 4,
    ...(scientific ? {notation: "scientific", maximumSignificantDigits: 4} : {}),
    signDisplay: signed ? "exceptZero" : "auto",
  }).format(value === 0 ? 0 : value);
}

// Format the native owner's existing projection, without deriving a gain,
// deciding adoption, or borrowing values from another pack.
function RetainedImprovement({native, zh, onCitation}: {native: NonNullable<ResearchProgressPack["native_outcomes"]>; zh: boolean; onCitation: (citation: AssessmentCitation, origin?: HTMLButtonElement) => void}) {
  const outcomes = native.projection, improvement = outcomes.retained_improvement;
  if (!improvement) return null;
  const format = (value: number) => formatNativeMeasurement(value, zh, true);
  const label = zh ? "已采用成果增量" : "Retained improvement";
  return <section className="research-retained-improvement" aria-label={label}>
    <div className="research-progress-title"><h4>{label}</h4><ContextHelp label={zh ? "成果增量说明" : "About retained improvement"}>
      <p>{zh ? "固定基线与实际保留结果的原生指标差值，正数表示改善。越低越好的指标反向计算；回退到基线为零，缺少基线或保留证据为未知。" : "Native change from a fixed baseline to the actually retained result. Positive means improvement; lower-is-better metrics reverse the subtraction. Returning to baseline is zero; missing baseline or retention evidence is unknown."}</p>
      <p>{zh ? "只代表所选比较，不是 LoopX 的独立贡献或原目标已达成；不能跨题、跨单位或重叠窗口相加。展开数值可看原文。" : "This comparison alone does not establish LoopX's causal contribution or objective completion. Do not sum across tasks, units or overlapping windows. Expand the value for original evidence."}</p>
    </ContextHelp></div>
    {native.applicability === "historical" ? <p className="research-progress-warning" role="status">{zh ? "历史捕获 · 当前结果未核验" : "Historical capture · current result unverified"}</p> : null}
    <details><summary aria-label={`${label}: ${improvement.value === null ? (zh ? "未知" : "Unknown") : `${format(improvement.value)} ${outcomes.metric.unit}`}`}>
      <strong data-retained-improvement>{improvement.value === null ? "—" : format(improvement.value)}</strong>
      <span>{improvement.value === null ? (zh ? "未知" : "Unknown") : outcomes.metric.unit} · {outcomes.metric.name}{improvement.baseline ? ` · ${zh ? "基线" : "baseline"} ${formatNativeMeasurement(improvement.baseline.value, zh)}` : ""}</span><ChevronDown size={14} aria-hidden/>
    </summary><dl>
      {([[improvement.baseline, zh ? "比较基线" : "Comparison baseline"], [outcomes.retained, zh ? "实际保留" : "Actually retained"]] as const).map(([point, title]) => <div key={title}><dt>{title}</dt><dd>{point ? <button type="button" onClick={event => onCitation(point.citation, event.currentTarget)}>{String(point.value)} · {point.candidate_ref}<ArrowUpRight size={13} aria-hidden/></button> : (zh ? "未知" : "Unknown")}</dd></div>)}
      <div><dt>{zh ? "保留依据" : "Retention evidence"}</dt><dd>{improvement.retention_citation ? <button type="button" onClick={event => onCitation(improvement.retention_citation!, event.currentTarget)}>{zh ? "查看原文" : "Read original evidence"}<ArrowUpRight size={13} aria-hidden/></button> : (zh ? "未采集" : "Uncaptured")}</dd></div>
      <div><dt>{zh ? "最后候选差值" : "Final candidate change"}</dt><dd>{improvement.final_candidate_delta === null ? (zh ? "未知" : "Unknown") : `${format(improvement.final_candidate_delta)} ${outcomes.metric.unit}`} · {zh ? "不代表已采用收益" : "not realized retention"}</dd></div>
    </dl></details>
  </section>;
}

function NativeResults({native, zh, onCitation}: {native: ResearchProgressPack["native_outcomes"]; zh: boolean; onCitation: (citation: AssessmentCitation, origin?: HTMLButtonElement) => void}) {
  const outcomes = native?.projection;
  if (!native || !outcomes) return <ContextHelp label={zh ? "原生测量未采集" : "Native measurements not captured"} icon={CircleHelp}><p className="research-native-empty">{zh ? "原生测量 · 未采集" : "Native measurements · not captured"}</p></ContextHelp>;
  const historical = native.applicability === "historical";
  const points = [["best_valid", "窗口内最佳有效值", "Best valid in capture"], ["initial", "首次提交", "Initial submission"], ["final_valid", "最后有效提交", "Final valid submission"], ["retained", "原生保留结果", "Native retained result"]] as const;
  const period = new Intl.DateTimeFormat(zh ? "zh-CN" : "en", {timeZone: "UTC", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
    ...(new Date(outcomes.start).getUTCFullYear() !== new Date(outcomes.end).getUTCFullYear() ? {year: "numeric"} : {})});
  const value = ([key, cn, en]: typeof points[number]) => {
    const point = outcomes[key];
    return <div key={key} className={key === "best_valid" ? "research-native-best" : undefined} data-native-point={key}><dt>{zh ? cn : en}</dt><dd>{point ?
      <button type="button" onClick={event => onCitation(point.citation, event.currentTarget)} aria-label={`${zh ? cn : en}: ${String(point.value)} ${outcomes.metric.unit} · ${zh ? "查看原始证据" : "Read original evidence"}`}>
        <strong>{formatNativeMeasurement(point.value, zh)}</strong>{key === "best_valid" ? <span className="research-native-unit">{outcomes.metric.unit}</span> : null}<FileText size={14} aria-hidden/>
      </button> : <span className="research-native-unknown">{zh ? "未知" : "Unknown"}{key === "best_valid" ? <span className="research-native-unit"> · {outcomes.metric.unit}</span> : null}</span>}</dd></div>;
  };
  return <section className="research-native-results" aria-label={zh ? "原生测量" : "Native measurements"}>
    <div className="research-progress-title research-native-heading"><div className="research-progress-title"><h4>{historical ? (zh ? "历史原生测量" : "Historical native measurements") : (zh ? "原生测量" : "Native measurements")}</h4><ContextHelp
      icon={historical ? TriangleAlert : undefined} label={historical ? (zh ? "当前来源未核验" : "Current source unverified") : (zh ? "测量说明" : "About native measurements")}>
      {historical ? <p>{zh ? "来源已变化或不可读；以下仅为历史捕获，当前结果未核验。" : "The source changed or is unavailable. These are historical captures; current results are unverified."}</p> : null}
      <p>{zh ? "最佳只限本次捕获窗口；原生保留与最后提交可能不同。未采集的值保留未知，不记零。" : "Best is limited to this capture; retained and final submissions can differ. Uncaptured values remain unknown, not zero."}</p>
      <p>{zh ? "检验过收益、采用了证据及原目标达成各需各自证据，不能由这些数值推断。" : "Utility testing, evidence adoption and original-objective success each need their own evidence; these values cannot establish them."}</p>
    </ContextHelp></div>
    <p className="research-native-metric">{outcomes.metric.name} · {outcomes.metric.higher_is_better ? (zh ? "越高越好" : "higher is better") : (zh ? "越低越好" : "lower is better")}</p></div>
    <dl className="research-native-reading">{points.map(value)}</dl>
    <div className="research-native-footer"><div className="research-native-scope"><span>{outcomes.valid_count} / {outcomes.sample_count} {zh ? "项有效结果" : "valid results"}</span>
      <span>{period.formatRange(new Date(outcomes.start), new Date(outcomes.end))} UTC</span>
      {!outcomes.complete ? <span>{zh ? "部分窗口 · 起点未知" : "Partial window · initial unknown"}</span> : null}
    </div>
    <details className="research-native-basis"><summary>{zh ? "测量来源与限制" : "Measurement sources and limits"}</summary>
      <dl><div><dt>{zh ? "捕获窗口" : "Capture window"}</dt><dd><time dateTime={outcomes.start}>{outcomes.start}</time> — <time dateTime={outcomes.end}>{outcomes.end}</time><br/>{outcomes.complete ? (zh ? "声明为完整前缀" : "Declared complete prefix") : (zh ? "部分窗口，起点未知" : "Partial window; initial unknown")}</dd></div>
        <div><dt>{zh ? "指标与单位" : "Metric and unit"}</dt><dd>{outcomes.metric.name} · {outcomes.metric.unit}</dd></div>
        <div><dt>{zh ? "方法" : "Method"}</dt><dd><code>{outcomes.method}</code></dd></div>
        <div><dt>{zh ? "来源" : "Source"}</dt><dd>{native.artifact}<br/><code>{native.artifact_sha256}</code></dd></div></dl>
      <dl>{points.map(([key, cn, en]) => {
        const point = outcomes[key];
        return point ? <div key={key}><dt>{zh ? cn : en}</dt><dd>{String(point.value)} {outcomes.metric.unit}<br/><code>{point.candidate_ref}</code><br/>{zh ? "提交" : "Submission"} {point.submission_index} · {zh ? "提交时间" : "Submitted at"}: {point.submitted_at ?? (zh ? "未知" : "Unknown")}<br/>{zh ? "收到结果" : "Result received"}: {point.observed_at}</dd></div> : null;
      })}</dl>
      <ul>{outcomes.limitations.map((line, i) => <li key={i}>{line}</li>)}</ul>
    </details></div>
  </section>;
}

function ResearchMetrics({pack: p, zh, onCitation}: {pack: ResearchProgressPack & {result: TrajectoryAssessment}; zh: boolean; onCitation: (citation: AssessmentCitation, origin?: HTMLButtonElement) => void}) {
  const [selected, setSelected] = useState<TrajectoryMetric | null>(null);
  const panelId = useId(), panel = useRef<HTMLDivElement>(null);
  const origin = useRef<HTMLButtonElement | null>(null);
  const result = p.result;
  const number = (n: number) => new Intl.NumberFormat(zh ? "zh-CN" : "en", {maximumFractionDigits: 1}).format(n);
  const percent = (n: number) => new Intl.NumberFormat(zh ? "zh-CN" : "en", {style: "percent", maximumFractionDigits: 0}).format(n);
  const rows = TRAJECTORY_METRICS.map(metric => {
    const label = metricCopy[metric], row = result.metrics.find(item => item.metric === metric);
    const assessments = result.assessments.filter(item => item.metric === metric);
    // Only complete, declared eligibility can establish exclusions.
    const excluded = row && p.episodes?.length === p.episode_count
      ? p.episodes.filter(episode => !episode.eligible_metrics.includes(metric)) : [];
    const notes = [row?.no ? `${zh ? "否" : "No"} ${number(row.no)}` : "", row?.unknown ? `${zh ? "未知" : "Unknown"} ${number(row.unknown)}` : "", excluded.length ? `${zh ? "不适用" : "Ineligible"} ${number(excluded.length)}` : ""].filter(Boolean);
    const unavailable = !row ? (zh ? "没有此指标记录" : "No metric record") : !row.eligible && !excluded.length ? (zh ? "不适用" : "N/A") : "";
    return {metric, label, row, assessments, excluded, notes, unavailable};
  });
  const active = rows.find(item => item.metric === selected);
  const close = () => {setSelected(null); origin.current?.focus({preventScroll: true}); origin.current?.scrollIntoView({block: "nearest"});};
  return <div className="research-process-counts">
    <div className="research-effect-metrics">
      {rows.map(({metric, label, row, notes, unavailable}) => <button type="button" className="research-effect-metric" data-metric={metric} key={metric}
        aria-expanded={metric === selected} aria-controls={metric === selected ? panelId : undefined}
        aria-label={[label[zh ? 0 : 1], row?.eligible ? `${number(row.yes)} / ${number(row.eligible)} ${zh ? "是 / 适用片段" : "yes / eligible episodes"}` : unavailable, ...notes].filter(Boolean).join(" · ")}
        onClick={event => {
          if (selected === metric) {close(); return;}
          origin.current = event.currentTarget; setSelected(metric);
          requestAnimationFrame(() => {panel.current?.focus({preventScroll: true}); panel.current?.scrollIntoView({block: "nearest"});});
        }}>
        <span className="research-effect-title">{label[zh ? 0 : 1]}<ChevronDown size={13} aria-hidden/></span>
        <strong className="research-effect-number">{row?.eligible ? <>{number(row.yes)}<span> / {number(row.eligible)}</span></> : "—"}</strong>
        {unavailable ? <span className="research-effect-verdict">{unavailable}</span> : null}
        {notes.length ? <small>{notes.join(" · ")}</small> : null}
      </button>)}
    </div>
    {active ? <div className="research-effect-evidence" id={panelId} ref={panel} role="region" tabIndex={-1} aria-labelledby={`${panelId}-heading`}
      key={active.metric} onKeyDown={event => {if (event.key === "Escape") {event.stopPropagation(); close();}}}>
      {(() => {
        const {metric, label, row, assessments, excluded} = active;
        return <>
                <header className="research-effect-detail-heading"><div className="research-progress-title"><h4 id={`${panelId}-heading`}>{label[zh ? 0 : 1]}</h4><ContextHelp label={`${label[zh ? 0 : 1]} · ${zh ? "判读口径" : "Interpretation"}`}>
                  <p>{label[zh ? 2 : 3]}</p>
                  {metric === "benefit_test" ? <p>{zh ? "“是”只表示做过收益检验，不保证收益为正；“否”不是收益为零。" : "Yes means utility was tested, not that utility improved. No is not zero utility."}</p> : null}
                  {metric === "redundancy" ? <p>{zh ? "“是”表示发现无益重复，不能当作正向表现。" : "Yes means avoidable repetition was found; it is not a positive outcome."}</p> : null}
                  {row?.assessed_fraction != null ? <p>{zh ? "已得到明确判读的覆盖" : "Coverage with a decisive judgment"} {percent(row.assessed_fraction)}{row.unknown && row.missing_data_bounds ? <> · {zh ? "未知分别全为否/全为是时，“是”占比" : "Yes share if unknowns are all no / all yes"} {percent(row.missing_data_bounds[0])}–{percent(row.missing_data_bounds[1])} ({zh ? "不是置信区间" : "not a confidence interval"})</> : null}</p> : null}
                </ContextHelp></div><button type="button" onClick={close} aria-label={zh ? "收起指标详情" : "Close metric details"}><X size={16} aria-hidden/></button></header>
                {excluded.length ? <div className="research-effect-excluded"><h4>{zh ? "未列入此指标的片段" : "Episodes excluded from this metric"}</h4><ul>{excluded.map(episode => <li key={episode.id}>{episodeQuestion(result, episode.id, episode)}</li>)}</ul></div> : null}
                <div className="research-assessments">{episodeQuestionGroups(result, assessments, p.episodes).map(group => <div className="research-assessment-group" key={group.items[0]!.record.episode_id}>
                  {group.shared ? <p className="research-assessment-shared-question">{group.before}<span>{zh ? "〔研究片段〕" : "[episode]"}</span>{group.after}</p> : null}
                  {group.items.map(({record: assessment, question}) => {
                  const episode = p.episodes?.find(item => item.id === assessment.episode_id);
                  return <details className="research-assessment" key={assessment.episode_id}>
                    <summary aria-label={`${question} · ${verdictLabel(metric, assessment.verdict, zh)}`}><span className="research-assessment-name">{group.shared ? assessment.episode_id : question}</span><span className="research-assessment-verdict">{verdictLabel(metric, assessment.verdict, zh)}</span><ChevronDown size={14} aria-hidden/></summary>
                    <div className="research-assessment-body">
                      <p className="research-assessment-reason">{assessment.reason}</p>
                      <div className="research-effect-citations">{assessment.citations.map(c => <button type="button" key={`${c.source_id}:${c.first_line}:${c.last_line}`} onClick={event => onCitation(c, event.currentTarget)}><ArrowUpRight size={13} aria-hidden/>{c.source_id} · {zh ? "行" : "lines"} {c.first_line}–{c.last_line}</button>)}</div>
                      {group.shared || (episode && episode.question !== question) ? <details className="research-assessment-original"><summary>{group.shared ? (zh ? "完整问题" : "Full question") : (zh ? "原始研究问题" : "Original research question")}</summary>
                        {group.shared ? <p className="research-assessment-question">{question}</p> : null}
                        {episode && episode.question !== question ? <>{group.shared ? <h5>{zh ? "原文" : "Original"}</h5> : null}<p>{episode.question}</p></> : null}
                      </details> : null}
                    </div>
                  </details>;
                })}</div>)}</div>
                {!row?.eligible ? <p>{zh ? "所选片段没有此指标的适用判读；未记作零。" : "No eligible judgment for this metric in the selected episodes; not recorded as zero."}</p> : null}

        </>;
      })()}
    </div> : null}
  </div>;
}

export function ResearchProgress({pack: p, packs, selectedId, zh, onSelect, onCitation, analysisOpen = false, includeReport = true}: {pack: ResearchProgressPack | null; packs: Pick<ResearchProgressPack, "pack_id" | "title">[]; selectedId: string; zh: boolean; analysisOpen?: boolean; includeReport?: boolean; onSelect: (id: string) => void; onCitation: (citation: AssessmentCitation, origin?: HTMLButtonElement) => void}) {
  const measured = p?.solver_usage, usage = measured?.projection, result = p?.result;
  const historical = p?.applicability === "historical";
  const resultHelp = historical ? (result ? (zh ? "历史分析 · 当前判断未核验" : "Historical analysis · Current judgments unverified")
    : (zh ? "历史来源 · 所选范围尚未分析" : "Historical source · Selected scope not analyzed"))
    : p && !result ? (zh ? "所选范围尚未分析" : "Selected scope not analyzed") : (zh ? "指标说明" : "About these metrics");
  const incomplete = usage?.incomplete_turn;
  const coverage = usage?.request_coverage;
  const number = (n: number) => new Intl.NumberFormat(zh ? "zh-CN" : "en", {maximumFractionDigits: 1}).format(n);
  const duration = (s: number) => `${number(s / 60)} ${zh ? "分钟" : "min"}`;
  const stamp = (s: string) => new Intl.DateTimeFormat(zh ? "zh-CN" : "en", {timeZone: "UTC", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false}).format(new Date(s));
  return <section className="research-progress" aria-label={zh ? "长程效果与用量" : "Long-horizon results and usage"}>
    <header><div className="research-progress-heading"><div className="research-progress-title"><h3>{p?.native_outcomes ? (zh ? "研究结果" : "Research results") : (zh ? "研究效果判读" : "Research effectiveness")}</h3><ContextHelp label={resultHelp} icon={historical ? TriangleAlert : p && !result ? CircleHelp : undefined}>
      {historical ? <p>{zh ? "来源或方法已变化；历史捕获与判读不能当作当前结果。" : "The source or method changed; historical captures and judgments cannot establish current results."}</p> : null}
      {p && !result ? <p>{zh ? "可先浏览已采集的测量与记录；深度分析需单独点击，不会随浏览自动运行。" : "Captured measurements and records remain readable. Deep analysis requires a separate action and never runs while browsing."}</p> : null}
      {p ? <p>{p.title}</p> : null}
      <p>{zh ? "分析器判读未校准，仅限所选片段；范围外或未捕获的工作未纳入。数字为“是 / 适用片段”，未知仍在分母中；点选指标可看依据。" : "Uncalibrated judgments of selected episodes only; work outside this scope or not captured is excluded. Counts are yes / eligible episodes, including unknowns. Select a metric for evidence."}</p>
      <p>{zh ? "不适用来自原始片段的明确取样规则，不算“否”或“未知”；展开指标可看被排除的片段。" : "Ineligible episodes follow the source's explicit selection, not a no or unknown judgment. Expand a metric to identify them."}</p>
      <p>{zh ? "收益检验的“是”不保证收益为正；无益重复的“是”表示发现问题。六项分别判断，不合成总分、成功率或原生收益。" : "Yes for benefit testing does not imply positive utility; yes for repetition identifies a problem. The six judgments are not a total score, success rate or native utility."}</p>
      <p>{p?.selection.exhaustive ? (zh ? "仅覆盖声明的固定分析范围，不等于覆盖全部研究。" : "Covers the declared fixed analysis scope, not necessarily the whole study.") : p?.selection.population_episodes == null ? (zh ? "定向抽样，全程覆盖未知。" : "Purposive sample; whole-run coverage is unknown.") : (zh ? "定向抽样，不代表全程。" : "Purposive sample, not the whole run.")}</p>
      <p>{zh ? "原生测量缺失时保持未采集；分析器判读不能补成实际收益。完整取样规则与版本在“来源与用量”中。" : "Missing native measurements remain uncaptured; judgments cannot supply realized utility. Full selection rules and revisions are under Sources and usage."}</p>
    </ContextHelp></div>{p ? <p className="research-progress-scope" data-pack-id={p.pack_id}>{number(p.episode_count)} {zh ? "个片段" : "episodes"} · {p.selection.exhaustive ? (zh ? "固定范围" : "Fixed scope") : (zh ? "定向抽样" : "Purposive sample")}{result ? (zh ? " · 未校准" : " · Uncalibrated") : ""}</p> : null}</div>{packs.length > 1 ? <label>{zh ? "评估范围" : "Evaluation scope"}<select value={selectedId} onChange={e => onSelect(e.target.value)}>{packs.map(pack => <option key={pack.pack_id} value={pack.pack_id}>{pack.title}</option>)}</select></label> : null}</header>
    {p ? <>
      {p.native_outcomes ? <NativeResults native={p.native_outcomes} zh={zh} onCitation={onCitation}/> : null}
      {p.native_outcomes ? <RetainedImprovement native={p.native_outcomes} zh={zh} onCitation={onCitation}/> : null}
      {result?.synthesis ? includeReport && !analysisOpen && <><ResearchSynthesis synthesis={result.synthesis} zh={zh} onCitation={onCitation}/>
        <details><summary>{zh ? "六项过程判读" : "Six process judgments"}</summary><ResearchMetrics key={`${p.pack_id}:${result.analysis_key}:${zh}`} pack={{...p, result}} zh={zh} onCitation={onCitation}/></details></>
        : result ? <ResearchMetrics key={`${p.pack_id}:${result.analysis_key}:${zh}`} pack={{...p, result}} zh={zh} onCitation={onCitation}/>
        : null}
      <details className="research-progress-basis"><summary>{zh ? "来源与用量" : "Sources and usage"}<ContextHelp icon={CircleHelp} label={p.native_outcomes ? (zh ? "完整成本未知" : "Total cost unknown") : (zh ? "原生测量未采集 · 完整成本未知" : "Native measurements uncaptured · Total cost unknown")}><p>{p.native_outcomes ? "" : (zh ? "原生测量未采集。" : "Native measurements were not captured. ")}{zh ? "完整成本未知。" : "Total cost is unknown."}</p></ContextHelp></summary>
        <section className="research-usage" aria-label={zh ? "求解用量" : "Solver usage"}>
          <div className="research-usage-heading"><h4>{zh ? "求解用量" : "Solver usage"}</h4>
            <span className="research-usage-scope">{usage ? <>{number(usage.completed_turns)} {zh ? "轮完整记录" : "complete Turns"}{measured?.applicability === "historical" ? (zh ? " · 历史捕获" : " · Historical capture") : ""}</> : (zh ? "未采集" : "Not captured")}</span>
            <ContextHelp icon={measured?.applicability === "historical" ? TriangleAlert : usage ? undefined : CircleHelp} label={measured?.applicability === "historical" ? (zh ? "历史捕获 · 当前用量未核验" : "Historical capture · Current usage unverified") : usage ? (zh ? "用量范围与成本" : "Usage scope and cost") : (zh ? "求解用量未采集" : "Solver usage not captured")}>
            <p>{zh ? "只读捕获，窗口可能与判读片段及 Explore 历史不同。金额与完整研究成本未测得，不能从 token 数推算。" : "Read-only capture; its window can differ from the judgments and Explore history. Monetary and complete study costs are unmeasured, not inferred from token counts."}</p>
            {measured?.applicability === "historical" ? <p>{zh ? "历史捕获：用量来源已变化或不可读，当前用量未核验。" : "Historical capture: the usage source changed or is unavailable; current usage is unverified."}</p> : null}
            {!usage ? <p>{zh ? "尚无用量捕获；未测，不记零。" : "No usage capture; unmeasured, not zero."}</p> : null}
            {usage ? <p>{usage.method === "codex_observed_session_counters_v1" ? (zh ? "仅已观测计数；未记录的请求、后续用量、继承前缀、设置、评测和操作者成本未知。" : "Observed counters only. Unrecorded requests, later usage, inherited prefixes, setup, evaluation and operator costs remain unknown.") : (zh ? "仅本次完整捕获；继承前缀、设置、原生评测、操作者及后续未完成轮不包含。" : "This complete capture only; inherited prefixes, setup, native evaluation, operator work and later incomplete Turns are excluded.")}</p> : null}
          </ContextHelp></div>
          <dl className="research-progress-values">
            <div><dt>{zh ? "捕获窗口跨度" : "Captured elapsed time"}</dt><dd>{usage ? duration(usage.elapsed_seconds) : "—"}</dd></div>
            <div><dt>{zh ? "未缓存输入" : "Uncached input"}</dt><dd>{usage ? number(usage.uncached_input_tokens) : "—"}<span> tokens</span></dd></div>
            <div><dt>{zh ? "输出（含推理）" : "Output (includes reasoning)"}</dt><dd>{usage ? number(usage.output_tokens) : "—"}<span> tokens</span></dd></div>
          </dl>
          {incomplete ? <div className="research-usage-tail">
            <div className="research-usage-heading"><strong>{zh ? "末轮未完成" : "Final Turn incomplete"}</strong><ContextHelp icon={incomplete.usage ? undefined : CircleHelp} label={zh ? "末轮用量说明" : "About unfinished Turn usage"}>
              <p>{zh ? "仅含已持久化用量，不能据此判定运行已结束。" : "Persisted usage only; this does not establish termination."}</p>
              {!incomplete.usage ? <p>{zh ? "末轮尚无计数观测，不记零。" : "No counter observation for the unfinished Turn; not zero."}</p> : null}
            </ContextHelp></div>
            <p>{zh ? "其中输入" : "Of which input"} <strong>{incomplete.usage ? number(incomplete.usage.input_tokens) : "—"}</strong> · {zh ? "输出" : "output"} <strong>{incomplete.usage ? number(incomplete.usage.output_tokens) : "—"}</strong> tokens</p>
          </div> : null}
          <div className="research-usage-secondary"><LoopXInputShare usage={usage} zh={zh}/>
          {usage ? <div className="research-request-coverage">
            <div className="research-usage-heading"><h4>{zh ? "请求覆盖" : "Request coverage"}</h4>
              {coverage?.status !== "verified" ? <span className="research-usage-scope">{coverage ? (zh ? "核对失败" : "Reconciliation failed") : (zh ? "未核对" : "Not reconciled")}</span> : null}
              <ContextHelp icon={coverage?.status === "verified" ? undefined : coverage ? TriangleAlert : CircleHelp} label={coverage?.status === "verified" ? (zh ? "请求计数口径" : "Request counter basis") : coverage ? (zh ? "请求覆盖核对失败" : "Request reconciliation failed") : (zh ? "请求覆盖未核对" : "Request coverage not reconciled")}>
              <p>{zh ? "逐响应去重后，核对轮次与会话累计量、上下文压缩记录及会话计数。以下是独立核对口径，与上方会话计数不相加，也不改写 Goal 账本。" : "Deduplicated responses are reconciled against Turn and session totals, compaction records and session counters. This separate basis is not added to the session counters above and does not rewrite the Goal ledger."}</p>
              <p>{zh ? "仅含捕获中已记录的请求；未记录请求及完整费用仍未知。" : "Persisted requests in this capture only; unrecorded requests and complete cost remain unknown."}</p>
              {coverage?.status === "verified" ? <p>{coverage.counter_basis === "all_requests"
                ? (zh ? "会话计数含压缩请求" : "Session counters include compaction requests")
                : coverage.counter_basis === "ordinary_requests"
                  ? (zh ? "会话计数不含压缩请求" : "Session counters exclude compaction requests")
                  : (zh ? "会话计数口径未确定" : "Session counter basis undetermined")}</p>
                : <p>{coverage ? (zh ? "请求记录未通过核对；压缩用量与请求总量未知。" : "Request records could not be reconciled; compaction usage and request totals are unknown.") : (zh ? "请求覆盖未核对；上方会话计数不能代表全部请求用量。" : "Request coverage has not been checked; session counters above do not establish all-request usage.")}</p>}
            </ContextHelp></div>
            {coverage?.status === "verified" ? <>
              <p>{number(coverage.unique_requests)} {zh ? "次请求" : "requests"} · {number(coverage.compaction_requests)} {zh ? "次上下文压缩" : "context compactions"}</p>
              <dl className="research-request-values">
                <div><dt>{zh ? "全部请求 · 未缓存输入" : "All requests · uncached input"}</dt><dd>{number(coverage.all_requests.input_tokens - coverage.all_requests.cached_input_tokens)} <span>tokens</span></dd></div>
                <div><dt>{zh ? "全部请求 · 输出" : "All requests · output"}</dt><dd>{number(coverage.all_requests.output_tokens)} <span>tokens</span></dd></div>
                <div><dt>{zh ? "其中压缩 · 未缓存输入" : "Of which compaction · uncached input"}</dt><dd>{number(coverage.compaction.input_tokens - coverage.compaction.cached_input_tokens)} <span>tokens</span></dd></div>
                <div><dt>{zh ? "其中压缩 · 输出" : "Of which compaction · output"}</dt><dd>{number(coverage.compaction.output_tokens)} <span>tokens</span></dd></div>
              </dl>
            </> : null}
          </div> : null}
          </div>
          {usage && measured ? <details className="research-usage-detail"><summary>{zh ? "计数明细与来源" : "Counter details and source"}</summary>
            <dl className="research-usage-basis">
              <dt>{zh ? "捕获时间（UTC）" : "Capture window (UTC)"}</dt><dd><time dateTime={usage.start}>{stamp(usage.start)}</time> — <time dateTime={usage.end}>{stamp(usage.end)}</time></dd>
              <dt>{zh ? "轮内时间合计" : "Time within Turns"}</dt><dd>{duration(usage.turn_elapsed_seconds)}<p>{zh ? "含工具和编排；不等于 CPU、生成时间或可节省时间。" : "Includes tools and orchestration; not CPU, generation or avoidable time."}</p></dd>
              <dt>{zh ? "输入总量" : "Total input"}</dt><dd>{number(usage.input_tokens)} tokens · {zh ? "其中缓存" : "including cached"} {number(usage.cached_input_tokens)}</dd>
              <dt>{zh ? "用量来源" : "Usage source"}</dt><dd>{measured.artifact}<code>{measured.artifact_sha256}</code></dd>
              <dt>{zh ? "计数方法" : "Counter method"}</dt><dd><code>{usage.method}</code></dd>
              {coverage ? <><dt>{zh ? "请求核对" : "Request reconciliation"}</dt><dd><code>{coverage.method}</code>{coverage.status === "unavailable" ? <p>{coverage.reason}</p> : <>
                <p>{zh ? "请求观测截至（UTC）" : "Requests observed through (UTC)"} {stamp(coverage.observed_through)}</p>
                <p>{coverage.counter_observations} {zh ? "次会话计数核对" : "session counter checks"} · {coverage.duplicate_records} {zh ? "条重复响应已去重" : "duplicate response records excluded"}</p>
                <p>{zh ? "全部请求输入" : "All request input"} {number(coverage.all_requests.input_tokens)} · {zh ? "其中缓存" : "including cached"} {number(coverage.all_requests.cached_input_tokens)} tokens</p>
                <p>{zh ? "压缩输入" : "Compaction input"} {number(coverage.compaction.input_tokens)} · {zh ? "其中缓存" : "including cached"} {number(coverage.compaction.cached_input_tokens)} tokens</p>
              </>}</dd></> : null}
            </dl>
          </details> : null}
          <div className="research-analysis-cost">
            <div className="research-usage-heading"><h4>{zh ? "分析尝试" : "Analysis attempt"}</h4>
              <span className="research-usage-scope">{p.job?.attempt != null ? (zh ? `第 ${p.job.attempt} 次` : `Attempt ${p.job.attempt}`) : (zh ? "未采集" : "Not captured")}</span><ContextHelp label={zh ? "分析开销范围" : "Analysis cost scope"}>
              <p>{zh ? "仅当前回执的本次尝试，不含更早失败、恢复、评测或操作者开销。不与求解捕获相加。" : "This attempt's receipt only; earlier failures, recovery, evaluation and operator work are excluded. Not added to solver capture."}</p>
              {!p.job?.usage ? <p>{zh ? "未测得，不记零。" : "Unmeasured, not zero."}</p> : null}
            </ContextHelp></div>
            {p.job?.usage || p.job?.analysis_wall_seconds != null ? <dl className="research-progress-values research-analysis-values">
              <div><dt>{zh ? "输入" : "Input"}</dt><dd>{p.job?.usage?.input_tokens == null ? "—" : number(p.job.usage.input_tokens)}<span> tokens</span></dd></div>
              <div><dt>{zh ? "输出" : "Output"}</dt><dd>{p.job?.usage?.output_tokens == null ? "—" : number(p.job.usage.output_tokens)}<span> tokens</span></dd></div>
              <div><dt>{zh ? "用时" : "Elapsed time"}</dt><dd>{p.job?.analysis_wall_seconds == null ? "—" : duration(p.job.analysis_wall_seconds)}</dd></div>
            </dl> : null}
          </div>
        </section>
        <details className="research-source-detail"><summary>{zh ? "分析范围与来源" : "Analysis scope and sources"}</summary><dl className="research-usage-basis">
          <dt>{zh ? "题目 / 运行" : "Case / run"}</dt><dd>{p.scope.case_id}<br/>{p.scope.run_id}</dd>
          <dt>{zh ? "证据窗口" : "Evidence window"}</dt><dd>{p.scope.window}</dd><dt>{zh ? "取样规则" : "Selection rule"}</dt><dd>{p.selection.rule}</dd>
          <dt>{zh ? "总体研究片段数" : "Population of episodes"}</dt><dd>{p.selection.population_episodes == null ? (zh ? "未知" : "Unknown") : number(p.selection.population_episodes)}</dd>
          {result ? <><dt>{zh ? "分析方法" : "Analysis method"}</dt><dd>{result.method_version} · {zh ? "Agent 判读未校准" : "Uncalibrated Agent judgment"}</dd><dt>{zh ? "结果标识" : "Result key"}</dt><dd><code>{result.analysis_key}</code></dd><dt>{zh ? "分析限制" : "Limitations"}</dt><dd>{result.limitations.map((line, i) => <p key={i}>{line}</p>)}</dd></> : null}
          {p.job?.config ? <><dt>{zh ? "分析模型" : "Analysis model"}</dt><dd>{p.job.config.model}</dd></> : null}
          <dt>{zh ? "来源版本" : "Source revision"}</dt><dd><code>{p.scope.revision}</code></dd>
        </dl></details>
      </details>
    </> : null}
  </section>;
}
