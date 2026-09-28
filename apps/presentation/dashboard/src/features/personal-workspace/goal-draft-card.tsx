import { useRef, useState } from "react";
import type { GoalDraft } from "../../../../../../loopx/control_plane/collaboration/goal_draft.js";
import { useWorkspaceI18n } from "./i18n";
import "./goal-draft-card.css";

export function GoalDraftCard({ draft, draftId, onReview, onSuggest }: {
  draft: GoalDraft;
  draftId: string;
  onReview?: (draft: GoalDraft, edit?: boolean, draftId?: string) => Promise<void>;
  onSuggest?: (text: string) => void;
}) {
  const { locale } = useWorkspaceI18n();
  const zh = locale === "zh-CN";
  const pending = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const ready = !draft.question && Boolean(draft.completion_criteria.trim());
  async function review(edit = false) {
    if (!onReview || pending.current) return;
    pending.current = true;
    setBusy(true);
    setError("");
    try { await onReview(draft, edit, draftId); }
    catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { pending.current = false; setBusy(false); }
  }
  return <section className="personal-goal-draft" aria-label={zh ? "目标草稿" : "Goal draft"}>
    <strong>{zh ? "目标草稿" : "Goal draft"}</strong>
    <dl>{[
      [zh ? "目标" : "Objective", draft.objective],
      [zh ? "完成标准" : "Completion criteria", draft.completion_criteria],
      [zh ? "执行边界" : "Execution boundary", draft.execution_boundary],
    ].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value || (zh ? "待补充" : "Not specified")}</dd></div>)}</dl>
    {draft.question ? <p>{draft.question}</p> : null}
    {onSuggest && draft.options.length ? <div className="personal-goal-draft-options">
      {draft.options.map(option => <button type="button" key={option} onClick={() => onSuggest(option)}>{option}</button>)}
      <small>{zh ? "点选后可修改再发送，也可以直接输入。" : "Choose a reply to edit before sending, or type your own."}</small>
    </div> : null}
    {error ? <p role="alert">{error}</p> : null}
    <footer><span>{zh ? "尚未创建或启动" : "Not created or started"}</span>
      {onReview ? <>
        {ready ? <button type="button" disabled={busy} onClick={() => void review(true)}>{zh ? "修改" : "Edit"}</button> : null}
        <button type="button" disabled={busy} onClick={() => void review()}>{busy ? (zh ? "正在准备…" : "Preparing…") : ready ? (zh ? "预览创建" : "Preview creation") : (zh ? "补充目标" : "Refine goal")}</button>
      </> : null}
    </footer>
  </section>;
}
