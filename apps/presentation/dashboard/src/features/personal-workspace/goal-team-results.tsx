import {useEffect, useRef, useState} from "react";
import {fetchLoopXTeamWork, readLoopXTeamWork, type DelegationInventory, type DelegationReadback} from "../../data/chat";
import {TeamArtifactReport, isMarkdownArtifact, type TeamArtifact} from "./team-artifact-content";
import {GoalTeamLineage} from "./goal-team-lineage";

/** Read-only entry in the original conversation, using the same scoped delegation API. */
export function GoalTeamResults({sessionId, zh, refreshKey}: {sessionId: string; zh: boolean; refreshKey: string}) {
  const [page, setPage] = useState<DelegationInventory | null>(null);
  const [selection, setSelection] = useState<{result: DelegationReadback; artifact: TeamArtifact} | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const report = useRef<HTMLDivElement>(null);
  useEffect(() => {void list(); return () => {generation.current++;};}, [sessionId, refreshKey]);
  useEffect(() => {if (selection) report.current?.focus();}, [selection]);
  async function list(cursor?: string) {
    const current = ++generation.current;
    setBusy(true); setError(""); setPage(null); setSelection(null);
    try {const value = await fetchLoopXTeamWork(sessionId, cursor); if (current === generation.current) setPage(value);}
    catch (failure) {if (current === generation.current) setError(String(failure));}
    finally {if (current === generation.current) setBusy(false);}
  }
  async function read(operationId: string, expected?: {ref: string; sha256: string}) {
    const current = ++generation.current;
    setBusy(true); setError(""); setSelection(null);
    try {
      const value = await readLoopXTeamWork(sessionId, operationId);
      if (current !== generation.current) return;
      const artifacts = value.artifacts ?? [];
      const matches = expected ? artifacts.filter(row => row.ref === expected.ref) : [];
      const artifact = expected ? (matches.length === 1 && matches[0].sha256 === expected.sha256 ? matches[0] : undefined) : artifacts.find(row => isMarkdownArtifact(row.ref)) ?? artifacts[0];
      if (value.operation_id !== operationId || value.status !== "accepted" || value.error || value.recovery_required || !artifact) {
        setError(zh ? "产物或验收已变化，未展示旧报告。刷新后重新选择。" : "Artifact or acceptance changed. Previous report cleared; refresh and select again.");
      } else setSelection({result: value, artifact});
    } catch (failure) {if (current === generation.current) setError(`${zh ? "无法核验，已清除上次报告。" : "Cannot verify; previous report cleared."} ${String(failure)}`);}
    finally {if (current === generation.current) setBusy(false);}
  }
  const rows = (page?.items ?? []).filter(row => row.operation_id && row.status === "accepted" && !row.recovery_required && row.artifacts?.length);
  return <section className="goal-team-results" aria-label={zh ? "团队成果" : "Team results"} aria-busy={busy}>
    <header><h3>{zh ? "团队成果" : "Team results"}</h3><button type="button" disabled={busy} onClick={() => void list()}>{zh ? "刷新成果" : "Refresh results"}</button></header>
    {busy ? <p role="status">{zh ? "正在核验产物…" : "Verifying artifacts…"}</p> : null}
    {error ? <p role="alert">{error}</p> : null}
    {page ? <>
      <div className="goal-team-result-list">{rows.map(row => {
        const artifact = row.artifacts!.find(item => isMarkdownArtifact(item.ref)) ?? row.artifacts![0];
        return <button type="button" key={row.record_id} disabled={busy} aria-pressed={selection?.result.operation_id === row.operation_id}
          onClick={() => void read(row.operation_id!, artifact)}>{row.agent_id} · {artifact.ref}</button>;
      })}</div>
      {!page.page_readback_complete ? <p role="status">{zh ? "部分工作无法核验，请在团队执行中检查。" : "Some work cannot be verified; inspect Team execution."}</p> : null}
      {!rows.length ? <p>{zh ? "本页没有可读取的已验收产物。" : "No accepted artifact is available on this page."}</p> : null}
      {page.has_more && page.next_cursor ? <button type="button" disabled={busy} onClick={() => void list(page.next_cursor!)}>{zh ? "下一页成果" : "Next results page"}</button> : null}
    </> : null}
    {selection ? <div ref={report} tabIndex={-1} className="goal-team-result-reader">
      <TeamArtifactReport key={`${selection.result.operation_id}:${selection.artifact.sha256}`} artifact={selection.artifact} zh={zh}/>
      {selection.result.artifacts && selection.result.artifacts.length > 1 ? <label>{zh ? "其他产物" : "Other artifacts"}<select value={selection.artifact.ref}
        onChange={event => {const artifact = selection.result.artifacts!.find(row => row.ref === event.target.value); if (artifact) setSelection({...selection, artifact});}}>
        {selection.result.artifacts.map(row => <option value={row.ref} key={row.ref}>{row.ref}</option>)}
      </select></label> : null}
      <details><summary>{zh ? "验收与采用关系" : "Acceptance and adoption"}</summary><GoalTeamLineage result={selection.result} zh={zh} onInspect={operationId => void read(operationId)}/></details>
    </div> : null}
  </section>;
}
