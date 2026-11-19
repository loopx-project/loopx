import { ChevronDown, Clock3 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ChatApiError, discoverAgentModelOptions, type AgentModelDiscovery, type ChatSessionSummary } from "../../data/chat";
import { ContextHelp } from "./context-help";
import { chatModelControls } from "./chat-model-controls";
import { useWorkspaceI18n } from "./i18n";
import { WorkspaceSelect } from "./workspace-select";
import { useComposerMenuSpace } from "./use-composer-menu-space";

/** Reported models stay visible; missing observations belong in the settings disclosure. */
export function ChatModelMenu({ agentId, goalId, ordinary, model = "", effort, readback, onModel, onEffort, onOpenDefaults }: {
  agentId: string; goalId?: string; ordinary: boolean; model?: string; effort?: string;
  readback?: ChatSessionSummary; onModel?: (value: string) => void | Promise<void>; onEffort?: (value: string) => void | Promise<void>;
  onOpenDefaults?: () => void;
}) {
  const { locale } = useWorkspaceI18n();
  const zh = locale === "zh-CN";
  const root = useRef<HTMLDetailsElement>(null);
  useComposerMenuSpace(root);
  const [open, setOpen] = useState(false);
  const [discovery, setDiscovery] = useState<AgentModelDiscovery | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<Error | null>(null);
  const retrySave = useRef<(() => void | Promise<void>) | null>(null);
  const saveAttempt = useRef(0);
  const editable = ordinary && readback?.model_settings_supported !== false;
  useEffect(() => {
    saveAttempt.current += 1; retrySave.current = null; setSaveError(null); setSaving(false);
  }, [agentId, goalId, readback?.session_id]);
  async function save(change: () => void | Promise<void>) {
    const attempt = ++saveAttempt.current;
    retrySave.current = change;
    setSaving(true); setSaveError(null);
    try { await change(); }
    catch (cause) { if (attempt === saveAttempt.current) setSaveError(cause instanceof Error ? cause : new Error(String(cause))); }
    finally { if (attempt === saveAttempt.current) setSaving(false); }
  }
  useEffect(() => {
    if (!open || !editable) return;
    const controller = new AbortController();
    setDiscovery(null); setLoading(true); setError(null);
    void discoverAgentModelOptions(agentId, controller.signal, goalId, model)
      .then(result => { if (!controller.signal.aborted) { setDiscovery(result); setLoading(false); } })
      .catch(cause => { if (!controller.signal.aborted) {
        setError(cause instanceof Error ? cause.message : (zh ? "配置读取失败" : "Settings unavailable")); setLoading(false);
      } });
    return () => controller.abort();
  }, [open, editable, agentId, goalId, model, retry, zh]);
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
  const nextModel = ordinary ? readback?.requested_agent_model_id : null;
  const nextEffort = ordinary ? readback?.requested_agent_reasoning_effort : null;
  const pending = Boolean(nextModel && (nextModel !== controls.observedModel || nextEffort && nextEffort !== controls.observedEffort));
  const summarySettings = pending ? nextEffort ?? "" : observedSettings;
  return <details className="personal-chat-model-menu" ref={root} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary aria-label={`${zh ? "模型与思考深度" : "Model and reasoning"} · ${nextModel ?? observed}${summarySettings ? ` · ${summarySettings}` : ""}${pending ? ` · ${zh ? "后续消息" : "Subsequent messages"}` : ""}`}>
      <span><span className="personal-model-current">{nextModel ?? observed}</span>{summarySettings ? <small>{summarySettings}</small> : null}{pending ? <Clock3 size={13} aria-label={zh ? "已保存，后续消息使用；当前回合不变" : "Saved for subsequent messages; current turn unchanged"}/> : null}</span>
      <ChevronDown aria-hidden size={16}/>
    </summary>
    <div className="personal-chat-model-panel">
      <strong>{zh ? "运行时回读" : "Runtime readback"}</strong>
      <p>{controls.observedModel
        ? [controls.observedModel, controls.observedEffort ? `${zh ? "推理" : "Reasoning"} ${controls.observedEffort}` : null,
          controls.observedTier ? `${zh ? "速度" : "Speed"} ${["priority", "fast"].includes(controls.observedTier) ? "Fast" : controls.observedTier}` : null].filter(Boolean).join(" · ")
        : (zh ? "运行时尚未回传实际模型" : "Runtime has not reported the actual model")}</p>
      {ordinary && !editable ? <p>{zh ? "请在此会话的原宿主或专属设置中调整配置。" : "Change this conversation in its owning host or dedicated settings."}</p> : null}
      {editable ? <>
        <div className="personal-model-next-heading"><strong>{zh ? "后续消息" : "Subsequent messages"}</strong>
        <ContextHelp label={zh ? "配置生效范围" : "When settings apply"}><p>{zh ? "保存到当前会话，后续消息采用；当前回合和原历史不变。尚未建立会话时，用于首次发送。" : "Saved to this conversation for subsequent messages; active work and history stay unchanged. Before a conversation exists, used for its first message."}</p></ContextHelp></div>
        <fieldset className="personal-model-options" disabled={saving}>
        {controls.modelOptions.length ? <WorkspaceSelect ariaLabel={zh ? "选择模型" : "Select model"}
          className="personal-agent-select personal-model-select" onChange={value => void save(() => onModel?.(value))}
          prefixLabel={zh ? "模型" : "Model"} value={model} options={[
            ...(!model ? [{ disabled: true, label: zh ? "选择模型" : "Choose model", value: "" }] : []),
            ...(model && !controls.modelOptions.some(option => option.value === model)
              ? [{ disabled: true, label: `${model} · ${zh ? "未列入可选模型" : "Not in catalog"}`, value: model }] : []),
            ...controls.modelOptions.map(option => ({ label: option.value === model ? option.value : option.label, value: option.value })),
          ]}/> : null}
        {controls.reasoningOptions.length && onEffort ? <WorkspaceSelect ariaLabel={zh ? "选择思考深度" : "Select reasoning effort"}
          className="personal-agent-select personal-model-select" onChange={value => void save(() => onEffort(value))}
          prefixLabel={zh ? "思考深度" : "Reasoning"} value={controls.effort} options={[
            ...(!readback ? [{ value: "", label: zh ? "沿用默认" : "Use default" }] : []),
            ...(controls.effort && !controls.reasoningOptions.some(option => option.value === controls.effort)
              ? [{ value: controls.effort, label: `${controls.effort} · ${zh ? "当前不可用" : "Unavailable"}`, disabled: true }] : []),
            ...controls.reasoningOptions,
          ]}/> : null}
        </fieldset>
        {saving ? <p role="status">{zh ? "正在保存…" : "Saving…"}</p> : null}
        {saveError ? <p role="alert">{zh
          ? (saveError instanceof ChatApiError && saveError.payload.error_code === "unsupported_configuration"
            ? "当前会话不支持所选模型或思考深度，已保存配置未更改。" : "配置保存未确认，请重试；当前配置以运行时回读为准。")
          : saveError.message}<ContextHelp label={zh ? "保存失败原因" : "Settings error details"}><p>{saveError.message}</p></ContextHelp>
          <button className="personal-action-button" type="button" disabled={saving} onClick={() => retrySave.current && void save(retrySave.current)}>{zh ? "重试保存" : "Retry save"}</button></p> : null}
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
