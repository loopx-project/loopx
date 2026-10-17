import {ArrowUpRight, ChevronDown} from "lucide-react";
import {TRAJECTORY_METRICS} from "../../../../../../loopx/control_plane/capabilities/trajectory_assessment_types.js";
import type {AssessmentCitation, AssessmentVerdict, TrajectoryAssessment, TrajectoryEpisode, TrajectoryInput, TrajectoryMetric, TrajectorySolverUsage} from "../../../../../../loopx/control_plane/capabilities/trajectory_assessment_types.js";
import type {TrajectoryNativeOutcomes} from "../../../../../../loopx/control_plane/capabilities/trajectory_native_outcomes.js";
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

export function ResearchProgress({pack: p, packs, selectedId, error, zh, onSelect, onCitation}: {pack: ResearchProgressPack | null; packs: Pick<ResearchProgressPack, "pack_id" | "title">[]; selectedId: string; error: string; zh: boolean; onSelect: (id: string) => void; onCitation: (citation: AssessmentCitation, origin?: HTMLButtonElement) => void}) {
  const measured = p?.solver_usage, usage = measured?.projection, result = p?.result;
  const native = p?.native_outcomes, outcomes = native?.projection;
  const number = (n: number) => new Intl.NumberFormat(zh ? "zh-CN" : "en", {maximumFractionDigits: 1}).format(n);
  const percent = (n: number) => new Intl.NumberFormat(zh ? "zh-CN" : "en", {style: "percent", maximumFractionDigits: 0}).format(n);
  const duration = (s: number) => `${number(s / 60)} ${zh ? "分钟" : "min"}`;
  const stamp = (s: string) => new Intl.DateTimeFormat(zh ? "zh-CN" : "en", {timeZone: "UTC", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false}).format(new Date(s));
  return <section className="research-progress" aria-label={zh ? "长程效果与用量" : "Long-horizon results and usage"}>
    <header><h3>{zh ? "研究效果判读" : "Research effectiveness"}</h3>{packs.length > 1 ? <label>{zh ? "评估范围" : "Evaluation scope"}<select value={selectedId} onChange={e => onSelect(e.target.value)}>{packs.map(pack => <option key={pack.pack_id} value={pack.pack_id}>{pack.title}</option>)}</select></label> : null}</header>
    {error ? <p className="research-progress-warning" role="alert">{zh ? "无法核对本次来源；已显示的值仅为上次读回。" : "The source could not be refreshed. Displayed values are from the last readback."} {error}</p> : null}
    {p ? <>
      <p className="research-progress-scope">{p.title} · {number(p.episode_count)} {zh ? "个固定研究片段" : "fixed episodes"} · {p.selection.exhaustive ? (zh ? "仅覆盖声明范围" : "Declared scope only") : (zh ? "目的抽样，不代表全程" : "Purposive sample, not the whole run")}</p>
      {p.applicability === "historical" ? <p className="research-progress-warning" role="status">{zh ? "历史分析：来源或方法已变化，不能当作当前判断。" : "Historical analysis: the source or method changed; these are not current judgments."}</p> : null}
      {result ? <div className="research-process-counts">
        <p>{zh ? "分析器逐项判读 · 未校准。数字为“是 / 适用片段”，未知仍在分母中；展开任一项看理由与证据。" : "Uncalibrated analyzer judgments. Counts are yes / eligible episodes, including unknowns in the denominator. Expand a metric for reasons and evidence."}</p>
        <div className="research-effect-metrics" key={p.pack_id}>
          {TRAJECTORY_METRICS.map(metric => {
            const label = metricCopy[metric], row = result.metrics.find(item => item.metric === metric);
            const assessments = result.assessments.filter(item => item.metric === metric);
            return <details className="research-effect-metric" data-metric={metric} key={metric}>
              <summary><span className="research-effect-title">{label[zh ? 0 : 1]}<ChevronDown size={13} aria-hidden/></span>
                <strong className="research-effect-number">{row?.eligible ? `${number(row.yes)} / ${number(row.eligible)}` : "—"}</strong>
                <span className="research-effect-verdict">{!row ? (zh ? "没有此指标记录" : "No metric record") : !row.eligible ? (zh ? "不适用" : "N/A") : assessments.length === 1 ? verdictLabel(metric, assessments[0].verdict, zh) : (zh ? "是 / 适用片段" : "Yes / eligible episodes")}</span>
                {row?.eligible ? <small>{zh ? "否" : "No"} {number(row.no)} · {zh ? "未知" : "Unknown"} {number(row.unknown)}</small> : null}
              </summary>
              <div className="research-effect-evidence"><p>{label[zh ? 2 : 3]}</p>
                {metric === "benefit_test" ? <p>{zh ? "“是”只表示做过收益检验，不保证收益为正；“否”不是收益为零。" : "Yes means utility was tested, not that utility improved. No is not zero utility."}</p> : null}
                {metric === "redundancy" ? <p>{zh ? "“是”表示发现无益重复，不能当作正向表现。" : "Yes means avoidable repetition was found; it is not a positive outcome."}</p> : null}
                {row?.assessed_fraction != null ? <p>{zh ? "已得到明确判读的覆盖" : "Coverage with a decisive judgment"} {percent(row.assessed_fraction)}{row.unknown && row.missing_data_bounds ? <> · {zh ? "未知分别全为否/全为是时，“是”占比" : "Yes share if unknowns are all no / all yes"} {percent(row.missing_data_bounds[0])}–{percent(row.missing_data_bounds[1])} ({zh ? "不是置信区间" : "not a confidence interval"})</> : null}</p> : null}
                {assessments.map(assessment => <article key={assessment.episode_id}>
                  <h4>{p.episodes?.find(episode => episode.id === assessment.episode_id)?.question ?? assessment.episode_id}</h4>
                  <span>{verdictLabel(metric, assessment.verdict, zh)}</span><p>{assessment.reason}</p>
                  <div className="research-effect-citations">{assessment.citations.map(c => <button type="button" key={`${c.source_id}:${c.first_line}:${c.last_line}`} onClick={event => onCitation(c, event.currentTarget)}><ArrowUpRight size={13} aria-hidden/>{c.source_id} · {zh ? "行" : "lines"} {c.first_line}–{c.last_line}</button>)}</div>
                </article>)}
                {!row?.eligible ? <p>{zh ? "所选片段没有此指标的适用判读；未记作零。" : "No eligible judgment for this metric in the selected episodes; not recorded as zero."}</p> : null}
              </div>
            </details>;
          })}
        </div>
        <p>{zh ? "六项分别判断，不合成总分、成功率或原生收益。" : "Six separate judgments, not a total score, success rate or native utility."}</p>
      </div> : <p className="research-effect-empty" role="status">{zh ? "此范围尚无分析器判读。已有用量不能替代效果；按需深度分析后在此展示。" : "No analyzer judgments exist for this scope. Usage is not effectiveness; requested deep analysis will appear here."}</p>}
      <details className="research-progress-basis"><summary>{zh ? "来源、方法与覆盖" : "Sources, method and coverage"}</summary><dl>
        <dt>{zh ? "题目 / 运行" : "Case / run"}</dt><dd>{p.scope.case_id}<br/>{p.scope.run_id}</dd><dt>{zh ? "证据窗口" : "Evidence window"}</dt><dd>{p.scope.window}</dd><dt>{zh ? "取样规则" : "Selection rule"}</dt><dd>{p.selection.rule}</dd>
        <dt>{zh ? "总体研究片段数" : "Population of episodes"}</dt><dd>{p.selection.population_episodes == null ? (zh ? "未知" : "Unknown") : number(p.selection.population_episodes)}</dd>
        {result ? <><dt>{zh ? "分析方法" : "Analysis method"}</dt><dd>{result.method_version} · {zh ? "Agent 判读未校准" : "Uncalibrated Agent judgment"}</dd><dt>{zh ? "结果标识" : "Result key"}</dt><dd><code>{result.analysis_key}</code></dd><dt>{zh ? "分析限制" : "Limitations"}</dt><dd>{result.limitations.map((line, i) => <p key={i}>{line}</p>)}</dd></> : null}
        <dt>{zh ? "来源版本" : "Source revision"}</dt><dd><code>{p.scope.revision}</code></dd>
        <dt>{zh ? "原生结果" : "Native outcomes"}</dt><dd>{outcomes ? <>
          <p>{outcomes.metric.name} · {outcomes.metric.unit} · {outcomes.metric.higher_is_better ? (zh ? "越高越好" : "higher is better") : (zh ? "越低越好" : "lower is better")}</p>
          <p>{outcomes.start} — {outcomes.end} · {outcomes.complete ? (zh ? "声明为完整前缀" : "Declared complete prefix") : (zh ? "部分窗口，起点未知" : "Partial window; initial unknown")} · {outcomes.valid_count} / {outcomes.sample_count} {zh ? "项有效结果" : "valid results"}</p>
          <dl>{([ ["initial", "首次提交", "Initial submission"], ["best_valid", "窗口内最佳有效值", "Best valid in capture"], ["retained", "原生保留结果", "Native retained result"], ["final_valid", "最后有效提交", "Final valid submission"] ] as const).map(([key, cn, en]) => {
            const point = outcomes[key];
            return <div key={key}><dt>{zh ? cn : en}</dt><dd>{point ? <>{String(point.value)} · {point.candidate_ref} <button type="button" onClick={event => onCitation(point.citation, event.currentTarget)}>{zh ? "查看原始证据" : "Read original evidence"}</button></> : (zh ? "— 未知，不记零" : "— Unknown, not zero")}</dd></div>;
          })}</dl>
          {native?.applicability === "historical" ? <p role="status">{zh ? "原生来源已变化或不可读；仅保留历史捕获，当前结果未核验。" : "Native source changed or is unavailable; retained historical capture, current results unverified."}</p> : null}
          <p>{zh ? "原生测量与分析器判断分开。有效性和保留选择来自提供方，不认证研究因果收益；用量窗口独立，不补算达标成本或全程账单。" : "Native measurements are separate from analyzer judgments. Validity and retention are provider-recorded, not causal benefit. Usage has its own window; threshold and total-study costs are not inferred."}</p>
        </> : (zh ? "— 尚无同范围的原生结果捕获；不能从分析器判断或用量补算。" : "— No native capture for this scope; analyzer judgments and usage cannot supply it.")}</dd>
      </dl></details>
      <details className="research-usage"><summary>{zh ? "用量与成本" : "Usage and cost"}{usage ? ` · ${zh ? "捕获跨度" : "Captured elapsed"} ${duration(usage.elapsed_seconds)}` : ` · ${zh ? "尚无完整用量捕获" : "No complete usage capture"}`}{measured?.applicability === "historical" ? (zh ? " · 历史捕获" : " · Historical capture") : ""}</summary>
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
          <div><dt>{zh ? "捕获窗口跨度" : "Captured elapsed time"}</dt><dd>{usage ? duration(usage.elapsed_seconds) : "—"}</dd><small>{usage ? `${number(usage.completed_turns)} ${zh ? "轮完整记录，不是全程总成本" : "complete Turns, not total study cost"}` : (zh ? "尚无完整用量捕获" : "No complete usage capture")}</small></div>
          <div><dt>{zh ? "未缓存输入" : "Uncached input"}</dt><dd>{usage ? number(usage.uncached_input_tokens) : "—"}</dd><small>{usage ? "tokens" : (zh ? "未测，不记零" : "Unmeasured, not zero")}</small></div>
          <div><dt>{zh ? "输出" : "Output"}</dt><dd>{usage ? number(usage.output_tokens) : "—"}</dd><small>{usage ? (zh ? "tokens，已含推理" : "tokens, includes reasoning") : (zh ? "未测，不记零" : "Unmeasured, not zero")}</small></div>
        </dl>
        {usage && measured ? <><p className="research-progress-window"><time dateTime={usage.start}>{stamp(usage.start)}</time> — <time dateTime={usage.end}>{stamp(usage.end)}</time> UTC</p>
          {measured.applicability === "historical" ? <p className="research-progress-warning">{zh ? "用量来源已变化或不可读；保留历史捕获值，当前用量未核验。" : "The usage source changed or is unavailable. Historical values are retained; current usage is unverified."}</p> : null}
          <dl className="research-usage-basis"><dt>{zh ? "轮内时间合计" : "Time within Turns"}</dt><dd>{duration(usage.turn_elapsed_seconds)} · {zh ? "含工具和编排；不等于CPU、生成时间或可节省时间。" : "Includes tools and orchestration; not CPU, generation or avoidable time."}</dd>
            <dt>{zh ? "输入总量" : "Total input"}</dt><dd>{number(usage.input_tokens)} tokens · {zh ? "其中缓存" : "including cached"} {number(usage.cached_input_tokens)}</dd><dt>{zh ? "用量来源" : "Usage source"}</dt><dd>{measured.artifact}<br/><code>{measured.artifact_sha256}</code><br/>{usage.method}</dd>
            <dt>{zh ? "覆盖边界" : "Coverage limits"}</dt><dd>{zh ? "仅本次完整捕获；继承前缀、设置、原生评测、操作者及后续未完成轮不包含。金额与全研究总成本未知。" : "This complete capture only. Inherited prefixes, setup, native evaluation, operator work and later incomplete Turns are excluded. Dollar and total study costs are unknown."}</dd>
          </dl></> : null}
      </details>
    </> : <p role="status">{zh ? "正在核对此范围的分析来源…" : "Checking analysis sources for this scope…"}</p>}
  </section>;
}
