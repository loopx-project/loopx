import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

import {
  applyAutomationCadence, fetchAutomationCadence, previewAutomationCadence,
  type AutomationCadence, type AutomationCadenceChange,
} from "../../data/chat";
import { useWorkspaceI18n } from "./i18n";
import type { WorkspaceGoal } from "./personal-workspace-model";

export function AutomationCadenceSettings({ goal }: Readonly<{ goal: WorkspaceGoal }>) {
  const { t } = useWorkspaceI18n();
  const agentLanes = useMemo(() => goal.agentLanes ?? (goal.agentId ? [{ agentId: goal.agentId, label: goal.agentLabel ?? goal.agentId }] : []), [goal]);
  const [scope, setScope] = useState<"goal" | "agent">("agent");
  const [agentId, setAgentId] = useState("");
  const [automationOnly, setAutomationOnly] = useState(false);
  const [automationId, setAutomationId] = useState("");
  const [inspection, setInspection] = useState<AutomationCadence | null>(null);
  const [minutes, setMinutes] = useState("0");
  const [ownerReference, setOwnerReference] = useState("");
  const [busy, setBusy] = useState<"load" | "save" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reloadSequence, setReloadSequence] = useState(0);
  const [requiresRefresh, setRequiresRefresh] = useState(false);
  const requestSequence = useRef(0);
  const saving = useRef(false);
  const scopedAgent = scope === "goal" ? null : agentId.trim() || null;
  const scopedAutomation = scope === "agent" && automationOnly ? automationId.trim() || null : null;
  const scopeReady = scope === "goal" || Boolean(scopedAgent && agentLanes.some((lane) => lane.agentId === scopedAgent) && (!automationOnly || scopedAutomation));

  useEffect(() => {
    const sequence = ++requestSequence.current;
    saving.current = false;
    setInspection(null);
    setNotice(null);
    setError(null);
    setRequiresRefresh(false);
    if (!scopeReady) { setBusy(null); return; }
    setBusy("load");
    fetchAutomationCadence(goal.goalId, scopedAgent, scopedAutomation)
      .then((result) => {
        if (sequence !== requestSequence.current) return;
        setInspection(result);
        const direct = result.sources.find((source) => source.agent_id === scopedAgent && source.automation_id === scopedAutomation);
        setMinutes(String(direct?.min_interval_minutes ?? 0));
        setOwnerReference("");
      })
      .catch((reason: unknown) => { if (sequence === requestSequence.current) setError(reason instanceof Error ? reason.message : t("cadence.loadFailed")); })
      .finally(() => { if (sequence === requestSequence.current) setBusy(null); });
    return () => { requestSequence.current += 1; };
  }, [goal.goalId, scopedAgent, scopedAutomation, scopeReady, reloadSequence, t]);

  const directRule = inspection?.sources.find((source) => source.agent_id === scopedAgent && source.automation_id === scopedAutomation);
  const inheritedFloor = Math.max(0, ...(inspection?.sources.filter((source) => source !== directRule).map((source) => source.min_interval_minutes) ?? []));
  const proposedMinutes = Number(minutes);
  const validMinutes = /^\d+$/.test(minutes) && Number.isSafeInteger(proposedMinutes) && proposedMinutes <= 525600;
  const reduction = validMinutes && directRule !== undefined && proposedMinutes < directRule.min_interval_minutes;
  const changed = proposedMinutes !== (directRule?.min_interval_minutes ?? 0);
  const canSave = Boolean(inspection && scopeReady && validMinutes && changed && !requiresRefresh && !busy);

  function edit() { setNotice(null); }

  async function save() {
    if (!inspection || !canSave || saving.current) return;
    saving.current = true;
    const sequence = requestSequence.current;
    const reference = `App settings Save: ${directRule?.min_interval_minutes ?? 0} -> ${proposedMinutes} minutes (${scopedAutomation ? "automation" : scope})`;
    const payload: AutomationCadenceChange = {
      goal_id: goal.goalId, agent_id: scopedAgent, automation_id: scopedAutomation,
      min_interval_minutes: proposedMinutes, expected_revision: inspection.configuration_revision,
      owner_reference: ownerReference.trim() ? `${reference}. ${ownerReference.trim()}` : reference,
      // The visible before/after value and explicit Save are the reduction intent.
      approve_reduction: reduction,
    };
    setBusy("save"); setError(null);
    try {
      const preview = await previewAutomationCadence(payload);
      if (sequence !== requestSequence.current) return;
      if (!preview.preview_revision) throw new Error(t("cadence.saveFailed"));
      const result = await applyAutomationCadence(payload, preview.preview_revision);
      if (sequence !== requestSequence.current) return;
      setInspection(result);
      const direct = result.sources.find((source) => source.agent_id === scopedAgent && source.automation_id === scopedAutomation);
      setMinutes(String(direct?.min_interval_minutes ?? 0));
      setOwnerReference("");
      setRequiresRefresh(!result.readback_verified);
      setNotice(t(result.readback_verified ? "cadence.applied" : "cadence.readbackChanged"));
    } catch (reason) {
      if (sequence !== requestSequence.current) return;
      setRequiresRefresh(true);
      setError(reason instanceof Error ? reason.message : t("cadence.saveFailed"));
    } finally {
      if (sequence === requestSequence.current) { saving.current = false; setBusy(null); }
    }
  }

  return <section className="personal-cadence-settings" aria-label={t("cadence.title")}>
    <form className="personal-cadence-form" onSubmit={(event) => { event.preventDefault(); void save(); }}>
    <div className="personal-cadence-fields personal-cadence-target">
      <label>{t("cadence.target")}<select aria-label={t("cadence.target")} disabled={Boolean(busy)} onChange={(event) => {
        const lane = agentLanes.find((item) => `agent:${item.agentId}` === event.target.value);
        setScope(event.target.value === "goal" ? "goal" : "agent");
        setAgentId(lane?.agentId ?? ""); setAutomationOnly(false); setAutomationId(""); edit();
      }} value={scope === "goal" ? "goal" : agentId ? `agent:${agentId}` : ""}>
        <option value="">{t("cadence.chooseTarget")}</option>
        <option value="goal">{t("cadence.scope.goal")} · {goal.title}</option>
        <optgroup label={t("cadence.scope.agent")}>
          {agentLanes.map((lane) => <option key={lane.agentId} value={`agent:${lane.agentId}`}>{lane.label === lane.agentId ? lane.agentId : `${lane.label} · ${lane.agentId}`}</option>)}
        </optgroup>
      </select>{agentLanes.length === 0 ? <small>{t("cadence.noAgents")}</small> : null}</label>
    </div>
    {busy === "load" ? <p aria-live="polite">{t("common.loading")}</p> : null}
    {inspection ? <>
      <div className="personal-cadence-readback">
        <small>{t("cadence.effective")}</small><strong>{inspection.min_interval_minutes} <span>{t("cadence.minutes")}</span></strong>
        <p>{directRule
          ? t("cadence.directSource", { minutes: directRule.min_interval_minutes, inherited: inheritedFloor })
          : scope === "goal" ? t("cadence.unconfigured") : t("cadence.inheritedSource", { minutes: inheritedFloor })}</p>
      </div>
      <div className="personal-cadence-fields personal-cadence-target">
        <label>{t("cadence.minimum")}<input aria-label={t("cadence.minimum")} disabled={Boolean(busy)} min={0} max={525600} onChange={(event) => { setMinutes(event.target.value); edit(); }} type="number" value={minutes} aria-invalid={!validMinutes} /><small>{t("cadence.zeroHint")}</small></label>
      </div>
      {!validMinutes ? <p className="personal-machine-error" role="alert">{t("cadence.invalidMinutes")}</p> : null}
      {validMinutes && changed ? <p className="personal-cadence-change">{t("cadence.change", { before: inspection.min_interval_minutes, after: Math.max(proposedMinutes, inheritedFloor) })}{proposedMinutes < inheritedFloor ? <small>{t("cadence.inheritedLimit", { minutes: inheritedFloor })}</small> : null}</p> : null}
    </> : null}
    {scope === "agent" && scopedAgent ? <details className="personal-cadence-details"><summary>{t("cadence.automationSettings")}</summary><label className="personal-cadence-automation"><input checked={automationOnly} disabled={Boolean(busy)} onChange={(event) => { setAutomationOnly(event.target.checked); edit(); }} type="checkbox" />{t("cadence.automationOnly")}</label>{automationOnly ? <div className="personal-cadence-fields"><label>{t("cadence.automationId")}<input aria-label={t("cadence.automationId")} disabled={Boolean(busy)} maxLength={256} onChange={(event) => { setAutomationId(event.target.value); edit(); }} value={automationId} /></label></div> : null}</details> : null}
    {inspection ? <>
      <details className="personal-cadence-details"><summary>{t("cadence.ownerReference")}</summary><div className="personal-cadence-fields"><label>{t("cadence.note")}<input aria-label={t("cadence.note")} disabled={Boolean(busy)} maxLength={160} onChange={(event) => { setOwnerReference(event.target.value); edit(); }} value={ownerReference} /><small>{t("cadence.referenceHint")}</small></label></div></details>
      <p className="personal-cadence-boundary">{t("cadence.boundary")}</p>
      <div className="personal-cadence-actions"><button className="is-primary" disabled={!canSave} type="submit">{t(busy === "save" ? "cadence.saving" : reduction ? "cadence.saveReduction" : "cadence.save")}</button></div>
    </> : null}
    {error ? <p className="personal-machine-error" role="alert">{error}</p> : null}
    {notice ? <p aria-live="polite" className={requiresRefresh ? "personal-cadence-boundary" : "personal-cadence-notice"}>{requiresRefresh ? <AlertTriangle aria-hidden size={16} /> : null}{notice}</p> : null}
    {error || requiresRefresh ? <button disabled={Boolean(busy)} onClick={() => setReloadSequence((value) => value + 1)} type="button"><RefreshCw aria-hidden size={14} />{t("cadence.retry")}</button> : null}
    </form>
  </section>;
}
