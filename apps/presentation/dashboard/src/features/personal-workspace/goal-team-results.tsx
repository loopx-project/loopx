import {RefreshCw, TriangleAlert} from "lucide-react";
import {ContextHelp} from "./context-help";
import {useEffect, useRef, useState} from "react";
import {delegationStateLabel, fetchLoopXTeamWork, readLoopXTeamWork, type DelegationInventory, type DelegationReadback} from "../../data/chat";
import {TeamArtifactReport, isMarkdownArtifact, type TeamArtifact} from "./team-artifact-content";
import {GoalTeamLineage} from "./goal-team-lineage";
import {ResultCatalogReturn, ResultCatalogRow, useResultCatalogFocus} from "./result-catalog";

type Selection = {operationId: string; ref?: string; sha256?: string};
const readableRows = (page: DelegationInventory) => page.items.filter(row =>
  row.operation_id && row.status === "accepted" && row.current_use?.state !== "unavailable" && !row.recovery_required && row.artifacts?.length);
const AUTO_DISCOVERY_PAGES = 3;
const MAX_INSPECTED_PAGES = 10;
const resultKey = (operationId: string, artifact: {ref: string; sha256: string}) => JSON.stringify([operationId, artifact.ref, artifact.sha256]);

/** Original-conversation result reader and explicit requester adoption. */
export function GoalTeamResults({sessionId, zh, refreshKey, catalogOnly = false}: {sessionId: string; zh: boolean; refreshKey: string; catalogOnly?: boolean}) {
  const [pages, setPages] = useState<DelegationInventory[]>([]);
  const [selection, setSelection] = useState<{result: DelegationReadback; artifact: TeamArtifact} | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reading, setReading] = useState(false);
  const generation = useRef(0);
  const chosen = useRef<Selection | null>(null);
  const inspectedPages = useRef(1);
  const focusReport = useRef(false);
  const origin = useRef<string | null>(null);
  const focus = useResultCatalogFocus();
  useEffect(() => {chosen.current = null; origin.current = null; inspectedPages.current = 1; setReading(false); setPages([]);}, [sessionId, catalogOnly]);
  useEffect(() => {void list(); return () => {generation.current++; focus.cancel();};}, [sessionId, refreshKey, catalogOnly]);
  useEffect(() => {
    if (selection && focusReport.current) {focus.reader(); focusReport.current = false;}
  }, [selection]);
  const changed = zh ? "产物或验收已变化，未展示旧报告。请重新选择可核验的产物。" : "Artifact or acceptance changed. Previous report cleared; select a verified artifact again.";
  async function load(operationId: string, expected: Selection | null, current: number) {
    const value = await readLoopXTeamWork(sessionId, operationId);
    if (current !== generation.current) return;
    const artifacts = value.artifacts ?? [];
    const matches = expected?.ref ? artifacts.filter(row => row.ref === expected.ref) : [];
    const artifact = expected?.ref ? (matches.length === 1 && matches[0].sha256 === expected.sha256 ? matches[0] : undefined)
      : artifacts.find(row => isMarkdownArtifact(row.ref)) ?? artifacts[0];
    if (value.operation_id !== operationId || value.status !== "accepted" || value.current_use?.state === "unavailable" || value.error || value.recovery_required || !artifact) {
      setError(changed);
    } else {
      chosen.current = {operationId, ref: artifact.ref, sha256: artifact.sha256};
      setSelection({result: value, artifact});
    }
  }
  async function list(nextPage = false) {
    const current = ++generation.current;
    focus.cancel();
    if (nextPage) {chosen.current = null; origin.current = null; setReading(false);}
    const minimumPages = Math.min(MAX_INSPECTED_PAGES, inspectedPages.current + (nextPage ? 1 : 0));
    focusReport.current = false;
    setBusy(true); setError(""); setSelection(null);
    let inventoryRead = false;
    try {
      const observed: DelegationInventory[] = [];
      let nextCursor: string | undefined;
      while (observed.length < minimumPages ||
          (!chosen.current && !observed.some(page => readableRows(page).length) && observed.length < AUTO_DISCOVERY_PAGES)) {
        const value = await fetchLoopXTeamWork(sessionId, nextCursor);
        if (current !== generation.current) return;
        observed.push(value);
        if (!value.has_more) break;
        if (!value.next_cursor || (nextCursor && value.next_cursor <= nextCursor)) {
          throw new Error(zh ? "执行分页无法继续核验；请重新读取。" : "Execution paging cannot be verified; refresh the list.");
        }
        nextCursor = value.next_cursor;
      }
      inspectedPages.current = observed.length;
      setPages(observed);
      inventoryRead = true;
      // Keep the reader's exact version. A refresh must never silently replace
      // it with a newer artifact or move to a different member's result.
      let target = chosen.current;
      if (!target && !catalogOnly) {
        const first = observed.flatMap(readableRows)[0];
        const artifact = first?.artifacts?.find(row => isMarkdownArtifact(row.ref)) ?? first?.artifacts?.[0];
        if (first?.operation_id && artifact) target = {operationId: first.operation_id, ...artifact};
      }
      if (target) {chosen.current = target; await load(target.operationId, target, current);}
    } catch (failure) {
      if (current === generation.current) {
        if (!inventoryRead) setPages([]);
        setError(`${zh ? "无法核验，已清除上次报告。" : "Cannot verify; previous report cleared."} ${String(failure)}`);
      }
    } finally {if (current === generation.current) setBusy(false);}
  }
  async function read(operationId: string, expected?: {ref: string; sha256: string}) {
    const current = ++generation.current;
    focus.cancel();
    focusReport.current = true;
    setReading(true);
    setBusy(true); setError(""); setSelection(null);
    if (catalogOnly) focus.reader();
    const target = {operationId, ...expected};
    // Keep failed explicit selections too; a background refresh cannot choose
    // a different result and mask the failed readback.
    chosen.current = target;
    try {await load(operationId, target, current);}
    catch (failure) {if (current === generation.current) setError(`${zh ? "无法核验，已清除上次报告。" : "Cannot verify; previous report cleared."} ${String(failure)}`);}
    finally {if (current === generation.current) setBusy(false);}
  }
  function returnToCatalog() {
    generation.current++;
    chosen.current = null;
    focusReport.current = false;
    setReading(false); setSelection(null); setBusy(false); setError("");
    focus.row(origin.current);
  }
  const rows = pages.flatMap(readableRows);
  const observationGeneration = generation.current;
  const lastPage = pages.at(-1);
  const adoptions = selection?.result.adoptions ?? [];
  const currentAdoptions = adoptions.filter(row => row.state === "current").length;
  const unavailableAdoptions = adoptions.length - currentAdoptions;
  const adoptionSummary = currentAdoptions && unavailableAdoptions
    ? (zh ? "部分采用可核验 · 查看后续结果与失效项" : "Some adoption is verifiable · inspect results and unavailable evidence")
    : currentAdoptions
      ? (zh ? "已记录采用 · 查看后续结果" : "Adoption recorded · inspect downstream results")
      : unavailableAdoptions
        ? (zh ? "采用证据无法核验 · 查看版本依据" : "Adoption evidence unavailable · inspect versions")
        : selection?.result.dependencies?.length
          ? (zh ? "此结果关联来源版本 · 查看依据" : "This result references source versions · inspect evidence")
          : (zh ? "尚无采用记录 · 查看验收与版本依据" : "No adoption recorded · inspect acceptance and versions");
  return <section ref={focus.root} tabIndex={-1} className={`goal-team-results${catalogOnly ? " is-catalog" : ""}`} aria-label={zh ? "团队成果" : "Team results"} aria-busy={busy}>
    <header><h3>{zh ? "团队成果" : "Team results"}</h3>
      <button type="button" disabled={busy} onClick={() => void list()}><RefreshCw size={14} aria-hidden="true"/>{zh ? "刷新成果" : "Refresh results"}</button></header>
    {busy ? <p role="status">{zh ? "正在核验产物…" : "Verifying artifacts…"}</p> : null}
    {error ? <p role="alert">{error}</p> : null}
    {pages.some(page => !page.page_readback_complete) ? <ContextHelp label={zh ? "团队记录未完全核验" : "Team records partially verified"} icon={TriangleAlert}><p role="status">{zh ? "已检查的工作中有无法核验的记录；当前报告只代表它自己的验收，不代表整个团队。" : "Some inspected work cannot be verified. This report reflects only its own acceptance, not the whole team."}</p></ContextHelp> : null}
    <div className="goal-team-results-layout">
      <nav className="goal-team-result-list" data-result-catalog tabIndex={-1} hidden={catalogOnly && reading} aria-label={zh ? "选择团队产物" : "Choose a team artifact"}>{rows.flatMap(row => {
        const artifacts = catalogOnly ? row.artifacts! : [row.artifacts!.find(item => isMarkdownArtifact(item.ref)) ?? row.artifacts![0]];
        return artifacts.map(artifact => <ResultCatalogRow key={`${row.record_id}:${resultKey(row.operation_id!, artifact)}`}
          resultKey={resultKey(row.operation_id!, artifact)} title={artifact.ref} source={`${row.agent_id ?? "—"} · ${row.operation_id}`}
          status={delegationStateLabel(row, zh)} zh={zh} disabled={busy} label={`${row.agent_id} · ${artifact.ref}`}
          selected={selection?.result.operation_id === row.operation_id && selection.artifact.ref === artifact.ref && selection.artifact.sha256 === artifact.sha256}
          onSelect={() => {origin.current = resultKey(row.operation_id!, artifact); void read(row.operation_id!, artifact);}}/>);
      })}
        {pages.length && !rows.length ? <p>{zh ? "已检查的页面没有可读取的已验收产物。" : "No accepted artifact is available in the inspected pages."}</p> : null}
        {lastPage?.has_more && lastPage.next_cursor && pages.length < MAX_INSPECTED_PAGES ? <button type="button" disabled={busy} onClick={() => void list(true)}>{zh ? "继续查找成果" : "Find more results"}</button> : null}
        {lastPage?.has_more ? <p>{pages.length >= MAX_INSPECTED_PAGES
          ? (zh ? "已达到有界查找上限；更多工作请在团队执行中按操作检查。" : "Bounded search limit reached; inspect further work by operation in Team execution.")
          : (zh ? `已检查 ${pages.length} 页；还有未检查的工作。` : `${pages.length} pages inspected; more work remains unseen.`)}</p> : null}
      </nav>
      {selection || (catalogOnly && reading) ? <div data-result-reader tabIndex={-1} className="goal-team-result-reader" aria-label={zh ? "当前报告" : "Current report"}>
        {catalogOnly ? <ResultCatalogReturn zh={zh} onReturn={returnToCatalog}/> : null}
        {selection ? <><details><summary>{adoptionSummary}</summary><GoalTeamLineage result={selection.result} zh={zh} onInspect={operationId => void read(operationId)}
          sessionId={sessionId} onObservation={value => {
            if (generation.current !== observationGeneration) return;
            const artifact = value?.artifacts?.find(row => row.ref === selection.artifact.ref && row.sha256 === selection.artifact.sha256);
            if (value && artifact) setSelection({result: value, artifact});
            else {setSelection(null); setError(changed);}
          }}/></details>
        <TeamArtifactReport key={`${selection.result.operation_id}:${selection.artifact.sha256}`} artifact={selection.artifact} zh={zh} heading={zh ? "已通过当前验收" : "Currently accepted"}/>
        {selection.result.artifacts && selection.result.artifacts.length > 1 ? <label>{zh ? "其他产物" : "Other artifacts"}<select value={selection.artifact.ref}
          onChange={event => {const artifact = selection.result.artifacts!.find(row => row.ref === event.target.value); if (artifact) {
            chosen.current = {operationId: selection.result.operation_id, ref: artifact.ref, sha256: artifact.sha256};
            setSelection({...selection, artifact});
          }}}>
          {selection.result.artifacts.map(row => <option value={row.ref} key={row.ref}>{row.ref}</option>)}
        </select></label> : null}</> : null}
      </div> : null}
    </div>
  </section>;
}
