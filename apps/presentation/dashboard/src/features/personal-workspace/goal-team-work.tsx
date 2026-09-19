import {useEffect, useRef, useState} from "react";
import {fetchLoopXTeamWork, inspectLoopXMember, type DelegationInventory, type DelegationPreflight} from "../../data/chat";

type Member = {id: string; agent_id: string; todo_id: string};

/** On-demand observations share the caller/config pin of this Goal conversation. */
export function GoalTeamWork({sessionId, members, zh}: {sessionId: string; members: Member[]; zh: boolean}) {
  const [page, setPage] = useState<DelegationInventory | null>(null);
  const [checks, setChecks] = useState<Record<string, DelegationPreflight>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  useEffect(() => {
    generation.current++; setPage(null); setChecks({}); setError(""); setBusy(false);
    void read();
    return () => {generation.current++;};
  }, [sessionId]);
  async function read(cursor?: string) {
    const current = ++generation.current;
    setBusy(true); setError(""); setPage(null);
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
  const labels: Record<DelegationPreflight["state"], string> = zh ? {
    turn_blocked: "当前任务未获准执行", acceptance_unavailable: "缺少有效验收绑定",
    runtime_unavailable: "运行时不可用", runtime_unverified: "运行时可用性尚未验证", launchable: "本机启动条件已满足",
  } : {turn_blocked: "Task admission blocked", acceptance_unavailable: "Acceptance binding unavailable",
    runtime_unavailable: "Runtime unavailable", runtime_unverified: "Runtime availability unverified", launchable: "Local launch prerequisites met"};
  const stateLabel = (row: DelegationInventory["items"][number]) => {
    if (row.status === "unavailable") return zh ? "无法核验" : "Unavailable";
    if (row.status === "accepted") return zh ? "已通过当前验收" : "Currently accepted";
    if (row.status === "rejected") return zh ? "未通过验收" : "Rejected";
    if (row.recovery_required) return zh ? "需要恢复原执行" : "Original execution needs recovery";
    if (row.status === "running" && row.worker_active) return zh ? "执行中" : "Executing";
    if (row.status === "turn_returned" && row.worker_active) return zh ? "正在验收" : "Validating";
    return zh ? "已派发，等待执行回读" : "Dispatched; awaiting execution readback";
  };
  return <div className="goal-team-work">
    <section aria-label={zh ? "团队执行详情" : "Team execution details"}>
      <p>{zh ? "检查不会启动成员。暂停协调员后，已派发的工作仍会继续。" : "Inspection starts no members. Dispatched work continues when the coordinator is paused."}</p>
      <h3>{zh ? "已绑定成员" : "Bound members"}</h3>
      <ul className="goal-team-bindings">{members.map(member => <li key={member.id}>
        <div><strong>{member.agent_id}</strong>
          <button type="button" disabled={busy} onClick={() => void inspect(member.id)}>{zh ? "检查启动条件" : "Check prerequisites"}</button></div>
        <details><summary>{zh ? "任务与执行配置" : "Task and execution details"}</summary><code>{member.todo_id}</code>{checks[member.id]?.executor.profile ? <code>{checks[member.id].executor.profile}</code> : null}</details>
        {checks[member.id] ? <p role="status">{labels[checks[member.id].state] ?? (zh ? "状态未知" : "Unknown")}
          {" · "}{checks[member.id].executor.host}{checks[member.id].executor.reason ? ` · ${checks[member.id].executor.reason}` : ""}

          {" · "}{zh ? "不代表正在执行" : "Does not mean executing"}</p> : null}
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
          <strong>{row.agent_id ?? (zh ? "记录不可读" : "Unreadable record")} · {stateLabel(row)}</strong>
          <details><summary>{zh ? "执行标识" : "Execution identifier"}</summary><code>{row.operation_id ?? row.record_id}</code></details>
          {row.artifacts?.map(artifact => <details key={artifact.ref}><summary>{artifact.ref}</summary><code>{artifact.sha256}</code></details>)}
        </li>)}</ul>
        <p>{zh ? "仅限当前协调身份；分页不是团队快照。" : "Scoped to this coordinator; paging is not a team snapshot."}{page.has_more ? (zh ? " 还有下一页。" : " More pages remain.") : ""}</p>
      </> : null}
    </section>
  </div>;
}
