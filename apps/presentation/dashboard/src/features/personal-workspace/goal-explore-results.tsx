import {useEffect, useRef, useState} from "react";
import {RefreshCw} from "lucide-react";
import {fetchExploreResults, type ExploreResultPage} from "../../data/chat";
import {parseExploreFindingSections} from "../../../../../../loopx/control_plane/presentation/explore_timeline.js";
import {ExploreMeasurements} from "./explore-measurements";
import {ContextHelp} from "./context-help";
import "./goal-explore-results.css";

/** Saved evidence remains readable after Explore is disabled. No execution here. */
export function GoalExploreResults({goalId, zh, active = true, readSourceUrl}: {goalId: string; zh: boolean; active?: boolean; readSourceUrl?: string}) {
  const [page, setPage] = useState<ExploreResultPage | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [cursor, setCursor] = useState<string>();
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState(false);
  const reader = useRef<HTMLDivElement>(null);
  const loadedQuery = useRef("");
  useEffect(() => {
    if (!active) return;
    const queryKey = JSON.stringify([goalId, readSourceUrl, cursor, revision]);
    if (loadedQuery.current === queryKey) return;
    let current = true;
    setBusy(true); setError(false); setPage(null);
    void fetchExploreResults(goalId, cursor, readSourceUrl).then(next => {
      if (!current) return;
      loadedQuery.current = queryKey;
      setPage(next); setSelectedId(id => next.items.some(item => item.finding_id === id) ? id : next.items[0]?.finding_id ?? "");
    }, () => {if (current) setError(true);}).finally(() => {if (current) setBusy(false);});
    return () => {current = false;};
  }, [goalId, cursor, revision, active, readSourceUrl]);
  const selected = page?.items.find(item => item.finding_id === selectedId);
  const sections = selected ? parseExploreFindingSections(selected.summary) : null;
  const statuses: Record<string, string> = zh
    ? {tentative: "待检查", confirmed: "来源记为确认", refuted: "已反驳", open: "未完成", done: "已完成", blocked: "受阻", deferred: "已延后"}
    : {tentative: "Tentative", confirmed: "Recorded confirmation", refuted: "Refuted", open: "Open", done: "Done", blocked: "Blocked", deferred: "Deferred"};
  const outcomes: Record<string, string> = zh
    ? {replan: "转向", continue: "继续", stop: "停止", wait: "等待", no_change: "无变化", ask_human: "请人决定"}
    : {replan: "Replan", continue: "Continue", stop: "Stop", wait: "Wait", no_change: "No change", ask_human: "Ask human"};
  const time = (value: string) => Number.isFinite(Date.parse(value))
    ? new Intl.DateTimeFormat(zh ? "zh-CN" : "en", {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"}).format(new Date(value)) : value;
  const selectFinding = (id: string, focusReader = false) => {
    setSelectedId(id);
    if (focusReader) window.requestAnimationFrame(() => {reader.current?.focus({preventScroll: true}); reader.current?.scrollIntoView({block: "nearest"});});
  };
  return <section className="goal-team-results goal-explore-results" aria-label={zh ? "Explore 证据" : "Explore evidence"} aria-busy={busy}>
    <header><div className="explore-reader-heading"><h3>{zh ? "已保存的结论" : "Saved findings"}</h3>
      <ContextHelp label={zh ? "结论与关联说明" : "About findings and links"}>
        <p>{zh ? "来源记录的观察与结论，保存不代表已经验证或被后续决策采用。关联任务是原记录的显式关联。" : "Source-recorded observations and conclusions. Saving does not establish validation or adoption. Linked tasks are explicit source relationships."}</p>
      </ContextHelp></div>
      <button type="button" disabled={busy} aria-label={zh ? "刷新证据" : "Refresh evidence"} onClick={() => setRevision(value => value + 1)}><RefreshCw size={16} aria-hidden/></button></header>
    {busy ? <p role="status">{zh ? "正在读取证据…" : "Reading evidence…"}</p> : null}
    {error ? <p role="alert">{zh ? "无法核验此页证据；旧内容已清除。请刷新重试。" : "Cannot verify this evidence page; previous content was cleared. Refresh to retry."}</p> : null}
    {page && !page.items.length ? <p>{zh ? "尚无已保存的结论。" : "No saved findings yet."}</p> : null}
    {page && page.items.length > 0 ? <div className="goal-team-results-layout">
      <div className="explore-finding-navigation">
        <div className="explore-finding-picker">
          <select aria-label={zh ? "选择结论" : "Choose a finding"} value={selectedId} onChange={event => selectFinding(event.target.value)}>
            {page.items.map((item, index) => <option key={item.finding_id} value={item.finding_id}>{index + 1}. {item.finding} · {statuses[item.status] ?? item.status} · {time(item.last_updated_at)}</option>)}
          </select>
        </div>
        <nav className="goal-team-result-list" aria-label={zh ? "选择结论" : "Choose a finding"}>
        {page.items.map(item => <button type="button" key={item.finding_id} aria-pressed={selectedId === item.finding_id}
          onClick={() => selectFinding(item.finding_id, true)}>
          <span><strong>{item.finding}</strong><small>{statuses[item.status] ?? item.status} · <time dateTime={item.last_updated_at} title={item.last_updated_at}>{time(item.last_updated_at)}</time></small></span>
        </button>)}
        </nav>
        <nav className="explore-finding-pagination" aria-label={zh ? "结论分页" : "Finding pages"}>
          <span>{zh ? `本页 ${page.items.length} / 共 ${page.total} 条` : `${page.items.length} on page / ${page.total} total`}</span>
          <div>{cursor ? <button type="button" onClick={() => setCursor(undefined)}>{zh ? "回到首页" : "First page"}</button> : null}
          {page.next_cursor ? <button type="button" onClick={() => setCursor(page.next_cursor!)}>{zh ? "下一页" : "Next page"}</button> : null}</div>
        </nav>
      </div>
      {selected ? <div ref={reader} tabIndex={-1} className="goal-team-result-reader" key={selected.finding_id}>
        <h4>{selected.question || selected.finding}</h4>
        <span className="explore-reader-status">{statuses[selected.status] ?? selected.status} · <time dateTime={selected.last_updated_at}>{time(selected.last_updated_at)}</time></span>
        {selected.measurements ? <ExploreMeasurements measurements={selected.measurements} zh={zh}/> : null}
        {sections ? <div className="explore-finding-prose">
          <section><h5>{zh ? "观察" : "Observation"}</h5><p>{sections.observation}</p></section>
          {sections.interpretation && !sections.decision ? <section><h5>{zh ? "解读" : "Interpretation"}</h5><p>{sections.interpretation}</p></section> : null}
          {sections.decision ? <section><h5>{zh ? "决策" : "Decision"} · {outcomes[sections.decision.outcome] ?? sections.decision.outcome}</h5>
            {(["changed", "stopped", "retained"] as const).map(key => sections.decision![key].length ? <div key={key}>
              <h6>{({changed: zh ? "变化" : "Changed", stopped: zh ? "停止" : "Stopped", retained: zh ? "保留" : "Retained"})[key]}</h6>
              <ul>{sections.decision![key].map((text, index) => <li key={index}>{text}</li>)}</ul>
            </div> : null)}</section> : null}
        </div> : <p className="explore-finding-raw">{selected.summary}</p>}
        <details className="explore-reader-details"><summary>{zh ? "来源与范围" : "Sources and scope"}</summary>
          <dl><dt>{zh ? "记录时间" : "Recorded at"}</dt><dd><time dateTime={selected.last_updated_at}>{selected.last_updated_at}</time></dd>
            <dt>{zh ? "记录者" : "Recorded by"}</dt><dd>{selected.agent_id || "—"}</dd>
            {selected.scope ? <><dt>{zh ? "问题范围" : "Question scope"}</dt><dd>{selected.scope}</dd></> : null}
            {sections ? <><dt>{zh ? "适用范围" : "Applicability"}</dt><dd>{sections.applicability}</dd><dt>{zh ? "输入版本" : "Input revision"}</dt><dd>{sections.inputRevision}</dd></> : null}
            <dt>{zh ? "来源引用" : "Source references"}</dt><dd>{selected.evidence_refs.length ? <ul>{selected.evidence_refs.map((ref, index) => <li key={index}><code>{ref}</code></li>)}</ul> : "—"}</dd>
            <dt>{zh ? "记录标识" : "Record identifiers"}</dt><dd><code>{selected.finding_id} · {selected.node_id}</code></dd></dl>
          {sections ? <details><summary>{zh ? "完整原文" : "Full original text"}</summary><p className="explore-finding-raw">{selected.summary}</p></details> : null}
        </details>
        <details className="explore-reader-details"><summary>{zh ? "关联任务" : "Linked tasks"} · {selected.linked_todos_available === false ? "—" : selected.linked_todos.length}</summary>
          {selected.linked_todos_available === false ? <p>{zh ? "此来源暂不提供完整的任务关联读取。" : "Complete task associations are not available from this source."}</p> : selected.linked_todos.length ? <ul className="explore-linked-tasks">{selected.linked_todos.map(todo => <li key={todo.todo_id}>
            <details><summary><span>{todo.text}</span><small>{statuses[todo.status] ?? todo.status} · {todo.claimed_by || "—"}</small></summary>
              <p>{todo.text}</p><code>{todo.todo_id}</code></details>
          </li>)}</ul> : <p>{zh ? "当前及已完成任务中未找到关联。" : "No links found in current or completed tasks."}</p>}
        </details>
      </div> : null}
    </div> : null}
  </section>;
}
