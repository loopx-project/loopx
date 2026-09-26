import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Check, Code2, RefreshCw, RotateCcw, ShieldCheck, Trash2 } from "lucide-react";

import {
  applyMachineConfiguration,
  applyMachineConfigurationRemoval,
  applyMachineConfigurationRollback,
  fetchMachineConfiguration,
  previewMachineConfiguration,
  previewMachineConfigurationRemoval,
  previewMachineConfigurationRollback,
  type CapabilityConfigurationCatalog,
  type MachineConfigurationInspection,
  type MachineConfigurationPreview,
  type MachineConfigurationRollbackPlan,
  type MachineConfigurationTransaction,
} from "../../data/chat";
import { projectEditableCapabilityConfiguration } from "../../data/capability-configuration";
import { CapabilityConfigurationFields } from "./capability-configuration-fields";
import { withReportScheduleTimezone } from "./periodic-report-schedule-field";
import { localizeCapability, localizedCapabilityFieldCopy } from "./capability-localization";
import { canEditCapability, CapabilityCatalogNavigation, CapabilityConfigurationSummary, CapabilityDetailHeader, CapabilityEditorStatus, orderCapabilitiesForPresentation } from "./capability-workbench";
import { useWorkspaceI18n } from "./i18n";

type CapabilityDescriptor = CapabilityConfigurationCatalog["capabilities"][number];
type EditorMode = "guided" | "json";

function configurationObject(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function currentConfiguration(
  inspection: MachineConfigurationInspection | null,
  capability: CapabilityDescriptor | undefined,
) {
  const namespace = capability?.machine_namespace;
  return namespace ? inspection?.machine_configuration?.namespaces[namespace] : undefined;
}

function completeMachineConfiguration(
  capability: CapabilityDescriptor,
  current: Record<string, unknown> | undefined,
  draft: Record<string, unknown>,
) {
  const complete = {
    ...configurationObject(capability.default),
    ...configurationObject(current),
    ...draft,
  };
  // The guided steward editor owns the v1 selection-policy fields. Opening an
  // installed v0 preference in that form is an explicit migration preview;
  // JSON mode can still submit the legacy shape unchanged when needed.
  if (capability.capability_id === "steward_executor"
    && (Object.hasOwn(draft, "selection_policy") || Object.hasOwn(draft, "eligible_endpoints"))) {
    complete.schema_version = configurationObject(capability.default).schema_version;
  }
  return complete;
}

function validGuidedDraft(capability: CapabilityDescriptor, value: Record<string, unknown>) {
  for (const field of capability.configuration_editor.fields) {
    const item = value[field.key];
    if (field.required && (item === undefined || item === null || item === "")) return false;
  }
  if (capability.capability_id === "periodic_report" && value.enabled === true) {
    return Boolean(String(value.profile_preset ?? "").trim()
      && String(value.route_ref ?? "").trim()
      && String(value.timezone ?? "").trim());
  }
  if (capability.capability_id === "steward_executor") {
    const policy = String(value.selection_policy ?? "preferred");
    const primary = String(value.executor_endpoint ?? "");
    const eligible = Array.isArray(value.eligible_endpoints)
      ? value.eligible_endpoints.map((item) => String(item))
      : [];
    if (policy === "flexible") {
      return eligible.length > 0 && eligible.includes(primary)
        && new Set(eligible).size === eligible.length;
    }
    return eligible.length === 0;
  }
  return true;
}

function parseJsonObject(value: string) {
  try {
    const parsed: unknown = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : null;
  } catch {
    return null;
  }
}

function shortRevision(value: string | undefined) {
  if (!value) return "—";
  if (value === "absent") return value;
  return value.replace(/^sha256:/, "").slice(0, 12);
}

export function MachineConfigurationSettings({ section }: { section: "steward" | "other" }) {
  const { locale, t } = useWorkspaceI18n();
  const [inspection, setInspection] = useState<MachineConfigurationInspection | null>(null);
  const [selectedCapabilityId, setSelectedCapabilityId] = useState("");
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [jsonDraft, setJsonDraft] = useState("{}");
  const [editorMode, setEditorMode] = useState<EditorMode>("guided");
  const [preview, setPreview] = useState<MachineConfigurationPreview | null>(null);
  const [previewOperation, setPreviewOperation] = useState<"upsert" | "remove">("upsert");
  const [transaction, setTransaction] = useState<MachineConfigurationTransaction | null>(null);
  const [rollbackPlan, setRollbackPlan] = useState<MachineConfigurationRollbackPlan | null>(null);
  const [busy, setBusy] = useState<"load" | "preview" | "apply" | "rollback-preview" | "rollback" | null>("load");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const capabilities = useMemo(() => orderCapabilitiesForPresentation(
    (inspection?.capability_catalog.capabilities ?? []).filter((capability) =>
      capability.available_scopes.includes("machine")
      && (section === "steward"
        ? capability.capability_id === "steward_executor" || capability.capability_id === "manager_runtime"
        : capability.capability_id !== "steward_executor" && capability.capability_id !== "manager_runtime")),
    locale,
  ), [inspection, locale, section]);
  const invalidNamespace = inspection?.invalid_namespaces[0];
  const selectedRaw = capabilities.find(
    (capability) => capability.capability_id === selectedCapabilityId,
  ) ?? (invalidNamespace ? capabilities.find(
    (capability) => capability.machine_namespace === invalidNamespace,
  ) : undefined) ?? (section === "steward"
    ? capabilities.find((capability) => capability.capability_id === "steward_executor")
    : undefined) ?? capabilities.find((capability) => canEditCapability(capability, "machine")) ?? capabilities[0];
  const selected = selectedRaw ? localizeCapability(selectedRaw, locale) : undefined;
  const selectedCurrent = currentConfiguration(inspection, selected);
  const configured = Boolean(selected?.machine_namespace && selectedCurrent);
  const editorAvailable = Boolean(selected && canEditCapability(selected, "machine"));
  const parsedJsonDraft = useMemo(() => parseJsonObject(jsonDraft), [jsonDraft]);
  const desiredConfiguration = selected
    ? editorMode === "json"
      ? parsedJsonDraft
      : completeMachineConfiguration(selected, selectedCurrent, draft)
    : null;
  const editorValid = Boolean(
    selected && (editorMode === "json"
      ? parsedJsonDraft
      : validGuidedDraft(selected, desiredConfiguration ?? {})),
  );

  async function reload() {
    setInspection(await fetchMachineConfiguration());
  }

  async function retryLoad() {
    if (busy) return;
    setBusy("load");
    setError(null);
    try {
      await reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("machine.loadError"));
    } finally {
      setBusy(null);
    }
  }

  useEffect(() => {
    let active = true;
    fetchMachineConfiguration()
      .then((next) => {
        if (!active) return;
        setInspection(next);
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : t("machine.loadError"));
      })
      .finally(() => {
        if (active) setBusy(null);
      });
    return () => { active = false; };
  }, [t]);

  useEffect(() => {
    if (!selected) return;
    const current = currentConfiguration(inspection, selected);
    const editable = projectEditableCapabilityConfiguration(
      selected.configuration_editor,
      current ?? selected.default,
      selected.default,
    );
    const complete = completeMachineConfiguration(selected, current, editable);
    setDraft(editable);
    setJsonDraft(JSON.stringify(complete, null, 2));
    setEditorMode(editorAvailable ? "guided" : "json");
    setPreview(null);
    setPreviewOperation("upsert");
    setRollbackPlan(null);
  }, [inspection, selectedCapabilityId, locale]);

  function changeDraft(key: string, value: unknown) {
    setDraft((current) => selected?.capability_id === "periodic_report"
      ? withReportScheduleTimezone(current, key, value) : { ...current, [key]: value });
    setPreview(null);
    setPreviewOperation("upsert");
    setError(null);
    setNotice(null);
  }

  function changeMode(mode: EditorMode) {
    if (!selected) return;
    if (mode === "json") {
      setJsonDraft(JSON.stringify(
        completeMachineConfiguration(selected, selectedCurrent, draft),
        null,
        2,
      ));
    } else if (parsedJsonDraft) {
      setDraft(projectEditableCapabilityConfiguration(
        selected.configuration_editor,
        parsedJsonDraft,
        selected.default,
      ));
    } else {
      setError(t("machine.jsonInvalid"));
      return;
    }
    setEditorMode(mode);
    setPreview(null);
    setPreviewOperation("upsert");
    setError(null);
  }

  async function createPreview() {
    if (!editorAvailable || !selected?.machine_namespace || !desiredConfiguration || !editorValid || busy) return;
    setBusy("preview");
    setError(null);
    setNotice(null);
    try {
      setPreviewOperation("upsert");
      setPreview(await previewMachineConfiguration(selected.machine_namespace, desiredConfiguration));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("machine.previewError"));
    } finally {
      setBusy(null);
    }
  }

  async function createRemovalPreview() {
    if (!editorAvailable || !selected?.machine_namespace || !configured || busy) return;
    setBusy("preview");
    setError(null);
    setNotice(null);
    try {
      setPreviewOperation("remove");
      setPreview(await previewMachineConfigurationRemoval(selected.machine_namespace));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("machine.previewError"));
    } finally {
      setBusy(null);
    }
  }

  async function applyPreview() {
    if (!editorAvailable || !selected?.machine_namespace || !preview || busy || (previewOperation === "upsert" && !desiredConfiguration)) return;
    setBusy("apply");
    setError(null);
    const operation = previewOperation;
    try {
      const result = operation === "remove"
        ? await applyMachineConfigurationRemoval(selected.machine_namespace, preview.plan_revision)
        : await applyMachineConfiguration(selected.machine_namespace, desiredConfiguration!, preview.plan_revision);
      setTransaction(result);
      setPreview(null);
      setPreviewOperation("upsert");
      setRollbackPlan(null);
      await reload();
      setNotice(result.status === "applied"
        ? t(operation === "remove" ? "machine.removed" : "machine.applied")
        : t("machine.unchanged"));
    } catch (cause) {
      setPreview(null);
      setPreviewOperation("upsert");
      setError(cause instanceof Error ? cause.message : t("machine.applyError"));
    } finally {
      setBusy(null);
    }
  }

  async function previewRollback() {
    if (!transaction?.transaction_id || busy) return;
    setBusy("rollback-preview");
    setError(null);
    try {
      setRollbackPlan(await previewMachineConfigurationRollback(transaction.transaction_id));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("machine.rollbackError"));
    } finally {
      setBusy(null);
    }
  }

  async function applyRollback() {
    if (!transaction?.transaction_id || !rollbackPlan || busy) return;
    setBusy("rollback");
    setError(null);
    try {
      await applyMachineConfigurationRollback(transaction.transaction_id, rollbackPlan.plan_revision);
      setTransaction(null);
      setRollbackPlan(null);
      setPreview(null);
      await reload();
      setNotice(t("machine.rolledBack"));
    } catch (cause) {
      setRollbackPlan(null);
      setError(cause instanceof Error ? cause.message : t("machine.rollbackError"));
    } finally {
      setBusy(null);
    }
  }

  if (busy === "load") {
    return <div className="personal-machine-loading" role="status">{t("common.loading")}</div>;
  }
  if (!inspection) {
    return <section className="personal-capability-error" role="alert">
      <AlertTriangle aria-hidden size={18} />
      <span><strong>{t("machine.loadError")}</strong><small>{error}</small></span>
      <button onClick={() => void retryLoad()} type="button"><RefreshCw aria-hidden size={15} />{t("capabilities.retry")}</button>
    </section>;
  }
  if (!selected) {
    return <p className="personal-capability-empty">{t("machine.capabilityEmpty")}</p>;
  }

  return (
    <section className="personal-capability-settings" data-revision={inspection?.revision}>
      <div>
        <details className="personal-capability-scope-note">
          <summary><ShieldCheck aria-hidden size={17} />{t("machine.liveDefault")}</summary>
          <p>{t("machine.liveDefaultDescription")}</p>
        </details>
        {section === "other" ? (
          <details className="personal-capability-scope-note">
            <summary><ShieldCheck aria-hidden size={17} />{locale === "zh-CN" ? "帮助改进 LoopX：匿名使用统计（自愿开启）" : "Help improve LoopX: optional anonymous usage"}</summary>
            <p>{locale === "zh-CN"
              ? "默认关闭。开启后每天最多发送一次随机安装 ID、LoopX 版本、系统类型、Python 版本和安装渠道；不发送项目、Goal、路径或命令。此选择影响这台机器后续的 LoopX 命令。"
              : "Off by default. If enabled, send a random installation ID, LoopX version, OS family, Python version and install channel at most once daily. No projects, Goals, paths or commands. Your choice applies to future LoopX commands on this machine."}</p>
            <p>{locale === "zh-CN" ? "机器所有者在终端开启：" : "Owner opt-in:"} <code>loopx usage-ping enable</code> · {locale === "zh-CN" ? "检查：" : "Inspect:"} <code>loopx usage-ping status</code> · {locale === "zh-CN" ? "关闭并删除 ID：" : "Disable and forget ID:"} <code>loopx usage-ping disable</code></p>
            <p><a href={locale === "zh-CN"
              ? "https://github.com/loopx-project/loopx/blob/main/docs/reference/usage-ping.zh-CN.md"
              : "https://github.com/loopx-project/loopx/blob/main/docs/reference/usage-ping.md"} target="_blank" rel="noreferrer">{locale === "zh-CN" ? "隐私与收集服务说明" : "Privacy and collector details"}</a></p>
          </details>
        ) : null}
      </div>

      {/* The catalog workbench is the only flexible block on this surface. It
          lives in one body element so the surface keeps exactly two grid rows
          however many notices the editor needs. */}
      <div className="personal-capability-body">
        {inspection?.status === "invalid" ? (
          <section className="personal-machine-error" data-testid="machine-invalid-repair" role="alert">
            <strong>{t("machine.invalidStoredConfiguration")}</strong>
            <p>{t("machine.invalidStoredConfigurationDescription")}</p>
          </section>
        ) : null}

        <div className="personal-capability-layout">
        <CapabilityCatalogNavigation capabilities={capabilities} locale={locale} onSelect={setSelectedCapabilityId} scope="machine" selectedCapabilityId={selected.capability_id} showScope={section !== "steward"} t={t} />

        <article aria-label={selected.display_name} className="personal-capability-detail" tabIndex={0}>
          <CapabilityDetailHeader capability={selectedRaw} locale={locale}
            source={selected.available_scopes.includes("machine") ? configured ? "machine_default" : "capability_default" : undefined} />
          <CapabilityEditorStatus available={editorAvailable} t={t} description={!selected.available_scopes.includes("machine") ? t("machine.goalOnly")
              : t("machine.editorUnavailableDescription")} />

          {selected.capability_id === "periodic_report" ? (
            <section className="personal-capability-behavior-note">
              <ShieldCheck aria-hidden size={18} />
              <div><strong>{selectedCurrent?.schedule
                ? (locale === "zh-CN" ? "日历与阶段汇报" : "Calendar and stage reports")
                : t("machine.periodicReportActivation")}</strong><p>{selectedCurrent?.schedule
                ? selectedCurrent.enabled === true
                  ? (locale === "zh-CN" ? "已配置日历计划，由现有唤醒检查；是否送达请核对报告回执。" : "A calendar schedule is configured and checked by existing wakes. Verify delivery in the report receipt.")
                  : (locale === "zh-CN" ? "日历计划已保存；启用此能力后才会检查和投递。" : "The schedule is saved; enable this capability to check and deliver reports.")
                : t("machine.periodicReportActivationDescription")}</p></div>
            </section>
          ) : null}

          {selected.capability_id === "change_quality_qualification" ? (
            <section className="personal-capability-behavior-note">
              <ShieldCheck aria-hidden size={18} />
              <div><strong>{t("machine.changeQualityActivation")}</strong><p>{t("machine.changeQualityActivationDescription")}</p></div>
            </section>
          ) : null}

          {selected.capability_id === "todo_replan_cadence" ? (
            <section className="personal-capability-behavior-note">
              <ShieldCheck aria-hidden size={18} />
              <div><strong>{t("machine.replanCadenceActivation")}</strong><p>{t("machine.replanCadenceActivationDescription")}</p></div>
            </section>
          ) : null}

          {selected.capability_id === "steward_executor" ? (
            <section className="personal-capability-behavior-note">
              <ShieldCheck aria-hidden size={18} />
              <div><strong>{locale === "zh-CN" ? "管家模型与思考深度" : "Steward model and reasoning"}</strong><p>{locale === "zh-CN"
                ? "这里设置本机管家新会话的默认模型和思考深度。已有会话可能继续使用原来的分配；配置成功不代表正在运行的会话已切换。"
                : "Choose the model and reasoning effort for new steward sessions on this machine. Existing sessions may retain their earlier allocation; saving a default does not switch a running session."}</p></div>
            </section>
          ) : null}

          {selected.capability_id === "pull_request_review" ? (
            <section className="personal-capability-behavior-note">
              <ShieldCheck aria-hidden size={18} />
              <div><strong>{locale === "zh-CN" ? "只改变队列排序" : "Queue ordering only"}</strong><p>{locale === "zh-CN"
                ? "默认先审阅其他开发者的 PR；选择 owner-first 才会优先当前已认证审阅者自己的 PR。此配置不会发布 review、写 Todo、push 或 merge。"
                : "The default reviews other developers' PRs first; choose owner-first only when the authenticated reviewer's own PRs should lead. This setting never posts a review, writes Todos, pushes, or merges."}</p></div>
            </section>
          ) : null}

          {editorAvailable ? <>{editorMode === "json" || !selected.configuration_editor.fields.some((field) => field.key === "enabled" && field.input_kind === "boolean") ? <div className="personal-capability-editor-mode">
            <button onClick={() => changeMode(editorMode === "guided" ? "json" : "guided")} type="button">
              <Code2 aria-hidden size={14} />{t(editorMode === "guided" ? "machine.editJson" : "machine.backToForm")}
            </button>
          </div> : null}

          {editorMode === "guided" ? (
            <section className="personal-capability-field-summary">
              <CapabilityConfigurationFields copy={localizedCapabilityFieldCopy(locale)} disabled={Boolean(busy)} editor={selected.configuration_editor}
                omitKeys={selected.capability_id === "steward_executor" && draft.selection_policy !== "flexible" ? ["eligible_endpoints"] : []}
                onChange={changeDraft} value={draft}
                enabledAction={<button className="personal-capability-edit-json" onClick={() => changeMode("json")} type="button"><Code2 aria-hidden size={14} />{t("machine.editJson")}</button>} />
              {!editorValid ? <p className="personal-machine-validation" role="alert">{t("machine.requiredFields")}</p> : null}
            </section>
          ) : (
            <label className="personal-capability-json-editor" htmlFor="machine-configuration-json">
              <span>{t("machine.jsonConfiguration")}</span>
              <textarea aria-describedby="machine-configuration-json-help" disabled={Boolean(busy)} id="machine-configuration-json" onChange={(event) => { setJsonDraft(event.target.value); setPreview(null); setError(null); }} rows={12} spellCheck={false} value={jsonDraft} />
              <small id="machine-configuration-json-help">{t("machine.jsonConfigurationHelp")}</small>
              {!parsedJsonDraft ? <span className="personal-machine-validation" role="alert">{t("machine.jsonInvalid")}</span> : null}
            </label>
          )}</> : null}

          {error ? <p className="personal-machine-error" role="alert">{error}</p> : null}
          {notice ? <p className="personal-machine-notice" role="status" aria-live="polite"><Check aria-hidden size={16} />{notice}</p> : null}

          {preview ? (
            <section aria-label={t("machine.preview")} className="personal-machine-preview">
              <header><strong>{t("machine.preview")}</strong><span>{t(`machine.action.${preview.action}`)}</span></header>
              <dl>
                <div><dt>{t("machine.currentRevision")}</dt><dd title={preview.current_revision}>{shortRevision(preview.current_revision)}</dd></div>
                <div><dt>{t("machine.desiredRevision")}</dt><dd title={preview.desired_revision}>{shortRevision(preview.desired_revision)}</dd></div>
                <div><dt>{t("machine.changedNamespaces")}</dt><dd>{preview.changed_namespaces.join(", ") || t("common.none")}</dd></div>
              </dl>
              <p>{t("machine.previewLocked")}</p>
            </section>
          ) : null}

          {transaction?.rollback_available && transaction.transaction_id ? (
            <section className="personal-machine-rollback">
              <div><strong>{t("machine.rollbackAvailable")}</strong><p>{rollbackPlan ? t("machine.rollbackPreviewDescription") : t("machine.rollbackDescription")}</p></div>
              <button className="personal-secondary-action" disabled={Boolean(busy) || Boolean(rollbackPlan && !rollbackPlan.rollback_allowed)} onClick={() => void (rollbackPlan ? applyRollback() : previewRollback())} type="button">
                <RotateCcw aria-hidden size={15} />
                {busy === "rollback" || busy === "rollback-preview" ? t("common.loading") : rollbackPlan ? t("machine.confirmRollback") : t("machine.previewRollback")}
              </button>
            </section>
          ) : null}

          {editorAvailable ? <footer className="personal-capability-actions">
            {configured ? <button className="is-danger" disabled={Boolean(busy)} onClick={() => void createRemovalPreview()} type="button"><Trash2 aria-hidden size={15} />{t("machine.previewRemoval")}</button> : null}
            <button disabled={Boolean(busy) || !editorValid} onClick={() => void createPreview()} type="button">{busy === "preview" ? t("common.loading") : t("machine.previewChanges")}</button>
            <button className="is-primary" disabled={Boolean(busy) || !preview} onClick={() => void applyPreview()} type="button">{busy === "apply" ? t("common.loading") : t("machine.applyPreview")}</button>
          </footer> : null}
          {selected.available_scopes.includes("machine") ? <CapabilityConfigurationSummary key={selected.capability_id}
            values={[{ label: t("machine.currentValue"), value: selectedCurrent }, { label: t("capabilities.defaultValue"), value: selected.default }]}
            t={t}
          /> : null}
        </article>
        </div>
      </div>
    </section>
  );
}
