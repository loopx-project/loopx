import {useEffect, useRef, useState} from "react";
import {Check, FlaskConical, LoaderCircle} from "lucide-react";
import {applyGoalConfiguration, fetchGoalConfiguration, previewGoalConfiguration, type GoalConfigurationInspection} from "../../data/chat";
import {projectEditableCapabilityConfiguration} from "../../data/capability-configuration";
import {canEditCapability} from "./capability-workbench";
import {ContextHelp} from "./context-help";
import "./goal-research-mode.css";

const capabilityId = "explore_harness";
function researchCapability(inspection: GoalConfigurationInspection | null) {
  return inspection?.capability_catalog.capabilities.find(row => row.capability_id === capabilityId);
}
function researchMode(inspection: GoalConfigurationInspection | null) {
  const capability = researchCapability(inspection);
  return capability?.effective_configuration?.configuration?.mode ?? capability?.current?.mode ?? capability?.default?.mode;
}

/** The existing Goal configuration owner remains the only writer. This control
 * changes evidence collection; it never dispatches analysis or starts a Goal. */
export function GoalResearchMode({goalId, zh, refreshRevision, onSettings}: {
  goalId: string; zh: boolean; refreshRevision: number; onSettings: () => void;
}) {
  const [inspection, setInspection] = useState<GoalConfigurationInspection | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const mounted = useRef(true);
  const changing = useRef(false);
  const copy = zh ? {
    enable: "开启研究模式", enabled: "研究模式已开启", disable: "关闭研究模式", loading: "读取研究模式…", saving: "保存中…",
    unavailable: "研究模式不可用", failed: "未能确认研究模式，请刷新后重试。", settings: "研究设置", retry: "刷新",
    mismatch: "配置来源与当前 Goal 不一致。", stale: "配置已变化，请刷新后重试。",
    partial: "配置已写入，但同步或回读未完成。请刷新确认，勿重复提交。",
    help: "研究模式说明", description: "开启后，后续工作可以记录研究问题与证据；不会立即运行 Agent 或深度分析。关闭会保留研究历史，已在运行的工作仍需单独停止。",
  } : {
    enable: "Enable research mode", enabled: "Research mode on", disable: "Turn off research mode", loading: "Reading research mode…", saving: "Saving…",
    unavailable: "Research mode unavailable", failed: "Research mode could not be confirmed. Refresh before retrying.", settings: "Research settings", retry: "Refresh",
    mismatch: "The configuration source does not match this Goal.", stale: "Configuration changed. Refresh before retrying.",
    partial: "Configuration was written, but sync or readback is incomplete. Refresh to confirm before resubmitting.",
    help: "About research mode", description: "Subsequent work can record research questions and evidence. Enabling does not run an Agent or deep analysis immediately. Turning it off preserves history; work already running must be stopped separately.",
  };
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    let current = true;
    setLoading(true); setInspection(null); setError(null);
    void fetchGoalConfiguration(goalId).then(result => {
      if (result.goal_id !== goalId) throw new Error(copy.mismatch);
      if (current) setInspection(result);
    }).catch(() => { if (current) setError(copy.failed); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [goalId, refreshRevision, retry]);
  const mode = researchMode(inspection);
  const enabled = mode === "evidence" || mode === "planning";
  const known = enabled || mode === "off";
  const capability = researchCapability(inspection);
  const writable = capability && known && canEditCapability(capability, "goal")
    && capability.configuration_editor.fields.some(field => field.key === "mode" && field.options?.includes(enabled ? "off" : "evidence"));

  async function change() {
    if (!writable || changing.current || loading || error) return;
    changing.current = true; setBusy(true); setError(null);
    try {
      // Fresh read prevents a stale toggle from changing an unseen mode or
      // overwriting another editor's profile/composition selection.
      const current = await fetchGoalConfiguration(goalId);
      if (current.goal_id !== goalId) throw new Error(copy.mismatch);
      if (!mounted.current) return;
      if (current.revision !== inspection?.revision) { setInspection(current); throw new Error(copy.stale); }
      const selected = researchCapability(current)!;
      if (!canEditCapability(selected, "goal")) throw new Error(copy.unavailable);
      const configuration = {...projectEditableCapabilityConfiguration(selected.configuration_editor,
        selected.current ?? selected.effective_configuration?.configuration, selected.default), mode: enabled ? "off" : "evidence"};
      const preview = await previewGoalConfiguration(goalId, capabilityId, configuration);
      if (!mounted.current) return;
      if (preview.goal_id !== goalId || preview.capability_id !== capabilityId) throw new Error(copy.mismatch);
      if (preview.base_revision !== current.revision) throw new Error(copy.stale);
      const applied = await applyGoalConfiguration(goalId, capabilityId, configuration, preview.plan_revision);
      if (applied.goal_id !== goalId || applied.capability_id !== capabilityId) throw new Error(copy.mismatch);
      if (applied.status === "partial_write") throw new Error(copy.partial);
      const readback = await fetchGoalConfiguration(goalId);
      if (readback.goal_id !== goalId || researchMode(readback) !== configuration.mode) throw new Error(copy.failed);
      if (mounted.current) setInspection(readback);
    } catch (reason) {
      if (mounted.current) setError(reason instanceof Error ? reason.message : copy.failed);
    } finally {
      changing.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  return <div className="goal-research-mode" data-testid="goal-research-mode">
    <button type="button" className="goal-research-mode-toggle" aria-pressed={known ? enabled : undefined}
      aria-label={known ? enabled ? copy.disable : copy.enable : copy.unavailable}
      disabled={loading || busy || !writable || Boolean(error)} onClick={() => void change()}>
      {busy || loading ? <LoaderCircle className="is-spinning" size={16} aria-hidden/> : enabled ? <Check size={16} aria-hidden/> : <FlaskConical size={16} aria-hidden/>}
      {busy ? copy.saving : loading ? copy.loading : known ? enabled ? copy.enabled : copy.enable : copy.unavailable}
    </button>
    <ContextHelp label={copy.help}>{copy.description}</ContextHelp>
    {!loading && (!writable || error) ? <button type="button" onClick={onSettings}>{copy.settings}</button> : null}
    {error ? <div className="goal-research-mode-error" role="alert"><span>{error}</span><button type="button" disabled={busy} onClick={() => setRetry(value => value + 1)}>{copy.retry}</button></div> : null}
  </div>;
}
