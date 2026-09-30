import { localizedAttentionAge, useWorkspaceI18n } from "./i18n";
import { MarkdownText } from "./markdown";
import type { WorkspaceAttention } from "./personal-workspace-model";

export function AttentionDetailCard({ item, successor, onSelect }: {
  item: WorkspaceAttention;
  successor?: WorkspaceAttention;
  onSelect?: (item: WorkspaceAttention) => void;
}) {
  const { t } = useWorkspaceI18n();
  const detail = item.details;
  const age = localizedAttentionAge(item.updatedAt, t);
  const lifecycle = detail?.lifecycle ?? "unknown";
  return <section className="personal-detail-card personal-attention-brief" aria-label={t("attentionDetail.title")}>
    <div className="personal-attention-meta">
      <span>{t(`attentionDetail.${lifecycle}`)}</span>
      {item.priority ? <span>{item.priority}</span> : null}
      {age ? <span>{t("tasks.waitingAge", { age })}</span> : null}
    </div>
    <h3>{t(detail?.requestText ? "attentionDetail.request" : "attentionDetail.summary")}</h3>
    {detail?.requestText ? <MarkdownText text={detail.requestText} /> : <>
      <p className="personal-attention-missing" role="status">{t("attentionDetail.summaryOnly")}</p>
      <MarkdownText text={item.text} />
    </>}
    <h4>{t("drawer.reason")}</h4>
    <MarkdownText text={detail?.reason ?? item.explanation ?? t("attentionDetail.unknownReason")} />
    <h4>{t("drawer.evidence")}</h4>
    <MarkdownText text={detail?.evidence ?? item.evidence ?? t("drawer.decisionDefaultEvidence")} />
    {successor && onSelect ? <button className="personal-secondary-action" onClick={() => onSelect(successor)} type="button">{t("attentionDetail.openReplacement")}</button> : null}
    <details className="personal-attention-identifiers">
      <summary>{t("drawer.advancedDiagnostics")}</summary>
      <dl>
        <div><dt>Todo</dt><dd>{item.todoId}</dd></div>
        {detail?.unblocksTodoId ? <div><dt>{t("attentionDetail.targetTodo")}</dt><dd>{detail.unblocksTodoId}</dd></div> : null}
        {detail?.blocksAgent ? <div><dt>{t("attentionDetail.targetAgent")}</dt><dd>{detail.blocksAgent}</dd></div> : null}
        {detail?.decisionScope ? <div><dt>{t("attentionDetail.scope")}</dt><dd>{detail.decisionScope.kind} · {detail.decisionScope.granularity} · {detail.decisionScope.scopeKey}</dd></div> : null}
        {detail?.supersededBy ? <div><dt>{t("attentionDetail.replacement")}</dt><dd>{detail.supersededBy}</dd></div> : null}
      </dl>
    </details>
    <p>{t("attentionDetail.boundary")}</p>
  </section>;
}
