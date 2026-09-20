import { useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { Bot, Check, ChevronDown, ExternalLink, ListPlus, LoaderCircle, MessageSquareText, MoreHorizontal } from "lucide-react";

import type {
  WorkspaceDrawerSelection,
  WorkspaceGoal,
  WorkspaceModel,
  WorkspaceTimelineItem,
} from "./personal-workspace-model";
import { localizedAttentionAge, useWorkspaceI18n } from "./i18n";
import { CompletedTaskLane } from "./completed-task-lane";
import { MarkdownText } from "./markdown";

function TaskLane({
  children,
  count,
  label,
  tone,
  listView = false,
}: {
  children: ReactNode;
  count: number;
  label: string;
  tone: "attention" | "done" | "progress" | "schedule";
  listView?: boolean;
}) {
  const labelId = useId();
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const observedChildrenRef = useRef<HTMLElement[]>([]);
  const resizeObserverRef = useRef<ResizeObserver | null>(null);
  const [overflow, setOverflow] = useState({ after: false, before: false });
  const syncOverflow = useCallback(() => {
    const element = scrollRef.current;
    if (!element) return;
    const maxScrollTop = Math.max(0, element.scrollHeight - element.clientHeight);
    const next = {
      after: maxScrollTop - element.scrollTop > 1,
      before: element.scrollTop > 1,
    };
    setOverflow((current) => current.after === next.after && current.before === next.before ? current : next);
  }, []);

  useEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    const observer = new ResizeObserver(syncOverflow);
    resizeObserverRef.current = observer;
    observer.observe(element);
    element.addEventListener("scroll", syncOverflow, { passive: true });
    syncOverflow();
    return () => {
      observer.disconnect();
      resizeObserverRef.current = null;
      observedChildrenRef.current = [];
      element.removeEventListener("scroll", syncOverflow);
    };
  }, [listView, syncOverflow]);

  useEffect(() => {
    const element = scrollRef.current;
    const observer = resizeObserverRef.current;
    if (!element || !observer) return;
    for (const child of observedChildrenRef.current) observer.unobserve(child);
    const currentChildren = Array.from(element.children).filter(
      (child): child is HTMLElement => child instanceof HTMLElement,
    );
    for (const child of currentChildren) observer.observe(child);
    observedChildrenRef.current = currentChildren;
    syncOverflow();
  }, [children, count, listView, syncOverflow]);

  if (listView) {
    if (!count && tone !== "done") return null;
    return (
      <details className={`personal-task-group tone-${tone}`} open>
        <summary><ChevronDown size={16} /><strong>{label}</strong><span>{count}</span></summary>
        <div className="personal-task-list-rows">{children}</div>
      </details>
    );
  }

  return (
    <section className="personal-object-list personal-task-lane">
      <header id={labelId}>
        <strong><i aria-hidden="true" className={`personal-kanban-dot tone-${tone}`} />{label}</strong>
        <span>{count}</span>
      </header>
      <div
        aria-labelledby={labelId}
        className={`personal-task-lane-scroll${overflow.before ? " has-overflow-before" : ""}${overflow.after ? " has-overflow-after" : ""}`}
        ref={scrollRef}
        role="region"
        tabIndex={count > 0 ? 0 : -1}
      >
        {children}
      </div>
    </section>
  );
}

/**
 * Goal Tasks tab: one kanban surface that merges owner decisions ("待确认")
 * with Agent work, scheduled monitors, and completed items — mirroring the
 * board insight that confirmation is a task state, not a separate list.
 * Columns are states; cards open the same typed-preview drawer as the chat.
 */
export function GoalTasksView({
  historyEnabled = false,
  goal,
  items,
  onDraftTaskFromMessage,
  onOpenChat,
  onQuickComplete,
  quickCompletingTodoIds,
  onSelect,
  selectedTodoId = null,
  userTodos,
}: {
  historyEnabled?: boolean;
  goal: WorkspaceGoal;
  items: WorkspaceTimelineItem[];
  onDraftTaskFromMessage?: (message: string) => void;
  onOpenChat?: () => void;
  onQuickComplete?: (todo: WorkspaceGoal["agentTodos"][number] & { goalId: string; goalTitle: string; ownerLabel: string }) => void | Promise<void>;
  quickCompletingTodoIds?: ReadonlySet<string>;
  onSelect: (selection: WorkspaceDrawerSelection) => void;
  selectedTodoId?: string | null;
  userTodos: WorkspaceModel["userTodos"];
}) {
  const { t } = useWorkspaceI18n();
  const [listView, setListView] = useState(false);
  const [laneSelection, setLaneSelection] = useState({ goalId: "", laneId: "all" });
  const selectedTodoRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!selectedTodoId) return;
    const frame = window.requestAnimationFrame(() => selectedTodoRef.current?.scrollIntoView({ block: "nearest", inline: "nearest" }));
    return () => window.cancelAnimationFrame(frame);
  }, [selectedTodoId]);
  const attentionItems = userTodos
    .filter((todo) => todo.goalId === goal.goalId)
    .map((todo) => ({ ...todo, goalTitle: goal.title }));
  const priorityRank = (todo: WorkspaceGoal["agentTodos"][number]) =>
    todo.priority === "P0" ? 0 : todo.priority === "P1" ? 1 : todo.priority === "P2" ? 2 : 3;
  const agentLanes = useMemo(() => {
    const lanes = new Map((goal.agentLanes ?? []).map((lane) => [lane.agentId, lane]));
    for (const todo of goal.agentTodos) {
      if (todo.claimedBy && !lanes.has(todo.claimedBy)) {
        lanes.set(todo.claimedBy, { agentId: todo.claimedBy, label: todo.claimedBy });
      }
    }
    return [...lanes.values()];
  }, [goal.agentLanes, goal.agentTodos]);
  const selectedLaneId = laneSelection.goalId === goal.goalId
    && agentLanes.some((lane) => lane.agentId === laneSelection.laneId)
    ? laneSelection.laneId
    : "all";
  const laneMatches = (agentId?: string | null) => selectedLaneId === "all" || agentId === selectedLaneId;
  const openAgentTodos = goal.agentTodos
    .filter((todo) => todo.taskClass !== "continuous_monitor" && !todo.done)
    .filter((todo) => laneMatches(todo.claimedBy))
    .sort((left, right) => priorityRank(left) - priorityRank(right));
  const doneAgentTodos = goal.agentTodos
    .filter((todo) => todo.taskClass !== "continuous_monitor" && todo.done)
    .filter((todo) => laneMatches(todo.claimedBy));
  const scheduleItems = items.filter((item): item is Extract<WorkspaceTimelineItem, { kind: "schedule" }> =>
    item.kind === "schedule" && laneMatches(item.schedule.agentId));
  const executionRuns = items.filter((item): item is Extract<WorkspaceTimelineItem, { kind: "run" }> =>
    item.kind === "run" && Boolean(item.run.todoId) && laneMatches(item.run.agentId));
  const isEmpty = !attentionItems.length && !openAgentTodos.length && !doneAgentTodos.length && !scheduleItems.length;
  const conversation = items.filter((item): item is Extract<WorkspaceTimelineItem, { kind: "message" }> =>
    item.kind === "message" && (item.message.role === "user" || item.message.role === "assistant"));
  const latestUserIndex = conversation.reduce((latest, item, index) => item.message.role === "user" ? index : latest, -1);
  const latestUserMessage = latestUserIndex >= 0 ? conversation[latestUserIndex]?.message : null;
  const latestReply = latestUserIndex >= 0
    ? conversation.slice(latestUserIndex + 1).reverse().find((item) => item.message.role === "assistant")?.message
    : null;
  const replyPending = latestUserIndex >= 0
    && conversation.slice(latestUserIndex + 1).some((item) => item.message.role === "assistant" && item.message.pending);

  return (
    <section aria-label={t("header.tasks")} className={`personal-task-board${listView ? " is-list-view" : ""}`}>
      <header className="personal-task-view-toolbar">
        <div><strong>{t("header.tasks")}</strong></div>
        <div className="personal-task-view-switch" role="group" aria-label={t("tasks.viewLabel")}>
          <button type="button" aria-pressed={listView} onClick={() => setListView(true)}>{t("tasks.listView")}</button>
          <button type="button" aria-pressed={!listView} onClick={() => setListView(false)}>{t("tasks.boardView")}</button>
        </div>
      </header>
      {agentLanes.length > 1 ? (
        <section aria-label={t("tasks.agentLaneFilter")} className="personal-task-lane-filter">
          <div>
            <Bot size={15} />
            <span><strong>{t("tasks.agentLane")}</strong></span>
          </div>
          <label>
            <span className="sr-only">{t("tasks.agentLaneFilter")}</span>
            <select
              aria-label={t("tasks.agentLaneFilter")}
              onChange={(event) => setLaneSelection({ goalId: goal.goalId, laneId: event.target.value })}
              value={selectedLaneId}
            >
              <option value="all">{t("tasks.allAgentLanes", { count: agentLanes.length })}</option>
              {agentLanes.map((lane) => <option key={lane.agentId} value={lane.agentId}>{lane.label}</option>)}
            </select>
            <ChevronDown aria-hidden size={14} />
          </label>
        </section>
      ) : null}
      {latestUserMessage ? (
        <section aria-label={t("tasks.chatRecent")} className="personal-task-chat-receipt">
          <span className="personal-task-chat-icon"><MessageSquareText size={18} /></span>
          <div>
            <header><strong>{replyPending ? t("tasks.chatPending") : latestReply ? t("tasks.chatAgentReplied") : t("tasks.chatRecent")}</strong><small>{latestReply?.returnDelivery ? t("tasks.chatReturn") : latestReply?.agentLabel}</small></header>
            <div className="personal-task-reply-preview"><MarkdownText text={replyPending ? t("tasks.chatPendingDescription") : (latestReply?.text ?? latestUserMessage.text).trim().split(/\r?\n/, 1)[0]} /></div>
          </div>
          <footer>
            <button onClick={onOpenChat} type="button"><MessageSquareText size={14} />{t("tasks.chatViewReply")}</button>
            {latestReply && !replyPending && onDraftTaskFromMessage ? <button onClick={() => onDraftTaskFromMessage(latestReply.text)} type="button"><ListPlus size={14} />{t("tasks.convertToTask")}</button> : null}
          </footer>
        </section>
      ) : null}
      <div className={listView ? "personal-task-grouped-list" : "personal-task-kanban"}>
      <TaskLane listView={listView} count={attentionItems.length} label={t("timeline.waitingConfirmation")} tone="attention">
        {attentionItems.map((attention) => {
          const age = localizedAttentionAge(attention.updatedAt, t);
          return (
            <button key={attention.todoId} onClick={() => onSelect({ item: attention, kind: "attention" })} type="button">
              <span aria-hidden="true" className="is-attention">!</span>
              <strong>{attention.text}</strong>
              <small>
                <span className={`personal-row-status ${attention.blocking ? "is-blocking" : "is-pending"}`}>{attention.blocking ? t("tasks.blocked") : t("tasks.pending")}</span>
                {age ? <span className="personal-task-age">{t("tasks.waitingAge", { age })}</span> : null}
              </small>
            </button>
          );
        })}
        {!attentionItems.length ? <p className="personal-task-empty">{t("tasks.emptyConfirm")}</p> : null}
      </TaskLane>
      <TaskLane listView={listView} count={openAgentTodos.length} label={t("tasks.pendingAndRunning")} tone="progress">
        {openAgentTodos.map((todo) => {
          const enriched = { ...todo, goalId: goal.goalId, goalTitle: goal.title, ownerLabel: todo.claimedBy ?? goal.agentLabel ?? goal.agentId };
          const execution = executionRuns.find((item) => item.run.todoId === todo.todoId)?.run;
          return (
            <div className={`personal-task-card${execution ? " has-session" : ""}${selectedTodoId === todo.todoId ? " is-selected" : ""}`} key={todo.todoId} ref={selectedTodoId === todo.todoId ? (element) => { selectedTodoRef.current = element; } : undefined}>
              <button aria-pressed={selectedTodoId === todo.todoId} onClick={() => onSelect({ item: enriched, kind: "todo" })} type="button">
                <span>○</span><strong>{todo.text}</strong>
                <small>
                  {todo.priority ? <span className={`personal-priority-badge is-${todo.priority.toLowerCase()}`}>{todo.priority}</span> : null}
                  {todo.status === "blocked" ? <span className="personal-priority-badge is-blocked">{t("tasks.blocked")}</span> : null}
                  {execution ? <span className="personal-task-session-status">{execution.status === "running" || execution.status === "queued" ? t("runs.running") : execution.status === "failed" ? t("tasks.sessionError") : t("common.waiting")}</span> : null}
                  {todo.status === "deferred" ? <span className="personal-task-session-status">{t("drawer.taskStatusDeferred")}</span> : !execution ? <span className="personal-task-session-status">{t("tasks.waiting")}</span> : null}
                  {todo.claimedBy ?? goal.agentLabel ?? goal.agentId}
                </small>
              </button>
              <div className="personal-task-card-actions">
                {execution ? <button className="personal-task-session-link" aria-label={t("tasks.openExecution", { name: todo.text })} onClick={() => onSelect({ item: execution, kind: "run" })} title={execution.status === "completed" ? t("tasks.viewResult") : t("tasks.viewExecution")} type="button"><ExternalLink size={14} /><span>{execution.status === "completed" ? t("tasks.viewResult") : t("tasks.viewExecution")}</span></button> : null}
                {onQuickComplete ? (
                  <button
                    aria-busy={quickCompletingTodoIds?.has(todo.todoId) || undefined}
                    aria-label={t("tasks.markComplete", { name: todo.text })}
                    disabled={quickCompletingTodoIds?.has(todo.todoId)}
                    onClick={() => void onQuickComplete(enriched)}
                    title={t("tasks.completed")}
                    type="button"
                  >
                    {quickCompletingTodoIds?.has(todo.todoId)
                      ? <LoaderCircle className="personal-spin" size={14} />
                      : <Check size={14} />}
                  </button>
                ) : null}
                <button aria-label={t("tasks.moreActions", { name: todo.text })} onClick={() => onSelect({ item: enriched, kind: "todo" })} title={t("common.actions")} type="button"><MoreHorizontal size={14} /></button>
              </div>
            </div>
          );
        })}
        {!openAgentTodos.length ? <p className="personal-task-empty">{t("tasks.emptyRunning")}</p> : null}
      </TaskLane>
      <TaskLane listView={listView} count={scheduleItems.length} label={t("tasks.scheduled")} tone="schedule">
        {scheduleItems.map((item) => (
          <button key={item.id} onClick={() => onSelect({ item: item.schedule, kind: "schedule" })} type="button">
            <span>◷</span><strong>{item.schedule.label}</strong><small>{item.schedule.status === "paused" ? t("schedule.paused") : t("schedule.active")}</small>
          </button>
        ))}
        {!scheduleItems.length ? <p className="personal-task-empty">{t("tasks.emptySchedules")}</p> : null}
      </TaskLane>
      <CompletedTaskLane key={`${goal.goalId}:${selectedLaneId}:${historyEnabled}`} goal={goal} agentId={selectedLaneId} seed={doneAgentTodos} enabled={historyEnabled} listView={listView} onSelect={onSelect} />
      </div>
      {isEmpty ? (
        <p className="personal-task-empty">
          {t("tasks.emptyGoal")}
        </p>
      ) : null}
    </section>
  );
}
