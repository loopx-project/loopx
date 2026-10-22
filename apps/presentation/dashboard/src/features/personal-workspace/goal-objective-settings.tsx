import {useEffect, useRef, useState} from "react";
import {useQuery} from "@tanstack/react-query";
import {acceptanceRequest} from "../../data/goal-acceptance-actions";
import {previewTypedAction, applyTypedAction, type TypedActionProposal} from "../../data/chat";
import {useTypedActionReadback} from "../../data/use-typed-action-readback";
import {useWorkspaceI18n} from "./i18n";

/** One owner review surface for Agent-prepared and directly authored revisions.
 * Peer acknowledgement is performed by the peer, never by this settings page. */
export function GoalObjectiveSettings({goalId}: {goalId: string}) {
  const zh = useWorkspaceI18n().locale === "zh-CN";
  const t = (cn: string, en: string) => zh ? cn : en;
  const [open, setOpen] = useState(false), [objective, setObjective] = useState(""), [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const generation = useRef(0), flight = useRef(false);
  const read = useQuery({queryKey: ["goal-acceptance-readback", goalId], queryFn: ({signal}) => acceptanceRequest(goalId, undefined, signal),
    enabled: open, retry: false, refetchOnWindowFocus: "always"});
  const actions = useTypedActionReadback(!open, goalId);
  const current = read.data?.goal_id === goalId ? read.data.goal_acceptance_contract.goal_objective : undefined;
  const writable = open && !read.isError && !!read.data && !actions.isError && !busy;
  const proposals = (actions.data ?? []).filter(p => p.action_kind === "goal.update" && p.normalized_parameters.goal_id === goalId
    && typeof p.normalized_parameters.reason === "string" && ["preview_ready", "deferred", "applying", "failed"].includes(p.status));
  useEffect(() => {generation.current++; flight.current = false; setError(""); setBusy(false); setObjective(""); setReason("");
    return () => {generation.current++;};}, [goalId]);
  async function operate(proposal?: TypedActionProposal) {
    if (!writable || flight.current) return;
    const token = generation.current; flight.current = true; setBusy(true); setError("");
    try {
      const id = crypto.randomUUID();
      const result = proposal ? (await applyTypedAction(proposal.proposal_id)).proposal
        : await previewTypedAction({actionKind: "goal.update", context: {kind: "goal", goal_id: goalId},
          idempotencyKey: `objective-ui:${id}`, summary: objective.trim(), normalizedParameters: {
            goal_id: goalId, objective: objective.trim(), reason: reason.trim(), source_ref: `owner:${id}`, prepared_by: null}});
      if (token !== generation.current) return;
      await actions.acceptProposal(result);
      await read.refetch();
      if (proposal && result.status !== "applied") throw new Error(t("修订依据已变化，请重新审阅后提交。", "The revision basis changed; review a fresh proposal."));
      if (!proposal) {setObjective(""); setReason("");}
    } catch (cause) {
      if (token === generation.current) setError(cause instanceof Error ? cause.message : t("结果待确认，请读取原提案后重试。", "Outcome unknown; read and retry the original proposal."));
    } finally {if (token === generation.current) {flight.current = false; setBusy(false);}}
  }
  async function recover() {
    const token = generation.current;
    const [goalRead, proposalRead] = await Promise.all([read.refetch(), actions.refetch()]);
    if (token === generation.current && !goalRead.isError && !proposalRead.isError) setError("");
  }
  return <details className="personal-cadence-settings" open={open} onToggle={e => setOpen(e.currentTarget.open)}>
    <summary>{t("当前目标与修订", "Current objective and revisions")}</summary>
    {open ? <div className="personal-cadence-form">
      <p>{t("仅当工作方向实质变化，审阅 Agent 的修订或填写新目标。普通进展无需修订。原始承诺、验收和权限保留；每位 Agent 对新版本确认一次即可。", "For a material direction change, review an Agent's revision or enter the new objective. Routine progress needs no revision. Original commitments, acceptance and permissions remain; each Agent adopts a new revision once.")}</p>
      {current ? <div className="personal-cadence-readback"><strong>{t(`当前版本 ${current.revision}`, `Current revision ${current.revision}`)}</strong>
        <p>{current.objective}</p><p>{current.reason}</p>
        <details><summary>{t("原始目标", "Original objective")}</summary><p>{current.original_objective}</p></details>
        <ul>{current.peer_adoption.map(peer => <li key={peer.agent_id}>{peer.agent_id} · {peer.current ? t("已对齐当前版本", "Current revision adopted") : t("待自行对齐", "Own work review pending")}</li>)}</ul>
      </div> : read.isSuccess ? <p>{t("尚无生效修订，仍使用已记录的原始目标。", "No revision is effective; the recorded original objective remains in use.")}</p> : null}
      {proposals.map(proposal => <div className="personal-cadence-readback" key={proposal.proposal_id}>
        <strong>{String(proposal.normalized_parameters.objective)}</strong><p>{String(proposal.normalized_parameters.reason)}</p>
        <p>{t("生效后，各 Agent 分别确认；此操作不代替它们确认，也不代表验收通过。", "After apply, each Agent adopts independently. This action neither acknowledges for peers nor certifies acceptance.")}</p>
        <button type="button" disabled={!writable} onClick={() => void operate(proposal)}>{["applying", "failed"].includes(proposal.status) ? t("重试原修订", "Retry original revision") : t("确认本次修订", "Apply this revision")}</button>
      </div>)}
      <div className="personal-cadence-fields personal-capability-fields">
      <label>{t("新的当前目标", "New current objective")}<textarea value={objective} disabled={!writable} onChange={e => setObjective(e.target.value)}/></label>
      <label>{t("修订原因", "Reason for revision")}<textarea value={reason} disabled={!writable} onChange={e => setReason(e.target.value)}/></label>
      </div>
      <div className="personal-cadence-actions"><button type="button" disabled={!writable || !objective.trim() || !reason.trim()} onClick={() => void operate()}>{t("预览修订", "Preview revision")}</button>
        <button type="button" disabled={busy} onClick={() => void recover()}>{t("读取当前结果", "Read current result")}</button></div>
      {read.isError || actions.isError ? <p role="alert">{t("当前依据不可用，请重试读取。", "Current authority is unavailable; retry the read.")}</p> : null}
      {error ? <p role="alert">{error}</p> : null}
    </div> : null}
  </details>;
}
