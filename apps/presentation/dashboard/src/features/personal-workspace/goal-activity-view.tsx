import type { CSSProperties } from "react";
import { goalIdentity, presentGoalActivity, type GoalActivity } from "./goal-activity";
import { useWorkspaceI18n, type WorkspaceMessageKey } from "./i18n";
import type { WorkspaceGoal } from "./personal-workspace-model";

type GoalActivitySubject = Pick<WorkspaceGoal, "activationState" | "execution" | "goalId" | "loadState" | "needsYou" | "state" | "title">;

function relativeTime(value: string, locale: string): string | null {
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return null;
  const seconds = Math.round((then - Date.now()) / 1000);
  const format = new Intl.RelativeTimeFormat(locale, { numeric: "auto", style: "short" });
  if (Math.abs(seconds) < 60) return format.format(Math.min(0, seconds), "second");
  if (Math.abs(seconds) < 3600) return format.format(Math.round(seconds / 60), "minute");
  if (Math.abs(seconds) < 86_400) return format.format(Math.round(seconds / 3600), "hour");
  return format.format(Math.round(seconds / 86_400), "day");
}

export function useGoalActivity(goal: GoalActivitySubject): GoalActivity & { text: string } {
  const { locale, t } = useWorkspaceI18n();
  const activity = presentGoalActivity(goal);
  const parts = [t(activity.labelKey as WorkspaceMessageKey)];
  if (activity.alsoKey) parts.push(t(activity.alsoKey as WorkspaceMessageKey));
  if (activity.labelKey === "activity.running" && goal.execution?.kind === "running" && goal.execution.lastActivityAt) {
    const time = relativeTime(goal.execution.lastActivityAt, locale);
    if (time) parts.push(time);
  }
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
    <span className={`personal-goal-activity-chip is-${activity.tone}${activity.live ? " is-live" : ""}`}>
      <span aria-hidden="true" className="personal-goal-activity-dot" />
      <span>{activity.text}</span>
    </span>
  );
}
