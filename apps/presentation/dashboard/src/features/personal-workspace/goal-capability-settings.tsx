import {ContextHelp} from "./context-help";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, CircleHelp, Code2, LoaderCircle, RefreshCw } from "lucide-react";

import {
  applyGoalConfiguration,
  fetchGoalConfiguration,
  previewGoalConfiguration,
  type CapabilityConfigurationCatalog,
  type GoalConfigurationInspection,
  type GoalConfigurationPreview,
  type GoalConfigurationPartialWrite,
} from "../../data/chat";
import { goalReplanCadenceEditorValue, goalReplanCadenceWriteValue, hasReplanCadenceOverride, parseEditableCapabilityJson, projectEditableCapabilityConfiguration, replanCadenceEditorDefaults } from "../../data/capability-configuration";
import { useWorkspaceI18n } from "./i18n";
import { CapabilityConfigurationFields } from "./capability-configuration-fields";
import { withReportScheduleTimezone } from "./periodic-report-schedule-field";
import {PeriodicReportSessionField} from "./periodic-report-session-field";
import { localizeCapability, localizedCapabilityFieldCopy } from "./capability-localization";
import { orderCapabilitiesForPresentation, canEditCapability, CapabilityCatalogNavigation, CapabilityConfigurationSummary, CapabilityDetailHeader, CapabilityEditorStatus, CapabilityConfigurationHelp } from "./capability-workbench";
import { GoalAutoNotifyToggle } from "./notification-settings-panel";
import { DecisionContextWorkbench } from "./decision-context-workbench";
import { rewardMemoryBindingStatus, rewardMemoryScopeDraft, rewardMemoryWriteValue } from "./reward-memory-settings";
import {isMemoryCapability, memorySettingsTitle, MemorySettingsHeader} from "./memory-settings";
import type { PersonalWorkspaceCallbacks, WorkspaceGoalNotification } from "./personal-workspace-model";

type CapabilityCatalogProps = Readonly<{
  callbacks: PersonalWorkspaceCallbacks;
  catalog: CapabilityConfigurationCatalog;
  goalId: string;
  loading: boolean;
  onApplied: () => Promise<void>;
  notification?: WorkspaceGoalNotification;
  onNotificationChanged: () => void;
}>;

type CapabilityMutationState = Readonly<{
  busy: "preview" | "apply" | null;
  draft: Record<string, unknown>;
  cadenceOverride: boolean;
  horizonOverride: boolean;
  partialWrite: GoalConfigurationPartialWrite | null;
  preview: GoalConfigurationPreview | null;
}>;

function goalWriteDraft(selected: CapabilityConfigurationCatalog["capabilities"][number], draft: Record<string, unknown>, editorMode: "guided" | "json", cadenceOverride: boolean, horizonOverride: boolean) {
  const defaults = selected.capability_id === "todo_replan_cadence"
    ? editorMode === "json" ? undefined : replanCadenceEditorDefaults(selected.effective_configuration?.configuration, selected.default)
    : selected.default;
  const writable = projectEditableCapabilityConfiguration(selected.configuration_editor, draft, defaults);
  if (selected.capability_id === "reward_memory") return rewardMemoryWriteValue(writable);
  if (selected.capability_id === "todo_replan_cadence" && editorMode === "guided") {
    return goalReplanCadenceWriteValue(writable, cadenceOverride, horizonOverride);
  }
  if (selected.capability_id === "pull_request_review" && !Object.hasOwn(selected.current ?? {}, "owner_logins")
    && JSON.stringify(writable.owner_logins ?? []) === JSON.stringify(selected.effective_configuration?.configuration?.owner_logins ?? [])) {
    delete writable.owner_logins;
  }
  if (selected.capability_id === "pull_request_review"
    && !Object.hasOwn(selected.current ?? {}, "wait_for_ci") && !Object.hasOwn(selected.current ?? {}, "review_order")) {
    const effective = selected.effective_configuration?.configuration;
    if (effective && writable.wait_for_ci === effective.wait_for_ci && writable.review_order === effective.review_order) {
      delete writable.wait_for_ci;
      delete writable.review_order;
    }
  }
  return writable;
}

function useCapabilityMutation({ goalId, loading, onApplied, selected, t }: Readonly<{
  goalId: string;
  loading: boolean;
  onApplied: () => Promise<void>;
  selected: CapabilityConfigurationCatalog["capabilities"][number] | undefined;
  t: ReturnType<typeof useWorkspaceI18n>["t"];
}>) {
  const [mutation, setMutation] = useState<CapabilityMutationState>({
    busy: null,
    draft: {},
    cadenceOverride: false,
    horizonOverride: false,
    partialWrite: null,
    preview: null,
  });
  const [error, setError] = useState<string | null>(null);
  const [editorMode, setEditorMode] = useState<"guided" | "json">("guided");
  const [jsonDraft, setJsonDraft] = useState("");
  // Preserve the inspected Agent across fresh readbacks, never across Goals.
  // Config paths and mutation previews still reset from the native snapshot.
  const memoryScope = useRef({goalId, agentId: ""});
  const parsedJson = useMemo(() => selected
    ? parseEditableCapabilityJson(selected.configuration_editor, jsonDraft) : null, [selected, jsonDraft]);
  const jsonValid = editorMode === "guided" || parsedJson !== null;

  // Initialize the new capability's draft before it can receive input. A
  // post-paint reset can otherwise erase the first toggle after selection.
  useLayoutEffect(() => {
    setEditorMode("guided");
    setJsonDraft("");
    const current = (selected?.capability_id === "pull_request_review" ? selected.effective_configuration?.configuration : selected?.current)
      ?? selected?.effective_configuration?.configuration ?? selected?.default;
    setMutation({
      busy: null,
      draft: projectEditableCapabilityConfiguration(
        selected?.configuration_editor ?? { fields: [] },
        selected?.capability_id === "todo_replan_cadence"
          ? goalReplanCadenceEditorValue(selected.current, selected.effective_configuration?.configuration)
          : selected?.capability_id === "reward_memory"
            ? rewardMemoryScopeDraft(selected.current, memoryScope.current.goalId === goalId ? memoryScope.current.agentId : "")
          : current,
        selected?.default,
      ),
      cadenceOverride: hasReplanCadenceOverride(selected?.current),
      horizonOverride: Object.hasOwn(selected?.current ?? {}, "planning_horizon_goal"),
      partialWrite: null,
      preview: null,
    });
    setError(null);
  }, [selected, goalId]);

  async function preview(configuration: Record<string, unknown> | null) {
    if (!selected || loading || mutation.busy || !jsonValid) return;
    setMutation((current) => ({ ...current, busy: "preview", partialWrite: null }));
    setError(null);
    try {
      const nextPreview = await previewGoalConfiguration(goalId, selected.capability_id, configuration === null ? null : goalWriteDraft(selected, configuration, editorMode, mutation.cadenceOverride, mutation.horizonOverride));
      setMutation((current) => ({ ...current, preview: nextPreview }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("capabilities.previewFailed"));
    } finally {
      setMutation((current) => ({ ...current, busy: null }));
    }
  }

  async function apply() {
    if (!selected || loading || !mutation.preview || mutation.busy || !jsonValid) return;
    setMutation((current) => ({ ...current, busy: "apply" }));
    setError(null);
    try {
      const writableDraft = goalWriteDraft(selected, mutation.draft, editorMode, mutation.cadenceOverride, mutation.horizonOverride);
      const result = await applyGoalConfiguration(
        goalId,
        selected.capability_id,
        mutation.preview.action === "delete" ? null : writableDraft,
        mutation.preview.plan_revision,
      );
      setMutation((current) => ({
        ...current,
        partialWrite: result.status === "partial_write" ? result : null,
        preview: null,
      }));
      if (result.status !== "partial_write") await onApplied();
    } catch (reason) {
      setMutation((current) => ({ ...current, preview: null }));
      setError(reason instanceof Error ? reason.message : t("capabilities.applyFailed"));
    } finally {
      setMutation((current) => ({ ...current, busy: null }));
    }
  }

  function changeDraft(key: string, value: unknown) {
    if (loading || mutation.busy) return;
    if (selected?.capability_id === "reward_memory" && key === "agent_id") {
      memoryScope.current = {goalId, agentId: typeof value === "string" ? value : ""};
    }
    setMutation((current) => ({
      ...current,
      draft: selected?.capability_id === "periodic_report"
        ? withReportScheduleTimezone(current.draft, key, value)
        : selected?.capability_id === "reward_memory" && key === "agent_id"
          ? rewardMemoryScopeDraft(selected.current, value)
        : { ...current.draft, [key]: value },
      cadenceOverride: current.cadenceOverride || (selected?.capability_id === "todo_replan_cadence" && (key === "count_unit" || key === "count")),
      horizonOverride: current.horizonOverride || (selected?.capability_id === "todo_replan_cadence" && key === "planning_horizon_goal"),
      preview: null,
    }));
    setError(null);
  }

  function changeJson(text: string) {
    if (!selected || loading || mutation.busy) return;
    setJsonDraft(text);
    const parsed = parseEditableCapabilityJson(selected.configuration_editor, text);
    if (selected.capability_id === "reward_memory" && parsed) {
      memoryScope.current = {goalId, agentId: typeof parsed.agent_id === "string" ? parsed.agent_id : ""};
    }
    setMutation((current) => ({ ...current, preview: null, draft: parsed
      ? projectEditableCapabilityConfiguration(selected.configuration_editor, parsed,
        selected.capability_id === "todo_replan_cadence" ? undefined : selected.default)
      : current.draft,
      cadenceOverride: selected.capability_id === "todo_replan_cadence" && parsed
        ? hasReplanCadenceOverride(parsed) : current.cadenceOverride,
      horizonOverride: selected.capability_id === "todo_replan_cadence" && parsed
        ? Object.hasOwn(parsed, "planning_horizon_goal") : current.horizonOverride,
    }));
    setError(null);
  }

  function changeMode() {
    if (loading || mutation.busy || !jsonValid) return;
    if (editorMode === "guided" && selected) {
      const jsonValue = selected.capability_id === "todo_replan_cadence"
        ? goalWriteDraft(selected, mutation.draft, editorMode, mutation.cadenceOverride, mutation.horizonOverride)
        : mutation.draft;
      setJsonDraft(JSON.stringify(jsonValue, null, 2));
    }
    setEditorMode(editorMode === "guided" ? "json" : "guided");
    setMutation((current) => ({ ...current, preview: null,
      draft: editorMode === "json" && selected?.capability_id === "todo_replan_cadence"
        ? projectEditableCapabilityConfiguration(selected.configuration_editor, current.draft,
          replanCadenceEditorDefaults(selected.effective_configuration?.configuration, selected.default))
        : current.draft,
    }));
  }

  return { apply, changeDraft, changeJson, changeMode, editorMode, jsonDraft, jsonValid, error, mutation, preview };
}

function CapabilityMutationFeedback({ mutationError, onApplied, partialWrite, preview }: Readonly<{
  mutationError: string | null;
  onApplied: () => Promise<void>;
  partialWrite: GoalConfigurationPartialWrite | null;
  preview: GoalConfigurationPreview | null;
}>) {
  const { locale, t } = useWorkspaceI18n();
  const readbackPending = partialWrite && !partialWrite.readback_verified && !partialWrite.shared_sync_pending && !partialWrite.host_capacity_pending;
  return (
    <>
      {mutationError ? <p className="personal-machine-error" role="alert">{mutationError}</p> : null}
      {partialWrite ? (
        <section aria-live="polite" className="personal-capability-recovery">
          <AlertTriangle aria-hidden size={18} />
          <div>
            <strong>{readbackPending ? locale === "zh-CN" ? "配置已写入，读回尚未确认" : "Configuration written; readback unverified" : t(partialWrite.host_capacity_pending
              ? "capabilities.hostCapacityPartialWrite"
              : "capabilities.partialWrite")}</strong>
            <p>{readbackPending ? locale === "zh-CN" ? "请先重新读取当前配置，再决定是否需要其它变更。" : "Reload the current configuration before deciding whether another change is needed." : t(partialWrite.host_capacity_pending
              ? "capabilities.hostCapacityPartialWriteDescription"
              : "capabilities.partialWriteDescription")}</p>
            <small>{partialWrite.recommended_action}</small>
          </div>
          <button onClick={() => void onApplied()} type="button"><RefreshCw aria-hidden size={15} />{t("capabilities.refreshSource")}</button>
        </section>
      ) : null}
      {preview ? (
        <section className="personal-capability-preview" aria-label={t("capabilities.preview") }>
          <strong>{t("capabilities.preview")}</strong>
          <span>{preview.action === "delete"
            ? locale === "zh-CN" ? "清除此 Goal 的配置" : "Clear this Goal configuration"
            : locale === "zh-CN" ? "更新此 Goal 的配置" : "Update this Goal configuration"}</span>
          {preview.codex_host_capacity ? <span>{t(
            preview.codex_host_capacity.write_required
              ? "drawer.subagentHostCapacityRaise"
              : "drawer.subagentHostCapacityReady",
            {
              configured: preview.codex_host_capacity.configured_children ?? t("drawer.subagentHostCapacityImplicit"),
              required: preview.codex_host_capacity.required_children,
            },
          )}</span> : null}
          <small>{t("capabilities.previewLocked")}</small>
        </section>
      ) : null}
    </>
  );
}

function CapabilityCatalog({ callbacks, catalog, goalId, loading, notification, onApplied, onNotificationChanged }: CapabilityCatalogProps) {
  const { locale, t } = useWorkspaceI18n();
  const orderedCapabilities = useMemo(
    () => orderCapabilitiesForPresentation(
      catalog.capabilities.filter((capability) => capability.available_scopes.includes("goal")),
      locale,
    ),
    [catalog.capabilities, locale],
  );
  const [selectedCapabilityId, setSelectedCapabilityId] = useState(
    () => orderedCapabilities[0]?.capability_id ?? "",
  );
  const selected = useMemo(
    () => orderedCapabilities.find((capability) => capability.capability_id === selectedCapabilityId)
      ?? orderedCapabilities[0],
    [orderedCapabilities, selectedCapabilityId],
  );
  const localizedSelected = useMemo(
    () => selected ? localizeCapability(selected, locale) : undefined,
    [locale, selected],
  );
  const capabilityMutation = useCapabilityMutation({ goalId, loading, onApplied, selected: localizedSelected, t });
  const { apply, changeDraft, changeJson, changeMode, editorMode, jsonDraft, jsonValid, error: mutationError, mutation, preview: requestPreview } = capabilityMutation;
  const { busy, draft, partialWrite, preview } = mutation;
  // A refreshed observation can replace `selected` and reset local mutation
  // state. The existing catalog read still owns admission until it settles.
  const editorBusy = loading || Boolean(busy);
  const memorySelected = selected ? isMemoryCapability(selected.capability_id) : false;

  if (!selected || !localizedSelected) {
    return <ContextHelp label={locale === "zh-CN" ? "暂无已注册能力" : "No registered capabilities"} icon={CircleHelp}><p className="personal-capability-empty">{t("capabilities.empty")}</p></ContextHelp>;
  }

  const supportsGoal = localizedSelected.available_scopes.includes("goal");
  const editorAvailable = canEditCapability(localizedSelected, "goal");
  const readOnlyReason = localizedSelected.configuration_editor.read_only_reason
    ?? (!supportsGoal ? t("capabilities.machineOnly") : t("capabilities.previewOnly"));
  const memoryAgents = Array.isArray(selected.current?.registered_agents)
    ? selected.current.registered_agents.filter((value): value is string => typeof value === "string") : [];

  async function createPreview() {
    if (!localizedSelected || !editorAvailable || editorBusy || !jsonValid) return;
    await requestPreview(draft);
  }

  async function createClearPreview() {
    if (!editorAvailable || editorBusy) return;
    await requestPreview(null);
  }

  return (
    <div className="personal-capability-layout">
      <CapabilityCatalogNavigation
        capabilities={orderedCapabilities}
        locale={locale}
        onSelect={setSelectedCapabilityId}
        scope="goal"
        showScope={false}
        selectedCapabilityId={localizedSelected.capability_id}
        t={t}
      />

      <article aria-label={memorySelected ? memorySettingsTitle(locale) : localizedSelected.display_name} className="personal-capability-detail" tabIndex={0}>
        {memorySelected ? <MemorySettingsHeader capabilities={orderedCapabilities} locale={locale}
          selectedId={selected.capability_id} onSelect={setSelectedCapabilityId}
          onRefresh={() => void onApplied()} disabled={editorBusy} /> : <CapabilityDetailHeader capability={selected} locale={locale}
          source={localizedSelected.effective_configuration?.source} />}
        {memorySelected && orderedCapabilities.some(item => item.capability_id === "decision_context") ? <section
          aria-labelledby="memory-purpose-decision_context" hidden={selected.capability_id !== "decision_context"}
          id="memory-panel-decision_context" role="tabpanel" tabIndex={0}>
          <DecisionContextWorkbench key={goalId} goalId={goalId} active={selected.capability_id === "decision_context"} />
        </section> : null}
        <section hidden={selected.capability_id === "decision_context"}
          id={memorySelected && selected.capability_id !== "decision_context" ? `memory-panel-${selected.capability_id}` : undefined}
          aria-labelledby={memorySelected && selected.capability_id !== "decision_context" ? `memory-purpose-${selected.capability_id}` : undefined}
          role={memorySelected && selected.capability_id !== "decision_context" ? "tabpanel" : undefined}>
        <CapabilityEditorStatus available={editorAvailable} t={t} description={readOnlyReason} />

        {selected.capability_id === "progress_review" && selected.observation ? <section className="personal-capability-observation" aria-label={locale === "zh-CN" ? "审查证据读回" : "Review evidence readback"}>
          <h3>{locale === "zh-CN" ? "审查证据读回" : "Review evidence readback"}</h3>
          <button type="button" onClick={() => void onApplied()}>{locale === "zh-CN" ? "重新读取审查证据" : "Recheck review evidence"}</button>
          <p>{selected.observation.read_state === "unavailable" ? (locale === "zh-CN" ? "审查存储不可读，当前判断未知。" : "Review storage unavailable; current judgment unknown.")
            : selected.observation.read_state === "missing" ? (locale === "zh-CN" ? "尚无审查回执。" : "No review receipts yet.")
            : `${selected.observation.receipt_count} ${locale === "zh-CN" ? "条已读取回执" : "loaded receipts"}`}</p>
          <p>{locale === "zh-CN" ? "过期 / 拒收" : "Stale / rejected"}: {selected.observation.stale_receipts} / {selected.observation.rejected_receipts}</p>
          {selected.observation.latest ? <>
            <p>{locale === "zh-CN" ? "最新审查" : "Latest review"}: {selected.observation.latest.status}</p>
            <p>{locale === "zh-CN" ? "与目标的关系" : "Relation to goal"}: {selected.observation.latest.judgments.choice?.relation ?? "unknown"}</p>
            <p>{locale === "zh-CN" ? "新证据增量" : "Evidence increment"}: {selected.observation.latest.judgments.choice?.increment ?? "unknown"}</p>
            {selected.observation.latest.evidence_scope ? <>
              <p>{selected.observation.latest.evidence_scope.criterion_binding.origin === "goal_acceptance"
                ? (locale === "zh-CN" ? "绑定任务的规范验收条款" : "Bound to canonical task acceptance criteria")
                : (locale === "zh-CN" ? "依据操作者研究条款；不证明规范验收关联" : "Operator study basis; canonical acceptance binding is unverified")}</p>
              {selected.observation.latest.criterion_current === false ? <p role="alert">{locale === "zh-CN" ? "条款或任务关联已变化，旧判断不可作为当前依据。" : "Criteria or task binding changed; the old judgment is unavailable for current use."}</p> : null}
              <ContextHelp label={locale === "zh-CN" ? "文件观察口径" : "File observation basis"}><p>{locale === "zh-CN" ? "仅观察声明文件在两个检查点之间的净变化" : "Observed only the net change of declared files between two checkpoints"}</p></ContextHelp>
              <details><summary>{locale === "zh-CN" ? "观察范围与条款版本" : "Observed scope and criterion version"}</summary>
                <code>{selected.observation.latest.evidence_scope.criterion_binding.criteria_sha256}</code>
                <p>{selected.observation.latest.evidence_scope.criterion_binding.criterion_ids?.join(", ")}</p>
                <p>{selected.observation.latest.evidence_scope.files.join(", ")}</p>
              </details>
            </> : <ContextHelp label={locale === "zh-CN" ? "历史检查覆盖" : "Historical check coverage"} icon={CircleHelp}><p>{locale === "zh-CN" ? "旧回执未记录所选条款及文件覆盖范围。" : "Legacy receipt did not record selected criteria or file coverage."}</p></ContextHelp>}
          </> : null}
          <ContextHelp label={locale === "zh-CN" ? "观察与验收" : "Observation and acceptance"}><p>{locale === "zh-CN" ? "此观察不证明任务完成；未命中漂移条件也不等于工作符合目标。" : "This observation does not prove completion; a drift condition not met does not establish on-goal work."}</p></ContextHelp>
        </section> : null}

        {localizedSelected.capability_id === "lark_event_inbox" ? (
          <section className="personal-capability-linked-setting">
            <div>
              <strong>{t("capabilities.larkInboxNotificationSetting")}</strong>
              <p>{t("capabilities.larkInboxNotificationDescription")}</p>
            </div>
            <div className="personal-capability-linked-controls">
              <GoalAutoNotifyToggle
                callbacks={callbacks}
                goalId={goalId}
                notification={notification}
                onChanged={onNotificationChanged}
              />
              <GoalAutoNotifyToggle
                callbacks={callbacks}
                goalId={goalId}
                kind="blocked_notice"
                notification={notification}
                onChanged={onNotificationChanged}
              />
              {notification?.blockedNoticeDelivery ? <p className="personal-notification-hint">
                {t("notifications.blockedDelivery", {
                  delivered: notification.blockedNoticeDelivery.deliveredCount,
                  unverified: notification.blockedNoticeDelivery.unverifiedCount,
                  resolved: notification.blockedNoticeDelivery.resolvedCount,
                })}
              </p> : null}
            </div>
          </section>
        ) : null}

        {editorAvailable ? <>
          {editorMode === "json" || !localizedSelected.configuration_editor.fields.some((field) => field.key === "enabled" && field.input_kind === "boolean") || (selected.capability_id === "reward_memory" && !draft.agent_id) ? (
            <div className="personal-capability-editor-mode">
              <button disabled={editorBusy || !jsonValid} onClick={changeMode} type="button"><Code2 aria-hidden size={14} />{t(editorMode === "guided" ? "machine.editJson" : "machine.backToForm")}</button>
            </div>
          ) : null}
          {editorMode === "guided" ? <section className="personal-capability-field-summary">
            {selected.capability_id === "reward_memory" ? <p>
              {locale === "zh-CN" ? "只修改所选作用范围。关闭单个 Agent 不影响其它 Agent 或旧版共享绑定。" : "Only the selected scope changes. Disabling one Agent preserves sibling and legacy shared bindings."}
            </p> : null}
            {selected.capability_id === "reward_memory" ? <label className="personal-memory-agent">
              <span>{locale === "zh-CN" ? "经验作用范围" : "Experience scope"}</span>
              <select aria-label={locale === "zh-CN" ? "经验作用范围" : "Experience scope"}
                value={typeof draft.agent_id === "string" ? draft.agent_id : ""}
                disabled={editorBusy} onChange={event => changeDraft("agent_id", event.target.value)}>
                <option value="">{locale === "zh-CN" ? "旧版共享绑定" : "Legacy shared binding"}</option>
                {memoryAgents.map(agent => <option key={agent} value={agent}>{agent}</option>)}
                {typeof draft.agent_id === "string" && draft.agent_id && !memoryAgents.includes(draft.agent_id)
                  ? <option value={draft.agent_id} disabled>{draft.agent_id} · {locale === "zh-CN" ? "未注册" : "not registered"}</option> : null}
              </select>
              {!memoryAgents.length ? <small>{locale === "zh-CN" ? "当前 Goal 尚无已注册 Agent。" : "No registered Agents in this Goal."}</small> : null}
            </label> : null}
            {selected.effective_value_policy === "goal_stride_over_live_review_cadence" ? <p>
              {locale === "zh-CN"
                ? `复盘周期沿用${selected.machine_current ? "设备" : "产品"}默认；规划选项由本 Goal 设置。`
                : `Review interval follows ${selected.machine_current ? "device" : "product"} defaults; this Goal sets the planning options.`}
            </p> : null}
            {selected.capability_id === "periodic_report" && draft.profile_preset === "weekly-progress"
              ? <PeriodicReportSessionField goalId={goalId} route={String(draft.route_ref ?? "")}
                zh={locale === "zh-CN"} disabled={editorBusy} revision={JSON.stringify(selected.current)}
                onChange={value => changeDraft("route_ref", value)} /> : null}
            <CapabilityConfigurationFields
              disabled={editorBusy}
              copy={localizedCapabilityFieldCopy(locale, localizedSelected.capability_id)}
              editor={localizedSelected.configuration_editor}
              advancedKeys={selected.capability_id === "todo_replan_cadence" ? ["planning_horizon_goal"] : []}
              omitKeys={selected.capability_id === "reward_memory"
                ? ["agent_id", ...(draft.agent_id ? ["enabled_agents", ...(draft.enabled === false ? ["config_path"] : [])] : ["enabled"])]
                : selected.capability_id === "periodic_report" && draft.profile_preset === "weekly-progress" ? ["route_ref"]
                : []}
              onChange={changeDraft}
              value={draft}
              enabledAction={<button className="personal-capability-edit-json" disabled={editorBusy} onClick={changeMode} type="button"><Code2 aria-hidden size={14} />{t("machine.editJson")}</button>}
            />
          </section> : <label className="personal-capability-json-editor" htmlFor="goal-configuration-json">
            <span>{t("capabilities.jsonConfiguration")}</span>
            <textarea id="goal-configuration-json" aria-describedby="goal-configuration-json-help" disabled={editorBusy} onChange={(event) => changeJson(event.target.value)} rows={12} spellCheck={false} value={jsonDraft} />
            <small id="goal-configuration-json-help">{t("capabilities.jsonHelp")}</small>
            {!jsonValid ? <span className="personal-machine-validation" role="alert">{t("capabilities.jsonInvalid")}</span> : null}
          </label>}
        </> : null}
        {selected.capability_id === "reward_memory" && selected.current?.effective_available === false ? <p className="personal-memory-unbound" role="status">{locale === "zh-CN" ? "此 Goal 当前没有可用的经验配置。" : "No usable experience configuration is available in this Goal."}</p> : null}
        {selected.capability_id === "reward_memory" && Array.isArray(selected.current?.agent_bindings) && selected.current.agent_bindings.length > 0 ? <section className="personal-capability-observation" aria-label={locale === "zh-CN" ? "Agent 记忆绑定读回" : "Agent memory binding readback"}>
          <h3>{locale === "zh-CN" ? "Agent 经验配置" : "Agent experience configuration"}</h3>
          {(selected.current.agent_bindings as Record<string, unknown>[]).map((binding) => <p key={String(binding.agent_id)}>
            <button type="button" disabled={!editorAvailable || editorBusy || editorMode === "json"}
              onClick={() => changeDraft("agent_id", String(binding.agent_id))}>{String(binding.agent_id)}</button>{" · "}{binding.binding_source === "agent"
              ? locale === "zh-CN" ? "独立绑定" : "Independent binding"
              : locale === "zh-CN" ? "旧版共享绑定" : "Legacy shared binding"}{" · "}{rewardMemoryBindingStatus(binding.status, locale === "zh-CN")}
            <br /><small>{locale === "zh-CN" ? "自动召回设置" : "Automatic recall setting"}: {binding.automatic_recall === true ? (locale === "zh-CN" ? "开启" : "on") : binding.automatic_recall === false ? (locale === "zh-CN" ? "关闭" : "off") : "—"}{" · "}
              {locale === "zh-CN" ? "自动写回设置" : "Automatic writeback setting"}: {binding.automatic_ingest === true ? (locale === "zh-CN" ? "开启" : "on") : binding.automatic_ingest === false ? (locale === "zh-CN" ? "关闭" : "off") : "—"}</small>
          </p>)}
        </section> : null}
        {selected.capability_id === "reward_memory" ? <CapabilityConfigurationHelp capability={selected} locale={locale} /> : null}
        <CapabilityMutationFeedback mutationError={mutationError} onApplied={onApplied} partialWrite={partialWrite} preview={preview} />
        {editorAvailable ? (
          <footer className="personal-capability-actions">
            {localizedSelected.current && localizedSelected.available_scopes.includes("machine") ? (
              <button disabled={editorBusy || !jsonValid} onClick={() => void createClearPreview()} type="button">
                {t("capabilities.restoreInheritance")}
              </button>
            ) : null}
            <button disabled={editorBusy || !jsonValid} onClick={() => void createPreview()} type="button">
              {busy === "preview" ? t("common.loading") : t("capabilities.previewChanges")}
            </button>
            <button className="is-primary" disabled={editorBusy || !preview || !jsonValid} onClick={() => void apply()} type="button">
              {busy === "apply" ? t("common.loading") : t("capabilities.applyPreview")}
            </button>
          </footer>
        ) : null}
        {!["content_ops", "decision_context"].includes(localizedSelected.capability_id) ? <CapabilityConfigurationSummary key={selected.capability_id} values={[
          { label: t("capabilities.goalValue"), value: localizedSelected.current },
          { label: t(localizedSelected.machine_current ? "capabilities.machineValue" : "capabilities.defaultValue"), value: localizedSelected.machine_current ?? localizedSelected.default },
        ]} t={t} /> : null}
        </section>
      </article>
    </div>
  );
}

export function GoalCapabilitySettings({ callbacks, goalId, notification, onChanged }: Readonly<{
  callbacks: PersonalWorkspaceCallbacks;
  goalId?: string | null;
  notification?: WorkspaceGoalNotification;
  onChanged: () => void;
}>) {
  const { t } = useWorkspaceI18n();
  const [inspection, setInspection] = useState<GoalConfigurationInspection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const readGeneration = useRef(0);

  async function load() {
    if (!goalId) return;
    const generation = ++readGeneration.current;
    setLoading(true);
    setError(null);
    // Withdraw the previous observation during a recheck; preserve navigation.
    setInspection(current => current ? {...current, capability_catalog: {...current.capability_catalog,
      capabilities: current.capability_catalog.capabilities.map(item => item.observation ? {...item, observation: undefined} : item)}} : null);
    await fetchGoalConfiguration(goalId)
      .then(value => {if (generation === readGeneration.current) setInspection(value);})
      .catch((reason: unknown) => {if (generation === readGeneration.current) setError(reason instanceof Error ? reason.message : t("capabilities.loadFailed"));})
      .finally(() => {if (generation === readGeneration.current) setLoading(false);});
  }

  useEffect(() => {setInspection(null); load(); return () => {readGeneration.current++;};}, [goalId]);

  if (!goalId) {
    return <p className="personal-capability-empty">{t("capabilities.chooseGoal")}</p>;
  }
  if (loading && !inspection) {
    return <p aria-live="polite" className="personal-capability-empty"><LoaderCircle className="personal-spin" size={18} />{t("capabilities.loading")}</p>;
  }
  if (error) {
    return (
      <section className="personal-capability-error" role="alert">
        <AlertTriangle aria-hidden size={18} />
        <span><strong>{t("capabilities.loadFailed")}</strong><small>{error}</small></span>
        <button onClick={() => void load()} type="button"><RefreshCw aria-hidden size={15} />{t("capabilities.retry")}</button>
      </section>
    );
  }
  if (!inspection) return null;

  return (
    <section className="personal-capability-settings is-goal-scoped" data-revision={inspection.revision}>
      <CapabilityCatalog
        callbacks={callbacks}
        catalog={inspection.capability_catalog}
        goalId={goalId}
        loading={loading}
        notification={notification}
        onApplied={load}
        onNotificationChanged={onChanged}
      />
    </section>
  );
}
