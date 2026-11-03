import {useId, useState} from "react";
import type {TrajectorySolverUsage} from "../../../../../../loopx/control_plane/capabilities/trajectory_assessment_types.js";
import {ContextHelp} from "./context-help";

/** Presentation of existing Turn counters; never estimates missing usage or attributes it to control work. */
export function ResearchTurnCosts({usage, zh, onRead}: {usage: TrajectorySolverUsage; zh: boolean;
  onRead: (turnId: string, origin: HTMLButtonElement) => void}) {
  const id = useId();
  const [sort, setSort] = useState("input");
  const [page, setPage] = useState(0);
  const rows: Array<Omit<TrajectorySolverUsage["turns"][number], "usage"> & {usage: TrajectorySolverUsage["turns"][number]["usage"] | null; ordinal: number; incomplete: boolean}> = usage.turns.map((turn, index) => ({...turn, ordinal: index + 1, incomplete: false}));
  const tail = usage.incomplete_turn;
  if (tail) rows.push({...tail, end: tail.observed_through,
    elapsed_seconds: Math.max(0, (Date.parse(tail.observed_through) - Date.parse(tail.start)) / 1000),
    ordinal: rows.length + 1, incomplete: true, usage: tail.usage});
  const ordered = [...rows].sort((a, b) => {
    const value = (row: typeof a) => sort === "elapsed" ? row.elapsed_seconds
      : sort === "time" ? row.ordinal : row.usage?.input_tokens ?? -1;
    return (sort === "time" ? value(a) - value(b) : value(b) - value(a)) || a.ordinal - b.ordinal;
  });
  const size = 8, pages = Math.max(1, Math.ceil(rows.length / size)), current = Math.min(page, pages - 1);
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
    {rows.length ? <ol className="research-turn-cost-list">
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
    </ol> : <p>{zh ? "此捕获没有可读轮次。" : "No captured Turns are available."}</p>}
    {pages > 1 ? <nav aria-label={zh ? "轮次分页" : "Turn pages"}>
      <button type="button" disabled={current === 0} onClick={() => setPage(current - 1)}>{zh ? "上一页" : "Previous"}</button>
      <span aria-live="polite">{current + 1} / {pages}</span>
      <button type="button" disabled={current + 1 === pages} onClick={() => setPage(current + 1)}>{zh ? "下一页" : "Next"}</button>
    </nav> : null}
  </details>;
}
