import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Bot, Check, Clock3, KeyRound, Languages, Palette, ServerCog, Settings2 } from "lucide-react";

import type { WorkspaceLocale } from "./i18n";
import { useWorkspaceI18n } from "./i18n";
import { LarkSettingsPage } from "./lark-settings-page";
import { GoalCapabilitySettings } from "./goal-capability-settings";
import { AutomationCadenceSettings } from "./automation-cadence-settings";
import { MachineConfigurationSettings } from "./machine-configuration-settings";
import { OperatorCredentialSettings } from "./operator-credential-settings";
import type { PersonalWorkspaceCallbacks, WorkspaceGoal, WorkspaceGoalNotification } from "./personal-workspace-model";
import type { WorkspaceTheme } from "./workspace-theme";

type WorkspaceSettingsTab = "steward" | "provider" | "machine" | "capabilities" | "cadence" | "lark" | "appearance" | "language";

type SettingsPage = Exclude<WorkspaceSettingsTab, "machine">;

const tabIcons: Record<SettingsPage, typeof Settings2> = {
  appearance: Palette,
  capabilities: ServerCog,
  cadence: Clock3,
  language: Languages,
  lark: Settings2,
  provider: KeyRound,
  steward: Bot,
};

export function WorkspaceSettingsPage({
  callbacks,
  focusGoalConnection = false,
  goals,
  initialGoalId,
  initialTab = "lark",
  goalNotifications,
  onChanged,
  onClose,
  onThemeChange,
  theme,
}: {
  callbacks: PersonalWorkspaceCallbacks;
  focusGoalConnection?: boolean;
  goals: WorkspaceGoal[];
  initialGoalId?: string | null;
  initialTab?: WorkspaceSettingsTab;
  goalNotifications: WorkspaceGoalNotification[];
  onChanged: () => void;
  onClose: () => void;
  onThemeChange: (theme: WorkspaceTheme) => void;
  theme: WorkspaceTheme;
}) {
  const { locale, setLocale, t } = useWorkspaceI18n();
  // Preserve existing Goal-settings links while presenting one capability entry.
  const [tab, setTab] = useState<SettingsPage>(initialTab === "machine" ? "capabilities" : initialTab);
  const [capabilityScope, setCapabilityScope] = useState<"machine" | "goal">(initialTab === "capabilities" ? "goal" : "machine");
  const [capabilityGoalId, setCapabilityGoalId] = useState(initialGoalId ?? "");
  const tabsRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const navigation = tabsRef.current;
    if (!navigation) return;
    const revealSelectedTab = () => {
      if (navigation.scrollWidth <= navigation.clientWidth) return;
      const selected = navigation.querySelector('[aria-current="page"]');
      if (!selected) return;
      const parent = navigation.getBoundingClientRect();
      const child = selected.getBoundingClientRect();
      const delta = child.left < parent.left ? child.left - parent.left
        : child.right > parent.right ? child.right - parent.right : 0;
      if (delta) navigation.scrollLeft += delta;
    };
    revealSelectedTab();
    const observer = new ResizeObserver(revealSelectedTab);
    observer.observe(navigation);
    return () => observer.disconnect();
  }, [tab]);
  const tabGroups: Array<{ label: string; tabs: Array<{ key: SettingsPage; label: string }> }> = [
    {
      label: t("settings.agentGroup"),
      tabs: [
        { key: "steward", label: t("settings.steward") },
        // The model provider is one machine decision (which endpoint and key the
        // operator credential holds); the capability catalog is another (which
        // machine defaults every Goal inherits). They answer different questions
        // and are edited on different surfaces, so they are separate categories.
        { key: "provider", label: t("settings.modelProvider") },
        { key: "capabilities", label: t("settings.globalCapabilities") },
        ...(initialGoalId ? [{ key: "cadence" as const, label: t("cadence.title") }] : []),
      ],
    },
    {
      label: t("settings.workspaceGroup"),
      tabs: [
        { key: "lark", label: "Lark" },
        { key: "appearance", label: t("settings.appearance") },
        { key: "language", label: t("settings.language") },
      ],
    },
  ];
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
  const selectedGoal = goals.find((item) => item.goalId === initialGoalId);
  const goalSettingsTarget = tab === "cadence" && initialGoalId
    ? selectedGoal?.title || initialGoalId
    : null;

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
        <nav aria-label={t("settings.categories")} className="personal-settings-tabs" ref={tabsRef}>
          {tabGroups.map((group) => <div className="personal-settings-tab-group" key={group.label}>
            <span className="personal-settings-tab-group-label">{group.label}</span>
            {group.tabs.map((item) => {
              const Icon = tabIcons[item.key];
              return (
                <button aria-current={tab === item.key ? "page" : undefined} key={item.key} onClick={() => setTab(item.key)} type="button">
                  <Icon size={17} />
                  <span><strong>{item.label}</strong></span>
                </button>
              );
            })}
          </div>)}
        </nav>
      </aside>

      <main className="personal-settings-body">
        <header className={`personal-settings-header${tab === "capabilities" ? " has-capability-target" : ""}`}>
          <div className="personal-settings-heading">
            <h1>{heading.title}</h1>
            {goalSettingsTarget ? (
              <span className="personal-settings-goal-target" title={`${goalSettingsTarget} · ${initialGoalId}`}>
                <span>Goal</span>
                <strong>{goalSettingsTarget}</strong>
              </span>
            ) : null}
          </div>
          {tab === "capabilities" ? <div className="personal-capability-target">
            <fieldset className="personal-capability-scope-options">
              <legend>{t("settings.capabilityScope")}</legend>
              {(["machine", "goal"] as const).map((scope) => <label key={scope}>
                <input type="radio" name="capability-scope" checked={capabilityScope === scope} onChange={() => setCapabilityScope(scope)} />
                {t(`settings.capabilityScope.${scope}`)}
              </label>)}
            </fieldset>
            {capabilityScope === "goal" ? <label className="personal-capability-goal-choice">
              <span>{t("settings.targetGoal")}</span>
              <select aria-label={t("settings.targetGoal")} value={capabilityGoalId} onChange={(event) => setCapabilityGoalId(event.target.value)}>
                <option value="">{t("capabilities.chooseGoal")}</option>
                {goals.map((goal) => <option key={goal.goalId} value={goal.goalId}>{goal.title} · {goal.goalId}</option>)}
              </select>
            </label> : null}
            <p>{t(`settings.capabilityScope.${capabilityScope}Description`)}</p>
          </div> : null}
        </header>
        {tab === "lark" ? (
          <LarkSettingsPage
            embedded
            focusGoalConnection={focusGoalConnection}
            goals={goals}
            initialGoalId={initialGoalId}
            onChanged={onChanged}
            onClose={onClose}
          />
        ) : null}

        {tab === "provider" ? (
          <div className="personal-provider-settings">
            <OperatorCredentialSettings />
          </div>
        ) : null}

        {tab === "steward" ? <MachineConfigurationSettings onChanged={onChanged} section="steward" /> : null}
        {tab === "capabilities" && capabilityScope === "machine" ? <MachineConfigurationSettings onChanged={onChanged} section="other" /> : null}
        {tab === "capabilities" && capabilityScope === "goal" ? (
          <GoalCapabilitySettings
            key={capabilityGoalId}
            callbacks={callbacks}
            goalId={capabilityGoalId}
            notification={goalNotifications.find((row) => row.goalId === capabilityGoalId)}
            onChanged={onChanged}
          />
        ) : null}
        {tab === "cadence" && selectedGoal ? <AutomationCadenceSettings key={selectedGoal.goalId} goal={selectedGoal} /> : null}

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
