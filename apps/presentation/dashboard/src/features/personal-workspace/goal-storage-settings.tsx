import {useEffect, useRef, useState} from "react";
import {fetchGoalStorage, previewGoalStorage, recoverGoalStorage, storageCarrierSchema,
  type StorageCarrier, type StorageResult, type StorageSource, type MigrationProvider} from "../../data/goal-storage";
import {useWorkspaceI18n} from "./i18n";

/** Only the immutable opaque carrier survives reload. Neither cached source
 * facts nor browser confirmation replace the server's current source checks. */
export function GoalStorageSettings({goalId, onChanged}: {goalId: string; onChanged: () => void}) {
  const {t} = useWorkspaceI18n();
  const key = `loopx-storage-preview:${goalId}`;
  const [current, setCurrent] = useState<StorageSource | null>(null);
  const [carrier, setCarrier] = useState<StorageCarrier | null>(null);
  const [result, setResult] = useState<StorageResult | null>(null);
  const [target, setTarget] = useState<MigrationProvider>("sqlite");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [invalidSaved, setInvalidSaved] = useState(false);
  const inFlight = useRef(false);
  const generation = useRef(0);
  useEffect(() => {
    const token = ++generation.current;
    setCurrent(null); setResult(null); setCarrier(null); setConfirmed(false); setInvalidSaved(false); setError(null); setBusy(true);
    let saved: StorageCarrier | null = null;
    try {
      const raw = localStorage.getItem(key);
      saved = raw ? storageCarrierSchema.parse(JSON.parse(raw)) : null;
      if (saved && saved.goal_id !== goalId) throw new Error("Goal mismatch");
      setCarrier(saved);
    } catch { setInvalidSaved(true); setError(t("storage.savedInvalid")); }
    void (async () => {
      try {
        const observed = await fetchGoalStorage(goalId);
        if (token !== generation.current) return;
        if (observed.ok && observed.current) {
          setCurrent(observed.current);
          setTarget(observed.current.provider === "sqlite" ? "file" : "sqlite");
        } else setError(t("storage.unavailable"));
        if (saved) {
          const recovered = await recoverGoalStorage(saved);
          if (token !== generation.current) return;
          setResult(recovered);
          if (recovered.current !== undefined) setCurrent(recovered.current);
          if (!recovered.ok) setError(t("storage.rejected"));
          else if (!recovered.current) setError(t("storage.unavailable"));
        }
      } catch { if (token === generation.current) setError(t("storage.unavailable")); }
      finally { if (token === generation.current) setBusy(false); }
    })();
    return () => { generation.current++; };
  }, [goalId, key, reload, t]);

  async function submit(apply: boolean) {
    if (inFlight.current || busy || invalidSaved || (apply && (!carrier || !confirmed))) return;
    inFlight.current = true; setBusy(true); setError(null);
    const token = generation.current;
    try {
      const next = apply ? await recoverGoalStorage(carrier!, true) : await previewGoalStorage(goalId, target);
      if (token !== generation.current) return;
      setResult(next);
      if (!apply && next.ok) {
        const saved = storageCarrierSchema.parse(next);
        // Persist before showing an enabled write control. Storage failure must
        // not allow a write whose original intent cannot be recovered.
        localStorage.setItem(key, JSON.stringify(saved)); setCarrier(saved); setConfirmed(false);
      }
      if (apply) setCurrent(next.current ?? null);
      if (!next.ok) setError(t("storage.rejected"));
      else if (apply) {
        setConfirmed(false);
        if (!next.current) setError(t("storage.unavailable"));
        onChanged();
      }
    } catch {
      if (token === generation.current) {
        if (apply) { setCurrent(null); setConfirmed(false); }
        setError(t(apply ? "storage.ambiguous" : "storage.unavailable"));
      }
    }
    finally { inFlight.current = false; if (token === generation.current) setBusy(false); }
  }
  function discard() {
    try { localStorage.removeItem(key); }
    catch { setError(t("storage.savedInvalid")); return; }
    setCarrier(null); setResult(null); setConfirmed(false); setInvalidSaved(false); setError(null);
  }
  const completed = result?.recovery?.phase === "completed";
  return <section className="personal-cadence-settings" aria-label={t("storage.title")}>
    <div className="personal-cadence-form">
      <h3>{t("storage.title")}</h3>
      <p>{t("storage.boundary")}</p>
      {current ? <div className="personal-cadence-readback"><small>{t("storage.current")}</small>
        <strong>{current.provider ?? t("ownership.unpromoted")}</strong>
        {current.canonical ? <span>{t("storage.counts", {todos: current.todo_count ?? 0, leases: current.unsettled_lease_count ?? 0})}</span> : <p>{t("storage.promoteFirst")}</p>}
      </div> : null}
      {current?.canonical || carrier ? <>
        {carrier ? <p>{t("storage.reviewed", {source: result?.reviewed_source?.provider ?? "?", target: result?.target_provider ?? "?", cursor: result?.reviewed_source?.cursor ?? "?"})}</p>
          : <label>{t("storage.target")}<select aria-label={t("storage.target")} value={target} disabled={busy} onChange={e => setTarget(e.target.value as MigrationProvider)}>
            <option value="sqlite">SQLite</option><option value="file">File</option></select></label>}
        {carrier && !completed ? <label><input type="checkbox" checked={confirmed} disabled={busy} onChange={e => setConfirmed(e.target.checked)} />{t("storage.confirm")}</label> : null}
        <div className="personal-cadence-actions">
          {carrier ? <><button className="is-primary" disabled={busy || !confirmed || completed || !result?.ok} onClick={() => void submit(true)} type="button">{t("storage.apply")}</button>
            <button disabled={busy} onClick={discard} type="button">{t("storage.fresh")}</button></>
            : <button className="is-primary" disabled={busy || invalidSaved || !current?.provider || target === current.provider} onClick={() => void submit(false)} type="button">{t("storage.preview")}</button>}
        </div>
      </> : null}
      <div className="personal-cadence-actions">
        {invalidSaved ? <button disabled={busy} onClick={discard} type="button">{t("storage.fresh")}</button> : null}
        <button disabled={busy} onClick={() => setReload(n => n + 1)} type="button">{t(carrier ? "storage.recover" : "storage.refresh")}</button>
      </div>
      {completed ? <p role="status">{t("storage.completed")}</p> : null}
      {result?.recovery?.phase === "prepared" ? <p role="status">{t("storage.prepared")}</p> : null}
      {error ? <p className="personal-machine-error" role="alert">{error}</p> : null}
      {result?.reason_code ? <p><code>{result.reason_code}</code></p> : null}
      {current?.canonical || carrier ? <details><summary>{t("storage.details")}</summary>
        {current?.canonical ? <p>{current.store_identity} · {current.provider_revision} · {current.cursor}</p> : null}
        {carrier ? <p>{carrier.preview_id} · {carrier.plan_sha256}</p> : null}
      </details> : null}
      {busy ? <p aria-live="polite">{t("common.loading")}</p> : null}
    </div>
  </section>;
}
