import {useEffect, useRef, useState} from "react";
import {CircleHelp, TriangleAlert} from "lucide-react";
import type {TrajectorySolverUsage} from "../../../../../../loopx/control_plane/capabilities/trajectory_assessment_types.js";
import {trajectoryUsageComparisonReason, type TrajectoryUsageCapture, type TrajectoryUsageComparison} from "../../../../../../loopx/control_plane/capabilities/trajectory_usage_comparison.js";
import {ContextHelp} from "./context-help";

const count = (n: number | null, zh: boolean) => n === null ? "—" : n.toLocaleString(zh ? "zh-CN" : "en");
const percent = (n: number | null, zh: boolean) => n === null ? "—"
  : n > 0 && n < 0.001 ? "<0.1%" : n < 1 && n > 0.999 ? ">99.9%"
    : new Intl.NumberFormat(zh ? "zh-CN" : "en", {style: "percent", maximumFractionDigits: 1}).format(n);

export function LoopXRequestFootprint({usage, zh}: {usage: TrajectorySolverUsage; zh: boolean}) {
  const v = usage.loopx_requests;
  if (!v) return null; // Historical projections keep their missing observation.
  return <section className="research-request-coverage" aria-label={zh ? "LoopX 命令请求" : "Requests issuing LoopX commands"}>
    <div className="research-usage-heading"><h4>{zh ? "LoopX 命令请求 · 整次用量" : "LoopX command requests · whole-request usage"}</h4>
      <ContextHelp icon={v.status === "unavailable" ? TriangleAlert : CircleHelp} label={zh ? "整次请求与控制文本的区别" : "Whole requests versus control text"}>
        <p>{zh ? "按调用、原生响应回执和唯一返回的记录顺序，保守关联发出简单 LoopX 命令的请求。数值包含该请求读取的任务历史；与控制文本输入归因、工具返回字节是重叠视角，不能相加。" : "Conservative association by ordered call, native response receipt and unique return identifies requests issuing simple LoopX commands. Their counters include task history. This overlaps control-text attribution and tool-return bytes; never add these views."}</p>
        <p>{zh ? "只识别已记录的简单调用；复合脚本及未匹配返回保留未归类。同次请求出现其他调用时标为混合调用；其余也不证明纯控制。未知来源不等于没有控制，整次用量不等于可省成本。模型耗时和实扣费用未采集。" : "Only recorded simple calls are recognized; compound scripts and unmatched returns remain unclassified. A request with other calls is marked mixed; the remainder does not establish pure control. Unknown sources do not mean no control, and whole-request usage is not avoidable cost. Model latency and actual charges were not captured."}</p>
      </ContextHelp></div>
    {v.status === "unavailable" ? <p role="status">{zh ? "请求关联不可用 · 用量未知" : "Request association unavailable · usage unknown"}</p> : <>
      <p className="research-input-share-value"><strong>{percent(v.input_fraction, zh)}</strong><span>{v.recognized_requests} / {v.total_requests} {zh ? "次请求已识别 · 归属未全" : "requests recognized · incomplete attribution"}</span></p>
      <dl className="research-request-values">
        <div><dt>{zh ? "整次输入（含缓存）" : "Whole input (includes cache)"}</dt><dd>{count(v.tokens.input_tokens, zh)} tokens</dd></div>
        <div><dt>{zh ? "其中未缓存" : "Of which uncached"}</dt><dd>{count(v.tokens.input_tokens - v.tokens.cached_input_tokens, zh)} tokens</dd></div>
        <div><dt>{zh ? "整次输出（含推理）" : "Whole output (includes reasoning)"}</dt><dd>{count(v.tokens.output_tokens, zh)} tokens</dd></div>
      </dl>
      <details className="research-usage-detail"><summary>{zh ? "请求关联明细" : "Request association details"}</summary>
        <dl className="research-usage-basis"><dt>{zh ? "方法" : "Method"}</dt><dd>{v.method}</dd>
          <dt>{zh ? "含其他调用的请求" : "Requests with other calls"}</dt><dd>{v.mixed_call_requests}</dd>
          <dt>{zh ? "未分类 / 未匹配调用" : "Unclassified / unmatched calls"}</dt><dd>{v.unclassified_calls} / {v.unmatched_calls}</dd>
          <dt>{zh ? "模型耗时 / 实扣费用" : "Model latency / actual charges"}</dt><dd>— / —</dd></dl>
        <ol className="research-request-ledger">{v.requests.map(r => <li key={r.response_id}>
          <details><summary><span>{r.operations.join(" · ")}</span><span>{count(r.tokens.input_tokens, zh)} tokens</span></summary>
            <dl className="research-usage-basis"><dt>{zh ? "原生响应" : "Native response"}</dt><dd>{r.response_id}</dd>
              <dt>Turn</dt><dd>{r.turn_id}</dd><dt>{zh ? "回执行 / 调用行" : "Receipt line / call lines"}</dt><dd>{r.line} / {r.call_lines.join(", ")}</dd>
              <dt>{zh ? "缓存 / 输出 / 推理子项" : "Cached / output / reasoning subset"}</dt><dd>{count(r.tokens.cached_input_tokens, zh)} / {count(r.tokens.output_tokens, zh)} / {count(r.tokens.reasoning_output_tokens, zh)}</dd>
              <dt>{zh ? "其他调用" : "Other calls"}</dt><dd>{r.other_calls}</dd></dl>
          </details></li>)}</ol>
      </details>
    </>}
  </section>;
}

export type ReadUsageComparison = (packId: string, referenceId: string, signal: AbortSignal) => Promise<TrajectoryUsageComparison>;
export function ResearchUsageComparison({pack, packs, zh, onRead}: {pack: TrajectoryUsageCapture;
  packs: TrajectoryUsageCapture[]; zh: boolean; onRead?: ReadUsageComparison}) {
  // The existing source-scoped catalog supplies candidates. Titles never select
  // arms or establish scientific comparability; the toolkit validates scope.
  const others = packs.filter(p => p.pack_id !== pack.pack_id && p.scope?.goal_id === pack.scope?.goal_id);
  const valid = (id: string | null) => others.some(p => p.pack_id === id) ? id! : "";
  const initial = () => valid(typeof window === "undefined" ? null : new URLSearchParams(window.location.search).get("research_compare"))
    || others.find(p => !trajectoryUsageComparisonReason(pack, p))?.pack_id || others[0]?.pack_id || "";
  const [selected, setSelected] = useState(initial);
  const [open, setOpen] = useState(false), [revision, setRevision] = useState(0);
  const abort = useRef<AbortController | null>(null);
  const summary = useRef<HTMLElement | null>(null);
  const reference = others.find(p => p.pack_id === selected) ?? others[0];
  const readKey = JSON.stringify([pack.pack_id, pack.scope, pack.solver_usage?.artifact_sha256,
    pack.solver_usage?.applicability, reference?.pack_id, reference?.scope,
    reference?.solver_usage?.artifact_sha256, reference?.solver_usage?.applicability]);
  const [read, setRead] = useState<{key: string; status: "loading" | "ready" | "error" | "cancelled"; value?: TrajectoryUsageComparison}>({key: "", status: "loading"});
  const readSelection = useRef(initial);
  readSelection.current = initial;
  useEffect(() => {
    // Other route listeners can synchronously render during this same event.
    // Keep one listener so their render cannot remove it before dispatch reaches it.
    const read = () => setSelected(readSelection.current());
    window.addEventListener("popstate", read);
    return () => window.removeEventListener("popstate", read);
  }, []);
  useEffect(() => {
    if (!open || !onRead || !reference) return;
    const controller = new AbortController(); abort.current = controller;
    setRead({key: readKey, status: "loading"});
    void onRead(pack.pack_id, reference.pack_id, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      if (value.status === "observed" && (value.left.pack_id !== pack.pack_id || value.right.pack_id !== reference.pack_id
        || value.left.source.sha256 !== pack.solver_usage?.artifact_sha256 || value.right.source.sha256 !== reference.solver_usage?.artifact_sha256)) {
        throw new Error("Comparison source changed");
      }
      setRead({key: readKey, status: "ready", value});
    }).catch(() => {if (!controller.signal.aborted) setRead({key: readKey, status: "error"});});
    return () => {controller.abort(); if (abort.current === controller) abort.current = null;};
  }, [open, onRead, readKey, revision]);
  if (!others.length || !pack.solver_usage || !reference || !onRead) return null;
  const state = read.key === readKey ? read : {key: readKey, status: "loading" as const};
  const comparison = state.value;
  const choose = (id: string) => {
    const url = new URL(window.location.href); url.searchParams.set("research_compare", id);
    window.history.pushState({}, "", url); setSelected(id);
  };
  const signed = (n: number | null) => n === null ? "—" : new Intl.NumberFormat(zh ? "zh-CN" : "en", {signDisplay: "exceptZero", maximumFractionDigits: 2}).format(n);
  return <details className="research-usage-comparison" onToggle={e => {if (e.target === e.currentTarget) setOpen(e.currentTarget.open);}}><summary ref={summary}>{zh ? "对照捕获用量" : "Compare captured usage"}</summary>
    <div className="research-usage-heading"><label>{zh ? "对照范围" : "Reference capture"}<select aria-label={zh ? "对照范围" : "Reference capture"} value={reference.pack_id} onChange={e => choose(e.target.value)}>{others.map(p => <option key={p.pack_id} value={p.pack_id}>{p.title}</option>)}</select></label>
      <ContextHelp label={zh ? "描述性用量对照 · 不代表因果收益" : "Descriptive usage comparison · no causal claim"}>
        <p>{zh ? "仅对照同一 Goal、案例、来源修订、声明窗口和用量方法的独立捕获；绝对时刻、原 run/session、来源和覆盖仍分别保留。这不核准 study/arm、模型、反馈和预算相同，不是匹配实验资格或因果收益。差值为当前减对照。" : "Compares independent captures with the same Goal, case, source revision, declared window and usage method. Absolute times, original run/session, sources and coverage remain separate. This does not qualify study/arm, model, feedback or budget equality, matched experiments or causality. Deltas are current minus reference."}</p>
        <p>{zh ? "控制文本占比与发出 LoopX 命令的整次请求用量分别读取；两者重叠，不能相加。占比使用原生请求总和作分母，不平均百分比，不把缓存重复加进输入。归因不全、方法不同或零分母时差值保持未知。比例降低也不代表绝对用量降低。" : "Control-text share and whole requests issuing LoopX commands are separate, overlapping views. Shares use native request sums, never mean percentages or cached input added again. Partial attribution, changed methods or a zero denominator keep deltas unknown. A lower share does not establish lower absolute usage."}</p>
      </ContextHelp></div>
    {state.status === "loading" ? <p role="status">{zh ? "正在读取对照…" : "Reading comparison…"} <button type="button" onClick={() => {abort.current?.abort(); setRead({key: readKey, status: "cancelled"}); summary.current?.focus({preventScroll: true});}}>{zh ? "取消读取" : "Cancel read"}</button></p>
      : state.status === "error" || state.status === "cancelled" ? <p role={state.status === "error" ? "alert" : "status"}>{state.status === "error" ? (zh ? "对照无法读取 · 差值未知" : "Comparison unavailable · deltas unknown") : (zh ? "已取消读取对照" : "Comparison read cancelled")} <button type="button" onClick={() => setRevision(v => v + 1)}>{zh ? "重试读取" : "Retry reading"}</button></p>
      : comparison?.status === "unavailable" ? <p role="status">{zh ? "范围、来源或方法无法对齐 · 差值未知" : "Scope, source or method cannot be aligned · deltas unknown"}</p> : comparison?.status === "observed" ? <>
      {comparison.input_share_down_absolute_up || comparison.command_share_down_absolute_up ? <p role="status">{zh ? "占比降低，但对应绝对输入增加；不能据此称为节省。" : "The share fell while its absolute input rose; this does not establish savings."}</p> : null}
      <div className="research-comparison-table"><table><caption>{pack.scope!.case_id} · tokens · {zh ? "描述性对照 · 费用未测" : "Descriptive comparison · charges unmeasured"}</caption><thead><tr><th>{zh ? "计量" : "Measure"}</th><th>{pack.title}</th><th>{reference.title}</th><th>{zh ? "差值" : "Delta"}</th></tr></thead>
        <tbody>{[
          [zh ? "全部请求输入（含缓存）" : "All-request input (includes cache)", comparison.left.total.input, comparison.right.total.input, comparison.delta.total_input],
          [zh ? "其中缓存" : "Of which cached", comparison.left.total.cached, comparison.right.total.cached, comparison.delta.cached],
          [zh ? "其中未缓存" : "Of which uncached", comparison.left.total.uncached, comparison.right.total.uncached, comparison.delta.uncached],
          [zh ? "全部输出（含推理）" : "All output (includes reasoning)", comparison.left.total.output, comparison.right.total.output, comparison.delta.output],
          [zh ? "已归因控制文本输入" : "Attributed control-text input", comparison.left.input.tokens, comparison.right.input.tokens, comparison.delta.input_tokens],
          [zh ? "LoopX 命令请求 · 整次输入" : "LoopX command requests · whole input", comparison.left.commands.input, comparison.right.commands.input, comparison.delta.command_input],
          [zh ? "LoopX 命令请求 · 未缓存" : "LoopX command requests · uncached", comparison.left.commands.uncached, comparison.right.commands.uncached, null],
          [zh ? "LoopX 命令请求 · 输出" : "LoopX command requests · output", comparison.left.commands.output, comparison.right.commands.output, null],
        ].map(([label, left, right, delta]) => <tr key={label as string}><th>{label}</th><td>{count(left as number | null, zh)}</td><td>{count(right as number | null, zh)}</td><td>{signed(delta as number | null)}</td></tr>)}
          <tr><th>{zh ? "控制文本占输入" : "Control text / input"}</th><td>{percent(comparison.left.input.fraction, zh)}</td><td>{percent(comparison.right.input.fraction, zh)}</td><td>{signed(comparison.delta.input_percentage_points)} pp</td></tr>
          <tr><th>{zh ? "控制文本归因 / 总请求" : "Text-attributed / total requests"}</th><td>{comparison.left.input.attributed_requests} / {comparison.left.input.total_requests}</td><td>{comparison.right.input.attributed_requests} / {comparison.right.input.total_requests}</td><td>—</td></tr>
          <tr><th>{zh ? "LoopX 命令请求占输入" : "LoopX command requests / input"}</th><td>{percent(comparison.left.commands.fraction, zh)}</td><td>{percent(comparison.right.commands.fraction, zh)}</td><td>{signed(comparison.delta.command_percentage_points)} pp</td></tr>
        </tbody></table></div>
      <div className="research-comparison-sources">{[comparison.left, comparison.right].map(v => <details key={v.pack_id}><summary>{v.title} · {zh ? "覆盖与来源" : "Coverage and source"}</summary><dl className="research-usage-basis">
        <dt>{zh ? "控制文本归因 / 未归因请求" : "Text-attributed / unattributed requests"}</dt><dd>{v.input.attributed_requests} / {v.input.unattributed_requests} · {v.input.total_requests} {zh ? "次总请求" : "total requests"}</dd>
        <dt>{zh ? "命令请求 / 混合调用请求" : "Command requests / mixed-call requests"}</dt><dd>{count(v.commands.recognized_requests, zh)} / {count(v.commands.mixed_call_requests, zh)}</dd>
        <dt>{zh ? "未分类 / 未匹配调用" : "Unclassified / unmatched calls"}</dt><dd>{count(v.commands.unclassified_calls, zh)} / {count(v.commands.unmatched_calls, zh)}</dd>
        <dt>{zh ? "输入归因方法" : "Input attribution method"}</dt><dd>{v.input.method ?? "—"} · {v.input.status}</dd>
        <dt>{zh ? "模型耗时 / 实扣费用" : "Model latency / actual charges"}</dt><dd>— / —</dd>
        <dt>Goal / run</dt><dd>{v.source.scope.goal_id}<br/>{v.source.scope.run_id}</dd>
        <dt>{zh ? "修订 / 声明窗口" : "Revision / declared window"}</dt><dd>{v.source.scope.revision}<br/>{v.source.scope.window}</dd>
        <dt>{zh ? "实际捕获窗口" : "Actual capture window"}</dt><dd>{v.source.start} — {v.source.end}</dd>
        <dt>{zh ? "方法 / Session" : "Method / session"}</dt><dd>{v.source.request_method}<br/>{v.source.command_method ?? "—"}<br/>{v.source.session_id}</dd>
        <dt>{zh ? "来源" : "Source"}</dt><dd>{v.source.artifact}<br/>{v.source.sha256}<br/>{v.pack_id}</dd>
      </dl></details>)}</div>
    </> : null}
  </details>;
}
