import {ArrowUpRight, ChevronDown} from "lucide-react";
import {TRAJECTORY_METRICS} from "../../../../../../loopx/control_plane/capabilities/trajectory_assessment_types.js";
import type {AssessmentCitation, AssessmentVerdict, TrajectoryAssessment, TrajectoryEpisode, TrajectoryInput, TrajectoryMetric, TrajectorySolverUsage} from "../../../../../../loopx/control_plane/capabilities/trajectory_assessment_types.js";
import type {TrajectoryNativeOutcomes} from "../../../../../../loopx/control_plane/capabilities/trajectory_native_outcomes.js";
import {ContextHelp} from "./context-help";
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

// Format the native owner's existing projection, without deriving a gain,
// deciding adoption, or borrowing values from another pack.
function RetainedImprovement({native, zh, onCitation}: {native: NonNullable<ResearchProgressPack["native_outcomes"]>; zh: boolean; onCitation: (citation: AssessmentCitation, origin?: HTMLButtonElement) => void}) {
  const outcomes = native.projection, improvement = outcomes.retained_improvement;
  if (!improvement) return null;
  const format = (value: number) => new Intl.NumberFormat(zh ? "zh-CN" : "en", {maximumSignificantDigits: 10, signDisplay: "exceptZero"}).format(value);
  const label = zh ? "已采用成果增量" : "Retained improvement";
  return <section className="research-retained-improvement" aria-label={label}>
    <div className="research-progress-title"><h4>{label}</h4><ContextHelp label={zh ? "成果增量说明" : "About retained improvement"}>
      <p>{zh ? "固定基线与实际保留结果的原生指标差值，正数表示改善。越低越好的指标反向计算；回退到基线为零，缺少基线或保留证据为未知。" : "Native change from a fixed baseline to the actually retained result. Positive means improvement; lower-is-better metrics reverse the subtraction. Returning to baseline is zero; missing baseline or retention evidence is unknown."}</p>
      <p>{zh ? "只代表所选比较，不是 LoopX 的独立贡献或原目标已达成；不能跨题、跨单位或重叠窗口相加。展开数值可看原文。" : "This comparison alone does not establish LoopX's causal contribution or objective completion. Do not sum across tasks, units or overlapping windows. Expand the value for original evidence."}</p>
    </ContextHelp></div>
    {native.applicability === "historical" ? <p className="research-progress-warning" role="status">{zh ? "历史捕获 · 当前结果未核验" : "Historical capture · current result unverified"}</p> : null}
    <details><summary aria-label={`${label}: ${improvement.value === null ? (zh ? "未知" : "Unknown") : `${format(improvement.value)} ${outcomes.metric.unit}`}`}>
      <strong data-retained-improvement>{improvement.value === null ? "—" : format(improvement.value)}</strong>
      <span>{improvement.value === null ? (zh ? "未知" : "Unknown") : outcomes.metric.unit} · {outcomes.metric.name}</span><ChevronDown size={14} aria-hidden/>
    </summary><dl>
      {([[improvement.baseline, zh ? "比较基线" : "Comparison baseline"], [outcomes.retained, zh ? "实际保留" : "Actually retained"]] as const).map(([point, title]) => <div key={title}><dt>{title}</dt><dd>{point ? <button type="button" onClick={event => onCitation(point.citation, event.currentTarget)}>{String(point.value)} · {point.candidate_ref}<ArrowUpRight size={13} aria-hidden/></button> : (zh ? "未知" : "Unknown")}</dd></div>)}
      <div><dt>{zh ? "保留依据" : "Retention evidence"}</dt><dd>{improvement.retention_citation ? <button type="button" onClick={event => onCitation(improvement.retention_citation!, event.currentTarget)}>{zh ? "查看原文" : "Read original evidence"}<ArrowUpRight size={13} aria-hidden/></button> : (zh ? "未采集" : "Uncaptured")}</dd></div>
      <div><dt>{zh ? "最后候选差值" : "Final candidate change"}</dt><dd>{improvement.final_candidate_delta === null ? (zh ? "未知" : "Unknown") : `${format(improvement.final_candidate_delta)} ${outcomes.metric.unit}`} · {zh ? "不代表已采用收益" : "not realized retention"}</dd></div>
    </dl></details>
  </section>;
}

function NativeResults({native, zh, onCitation}: {native: ResearchProgressPack["native_outcomes"]; zh: boolean; onCitation: (citation: AssessmentCitation, origin?: HTMLButtonElement) => void}) {
  const outcomes = native?.projection;
  if (!native || !outcomes) return <p className="research-native-empty">{zh ? "原生测量 · 未采集" : "Native measurements · not captured"}</p>;
  const historical = native.applicability === "historical";
  const points = [["initial", "首次提交", "Initial submission"], ["best_valid", "窗口内最佳有效值", "Best valid in capture"], ["retained", "原生保留结果", "Native retained result"], ["final_valid", "最后有效提交", "Final valid submission"]] as const;
  return <section className="research-native-results" aria-label={zh ? "原生测量" : "Native measurements"}>
    <div className="research-progress-title"><h4>{historical ? (zh ? "历史原生测量" : "Historical native measurements") : (zh ? "原生测量" : "Native measurements")}</h4><ContextHelp label={zh ? "测量说明" : "About native measurements"}>
      <p>{zh ? "最佳只限本次捕获窗口；原生保留与最后提交可能不同。未采集的值保留未知，不记零。" : "Best is limited to this capture; retained and final submissions can differ. Uncaptured values remain unknown, not zero."}</p>
      <p>{zh ? "检验过收益、采用了证据及原目标达成各需各自证据，不能由这些数值推断。" : "Utility testing, evidence adoption and original-objective success each need their own evidence; these values cannot establish them."}</p>
    </ContextHelp></div>
    {historical ? <p className="research-progress-warning" role="status">{zh ? "来源已变化或不可读；以下仅为历史捕获，当前结果未核验。" : "The source changed or is unavailable. These are historical captures; current results are unverified."}</p> : null}
    <p>{outcomes.metric.name} · {outcomes.metric.unit} · {outcomes.metric.higher_is_better ? (zh ? "越高越好" : "higher is better") : (zh ? "越低越好" : "lower is better")} · {outcomes.valid_count} / {outcomes.sample_count} {zh ? "项有效结果" : "valid results"}</p>
    <p>{outcomes.complete ? (zh ? "声明为完整前缀" : "Declared complete prefix") : (zh ? "部分窗口，起点未知" : "Partial window; initial unknown")} · <time dateTime={outcomes.start}>{outcomes.start}</time> — <time dateTime={outcomes.end}>{outcomes.end}</time></p>
    <dl className="research-native-values">{points.map(([key, cn, en]) => {
      const point = outcomes[key];
      return <div key={key} data-native-point={key}><dt>{zh ? cn : en}</dt><dd>{point ? <>
        <button type="button" onClick={event => onCitation(point.citation, event.currentTarget)} aria-label={`${zh ? cn : en}: ${String(point.value)} · ${zh ? "查看原始证据" : "Read original evidence"}`}><strong>{String(point.value)}</strong><ArrowUpRight size={13} aria-hidden/></button>
        <small>{zh ? "提交" : "Submission"} {point.submission_index}</small>
      </> : <span>{zh ? "— 未知" : "— Unknown"}</span>}</dd></div>;
    })}</dl>
    <details className="research-native-basis"><summary>{zh ? "测量来源与限制" : "Measurement sources and limits"}</summary>
      <p>{native.artifact}<br/><code>{native.artifact_sha256}</code></p>
      <dl>{points.map(([key, cn, en]) => {
        const point = outcomes[key];
        return point ? <div key={key}><dt>{zh ? cn : en}</dt><dd><code>{point.candidate_ref}</code><br/>{zh ? "提交" : "Submitted"}: {point.submitted_at ?? (zh ? "未知" : "Unknown")}<br/>{zh ? "收到结果" : "Result received"}: {point.observed_at}</dd></div> : null;
      })}</dl>
      <ul>{outcomes.limitations.map((line, i) => <li key={i}>{line}</li>)}</ul>
    </details>
  </section>;
}

export function ResearchProgress({pack: p, packs, selectedId, zh, onSelect, onCitation}: {pack: ResearchProgressPack | null; packs: Pick<ResearchProgressPack, "pack_id" | "title">[]; selectedId: string; zh: boolean; onSelect: (id: string) => void; onCitation: (citation: AssessmentCitation, origin?: HTMLButtonElement) => void}) {
  const measured = p?.solver_usage, usage = measured?.projection, result = p?.result;
  const incomplete = usage?.incomplete_turn;
  const number = (n: number) => new Intl.NumberFormat(zh ? "zh-CN" : "en", {maximumFractionDigits: 1}).format(n);
  const percent = (n: number) => new Intl.NumberFormat(zh ? "zh-CN" : "en", {style: "percent", maximumFractionDigits: 0}).format(n);
  const duration = (s: number) => `${number(s / 60)} ${zh ? "分钟" : "min"}`;
  const stamp = (s: string) => new Intl.DateTimeFormat(zh ? "zh-CN" : "en", {timeZone: "UTC", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false}).format(new Date(s));
  return <section className="research-progress" aria-label={zh ? "长程效果与用量" : "Long-horizon results and usage"}>
    <header><div className="research-progress-heading"><div className="research-progress-title"><h3>{zh ? "研究效果判读" : "Research effectiveness"}</h3><ContextHelp label={zh ? "指标说明" : "About these metrics"}>
      {p ? <p>{p.title}</p> : null}
      <p>{zh ? "分析器判读未校准，仅限所选片段；范围外或未捕获的工作未纳入。数字为“是 / 适用片段”，未知仍在分母中；点选指标可看依据。" : "Uncalibrated judgments of selected episodes only; work outside this scope or not captured is excluded. Counts are yes / eligible episodes, including unknowns. Select a metric for evidence."}</p>
      <p>{zh ? "不适用来自原始片段的明确取样规则，不算“否”或“未知”；展开指标可看被排除的片段。" : "Ineligible episodes follow the source's explicit selection, not a no or unknown judgment. Expand a metric to identify them."}</p>
      <p>{zh ? "收益检验的“是”不保证收益为正；无益重复的“是”表示发现问题。六项分别判断，不合成总分、成功率或原生收益。" : "Yes for benefit testing does not imply positive utility; yes for repetition identifies a problem. The six judgments are not a total score, success rate or native utility."}</p>
      <p>{p?.selection.exhaustive ? (zh ? "仅覆盖声明的固定分析范围，不等于覆盖全部研究。" : "Covers the declared fixed analysis scope, not necessarily the whole study.") : p?.selection.population_episodes == null ? (zh ? "定向抽样，全程覆盖未知。" : "Purposive sample; whole-run coverage is unknown.") : (zh ? "定向抽样，不代表全程。" : "Purposive sample, not the whole run.")}</p>
      <p>{zh ? "原生测量缺失时保持未采集；分析器判读不能补成实际收益。完整取样规则与版本在“来源与用量”中。" : "Missing native measurements remain uncaptured; judgments cannot supply realized utility. Full selection rules and revisions are under Sources and usage."}</p>
    </ContextHelp></div>{p ? <p className="research-progress-scope" data-pack-id={p.pack_id}>{number(p.episode_count)} {zh ? "个片段" : "episodes"} · {p.selection.exhaustive ? (zh ? "固定范围" : "Fixed scope") : (zh ? "定向抽样" : "Purposive sample")}{result ? (zh ? " · 未校准" : " · Uncalibrated") : ""}</p> : null}</div>{packs.length > 1 ? <label>{zh ? "评估范围" : "Evaluation scope"}<select value={selectedId} onChange={e => onSelect(e.target.value)}>{packs.map(pack => <option key={pack.pack_id} value={pack.pack_id}>{pack.title}</option>)}</select></label> : null}</header>
    {p ? <>
      {p.applicability === "historical" ? <p className="research-progress-warning" role="status">{zh ? "历史分析：来源或方法已变化，不能当作当前判断。" : "Historical analysis: the source or method changed; these are not current judgments."}</p> : null}
      {p.native_outcomes ? <RetainedImprovement native={p.native_outcomes} zh={zh} onCitation={onCitation}/> : null}
      {result ? <div className="research-process-counts">
        <div className="research-effect-metrics" key={p.pack_id}>
          {TRAJECTORY_METRICS.map(metric => {
            const label = metricCopy[metric], row = result.metrics.find(item => item.metric === metric);
            const assessments = result.assessments.filter(item => item.metric === metric);
            // Format declared eligibility only when the complete episode list
            // is present. Counts or missing assessments cannot establish exclusion.
            const excluded = row && p.episodes?.length === p.episode_count
              ? p.episodes.filter(episode => !episode.eligible_metrics.includes(metric)) : [];
            const notes = [row?.no ? `${zh ? "否" : "No"} ${number(row.no)}` : "", row?.unknown ? `${zh ? "未知" : "Unknown"} ${number(row.unknown)}` : "", excluded.length ? `${zh ? "不适用" : "Ineligible"} ${number(excluded.length)}` : ""].filter(Boolean);
            const unavailable = !row ? (zh ? "没有此指标记录" : "No metric record") : !row.eligible && !excluded.length ? (zh ? "不适用" : "N/A") : "";
            return <details className="research-effect-metric" data-metric={metric} key={metric}>
              <summary aria-label={[label[zh ? 0 : 1], row?.eligible ? `${number(row.yes)} / ${number(row.eligible)} ${zh ? "是 / 适用片段" : "yes / eligible episodes"}` : unavailable, ...notes].filter(Boolean).join(" · ")}><span className="research-effect-title">{label[zh ? 0 : 1]}<ChevronDown size={13} aria-hidden/></span>
                <strong className="research-effect-number">{row?.eligible ? <>{number(row.yes)}<span> / {number(row.eligible)}</span></> : "—"}</strong>
                {unavailable ? <span className="research-effect-verdict">{unavailable}</span> : null}
                {notes.length ? <small>{notes.join(" · ")}</small> : null}
              </summary>
              <div className="research-effect-evidence">
                <div className="research-effect-help"><span>{zh ? "逐项判读" : "Episode judgments"}</span><ContextHelp label={`${label[zh ? 0 : 1]} · ${zh ? "判读口径" : "Interpretation"}`}>
                  <p>{label[zh ? 2 : 3]}</p>
                  {metric === "benefit_test" ? <p>{zh ? "“是”只表示做过收益检验，不保证收益为正；“否”不是收益为零。" : "Yes means utility was tested, not that utility improved. No is not zero utility."}</p> : null}
                  {metric === "redundancy" ? <p>{zh ? "“是”表示发现无益重复，不能当作正向表现。" : "Yes means avoidable repetition was found; it is not a positive outcome."}</p> : null}
                  {row?.assessed_fraction != null ? <p>{zh ? "已得到明确判读的覆盖" : "Coverage with a decisive judgment"} {percent(row.assessed_fraction)}{row.unknown && row.missing_data_bounds ? <> · {zh ? "未知分别全为否/全为是时，“是”占比" : "Yes share if unknowns are all no / all yes"} {percent(row.missing_data_bounds[0])}–{percent(row.missing_data_bounds[1])} ({zh ? "不是置信区间" : "not a confidence interval"})</> : null}</p> : null}
                </ContextHelp></div>
                {excluded.length ? <div className="research-effect-excluded"><h4>{zh ? "未列入此指标的片段" : "Episodes excluded from this metric"}</h4><ul>{excluded.map(episode => <li key={episode.id}>{episode.question}</li>)}</ul></div> : null}
                <div className="research-assessments">{assessments.map(assessment => {
                  const episode = p.episodes?.find(item => item.id === assessment.episode_id);
                  // Use the declared identity, not a guessed title extracted
                  // from the question. The complete source wording stays below.
                  return <details className="research-assessment" key={assessment.episode_id}>
                    <summary><span className="research-assessment-name">{assessment.episode_id}</span><span className="research-assessment-verdict">{verdictLabel(metric, assessment.verdict, zh)}</span><ChevronDown size={14} aria-hidden/></summary>
                    <div className="research-assessment-body">
                      {episode ? <><p className="research-assessment-question">{result.episode_questions?.find(q => q.episode_id === episode.id)?.question ?? episode.question}</p>
                        {result.episode_questions ? <details><summary>{zh ? "原始研究问题" : "Original research question"}</summary><p>{episode.question}</p></details> : null}</> : null}
                      <p>{assessment.reason}</p>
                      <div className="research-effect-citations">{assessment.citations.map(c => <button type="button" key={`${c.source_id}:${c.first_line}:${c.last_line}`} onClick={event => onCitation(c, event.currentTarget)}><ArrowUpRight size={13} aria-hidden/>{c.source_id} · {zh ? "行" : "lines"} {c.first_line}–{c.last_line}</button>)}</div>
                    </div>
                  </details>;
                })}</div>
                {!row?.eligible ? <p>{zh ? "所选片段没有此指标的适用判读；未记作零。" : "No eligible judgment for this metric in the selected episodes; not recorded as zero."}</p> : null}
              </div>
            </details>;
          })}
        </div>
      </div> : <p className="research-effect-empty" role="status">{zh ? "所选范围尚未分析" : "No analysis for the selected scope"}</p>}
      <details className="research-progress-basis"><summary>{zh ? "来源与用量" : "Sources and usage"}<span>{p.native_outcomes ? "" : (zh ? " · 原生测量未采集" : " · Native measurements uncaptured")}{zh ? " · 完整成本未知" : " · Total cost unknown"}</span></summary><dl>
        <dt>{zh ? "题目 / 运行" : "Case / run"}</dt><dd>{p.scope.case_id}<br/>{p.scope.run_id}</dd><dt>{zh ? "证据窗口" : "Evidence window"}</dt><dd>{p.scope.window}</dd><dt>{zh ? "取样规则" : "Selection rule"}</dt><dd>{p.selection.rule}</dd>
        <dt>{zh ? "总体研究片段数" : "Population of episodes"}</dt><dd>{p.selection.population_episodes == null ? (zh ? "未知" : "Unknown") : number(p.selection.population_episodes)}</dd>
        {result ? <><dt>{zh ? "分析方法" : "Analysis method"}</dt><dd>{result.method_version} · {zh ? "Agent 判读未校准" : "Uncalibrated Agent judgment"}</dd><dt>{zh ? "结果标识" : "Result key"}</dt><dd><code>{result.analysis_key}</code></dd><dt>{zh ? "分析限制" : "Limitations"}</dt><dd>{result.limitations.map((line, i) => <p key={i}>{line}</p>)}</dd></> : null}
        <dt>{zh ? "来源版本" : "Source revision"}</dt><dd><code>{p.scope.revision}</code></dd>
      </dl>
      {p.native_outcomes ? <NativeResults native={p.native_outcomes} zh={zh} onCitation={onCitation}/> : null}
      <section className="research-usage"><h4>{zh ? "用量与成本" : "Usage and cost"}{measured?.applicability === "historical" ? (zh ? " · 历史捕获" : " · Historical capture") : ""}</h4>
        <p>{zh ? "只读捕获，窗口可能与以上判读片段及下方 Explore 历史不同。" : "Read-only capture; its window can differ from the judgments above and Explore history below."}</p>
        <dl className="research-usage-basis">
          <dt>{zh ? "分析尝试开销" : "Analysis attempt cost"}</dt><dd>{p.job?.usage ? <>
            {zh ? "第" : "Attempt"} {p.job.attempt ?? "—"} · {zh ? "输入" : "input"} {p.job.usage.input_tokens == null ? "—" : number(p.job.usage.input_tokens)} · {zh ? "输出" : "output"} {p.job.usage.output_tokens == null ? "—" : number(p.job.usage.output_tokens)} tokens
            {p.job.config ? ` · ${p.job.config.model}` : ""}
            {p.job.analysis_wall_seconds != null ? ` · ${duration(p.job.analysis_wall_seconds)}` : ""}
          </> : (zh ? "— 本次分析尝试未测得，不记零" : "— No measurement for this analysis attempt; not zero")}</dd>
          <dt>{zh ? "合计边界" : "Aggregation boundary"}</dt><dd>{zh ? "分析仅显示当前回执的本次尝试；不含更早失败、恢复、评测或操作者开销。不与求解捕获盲目相加，金额及完整研究成本仍未知。" : "Analysis is this attempt's receipt only; earlier failures, recovery, evaluation and operator work are excluded. It is not blindly added to solver capture. Monetary and complete study costs remain unknown."}</dd>
        </dl>
        <dl className="research-progress-values">
          <div><dt>{zh ? "捕获窗口跨度" : "Captured elapsed time"}</dt><dd>{usage ? duration(usage.elapsed_seconds) : "—"}</dd><small>{usage ? `${number(usage.completed_turns)} ${incomplete ? (zh ? "轮完整记录 · 末轮未完成 · 完整成本未知" : "complete Turns · final Turn incomplete · total cost unknown") : (zh ? "轮完整记录，不是全程总成本" : "complete Turns, not total study cost")}` : (zh ? "尚无完整用量捕获" : "No complete usage capture")}</small></div>
          <div><dt>{zh ? "未缓存输入" : "Uncached input"}</dt><dd>{usage ? number(usage.uncached_input_tokens) : "—"}</dd><small>{usage ? "tokens" : (zh ? "未测，不记零" : "Unmeasured, not zero")}</small></div>
          <div><dt>{zh ? "输出" : "Output"}</dt><dd>{usage ? number(usage.output_tokens) : "—"}</dd><small>{usage ? (zh ? "tokens，已含推理" : "tokens, includes reasoning") : (zh ? "未测，不记零" : "Unmeasured, not zero")}</small></div>
        </dl>
        {incomplete ? <p className="research-progress-warning">{zh
          ? "已包含末轮持久化的用量；未记录的请求和后续用量未知，不能据此判定运行已结束。"
          : "Includes persisted usage from the unfinished Turn. Unrecorded requests and later usage are unknown; this does not establish termination."}</p> : null}
        {incomplete ? <dl className="research-usage-basis"><dt>{zh ? "未完成轮已观测用量" : "Observed unfinished Turn"}</dt><dd>{incomplete.usage
          ? `${zh ? "输入" : "Input"} ${number(incomplete.usage.input_tokens)} · ${zh ? "输出" : "output"} ${number(incomplete.usage.output_tokens)} tokens`
          : (zh ? "尚无计数观测，不记零" : "No counter observation; not zero")}</dd></dl> : null}
        {usage && measured ? <><p className="research-progress-window"><time dateTime={usage.start}>{stamp(usage.start)}</time> — <time dateTime={usage.end}>{stamp(usage.end)}</time> UTC</p>
          {measured.applicability === "historical" ? <p className="research-progress-warning">{zh ? "用量来源已变化或不可读；保留历史捕获值，当前用量未核验。" : "The usage source changed or is unavailable. Historical values are retained; current usage is unverified."}</p> : null}
          <dl className="research-usage-basis"><dt>{zh ? "轮内时间合计" : "Time within Turns"}</dt><dd>{duration(usage.turn_elapsed_seconds)} · {zh ? "含工具和编排；不等于CPU、生成时间或可节省时间。" : "Includes tools and orchestration; not CPU, generation or avoidable time."}</dd>
            <dt>{zh ? "输入总量" : "Total input"}</dt><dd>{number(usage.input_tokens)} tokens · {zh ? "其中缓存" : "including cached"} {number(usage.cached_input_tokens)}</dd><dt>{zh ? "用量来源" : "Usage source"}</dt><dd>{measured.artifact}<br/><code>{measured.artifact_sha256}</code><br/>{usage.method}</dd>
            <dt>{zh ? "覆盖边界" : "Coverage limits"}</dt><dd>{usage.method === "codex_observed_session_counters_v1" ? (zh ? "仅已观测计数，含末轮已持久化用量；未记录的请求、后续用量、继承前缀、设置、评测和操作者成本未知。" : "Observed counters include persisted unfinished usage. Unrecorded requests, later usage, inherited prefixes, setup, evaluation and operator costs remain unknown.") : (zh ? "仅本次完整捕获；继承前缀、设置、原生评测、操作者及后续未完成轮不包含。金额与全研究总成本未知。" : "This complete capture only. Inherited prefixes, setup, native evaluation, operator work and later incomplete Turns are excluded. Dollar and total study costs are unknown.")}</dd>
          </dl></> : null}
      </section></details>
    </> : null}
  </section>;
}
