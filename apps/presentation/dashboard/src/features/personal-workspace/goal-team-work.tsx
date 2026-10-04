import {useEffect, useRef, useState} from "react";
import {AlertTriangle, CheckCircle2, CircleDashed, CircleHelp, Clock3, Loader2, RotateCcw, ShieldCheck} from "lucide-react";
import {fetchLoopXTeamWork, inspectLoopXMember, delegationState, delegationStateLabel, type DelegationState, type LoopXModeSnapshot, type DelegationInventory, type DelegationPreflight} from "../../data/chat";

import {GoalTeamEvidence} from "./goal-team-evidence";
import {DelegationPreflightStatus} from "./delegation-preflight-status";

type Member = {id: string; agent_id: string; todo_id: string};
type DelegationRecord = DelegationInventory["items"][number];
type PulseBucket = "executing" | "validating" | "accepted" | "attention" | "stopped" | "dispatched" | "unknown";
type CheckTone = "unchecked" | "ready" | "unverified" | "blocked";

const PULSE_BUCKETS: Record<DelegationState, PulseBucket> = {
  executing: "executing", validating: "validating", accepted: "accepted",
  rejected: "attention", recovery_required: "attention", unavailable: "attention",
  // A recorded stop is not proof its Host group released; it is its own bucket.
  stopped: "stopped", dispatched: "dispatched", unknown: "unknown",
};
const PULSE_LABELS: Record<PulseBucket, {zh: string; en: string}> = {
  executing: {zh: "执行中", en: "Executing"},
  validating: {zh: "正在验收", en: "Validating"},
  accepted: {zh: "已通过", en: "Accepted"},
  attention: {zh: "需要处理", en: "Needs attention"},
  stopped: {zh: "已登记停止", en: "Stop on record"},
  dispatched: {zh: "等待回读", en: "Awaiting readback"},
  unknown: {zh: "状态未知", en: "Unknown"},
};
const CHECK_LABELS: Record<CheckTone, {zh: string; en: string}> = {
  unchecked: {zh: "未检查", en: "Not checked"},
  ready: {zh: "可启动", en: "Ready"},
  unverified: {zh: "运行时待核验", en: "Runtime unverified"},
  blocked: {zh: "受阻", en: "Blocked"},
};

function checkTone(check: DelegationPreflight | undefined, failed: boolean): CheckTone {
  if (failed) return "blocked";
  if (!check) return "unchecked";
  return check.state === "launchable" ? "ready" : check.state === "runtime_unverified" ? "unverified" : "blocked";
}

function StateIcon({state}: {state: DelegationState | PulseBucket | CheckTone}) {
  if (state === "executing") return <span className="goal-team-live" aria-hidden="true"/>;
  if (state === "validating") return <Loader2 aria-hidden="true" size={14}/>;
  if (state === "accepted" || state === "ready") return <CheckCircle2 aria-hidden="true" size={14}/>;
  if (state === "dispatched") return <Clock3 aria-hidden="true" size={14}/>;
  // A recorded stop is an explicit terminal marker, not an unknown to triage.
  if (state === "stopped") return <CircleDashed aria-hidden="true" size={14}/>;
  if (state === "unverified") return <ShieldCheck aria-hidden="true" size={14}/>;
  if (state === "unchecked") return <CircleDashed aria-hidden="true" size={14}/>;
  if (state === "unknown") return <CircleHelp aria-hidden="true" size={14}/>;
  return <AlertTriangle aria-hidden="true" size={14}/>;
}

/** On-demand observations share the caller/config pin of this Goal conversation. */
export function GoalTeamWork({sessionId, members, zh, canMessage, ingress}: {sessionId: string; members: Member[]; zh: boolean; canMessage: boolean; ingress: LoopXModeSnapshot["ingress"]}) {
  // Navigation retains references only. Each visit remounts the evidence reader
  // and rechecks current authority and acceptance, including on the way back.
  const [evidencePath, setEvidencePath] = useState<string[]>([]);
  const selected = evidencePath.at(-1) ?? null;
  const backButton = useRef<HTMLButtonElement | null>(null);
  const lastSelection = useRef<string | null>(null);
  const selectedTrigger = useRef<HTMLButtonElement | null>(null);
  const [page, setPage] = useState<DelegationInventory | null>(null);
  const [checks, setChecks] = useState<Record<string, DelegationPreflight>>({});
  const [checkErrors, setCheckErrors] = useState<Record<string, string>>({});
  const [inspectionTotal, setInspectionTotal] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  useEffect(() => {(selected ? backButton.current : selectedTrigger.current)?.focus();}, [selected]);
  const memberKey = members.map(member => `${member.id}:${member.agent_id}:${member.todo_id}`).join("|");
  useEffect(() => {
    generation.current++; setPage(null); setChecks({}); setCheckErrors({}); setInspectionTotal(0); setError(""); setBusy(false);
    void read();
    return () => {generation.current++;};
  }, [sessionId, memberKey]);
  async function read(cursor?: string) {
    const current = ++generation.current;
    setBusy(true); setError(""); setPage(null); setEvidencePath([]);
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
    setCheckErrors(previous => {const next = {...previous}; delete next[id]; return next;});
    try {
      const result = await inspectLoopXMember(sessionId, id);
      if (current === generation.current) setChecks(previous => ({...previous, [id]: result}));
    } catch (failure) {
      if (current === generation.current) setCheckErrors(previous => ({...previous, [id]: failure instanceof Error ? failure.message : String(failure)}));
    } finally {if (current === generation.current) setBusy(false);}
  }
  async function inspectAll() {
    const current = ++generation.current;
    setBusy(true); setError(""); setChecks({}); setCheckErrors({}); setInspectionTotal(members.length);
    let next = 0;
    async function worker() {
      // Stop taking new members once a newer team snapshot supersedes this run,
      // so a stale generation cannot keep issuing queued read requests.
      while (next < members.length && current === generation.current) {
        const member = members[next++];
        try {
          const check = await inspectLoopXMember(sessionId, member.id);
          if (current === generation.current) setChecks(previous => ({...previous, [member.id]: check}));
        } catch (failure) {
          if (current === generation.current) setCheckErrors(previous => ({...previous, [member.id]: failure instanceof Error ? failure.message : String(failure)}));
        }
      }
    }
    await Promise.all(Array.from({length: Math.min(4, members.length)}, () => worker()));
    if (current !== generation.current) return;
    setInspectionTotal(0); setBusy(false);
  }
  const checked = Object.keys(checks).length + Object.keys(checkErrors).length;
  const ready = Object.values(checks).filter(check => check.state === "launchable").length;
  const unverified = Object.values(checks).filter(check => check.state === "runtime_unverified").length;
  const blocked = checked - ready - unverified;
  if (selected) return <div className="goal-team-work">
    <div className="goal-team-work-actions">
      <button ref={backButton} type="button" onClick={() => setEvidencePath(path => path.slice(0, -1))}>
        {evidencePath.length > 1 ? (zh ? "返回上一份证据" : "Back to previous evidence") : (zh ? "返回执行列表" : "Back to executions")}
      </button>
      {evidencePath.length > 1 ? <button type="button" onClick={() => setEvidencePath([])}>{zh ? "返回执行列表" : "Back to executions"}</button> : null}
    </div>
    <GoalTeamEvidence key={`${sessionId}:${selected}`} sessionId={sessionId} operationId={selected} zh={zh} canMessage={canMessage} ingress={ingress}
      onInspect={operationId => {if (operationId !== selected) setEvidencePath(path => [...path, operationId]);}}/>
  </div>;

  const items = page?.items ?? [];
  const memberRecords = new Map(members.map(member => [member.id, [] as DelegationRecord[]]));
  const unboundRecords: DelegationRecord[] = [];
  for (const row of items) {
    // Inventory has no binding id. Only a unique Agent/Todo match establishes
    // ownership; historical, incomplete and ambiguous records remain visible.
    const matches = members.filter(member => member.agent_id === row.agent_id && member.todo_id === row.todo_id);
    if (matches.length === 1) memberRecords.get(matches[0].id)!.push(row);
    else unboundRecords.push(row);
  }
  const pulse = items.reduce((counts, row) => {counts[PULSE_BUCKETS[delegationState(row)]] += 1; return counts;},
    {executing: 0, validating: 0, accepted: 0, attention: 0, stopped: 0, dispatched: 0, unknown: 0} as Record<PulseBucket, number>);
  const visibleBuckets = (Object.keys(PULSE_LABELS) as PulseBucket[]).filter(bucket => bucket !== "unknown" || pulse.unknown > 0);

  function renderRecord(row: DelegationRecord, showAgent: boolean) {
    const state = delegationState(row);
    return <li key={row.record_id} className="goal-team-record" data-state={state}>
      <span className="goal-team-record-state"><StateIcon state={state}/>{showAgent
        ? `${row.agent_id ?? (zh ? "记录不可读" : "Unreadable record")} · ${delegationStateLabel(row, zh)}` : delegationStateLabel(row, zh)}</span>
      {showAgent ? <details><summary>{zh ? "执行标识" : "Execution identifier"}</summary>
        <code>{row.operation_id ?? row.record_id}</code>{row.todo_id ? <code>{row.todo_id}</code> : null}
      </details> : null}
      {row.operation_id ? <button ref={row.operation_id === lastSelection.current ? selectedTrigger : undefined} type="button" onClick={() => {
        lastSelection.current = row.operation_id; setEvidencePath([row.operation_id!]);
      }}>{zh ? "查看证据与反馈" : "Evidence and feedback"}</button> : null}
    </li>;
  }

  return <div className="goal-team-work">
    <section aria-label={zh ? "团队执行详情" : "Team execution details"}>
      <p className="goal-team-note">{zh ? "检查不会启动成员。暂停协调员后，已派发的工作仍会继续。" : "Inspection starts no members. Dispatched work continues when the coordinator is paused."}</p>
      {page ? <div className="goal-team-pulse-block">
        <ul className="goal-team-pulse" aria-label={zh ? "本页工作概览" : "Work on this page"}>{visibleBuckets.map(bucket => <li key={bucket} data-bucket={bucket} data-empty={pulse[bucket] === 0}>
          <strong>{pulse[bucket]}</strong><span><StateIcon state={bucket === "executing" && pulse.executing === 0 ? "unchecked" : bucket}/>{zh ? PULSE_LABELS[bucket].zh : PULSE_LABELS[bucket].en}</span>
        </li>)}</ul>
        <p>{zh ? `本页 ${items.length} 条委派记录，仅限当前协调身份；分页不是团队快照。` : `${items.length} delegation records on this page, scoped to this coordinator; paging is not a team snapshot.`}{page.has_more ? (zh ? " 还有下一页。" : " More pages remain.") : ""}</p>
      </div> : null}
      <div className="goal-team-work-actions"><h3>{zh ? `已绑定成员 · ${members.length}` : `Bound members · ${members.length}`}</h3>
        <button type="button" disabled={busy || !members.length} onClick={() => void inspectAll()}>{zh ? "检查整个团队" : "Check whole team"}</button></div>
      {checked || inspectionTotal ? <p role="status">{zh
        ? `${inspectionTotal ? "正在检查" : "上次检查"} ${checked}/${members.length} 名：${ready} 名满足本机启动条件，${unverified} 名运行时待核验，${blocked} 名受阻或无法读取。检查不代表已经执行。`
        : `${inspectionTotal ? "Checking" : "Last check"} ${checked}/${members.length}: ${ready} meet local launch prerequisites, ${unverified} runtimes unverified, ${blocked} blocked or unreadable. Inspection does not mean execution.`}</p> : null}
      <ul className="goal-team-bindings">{members.map(member => {
        const tone = checkTone(checks[member.id], Boolean(checkErrors[member.id]));
        const records = memberRecords.get(member.id)!;
        return <li key={member.id} data-check={tone}>
          <div className="goal-team-member-head">
            <span className="goal-team-avatar" aria-hidden="true">{member.agent_id.slice(0, 1).toUpperCase()}</span>
            <strong>{member.agent_id}</strong>
            <span className="goal-team-check-pill"><StateIcon state={tone}/>{zh ? CHECK_LABELS[tone].zh : CHECK_LABELS[tone].en}</span>
            <button type="button" className="goal-team-icon-button" disabled={busy} aria-label={zh ? "重新检查此成员" : "Recheck this member"} title={zh ? "重新检查此成员" : "Recheck this member"} onClick={() => void inspect(member.id)}><RotateCcw aria-hidden="true" size={15}/></button>
          </div>
          {checks[member.id] ? <DelegationPreflightStatus check={checks[member.id]} zh={zh}/> : null}
          {checkErrors[member.id] ? <p role="alert">{zh ? "启动条件无法核验" : "Prerequisites unavailable"} · {checkErrors[member.id]}</p> : null}
          {page ? records.length
            ? <ul className="goal-team-records" aria-label={zh ? `${member.agent_id} 的委派记录` : `${member.agent_id} delegation records`}>{records.map(row => renderRecord(row, false))}</ul>
            : <span className="goal-team-records-empty">{zh ? "本页没有它的委派记录" : "No records for this member on this page"}</span> : null}
          <details><summary>{zh ? "任务与执行配置" : "Task and execution details"}</summary><code>{member.todo_id}</code>{checks[member.id]?.executor?.profile ? <code>{checks[member.id]?.executor?.profile}</code> : null}
            {records.map(row => <code key={row.record_id}>{row.operation_id ?? row.record_id}</code>)}</details>
        </li>;
      })}</ul>
      <div className="goal-team-work-actions"><strong>{zh ? "此协调身份的持久工作" : "Durable work for this coordinator"}</strong>
        <span><button type="button" disabled={busy} onClick={() => {setChecks({}); setCheckErrors({}); void read();}}>{zh ? "重新核验" : "Refresh"}</button>
        {page?.has_more && page.next_cursor ? <button type="button" disabled={busy} onClick={() => void read(page.next_cursor!)}>{zh ? "下一页" : "Next page"}</button> : null}</span></div>
      {busy ? <p role="status">{zh ? "正在读取当前事实…" : "Reading current facts…"}</p> : null}
      {error ? <p role="alert">{error}</p> : null}
      {page ? <>
        {!page.page_readback_complete ? <p className="goal-team-warning" role="status"><AlertTriangle aria-hidden="true" size={15}/>{zh ? "本页有无法核验的工作，请检查原请求；不要直接重新派工。" : "Some work cannot be verified. Reconcile the original request before redispatching."}</p> : null}
        {!items.length ? <p>{zh ? "此页没有委派记录；不代表整个团队没有工作或 Goal 已完成。" : "No records on this page; this does not establish an idle team or a completed Goal."}</p> : null}
        {unboundRecords.length ? <>
          <h3>{zh ? "其他记录" : "Other records"}</h3>
          <ul className="goal-team-records goal-team-operations">{unboundRecords.map(row => renderRecord(row, true))}</ul>
        </> : null}
      </> : null}
    </section>
  </div>;
}
