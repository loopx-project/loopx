import { ChevronDown } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { discoverAgentModelOptions, type AgentModelDiscovery, type ChatSessionSummary } from "../../data/chat";
import { chatModelControls } from "./chat-model-controls";
import { useWorkspaceI18n } from "./i18n";
import { WorkspaceSelect } from "./workspace-select";

/** Reported models stay visible; missing observations belong in the settings disclosure. */
export function ChatModelMenu({ agentId, goalId, ordinary, model = "", effort, readback, onModel, onEffort, onOpenDefaults }: {
  agentId: string; goalId?: string; ordinary: boolean; model?: string; effort?: string;
  readback?: ChatSessionSummary; onModel?: (value: string) => void; onEffort?: (value: string) => void;
  onOpenDefaults?: () => void;
}) {
  const { locale } = useWorkspaceI18n();
  const zh = locale === "zh-CN";
  const root = useRef<HTMLDetailsElement>(null);
  const [open, setOpen] = useState(false);
  const [discovery, setDiscovery] = useState<AgentModelDiscovery | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!open || !ordinary) return;
    const controller = new AbortController();
    setDiscovery(null); setLoading(true); setError(null);
    void discoverAgentModelOptions(agentId, controller.signal, goalId, model)
      .then(result => { if (!controller.signal.aborted) { setDiscovery(result); setLoading(false); } })
      .catch(cause => { if (!controller.signal.aborted) {
        setError(cause instanceof Error ? cause.message : (zh ? "配置读取失败" : "Settings unavailable")); setLoading(false);
      } });
    return () => controller.abort();
  }, [open, ordinary, agentId, goalId, model, retry, zh]);
  useEffect(() => {
    const close = (event: PointerEvent | KeyboardEvent) => {
      if (!root.current?.open) return;
      if (event instanceof KeyboardEvent) {
        if (event.key !== "Escape" || event.defaultPrevented) return;
        root.current?.querySelector("summary")?.focus();
      } else if (root.current?.contains(event.target as Node)) return;
      if (root.current) root.current.open = false;
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", close);
    return () => { document.removeEventListener("pointerdown", close); document.removeEventListener("keydown", close); };
  }, []);
  const controls = chatModelControls(discovery, model, effort, readback);
  const observed = controls.observedModel ?? (zh ? "模型设置" : "Model settings");
  const observedSettings = controls.observedModel ? [controls.observedEffort,
    controls.observedTier ? (["priority", "fast"].includes(controls.observedTier) ? "Fast" : controls.observedTier) : null,
  ].filter(Boolean).join(" · ") : "";
  return <details className="personal-chat-model-menu" ref={root} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary aria-label={`${zh ? "模型与思考深度" : "Model and reasoning"} · ${observed}${observedSettings ? ` · ${observedSettings}` : ""}`}>
      <span><span className="personal-model-current">{observed}</span>{observedSettings ? <small>{observedSettings}</small> : null}</span>
      <ChevronDown aria-hidden size={16}/>
    </summary>
    <div className="personal-chat-model-panel">
      <strong>{zh ? "当前会话" : "Current conversation"}</strong>
      <p>{controls.observedModel
        ? [controls.observedModel, controls.observedEffort ? `${zh ? "推理" : "Reasoning"} ${controls.observedEffort}` : null,
          controls.observedTier ? `${zh ? "速度" : "Speed"} ${["priority", "fast"].includes(controls.observedTier) ? "Fast" : controls.observedTier}` : null].filter(Boolean).join(" · ")
        : (zh ? "运行时尚未回传实际模型" : "Runtime has not reported the actual model")}</p>
      {ordinary ? <>
        <strong>{zh ? "新对话配置" : "New conversation settings"}</strong>
        <p>{zh ? "用于下次新对话，保留原历史和活动回合。" : "Applies to a new conversation; existing history and active work are preserved."}</p>
        {controls.modelOptions.length ? <WorkspaceSelect ariaLabel={zh ? "选择模型" : "Select model"}
          className="personal-agent-select personal-model-select" onChange={value => onModel?.(value)}
          prefixLabel={zh ? "模型" : "Model"} value={model} options={[
            ...(!model ? [{ disabled: true, label: zh ? "选择模型" : "Choose model", value: "" }] : []),
            ...(model && !controls.modelOptions.some(option => option.value === model)
              ? [{ disabled: true, label: `${model} · ${zh ? "未列入可选模型" : "Not in catalog"}`, value: model }] : []),
            ...controls.modelOptions.map(option => ({ label: option.value === model ? option.value : option.label, value: option.value })),
          ]}/> : null}
        {controls.reasoningOptions.length && onEffort ? <WorkspaceSelect ariaLabel={zh ? "选择思考深度" : "Select reasoning effort"}
          className="personal-agent-select personal-model-select" onChange={onEffort}
          prefixLabel={zh ? "思考深度" : "Reasoning"} value={controls.effort} options={[
            { value: "", label: zh ? "沿用默认" : "Use default" },
            ...(controls.effort && !controls.reasoningOptions.some(option => option.value === controls.effort)
              ? [{ value: controls.effort, label: `${controls.effort} · ${zh ? "当前不可用" : "Unavailable"}`, disabled: true }] : []),
            ...controls.reasoningOptions,
          ]}/> : null}
        {discovery && controls.modelOptions.length > 0 && !controls.reasoningOptions.length && model
          ? <p>{zh ? "运行时未提供思考深度选项" : "Runtime provides no reasoning choices"}</p> : null}
        {discovery?.supported === false ? <p>{zh ? "执行器未提供可选模型与思考深度" : "Executor provides no model or reasoning choices"}</p> : null}
        {loading ? <p role="status">{zh ? "正在读取可选配置…" : "Reading available settings…"}</p> : null}
        {error ? <><p role="alert">{error}</p><button className="personal-action-button" type="button" onClick={() => setRetry(value => value + 1)}>{zh ? "配置读取失败，重试" : "Settings unavailable · Retry"}</button></> : null}
        {onOpenDefaults ? <button className="personal-action-button" type="button" onClick={onOpenDefaults}>
          {zh ? "默认对话配置" : "Conversation defaults"}
        </button> : null}
      </> : null}
    </div>
  </details>;
}
