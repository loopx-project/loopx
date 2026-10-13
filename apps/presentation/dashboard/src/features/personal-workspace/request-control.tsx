import {useCallback, useEffect, useRef, useState} from "react";
import {ChatApiError} from "../../data/chat";
import {applyRequestControl, fetchRequestControl, pendingRequestControlSchema, requestControlRouteKey,
  type PendingRequestControl, type RequestBrief, type RequestControlRoute, type RequestControlView} from "../../data/request-control";
import {useWorkspaceI18n} from "./i18n";

const words = {
  "zh-CN": {open: "修正或取消请求", refresh: "重新读取", edit: "修正内容", save: "保存修正", cancel: "取消请求", recover: "核对原操作",
    revision: "请求版本", pending: "目标变更仍待核对", cancelling: "取消已记录，原操作仍待核对", cancelled: "请求已取消", current: "当前请求",
    committed: "已写入目标", aborted: "已中止，未写入目标", rejected: "目标未接受这项变更", goalRevision: "目标版本",
    limitation: "取消会停止这项请求的新变更；已经写入目标的任务会保留。", retained: "已记录的变更不代表任务执行或验收完成。",
    purpose: "请求内容", context: "背景与补充", constraints: "约束（每行一项）", acceptance: "验收要求（每行一项）", expected: "需要回传",
    inputs: "输入材料", ref: "工作区内的相对路径", description: "材料说明", digest: "版本摘要（可选）", add: "添加材料", remove: "移除材料",
    reason: "修正原因（可选）", defaultReason: "在原对话中修正请求", cancelReason: "原请求发起者取消请求", close: "收起编辑",
    unknown: "结果尚未确认。原操作已保留，可重新核对。", retry: "重试原操作", stale: "请求已有新修订。已重新读取，请基于当前内容修改。",
    historical: "这是历史 Goal 实例的请求，当前界面仅保留回读。", loading: "正在读取请求…", error: "暂时无法读取当前请求。", storage: "无法保存恢复信息，本次操作尚未发送。",
  },
  en: {open: "Revise or cancel request", refresh: "Refresh", edit: "Revise content", save: "Save revision", cancel: "Cancel request", recover: "Recover original operation",
    revision: "Request revision", pending: "Goal effect needs reconciliation", cancelling: "Cancellation recorded; original operation still needs reconciliation", cancelled: "Request cancelled", current: "Current request",
    committed: "Committed to Goal", aborted: "Aborted without a Goal change", rejected: "Goal change rejected", goalRevision: "Goal revision",
    limitation: "Cancellation stops new changes for this request. Work already committed to the Goal is retained.", retained: "A recorded change does not prove task execution or acceptance.",
    purpose: "Request", context: "Context & corrections", constraints: "Constraints (one per line)", acceptance: "Acceptance (one per line)", expected: "Expected return",
    inputs: "Input materials", ref: "Relative workspace path", description: "Description", digest: "Version digest (optional)", add: "Add material", remove: "Remove material",
    reason: "Revision reason (optional)", defaultReason: "Request revised in the original conversation", cancelReason: "Original requester cancelled the request", close: "Close editor",
    unknown: "The outcome is not confirmed. The original operation is retained for recovery.", retry: "Retry original operation", stale: "The request changed. Its current revision has been loaded for editing.",
    historical: "This request belongs to a historical Goal instance and remains read-only here.", loading: "Reading the request…", error: "Current request readback is unavailable.", storage: "Recovery data could not be saved. The operation was not sent.",
  },
};
const lines = (value: string) => value.split("\n").map(row => row.trim()).filter(Boolean);

export function RequestControl({route, active, onReadback, onEditing}: {route: RequestControlRoute; active: boolean;
  onReadback: (view: RequestControlView | null) => void; onEditing: (editing: boolean) => void}) {
  const {locale} = useWorkspaceI18n(); const c = words[locale];
  const [opened, setOpened] = useState(false), [view, setView] = useState<RequestControlView | null>(null);
  const [draft, setDraft] = useState<RequestBrief | null>(null), [reason, setReason] = useState("");
  const [pending, setPending] = useState<PendingRequestControl | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const generation = useRef(0), onRead = useRef(onReadback); onRead.current = onReadback;
  const editNotice = useRef(onEditing); editNotice.current = onEditing;
  useEffect(() => {editNotice.current(draft !== null);}, [draft !== null]);
  const storageKey = `loopx.request-control.${route.goal_instance_id}.${route.session_id}.${route.request_id}`;
  const routeKey = requestControlRouteKey(route);
  const show = useCallback((value: RequestControlView | null) => {setView(value); onRead.current(value);}, []);
  const remember = (value: PendingRequestControl | null) => {
    if (value) sessionStorage.setItem(storageKey, JSON.stringify(value)); else sessionStorage.removeItem(storageKey);
    setPending(value);
  };
  const load = useCallback(async () => {
    const observed = ++generation.current;
    try {
      const value = await fetchRequestControl(route);
      if (observed === generation.current) {show(value); return value;}
    } catch (failure) {
      if (observed === generation.current) {show(null); setError(failure instanceof Error ? failure.message : c.error);}
    }
    return null;
  }, [routeKey, show, c.error]); // Route identity, not caller object allocation, owns this read.
  useEffect(() => {
    generation.current++; setOpened(false); setDraft(null); setReason(""); setPending(null); setError(""); setView(null); setBusy(false);
    try {
      const raw = sessionStorage.getItem(storageKey);
      if (raw) {
        const saved = pendingRequestControlSchema.parse(JSON.parse(raw));
        if (requestControlRouteKey(saved.route) === routeKey) {setPending(saved); setOpened(true); void load();}
      }
    } catch {setError(c.unknown);}
    return () => {generation.current++;};
  }, [storageKey, routeKey]);
  useEffect(() => {
    if (active && !opened && route.available !== false) {setOpened(true); void load();}
  }, [active, opened, route.available, load]);

  async function submit(request: PendingRequestControl) {
    const observed = ++generation.current;
    setBusy(true); setError("");
    if (pending === null && request.operation !== "recover") {
      try {
        await applyRequestControl(request, false);
        if (observed !== generation.current) return;
      } catch (failure) {
        if (observed !== generation.current) return;
        setBusy(false);
        if (failure instanceof ChatApiError && failure.payload.error_code === "request_basis_changed") {
          setDraft(null); await load(); setError(c.stale);
        } else setError(failure instanceof Error ? failure.message : c.error);
        return;
      }
    }
    try {remember(request);} catch {setBusy(false); setError(c.storage); return;}
    try {
      const result = await applyRequestControl(request);
      if (observed !== generation.current) return;
      const current = result.view ?? result.request_control ?? null;
      show(current);
      if (!result.ok || !current) {
        setError(result.readback_errors?.join(" ") || result.effect_recovery?.readback_errors?.join(" ") || c.unknown);
        return;
      }
      remember(null); setDraft(null); setReason("");
    } catch (failure) {
      if (observed !== generation.current) return;
      show(null);
      if (failure instanceof ChatApiError && failure.payload.error_code === "request_basis_changed") {
        remember(null); setDraft(null); setBusy(false);
        await load(); setError(c.stale);
      } else setError(failure instanceof Error ? `${c.unknown} ${failure.message}` : c.unknown);
    } finally {if (observed === generation.current) setBusy(false);}
  }
  function operation(kind: "revise" | "cancel" | "recover") {
    if (kind !== "recover" && !view) return;
    const parameters = kind === "recover" ? {} : {operation_id: `request-${kind}-${crypto.randomUUID()}`,
      expected_revision: view!.revision, expected_digest: view!.revision_digest,
      reason: kind === "cancel" ? c.cancelReason : reason.trim() || c.defaultReason,
      ...(kind === "revise" ? {brief: draft} : {})};
    void submit({route, operation: kind, parameters, execute: true});
  }
  if (route.available === false) return <p>{c.historical}</p>;
  if (!opened) return null;
  const terminal = view?.terminal_effects.at(-1);
  return <section className="personal-request-control" aria-label={c.open}>
    {!view && <p><strong>{busy ? c.loading : c.error}</strong></p>}
    {view && view.control_state !== "open" && <p role="status">{view.control_state === "cancelled" ? c.cancelled : view.control_state === "cancellation_pending" ? c.cancelling
      : view.control_state === "effect_pending" ? c.pending : c.current}</p>}
    {terminal && <p>{c.revision} {terminal.binding.request_revision}: {c[terminal.terminal.outcome]}
      {terminal.terminal.goal_revision !== null ? ` · ${c.goalRevision} ${terminal.terminal.goal_revision}` : ""}</p>}
    {view?.terminal_effects.length ? <p className="personal-request-help">{c.retained}</p> : null}
    <p className="personal-request-help">{c.limitation}</p>
    {error && <p role="alert">{error}</p>}
    <div className="personal-request-actions">
      <button type="button" disabled={busy} onClick={() => void load()}>{c.refresh}</button>
      {pending ? <button type="button" disabled={busy} onClick={() => void submit(pending)}>{c.retry}</button> : <>
        {view && !view.cancellation && <button type="button" disabled={busy} onClick={() => {setDraft(structuredClone(view.brief)); setReason("");}}>{c.edit}</button>}
        {view && !view.cancellation && <button type="button" disabled={busy} onClick={() => operation("cancel")}>{c.cancel}</button>}
        {view?.active_reservation && <button type="button" disabled={busy} onClick={() => operation("recover")}>{c.recover}</button>}
      </>}
    </div>
    {draft && <form onSubmit={event => {event.preventDefault(); operation("revise");}}>
      <label>{c.purpose}<input aria-label={c.purpose} value={draft.purpose} disabled={busy || !!pending} onChange={e => setDraft({...draft, purpose: e.target.value})} required /></label>
      <label>{c.context}<textarea aria-label={c.context} value={draft.context} disabled={busy || !!pending} onChange={e => setDraft({...draft, context: e.target.value})} required /></label>
      <label>{c.constraints}<textarea aria-label={c.constraints} value={draft.constraints.join("\n")} disabled={busy || !!pending} onChange={e => setDraft({...draft, constraints: lines(e.target.value)})} /></label>
      <label>{c.acceptance}<textarea aria-label={c.acceptance} value={draft.acceptance.join("\n")} disabled={busy || !!pending} onChange={e => setDraft({...draft, acceptance: lines(e.target.value)})} required /></label>
      <label>{c.expected}<textarea aria-label={c.expected} value={draft.return_requirement} disabled={busy || !!pending} onChange={e => setDraft({...draft, return_requirement: e.target.value})} required /></label>
      <fieldset><legend>{c.inputs}</legend>{draft.inputs.map((input, index) => <div className="personal-request-input" key={index}>
        <label>{c.ref}<input aria-label={c.ref} value={input.ref} disabled={busy || !!pending} onChange={e => setDraft({...draft, inputs: draft.inputs.map((old, i) => i === index ? {...old, ref: e.target.value} : old)})} required /></label>
        <label>{c.description}<input aria-label={c.description} value={input.description} disabled={busy || !!pending} onChange={e => setDraft({...draft, inputs: draft.inputs.map((old, i) => i === index ? {...old, description: e.target.value} : old)})} required /></label>
        <label>{c.digest}<input aria-label={c.digest} value={input.sha256 ?? ""} disabled={busy || !!pending} onChange={e => setDraft({...draft, inputs: draft.inputs.map((old, i) => {
          if (i !== index) return old; const next = {...old}; if (e.target.value) next.sha256 = e.target.value; else delete next.sha256; return next;
        })})} /></label>
        <button type="button" disabled={busy || !!pending} onClick={() => setDraft({...draft, inputs: draft.inputs.filter((_, i) => i !== index)})}>{c.remove}</button>
      </div>)}<button type="button" disabled={busy || !!pending || draft.inputs.length >= 12} onClick={() => setDraft({...draft, inputs: [...draft.inputs, {ref: "", description: ""}]})}>{c.add}</button></fieldset>
      <label>{c.reason}<input aria-label={c.reason} value={reason} disabled={busy || !!pending} onChange={e => setReason(e.target.value)} /></label>
      <div className="personal-request-actions"><button disabled={busy || !!pending} type="submit">{c.save}</button>
        <button disabled={busy || !!pending} type="button" onClick={() => setDraft(null)}>{c.close}</button></div>
    </form>}
  </section>;
}
