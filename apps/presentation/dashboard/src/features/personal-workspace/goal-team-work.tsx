import {useEffect, useRef, useState} from "react";
import {fetchLoopXTeamWork, inspectLoopXMember, delegationStateLabel, type LoopXModeSnapshot, type DelegationInventory, type DelegationPreflight} from "../../data/chat";

import {GoalTeamEvidence} from "./goal-team-evidence";
import {DelegationPreflightStatus} from "./delegation-preflight-status";

type Member = {id: string; agent_id: string; todo_id: string};

/** On-demand observations share the caller/config pin of this Goal conversation. */
export function GoalTeamWork({sessionId, members, zh, canMessage, ingress}: {sessionId: string; members: Member[]; zh: boolean; canMessage: boolean; ingress: LoopXModeSnapshot["ingress"]}) {
  const [selected, setSelected] = useState<string | null>(null);
  const backButton = useRef<HTMLButtonElement | null>(null);
  const lastSelection = useRef<string | null>(null);
  const selectedTrigger = useRef<HTMLButtonElement | null>(null);
  const [page, setPage] = useState<DelegationInventory | null>(null);
  const [checks, setChecks] = useState<Record<string, DelegationPreflight>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  useEffect(() => {(selected ? backButton.current : selectedTrigger.current)?.focus();}, [selected]);
  useEffect(() => {
    generation.current++; setPage(null); setChecks({}); setError(""); setBusy(false);
    void read();
    return () => {generation.current++;};
  }, [sessionId]);
  async function read(cursor?: string) {
    const current = ++generation.current;
    setBusy(true); setError(""); setPage(null); setSelected(null);
    try {
      const result = await fetchLoopXTeamWork(sessionId, cursor);
      if (current === generation.current) setPage(result);
    } catch (failure) {
      if (current === generation.current) setError(failure instanceof Error ? failure.message : String(failure));
    } finally {if (current === generation.current) setBusy(false);}
  }
  async function inspect(id: string) {
    const current = ++generation.current;
    setBusy(true); setError("");
    setChecks(previous => {const next = {...previous}; delete next[id]; return next;});
    try {
      const result = await inspectLoopXMember(sessionId, id);
      if (current === generation.current) setChecks(previous => ({...previous, [id]: result}));
    } catch (failure) {
      if (current === generation.current) setError(failure instanceof Error ? failure.message : String(failure));
    } finally {if (current === generation.current) setBusy(false);}
  }
  if (selected) return <div className="goal-team-work">
    <button ref={backButton} type="button" onClick={() => setSelected(null)}>{zh ? "返回执行列表" : "Back to executions"}</button>
    <GoalTeamEvidence key={`${sessionId}:${selected}`} sessionId={sessionId} operationId={selected} zh={zh} canMessage={canMessage} ingress={ingress} onInspect={setSelected}/>
  </div>;
  return <div className="goal-team-work">
    <section aria-label={zh ? "团队执行详情" : "Team execution details"}>
      <p>{zh ? "检查不会启动成员。暂停协调员后，已派发的工作仍会继续。" : "Inspection starts no members. Dispatched work continues when the coordinator is paused."}</p>
      <h3>{zh ? "已绑定成员" : "Bound members"}</h3>
      <ul className="goal-team-bindings">{members.map(member => <li key={member.id}>
        <div><strong>{member.agent_id}</strong>
          <button type="button" disabled={busy} onClick={() => void inspect(member.id)}>{zh ? "检查启动条件" : "Check prerequisites"}</button></div>
        <details><summary>{zh ? "任务与执行配置" : "Task and execution details"}</summary><code>{member.todo_id}</code>{checks[member.id]?.executor?.profile ? <code>{checks[member.id]?.executor?.profile}</code> : null}</details>
        {checks[member.id] ? <DelegationPreflightStatus check={checks[member.id]} zh={zh}/> : null}
      </li>)}</ul>
      <div className="goal-team-work-actions"><strong>{zh ? "此协调身份的持久工作" : "Durable work for this coordinator"}</strong>
        <button type="button" disabled={busy} onClick={() => {setChecks({}); void read();}}>{zh ? "重新核验" : "Refresh"}</button>
        {page?.has_more && page.next_cursor ? <button type="button" disabled={busy} onClick={() => void read(page.next_cursor!)}>{zh ? "下一页" : "Next page"}</button> : null}</div>
      {busy ? <p role="status">{zh ? "正在读取当前事实…" : "Reading current facts…"}</p> : null}
      {error ? <p role="alert">{error}</p> : null}
      {page ? <>
        {!page.page_readback_complete ? <p role="status">{zh ? "本页有无法核验的工作，请检查原请求；不要直接重新派工。" : "Some work cannot be verified. Reconcile the original request before redispatching."}</p> : null}
        {!page.items.length ? <p>{zh ? "此页没有委派记录；不代表整个团队没有工作或 Goal 已完成。" : "No records on this page; this does not establish an idle team or a completed Goal."}</p> : null}
        <ul className="goal-team-operations">{page.items.map(row => <li key={row.record_id}>
          <strong>{row.agent_id ?? (zh ? "记录不可读" : "Unreadable record")} · {delegationStateLabel(row, zh)}</strong>
          <details><summary>{zh ? "执行标识" : "Execution identifier"}</summary><code>{row.operation_id ?? row.record_id}</code></details>
          {row.operation_id ? <button ref={row.operation_id === lastSelection.current ? selectedTrigger : undefined} type="button" onClick={() => {
            lastSelection.current = row.operation_id; setSelected(row.operation_id);
          }}>{zh ? "查看证据与反馈" : "Evidence and feedback"}</button> : null}
        </li>)}</ul>
        <p>{zh ? "仅限当前协调身份；分页不是团队快照。" : "Scoped to this coordinator; paging is not a team snapshot."}{page.has_more ? (zh ? " 还有下一页。" : " More pages remain.") : ""}</p>
      </> : null}
    </section>
  </div>;
}
