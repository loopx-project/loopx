import {useEffect, useRef, useState} from "react";
import {FileText, RefreshCw} from "lucide-react";
import {
  fetchManagedGoalResults, readManagedGoalResult,
  type ManagedGoalResultPage, type ManagedGoalResultRow, type ManagedGoalResultRead,
} from "../../data/chat";
import {TeamArtifactReport, managedReportArtifact} from "./team-artifact-content";

/** Goal-scoped local reports; an inventory row never stands in for exact acceptance readback. */
export function GoalManagedResults({goalId, zh}: {goalId: string; zh: boolean}) {
  const [page, setPage] = useState<ManagedGoalResultPage | null>(null);
  const [selected, setSelected] = useState<{row: ManagedGoalResultRow; read: ManagedGoalResultRead} | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const chosen = useRef<{todoId: string; sha256: string} | null>(null);
  const reader = useRef<HTMLDivElement>(null);

  useEffect(() => {
    chosen.current = null;
    void load();
    return () => {generation.current++;};
  }, [goalId]);

  async function read(row: ManagedGoalResultRow, current: number, focus = false) {
    const result = await readManagedGoalResult(goalId, row.todo_id);
    if (current !== generation.current) return;
    if (result.todo_id !== row.todo_id || result.goal_id !== goalId ||
        result.result.sha256 !== row.sha256 || result.result.producer_agent_id !== row.producer_agent_id) {
      throw new Error(zh ? "报告版本或验收已变化" : "Report version or acceptance changed");
    }
    chosen.current = {todoId: row.todo_id, sha256: row.sha256};
    setSelected({row, read: result});
    if (focus) window.requestAnimationFrame(() => reader.current?.focus());
  }

  async function load(cursor?: string) {
    const current = ++generation.current;
    if (cursor) chosen.current = null;
    setBusy(true); setError(""); setSelected(null);
    try {
      const next = await fetchManagedGoalResults(goalId, cursor);
      if (current !== generation.current) return;
      if (!Array.isArray(next.items) || !Number.isInteger(next.total) ||
          !Number.isInteger(next.unavailable_count) ||
          !Array.isArray(next.unavailable_todo_ids) ||
          next.unavailable_count !== next.unavailable_todo_ids.length ||
          (next.next_cursor !== null && typeof next.next_cursor !== "string")) {
        throw new Error(zh ? "报告列表响应不完整" : "Report inventory response is incomplete");
      }
      setPage(next);
      const previous = chosen.current;
      const row = previous
        ? next.items.find(item => item.todo_id === previous.todoId && item.sha256 === previous.sha256)
        : next.items[0];
      if (previous && !row) {
        setError(zh ? "上次报告已不在当前验收结果中。" : "The previous report is no longer in current accepted results.");
      } else if (row) {
        await read(row, current);
      }
    } catch (failure) {
      if (current === generation.current) {
        setPage(null);
        setError(`${zh ? "无法核验报告；旧内容已清除。" : "Cannot verify report; previous content was cleared."} ${String(failure)}`);
      }
    } finally {
      if (current === generation.current) setBusy(false);
    }
  }

  async function select(row: ManagedGoalResultRow) {
    const current = ++generation.current;
    chosen.current = {todoId: row.todo_id, sha256: row.sha256};
    setBusy(true); setError(""); setSelected(null);
    try {await read(row, current, true);}
    catch (failure) {
      if (current === generation.current) setError(`${zh ? "报告或验收已变化；旧内容已清除。" : "Report or acceptance changed; previous content was cleared."} ${String(failure)}`);
    } finally {if (current === generation.current) setBusy(false);}
  }

  const artifact = selected ? managedReportArtifact(
    selected.row.content_type, selected.row.sha256, selected.read.text) : null;
  return <section className="goal-team-results goal-managed-results" aria-label={zh ? "已验收的团队报告" : "Accepted team reports"} aria-busy={busy}>
    <header><div><h3>{zh ? "团队报告" : "Team reports"}</h3>
      <p>{zh ? "只有仍能通过当前验收的报告会出现在这里。" : "Only reports that still pass current acceptance appear here."}</p></div>
      <button type="button" disabled={busy} onClick={() => void load()}><RefreshCw size={14} aria-hidden="true"/>{zh ? "刷新" : "Refresh"}</button></header>
    {busy ? <p role="status">{zh ? "正在核验报告…" : "Verifying reports…"}</p> : null}
    {error ? <p role="alert">{error}</p> : null}
    {page && page.unavailable_count > 0 ? <p role="status">{zh
      ? `本页有 ${page.unavailable_count} 份报告已无法通过当前核验。`
      : `${page.unavailable_count} report(s) on this page cannot pass current verification.`}</p> : null}
    {page && !busy && !page.items.length ? <p>{page.next_cursor
      ? (zh ? "本页没有可核验的报告，可继续下一页。" : "No verifiable reports on this page; continue to the next page.")
      : (zh ? "暂无可核验的团队报告。" : "No verifiable team reports yet.")}</p> : null}
    {page?.next_cursor && !page.items.length ? <button type="button" disabled={busy} onClick={() => void load(page.next_cursor!)}>
      {zh ? "下一页" : "Next page"}
    </button> : null}
    {page && page.items.length > 0 ? <div className="goal-team-results-layout">
      <nav className="goal-team-result-list" aria-label={zh ? "选择团队报告" : "Choose a team report"}>
        {page.items.map(row => <button type="button" key={row.todo_id} disabled={busy}
          aria-pressed={selected?.row.todo_id === row.todo_id} onClick={() => void select(row)}>
          <FileText size={16} aria-hidden="true"/><span><strong>{row.title}</strong><small>{row.producer_agent_id}</small></span>
        </button>)}
        {page.next_cursor ? <button type="button" disabled={busy} onClick={() => void load(page.next_cursor!)}>
          {zh ? "下一页" : "Next page"}
        </button> : null}
      </nav>
      {artifact && selected ? <div ref={reader} tabIndex={-1} className="goal-team-result-reader">
        <TeamArtifactReport key={`${selected.row.todo_id}:${artifact.sha256}`} artifact={artifact}
          zh={zh} heading={selected.row.title}/>
        <p>{zh ? "验收任务" : "Accepted Todo"}: <code>{selected.row.todo_id}</code></p>
      </div> : null}
    </div> : null}
  </section>;
}
