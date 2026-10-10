import {useEffect, useRef, useState} from "react";
import {RefreshCw} from "lucide-react";
import {fetchExploreResults, type ExploreResultPage} from "../../data/chat";

/** Read-only saved evidence, including history when Explore has been disabled. */
export function GoalExploreResults({goalId, zh}: {goalId: string; zh: boolean}) {
  const [page, setPage] = useState<ExploreResultPage | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const generation = useRef(0);
  const reader = useRef<HTMLDivElement>(null);
  useEffect(() => {
    void load();
    return () => {generation.current++;};
  }, [goalId]);
  async function load(cursor?: string) {
    const current = ++generation.current;
    setBusy(true); setError(false); setPage(null); setSelectedId("");
    try {
      const next = await fetchExploreResults(goalId, cursor);
      if (current !== generation.current) return;
      setPage(next); setSelectedId(next.items[0]?.finding_id ?? "");
    } catch {
      if (current === generation.current) setError(true);
    } finally {if (current === generation.current) setBusy(false);}
  }
  const selected = page?.items.find(item => item.finding_id === selectedId);
  return <section className="goal-team-results goal-explore-results" aria-label={zh ? "Explore 证据" : "Explore evidence"} aria-busy={busy}>
    <header><div><h3>{zh ? "Explore 证据" : "Explore evidence"}</h3>
      <p>{zh ? "已保存的观察与结论，包括历史记录。保存不代表已验证或被后续决策采用。" : "Saved observations and conclusions, including history. Saving does not establish validation or adoption by later decisions."}</p></div>
      <button style={{flexShrink: 0, whiteSpace: "nowrap"}} type="button" disabled={busy} onClick={() => void load()}><RefreshCw size={14} aria-hidden="true"/>{zh ? "刷新" : "Refresh"}</button></header>
    {busy ? <p role="status">{zh ? "正在读取证据…" : "Reading evidence…"}</p> : null}
    {error ? <p role="alert">{zh ? "无法核验此页证据；旧内容已清除。请刷新重试。" : "Cannot verify this evidence page; previous content was cleared. Refresh to retry."}</p> : null}
    {page && !page.items.length ? <p>{zh ? "尚无已保存的结论。" : "No saved findings yet."}</p> : null}
    {page && page.items.length > 0 ? <div className="goal-team-results-layout">
      <nav className="goal-team-result-list" aria-label={zh ? "选择结论" : "Choose a finding"}>
        {page.items.map(item => <button type="button" key={item.finding_id} aria-pressed={selectedId === item.finding_id}
          onClick={() => {setSelectedId(item.finding_id); window.requestAnimationFrame(() => reader.current?.focus());}}>
          <span><strong>{item.finding}</strong><small>{item.status} · {item.last_updated_at}</small></span>
        </button>)}
        {page.next_cursor ? <button type="button" onClick={() => void load(page.next_cursor!)}>{zh ? "下一页" : "Next page"}</button> : null}
      </nav>
      {selected ? <div ref={reader} tabIndex={-1} className="goal-team-result-reader" style={{overflowWrap: "anywhere"}}>
        <h4>{selected.question || selected.finding}</h4>
        {selected.scope ? <p>{zh ? "问题范围" : "Question scope"}: {selected.scope}</p> : null}
        <p style={{whiteSpace: "pre-wrap", overflowWrap: "anywhere"}}>{selected.summary}</p>
        <p>{zh ? "记录状态" : "Recorded status"}: {selected.status}</p>
        <p>{zh ? "记录者" : "Recorded by"}: {selected.agent_id || "—"}</p>
        <p>{zh ? "来源引用" : "Source references"}: {selected.evidence_refs.length ? selected.evidence_refs.join(" · ") : "—"}</p>
        <h4>{zh ? "关联任务" : "Linked tasks"}</h4>
        <p>{zh ? "来自当前及已完成任务的显式关联，不代表后续决策已采用此结论。" : "Explicit links from current and completed tasks; they do not establish adoption by later decisions."}</p>
        {selected.linked_todos.length ? <ul>{selected.linked_todos.map(todo => <li key={todo.todo_id}>
          <p>{todo.text}</p><small>{todo.status} · {todo.claimed_by || "—"}</small>
        </li>)}</ul> : <p>{zh ? "当前及已完成任务中未找到关联。" : "No links found in current or completed tasks."}</p>}
        <details><summary>{zh ? "记录标识" : "Record identifiers"}</summary>
          <small>{selected.finding_id} · {selected.node_id}<br/>{selected.linked_todos.map(todo => todo.todo_id).join(" · ")}</small>
        </details>
      </div> : null}
    </div> : null}
  </section>;
}
