import type {GoalCreationExecutionReview} from "../../../../../../loopx/control_plane/goals/creation_execution_review.ts";
import {useWorkspaceI18n} from "./i18n";

/** Display the canonical creation approval; UI prose never grants authority. */
export function GoalCreationExecution({execution}: {execution: GoalCreationExecutionReview}) {
  const {t} = useWorkspaceI18n();
  return <dl className="personal-goal-creation-execution">
    <div><dt>{t("proposal.field.permission")}</dt><dd>{t(execution.permission === "read_only"
      ? "proposal.execution.readOnly" : "proposal.execution.workspaceWrite")}</dd></div>
    <div><dt>{t("proposal.execution.writeScope")}</dt><dd>{execution.writeScope.length === 0
      ? t("proposal.execution.noWrites") : execution.writeScope.map(scope => scope === "**"
        ? t("proposal.execution.allWorkspaceFiles") : scope).join(" · ")}</dd></div>
  </dl>;
}
