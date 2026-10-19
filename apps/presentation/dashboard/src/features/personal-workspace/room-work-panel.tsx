import "./room-work.css";
import {useEffect, useRef, useState} from "react";
import {claimRoomWork, fetchRoomWork, roomClaimIntentSchema, type RoomClaimIntent, type RoomWork} from "../../data/room-work";
import {ChatApiError, type LarkGoalConnection} from "../../data/chat";
import {useWorkspaceI18n} from "./i18n";

/** Browser storage holds only an uncertain request tuple, never work authority. */
export function RoomWorkPanel({connection, onClose, triggerElement}: {connection: LarkGoalConnection; onClose: () => void; triggerElement: HTMLElement | null}) {
  const {t} = useWorkspaceI18n();
  const storageKey = `loopx.room-work:${connection.goal_id}:${connection.connection_id}`;
  const [intent, setIntent] = useState<RoomClaimIntent | null>(() => {
    try {
      const saved = roomClaimIntentSchema.safeParse(JSON.parse(sessionStorage.getItem(storageKey) ?? "null"));
      return saved.success && saved.data.goal_id === connection.goal_id && saved.data.connection_id === connection.connection_id ? saved.data : null;
    } catch {return null;}
  });
  const [work, setWork] = useState<RoomWork | null>(null);
  const [result, setResult] = useState<RoomWork | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const envelope = work?.projection_envelope;
  const projectionReady = envelope?.fresh === true && envelope.complete && envelope.alert === false;
  const inFlight = useRef(false);
  const alive = useRef(true);
  const dialog = useRef<HTMLElement>(null);
  useEffect(() => {
    const prior = triggerElement ?? document.activeElement;
    dialog.current?.focus();
    function keydown(event: KeyboardEvent) {
      if (event.key === "Escape") {event.preventDefault(); event.stopPropagation(); onClose();}
      if (event.key !== "Tab") return;
      const controls = Array.from(dialog.current?.querySelectorAll<HTMLElement>("button:not(:disabled), summary") ?? []);
      const first = controls[0], last = controls.at(-1);
      const outside = !dialog.current?.contains(document.activeElement);
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current || outside)) {event.preventDefault(); last?.focus();}
      else if (!event.shiftKey && (document.activeElement === last || outside)) {event.preventDefault(); first?.focus();}
    }
    document.addEventListener("keydown", keydown);
    return () => {
      document.removeEventListener("keydown", keydown);
      if (prior instanceof HTMLElement && prior.isConnected) prior.focus();
    };
  }, []);

  async function refresh() {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError("");
    try {
      const next = await fetchRoomWork(connection.goal_id, connection.connection_id, intent?.todo_id ?? result?.receipt?.todo_id);
      if (alive.current) {setWork(next); if (!next.ok) setError(t("roomWork.unavailable"));}
    } catch {if (alive.current) {setWork(null); setError(t("roomWork.unavailable"));}}
    finally {inFlight.current = false; if (alive.current) setBusy(false);}
  }
  useEffect(() => {alive.current = true; void refresh(); return () => {alive.current = false;};}, []);

  async function claim() {
    if (inFlight.current) return;
    const selected = work?.projection?.selected_todo;
    if (!intent && (!work?.ok || !projectionReady || !work.claim_available || !selected)) return;
    const request = intent ?? {goal_id: connection.goal_id, connection_id: connection.connection_id,
      scope_revision: work!.scope_revision, todo_id: selected!.todo_id,
      expected_revision: work!.projection!.source_revision, idempotency_key: crypto.randomUUID()};
    // Preserve the original tuple before the effect; a lost response or reload
    // can only retry this exact request, never generate another claim key.
    try {sessionStorage.setItem(storageKey, JSON.stringify(request));}
    catch {setError(t("roomWork.storageUnavailable")); return;}
    setIntent(request); inFlight.current = true; setBusy(true); setError("");
    try {
      const next = await claimRoomWork(request);
      if (!alive.current) return;
      setResult(next); setWork(next);
      if (next.ok || next.status === "conflict" || next.status === "rejected") {
        sessionStorage.removeItem(storageKey); setIntent(null);
      }
      if (!next.ok) setError(t(next.status === "conflict" ? "roomWork.conflict" : next.canonical_claim_accepted ? "roomWork.deliveryPending" : "roomWork.unavailable"));
    } catch (cause) {
      if (!alive.current) return;
      if (cause instanceof ChatApiError && [400, 409].includes(Number(cause.payload.http_status))) {
        sessionStorage.removeItem(storageKey); setIntent(null); setWork(null);
        setError(t("roomWork.scopeChanged"));
      } else setError(t("roomWork.unknown"));
    } finally {inFlight.current = false; if (alive.current) setBusy(false);}
  }
  const observed = !projectionReady || !work?.projection ? null : Object.hasOwn(work.projection, "inspected_todo")
    ? work.projection.inspected_todo : result?.receipt;
  return <div className="personal-lark-modal-backdrop" role="presentation">
    <section ref={dialog} tabIndex={-1} aria-labelledby="room-work-title" aria-modal="true" className="personal-lark-modal personal-room-work" role="dialog">
      <header><h2 id="room-work-title">{t("roomWork.title")}</h2><button type="button" onClick={onClose} aria-label={t("roomWork.close")}>×</button></header>
      <p>{connection.chat_name} · {connection.goal_title}</p>
      <p>{t("roomWork.agent")}: {work?.actor_id ?? connection.agent_id}</p>
      <p className="personal-room-work-boundary">{t("roomWork.boundary")}</p>
      {work && !projectionReady ? <p role="alert">{t("roomWork.readUnavailable")}</p> : null}
      {work?.ok && projectionReady && !work.claim_available && !result ? <p role="status">{t("roomWork.readOnly")}</p> : null}
      {error ? <p role="alert">{error}</p> : null}
      {busy ? <p role="status">{t("roomWork.loading")}</p> : null}
      {work?.projection && projectionReady ? <dl className="personal-room-work-counts"><div><dt>{t("roomWork.available")}</dt><dd>{work.projection.counts.unclaimed}</dd></div>
        <div><dt>{t("roomWork.gates")}</dt><dd>{work.projection.counts.user_gates}</dd></div></dl> : null}
      {result?.canonical_claim_accepted && result.receipt ? <p role="status">{t("roomWork.accepted")}</p> : null}
      {result?.readback_verified ? <p role="status">{t("roomWork.messageVerified")}</p> : null}
      {observed ? <p>{t("roomWork.owner")}: {observed.current_owner === undefined ? t("roomWork.unavailable") : observed.current_owner ?? t("roomWork.none")}</p> : null}
      {intent ? <p>{t("roomWork.pending")}</p> : null}
      {work?.projection ? <details><summary>{t("roomWork.details")}</summary><dl>
        <dt>{t("roomWork.task")}</dt><dd>{intent?.todo_id ?? work.projection.inspected_todo?.todo_id ?? result?.receipt?.todo_id ?? work.projection.selected_todo?.todo_id ?? t("roomWork.none")}</dd>
        <dt>{t("roomWork.observed")}</dt><dd>{envelope?.observed_at ?? work.projection.generated_at}</dd>
        {result?.receipt ? <><dt>{t("roomWork.receipt")}</dt><dd>{result.receipt.receipt_id}</dd></> : null}
      </dl></details> : null}
      <footer><button type="button" disabled={busy} onClick={() => void refresh()}>{t("roomWork.refresh")}</button>
        {intent || work?.projection?.selected_todo ? <button className="personal-primary-action" type="button" disabled={busy || (!intent && (!work?.ok || !projectionReady || !work.claim_available))} onClick={() => void claim()}>{t(intent ? "roomWork.recover" : "roomWork.claim")}</button> : null}</footer>
    </section>
  </div>;
}
