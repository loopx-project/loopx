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
  const [cold, setCold] = useState<StorageResult["cold_source"]>();
  const [carrier, setCarrier] = useState<StorageCarrier | null>(null);
  const [result, setResult] = useState<StorageResult | null>(null);
  const [target, setTarget] = useState<MigrationProvider>("sqlite");
  const [mode, setMode] = useState<"" | "soft_claim" | "hard_lease">("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [invalidSaved, setInvalidSaved] = useState(false);
  const inFlight = useRef(false);
  const generation = useRef(0);
  useEffect(() => {
    const token = ++generation.current;
    setCurrent(null); setCold(undefined); setResult(null); setCarrier(null); setConfirmed(false); setMode(""); setInvalidSaved(false); setError(null); setBusy(true);
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
        setCold(observed.cold_source);
        if (observed.ok && observed.current) {
          setCurrent(observed.current);
          setTarget(observed.current.provider === "sqlite" ? "file" : "sqlite");
        } else { setResult(observed); setError(t(saved ? "storage.unavailable" : "storage.readUnavailable")); }
        if (saved) {
          const recovered = await recoverGoalStorage(saved);
          if (token !== generation.current) return;
          setResult(recovered);
          if (recovered.current !== undefined) setCurrent(recovered.current);
          if (!recovered.ok) setError(t("storage.rejected"));
          else if (!recovered.current) setError(t("storage.unavailable"));
        }
      } catch { if (token === generation.current) setError(t(saved ? "storage.unavailable" : "storage.readUnavailable")); }
      finally { if (token === generation.current) setBusy(false); }
    })();
    return () => { generation.current++; };
  }, [goalId, key, reload, t]);

  async function submit(apply: boolean) {
    if (inFlight.current || busy || invalidSaved || (apply && (!carrier || !confirmed)) ||
      (!apply && !current?.canonical && !mode)) return;
    inFlight.current = true; setBusy(true); setError(null);
    const token = generation.current;
    try {
      const next = apply ? await recoverGoalStorage(carrier!, true) : await previewGoalStorage(goalId, target,
        current?.canonical ? undefined : mode || undefined);
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
  const coldCarrier = carrier && "operation_id" in carrier;
  const completed = result?.recovery?.phase === "completed" || (coldCarrier &&
    (result?.status === "applied" || result?.status === "recovered" || result?.status === "replayed"));
  return <section className="personal-cadence-settings" aria-label={t("storage.title")}>
    <div className="personal-cadence-form">
      <h3>{t("storage.title")}</h3>
      {current?.canonical || carrier ? <p>{t("storage.boundary")}</p> : null}
      {current ? <div className="personal-cadence-readback"><small>{t("storage.current")}</small>
        <strong>{current.provider ?? t("storage.oldSource")}</strong>
        {current.canonical ? <span>{t("storage.counts", {todos: current.todo_count ?? 0, leases: current.unsettled_lease_count ?? 0})}</span> : <>
          {cold ? <>
            <span>{t("storage.coldCounts", {active: cold.active_todo_count, archived: cold.archived_todo_count, leases: cold.unsettled_lease_count})}</span>
            {cold.capture_artifacts_present ? <span>{t("storage.coldCapture")}</span> : null}
            {cold.outbox_files_present ? <span>{t("storage.coldOutbox")}</span> : null}
          </> : null}
          <p>{t("storage.coldBoundary")}</p>
        </>}
      </div> : null}
      {current || carrier ? <>
        {carrier ? <p>{coldCarrier ? t("storage.coldReviewed", {target: result?.target_provider ?? "?", mode: result?.target_handoff_mode ? t(`ownership.${result.target_handoff_mode}`) : "?"}) : t("storage.reviewed", {source: result?.reviewed_source?.provider ?? "?", target: result?.target_provider ?? "?", cursor: result?.reviewed_source?.cursor ?? "?"})}</p>
          : <label>{t("storage.target")}<select aria-label={t("storage.target")} value={target} disabled={busy} onChange={e => setTarget(e.target.value as MigrationProvider)}>
            <option value="sqlite">SQLite</option><option value="file">File</option></select></label>}
        {!carrier && !current?.canonical ? <label>{t("ownership.target")}<select aria-label={t("ownership.target")} value={mode} disabled={busy} onChange={e => setMode(e.target.value as typeof mode)}>
          <option value="" disabled>{t("storage.choosePolicy")}</option>
          <option value="soft_claim">{t("ownership.soft_claim")}</option><option value="hard_lease">{t("ownership.hard_lease")}</option>
        </select></label> : null}
        {result?.source_inventory ? <p>{t("storage.coldInventory", {todos: result.source_inventory.todo_count, archived: result.source_inventory.archived_todo_count, leases: result.source_inventory.lease_count})}</p> : null}
        {carrier && !completed ? <label><input type="checkbox" checked={confirmed} disabled={busy} onChange={e => setConfirmed(e.target.checked)} />{t(coldCarrier ? "storage.coldConfirm" : "storage.confirm")}</label> : null}
        <div className="personal-cadence-actions">
          {carrier ? <><button className="is-primary" disabled={busy || !confirmed || completed || !result?.ok} onClick={() => void submit(true)} type="button">{t(coldCarrier ? "storage.coldApply" : "storage.apply")}</button>
            <button disabled={busy} onClick={discard} type="button">{t("storage.fresh")}</button></>
            : <button className="is-primary" disabled={busy || invalidSaved || (current?.canonical ? !current.provider || target === current.provider : !mode)} onClick={() => void submit(false)} type="button">{t(current?.canonical ? "storage.preview" : "storage.coldPreview")}</button>}
        </div>
      </> : null}
      <div className="personal-cadence-actions">
        {invalidSaved ? <button disabled={busy} onClick={discard} type="button">{t("storage.fresh")}</button> : null}
        <button disabled={busy} onClick={() => setReload(n => n + 1)} type="button">{t(carrier ? "storage.recover" : "storage.refresh")}</button>
      </div>
      {completed ? <p role="status">{t(coldCarrier ? "storage.coldCompleted" : "storage.completed")}</p> : null}
      {result?.recovery?.phase === "prepared" || (coldCarrier && result?.status === "prepared") ? <p role="status">{t("storage.prepared")}</p> : null}
      {error ? <p className="personal-machine-error" role="alert">{error}</p> : null}
      {result?.reason_code ? <p><code>{result.reason_code}</code></p> : null}
      {current?.canonical || carrier ? <details><summary>{t("storage.details")}</summary>
        {current?.canonical ? <p>{current.store_identity} · {current.provider_revision} · {current.cursor}</p> : null}
        {carrier ? <p>{"operation_id" in carrier ? carrier.operation_id : carrier.preview_id} · {carrier.plan_sha256}</p> : null}
      </details> : null}
      {busy ? <p aria-live="polite">{t("common.loading")}</p> : null}
    </div>
  </section>;
}
