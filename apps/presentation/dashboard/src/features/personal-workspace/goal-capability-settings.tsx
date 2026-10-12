import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Code2, LoaderCircle, RefreshCw } from "lucide-react";

import {
  applyGoalConfiguration,
  fetchGoalConfiguration,
  previewGoalConfiguration,
  type CapabilityConfigurationCatalog,
  type GoalConfigurationInspection,
  type GoalConfigurationPreview,
  type GoalConfigurationPartialWrite,
} from "../../data/chat";
import { parseEditableCapabilityJson, projectEditableCapabilityConfiguration, replanCadenceEditorValue } from "../../data/capability-configuration";
import { useWorkspaceI18n } from "./i18n";
import { CapabilityConfigurationFields } from "./capability-configuration-fields";
import { withReportScheduleTimezone } from "./periodic-report-schedule-field";
import { localizeCapability, localizedCapabilityFieldCopy } from "./capability-localization";
import { orderCapabilitiesForPresentation, canEditCapability, CapabilityCatalogNavigation, CapabilityConfigurationSummary, CapabilityDetailHeader, CapabilityEditorStatus } from "./capability-workbench";
import { GoalAutoNotifyToggle } from "./notification-settings-panel";
import { ContentReferenceWorkbench } from "./content-reference-workbench";
import type { PersonalWorkspaceCallbacks, WorkspaceGoalNotification } from "./personal-workspace-model";

type CapabilityCatalogProps = Readonly<{
  callbacks: PersonalWorkspaceCallbacks;
  catalog: CapabilityConfigurationCatalog;
  goalId: string;
  onApplied: () => void;
  notification?: WorkspaceGoalNotification;
  onNotificationChanged: () => void;
}>;

type CapabilityMutationState = Readonly<{
  busy: "preview" | "apply" | null;
  draft: Record<string, unknown>;
  partialWrite: GoalConfigurationPartialWrite | null;
  preview: GoalConfigurationPreview | null;
}>;

function goalWriteDraft(selected: CapabilityConfigurationCatalog["capabilities"][number], draft: Record<string, unknown>) {
  const writable = projectEditableCapabilityConfiguration(selected.configuration_editor, draft, selected.default);
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

function useCapabilityMutation({ goalId, onApplied, selected, t }: Readonly<{
  goalId: string;
  onApplied: () => void;
  selected: CapabilityConfigurationCatalog["capabilities"][number] | undefined;
  t: ReturnType<typeof useWorkspaceI18n>["t"];
}>) {
  const [mutation, setMutation] = useState<CapabilityMutationState>({
    busy: null,
    draft: {},
    partialWrite: null,
    preview: null,
  });
  const [error, setError] = useState<string | null>(null);
  const [editorMode, setEditorMode] = useState<"guided" | "json">("guided");
  const [jsonDraft, setJsonDraft] = useState("");
  const parsedJson = useMemo(() => selected
    ? parseEditableCapabilityJson(selected.configuration_editor, jsonDraft) : null, [selected, jsonDraft]);
  const jsonValid = editorMode === "guided" || parsedJson !== null;

  useEffect(() => {
    setEditorMode("guided");
    setJsonDraft("");
    const current = (selected?.capability_id === "pull_request_review" ? selected.effective_configuration?.configuration : selected?.current)
      ?? selected?.effective_configuration?.configuration ?? selected?.default;
    setMutation({
      busy: null,
      draft: projectEditableCapabilityConfiguration(
        selected?.configuration_editor ?? { fields: [] },
        selected?.capability_id === "todo_replan_cadence" ? replanCadenceEditorValue(current) : current,
        selected?.default,
      ),
      partialWrite: null,
      preview: null,
    });
    setError(null);
  }, [selected]);

  async function preview(configuration: Record<string, unknown> | null) {
    if (!selected || mutation.busy || !jsonValid) return;
    setMutation((current) => ({ ...current, busy: "preview", partialWrite: null }));
    setError(null);
    try {
      const nextPreview = await previewGoalConfiguration(goalId, selected.capability_id, configuration === null ? null : goalWriteDraft(selected, configuration));
      setMutation((current) => ({ ...current, preview: nextPreview }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("capabilities.previewFailed"));
    } finally {
      setMutation((current) => ({ ...current, busy: null }));
    }
  }

  async function apply() {
    if (!selected || !mutation.preview || mutation.busy || !jsonValid) return;
    setMutation((current) => ({ ...current, busy: "apply" }));
    setError(null);
    try {
      const writableDraft = goalWriteDraft(selected, mutation.draft);
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
      if (result.status !== "partial_write") onApplied();
    } catch (reason) {
      setMutation((current) => ({ ...current, preview: null }));
      setError(reason instanceof Error ? reason.message : t("capabilities.applyFailed"));
    } finally {
      setMutation((current) => ({ ...current, busy: null }));
    }
  }

  function changeDraft(key: string, value: unknown) {
    setMutation((current) => ({
      ...current,
      draft: selected?.capability_id === "periodic_report"
        ? withReportScheduleTimezone(current.draft, key, value)
        : { ...current.draft, [key]: value },
      preview: null,
    }));
    setError(null);
  }

  function changeJson(text: string) {
    if (!selected || mutation.busy) return;
    setJsonDraft(text);
    const parsed = parseEditableCapabilityJson(selected.configuration_editor, text);
    setMutation((current) => ({ ...current, preview: null, draft: parsed
      ? projectEditableCapabilityConfiguration(selected.configuration_editor, parsed, selected.default)
      : current.draft }));
    setError(null);
  }

  function changeMode() {
    if (mutation.busy || !jsonValid) return;
    if (editorMode === "guided") setJsonDraft(JSON.stringify(mutation.draft, null, 2));
    setEditorMode(editorMode === "guided" ? "json" : "guided");
    setMutation((current) => ({ ...current, preview: null }));
  }

  return { apply, changeDraft, changeJson, changeMode, editorMode, jsonDraft, jsonValid, error, mutation, preview };
}

function CapabilityMutationFeedback({ mutationError, onApplied, partialWrite, preview }: Readonly<{
  mutationError: string | null;
  onApplied: () => void;
  partialWrite: GoalConfigurationPartialWrite | null;
  preview: GoalConfigurationPreview | null;
}>) {
  const { t } = useWorkspaceI18n();
  return (
    <>
      {mutationError ? <p className="personal-machine-error" role="alert">{mutationError}</p> : null}
      {partialWrite ? (
        <section aria-live="polite" className="personal-capability-recovery">
          <AlertTriangle aria-hidden size={18} />
          <div>
            <strong>{t(partialWrite.host_capacity_pending
              ? "capabilities.hostCapacityPartialWrite"
              : "capabilities.partialWrite")}</strong>
            <p>{t(partialWrite.host_capacity_pending
              ? "capabilities.hostCapacityPartialWriteDescription"
              : "capabilities.partialWriteDescription")}</p>
            <small>{partialWrite.recommended_action}</small>
          </div>
          <button onClick={onApplied} type="button"><RefreshCw aria-hidden size={15} />{t("capabilities.refreshSource")}</button>
        </section>
      ) : null}
      {preview ? (
        <section className="personal-capability-preview" aria-label={t("capabilities.preview") }>
          <strong>{t("capabilities.preview")}</strong>
          <span>{t(`machine.action.${preview.action}`)}</span>
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

function CapabilityCatalog({ callbacks, catalog, goalId, notification, onApplied, onNotificationChanged }: CapabilityCatalogProps) {
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
  const capabilityMutation = useCapabilityMutation({ goalId, onApplied, selected: localizedSelected, t });
  const { apply, changeDraft, changeJson, changeMode, editorMode, jsonDraft, jsonValid, error: mutationError, mutation, preview: requestPreview } = capabilityMutation;
  const { busy, draft, partialWrite, preview } = mutation;

  if (!selected || !localizedSelected) {
    return <p className="personal-capability-empty">{t("capabilities.empty")}</p>;
  }

  const supportsGoal = localizedSelected.available_scopes.includes("goal");
  const editorAvailable = canEditCapability(localizedSelected, "goal");
  const readOnlyReason = localizedSelected.configuration_editor.read_only_reason
    ?? (!supportsGoal ? t("capabilities.machineOnly") : t("capabilities.previewOnly"));

  async function createPreview() {
    if (!localizedSelected || !editorAvailable || busy || !jsonValid) return;
    const writableDraft = projectEditableCapabilityConfiguration(
      localizedSelected.configuration_editor,
      draft,
      localizedSelected.default,
    );
    await requestPreview(writableDraft);
  }

  async function createClearPreview() {
    if (!editorAvailable || busy) return;
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

      <article aria-label={localizedSelected.display_name} className="personal-capability-detail" tabIndex={0}>
        {localizedSelected.capability_id === "content_ops" ? <h2>{localizedSelected.display_name}</h2> : <CapabilityDetailHeader capability={selected} locale={locale}
          source={localizedSelected.effective_configuration?.source} />}
        {localizedSelected.capability_id === "content_ops" ? <ContentReferenceWorkbench key={goalId} /> : <CapabilityEditorStatus available={editorAvailable} t={t}
          description={readOnlyReason} />}

        {selected.capability_id === "progress_review" && selected.observation ? <section className="personal-capability-observation" aria-label={locale === "zh-CN" ? "审查证据读回" : "Review evidence readback"}>
          <h3>{locale === "zh-CN" ? "审查证据读回" : "Review evidence readback"}</h3>
          <button type="button" onClick={onApplied}>{locale === "zh-CN" ? "重新读取审查证据" : "Recheck review evidence"}</button>
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
              <p>{locale === "zh-CN" ? "仅观察声明文件在两个检查点之间的净变化" : "Observed only the net change of declared files between two checkpoints"}</p>
              <details><summary>{locale === "zh-CN" ? "观察范围与条款版本" : "Observed scope and criterion version"}</summary>
                <code>{selected.observation.latest.evidence_scope.criterion_binding.criteria_sha256}</code>
                <p>{selected.observation.latest.evidence_scope.criterion_binding.criterion_ids?.join(", ")}</p>
                <p>{selected.observation.latest.evidence_scope.files.join(", ")}</p>
              </details>
            </> : <p>{locale === "zh-CN" ? "旧回执未记录所选条款及文件覆盖范围。" : "Legacy receipt did not record selected criteria or file coverage."}</p>}
          </> : null}
          <p>{locale === "zh-CN" ? "此观察不证明任务完成；未命中漂移条件也不等于工作符合目标。" : "This observation does not prove completion; a drift condition not met does not establish on-goal work."}</p>
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
          {editorMode === "json" || !localizedSelected.configuration_editor.fields.some((field) => field.key === "enabled" && field.input_kind === "boolean") ? (
            <div className="personal-capability-editor-mode">
              <button disabled={Boolean(busy) || !jsonValid} onClick={changeMode} type="button"><Code2 aria-hidden size={14} />{t(editorMode === "guided" ? "machine.editJson" : "machine.backToForm")}</button>
            </div>
          ) : null}
          {editorMode === "guided" ? <section className="personal-capability-field-summary">
            <CapabilityConfigurationFields
              disabled={Boolean(busy)}
              copy={localizedCapabilityFieldCopy(locale, localizedSelected.capability_id)}
              editor={localizedSelected.configuration_editor}
              onChange={changeDraft}
              value={draft}
              enabledAction={<button className="personal-capability-edit-json" onClick={changeMode} type="button"><Code2 aria-hidden size={14} />{t("machine.editJson")}</button>}
            />
          </section> : <label className="personal-capability-json-editor" htmlFor="goal-configuration-json">
            <span>{t("capabilities.jsonConfiguration")}</span>
            <textarea id="goal-configuration-json" aria-describedby="goal-configuration-json-help" disabled={Boolean(busy)} onChange={(event) => changeJson(event.target.value)} rows={12} spellCheck={false} value={jsonDraft} />
            <small id="goal-configuration-json-help">{t("capabilities.jsonHelp")}</small>
            {!jsonValid ? <span className="personal-machine-validation" role="alert">{t("capabilities.jsonInvalid")}</span> : null}
          </label>}
        </> : null}
        <CapabilityMutationFeedback mutationError={mutationError} onApplied={onApplied} partialWrite={partialWrite} preview={preview} />
        {editorAvailable ? (
          <footer className="personal-capability-actions">
            {localizedSelected.current && localizedSelected.available_scopes.includes("machine") ? (
              <button disabled={Boolean(busy) || !jsonValid} onClick={() => void createClearPreview()} type="button">
                {t("capabilities.restoreInheritance")}
              </button>
            ) : null}
            <button disabled={Boolean(busy) || !jsonValid} onClick={() => void createPreview()} type="button">
              {busy === "preview" ? t("common.loading") : t("capabilities.previewChanges")}
            </button>
            <button className="is-primary" disabled={Boolean(busy) || !preview || !jsonValid} onClick={() => void apply()} type="button">
              {busy === "apply" ? t("common.loading") : t("capabilities.applyPreview")}
            </button>
          </footer>
        ) : null}
        {localizedSelected.capability_id !== "content_ops" ? <CapabilityConfigurationSummary key={selected.capability_id} values={[
          { label: t("capabilities.goalValue"), value: localizedSelected.current },
          { label: t(localizedSelected.machine_current ? "capabilities.machineValue" : "capabilities.defaultValue"), value: localizedSelected.machine_current ?? localizedSelected.default },
        ]} t={t} /> : null}
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

  function load() {
    if (!goalId) return;
    const generation = ++readGeneration.current;
    setLoading(true);
    setError(null);
    // Withdraw the previous observation during a recheck; preserve navigation.
    setInspection(current => current ? {...current, capability_catalog: {...current.capability_catalog,
      capabilities: current.capability_catalog.capabilities.map(item => item.observation ? {...item, observation: undefined} : item)}} : null);
    void fetchGoalConfiguration(goalId)
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
        <button onClick={load} type="button"><RefreshCw aria-hidden size={15} />{t("capabilities.retry")}</button>
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
        notification={notification}
        onApplied={load}
        onNotificationChanged={onChanged}
      />
    </section>
  );
}
