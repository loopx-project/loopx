import {useEffect, useState} from "react";
import {readTodoRequest} from "../../data/chat";
import {useWorkspaceI18n} from "./i18n";
import type {WorkspaceTodo} from "./personal-workspace-model";

// Per-open read state, never a second Task store. A different selection or
// source invalidates both the in-flight read and its earlier successful body.
type RequestRead = {identity: string} & (
  | {phase: "loading" | "error"; text?: never}
  | {phase: "ready"; text: string}
);

export function TaskRequest({todo, local}: {todo: WorkspaceTodo; local: boolean}) {
  const {t} = useWorkspaceI18n();
  const sourceId = todo.sourceTodoId;
  const identity = JSON.stringify([local, todo.goalId, sourceId, todo.requestText, todo.status]);
  const [read, setRead] = useState<RequestRead | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!local || !sourceId) return;
    const controller = new AbortController();
    setRead({identity, phase: "loading"});
    void readTodoRequest(todo.goalId, sourceId, controller.signal).then(result => {
      if (!controller.signal.aborted) setRead({identity, phase: "ready", text: result.text});
    }).catch(() => {
      if (!controller.signal.aborted) setRead({identity, phase: "error"});
    });
    return () => controller.abort();
  }, [identity, local, retry, todo.goalId, sourceId]);
  const current = local && read?.identity === identity ? read : null;
  return <div className="personal-task-request">
    <h3>{current?.phase === "ready" ? current.text : todo.requestText ?? todo.text}</h3>
    {current?.phase !== "ready" ? <div className="personal-proposal-explainer" role={current?.phase === "error" ? "alert" : "status"}>
      <span>{t(!local ? "drawer.requestLocalOnly" : !sourceId ? "drawer.requestUnidentified" : current?.phase === "error" ? "drawer.requestError" : "drawer.requestLoading")}</span>
      {current?.phase === "error" ? <button className="personal-secondary-action" type="button" onClick={() => setRetry(value => value + 1)}>{t("drawer.requestRetry")}</button> : null}
    </div> : null}
  </div>;
}
