import type { CSSProperties } from "react";
import { goalHostSurfaces, goalIdentity, goalObservationGaps, hostSurfaceLabel, presentGoalActivity, type GoalActivity, type WorkspaceGoalExecution } from "./goal-activity";
import { ContextHelp } from "./context-help";
import { useWorkspaceI18n, type WorkspaceMessageKey } from "./i18n";
import type { WorkspaceGoal } from "./personal-workspace-model";

type GoalActivitySubject = Pick<WorkspaceGoal, "activationState" | "boundHostSurfaces" | "execution" | "goalId" | "hostThreadActivity" | "loadState" | "needsYou" | "state" | "title">;

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

function hostNames(surfaces: readonly string[]) {
  return surfaces.map(hostSurfaceLabel).join(" / ");
}

/** Secondary line for an observed turn: host, then claim time, recency or silence. */
export function useExecutionDetail(execution: WorkspaceGoalExecution | undefined): string[] {
  const { locale, t } = useWorkspaceI18n();
  if (execution?.kind !== "running") return [];
  const parts: string[] = [];
  const hosts = hostNames(execution.hostSurfaces);
  if (hosts) parts.push(t("activity.viaHost", { host: hosts }));
  // The claim and the execution are different facts and are reported as such:
  // a Goal can hold a silent managed turn and a freshly claimed attached one.
  if (execution.quiet && execution.lastActivityAt) {
    parts.push(t("activity.quiet", { minutes: Math.round((Date.now() - Date.parse(execution.lastActivityAt)) / 60_000) }));
  } else if (execution.lastActivityAt) {
    const time = relativeTime(execution.lastActivityAt, locale);
    if (time) parts.push(time);
  }
  if (execution.claimedAt) {
    const time = relativeTime(execution.claimedAt, locale);
    if (time) parts.push(t("activity.claimedAt", { time }));
  }
  return parts;
}

export function useGoalActivity(goal: GoalActivitySubject): GoalActivity & {
  text: string;
  windowBadge: { state: "stale" | "missing"; text: string } | null;
} {
  const { t } = useWorkspaceI18n();
  const activity = presentGoalActivity(goal);
  const detail = useExecutionDetail(goal.execution);
  const gaps = goalObservationGaps(goal);
  const parts = [t(activity.labelKey as WorkspaceMessageKey)];
  if (activity.alsoKey && !(gaps.unavailable && activity.alsoKey === "activity.executionUnknown")) parts.push(t(activity.alsoKey as WorkspaceMessageKey, { host: hostNames(goalHostSurfaces(goal)) }));
  if (activity.labelKey === "activity.running" || activity.labelKey === "activity.hostClaimed") parts.push(...detail);
  const windowBadge = goal.activationState === "stopped" || (activity.labelKey === "activity.observationUnavailable" && gaps.unavailable)
    || (!gaps.missing && !gaps.stale && !gaps.incomplete) ? null : {
    state: gaps.stale ? "stale" as const : "missing" as const,
    text: gaps.unavailable ? t("activity.observationUnavailable")
      : gaps.missing || gaps.stale ? t("activity.observationGaps", { count: gaps.missing + gaps.stale })
        : t("activity.observationIncomplete"),
  };
  return { ...activity, text: parts.join(" · "), windowBadge };
}

const observationReasons: Readonly<Record<string, WorkspaceMessageKey>> = {
  unsupported_host: "activity.reason.unsupportedHost",
  store_unavailable: "activity.reason.storeUnavailable",
  thread_not_found: "activity.reason.threadNotFound",
  record_unrecognized: "activity.reason.recordUnrecognized",
  no_turn_marker: "activity.reason.noTurnMarker",
  unparseable_observation: "activity.reason.unparseableObservation",
};

/** A sibling of the Goal navigation button so help never selects a Goal. */
export function GoalActivityHelp({ goal }: { goal: GoalActivitySubject }) {
  const { locale, t } = useWorkspaceI18n();
  const threads = goal.hostThreadActivity?.threads ?? [];
  if (!goal.hostThreadActivity || goal.activationState === "stopped" || goal.loadState) return null;
  return <ContextHelp label={t("activity.observationDetails", { goal: goal.title })}>
    {goal.hostThreadActivity?.completeness === "incomplete" ? <p>{t("activity.observationIncomplete")}</p> : null}
    <ul className="personal-goal-observation-list">{threads.map((thread, index) => {
      const reason = thread.reason || thread.deliveryWindow?.reason;
      const time = thread.lastEventAt ? relativeTime(thread.lastEventAt, locale) : null;
      const state = thread.state === "turn_open" ? "activity.observedOpen"
        : thread.state === "idle" ? "activity.observedEnded"
          : thread.state === "archived" ? "activity.observedArchived" : "activity.observationUnavailable";
      return <li key={`${thread.agentId}:${thread.hostSurface}:${index}`}>
        <strong>{thread.agentId || "Agent"} · {hostSurfaceLabel(thread.hostSurface)}</strong>
        <p>{t(state)}{time ? ` · ${time}` : ""}</p>
        {thread.deliveryWindow?.state === "stale" ? <p>{t("activity.hostWindowStale", {hours: Math.round((thread.deliveryWindow.ageHours ?? 0) * 10) / 10})}</p> : null}
        {reason && observationReasons[reason] ? <p>{t(observationReasons[reason])}</p>
          : thread.state === "unknown" ? <p>{t("activity.reason.recordUnrecognized")}</p> : null}
      </li>;
    })}</ul>
  </ContextHelp>;
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
      {activity.windowBadge ? <span className={`personal-goal-window-badge is-${activity.windowBadge.state}`}>{activity.windowBadge.text}</span> : null}
      <span>{activity.text}</span>
    </span>
  );
}
