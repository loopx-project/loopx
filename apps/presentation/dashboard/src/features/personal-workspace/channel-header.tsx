import { Bot, Eye, Menu, RefreshCw, SlidersHorizontal } from "lucide-react";

import { localizedGoalState, useWorkspaceI18n } from "./i18n";
import type { ManagerChannelBinding, ManagerRuntimeSessionReadback } from "../../data/chat";
import type { WorkspaceAgentOption, WorkspaceGoal, WorkspaceGoalTab } from "./personal-workspace-model";
import { WorkspaceSelect } from "./workspace-select";

export function ChannelHeader({
  agents,
  managerChannelBinding,
  managerChatOpen,
  managerRuntime,
  mobileNavigationOpen,
  onOpenGoalCapabilities,
  onOpenManagerChat,
  onRefresh,
  onOpenNavigation,
  onSelectGoalTab,
  onSelectAgent,
  onReturnManagerHome,
  refreshState,
  readOnlySourceLabel,
  selectedAgentId,
  selectedGoal,
  selectedGoalTab,
}: {
  agents: WorkspaceAgentOption[];
  managerChannelBinding?: ManagerChannelBinding | null;
  managerChatOpen?: boolean;
  managerRuntime?: ManagerRuntimeSessionReadback | null;
  mobileNavigationOpen?: boolean;
  onOpenGoalCapabilities?: () => void;
  onOpenManagerChat?: () => void;
  onRefresh?: () => void;
  onOpenNavigation?: () => void;
  onSelectGoalTab: (tab: WorkspaceGoalTab) => void;
  onSelectAgent: (agentId: string) => void;
  onReturnManagerHome?: () => void;
  refreshState?: "idle" | "loading" | "done" | "error";
  readOnlySourceLabel?: string;
  selectedAgentId: string;
  selectedGoal: WorkspaceGoal | null;
  selectedGoalTab: WorkspaceGoalTab;
}) {
  const { locale, t } = useWorkspaceI18n();
  // The chip reports the selected executor, whose credential pays for it, and
  // the resolved model, so an executor and a model that disagree are visible
  // instead of arriving as one silent configuration.
  const managerExecutionKindLabel = managerChannelBinding
    ? managerChannelBinding.executor_kind === "individual"
      ? t("header.managerExecutorKindIndividual")
      : managerChannelBinding.executor_kind === "managed"
        ? t("header.managerExecutorKindManaged")
        : t("header.managerExecutorKindRegistered")
    : null;
  const managerExecutionUnavailable = managerChannelBinding?.available === false;
  const managerOutputTokenBudget = managerChannelBinding?.output_token_budget;
  const managerOutputTokenBudgetLabel = managerOutputTokenBudget?.scope === "per_model_request"
    && managerOutputTokenBudget.valid
    && typeof managerOutputTokenBudget.max_tokens === "number"
    ? t("header.managerOutputTokenBudget", {
      tokens: new Intl.NumberFormat(locale).format(managerOutputTokenBudget.max_tokens),
    })
    : null;
  // Name the reason instead of one hardcoded host: the channel can hold the
  // managed host through its segment transport now, so "this channel needs
  // codex" would be both wrong and unactionable. An unknown reason stays
  // unclaimed rather than being rendered as a reason this build invented.
  const managerExecutionUnavailableReason = managerChannelBinding?.available === false
    ? managerChannelBinding.unavailable_reason
    : null;
  const managerExecutionUnavailableKey = managerExecutionUnavailableReason === "operator_credential_unconfigured"
    ? "header.managerExecutionUnavailableCredential"
    : managerExecutionUnavailableReason === "dsh_runtime_unavailable"
      ? "header.managerExecutionUnavailableRuntime"
      : managerExecutionUnavailableReason === "invalid_reasoning_effort"
        ? "header.managerExecutionUnavailableEffort"
        : managerExecutionUnavailableReason === "invalid_output_token_limit"
          ? "header.managerExecutionUnavailableOutputBudget"
        : "header.managerExecutionUnavailable";
  // The shipped default is one endpoint, so the chip names it and the one way
  // to move it; without this a steward the operator selected looks identical to
  // the one every machine runs, and the reason stays in the binding's typed
  // field instead of being invented here.
  const managerExecutionDefaultReason = managerChannelBinding
    && managerChannelBinding.executor_endpoint_source === "product_default"
    && managerChannelBinding.executor_endpoint_default_reason === "steward_channel_default"
    ? "header.managerEndpointStewardDefault"
    : null;

  const runtimeControl = readOnlySourceLabel ? (
          <span className="personal-read-only-source" title={t("header.readOnlySourceDescription", { source: readOnlySourceLabel })}><Eye size={15} />{readOnlySourceLabel}<small>{t("common.readOnly")}</small></span>
        ) : (
          <WorkspaceSelect
            ariaLabel={t("header.selectChatRuntime")}
            className="personal-agent-select"
            icon={<Bot size={16} />}
            onChange={onSelectAgent}
            options={agents.map((agent) => ({
              disabled: !agent.available,
              label: `${agent.label}${agent.available ? "" : ` · ${t("header.agentUnavailable")}`}`,
              value: agent.agentId,
            }))}
            prefixLabel={t("header.chatRuntime")}
            value={selectedAgentId}
          />
        );

  return (
    <header className="personal-channel-header" data-goal-selected={Boolean(selectedGoal)}>
      <button aria-expanded={mobileNavigationOpen ?? false} aria-label={t("header.openGoalNavigation")} className="personal-icon-button personal-mobile-menu" onClick={onOpenNavigation} type="button"><Menu size={18} /></button>
      <div className="personal-channel-title">
        <h1>{selectedGoal?.title ?? t("header.manager")}</h1>
        {selectedGoal && !selectedGoal.loadState && !["安静运行", "推进中"].includes(selectedGoal.state) ? <p>{localizedGoalState(selectedGoal.state, locale)}</p> : null}
        {!selectedGoal && managerChannelBinding ? (
          <p className="personal-manager-execution">
            <span className={managerExecutionUnavailable ? "personal-execution-chip is-unavailable" : "personal-execution-chip"}>
              <span className="personal-execution-chip-endpoint">{managerChannelBinding.executor_endpoint}</span>
              {managerExecutionKindLabel ? <span className="personal-execution-chip-kind">{managerExecutionKindLabel}</span> : null}
              <span className="personal-execution-chip-model">{managerChannelBinding.model}</span>
              {managerOutputTokenBudgetLabel ? <span className="personal-execution-chip-budget">{managerOutputTokenBudgetLabel}</span> : null}
            </span>
            {managerExecutionUnavailable ? (
              <span className="personal-execution-note">
                {t(managerExecutionUnavailableKey, {
                  executor: managerChannelBinding.executor_endpoint,
                  credential: managerChannelBinding.credential_env_var,
                })}
              </span>
            ) : null}
          </p>
        ) : null}
        {!selectedGoal && (managerRuntime || managerExecutionDefaultReason) ? <details className="personal-runtime-details" open={managerRuntime != null && managerRuntime.status !== "ready" ? true : undefined}><summary>{locale === "zh-CN" ? "运行环境" : "Execution environment"}</summary>
        {!selectedGoal && managerRuntime ? (
          <p>{managerRuntime.status === "ready"
            ? t("header.managerRuntime", {
              profile: managerRuntime.runtime_profile,
              sandbox: managerRuntime.sandbox,
            })
            : t("header.managerRuntimeFallback", {
              profile: managerRuntime.runtime_profile,
              sandbox: managerRuntime.sandbox,
            })}</p>
        ) : null}
            {managerExecutionDefaultReason && managerChannelBinding ? (
              <span className="personal-execution-rule-note">
                {t(managerExecutionDefaultReason, { executor: managerChannelBinding.executor_endpoint })}
              </span>
            ) : null}
        </details> : null}
        {selectedGoal?.loadState ? <p role="status">{t(selectedGoal.loadState === "error" ? "startup.goalError" : "startup.goalLoading")}</p> : null}
      </div>
      {selectedGoal ? (
        <div className="personal-goal-navigation">
        <nav aria-label={t("header.goalView")} className="personal-goal-tabs">
          <button aria-current={selectedGoalTab === "overview" ? "page" : undefined} onClick={() => onSelectGoalTab("overview")} type="button">{t("header.overview")}</button>
          <button aria-current={selectedGoalTab === "tasks" ? "page" : undefined} onClick={() => onSelectGoalTab("tasks")} type="button">{t("header.tasks")}</button>
          <button aria-current={selectedGoalTab === "chat" ? "page" : undefined} onClick={() => onSelectGoalTab("chat")} type="button">{t("header.chat")}</button>
          <button aria-current={selectedGoalTab === "files" ? "page" : undefined} onClick={() => onSelectGoalTab("files")} type="button">{t("header.files")}</button>
        </nav>
        {runtimeControl}
        </div>
      ) : (
        <nav aria-label={t("header.managerView")} className="personal-goal-tabs">
          <button aria-current={!managerChatOpen ? "page" : undefined} onClick={onReturnManagerHome} type="button">{t("header.managerOverview")}</button>
          <button aria-current={managerChatOpen ? "page" : undefined} onClick={onOpenManagerChat} type="button">{t("header.chat")}</button>
        </nav>
      )}
      <div className="personal-channel-actions">
        {selectedGoal && onOpenGoalCapabilities ? (
          <button aria-label={t("header.goalSettings")} title={t("header.goalSettingsDescription")} className="personal-icon-button personal-goal-settings-action" onClick={onOpenGoalCapabilities} type="button"><SlidersHorizontal aria-hidden size={17} /></button>
        ) : null}
        {!selectedGoal ? runtimeControl : null}
        {onRefresh ? (
          <span className={`personal-refresh-control is-${refreshState ?? "idle"}`}>
            {refreshState === "loading" ? <small>{t("header.refreshing")}</small> : refreshState === "done" ? <small>{t("header.refreshDone")}</small> : refreshState === "error" ? <small>{t("header.refreshFailed")}</small> : null}
            <button aria-label={refreshState === "loading" ? t("header.refreshing") : t("header.refresh")} className="personal-icon-button" disabled={refreshState === "loading"} onClick={onRefresh} type="button">
              <RefreshCw className={refreshState === "loading" ? "is-spinning" : undefined} size={17} />
            </button>
          </span>
        ) : null}
      </div>
    </header>
  );
}
