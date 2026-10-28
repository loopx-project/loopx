import {AlertTriangle, Check, CircleHelp, Code2, RefreshCw, RotateCcw, ShieldCheck, Trash2} from "lucide-react";
import {ContextHelp} from "./context-help";
import { PerformanceDiagnosisPanel } from "./performance-diagnosis-panel";
import { UsageStatisticsSettings } from "./usage-statistics-settings";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

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
import { changeStewardExecutorDraft, goalStorageEditorValue, projectEditableCapabilityConfiguration, replanCadenceEditorValue } from "../../data/capability-configuration";
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
  if (capability.capability_id === "pull_request_review" && Object.hasOwn(draft, "review_order")) {
    delete complete.review_priority;
  }
  // The guided steward editor owns the v1 selection-policy fields. Opening an
  // installed v0 preference in that form is an explicit migration preview;
  // JSON mode can still submit the legacy shape unchanged when needed.
  if (capability.capability_id === "goal_storage" || (capability.capability_id === "steward_executor"
    && (Object.hasOwn(draft, "selection_policy") || Object.hasOwn(draft, "eligible_endpoints")))) {
    complete.schema_version = configurationObject(capability.default).schema_version;
  }
  if (capability.capability_id === "todo_replan_cadence" && Object.hasOwn(draft, "count_unit")) {
    complete.schema_version = configurationObject(capability.default).schema_version;
    delete complete.completed_todos;
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

export function MachineConfigurationSettings({ section, onChanged }: {
  section: "steward" | "conversation" | "other";
  onChanged?: () => void;
}) {
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
  const [busy, setBusy] = useState<"load" | "readback" | "preview" | "apply" | "rollback-preview" | "rollback" | null>("load");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [readbackPending, setReadbackPending] = useState<string | null>(null);
  const editorGeneration = useRef(0);

  const capabilities = useMemo(() => orderCapabilitiesForPresentation(
    (inspection?.capability_catalog.capabilities ?? []).filter((capability) =>
      capability.available_scopes.includes("machine")
      && (section === "conversation" ? capability.capability_id === "codex_chat" : section === "steward"
        ? capability.capability_id === "steward_executor" || capability.capability_id === "manager_runtime"
        : capability.capability_id !== "steward_executor" && capability.capability_id !== "manager_runtime" && capability.capability_id !== "codex_chat")),
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

  async function retryReadback() {
    if (busy || !readbackPending) return;
    const generation = editorGeneration.current;
    setBusy("readback");
    setError(null);
    try {
      const next = await fetchMachineConfiguration();
      if (generation !== editorGeneration.current) return;
      setInspection(next);
      setReadbackPending(null);
      setNotice(locale === "zh-CN" ? "已重新读取当前配置。" : "Current configuration reloaded.");
      onChanged?.();
    } catch (cause) {
      if (generation === editorGeneration.current) setError(cause instanceof Error ? cause.message : t("machine.loadError"));
    } finally {
      if (generation === editorGeneration.current) setBusy(null);
    }
  }

  async function retryLoad() {
    if (busy) return;
    const generation = editorGeneration.current;
    setBusy("load");
    setError(null);
    try {
      const next = await fetchMachineConfiguration();
      if (generation === editorGeneration.current) setInspection(next);
    } catch (cause) {
      if (generation === editorGeneration.current) setError(cause instanceof Error ? cause.message : t("machine.loadError"));
    } finally {
      if (generation === editorGeneration.current) setBusy(null);
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

  useLayoutEffect(() => {
    editorGeneration.current++;
    if (!selected) return () => { editorGeneration.current++; };
    const current = currentConfiguration(inspection, selected);
    const editable = projectEditableCapabilityConfiguration(
      selected.configuration_editor,
      selected.capability_id === "todo_replan_cadence" ? replanCadenceEditorValue(current ?? selected.default)
        : selected.capability_id === "goal_storage" ? goalStorageEditorValue(current ?? selected.default) : current ?? selected.default,
      selected.default,
    );
    const complete = completeMachineConfiguration(selected, current, editable);
    setDraft(editable);
    setJsonDraft(JSON.stringify(complete, null, 2));
    setEditorMode(editorAvailable ? "guided" : "json");
    setPreview(null);
    setPreviewOperation("upsert");
    setRollbackPlan(null);
    setBusy(null);
    return () => { editorGeneration.current++; };
  }, [inspection, selectedCapabilityId, locale, section]);

  useLayoutEffect(() => {
    // A transaction and its feedback belong to the capability that was edited.
    setTransaction(null);
    setError(null);
    setNotice(null);
  }, [selected?.capability_id]);

  function changeDraft(key: string, value: unknown) {
    if (busy) return;
    setDraft((current) => selected?.capability_id === "periodic_report"
      ? withReportScheduleTimezone(current, key, value)
      : selected?.capability_id === "steward_executor"
        ? changeStewardExecutorDraft(current, key, value) : { ...current, [key]: value });
    setPreview(null);
    setPreviewOperation("upsert");
    setError(null);
    setNotice(null);
  }

  function changeMode(mode: EditorMode) {
    if (!selected || busy) return;
    if (mode === "json") {
      setJsonDraft(JSON.stringify(
        completeMachineConfiguration(selected, selectedCurrent, draft),
        null,
        2,
      ));
    } else if (parsedJsonDraft) {
      setDraft(projectEditableCapabilityConfiguration(
        selected.configuration_editor,
        selected.capability_id === "todo_replan_cadence" ? replanCadenceEditorValue(parsedJsonDraft)
          : selected.capability_id === "goal_storage" ? goalStorageEditorValue(parsedJsonDraft) : parsedJsonDraft,
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
    setNotice(null);
  }

  async function setContextDelivery(mode: "off" | "on") {
    if (busy || !editorAvailable || selected?.capability_id !== "context_delivery" || !selected.machine_namespace) return;
    const namespace = selected.machine_namespace;
    const configuration = {...selected.default, mode};
    const generation = editorGeneration.current;
    setBusy("apply"); setError(null); setNotice(null);
    try {
      const plan = await previewMachineConfiguration(namespace, configuration);
      if (generation !== editorGeneration.current) return;
      await applyMachineConfiguration(namespace, configuration, plan.plan_revision);
      if (generation !== editorGeneration.current) { onChanged?.(); return; }
      setReadbackPending(selected.display_name);
      const next = await fetchMachineConfiguration();
      if (generation !== editorGeneration.current) return;
      setInspection(next);
      setReadbackPending(null);
      if (next.machine_configuration?.namespaces[namespace]?.mode !== mode) {
        throw new Error(locale === "zh-CN" ? "保存后的配置已变化，请刷新后重试。" : "The saved configuration changed. Refresh and retry.");
      }
      setNotice(locale === "zh-CN" ? "Codex 偏好已保存。" : "Codex preference saved.");
      onChanged?.();
    } catch (cause) {
      if (generation === editorGeneration.current) setError(cause instanceof Error ? cause.message : t("machine.applyError"));
    } finally { if (generation === editorGeneration.current) setBusy(null); }
  }

  async function createPreview() {
    if (!editorAvailable || !selected?.machine_namespace || !desiredConfiguration || !editorValid || busy) return;
    const generation = editorGeneration.current;
    setBusy("preview");
    setError(null);
    setNotice(null);
    try {
      setPreviewOperation("upsert");
      const next = await previewMachineConfiguration(selected.machine_namespace, desiredConfiguration);
      if (generation === editorGeneration.current) setPreview(next);
    } catch (cause) {
      if (generation === editorGeneration.current) setError(cause instanceof Error ? cause.message : t("machine.previewError"));
    } finally {
      if (generation === editorGeneration.current) setBusy(null);
    }
  }

  async function createRemovalPreview() {
    if (!editorAvailable || !selected?.machine_namespace || !configured || busy) return;
    const generation = editorGeneration.current;
    setBusy("preview");
    setError(null);
    setNotice(null);
    try {
      setPreviewOperation("remove");
      const next = await previewMachineConfigurationRemoval(selected.machine_namespace);
      if (generation === editorGeneration.current) setPreview(next);
    } catch (cause) {
      if (generation === editorGeneration.current) setError(cause instanceof Error ? cause.message : t("machine.previewError"));
    } finally {
      if (generation === editorGeneration.current) setBusy(null);
    }
  }

  async function applyPreview() {
    if (!editorAvailable || !selected?.machine_namespace || !preview || busy || (previewOperation === "upsert" && !desiredConfiguration)) return;
    const generation = editorGeneration.current;
    setBusy("apply");
    setError(null);
    setNotice(null);
    const operation = previewOperation;
    try {
      const result = operation === "remove"
        ? await applyMachineConfigurationRemoval(selected.machine_namespace, preview.plan_revision)
        : await applyMachineConfiguration(selected.machine_namespace, desiredConfiguration!, preview.plan_revision);
      if (generation !== editorGeneration.current) { onChanged?.(); return; }
      setTransaction(result);
      setPreview(null);
      setPreviewOperation("upsert");
      setRollbackPlan(null);
      setReadbackPending(selected.display_name);
      const next = await fetchMachineConfiguration();
      if (generation !== editorGeneration.current) return;
      setInspection(next);
      setReadbackPending(null);
      setNotice(result.status === "applied"
        ? t(operation === "remove" ? "machine.removed" : "machine.applied")
        : t("machine.unchanged"));
      onChanged?.();
    } catch (cause) {
      if (generation !== editorGeneration.current) return;
      setPreview(null);
      setPreviewOperation("upsert");
      setError(cause instanceof Error ? cause.message : t("machine.applyError"));
    } finally {
      if (generation === editorGeneration.current) setBusy(null);
    }
  }

  async function previewRollback() {
    if (!transaction?.transaction_id || busy) return;
    const generation = editorGeneration.current;
    setBusy("rollback-preview");
    setError(null);
    try {
      const next = await previewMachineConfigurationRollback(transaction.transaction_id);
      if (generation === editorGeneration.current) setRollbackPlan(next);
    } catch (cause) {
      if (generation === editorGeneration.current) setError(cause instanceof Error ? cause.message : t("machine.rollbackError"));
    } finally {
      if (generation === editorGeneration.current) setBusy(null);
    }
  }

  async function applyRollback() {
    if (!transaction?.transaction_id || !rollbackPlan || busy) return;
    const generation = editorGeneration.current;
    setBusy("rollback");
    setError(null);
    setNotice(null);
    try {
      await applyMachineConfigurationRollback(transaction.transaction_id, rollbackPlan.plan_revision);
      if (generation !== editorGeneration.current) { onChanged?.(); return; }
      setTransaction(null);
      setRollbackPlan(null);
      setPreview(null);
      setReadbackPending(selected?.display_name ?? t("settings.scope.machine"));
      const next = await fetchMachineConfiguration();
      if (generation !== editorGeneration.current) return;
      setInspection(next);
      setReadbackPending(null);
      setNotice(t("machine.rolledBack"));
      onChanged?.();
    } catch (cause) {
      if (generation !== editorGeneration.current) return;
      setRollbackPlan(null);
      setError(cause instanceof Error ? cause.message : t("machine.rollbackError"));
    } finally {
      if (generation === editorGeneration.current) setBusy(null);
    }
  }

  const diagnosis = section === "other" ? <PerformanceDiagnosisPanel /> : null;
  if (busy === "load") {
    return <>{diagnosis}<div className="personal-machine-loading" role="status">{t("common.loading")}</div></>;
  }
  if (!inspection) {
    return <>{diagnosis}<section className="personal-capability-error" role="alert">
      <AlertTriangle aria-hidden size={18} />
      <span><strong>{t("machine.loadError")}</strong><small>{error}</small></span>
      <button onClick={() => void retryLoad()} type="button"><RefreshCw aria-hidden size={15} />{t("capabilities.retry")}</button>
    </section></>;
  }
  if (!selected) {
    return <>{diagnosis}<ContextHelp label={locale === "zh-CN" ? "暂无本机能力" : "No device capabilities"} icon={CircleHelp}><p className="personal-capability-empty">{t("machine.capabilityEmpty")}</p></ContextHelp></>;
  }

  return (
    <section className="personal-capability-settings" data-revision={inspection?.revision}>
      <div>
        {diagnosis}
        {section === "steward" ? <details className="personal-capability-scope-note">
          <summary><ShieldCheck aria-hidden size={17} />{t("machine.stewardDefaults")}</summary>
          <p>{t("machine.stewardDefaultsDescription")}</p>
        </details> : null}
        {section === "other" ? <UsageStatisticsSettings /> : null}
      </div>

      {/* The catalog workbench is the only flexible block on this surface. It
          lives in one body element so the surface keeps exactly two grid rows
          however many notices the editor needs. */}
      <div className="personal-capability-body">
        {readbackPending ? <section className="personal-capability-recovery" role="status">
          <AlertTriangle aria-hidden size={18} />
          <div><strong>{locale === "zh-CN" ? "配置已写入，读回尚未确认" : "Configuration written; readback unverified"}</strong>
            <p>{readbackPending}</p></div>
          <button disabled={Boolean(busy)} onClick={() => void retryReadback()} type="button">
            <RefreshCw aria-hidden size={15} />{busy === "readback" ? t("common.loading") : t("capabilities.refreshSource")}
          </button>
        </section> : null}
        {inspection?.status === "invalid" ? (
          <section className="personal-machine-error" data-testid="machine-invalid-repair" role="alert">
            <strong>{t("machine.invalidStoredConfiguration")}</strong>
            <p>{t("machine.invalidStoredConfigurationDescription")}</p>
          </section>
        ) : null}

        <div className="personal-capability-layout">
        <CapabilityCatalogNavigation capabilities={capabilities} locale={locale} onSelect={setSelectedCapabilityId} scope="machine" selectedCapabilityId={selected.capability_id} showScope={false} t={t} />

        <article aria-label={selected.display_name} className="personal-capability-detail" tabIndex={0}>
          <CapabilityDetailHeader capability={selectedRaw} locale={locale}
            source={selected.available_scopes.includes("machine") ? configured ? "machine_default" : "capability_default" : undefined} />
          <CapabilityEditorStatus available={editorAvailable} t={t} description={!selected.available_scopes.includes("machine") ? t("machine.goalOnly")
              : t("machine.editorUnavailableDescription")} />

          {selected.capability_id === "goal_storage" ? (
            <section className="personal-capability-behavior-note">
              <ShieldCheck aria-hidden size={18} />
              <div><strong>{locale === "zh-CN" ? "仅影响此后创建的 Goal" : "Future Goals only"}</strong><ContextHelp label={locale === "zh-CN" ? "创建策略与迁移" : "Creation policy and migration"}><p>{locale === "zh-CN"
                ? "2.0 新建 Goal 必须启用 canonical 创建，并采用所选执行策略。旧的关闭设置不会自动改变；启用后再重试创建。已有 Goal 需备份、停止写入并导入；这里不迁移已有数据，也不授予工具权限。"
                : "2.0 requires canonical creation with the selected execution policy. An older disabled setting is not changed automatically; enable it before retrying creation. Back up, stop writers and import existing Goals separately. This setting neither migrates existing data nor grants tool permissions."}</p></ContextHelp></div>
            </section>
          ) : null}

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
              <div><strong>{locale === "zh-CN" ? "管家模型与思考深度" : "Steward model and reasoning"}</strong><ContextHelp label={locale === "zh-CN" ? "管家默认值的生效范围" : "Steward default scope"}><p>{locale === "zh-CN"
                ? "这里设置本机管家新会话的默认模型和思考深度。已有会话可能继续使用原来的分配；配置成功不代表正在运行的会话已切换。"
                : "Choose the model and reasoning effort for new steward sessions on this machine. Existing sessions may retain their earlier allocation; saving a default does not switch a running session."}</p></ContextHelp></div>
            </section>
          ) : null}

          {selected.capability_id === "pull_request_review" ? (
            <section className="personal-capability-behavior-note">
              <ShieldCheck aria-hidden size={18} />
              <div><strong>{locale === "zh-CN" ? "只改变队列排序" : "Queue ordering only"}</strong><ContextHelp label={locale === "zh-CN" ? "PR 队列顺序" : "PR queue order"}><p>{locale === "zh-CN"
                ? "默认先审阅其他开发者的 PR；选择 owner-first 才会优先当前已认证审阅者自己的 PR。此配置不会发布 review、写 Todo、push 或 merge。"
                : "The default reviews other developers' PRs first; choose owner-first only when the authenticated reviewer's own PRs should lead. This setting never posts a review, writes Todos, pushes, or merges."}</p></ContextHelp></div>
            </section>
          ) : null}

          {selected.capability_id === "context_delivery" ? <section aria-label={locale === "zh-CN" ? "使用差分上下文的应用" : "Applications using differential context"} className="personal-capability-field-summary">
            <div className="personal-capability-fields"><label className="is-boolean">
              <span>Codex</span>
              <input aria-label={locale === "zh-CN" ? "在 Codex 中使用差分上下文" : "Use differential context in Codex"}
                type="checkbox" role="switch" checked={selectedCurrent?.mode === "on"} disabled={Boolean(busy) || !editorAvailable}
                onChange={(event) => void setContextDelivery(event.target.checked ? "on" : "off")} />
            </label></div>
            <p role="status">{selectedCurrent?.mode === "on"
              ? locale === "zh-CN" ? "已为 Codex 保存开启偏好；宿主就绪与实际使用尚未验证。" : "Codex preference is on; host readiness and actual use are not verified."
              : locale === "zh-CN" ? "Codex 未开启。" : "Off for Codex."}</p>
            <ContextHelp label={locale === "zh-CN" ? "Codex 支持范围与生效条件" : "Codex support and activation requirements"}><p>{locale === "zh-CN"
              ? "目前仅支持 Codex App 心跳和 benchmark 心跳，无法确认或不支持的跑法报错。保存偏好不代表当前会话已采用差分，也不会改变其他应用或显式关闭的实验。宿主准备就绪且旧上下文完整时才使用差分，否则保留完整上下文。私有存储与会话隔离自动管理；每轮回执显示实际传输方式。"
              : "Currently supports only Codex App and benchmark heartbeats; unknown or unsupported declarations return an error. Saving a preference does not establish use in the current session or change other applications or explicitly disabled runs. Delta requires a ready host and complete retained context; otherwise full context is preserved. Private storage and session isolation are automatic; each delivery reports its actual mode."}</p></ContextHelp>
          </section> : null}
          {editorAvailable && selected.capability_id !== "context_delivery" ? <>{editorMode === "json" || !selected.configuration_editor.fields.some((field) => field.key === "enabled" && field.input_kind === "boolean") ? <div className="personal-capability-editor-mode">
            <button disabled={Boolean(busy)} onClick={() => changeMode(editorMode === "guided" ? "json" : "guided")} type="button">
              <Code2 aria-hidden size={14} />{t(editorMode === "guided" ? "machine.editJson" : "machine.backToForm")}
            </button>
          </div> : null}

          {editorMode === "guided" ? (
            <section className="personal-capability-field-summary">
              <CapabilityConfigurationFields copy={localizedCapabilityFieldCopy(locale, selected.capability_id)} disabled={Boolean(busy)} editor={selected.configuration_editor}
                advancedKeys={selected.capability_id === "todo_replan_cadence" ? ["planning_horizon_goal"] : []}
                omitKeys={selected.capability_id === "steward_executor" && draft.selection_policy !== "flexible" ? ["eligible_endpoints"] : []}
                onChange={changeDraft} value={draft}
                enabledAction={<button className="personal-capability-edit-json" disabled={Boolean(busy)} onClick={() => changeMode("json")} type="button"><Code2 aria-hidden size={14} />{t("machine.editJson")}</button>} />
              {!editorValid ? <p className="personal-machine-validation" role="alert">{t("machine.requiredFields")}</p> : null}
            </section>
          ) : (
            <label className="personal-capability-json-editor" htmlFor="machine-configuration-json">
              <span>{t("machine.jsonConfiguration")}</span>
              <textarea aria-describedby="machine-configuration-json-help" disabled={Boolean(busy)} id="machine-configuration-json" onChange={(event) => { setJsonDraft(event.target.value); setPreview(null); setError(null); setNotice(null); }} rows={12} spellCheck={false} value={jsonDraft} />
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

          {editorAvailable && selected.capability_id !== "context_delivery" ? <footer className="personal-capability-actions">
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
