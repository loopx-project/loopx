import { useEffect, useEffectEvent, useRef, useState } from "react";
import type { GoalDraft } from "../../../../../../loopx/control_plane/collaboration/goal_draft.js";
import type { WorkspaceActionPreview } from "./personal-workspace-model";
import {goalDraftReady} from "./goal-create-request";
import { useWorkspaceI18n } from "./i18n";
import "./goal-draft-card.css";

export function GoalDraftCard({ draft, draftId, preparationContext = "", onReview, onApply, onSuggest }: {
  draft: GoalDraft;
  draftId: string;
  preparationContext?: string;
  onReview?: (draft: GoalDraft, edit?: boolean, draftId?: string) => Promise<WorkspaceActionPreview | void>;
  onApply?: (proposal: WorkspaceActionPreview) => Promise<WorkspaceActionPreview>;
  onSuggest?: (text: string) => void;
}) {
  const { locale, t } = useWorkspaceI18n();
  const zh = locale === "zh-CN";
  const pending = useRef(false);
  const card = useRef<HTMLElement>(null);
  const [visible, setVisible] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [prepared, setPrepared] = useState<{ key: string; proposal: WorkspaceActionPreview }>();
  const [preparing, setPreparing] = useState(false);
  const [preparationAttempt, setPreparationAttempt] = useState(0);
  const ready = goalDraftReady(draft);
  const draftKey = JSON.stringify([draftId, draft, preparationContext]);
  const proposal = prepared?.key === draftKey ? prepared.proposal : undefined;
  const prepare = useEffectEvent(async () => onReview?.(draft, false, draftId));
  const preparesCreation = ready && visible && Boolean(onReview && onApply);
  useEffect(() => {
    if (visible || !card.current) return;
    const observer = new IntersectionObserver(([entry]) => {
      if (!entry?.isIntersecting) return;
      observer.disconnect();
      setVisible(true);
    }, {root: card.current.closest(".personal-channel-scroll")});
    observer.observe(card.current);
    return () => observer.disconnect();
  }, [visible]);
  useEffect(() => {
    let current = true;
    setPrepared(undefined);
    setError("");
    if (!preparesCreation) { setPreparing(false); return; }
    setPreparing(true);
    void prepare().then(result => {
      if (!current) return;
      if (result?.actionKind === "goal.create") setPrepared({ key: draftKey, proposal: result });
      else setError(zh ? "无法准备创建，请重试。" : "Could not prepare creation. Please retry.");
    }).catch(failure => {
      if (current) setError(failure instanceof Error ? failure.message : String(failure));
    }).finally(() => { if (current) setPreparing(false); });
    return () => { current = false; };
  }, [draftKey, preparesCreation, preparationAttempt]);
  const created = proposal?.status === "applied" && proposal.reviewPlan?.interaction === "completed";
  async function review(edit = false) {
    if (!onReview || pending.current) return;
    pending.current = true;
    setBusy(true);
    setError("");
    try { await onReview(draft, edit, draftId); }
    catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { pending.current = false; setBusy(false); }
  }
  async function create() {
    if (!onApply || pending.current) return;
    if (!proposal) { setPreparationAttempt(attempt => attempt + 1); return; }
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await onApply(proposal);
      setPrepared(current => current?.key === draftKey ? { key: draftKey, proposal: result } : current);
    }
    catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { pending.current = false; setBusy(false); }
  }
  return <section ref={card} className="personal-goal-draft" aria-label={zh ? "目标草稿" : "Goal draft"}>
    <strong>{zh ? "目标草稿" : "Goal draft"}</strong>
    <dl>{[
      [zh ? "目标" : "Objective", draft.objective],
      [zh ? "完成标准" : "Completion criteria", draft.completion_criteria],
      [zh ? "执行边界" : "Execution boundary", draft.execution_boundary],
    ].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value || (zh ? "待补充" : "Not specified")}</dd></div>)}</dl>
    {proposal ? <dl>{proposal.fields.filter(field => field.key === "agent_id" || field.key === "workspace_ref")
      .map(field => <div key={field.key}><dt>{field.label}</dt><dd>{field.value}</dd></div>)}</dl> : null}
    {draft.question ? <p>{draft.question}</p> : null}
    {onSuggest && draft.options.length ? <div className="personal-goal-draft-options">
      {draft.options.map(option => <button type="button" key={option} onClick={() => onSuggest(option)}>{option}</button>)}
      <small>{zh ? "点选后可修改再发送，也可以直接输入。" : "Choose a reply to edit before sending, or type your own."}</small>
    </div> : null}
    {error ? <p role="alert">{error}</p> : null}
    {proposal?.gate ? <p role="alert">{proposal.gate.summary}</p> : null}
    {proposal?.errorMessage ? <p role="alert">{proposal.reviewPlan
      ? t(`actionReview.${proposal.reviewPlan.reason}`) : proposal.errorMessage}</p> : null}
    <footer>
      {onReview ? <>
        {ready && !created ? <button type="button" disabled={busy} onClick={() => void review(true)}>{zh ? "修改" : "Edit"}</button> : null}
        {preparesCreation ? <button type="button" disabled={busy || preparing || created} onClick={() => void create()}>
          {created ? (zh ? "已创建" : "Created") : busy || preparing ? (zh ? "正在准备…" : "Preparing…")
            : !proposal ? (zh ? "重新准备" : "Retry preparation") : proposal.reviewPlan?.canApply ? proposal.status === "error" ? (zh ? "重试创建" : "Retry creation")
              : (zh ? "创建并开始" : "Create and start") : (zh ? "查看并处理" : "Review and resolve")}
        </button> : <button type="button" disabled={busy} onClick={() => void review()}>{busy ? (zh ? "正在准备…" : "Preparing…") : ready ? (zh ? "预览创建" : "Preview creation") : (zh ? "补充目标" : "Refine goal")}</button>}
      </> : null}
    </footer>
  </section>;
}
