import { AlertCircle, ChevronRight } from "lucide-react";

import type { WorkspaceAttention } from "../personal-workspace-model";
import { localizedAttentionAge, useWorkspaceI18n } from "../i18n";

export function AttentionRow({ attention, onSelect }: { attention: WorkspaceAttention; onSelect: () => void }) {
  const { t } = useWorkspaceI18n();
  const age = localizedAttentionAge(attention.updatedAt, t);
  const requester = attention.details?.createdBy;
  return (
    <button className="personal-timeline-row personal-attention-row" data-testid="personal-browse-row" onClick={onSelect} type="button">
      <span className="personal-row-icon is-attention"><AlertCircle size={18} /></span>
      <span className="personal-row-copy">
        <small>{attention.goalTitle ?? attention.goalId} · {t("home.lane.needsYou")} · {t(requester ? "attentionDetail.requestedBy" : "attentionDetail.requesterUnknown", { agent: requester ?? "" })}{age ? ` · ${t("tasks.waitingAge", { age })}` : ""}</small>
        <strong>{attention.text}</strong>
      </span>
      <span className={`personal-priority-dot is-${attention.priority ?? (attention.blocking ? "high" : "medium")}`} />
      <span className={`personal-row-status ${attention.blocking ? "is-blocking" : "is-pending"}`}>{attention.blocking ? t("tasks.blocked") : t("tasks.pending")}</span>
      <ChevronRight size={17} />
    </button>
  );
}
