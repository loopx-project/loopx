import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Check, KeyRound, Languages, Palette, ServerCog, Settings2, SlidersHorizontal } from "lucide-react";

import type { WorkspaceLocale } from "./i18n";
import { useWorkspaceI18n } from "./i18n";
import { LarkSettingsPage } from "./lark-settings-page";
import { GoalCapabilitySettings } from "./goal-capability-settings";
import { MachineConfigurationSettings } from "./machine-configuration-settings";
import { OperatorCredentialSettings } from "./operator-credential-settings";
import type { PersonalWorkspaceCallbacks, WorkspaceGoal, WorkspaceGoalNotification } from "./personal-workspace-model";
import type { WorkspaceTheme } from "./workspace-theme";

type WorkspaceSettingsTab = "provider" | "machine" | "capabilities" | "lark" | "appearance" | "language";

const tabIcons: Record<WorkspaceSettingsTab, typeof Settings2> = {
  appearance: Palette,
  capabilities: SlidersHorizontal,
  language: Languages,
  lark: Settings2,
  machine: ServerCog,
  provider: KeyRound,
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
  const [tab, setTab] = useState<WorkspaceSettingsTab>(initialTab);
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
  const tabs: Array<{ key: WorkspaceSettingsTab; label: string }> = [
    ...(initialGoalId ? [{ key: "capabilities" as const, label: t("capabilities.title") }] : []),
    // The model provider is one machine decision (which endpoint and key the
    // operator credential holds); the capability catalog is another (which
    // machine defaults every Goal inherits). They answer different questions
    // and are edited on different surfaces, so they are separate categories.
    { key: "provider", label: t("settings.modelProvider") },
    { key: "machine", label: t("settings.globalCapabilities") },
    { key: "lark", label: "Lark" },
    { key: "appearance", label: t("settings.appearance") },
    { key: "language", label: t("settings.language") },
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
  const headings: Record<WorkspaceSettingsTab, { title: string }> = {
    appearance: {
      title: t("settings.appearance"),
    },
    capabilities: {
      title: t("capabilities.title"),
    },
    language: {
      title: t("settings.language"),
    },
    lark: {
      title: "Lark",
    },
    machine: {
      title: t("settings.globalCapabilities"),
    },
    provider: {
      title: t("settings.modelProvider"),
    },
  };
  const heading = headings[tab];

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
          {tabs.map((item) => {
            const Icon = tabIcons[item.key];
            return (
              <button aria-current={tab === item.key ? "page" : undefined} key={item.key} onClick={() => setTab(item.key)} type="button">
                <Icon size={17} />
                <span>
                  <strong>{item.label}</strong>
                </span>
              </button>
            );
          })}
        </nav>
      </aside>

      <main className="personal-settings-body">
        <header className="personal-settings-header">
          <div>
            <h1>{heading.title}</h1>
          </div>
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

        {tab === "machine" ? <MachineConfigurationSettings /> : null}
        {tab === "capabilities" ? (
          <GoalCapabilitySettings
            callbacks={callbacks}
            goalId={initialGoalId}
            notification={goalNotifications.find((row) => row.goalId === initialGoalId)}
            onChanged={onChanged}
          />
        ) : null}

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
