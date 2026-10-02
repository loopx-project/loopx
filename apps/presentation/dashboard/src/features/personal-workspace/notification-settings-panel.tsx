import { useEffect, useState } from "react";
import { Bell, Check, Loader2, X } from "lucide-react";

import type {
  PersonalWorkspaceCallbacks,
  WorkspaceGoal,
  WorkspaceGoalNotification,
} from "./personal-workspace-model";
import { useWorkspaceI18n } from "./i18n";

type NotificationTarget = { enabled: boolean; provider: string; target_name: string };

type BindState =
  | { phase: "idle" }
  | { phase: "confirm"; target: string }
  | { phase: "busy" }
  | { phase: "error"; message: string };

export function GoalAutoNotifyToggle({
  callbacks,
  goalId,
  notification,
  onChanged,
  kind = "human_gate",
}: {
  callbacks: PersonalWorkspaceCallbacks;
  goalId: string;
  notification?: WorkspaceGoalNotification;
  onChanged: () => void;
  kind?: "human_gate" | "blocked_notice";
}) {
  const { t } = useWorkspaceI18n();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function toggle(autoNotify: boolean) {
    if (!callbacks.onToggleGoalAutoNotify) return;
    setBusy(true);
    setError(null);
    try {
      const result = await callbacks.onToggleGoalAutoNotify({ autoNotify, goalId, kind });
      if (!result.ok) {
        setError(result.public_summary ?? result.blocker ?? t("notifications.setupFailed"));
        return;
      }
      onChanged();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("notifications.setupFailed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <label className="personal-notification-toggle">
        <input
          checked={kind === "blocked_notice" ? notification?.blockedNoticeAutoNotifyEnabled ?? false : notification?.humanGateAutoNotifyEnabled ?? false}
          disabled={busy || notification?.configured !== true || !callbacks.onToggleGoalAutoNotify}
          onChange={(event) => void toggle(event.target.checked)}
          type="checkbox"
        />
        <span>{t(kind === "blocked_notice" ? "notifications.blockedAutoNotify" : "notifications.autoNotify")}</span>
        {busy ? <Loader2 aria-hidden className="is-spinning" size={14} /> : null}
      </label>
      {kind === "human_gate" ? <p className="personal-notification-hint">{t("notifications.stewardHint")}</p> : null}
      {kind === "human_gate" && (notification?.stewardNoticeDelivery?.pending_count ?? 0) > 0 ? (
        <p className="personal-notification-error" role="status">{t("notifications.stewardPending", { count: notification!.stewardNoticeDelivery!.pending_count })}</p>
      ) : null}
      {error ? <p className="personal-notification-error" role="alert">{error}</p> : null}
    </>
  );
}

function GoalNotificationRow({
  callbacks,
  goal,
  notification,
  targets,
  onChanged,
}: {
  callbacks: PersonalWorkspaceCallbacks;
  goal: WorkspaceGoal;
  notification?: WorkspaceGoalNotification;
  targets: NotificationTarget[];
  onChanged: () => void;
}) {
  const { t } = useWorkspaceI18n();
  const [bindState, setBindState] = useState<BindState>({ phase: "idle" });
  const [selectedTarget, setSelectedTarget] = useState(targets[0]?.target_name ?? "");

  async function bind(execute: boolean) {
    if (!callbacks.onSetupGoalChannel || !selectedTarget) return;
    setBindState({ phase: "busy" });
    try {
      const result = await callbacks.onSetupGoalChannel({
        execute,
        goalId: goal.goalId,
        target: selectedTarget,
      });
      if (!result.ok) {
        setBindState({ phase: "error", message: result.public_summary ?? result.blocker ?? t("notifications.bindFailed") });
        return;
      }
      if (!execute) {
        setBindState({ phase: "confirm", target: selectedTarget });
        return;
      }
      setBindState({ phase: "idle" });
      onChanged();
    } catch (error) {
      setBindState({ phase: "error", message: error instanceof Error ? error.message : t("notifications.bindFailed") });
    }
  }

  const configured = notification?.configured === true;

  return (
    <li className="personal-notification-row">
      <div className="personal-notification-row-head">
        <strong>{goal.title}</strong>
        <span className={`personal-notification-badge ${configured ? "is-on" : "is-off"}`}>
          {configured ? (notification?.enabled ? t("notifications.bound") : t("notifications.disabled")) : t("notifications.notBound")}
        </span>
      </div>
      {configured ? (
        <>
          <div className="personal-notification-meta">
            {notification?.targetRef ? <span>{t("notifications.group", { target: notification.targetRef })}</span> : null}
            <span>{t("notifications.sentCount", { count: notification?.receiptCount ?? 0 })}</span>
            {notification?.lastNotifiedAt ? <span>{t("notifications.recent", { time: notification.lastNotifiedAt })}</span> : null}
          </div>
          <GoalAutoNotifyToggle
            callbacks={callbacks}
            goalId={goal.goalId}
            notification={notification}
            onChanged={onChanged}
          />

        </>
      ) : targets.length === 0 ? (
        <p className="personal-notification-hint">
          {t("notifications.noTargets")}
          <code>loopx goal-channel target add</code>
        </p>
      ) : bindState.phase === "confirm" ? (
        <div className="personal-notification-confirm">
          <p>{t("notifications.bindConfirm", { goal: goal.title, target: bindState.target })}</p>
          <div className="personal-notification-actions">
            <button className="personal-primary-action" onClick={() => void bind(true)} type="button">
              <Check size={15} />{t("notifications.confirmBind")}
            </button>
            <button className="personal-secondary-action" onClick={() => setBindState({ phase: "idle" })} type="button">
              <X size={15} />{t("common.cancel")}
            </button>
          </div>
        </div>
      ) : (
        <div className="personal-notification-bind">
          <select
            aria-label={`${t("notifications.bind")} · ${goal.title}`}
            onChange={(event) => setSelectedTarget(event.target.value)}
            value={selectedTarget}
          >
            {targets.map((target) => (
              <option key={target.target_name} value={target.target_name}>{target.target_name}</option>
            ))}
          </select>
          <button
            className="personal-secondary-action"
            disabled={!selectedTarget || bindState.phase === "busy"}
            onClick={() => void bind(false)}
            type="button"
          >
            {bindState.phase === "busy" ? <Loader2 aria-hidden className="is-spinning" size={15} /> : <Bell size={15} />}
            {t("notifications.bind")}
          </button>
        </div>
      )}
      {bindState.phase === "error" ? <p className="personal-notification-error">{bindState.message}</p> : null}
    </li>
  );
}

export function NotificationSettingsPanel({
  callbacks,
  goalNotifications,
  goals,
  onChanged,
}: {
  callbacks: PersonalWorkspaceCallbacks;
  goalNotifications: WorkspaceGoalNotification[];
  goals: WorkspaceGoal[];
  onChanged: () => void;
}) {
  const { t } = useWorkspaceI18n();
  const [targets, setTargets] = useState<NotificationTarget[] | null>(null);
  const [targetsError, setTargetsError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (!callbacks.onFetchNotificationTargets) {
      setTargets([]);
      return;
    }
    callbacks.onFetchNotificationTargets()
      .then((items) => { if (!cancelled) setTargets(items); })
      .catch((error: unknown) => {
        if (!cancelled) {
          setTargets([]);
          setTargetsError(error instanceof Error ? error.message : t("notifications.loadFailed"));
        }
      });
    return () => { cancelled = true; };
  }, [callbacks, t]);

  return (
    <section className="personal-detail-card personal-notification-settings">
      <small>{t("notifications.title")}</small>
      <h3>{t("notifications.settings")}</h3>
      {targetsError ? <p className="personal-notification-error">{targetsError}</p> : null}
      <ul className="personal-notification-list">
        {goals.map((goal) => (
          <GoalNotificationRow
            callbacks={callbacks}
            goal={goal}
            key={goal.goalId}
            notification={goalNotifications.find((row) => row.goalId === goal.goalId)}
            onChanged={onChanged}
            targets={targets ?? []}
          />
        ))}
      </ul>
    </section>
  );
}
