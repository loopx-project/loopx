import { useEffect, useState } from "react";
import { CalendarClock, Check, MessageCircleQuestion, MessageSquareReply, MoreHorizontal, Square, X } from "lucide-react";
import type { DecisionOutcome } from "../../../../../../loopx/control_plane/todos/user_completion_types.js";
import { canDecideAttention, canHandleUserAction, canReviewAttention, nextMorningResumeWhen } from "./attention-details";
import { useWorkspaceI18n } from "./i18n";
import type { PersonalWorkspaceCallbacks, WorkspaceActionPreviewRequest, WorkspaceAttention } from "./personal-workspace-model";

type PreviewState = { status: "idle" } | { status: "pending" } | { status: "error"; message: string };

/** Owner affordances for one "needs you" item. Every write is a typed preview
 * the owner confirms once; the Todo owner validates it and decides effects. */
export function AttentionActions({ item, readOnly, callbacks, fallbackAgentId }: {
  item: WorkspaceAttention;
  readOnly: boolean;
  callbacks: PersonalWorkspaceCallbacks;
  fallbackAgentId: string;
}) {
  const { t } = useWorkspaceI18n();
  const [preview, setPreview] = useState<PreviewState>({ status: "idle" });
  useEffect(() => setPreview({ status: "idle" }), [item.goalId, item.todoId]);
  // Hard-lease Goals attribute the write to the Agent the request unblocks.
  const agentId = item.details?.blocksAgent ?? fallbackAgentId;
  const context = { goal_id: item.goalId, kind: "todo", todo_id: item.todoId };
  const key = (suffix: string) => `workspace-attention-${item.todoId}-${suffix}-${Date.now().toString(36)}`;

  async function requestPreview(request: WorkspaceActionPreviewRequest) {
    if (preview.status === "pending" || !callbacks.onPreviewAction) return;
    setPreview({ status: "pending" });
    try {
      await callbacks.onPreviewAction(request);
      setPreview({ status: "idle" });
    } catch (error) {
      setPreview({ status: "error", message: error instanceof Error ? error.message : String(error) });
    }
  }

  function previewDecision(attention: WorkspaceAttention, decision: DecisionOutcome) {
    if (readOnly || !canDecideAttention(attention)) return;
    void requestPreview({
      actionKind: "gate.resolve", context, idempotencyKey: key(`decision-${decision}`),
      normalizedParameters: { agent_id: agentId, goal_id: attention.goalId, decision, todo_id: attention.todoId },
      summary: attention.text,
    });
  }

  function previewUserAction(attention: WorkspaceAttention, operation: "complete" | "defer" | "cancel") {
    if (readOnly || !canHandleUserAction(attention)) return;
    if (operation === "cancel") {
      // Cancelling a user_action is the one decision outcome its owner admits;
      // it closes only this reminder and never resumes or approves other work.
      void requestPreview({
        actionKind: "gate.resolve", context, idempotencyKey: key("cancel"),
        normalizedParameters: { agent_id: agentId, goal_id: attention.goalId, decision: "cancel",
          note: t("drawer.userActionCancelNote"), todo_id: attention.todoId },
        summary: t("drawer.userActionCancelSummary", { task: attention.text }),
      });
      return;
    }
    void requestPreview({
      actionKind: "todo.update", context, idempotencyKey: key(operation),
      normalizedParameters: { agent_id: agentId, goal_id: attention.goalId, operation, todo_id: attention.todoId,
        ...(operation === "defer" ? { resume_when: nextMorningResumeWhen(new Date()) } : {}) },
      summary: t(operation === "defer" ? "drawer.userActionDeferSummary" : "drawer.userActionCompleteSummary", { task: attention.text }),
    });
  }

  const pending = preview.status === "pending";
  const feedback = preview.status === "error"
    ? <p className="personal-proposal-state is-error" role="alert"><X size={16} /><span>{t("drawer.attentionPreviewFailed", { error: preview.message })}</span></p>
    : pending ? <p className="personal-proposal-state" role="status">{t("drawer.attentionPreparing")}</p> : null;
  const reply = callbacks.onReplyToAttention
    ? <button className="personal-secondary-action" onClick={() => callbacks.onReplyToAttention?.(item, "reply")} type="button"><MessageSquareReply size={16} />{t("drawer.attentionReply")}</button>
    : null;
  const explain = (label: string) => callbacks.onExplainDecision
    ? <button onClick={() => void callbacks.onExplainDecision?.(item)} type="button"><MessageCircleQuestion size={16} />{label}</button>
    : null;

  if (readOnly) {
    return canReviewAttention(item) ? <p className="personal-proposal-explainer" role="status">{t("drawer.attentionReadOnly")}</p> : null;
  }
  if (canDecideAttention(item)) {
    return <>
      <div className="personal-decision-bar" role="group" aria-label={t("drawer.decisionGroup")}>
        <button className="personal-primary-action" disabled={pending} onClick={() => previewDecision(item, "approve")} type="button"><Check size={17} />{t("drawer.decisionApprove")}</button>
        <button className="personal-secondary-action" disabled={pending} onClick={() => previewDecision(item, "reject")} type="button"><X size={17} />{t("drawer.decisionReject")}</button>
      </div>
      {feedback}
      <details className="personal-compact-menu">
        <summary><MoreHorizontal size={17} />{t("drawer.decisionMore")}</summary>
        <div>
          {explain(t("drawer.explainDecision"))}
          <button disabled={pending} onClick={() => previewDecision(item, "cancel")} type="button"><Square size={16} />{t("drawer.decisionCancel")}</button>
        </div>
      </details>
    </>;
  }
  if (canHandleUserAction(item)) {
    return <>
      <p className="personal-proposal-explainer">{t(item.details?.unblocksTodoId ? "drawer.userActionUnblocks" : "drawer.userActionReminder")}</p>
      <div className="personal-decision-bar" role="group" aria-label={t("drawer.userActionGroup")}>
        <button className="personal-primary-action" disabled={pending} onClick={() => previewUserAction(item, "complete")} type="button"><Check size={17} />{t("drawer.userActionComplete")}</button>
        {reply}
      </div>
      {feedback}
      <details className="personal-compact-menu">
        <summary><MoreHorizontal size={17} />{t("drawer.userActionMore")}</summary>
        <div>
          <button disabled={pending} onClick={() => previewUserAction(item, "defer")} type="button"><CalendarClock size={16} />{t("drawer.userActionDefer")}</button>
          <button disabled={pending} onClick={() => previewUserAction(item, "cancel")} type="button"><Square size={16} />{t("drawer.userActionCancel")}</button>
          {explain(t("drawer.askAgentExplain"))}
        </div>
      </details>
    </>;
  }
  if (!canReviewAttention(item)) return null;
  const note = item.decisionSource === "run_operator_gate" ? "drawer.decisionRunGate"
    : item.details?.interaction === "user_action" ? "drawer.userActionMissingTodo" : "drawer.decisionNotGate";
  return <>
    <p className="personal-proposal-explainer">{t(note)}</p>
    <div className="personal-decision-bar" role="group" aria-label={t("drawer.userActionGroup")}>
      {reply}
      {callbacks.onExplainDecision ? <button className="personal-secondary-action" onClick={() => void callbacks.onExplainDecision?.(item)} type="button"><MessageCircleQuestion size={16} />{t("drawer.askAgentExplain")}</button> : null}
    </div>
  </>;
}
