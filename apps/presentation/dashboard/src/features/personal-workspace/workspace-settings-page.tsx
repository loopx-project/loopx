import {GoalAcceptanceContractSection} from "./goal-acceptance-contract";
import {GoalObjectiveSettings} from "./goal-objective-settings";
import {goalAcceptanceContractCopy} from "./delivery-review-copy";
import { useEffect, useRef, useState } from "react";
import type {SettingsTarget} from "./workspace-settings-route";
import { ArrowLeft, Bot, Check, Clock3, KeyRound, Languages, Palette, ServerCog, Settings2 } from "lucide-react";

import type { WorkspaceLocale } from "./i18n";
import { useWorkspaceI18n } from "./i18n";
import { LarkSettingsPage } from "./lark-settings-page";
import { GoalCapabilitySettings } from "./goal-capability-settings";
import { GoalAmendmentSettings } from "./goal-amendment-settings";
import { GoalDirectionSettings } from "./goal-direction-settings";
import { GoalOwnershipSettings } from "./goal-ownership-settings";
import { GoalStorageSettings } from "./goal-storage-settings";
import { AutomationCadenceSettings } from "./automation-cadence-settings";
import { MachineConfigurationSettings } from "./machine-configuration-settings";
import { ConfigurationBackupSettings } from "./configuration-backup-settings";
import { OperatorCredentialSettings } from "./operator-credential-settings";
import { CodexAccountSettings } from "./codex-account-settings";
import type { PersonalWorkspaceCallbacks, WorkspaceGoal, WorkspaceGoalNotification } from "./personal-workspace-model";
import type { WorkspaceTheme } from "./workspace-theme";
import type { AttentionNotificationSupport } from "./attention-notifications";

const attentionNotificationSupportCopy = {
  default: "settings.attentionNotificationsDefault",
  denied: "settings.attentionNotificationsDenied",
  granted: "settings.attentionNotificationsGranted",
  unsupported: "settings.attentionNotificationsUnsupported",
} as const;

type WorkspaceSettingsTab = "ownership" | "steward" | "conversation" | "provider" | "machine" | "capabilities" | "cadence" | "lark" | "appearance" | "language";

type SettingsPage = Exclude<WorkspaceSettingsTab, "machine">;

const tabIcons: Record<SettingsPage, typeof Settings2> = {
  ownership: KeyRound,
  appearance: Palette,
  capabilities: ServerCog,
  cadence: Clock3,
  language: Languages,
  lark: Settings2,
  provider: KeyRound,
  steward: Bot,
  conversation: Bot,
};

export function WorkspaceSettingsPage({
  callbacks,
  scope = "machine",
  onNavigate,
  focusGoalConnection = false,
  goals,
  initialGoalId,
  initialTab = "steward",
  goalNotifications,
  onChanged,
  onClose,
  onThemeChange,
  theme,
  attentionNotifications,
  onAttentionNotificationsChange,
  onOpenOriginalConversation,
}: {
  callbacks: PersonalWorkspaceCallbacks;
  scope?: "machine" | "goal";
  onNavigate: (target: SettingsTarget) => void;
  focusGoalConnection?: boolean;
  goals: WorkspaceGoal[];
  initialGoalId?: string | null;
  initialTab?: WorkspaceSettingsTab;
  goalNotifications: WorkspaceGoalNotification[];
  onChanged: () => void;
  onClose: () => void;
  onThemeChange: (theme: WorkspaceTheme) => void;
  theme: WorkspaceTheme;
  attentionNotifications?: { enabled: boolean; support: AttentionNotificationSupport };
  onAttentionNotificationsChange?: (enabled: boolean) => void | Promise<void>;
  onOpenOriginalConversation?: (target: import("../../data/chat").BoundConversationTarget) => void;
}) {
  const { locale, setLocale, t } = useWorkspaceI18n();
  const machineTabs: Array<{ key: SettingsPage; label: string }> = [
    { key: "steward", label: t("settings.steward") },
    { key: "conversation", label: locale === "zh-CN" ? "对话与模型" : "Conversations and models" },
    { key: "provider", label: t("settings.modelProvider") },
    { key: "capabilities", label: t("settings.globalCapabilities") },
    { key: "lark", label: "Lark" },
    { key: "appearance", label: t("settings.appearance") },
    { key: "language", label: t("settings.language") },
  ];
  const goalTabs: Array<{ key: SettingsPage; label: string }> = [
    { key: "capabilities", label: t("settings.globalCapabilities") },
    { key: "cadence", label: t("cadence.title") },
    { key: "ownership", label: t("ownership.title") },
    { key: "lark", label: "Lark" },
  ];
  const tabs = scope === "goal" ? goalTabs : machineTabs;
  const requestedTab = initialTab === "machine" ? "capabilities" : initialTab;
  const tab = tabs.find(item => item.key === requestedTab)?.key ?? tabs[0].key;
  const selectedGoal = scope === "goal" ? goals.find(item => item.goalId === initialGoalId) : undefined;
  const editableGoal = selectedGoal && !selectedGoal.sourceAccess?.readOnly ? selectedGoal : undefined;
  const canonicalSettingsReady = editableGoal?.loadState !== "loading" && editableGoal?.loadError !== "authority";
  const [ownershipRefresh, setOwnershipRefresh] = useState(0);
  const navigate = (nextScope: "machine" | "goal", nextGoalId?: string, nextTab?: SettingsPage) => {
    onNavigate({kind: "settings", scope: nextScope,
      ...(nextScope === "goal" ? {goalId: nextGoalId ?? ""} : {}),
      tab: nextTab ?? (nextScope === "goal" ? "capabilities" : "steward")});
  };
  const localeOptions: Array<{ label: string; value: WorkspaceLocale }> = [
    {
      label: t("settings.languageEnglish"),
      value: "en",
    },
    {
      label: t("settings.languageSimplifiedChinese"),
      value: "zh-CN",
    },
  ];
  const headings: Record<SettingsPage, { title: string }> = {
    conversation: {title: locale === "zh-CN" ? "Goal 对话默认值" : "Goal conversation defaults"},
    ownership: {title: t("ownership.title")},
    appearance: {
      title: t("settings.appearance"),
    },
    capabilities: {
      title: t("settings.globalCapabilities"),
    },
    cadence: {
      title: t("cadence.title"),
    },
    language: {
      title: t("settings.language"),
    },
    lark: {
      title: "Lark",
    },
    provider: {
      title: t("settings.modelProvider"),
    },
    steward: {
      title: t("settings.steward"),
    },
  };
  const heading = headings[tab];
  const bodyRef = useRef<HTMLElement>(null);
  useEffect(() => { if (bodyRef.current) bodyRef.current.scrollTop = 0; }, [scope, initialGoalId, tab]);
  const scopeHint = scope === "machine" ? {
    steward: "",
    conversation: t("settings.effect.conversation"),
    provider: t("settings.effect.provider"),
    capabilities: t("settings.effect.capabilities"),
    lark: t("settings.effect.larkMachine"),
    appearance: t("settings.effect.appearance"),
    language: t("settings.effect.language"),
    cadence: "", ownership: "",
  }[tab] : tab === "lark" ? t("settings.effect.larkGoal") : "";

  return (
    <section aria-label={t("settings.title")} className="personal-settings-page" data-pw-theme={theme}>
      <aside className="personal-settings-sidebar">
        <button autoFocus className="personal-settings-back" onClick={onClose} type="button">
          <ArrowLeft size={17} />
          <span>{t("settings.back")}</span>
        </button>
        <div className="personal-settings-title">
          <strong>{t("settings.title")}</strong>
        </div>
        <fieldset className="personal-settings-scopes">
          <legend>{t("settings.scope")}</legend>
          {(["machine", "goal"] as const).map(value => <label key={value}>
            <input type="radio" name="settings-scope" checked={scope === value}
              onChange={() => navigate(value, undefined, machineTabs.some(item => item.key === tab) && goalTabs.some(item => item.key === tab) ? tab : undefined)} />
            {t(`settings.scope.${value}`)}
          </label>)}
        </fieldset>
        <label className="personal-settings-mobile-category">
          <span>{t("settings.categories")}</span>
          <select value={tab} onChange={event => navigate(scope, initialGoalId ?? undefined, event.target.value as SettingsPage)}>
            {tabs.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}
          </select>
        </label>
        <nav aria-label={t("settings.categories")} className="personal-settings-tabs">
          <div className="personal-settings-tab-group">
            {tabs.map(item => {
              const Icon = tabIcons[item.key];
              return <button aria-current={tab === item.key ? "page" : undefined} key={item.key}
                onClick={() => navigate(scope, initialGoalId ?? undefined, item.key)} type="button">
                <Icon size={17} /><span><strong>{item.label}</strong></span>
              </button>;
            })}
          </div>
        </nav>
      </aside>

      <main className="personal-settings-body" ref={bodyRef}>
        <header className="personal-settings-header">
          <div className="personal-settings-heading">
            <h1>{heading.title}</h1>
            {scope === "machine" ? <span className="personal-settings-goal-target">{t("settings.scope.machine")}</span>
              : <label className="personal-settings-goal-choice personal-settings-goal-target">
                <span>{t("settings.targetGoal")}</span>
                <select aria-label={t("settings.targetGoal")} value={initialGoalId ?? ""}
                  onChange={event => navigate("goal", event.target.value, tab)}>
                  <option value="">{t("capabilities.chooseGoal")}</option>
                  {initialGoalId && !selectedGoal ? <option value={initialGoalId} disabled>{t("settings.goalUnavailable")}</option> : null}
                  {goals.map(goal => <option key={goal.goalId} value={goal.goalId}>
                    {goal.title}{goal.sourceAccess ? ` · ${goal.sourceAccess.label}` : ""}{goals.filter(item => item.title === goal.title).length > 1 ? ` · ${goal.goalId}` : ""}
                  </option>)}
                </select>
              </label>}
          </div>
          {scopeHint ? <p className="personal-settings-effect">{scopeHint}</p> : null}
        </header>
        {scope === "goal" && initialGoalId && !editableGoal ? <p role="status" className="personal-settings-unavailable">
          {selectedGoal?.sourceAccess ? t("settings.goalReadOnly", {source: selectedGoal.sourceAccess.label})
            : initialGoalId ? t("settings.goalUnavailable") : t("capabilities.chooseGoal")}
        </p> : null}
        {tab === "lark" && (scope === "machine" || editableGoal) ? (
          <LarkSettingsPage
            onOpenOriginalConversation={onOpenOriginalConversation}
            embedded
            focusGoalConnection={focusGoalConnection}
            key={`${scope}:${initialGoalId ?? ""}`}
            goalScope={scope === "goal" ? editableGoal?.goalId : undefined}
            goals={scope === "goal" && editableGoal ? [editableGoal] : goals.filter(goal => !goal.sourceAccess?.readOnly)}
            initialGoalId={scope === "goal" ? initialGoalId : undefined}
            onChanged={onChanged}
            onClose={onClose}
          />
        ) : null}

        {tab === "provider" ? (
          <div className="personal-provider-settings">
            <CodexAccountSettings />
            <OperatorCredentialSettings />
          </div>
        ) : null}

        {tab === "steward" ? <MachineConfigurationSettings
          goals={goals}
          onChanged={onChanged}
          onOpenProviderSettings={() => navigate("machine", undefined, "provider")}
          section="steward"
        /> : null}
        {tab === "conversation" ? <MachineConfigurationSettings onChanged={onChanged} section="conversation" /> : null}
        {tab === "capabilities" && scope === "machine" ? <MachineConfigurationSettings onChanged={onChanged} section="other" /> : null}
        {tab === "capabilities" && editableGoal ? (
          <GoalCapabilitySettings
            key={editableGoal.goalId}
            callbacks={callbacks}
            goalId={editableGoal.goalId}
            notification={goalNotifications.find((row) => row.goalId === editableGoal.goalId)}
            onChanged={onChanged}
          />
        ) : null}
        {tab === "ownership" && editableGoal ? <div key={editableGoal.goalId}>
              {canonicalSettingsReady ? <GoalOwnershipSettings goalId={editableGoal.goalId} onChanged={onChanged} refreshKey={ownershipRefresh} /> : null}
              <GoalStorageSettings goalId={editableGoal.goalId} onChanged={() => {
                setOwnershipRefresh(value => value + 1);
                onChanged();
              }} />
              {canonicalSettingsReady ? <><GoalAmendmentSettings goalId={editableGoal.goalId} />
              <GoalObjectiveSettings goalId={editableGoal.goalId} />
              <GoalDirectionSettings goalId={editableGoal.goalId} />
              <GoalAcceptanceContractSection goalId={editableGoal.goalId} current canManage management copy={goalAcceptanceContractCopy[locale]} /></> : null}
        </div> : null}
        {tab === "capabilities" && (scope === "machine" || editableGoal) ? <ConfigurationBackupSettings key={`${scope}:${initialGoalId ?? ""}`} goalId={scope === "goal" ? editableGoal!.goalId : null} /> : null}
        {tab === "cadence" && editableGoal ? <AutomationCadenceSettings key={editableGoal.goalId} goal={editableGoal} /> : null}

        {tab === "appearance" ? (
          <section className="personal-detail-card personal-appearance-settings">
            <div className="personal-settings-choice-group" role="radiogroup" aria-label={t("settings.workspaceTheme")}>
              <button aria-checked={theme === "loopx"} onClick={() => onThemeChange("loopx")} role="radio" type="button">
                <span className="personal-settings-theme-swatch is-loopx" />
                <strong>{t("settings.themeLoopx")}</strong>
              </button>
              <button aria-checked={theme === "paper"} onClick={() => onThemeChange("paper")} role="radio" type="button">
                <span className="personal-settings-theme-swatch is-paper" />
                <strong>{t("settings.themeDefault")}</strong>
              </button>
              <button aria-checked={theme === "brutal"} onClick={() => onThemeChange("brutal")} role="radio" type="button">
                <span className="personal-settings-theme-swatch is-brutal" />
                <strong>{t("settings.themeHighContrast")}</strong>
              </button>
            </div>
            {attentionNotifications && onAttentionNotificationsChange ? (
              <div className="personal-attention-notifications" data-testid="attention-notification-settings">
                <h3>{t("settings.attentionNotifications")}</h3>
                <label className="personal-notification-toggle">
                  <input
                    checked={attentionNotifications.enabled}
                    disabled={attentionNotifications.support === "unsupported" || attentionNotifications.support === "denied"}
                    onChange={(event) => void onAttentionNotificationsChange(event.target.checked)}
                    type="checkbox"
                  />
                  <span>{t("settings.attentionNotificationsToggle")}</span>
                </label>
                <p className="personal-notification-hint">{t("settings.attentionNotificationsHint")}</p>
                <p className={attentionNotifications.support === "denied" ? "personal-notification-error" : "personal-notification-hint"} data-testid="attention-notification-permission" role="status">
                  {t(attentionNotificationSupportCopy[attentionNotifications.support])}
                </p>
              </div>
            ) : null}
          </section>
        ) : null}

        {tab === "language" ? (
          <section className="personal-settings-card">
            <div aria-label={t("settings.language")} className="personal-language-options" role="radiogroup">
              {localeOptions.map((option) => (
                <button
                  aria-checked={locale === option.value}
                  className={locale === option.value ? "is-selected" : ""}
                  key={option.value}
                  onClick={() => setLocale(option.value)}
                  role="radio"
                  type="button"
                >
                  <span>
                    <strong>{option.label}</strong>
                  </span>
                  {locale === option.value ? <Check aria-hidden size={17} /> : null}
                </button>
              ))}
            </div>
          </section>
        ) : null}
      </main>
    </section>
  );
}
