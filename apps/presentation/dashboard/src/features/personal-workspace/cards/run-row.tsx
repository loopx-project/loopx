import { Bot, ChevronRight, LoaderCircle } from "lucide-react";

import type { WorkspaceRun } from "../personal-workspace-model";
import { useWorkspaceI18n, type WorkspaceMessageKey } from "../i18n";

const runStatusKey: Record<WorkspaceRun["status"], WorkspaceMessageKey> = {
  completed: "runs.completed",
  failed: "runs.failed",
  interrupted: "runs.interrupted",
  queued: "runs.queued",
  running: "runs.running",
  waiting: "runs.waiting",
};

export function RunRow({ onSelect, run, showGoal = true }: { onSelect: () => void; run: WorkspaceRun; showGoal?: boolean }) {
  const { t } = useWorkspaceI18n();
  return (
    <button aria-label={`${t("tasks.viewExecution")}：${run.title}`} className="personal-timeline-row personal-run-row" data-testid="personal-browse-row" onClick={onSelect} type="button">
      <span className="personal-row-icon is-run"><Bot size={18} /></span>
      <span className="personal-row-copy"><small>{showGoal ? `${run.goalTitle} · ` : ""}{run.agentLabel}</small><strong>{run.title}</strong>{run.latestActivity !== run.title ? <small>{run.latestActivity}</small> : null}</span>
      <span className={`personal-row-status is-${run.status}`}>
        {run.status === "running" ? <LoaderCircle className="personal-spin" size={14} /> : null}
        {t(runStatusKey[run.status])}
      </span>
      <ChevronRight size={17} />
    </button>
  );
}
