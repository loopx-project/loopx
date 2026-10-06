import {GoalTeamLineage} from "./goal-team-lineage";
import {GoalTeamEpisode} from "./goal-team-episode";
import {GoalTeamComparison} from "./goal-team-comparison";
import {TeamArtifactReport} from "./team-artifact-content";
import {useEffect, useRef, useState} from "react";
import {delegationStateLabel, readLoopXTeamWork, sendLoopXMessage, type DelegationReadback, type LoopXModeSnapshot} from "../../data/chat";

/** Owner-only artifact readback. Text is evidence, never rendered as executable markup. */
export function GoalTeamEvidence({sessionId, operationId, zh, canMessage, ingress, onInspect}: {
  sessionId: string; operationId: string; zh: boolean; canMessage: boolean;
  ingress: LoopXModeSnapshot["ingress"]; onInspect: (operationId: string) => void;
}) {
  const [result, setResult] = useState<DelegationReadback | null>(null);
  const [observedAt, setObservedAt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState("");
  const [receipt, setReceipt] = useState<{id: string; status: string} | null>(null);
  const submission = useRef<{id: string; text: string} | null>(null);
  const generation = useRef(0);
  useEffect(() => {
    void read();
    return () => {generation.current++;};
  }, [sessionId, operationId]);
  async function read() {
    const current = ++generation.current;
    setResult(null); setObservedAt(""); setError(""); setBusy(true);
    try {
      const value = await readLoopXTeamWork(sessionId, operationId);
      if (current === generation.current) {
        setResult(value); setObservedAt(new Date().toLocaleTimeString());
      }
    } catch (failure) {
      if (current === generation.current) setError(failure instanceof Error ? failure.message : String(failure));
    } finally {if (current === generation.current) setBusy(false);}
  }
  async function send() {
    if ((!result || !message.trim()) && !submission.current) return;
    // A retry after transport failure must carry the original identity and exact evidence.
    const attempt = submission.current ?? {id: crypto.randomUUID(), text: [
      `${zh ? "针对团队执行" : "Regarding team execution"}: ${operationId}`,
      ...(result?.artifacts ?? []).map(artifact => `${artifact.ref} · sha256:${artifact.sha256}`),
      message.trim(),
    ].join("\n")};
    submission.current = attempt;
    setSending(true); setSendError("");
    try {
      const value = await sendLoopXMessage(sessionId, attempt.text, "inbox", attempt.id);
      setReceipt({id: attempt.id, status: value.status});
    } catch (failure) {setSendError(failure instanceof Error ? failure.message : String(failure));}
    finally {setSending(false);}
  }
  const status = receipt ? ingress.find(row => row.client_ingress_id === receipt.id)?.status ?? receipt.status : "";
  const receiptLabel = status === "pending" ? (zh ? "已进入协调员收件箱，等待读取" : "In the coordinator inbox; awaiting read")
    : status === "delivered" ? (zh ? "已交给协调员；尚无应用回执" : "Delivered to coordinator; application not confirmed")
    : (zh ? "投递状态待核实" : "Delivery requires reconciliation");
  const hasCorrectionPath = Boolean(result?.dependencies?.some(link => link.relation === "revises")
    && result?.dependencies?.some(link => link.relation === "responds_to"));
  return <section className="goal-team-evidence" aria-label={zh ? "执行证据" : "Execution evidence"} aria-busy={busy}>
    <div className="goal-team-work-actions"><h3>{zh ? "执行证据" : "Execution evidence"}</h3>
      <button type="button" disabled={busy} onClick={() => void read()}>{zh ? "重新读取证据" : "Recheck evidence"}</button></div>
    {busy ? <p role="status">{zh ? "正在核验绑定、验收与文件…" : "Checking bindings, acceptance and files…"}</p> : null}
    {error ? <p role="alert">{zh ? "无法核验，已清除上次证据。" : "Cannot verify; previous evidence cleared."} {error}</p> : null}
    {result ? <>
      <p role="status"><strong>{result.agent_id} · {delegationStateLabel(result, zh)}</strong>{" · "}{observedAt}</p>
      <p>{zh ? "按需读取的当前观察，不是持续在线状态；验收不代表协调员已采用。" : "An on-demand observation, not continuous liveness; acceptance does not establish coordinator adoption."}</p>
      <GoalTeamEpisode sessionId={sessionId} result={result} zh={zh} onInspect={onInspect} onObservation={value => {
        setResult(value); setObservedAt(value ? new Date().toLocaleTimeString() : "");
        setError(value ? "" : (zh ? "关联执行或版本已变化；请重新读取证据。" : "A linked execution or version changed; recheck the evidence."));
      }}/>
      <GoalTeamComparison sessionId={sessionId} result={result} zh={zh}/>
      {hasCorrectionPath ? <details className="goal-team-lineage-detail"><summary>{zh ? "版本与采用关系详情" : "Version and adoption details"}</summary>
        <GoalTeamLineage result={result} zh={zh} onInspect={onInspect}/></details>
        : <GoalTeamLineage result={result} zh={zh} onInspect={onInspect}/>}
      {result.error ? <p role="alert">{result.error}</p> : null}
      {result.status === "accepted" && !result.error && !result.recovery_required ? <details>
        <summary>{zh ? "本次验收依据" : "Current validation basis"}</summary>
        {result.validation ? <>
          <p>{result.validation.source === "goal_acceptance" ? (zh ? "Goal 验收规则" : "Goal acceptance rules")
            : (zh ? "任务验收规则" : "Task validation rules")}{" · "}
            {result.validation.check_count} {zh ? "项检查" : "checks"}{" · "}
            {result.validation.pinned_file_count} {zh ? "项文件版本固定" : "file pins"}</p>
          <p>{zh ? "本次读取重跑了当前规则，并核对产物版本。规则标识不证明独立复核者或异议已解决。"
            : "This read reran the current rules and checked output versions. The rule identity does not attest an independent reviewer or resolve an objection."}</p>
          <code>{result.validation.basis_sha256}</code>
        </> : <p>{zh ? "此运行时未提供验收依据标识。" : "This runtime did not provide the validation basis identity."}</p>}
      </details> : null}
      {result.status === "accepted" && !result.error && !result.recovery_required && result.artifacts?.length ? result.artifacts.map(artifact => <div key={`${artifact.ref}:${artifact.sha256}`}>
        <TeamArtifactReport artifact={artifact} zh={zh}/>
        <details><summary>{zh ? "版本与来源标识" : "Version and source identifiers"}</summary>
          <code>{operationId}</code><code>{result.request_id}</code><code>{result.todo_id}</code>
        </details>
      </div>) : <p>{zh ? "本次读取没有可展示的已验收产物。" : "No accepted artifact is available in this readback."}</p>}
      <form onSubmit={event => {event.preventDefault(); void send();}}>
        <label>{zh ? "向协调员反馈此执行" : "Send feedback about this execution"}
          <textarea value={message} maxLength={6000} rows={3} disabled={Boolean(submission.current)} onChange={event => setMessage(event.target.value)}/>
        </label>
        <p>{zh ? "附上执行标识及当前产物版本，交给原协调员收件箱；不会直接改写任务或中断成员。" : "Includes the operation and observed artifact versions in the original coordinator inbox; does not change tasks or interrupt members."}</p>
        {!canMessage ? <p>{zh ? "协调员未在执行或当前观察不可用；恢复执行后可发送。" : "Coordinator inactive or observation unavailable; resume execution to send."}</p> : null}
        <button type="submit" disabled={!canMessage || sending || Boolean(receipt) || !message.trim()}>{sending ? (zh ? "正在投递…" : "Sending…") : sendError ? (zh ? "重试同一条反馈" : "Retry this feedback") : (zh ? "发送反馈" : "Send feedback")}</button>
      </form>
    </> : null}
    {sendError ? <p role="alert">{zh ? "未确认投递，请重试同一条反馈，避免重复发送。" : "Delivery not confirmed; retry the same feedback to avoid duplicates."} {sendError}</p> : null}
    {receipt ? <p role="status">{receiptLabel}</p> : null}
  </section>;
}
