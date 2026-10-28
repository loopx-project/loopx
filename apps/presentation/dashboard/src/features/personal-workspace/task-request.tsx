import {useEffect, useId, useRef, useState} from "react";
import {compactPythonWhitespace, compactPythonTextLength} from "../../../../../../loopx/control_plane/coordination/python_whitespace.ts";
import {readTodoRequest} from "../../data/chat";
import type {TodoUpdateSourceBasis} from "../../data/action-source-basis";
import {useWorkspaceI18n} from "./i18n";
import {MarkdownText} from "./markdown";
import type {PersonalWorkspaceCallbacks, WorkspaceActionPreview, WorkspaceTodo} from "./personal-workspace-model";

// Per-open read/draft state, never a second Task or execution owner.
type RequestRead = {identity: string} & (
  | {phase: "loading" | "error"; detail?: never}
  | {phase: "ready"; detail: Awaited<ReturnType<typeof readTodoRequest>>}
);

export function TaskRequest({todo, local, sourceNamespace, onSave}: {todo: WorkspaceTodo; local: boolean; sourceNamespace?: string;
  onSave?: PersonalWorkspaceCallbacks["onSaveTodoRequirements"]}) {
  const {locale, t} = useWorkspaceI18n();
  const countId = useId();
  const sourceId = todo.sourceTodoId;
  const identity = JSON.stringify([local, sourceNamespace, todo.goalId, sourceId, todo.requestText, todo.status]);
  const [read, setRead] = useState<RequestRead | null>(null);
  const [retry, setRetry] = useState(0);
  const [draft, setDraft] = useState<{text: string; basis: TodoUpdateSourceBasis} | null>(null);
  const [pending, setPending] = useState<WorkspaceActionPreview | null>(null);
  const [saving, setSaving] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const savingRef = useRef(false);
  const rebaseRef = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!local || !sourceId) return;
    const controller = new AbortController();
    setRead({identity, phase: "loading"});
    void readTodoRequest(todo.goalId, sourceId, controller.signal, sourceNamespace).then(detail => {
      if (controller.signal.aborted) return;
      setRead({identity, phase: "ready", detail});
      if (rebaseRef.current && detail.review_basis) {
        const basis = detail.review_basis;
        setDraft(previous => previous ? {...previous, basis} : null);
        setPending(null);
        setFailure(null);
      }
      rebaseRef.current = false;
    }).catch(() => {
      if (!controller.signal.aborted) setRead({identity, phase: "error"});
    });
    return () => controller.abort();
  }, [identity, local, retry, todo.goalId, sourceId, sourceNamespace]);
  const current = local && read?.identity === identity ? read : null;
  const detail = current?.phase === "ready" ? current.detail : null;
  const editable = local && onSave && todo.claimedBy && sourceId && detail?.review_basis
    && detail.archive_state === "active" && detail.status !== "done";
  const draftLength = compactPythonTextLength(draft?.text ?? "");
  async function save() {
    if (!onSave || !draft || !sourceId || !editable || savingRef.current || !draftLength || draftLength > 8000) return;
    savingRef.current = true;
    setSaving(true); setFailure(null); setSaved(false);
    try {
      const text = compactPythonWhitespace(draft.text);
      const proposal = await onSave(todo, text, draft.basis, pending ?? undefined);
      if (!mounted.current) return;
      setPending(proposal);
      if (proposal.status !== "applied" || proposal.reviewPlan?.interaction !== "completed") {
        throw new Error(proposal.errorMessage ?? proposal.gate?.summary
          ?? (proposal.reviewPlan ? t(`actionReview.${proposal.reviewPlan.reason}`) : t("drawer.requirementsNotSaved")));
      }
      const observed = await readTodoRequest(todo.goalId, sourceId, new AbortController().signal, sourceNamespace);
      if (!mounted.current) return;
      setRead({identity, phase: "ready", detail: observed});
      if (observed.text !== text) throw new Error(t("drawer.requirementsReadbackChanged"));
      setDraft(null); setPending(null); setSaved(true);
    } catch (error) {
      if (mounted.current) setFailure(error instanceof Error ? error.message : String(error));
    } finally {
      savingRef.current = false;
      if (mounted.current) setSaving(false);
    }
  }
  return <div className="personal-task-request">
    {!draft ? <MarkdownText text={detail?.text ?? todo.requestText ?? todo.text} /> : null}
    {current?.phase !== "ready" ? <div className="personal-proposal-explainer" role={current?.phase === "error" ? "alert" : "status"}>
      <span>{t(!local ? "drawer.requestLocalOnly" : !sourceId ? "drawer.requestUnidentified" : current?.phase === "error" ? "drawer.requestError" : "drawer.requestLoading")}</span>
      {current?.phase === "error" ? <button className="personal-secondary-action" type="button" onClick={() => setRetry(value => value + 1)}>{t("drawer.requestRetry")}</button> : null}
    </div> : null}
    {editable && !draft ? <button className="personal-secondary-action" type="button" onClick={() => {
      setDraft({text: detail.text, basis: detail.review_basis!}); setFailure(null); setSaved(false);
    }}>{t("drawer.requirementsEdit")}</button> : null}
    {draft ? <form className="personal-task-requirements-editor" onSubmit={event => {event.preventDefault(); void save();}}>
      <label>{t("drawer.requirementsLabel")}<textarea autoFocus rows={8} value={draft.text} disabled={saving}
        aria-describedby={countId} aria-invalid={draftLength > 8000}
        onChange={event => {setDraft({...draft, text: event.target.value}); setPending(null); setSaved(false);}} /></label>
      <output id={countId} aria-live="off" aria-label={locale === "zh-CN" ? "保存字数" : "Characters to save"}>{draftLength} / 8000</output>
      <p>{t("drawer.requirementsEffect")}</p>
      <div><button className="personal-primary-action" type="submit" disabled={saving || !editable || !draftLength || draftLength > 8000}>
        {t(saving ? "drawer.requirementsSaving" : pending?.status === "applied" || pending?.status === "error" ? "drawer.requirementsReadback" : "drawer.requirementsSave")}</button>
        <button className="personal-secondary-action" type="button" disabled={saving} onClick={() => {setDraft(null); setPending(null); setFailure(null);}}>{t("common.cancel")}</button></div>
      {draftLength > 8000 ? <p role="alert">{t("drawer.requirementsTooLong")}</p> : null}
      {failure ? <div role="alert"><p>{failure}</p><button className="personal-secondary-action" type="button" disabled={saving} onClick={() => {
        rebaseRef.current = true; setRetry(value => value + 1);
      }}>{t("drawer.requirementsReload")}</button></div> : null}
    </form> : null}
    {saved ? <p role="status">{t("drawer.requirementsSaved")}</p> : null}
  </div>;
}
