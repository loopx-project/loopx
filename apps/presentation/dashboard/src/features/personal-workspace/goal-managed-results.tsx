import {useEffect, useRef, useState} from "react";
import {RefreshCw} from "lucide-react";
import {
  fetchManagedGoalResults, readManagedGoalResult,
  type ManagedGoalResultPage, type ManagedGoalResultRow, type ManagedGoalResultRead,
} from "../../data/chat";
import {TeamArtifactReport, managedReportArtifact} from "./team-artifact-content";
import {ResultCatalogReturn, ResultCatalogRow, useResultCatalogFocus} from "./result-catalog";

const resultKey = (row: ManagedGoalResultRow) => JSON.stringify([row.todo_id, row.sha256, row.producer_agent_id, row.content_type]);

/** Goal-scoped local reports; an inventory row never stands in for exact acceptance readback. */
export function GoalManagedResults({goalId, zh, hideEmpty = false, catalogOnly = false}: {goalId: string; zh: boolean; hideEmpty?: boolean; catalogOnly?: boolean}) {
  const [page, setPage] = useState<ManagedGoalResultPage | null>(null);
  const [selected, setSelected] = useState<{row: ManagedGoalResultRow; read: ManagedGoalResultRead} | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reading, setReading] = useState(false);
  const generation = useRef(0);
  const chosen = useRef<string | null>(null);
  const openedCatalog = useRef(false);
  const pageCursor = useRef<string | undefined>(undefined);
  const focus = useResultCatalogFocus();

  useEffect(() => {
    chosen.current = null;
    openedCatalog.current = false;
    pageCursor.current = undefined;
    setReading(false);
    setPage(null);
    void load();
    return () => {generation.current++; focus.cancel();};
  }, [goalId, catalogOnly]);

  async function read(row: ManagedGoalResultRow, current: number, focusReader = false) {
    const result = await readManagedGoalResult(goalId, row.todo_id);
    if (current !== generation.current) return;
    if (result.todo_id !== row.todo_id || result.goal_id !== goalId ||
        result.result.sha256 !== row.sha256 || result.result.producer_agent_id !== row.producer_agent_id ||
        result.result.content_type !== row.content_type) {
      throw new Error(zh ? "报告版本或验收已变化" : "Report version or acceptance changed");
    }
    chosen.current = resultKey(row);
    setSelected({row, read: result});
    if (focusReader) focus.reader();
  }

  async function load(cursor?: string) {
    const current = ++generation.current;
    focus.cancel();
    if (cursor !== pageCursor.current) {chosen.current = null; setReading(false);}
    pageCursor.current = cursor;
    setBusy(true); setError(""); setSelected(null);
    let inventoryRead = false;
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
      inventoryRead = true;
      const previous = chosen.current;
      const row = previous
        ? next.items.find(item => resultKey(item) === previous)
        : catalogOnly ? undefined : next.items[0];
      if (previous && !row) {
        setError(zh ? "上次报告已不在当前验收结果中。" : "The previous report is no longer in current accepted results.");
      } else if (row) {
        await read(row, current);
      }
    } catch (failure) {
      if (current === generation.current) {
        if (!inventoryRead) {
          setPage(null); pageCursor.current = undefined;
          chosen.current = null; setReading(false);
        }
        setError(`${zh ? "无法核验报告；旧内容已清除。" : "Cannot verify report; previous content was cleared."} ${String(failure)}`);
      }
    } finally {
      if (current === generation.current) setBusy(false);
    }
  }

  async function select(row: ManagedGoalResultRow) {
    const current = ++generation.current;
    focus.cancel();
    chosen.current = resultKey(row);
    openedCatalog.current = true;
    setReading(true);
    setBusy(true); setError(""); setSelected(null);
    if (catalogOnly) focus.reader();
    try {await read(row, current, true);}
    catch (failure) {
      if (current === generation.current) setError(`${zh ? "报告或验收已变化；旧内容已清除。" : "Report or acceptance changed; previous content was cleared."} ${String(failure)}`);
    } finally {if (current === generation.current) setBusy(false);}
  }

  function returnToCatalog() {
    const origin = chosen.current;
    generation.current++;
    chosen.current = null;
    setReading(false); setSelected(null); setBusy(false); setError("");
    focus.row(origin);
  }

  const artifact = selected ? managedReportArtifact(
    selected.row.content_type, selected.row.sha256, selected.read.text) : null;
  if (hideEmpty && !openedCatalog.current && page && !reading && !busy && !error && !page.items.length && !page.unavailable_count && !page.next_cursor) return null;
  return <section ref={focus.root} tabIndex={-1} className={`goal-team-results goal-managed-results${catalogOnly ? " is-catalog" : ""}`} aria-label={zh ? "已验收的团队报告" : "Accepted team reports"} aria-busy={busy}>
    <header><div><h3>{zh ? "团队报告" : "Team reports"}</h3>
      <p>{zh ? "只有仍能通过当前验收的报告会出现在这里。" : "Only reports that still pass current acceptance appear here."}</p></div>
      <button type="button" disabled={busy} onClick={() => void load(pageCursor.current)}><RefreshCw size={14} aria-hidden="true"/>{zh ? "刷新" : "Refresh"}</button></header>
    {busy ? <p role="status">{zh ? "正在核验报告…" : "Verifying reports…"}</p> : null}
    {error ? <p role="alert">{error}</p> : null}
    {page && page.unavailable_count > 0 ? <p role="status">{zh
      ? `本页有 ${page.unavailable_count} 份报告已无法通过当前核验。`
      : `${page.unavailable_count} report(s) on this page cannot pass current verification.`}</p> : null}
    {page && !busy && !page.items.length ? <p>{page.next_cursor
      ? (zh ? "本页没有可核验的报告，可继续下一页。" : "No verifiable reports on this page; continue to the next page.")
      : (zh ? "暂无可核验的团队报告。" : "No verifiable team reports yet.")}</p> : null}
    <div className="goal-team-results-layout">
      <nav className="goal-team-result-list" data-result-catalog tabIndex={-1} hidden={catalogOnly && reading} aria-label={zh ? "选择团队报告" : "Choose a team report"}>
        {page?.items.map(row => <ResultCatalogRow key={resultKey(row)} resultKey={resultKey(row)} title={row.title}
          source={`${row.producer_agent_id} · ${row.todo_id}`} status={zh ? "已通过当前验收" : "Currently accepted"}
          time={row.completed_at} zh={zh} disabled={busy} selected={chosen.current === resultKey(row)} onSelect={() => void select(row)}/>)}
        {page?.next_cursor ? <button type="button" disabled={busy} onClick={() => void load(page.next_cursor!)}>
          {zh ? "下一页" : "Next page"}
        </button> : null}
      </nav>
      {(catalogOnly && reading) || (artifact && selected) ? <div data-result-reader tabIndex={-1} className="goal-team-result-reader">
        {catalogOnly ? <ResultCatalogReturn zh={zh} onReturn={returnToCatalog}/> : null}
        {artifact && selected ? <><TeamArtifactReport key={`${selected.row.todo_id}:${artifact.sha256}`} artifact={artifact}
          zh={zh} heading={selected.row.title}/>
        <p>{zh ? "验收任务" : "Accepted Todo"}: <code>{selected.row.todo_id}</code></p></> : null}
      </div> : null}
    </div>
  </section>;
}
