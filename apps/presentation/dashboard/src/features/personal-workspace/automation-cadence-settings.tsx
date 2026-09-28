import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

import {
  applyAutomationCadence, fetchAutomationCadence, previewAutomationCadence,
  type AutomationCadence, type AutomationCadenceChange,
} from "../../data/chat";
import { useWorkspaceI18n } from "./i18n";
import type { WorkspaceGoal } from "./personal-workspace-model";

type Scope = "goal" | "agent" | "automation";

export function AutomationCadenceSettings({ goal }: Readonly<{ goal: WorkspaceGoal }>) {
  const { t } = useWorkspaceI18n();
  const agentLanes = useMemo(() => goal.agentLanes ?? (goal.agentId ? [{ agentId: goal.agentId, label: goal.agentLabel ?? goal.agentId }] : []), [goal]);
  const [scope, setScope] = useState<Scope>("agent");
  const [agentId, setAgentId] = useState("");
  const [automationId, setAutomationId] = useState("");
  const [inspection, setInspection] = useState<AutomationCadence | null>(null);
  const [minutes, setMinutes] = useState("0");
  const [ownerReference, setOwnerReference] = useState("");
  const [approveReduction, setApproveReduction] = useState(false);
  const [preview, setPreview] = useState<AutomationCadence | null>(null);
  const [busy, setBusy] = useState<"load" | "preview" | "apply" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reloadSequence, setReloadSequence] = useState(0);
  const scopedAgent = scope === "goal" ? null : agentId.trim() || null;
  const scopedAutomation = scope === "automation" ? automationId.trim() || null : null;
  const scopeReady = scope === "goal" || Boolean(scopedAgent && agentLanes.some((lane) => lane.agentId === scopedAgent) && (scope !== "automation" || scopedAutomation));

  useEffect(() => {
    if (!scopeReady) { setInspection(null); setPreview(null); setBusy(null); return; }
    let active = true;
    setBusy("load");
    setError(null);
    setPreview(null);
    setInspection(null);
    fetchAutomationCadence(goal.goalId, scopedAgent, scopedAutomation)
      .then((result) => {
        if (!active) return;
        setInspection(result);
        const direct = result.sources.find((source) => source.agent_id === scopedAgent && source.automation_id === scopedAutomation);
        setMinutes(String(direct?.min_interval_minutes ?? result.min_interval_minutes));
        setOwnerReference("");
        setApproveReduction(false);
      })
      .catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : t("cadence.loadFailed")); })
      .finally(() => { if (active) setBusy(null); });
    return () => { active = false; };
  }, [goal.goalId, scope, scopedAgent, scopedAutomation, scopeReady, reloadSequence, t]);

  const directRule = inspection?.sources.find((source) => source.agent_id === scopedAgent && source.automation_id === scopedAutomation);
  const inheritedFloor = Math.max(0, ...(inspection?.sources.filter((source) => source !== directRule).map((source) => source.min_interval_minutes) ?? []));
  const proposedMinutes = Number(minutes);
  const validMinutes = /^\d+$/.test(minutes) && Number.isSafeInteger(proposedMinutes) && proposedMinutes <= 525600;
  const reduction = validMinutes && directRule !== undefined && proposedMinutes < directRule.min_interval_minutes;
  const canPreview = Boolean(inspection && scopeReady && validMinutes && ownerReference.trim() && (!reduction || approveReduction) && !busy);

  function invalidate() { setPreview(null); setNotice(null); setError(null); }

  function change(): AutomationCadenceChange | null {
    if (!inspection || !canPreview) return null;
    return {
      goal_id: goal.goalId, agent_id: scopedAgent, automation_id: scopedAutomation,
      min_interval_minutes: proposedMinutes, expected_revision: inspection.configuration_revision,
      owner_reference: ownerReference.trim(), approve_reduction: reduction && approveReduction,
    };
  }

  async function createPreview() {
    const payload = change();
    if (!payload) return;
    setBusy("preview"); setError(null);
    try { setPreview(await previewAutomationCadence(payload)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : t("cadence.previewFailed")); }
    finally { setBusy(null); }
  }

  async function apply() {
    const payload = change();
    if (!payload || !preview?.preview_revision) return;
    setBusy("apply"); setError(null);
    try {
      const result = await applyAutomationCadence(payload, preview.preview_revision);
      setInspection(result);
      setPreview(null);
      setOwnerReference("");
      setApproveReduction(false);
      setNotice(t(result.readback_verified ? "cadence.applied" : "cadence.readbackChanged"));
    } catch (reason) {
      setPreview(null);
      setError(reason instanceof Error ? reason.message : t("cadence.applyFailed"));
    } finally { setBusy(null); }
  }

  return <section className="personal-cadence-settings" aria-label={t("cadence.title")}>
    <div className="personal-cadence-intro">
      <p>{t("cadence.description")}</p>
    </div>
    <fieldset className="personal-cadence-scopes">
      <legend>{t("cadence.scope")}</legend>
      {(["goal", "agent", "automation"] as const).map((option) =>
        <label key={option}><input checked={scope === option} disabled={Boolean(busy)} onChange={() => { setScope(option); invalidate(); }} type="radio" name="cadence-scope" value={option} />{t(`cadence.scope.${option}`)}</label>)}
    </fieldset>
    {scope !== "goal" ? <div className="personal-cadence-fields">
      <label>{t("cadence.agentId")}<select aria-label={t("cadence.agentId")} aria-describedby="cadence-agent-hint" disabled={Boolean(busy) || agentLanes.length === 0} onChange={(event) => { setAgentId(event.target.value); invalidate(); }} value={agentId}>
        <option value="">{t(agentLanes.length ? "cadence.chooseAgent" : "cadence.noAgents")}</option>
        {agentLanes.map((lane) => <option key={lane.agentId} value={lane.agentId}>{lane.label === lane.agentId ? lane.agentId : `${lane.label} · ${lane.agentId}`}</option>)}
      </select><small id="cadence-agent-hint">{t("cadence.agentHint")}</small></label>
      {scope === "automation" ? <label>{t("cadence.automationId")}<input disabled={Boolean(busy)} onChange={(event) => { setAutomationId(event.target.value); invalidate(); }} value={automationId} /></label> : null}
    </div> : null}
    {busy === "load" ? <p aria-live="polite">{t("common.loading")}</p> : null}
    {inspection ? <>
      <div className="personal-cadence-readback">
        <small>{t("cadence.effective")}</small><strong>{inspection.min_interval_minutes} <span>{t("cadence.minutes")}</span></strong>
        <p>{directRule
          ? t("cadence.directSource", { minutes: directRule.min_interval_minutes, inherited: inheritedFloor })
          : scope === "goal" ? t("cadence.unconfigured") : t("cadence.inheritedSource", { minutes: inheritedFloor })}</p>
      </div>
      <p className="personal-cadence-boundary"><AlertTriangle aria-hidden size={16} />{t("cadence.boundary")}</p>
      <div className="personal-cadence-fields">
        <label>{t("cadence.minimum")}<input disabled={Boolean(busy)} min={0} max={525600} onChange={(event) => { setMinutes(event.target.value); invalidate(); }} type="number" value={minutes} /><small>{t("cadence.zeroHint")}</small></label>
        <label>{t("cadence.ownerReference")}<input disabled={Boolean(busy)} maxLength={256} onChange={(event) => { setOwnerReference(event.target.value); invalidate(); }} value={ownerReference} /><small>{t("cadence.referenceHint")}</small></label>
      </div>
      {reduction ? <label className="personal-cadence-reduction"><input checked={approveReduction} disabled={Boolean(busy)} onChange={(event) => { setApproveReduction(event.target.checked); invalidate(); }} type="checkbox" />{t("cadence.reduction")}</label> : null}
      {preview ? <div className="personal-cadence-preview"><strong>{t("cadence.preview")}</strong><small>{goal.title} · {scopedAgent ?? t("cadence.scope.goal")}{scopedAutomation ? ` · ${scopedAutomation}` : ""}</small><span>{t("cadence.previewValue", { minutes: preview.min_interval_minutes })}</span><small>{t("cadence.previewLocked")}</small></div> : null}
      <div className="personal-cadence-actions"><button disabled={!canPreview} onClick={() => void createPreview()} type="button">{t("cadence.preview")}</button><button className="is-primary" disabled={!preview || Boolean(busy)} onClick={() => void apply()} type="button">{t("cadence.apply")}</button></div>
    </> : null}
    {error ? <p className="personal-machine-error" role="alert">{error} <button onClick={() => { invalidate(); setReloadSequence((value) => value + 1); }} type="button"><RefreshCw aria-hidden size={14} />{t("cadence.retry")}</button></p> : null}
    {notice ? <p aria-live="polite" className="personal-cadence-notice">{notice}</p> : null}
  </section>;
}
