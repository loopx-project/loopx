import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Code2, LoaderCircle, RefreshCw, ShieldCheck } from "lucide-react";

import {
  applyGoalConfiguration,
  fetchGoalConfiguration,
  previewGoalConfiguration,
  type CapabilityConfigurationCatalog,
  type GoalConfigurationInspection,
  type GoalConfigurationPreview,
  type GoalConfigurationPartialWrite,
} from "../../data/chat";
import { parseEditableCapabilityJson, projectEditableCapabilityConfiguration } from "../../data/capability-configuration";
import { useWorkspaceI18n } from "./i18n";
import { CapabilityConfigurationFields } from "./capability-configuration-fields";
import { withReportScheduleTimezone } from "./periodic-report-schedule-field";
import { localizeCapability, localizedCapabilityFieldCopy } from "./capability-localization";
import { orderCapabilitiesForPresentation, canEditCapability, CapabilityCatalogNavigation, CapabilityConfigurationSummary, CapabilityDetailHeader, CapabilityEditorStatus } from "./capability-workbench";
import { GoalAutoNotifyToggle } from "./notification-settings-panel";
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
    setMutation({
      busy: null,
      draft: projectEditableCapabilityConfiguration(
        selected?.configuration_editor ?? { fields: [] },
        selected?.current
        ?? selected?.effective_configuration?.configuration
        ?? selected?.default,
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
      const nextPreview = await previewGoalConfiguration(goalId, selected.capability_id, configuration);
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
      const writableDraft = projectEditableCapabilityConfiguration(
        selected.configuration_editor,
        mutation.draft,
        selected.default,
      );
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
    () => orderCapabilitiesForPresentation(catalog.capabilities, locale),
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
        capabilities={catalog.capabilities}
        locale={locale}
        onSelect={setSelectedCapabilityId}
        scope="goal"
        selectedCapabilityId={localizedSelected.capability_id}
        t={t}
      />

      <article aria-label={localizedSelected.display_name} className="personal-capability-detail" tabIndex={0}>
        <CapabilityDetailHeader capability={selected} locale={locale}
          source={localizedSelected.effective_configuration?.source} />
        <CapabilityEditorStatus available={editorAvailable} t={t}
          description={readOnlyReason} />

        {localizedSelected.capability_id === "lark_event_inbox" ? (
          <section className="personal-capability-linked-setting">
            <div>
              <strong>{t("capabilities.larkInboxNotificationSetting")}</strong>
              <p>{t("capabilities.larkInboxNotificationDescription")}</p>
            </div>
            <GoalAutoNotifyToggle
              callbacks={callbacks}
              goalId={goalId}
              notification={notification}
              onChanged={onNotificationChanged}
            />
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
              copy={localizedCapabilityFieldCopy(locale)}
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
        <CapabilityConfigurationSummary key={selected.capability_id} values={[
          { label: t("capabilities.goalValue"), value: localizedSelected.current },
          { label: t(localizedSelected.machine_current ? "capabilities.machineValue" : "capabilities.defaultValue"), value: localizedSelected.machine_current ?? localizedSelected.default },
        ]} t={t} />
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

  function load() {
    if (!goalId) return;
    setLoading(true);
    setError(null);
    void fetchGoalConfiguration(goalId)
      .then(setInspection)
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : t("capabilities.loadFailed")))
      .finally(() => setLoading(false));
  }

  useEffect(load, [goalId]);

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
    <section className="personal-capability-settings" data-revision={inspection.revision}>
      <details className="personal-capability-scope-note">
        <summary><ShieldCheck aria-hidden size={17} />{t("capabilities.atomicOverride")}</summary>
        <p>{t("capabilities.atomicOverrideDescription")}</p>
      </details>
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
