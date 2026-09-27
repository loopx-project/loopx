import { useWorkspaceI18n } from "./i18n";
import type { WorkspaceAttention } from "./personal-workspace-model";

export function AttentionDetailCard({ item, successor, onSelect }: {
  item: WorkspaceAttention;
  successor?: WorkspaceAttention;
  onSelect?: (item: WorkspaceAttention) => void;
}) {
  const { t } = useWorkspaceI18n();
  const detail = item.details;
  return <section className="personal-detail-card" aria-label={t("attentionDetail.title")}>
    <h3>{t("attentionDetail.title")}</h3>
    {detail?.requestText && detail.requestText !== item.text ? <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{detail.requestText}</p> : null}
    <dl>
      <div><dt>{t("attentionDetail.request")}</dt><dd>{t(detail?.interaction === "decision" ? "attentionDetail.decision" : "attentionDetail.unknownRequest")}</dd></div>
      <div><dt>{t("common.status")}</dt><dd>{t(`attentionDetail.${detail?.lifecycle ?? "unknown"}`)}</dd></div>
      <div><dt>{t("drawer.reason")}</dt><dd>{detail?.reason ?? item.explanation ?? t("attentionDetail.unknownReason")}</dd></div>
      <div><dt>Todo</dt><dd>{item.todoId}</dd></div>
      <div><dt>{t("attentionDetail.targetTodo")}</dt><dd>{detail?.unblocksTodoId ?? t("attentionDetail.notProvided")}</dd></div>
      <div><dt>{t("attentionDetail.targetAgent")}</dt><dd>{detail?.blocksAgent ?? t("attentionDetail.notProvided")}</dd></div>
      <div><dt>{t("attentionDetail.scope")}</dt><dd>{detail?.decisionScope
        ? `${detail.decisionScope.kind} · ${detail.decisionScope.granularity} · ${detail.decisionScope.scopeKey}`
        : t("attentionDetail.notProvided")}</dd></div>
      <div><dt>{t("drawer.evidence")}</dt><dd>{detail?.evidence ?? item.evidence ?? t("drawer.decisionDefaultEvidence")}</dd></div>
    </dl>
    {detail?.supersededBy ? <p>{t("attentionDetail.replacement")}: {detail.supersededBy}</p> : null}
    {successor && onSelect ? <button className="personal-secondary-action" onClick={() => onSelect(successor)} type="button">{t("attentionDetail.openReplacement")}</button> : null}
    <p>{t("attentionDetail.boundary")}</p>
  </section>;
}
