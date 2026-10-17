import type {CapabilityConfigurationCatalog} from "../../data/chat";
import type {WorkspaceLocale} from "./i18n";

type Capability = CapabilityConfigurationCatalog["capabilities"][number];

// Local navigation only. Each purpose retains its native capability/configuration owner.
const purposes = ["decision_context", "reward_memory", "content_ops"] as const;
export function isMemoryCapability(id: string) {return purposes.some(purpose => purpose === id);}
export function memorySettingsTitle(locale: WorkspaceLocale) {return locale === "zh-CN" ? "记忆与资料" : "Memory & sources";}
export function memoryCapabilities(capabilities: readonly Capability[]) {
  return purposes.flatMap(id => capabilities.filter(capability => capability.capability_id === id));
}
export function memoryRetrievalLabel(provider: unknown, locale: WorkspaceLocale) {
  const cn = locale === "zh-CN";
  if (provider === "bm25") return cn ? "BM25 · 本地词匹配" : "BM25 · local lexical search";
  if (provider === "openviking") return cn ? "OpenViking · 语义检索" : "OpenViking · semantic search";
  return cn ? "配置指定的检索器" : "Configured retrieval provider";
}

export function MemorySettingsHeader({capabilities, locale, onSelect, selectedId, onRefresh, disabled}: Readonly<{
  capabilities: readonly Capability[];
  locale: WorkspaceLocale;
  onSelect: (id: string) => void;
  selectedId: string;
  onRefresh: () => void;
  disabled: boolean;
}>) {
  const cn = locale === "zh-CN";
  const copy = {
    decision_context: {name: cn ? "资料召回" : "Find evidence", mode: cn ? "本页临时使用" : "Temporary, in this page",
      effect: cn ? "按问题查找已有材料。不会保存配置或开启 Agent 的自动记忆。" : "Find existing material for a question. This does not save configuration or enable automatic Agent memory."},
    reward_memory: {name: cn ? "经验学习" : "Learn from outcomes", mode: cn ? "持久配置 · 实验功能" : "Saved configuration · experimental",
      effect: cn ? "为指定 Agent 配置经验复用；旧版共享绑定单独管理。配置、自动召回和写回的实际接入分别验证。" : "Configure experience reuse for a named Agent, or manage a legacy shared binding separately. Configuration, automatic recall and writeback each require their own verification."},
    content_ops: {name: cn ? "素材与风格" : "Reference styles", mode: cn ? "只读素材目录" : "Read-only reference catalog",
      effect: cn ? "查找参考素材的表达和结构，保留来源署名；与事实证据、Agent 经验分开使用。" : "Find source-attributed writing styles and structures, separately from factual evidence and Agent experience."},
  };
  const available = memoryCapabilities(capabilities);
  const selected = purposes.find(id => id === selectedId);
  return <header className="personal-memory-header">
    <div className="personal-memory-title"><h2>{memorySettingsTitle(locale)}</h2>
      <button disabled={disabled} onClick={onRefresh} type="button">{cn ? "重新读取配置" : "Reload configuration"}</button>
    </div>
    <div aria-label={cn ? "记忆用途" : "Memory purpose"} className="personal-memory-purposes" role="tablist"
      onKeyDown={event => {
        if (disabled) return;
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const index = available.findIndex(item => item.capability_id === selectedId);
        const next = event.key === "Home" ? 0 : event.key === "End" ? available.length - 1
          : (index + (event.key === "ArrowRight" ? 1 : -1) + available.length) % available.length;
        const id = available[next]?.capability_id;
        if (id) {onSelect(id); document.getElementById(`memory-purpose-${id}`)?.focus();}
      }}>
      {available.map(item => {
        const id = purposes.find(purpose => purpose === item.capability_id)!;
        return <button aria-controls={`memory-panel-${id}`} aria-selected={selectedId === id} disabled={disabled}
          id={`memory-purpose-${id}`} key={id} onClick={() => onSelect(id)} role="tab"
          tabIndex={selectedId === id ? 0 : -1} type="button">{copy[id].name}</button>;
      })}
    </div>
    {selected ? <div className="personal-memory-purpose-effect">
      <strong>{copy[selected].mode}</strong><p>{copy[selected].effect}</p>
    </div> : null}
    <details className="personal-capability-help">
      <summary>{cn ? "用途、检索器和用户偏好有什么区别？" : "Purposes, providers and user preferences"}</summary>
      <p>{cn ? "OpenViking、BM25 是检索实现，不是另一套记忆总开关。各用途保留自己的资料范围和配置，不会自动共享 Agent 私有内容。" : "OpenViking and BM25 are retrieval implementations, not separate memory master switches. Each purpose retains its own scope and configuration; Agent-private content is not automatically shared."}</p>
      <p>{cn ? "你的明确要求与纠正使用完整的当前偏好版本，与检索出的参考材料分开；不能用搜索排名替代偏好读取。" : "Explicit instructions and corrections have a complete current preference view, separate from advisory search results. Ranked retrieval cannot replace that preference read."}</p>
    </details>
  </header>;
}
