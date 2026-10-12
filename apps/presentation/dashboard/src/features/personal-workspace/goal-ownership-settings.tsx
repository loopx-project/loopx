import { useEffect, useRef, useState } from "react";
import { fetchGoalOwnership, updateGoalOwnership, ownershipPreviewSchema,
  type GoalOwnership, type OwnershipPreview, type ExecutionOwnershipMode } from "../../data/chat";
import { useWorkspaceI18n } from "./i18n";

/** The saved browser carrier is recovery context, never write authority: the
 * server loads its immutable plan and the canonical owner checks the digest. */
export function GoalOwnershipSettings({goalId, onChanged, refreshKey = 0}: {
  goalId: string; onChanged: () => void; refreshKey?: number;
}) {
  const {t} = useWorkspaceI18n();
  const key = `loopx-ownership-preview:${goalId}`;
  const [current, setCurrent] = useState<GoalOwnership | null>(null);
  const [mode, setMode] = useState<ExecutionOwnershipMode>("soft_claim");
  const [preview, setPreview] = useState<OwnershipPreview | null>(null);
  const [result, setResult] = useState<OwnershipPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const inFlight = useRef(false);
  const generation = useRef(0);
  useEffect(() => {
    const token = ++generation.current;
    setCurrent(null); setError(null); setBusy(true);
    try {
      const stored = sessionStorage.getItem(key);
      const restored = stored ? ownershipPreviewSchema.parse(JSON.parse(stored)) : null;
      if (restored?.goal_id === goalId && restored.ok && restored.plan_sha256 && restored.handoff_mode) {
        setPreview(restored); setMode(restored.handoff_mode);
      }
    } catch {
      // Storage may be unavailable. Reading policy remains usable; Apply is
      // enabled only after a new preview has been persisted successfully.
      setPreview(null);
    }
    void fetchGoalOwnership(goalId).then(value => {
      if (generation.current === token) setCurrent(value);
    }).catch(() => { if (generation.current === token) setError(t("ownership.loadFailed")); })
      .finally(() => { if (generation.current === token) setBusy(false); });
    return () => { generation.current++; };
  }, [goalId, key, reload, refreshKey, t]);

  async function submit(apply: boolean) {
    if (inFlight.current || busy) return;
    if (apply && (!preview?.ok || !preview.plan_sha256)) return;
    inFlight.current = true; setBusy(true); setError(null);
    const token = generation.current;
    try {
      const next = await updateGoalOwnership(apply
        ? {goal_id: goalId, preview_id: preview!.preview_id, plan_sha256: preview!.plan_sha256!}
        : {goal_id: goalId, mode});
      if (token !== generation.current) return;
      setResult(next);
      if (!apply && next.ok) {
        // Persist before enabling Apply; reload/lost replies keep the same intent.
        sessionStorage.setItem(key, JSON.stringify(next));
        setPreview(next);
      }
      if (apply && next.ok) {
        setCurrent(next.current ?? null);
        onChanged();
      }
      if (!next.ok) setError(t("ownership.conflict"));
    } catch {
      if (token === generation.current) setError(t(apply ? "ownership.ambiguous" : "ownership.previewFailed"));
    } finally {
      inFlight.current = false;
      if (token === generation.current) setBusy(false);
    }
  }
  function discard() {
    try { sessionStorage.removeItem(key); }
    catch { setError(t("ownership.previewFailed")); return; }
    setPreview(null); setResult(null); setError(null);
  }
  return <section className="personal-cadence-settings" aria-label={t("ownership.title")}>
    <div className="personal-cadence-form">
      {current ? <div className="personal-cadence-readback"><small>{t("ownership.current")}</small>
        <strong>{current.current_mode ? t(`ownership.${current.current_mode}`) : t("ownership.unpromoted")}</strong></div> : null}
      {current && !current.canonical ? <p>{t("ownership.promoteFirst")}</p> : null}
      {current?.canonical || preview ? <>
        <div className="personal-cadence-fields"><label>{t("ownership.target")}<select aria-label={t("ownership.target")} disabled={busy || Boolean(preview)} value={mode} onChange={e => { setMode(e.target.value as ExecutionOwnershipMode); setResult(null); setError(null); }}>
          <option value="soft_claim">{t("ownership.soft_claim")}</option>
          <option value="hard_lease">{t("ownership.hard_lease")}</option>
        </select><small>{t(mode === "soft_claim" ? "ownership.softHelp" : "ownership.hardHelp")}</small></label></div>
        <p className="personal-cadence-boundary">{t("ownership.boundary")}</p>
        {preview ? <p>{t("ownership.summary", {claims: preview.preserved_claim_count ?? 0, leases: preview.retained_lease_count})}</p> : null}
        <div className="personal-cadence-actions">
          {preview ? <><button className="is-primary" disabled={busy || Boolean(result?.ok && result.current !== undefined)} onClick={() => void submit(true)} type="button">{t("ownership.apply")}</button>
            <button disabled={busy} onClick={discard} type="button">{t("ownership.fresh")}</button></>
            : <button className="is-primary" disabled={busy} onClick={() => void submit(false)} type="button">{t("ownership.preview")}</button>}
        </div>
      </> : null}
      {result?.ok && result.current !== undefined ? <p role="status">{t(result.backup_verified ? "ownership.confirmed" : "ownership.unverified")}{!result.current ? ` ${t("ownership.loadFailed")}` : ""}</p> : null}
      {error ? <p className="personal-machine-error" role="alert">{error}</p> : null}
      {result?.reason_code ? <p><code>{result.reason_code}</code></p> : null}
      {result && result.conflict_count > 0 ? <p>{t("ownership.conflictCount", {count: result.conflict_count})}</p> : null}
      {result && result.conflicts.length > 0 ? <details><summary>{t("ownership.conflictDetails")}</summary><ul>{result.conflicts.map((row, index) => <li key={index}>{row.todo_id}: {row.reason_code}</li>)}</ul></details> : null}
      <button disabled={busy} onClick={() => setReload(n => n + 1)} type="button">{t("ownership.refresh")}</button>
      {busy ? <p aria-live="polite">{t("common.loading")}</p> : null}
    </div>
  </section>;
}
