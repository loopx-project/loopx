import type { CSSProperties } from "react";
import { goalIdentity, hostSurfaceLabel, presentGoalActivity, type GoalActivity, type WorkspaceGoalExecution } from "./goal-activity";
import { useWorkspaceI18n, type WorkspaceMessageKey } from "./i18n";
import type { WorkspaceGoal } from "./personal-workspace-model";

type GoalActivitySubject = Pick<WorkspaceGoal, "activationState" | "execution" | "goalId" | "loadState" | "needsYou" | "state" | "title">;

export function relativeTime(value: string, locale: string): string | null {
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return null;
  const seconds = Math.round((then - Date.now()) / 1000);
  const format = new Intl.RelativeTimeFormat(locale, { numeric: "auto", style: "short" });
  if (Math.abs(seconds) < 60) return format.format(Math.min(0, seconds), "second");
  if (Math.abs(seconds) < 3600) return format.format(Math.round(seconds / 60), "minute");
  if (Math.abs(seconds) < 86_400) return format.format(Math.round(seconds / 3600), "hour");
  return format.format(Math.round(seconds / 86_400), "day");
}

function hostNames(execution: WorkspaceGoalExecution | undefined) {
  return execution && execution.kind !== "unknown" ? execution.hostSurfaces.map(hostSurfaceLabel).join(" / ") : "";
}

/** Secondary line for an observed turn: host, then recency or silence. */
export function useExecutionDetail(execution: WorkspaceGoalExecution | undefined): string[] {
  const { locale, t } = useWorkspaceI18n();
  if (execution?.kind !== "running") return [];
  const parts: string[] = [];
  const hosts = hostNames(execution);
  if (hosts) parts.push(t("activity.viaHost", { host: hosts }));
  if (execution.lastActivityAt) {
    if (execution.quiet) {
      parts.push(t("activity.quiet", { minutes: Math.round((Date.now() - Date.parse(execution.lastActivityAt)) / 60_000) }));
    } else {
      const time = relativeTime(execution.lastActivityAt, locale);
      if (time) parts.push(time);
    }
  }
  return parts;
}

export function useGoalActivity(goal: GoalActivitySubject): GoalActivity & { text: string } {
  const { t } = useWorkspaceI18n();
  const activity = presentGoalActivity(goal);
  const detail = useExecutionDetail(goal.execution);
  const parts = [t(activity.labelKey as WorkspaceMessageKey)];
  if (activity.alsoKey) parts.push(t(activity.alsoKey as WorkspaceMessageKey, { host: hostNames(goal.execution) }));
  if (activity.labelKey === "activity.running") parts.push(...detail);
  return { ...activity, text: parts.join(" · ") };
}

export function GoalIdentityMark({ goal, size = "md" }: { goal: GoalActivitySubject; size?: "md" | "lg" }) {
  const activity = presentGoalActivity(goal);
  const identity = goalIdentity(goal.goalId, goal.title);
  return (
    <span
      aria-hidden="true"
      className={`personal-goal-mark is-${size}${goal.loadState ? "" : ` is-${activity.tone}`}${activity.live && !goal.loadState ? " is-live" : ""}`}
      style={{ "--goal-hue": identity.hue } as CSSProperties}
    >
      <span className="personal-goal-mark-glyph">{identity.glyph}</span>
    </span>
  );
}

export function GoalActivityChip({ goal }: { goal: GoalActivitySubject }) {
  const activity = useGoalActivity(goal);
  return (
    <span className={`personal-goal-activity-chip is-${activity.tone}${activity.live ? " is-live" : ""}`} title={activity.text}>
      <span aria-hidden="true" className="personal-goal-activity-dot" />
      <span>{activity.text}</span>
    </span>
  );
}
