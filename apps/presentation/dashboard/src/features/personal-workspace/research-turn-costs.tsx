import {useId, useState} from "react";
import type {TrajectorySolverUsage} from "../../../../../../loopx/control_plane/capabilities/trajectory_assessment_types.js";
import {downloadReport} from "./report-download";
import {exportResearchUsage, researchUsageRows, selectResearchUsage} from "./research-usage-selection";
import {ContextHelp} from "./context-help";

/** Presentation of existing Turn counters; never estimates missing usage or attributes it to control work. */
export function ResearchTurnCosts({usage, source, zh, onRead}: {usage: TrajectorySolverUsage; zh: boolean;
  source: Parameters<typeof exportResearchUsage>[3];
  onRead: (turnId: string, origin: HTMLButtonElement) => void}) {
  const id = useId();
  const [sort, setSort] = useState("input");
  const [page, setPage] = useState(0);
  const rows = researchUsageRows(usage);
  const [first, setFirst] = useState("1");
  const [last, setLast] = useState(String(rows.length));
  const [downloadFailed, setDownloadFailed] = useState(false);
  const valid = Number.isInteger(Number(first)) && Number.isInteger(Number(last))
    && Number(first) >= 1 && Number(last) >= Number(first) && Number(last) <= rows.length;
  const selection = valid ? selectResearchUsage(usage, Number(first), Number(last)) : null;
  const selectedRows = selection?.turns ?? [];
  const ordered = [...selectedRows].sort((a, b) => {
    const value = (row: typeof a) => sort === "elapsed" ? row.elapsed_seconds
      : sort === "time" ? row.ordinal : row.usage?.input_tokens ?? -1;
    return (sort === "time" ? value(a) - value(b) : value(b) - value(a)) || a.ordinal - b.ordinal;
  });
  const size = 8, pages = Math.max(1, Math.ceil(selectedRows.length / size)), current = Math.min(page, pages - 1);
  const number = (n: number | undefined | null) => n == null ? "—" : n.toLocaleString(zh ? "zh-CN" : "en");
  return <details className="research-turn-costs">
    <summary>{zh ? "定位耗时与输入最多的轮次" : "Find time and input hotspots"}</summary>
    <div className="research-turn-costs-toolbar">
      <div className="research-turn-costs-sort">
        <label htmlFor={id}>{zh ? "排序" : "Sort by"}</label>{" "}
        <select id={id} value={sort} onChange={event => {setSort(event.target.value); setPage(0);}}>
          <option value="input">{zh ? "输入最多" : "Most input"}</option>
          <option value="elapsed">{zh ? "耗时最长" : "Longest elapsed"}</option>
          <option value="time">{zh ? "时间顺序" : "Chronological"}</option>
        </select>
      </div>
      <ContextHelp label={zh ? "轮次耗时与输入说明" : "Turn elapsed time and input note"}>
        <p>{zh ? "仅比较当前捕获内的轮次。耗时含工具、等待与编排，不是可节省时间；轮次输入不等于 LoopX 控制输入。金额未采集。" : "Compares Turns within this capture only. Elapsed time includes tools, waiting and orchestration, not avoidable time. Turn input is not LoopX control input. Dollar cost is unmeasured."}</p>
      </ContextHelp>
    </div>
    {rows.length ? <fieldset className="research-usage-range">
      <legend>{zh ? "分析轮次范围" : "Analyze a Turn range"}</legend>
      <div className="research-usage-range-controls">
        <label>{zh ? "起始轮次" : "First Turn"}<input type="number" min="1" max={rows.length} step="1" value={first}
          aria-invalid={!valid} onChange={event => {setFirst(event.target.value); setPage(0); setDownloadFailed(false);}}/></label>
        <label>{zh ? "结束轮次" : "Last Turn"}<input type="number" min="1" max={rows.length} step="1" value={last}
          aria-invalid={!valid} onChange={event => {setLast(event.target.value); setPage(0); setDownloadFailed(false);}}/></label>
        <button type="button" onClick={() => {setFirst("1"); setLast(String(rows.length)); setPage(0); setDownloadFailed(false);}}>{zh ? "全部轮次" : "All Turns"}</button>
        <button type="button" disabled={!valid} onClick={() => {
          try {downloadReport("selected-turn-usage.json", source.artifact_sha256,
            exportResearchUsage(usage, Number(first), Number(last), source, new Date().toISOString())); setDownloadFailed(false);}
          catch {setDownloadFailed(true);}
        }}>{zh ? "下载所选用量" : "Download selected usage"}</button>
      </div>
      {!valid ? <p role="alert">{zh ? `请选择 1 至 ${rows.length} 轮之间的有效范围。` : `Choose a valid range between Turns 1 and ${rows.length}.`}</p> : null}
      {selection ? <div className="research-usage-range-summary" role="status">
        <p>{zh ? `已选 ${selection.selected_turns} / ${rows.length} 轮 · 完成 ${selection.completed_turns} 轮 · 无计数 ${selection.turns_without_counters} 轮`
          : `${selection.selected_turns} / ${rows.length} Turns selected · ${selection.completed_turns} completed · ${selection.turns_without_counters} without counters`}
          {source.applicability === "historical" ? (zh ? " · 历史捕获" : " · Historical capture") : ""}</p>
        <dl>
          <div><dt>{zh ? "已观测输入" : "Observed input"}</dt><dd>{number(selection.observed_input_tokens)}</dd></div>
          <div><dt>{zh ? "其中缓存" : "Cached subset"}</dt><dd>{number(selection.observed_cached_input_tokens)}</dd></div>
          <div><dt>{zh ? "未缓存" : "Uncached"}</dt><dd>{number(selection.observed_uncached_input_tokens)}</dd></div>
          <div><dt>{zh ? "已观测输出" : "Observed output"}</dt><dd>{number(selection.observed_output_tokens)}</dd></div>
          <div><dt>{zh ? "轮次耗时合计" : "Summed Turn time"}</dt><dd>{(selection.observed_turn_seconds / 60).toLocaleString(zh ? "zh-CN" : "en", {maximumFractionDigits: 1})} {zh ? "分钟" : "min"}</dd></div>
        </dl>
        <p>{zh ? "仅合计已观测计数；无计数不记零，未完成轮不代表最终用量。耗时不含轮次间空档。" : "Sums observed counters only. Missing counters are not zero; unfinished Turns are not final usage. Time excludes gaps between Turns."}</p>
      </div> : null}
      {downloadFailed ? <p role="alert">{zh ? "无法开始下载，请重试。" : "Could not start the download. Try again."}</p> : null}
    </fieldset> : null}
    {selectedRows.length ? <ol className="research-turn-cost-list">
      {ordered.slice(current * size, (current + 1) * size).map(turn => <li key={turn.turn_id}>
        <div><strong>{zh ? `第 ${turn.ordinal} 轮` : `Turn ${turn.ordinal}`}</strong>
          {turn.incomplete ? <span className="research-usage-scope">{zh ? " · 未完成（仅已观测）" : " · Incomplete (observed only)"}</span> : null}
          <time dateTime={turn.start}>{turn.start.replace("T", " ")}</time>
        </div>
        <dl>
          <div><dt>{zh ? "耗时" : "Elapsed"}</dt><dd>{(turn.elapsed_seconds / 60).toLocaleString(zh ? "zh-CN" : "en", {maximumFractionDigits: 1})} {zh ? "分钟" : "min"}</dd></div>
          <div><dt>{zh ? "输入" : "Input"}</dt><dd>{number(turn.usage?.input_tokens)}</dd></div>
          <div><dt>{zh ? "其中缓存" : "Cached subset"}</dt><dd>{number(turn.usage?.cached_input_tokens)}</dd></div>
          <div><dt>{zh ? "未缓存" : "Uncached"}</dt><dd>{number(turn.usage?.uncached_input_tokens)}</dd></div>
          <div><dt>{zh ? "输出" : "Output"}</dt><dd>{number(turn.usage?.output_tokens)}</dd></div>
        </dl>
        <button type="button" aria-label={zh ? `第 ${turn.ordinal} 轮：查看计数证据` : `Turn ${turn.ordinal}: read counter evidence`}
          onClick={event => onRead(turn.turn_id, event.currentTarget)}>{zh ? "计数证据" : "Counter evidence"}</button>
      </li>)}
    </ol> : rows.length ? null : <p>{zh ? "此捕获没有可读轮次。" : "No captured Turns are available."}</p>}
    {pages > 1 ? <nav aria-label={zh ? "轮次分页" : "Turn pages"}>
      <button type="button" disabled={current === 0} onClick={() => setPage(current - 1)}>{zh ? "上一页" : "Previous"}</button>
      <span aria-live="polite">{current + 1} / {pages}</span>
      <button type="button" disabled={current + 1 === pages} onClick={() => setPage(current + 1)}>{zh ? "下一页" : "Next"}</button>
    </nav> : null}
  </details>;
}
