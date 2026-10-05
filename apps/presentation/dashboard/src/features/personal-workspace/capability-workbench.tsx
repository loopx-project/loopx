import { AlertTriangle, ShieldCheck, SlidersHorizontal } from "lucide-react";

import type { CapabilityConfigurationCatalog } from "../../data/chat";
import { useWorkspaceI18n, type WorkspaceLocale, type WorkspaceTranslate } from "./i18n";
import { localizeCapability, localizedCapabilityFieldCopy } from "./capability-localization";

type CapabilityDescriptor = CapabilityConfigurationCatalog["capabilities"][number];

export function canEditCapability(capability: CapabilityDescriptor, scope: "goal" | "machine") {
  return capability.available_scopes.includes(scope)
    && (scope !== "machine" || Boolean(capability.machine_namespace))
    && capability.configuration_editor.editable
    && capability.configuration_editor.writable_scopes.includes(scope);
}

export function CapabilityConfigurationSummary({ values, t }: Readonly<{
  values: ReadonlyArray<{ label: string; value: Record<string, unknown> | undefined }>;
  t: WorkspaceTranslate;
}>) {
  return <details className="personal-capability-raw-values">
    <summary>{t("capabilities.rawJson")}</summary>
    <div className="personal-capability-value-grid">
      {values.map(({ label, value }) => <section key={label}>
        <strong>{label}</strong><pre>{value ? JSON.stringify(value, null, 2) : "—"}</pre>
      </section>)}
    </div>
  </details>;
}

function CapabilityEffectiveSource({ source, t }: Readonly<{
  source?: NonNullable<CapabilityDescriptor["effective_configuration"]>["source"];
  t: WorkspaceTranslate;
}>) {
  return source ? <p className="personal-capability-effective-source">
      <ShieldCheck aria-hidden size={15} />
      <span><strong>{t("capabilities.effectiveSource")}</strong>{t(`capabilities.source.${source}`)}</span>
    </p> : null;
}

export function CapabilityEditorStatus({ available, description, t }: Readonly<{
  available: boolean;
  description: string;
  t: WorkspaceTranslate;
}>) {
  if (available) return null;
  return <section className="personal-capability-editor-status is-read-only">
    <AlertTriangle aria-hidden size={18} />
    <div><strong>{t("capabilities.readOnly")}</strong><p>{description}</p></div>
  </section>;
}

function capabilityPresentationTier(capability: CapabilityDescriptor) {
  if (capability.availability === "retired") return 5;
  if (capability.availability?.includes("experimental")) return 4;
  if (capability.capability_id === "multi_subagent") return 3;
  if (capability.configuration_editor.writable_scopes.length === 0) return 2;
  if (capability.availability === "supported_explicit_opt_in") return 2;
  if (capability.availability === "supported_explicit_override") return 0;
  return 1;
}

export function orderCapabilitiesForPresentation(
  capabilities: CapabilityDescriptor[],
  locale: WorkspaceLocale,
) {
  return [...capabilities].sort((left, right) => {
    const tierDifference = capabilityPresentationTier(left) - capabilityPresentationTier(right);
    if (tierDifference !== 0) return tierDifference;
    const localizedLeft = localizeCapability(left, locale);
    const localizedRight = localizeCapability(right, locale);
    return localizedLeft.display_name.localeCompare(localizedRight.display_name, locale)
      || left.capability_id.localeCompare(right.capability_id);
  });
}

export function CapabilityCatalogNavigation({
  capabilities,
  locale,
  onSelect,
  scope,
  selectedCapabilityId,
  showScope = true,
  t,
}: Readonly<{
  capabilities: CapabilityDescriptor[];
  locale: WorkspaceLocale;
  onSelect: (capabilityId: string) => void;
  scope: "goal" | "machine";
  selectedCapabilityId: string;
  showScope?: boolean;
  t: WorkspaceTranslate;
}>) {
  return (
    <nav aria-label={t(scope === "goal" ? "capabilities.catalog" : "machine.capabilityCatalog")} className="personal-capability-list" tabIndex={0}>
      {orderCapabilitiesForPresentation(capabilities, locale).map((rawCapability) => {
        const capability = localizeCapability(rawCapability, locale);
        return (
          <button
            aria-current={selectedCapabilityId === capability.capability_id ? "page" : undefined}
            key={capability.capability_id}
            onClick={() => onSelect(capability.capability_id)}
            type="button"
          >
            <span>
              <strong>{capability.display_name}</strong>
            </span>
            {showScope ? <em>{t(capability.available_scopes.includes(scope)
              ? scope === "goal" ? "capabilities.goalScope" : "capabilities.machineScope"
              : scope === "machine" ? "capabilities.goalScope" : "capabilities.machineScope")}</em> : null}
          </button>
        );
      })}
    </nav>
  );
}

export function CapabilityDetailHeader({ capability, locale, source }: Readonly<{
  capability: CapabilityDescriptor;
  locale: WorkspaceLocale;
  source?: NonNullable<CapabilityDescriptor["effective_configuration"]>["source"];
}>) {
  const { t } = useWorkspaceI18n();
  const localized = localizeCapability(capability, locale);
  // Capability ownership remains visible even when this editor is read-only.
  const scopes = capability.configuration_editor.supported_scopes;
  const scopeKind = scopes.includes("machine")
    ? scopes.includes("goal") ? "machineAndGoal" : "machine"
    : scopes.includes("goal") ? "goal" : "unknown";
  return (
    <header>
      <span className="personal-settings-icon"><SlidersHorizontal aria-hidden size={18} /></span>
      <div>
        <div className="personal-capability-heading-row">
          <h2>{localized.display_name}</h2>
          <CapabilityEffectiveSource source={source} t={t} />
        </div>
        <p className="personal-capability-supported-scopes">{t(`capabilities.configurationScope.${scopeKind}`)}</p>
        {capability.context_contribution && (
          <details className="personal-capability-help" data-testid="capability-context-phases">
            <summary>{locale === "zh-CN" ? "主 Agent 协作指导" : "Coordinator workflow guidance"}</summary>
            <p>{locale === "zh-CN"
              ? "随能力开启。支持以下阶段；此处展示能力范围，不能证明某次运行已读取或采纳。"
              : "Enabled with this capability. These are supported phases, not proof that a run read or adopted the guidance."}</p>
            <dl>{capability.context_contribution.supported_phases.map((phase) => (
              <div key={phase}>
                <dt><code>{phase}</code></dt>
                <dd>{contextPhaseCopy[phase][locale === "zh-CN" ? "zh" : "en"]}</dd>
              </div>
            ))}</dl>
            <p>{locale === "zh-CN"
              ? "LoopX Turn 在请求与结果中提供上下文；原生工具入口由 LoopX skill 调用同一只读接口。执行与采纳需另看运行证据。"
              : "LoopX Turn includes context in requests and results. For native tools, the LoopX skill reads the same interface. Execution and adoption require run evidence."}</p>
          </details>
        )}
        <details className="personal-capability-help" key={capability.capability_id}>
          <summary>{locale === "zh-CN" ? "配置说明" : "Configuration help"}</summary>
          <p>{localized.description}</p>
          <dl>{capability.configuration_editor.fields.map((field) => {
            const copy = localizedCapabilityFieldCopy(locale, capability.capability_id)[field.key];
            const description = copy?.description ?? field.description;
            return description ? <div key={field.key}><dt>{copy?.label ?? field.label}</dt><dd>{description}</dd></div> : null;
          })}</dl>
        </details>
      </div>
    </header>
  );
}


const contextPhaseCopy = {
  before_plan: { zh: "规划前：识别独立问题并保留主 Agent 的核验与整合职责。", en: "Before planning: identify independent questions and retain coordinator validation and integration." },
  before_delegate: { zh: "委派前：明确子任务边界、模型偏好及预期证据。", en: "Before delegation: specify task boundaries, model preferences and expected evidence." },
  after_delegate_result: { zh: "回收后：核验结果，说明采纳决定并关联计划与成果。", en: "After results: validate evidence, explain acceptance and link plans and deliverables." },
} as const;
