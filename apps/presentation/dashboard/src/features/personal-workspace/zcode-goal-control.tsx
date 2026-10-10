import { useCallback, useEffect, useRef, useState } from "react";
import { fetchZCodeGoal, updateZCodeGoal } from "../../data/zcode-goal";
import type { ZCodeGoalAction, ZCodeGoalReadback, ZCodeModelSelection } from "../../../../../../loopx/zcode_goal_mode/contract.js";
import { useWorkspaceI18n, type WorkspaceTranslate } from "./i18n";
import type { WorkspaceGoal } from "./personal-workspace-model";
import "./zcode-goal-control.css";

function zcodeReason(reason: string | undefined, t: WorkspaceTranslate) {
  return reason === "zcode_native_execution_failed" ? t("zcode.executionFailed") : reason;
}

export function ZCodeGoalControl({goal, readOnly}: {goal: WorkspaceGoal; readOnly: boolean}) {
  const {t} = useWorkspaceI18n();
  const [opened, setOpened] = useState(false);
  const eligibleAgentIds = goal.zcodeGoalEligibleAgentIds ?? [];
  const [agentId, setAgentId] = useState(eligibleAgentIds[0] ?? "");
  const selectedAgent = eligibleAgentIds.includes(agentId) ? agentId : eligibleAgentIds[0] ?? "";
  if (eligibleAgentIds.length === 0) return null;
  return <details className="personal-detail-card personal-zcode-goal" onToggle={event => setOpened(event.currentTarget.open)}>
    <summary>{t("zcode.title")}</summary>
    {opened ? <>
      <p>{t("zcode.scope")}</p>
      {readOnly ? <p role="status">{t("zcode.remote")}</p> : <>
        <label>{t("zcode.agent")}<select value={selectedAgent} onChange={event => setAgentId(event.target.value)}>
          {eligibleAgentIds.map(id => <option value={id} key={id}>{id}</option>)}
        </select></label>
        <ZCodeAgentControl key={`${goal.goalId}:${selectedAgent}`} goalId={goal.goalId} agentId={selectedAgent} />
      </>}
    </> : null}
  </details>;
}

function ZCodeAgentControl({goalId, agentId}: {goalId: string; agentId: string}) {
  const {t} = useWorkspaceI18n();
  const [snapshot, setSnapshot] = useState<ZCodeGoalReadback | null>(null);
  const [cliPath, setCliPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [checkedAt, setCheckedAt] = useState("");
  const [modelChoice, setModelChoice] = useState("");
  const [reasoningLevel, setReasoningLevel] = useState("");
  const mounted = useRef(false);
  const request = useRef<AbortController | null>(null);
  const pathEdited = useRef(false);
  const pending = useRef(false);

  const perform = useCallback(async (action: ZCodeGoalAction = "status", options?: {cliPath?: string; modelSelection?: ZCodeModelSelection}) => {
    if (pending.current) return;
    if (action !== "status" && !snapshot) {setError(t("zcode.readAgain")); return;}
    pending.current = true;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setError("");
    try {
      const result = action === "status"
        ? await fetchZCodeGoal(goalId, agentId, controller.signal)
        : await updateZCodeGoal(goalId, agentId, action,
          {goal_ref: snapshot!.goal_ref, goal_creation_operation_id: snapshot!.goal_creation_operation_id}, options, controller.signal);
      if (!mounted.current || controller.signal.aborted) return;
      setSnapshot(result);
      if (action === "bind" && result.ok) {setModelChoice(""); setReasoningLevel("");}
      setCheckedAt(new Date().toLocaleTimeString());
      if ((action === "bind" && result.ok) || !pathEdited.current) {
        setCliPath(result.binding?.cli_path ?? "");
        pathEdited.current = false;
      }
      if (!result.ok) setError(zcodeReason(result.reason, t) || t("zcode.failed"));
    } catch (failure) {
      if (!mounted.current || controller.signal.aborted) return;
      // A failed operation can have an unknown outcome; only a fresh readback enables another action.
      setSnapshot(null);
      setCheckedAt("");
      setError(`${failure instanceof Error ? failure.message : t("zcode.failed")} ${t("zcode.readAgain")}`);
    } finally {
      pending.current = false;
      if (mounted.current && !controller.signal.aborted) setBusy(false);
    }
  }, [goalId, agentId, snapshot, t]);

  useEffect(() => {
    mounted.current = true;
    void perform();
    return () => { mounted.current = false; request.current?.abort(); };
  // Goal/Agent identity is also the parent key; locale changes do not restart an in-flight request.
  }, [goalId, agentId]);

  const allowed = (action: ZCodeGoalAction) => !busy && Boolean(snapshot?.actions.includes(action));
  const pathChanged = pathEdited.current && cliPath.trim() !== (snapshot?.binding?.cli_path ?? "");
  const native = snapshot?.native;
  const reason = zcodeReason(snapshot?.reason, t);
  const models = native?.available_models ?? [];
  const chosenModel = models.find(model => JSON.stringify(model.selection) === modelChoice);
  const selectedModel = native?.selected_model;
  const currentModel = models.find(model => model.selection.providerId === selectedModel?.providerId && model.selection.modelId === selectedModel.modelId);
  const selectModel = () => {
    if (!chosenModel || chosenModel.disabled || !allowed("select_model")) return;
    const selection: ZCodeModelSelection = {
      ...chosenModel.selection,
      ...(chosenModel.reasoning_levels.length ? {options: {reasoningLevel}} : {}),
    };
    void perform("select_model", {modelSelection: selection});
  };
  const controls = <div className="personal-zcode-actions">
      {(["start", "pause", "resume", "stop"] as const).map(action => <button
        className={action === "start" ? "personal-primary-action" : "personal-secondary-action"}
        disabled={!allowed(action) || (pathChanged && (action === "start" || action === "resume"))} key={action} onClick={() => void perform(action)} type="button">{t(`zcode.${action}`)}</button>)}
    </div>;
  return <div className="personal-zcode-agent" aria-busy={busy}>
    <label>{t("zcode.cli")}<input value={cliPath} disabled={busy} placeholder={t("zcode.pathPlaceholder")}
      onChange={event => {pathEdited.current = true; setCliPath(event.target.value);}} /></label>
    <p className="personal-zcode-help">{t("zcode.pathHelp")}</p>
    <div className="personal-zcode-actions">
      <button className="personal-secondary-action" disabled={!allowed("bind")} onClick={() => void perform("bind", {cliPath})} type="button">{t("zcode.bind")}</button>
      <button className="personal-secondary-action" disabled={busy} onClick={() => void perform()} type="button">{t("zcode.refresh")}</button>
    </div>
    {busy ? <p role="status">{t("zcode.pending")}</p> : null}
    {error ? <p className="personal-zcode-error" role="alert">{error}</p> : null}
    {pathChanged ? <p role="status">{t("zcode.pathChanged")}</p> : null}
    {snapshot ? <div aria-live="polite">
      {!snapshot.available ? <p role="status">{t("zcode.unavailable")}{reason && !error ? ` · ${reason}` : ""}</p> : reason && !error ? <p role="status">{reason}</p> : null}
      <dl>
        <div><dt>{t("zcode.binding")}</dt><dd>{snapshot.binding ? t(snapshot.binding.connected ? "zcode.connected" : "zcode.disconnected") : t("zcode.unbound")}</dd></div>
        <div><dt>{t("zcode.native")}</dt><dd>{native?.status ? t(`zcode.state.${native.status}`) : t("zcode.unknown")}</dd></div>
        <div><dt>{t("zcode.execution")}</dt><dd>{native ? t(native.running ? "zcode.running" : "zcode.idle") : t("zcode.unknown")}</dd></div>
        {native ? <div><dt>{t("zcode.currentModel")}</dt><dd>{selectedModel ? currentModel?.label || selectedModel.modelId : selectedModel === null ? t("zcode.modelNotSelected") : t("zcode.unknown")}</dd></div> : null}
        <div><dt>{t("zcode.quota")}</dt><dd>{snapshot.quota ? t(snapshot.quota.should_run ? "zcode.quotaAllowed" : "zcode.quotaHeld") : t("zcode.unknown")}</dd></div>
      </dl>
      {snapshot.binding ? <details className="personal-zcode-model" open={selectedModel === null}>
        <summary>{t("zcode.model")}</summary>
        {models.length ? <>
          <label>{t("zcode.model")}<select value={chosenModel ? modelChoice : ""} disabled={!allowed("select_model") || pathChanged} onChange={event => {
            setModelChoice(event.target.value);
            const model = models.find(item => JSON.stringify(item.selection) === event.target.value);
            const previousLevel = model?.selection.providerId === selectedModel?.providerId && model?.selection.modelId === selectedModel?.modelId
              ? selectedModel?.options?.reasoningLevel : undefined;
            const level = previousLevel || model?.default_reasoning_level || "";
            setReasoningLevel(model?.reasoning_levels.includes(level) ? level : "");
          }}>
            <option value="">{t("zcode.chooseModel")}</option>
            {models.map(model => <option key={JSON.stringify(model.selection)} value={JSON.stringify(model.selection)} disabled={model.disabled}>
              {model.provider_label ? `${model.provider_label} · ` : ""}{model.label}{model.disabled ? ` · ${t("zcode.modelDisabled")}` : ""}
            </option>)}
          </select></label>
          {chosenModel?.reasoning_levels.length ? <label>{t("zcode.reasoning")}<select value={reasoningLevel} disabled={busy} onChange={event => setReasoningLevel(event.target.value)}>
            <option value="">{t("zcode.chooseReasoning")}</option>
            {chosenModel.reasoning_levels.map(level => <option value={level} key={level}>{level}</option>)}
          </select></label> : null}
          <button className="personal-secondary-action" type="button"
            disabled={!allowed("select_model") || pathChanged || !chosenModel || chosenModel.disabled
              || Boolean(chosenModel.reasoning_levels.length && !chosenModel.reasoning_levels.includes(reasoningLevel))}
            onClick={selectModel}>{t("zcode.useModel")}</button>
        </> : <p role="status">{t("zcode.modelsUnavailable")}</p>}
      </details> : null}
      {snapshot.quota && !snapshot.quota.should_run && snapshot.quota.reason ? <p>{snapshot.quota.reason}</p> : null}
    {controls}
      <p className="personal-zcode-help">{t("zcode.quotaBoundary")}</p>
      <p className="personal-zcode-help">{t("zcode.usageUnknown")}</p>
      {checkedAt ? <p className="personal-zcode-help">{t("zcode.checked", {time: checkedAt})}</p> : null}
      <details className="personal-zcode-diagnostics"><summary>{t("zcode.diagnostics")}</summary>
        <dl>
          <div><dt>{t("zcode.cli")}</dt><dd>{snapshot.binding?.cli_path || "PATH"}</dd></div>
          {snapshot.identity_scope ? <div><dt>{t("zcode.identity")}</dt><dd>{t(`zcode.identity.${snapshot.identity_scope}`)}</dd></div> : null}
          {snapshot.identity_scope === "legacy_goal_alias" ? <div><dt>{t("zcode.identityBoundary")}</dt><dd>{t("zcode.legacyBoundary")}</dd></div> : null}
          <div><dt>Protocol</dt><dd>{snapshot.binding?.protocol || t("zcode.unknown")}</dd></div>
          <div><dt>Session</dt><dd>{native?.session_id || t("zcode.unknown")}</dd></div>
          {native?.session_status ? <div><dt>{t("zcode.sessionState")}</dt><dd>{native.session_status}</dd></div> : null}
          {native?.raw_status ? <div><dt>{t("zcode.rawState")}</dt><dd>{native.raw_status}</dd></div> : null}
          <div><dt>Target</dt><dd>{native?.target_id || t("zcode.unknown")}</dd></div>
        </dl>
      </details>
    </div> : null}
    {!snapshot ? controls : null}
  </div>;
}
