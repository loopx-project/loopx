import {useEffect, useId, useState} from "react";
import {fetchConfirmedTaskTurn} from "../../data/chat";
import {visibleAgentMessage} from "./answer-text";
import {localizedSessionStatus, useWorkspaceI18n} from "./i18n";
import {MarkdownText} from "./markdown";

type TaskReceipt = Parameters<typeof fetchConfirmedTaskTurn>[0];
type Result = Awaited<ReturnType<typeof fetchConfirmedTaskTurn>>;

export function ConfirmedTaskResult({receipt}: {receipt: TaskReceipt}) {
  const {t} = useWorkspaceI18n();
  const panelId = useId();
  const [open, setOpen] = useState(false);
  const [read, setRead] = useState<Result | Error | null>(null);
  const [retry, setRetry] = useState(0);
  const {goalId, todoId, sessionId, turnId} = receipt;
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    let next: ReturnType<typeof setTimeout> | undefined;
    async function load() {
      try {
        const result = await fetchConfirmedTaskTurn({goalId, todoId, sessionId, turnId}, controller.signal);
        if (controller.signal.aborted) return;
        setRead(result);
        if (!result.turn.terminal) next = setTimeout(load, 2_500);
      } catch {
        if (!controller.signal.aborted) setRead(new Error());
      }
    }
    void load();
    return () => { controller.abort(); clearTimeout(next); };
  }, [open, goalId, todoId, sessionId, turnId, retry]);

  const result = read && !(read instanceof Error) ? read : null;
  const messages = result?.turn.terminal
    ? result.messages.filter(message => ["agent", "assistant", "error"].includes(message.role))
      .map(message => ({...message, text: visibleAgentMessage(message.text)})).filter(message => message.text)
    : [];
  const failure = result?.turn.terminal && result.turn.error ? visibleAgentMessage(result.turn.error) : "";
  const showFailure = failure && !messages.some(message => message.role === "error" && message.text === failure);
  const retryRead = () => {setRead(null); setRetry(value => value + 1);};
  return <>
    <button aria-controls={panelId} aria-expanded={open} className="personal-primary-action"
      onClick={() => {setRead(null); setOpen(!open);}} type="button">{t(open ? "drawer.taskReceiptHideResult" : "drawer.taskReceiptShowResult")}</button>
    <section aria-label={t("drawer.taskReceiptResult")} className="personal-detail-card personal-task-receipt-result"
      id={panelId} hidden={!open}>
      <h3>{t("drawer.taskReceiptResult")}</h3>
      {read instanceof Error ? <>
        <p role="status">{t("drawer.taskReceiptMissing")}</p>
        <button className="personal-secondary-action" onClick={retryRead}
          type="button">{t("proposal.readbackRetry")}</button>
      </> : !result ? <p role="status">{t("drawer.taskReceiptReading")}</p>
        : !result.turn.terminal ? <p role="status">{t("drawer.taskReceiptPending")}</p>
        : <>
          <p role="status">{result.turn.status === "timed_out" ? t("drawer.taskReceiptTimedOut")
            : localizedSessionStatus(result.turn.status, t)}</p>
          {messages.length || showFailure ? <div className="personal-task-receipt-answer">
            {showFailure ? <MarkdownText headingLevel={4} text={failure} /> : null}
            {messages.map(message => <MarkdownText key={message.message_id} headingLevel={4} text={message.text} />)}
          </div> : <>
            <p>{t("drawer.taskReceiptNoResult")}</p>
            <button className="personal-secondary-action" onClick={retryRead}
              type="button">{t("proposal.readbackRetry")}</button>
          </>}
        </>}
    </section>
  </>;
}
