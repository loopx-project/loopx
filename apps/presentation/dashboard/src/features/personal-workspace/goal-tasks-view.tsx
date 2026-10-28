import {Bot, Check, ChevronDown, CircleHelp, ExternalLink, ListPlus, LoaderCircle, MessageSquareText, MoreHorizontal, Search} from "lucide-react";
import {ContextHelp} from "./context-help";
import type {GoalWorkspaceDigest} from "../../../../../../loopx/control_plane/presentation/goal_workspace_digest.js";
import {useGoalWorkspaceDigest} from "./goal-masthead";
import {goalWorkClassLabel} from "./goal-workspace-work";
import { useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";

import type {
  WorkspaceDrawerSelection,
  WorkspaceGoal,
  WorkspaceModel,
  WorkspaceTimelineItem,
} from "./personal-workspace-model";
import { localizedAttentionAge, localizedSessionStatus, useWorkspaceI18n } from "./i18n";
import { CompletedTaskLane } from "./completed-task-lane";
import { MarkdownText } from "./markdown";
import { isGoalWorkTodo } from "../../../../../../loopx/control_plane/presentation/goal_workspace_digest.js";
import {useGoalWorkBrowse, type GoalWorkBrowseState} from "./goal-work-browse";
import "./goal-tasks-view.css";

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
  digest: sharedDigest,
  historyEnabled = false,
  historyActive = true,
  historyRefreshRevision = 0,
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
  digest?: GoalWorkspaceDigest;
  historyEnabled?: boolean;
  historyActive?: boolean;
  historyRefreshRevision?: number;
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
  const { t, locale } = useWorkspaceI18n();
  const zh = locale === "zh-CN";
  const [browse, changeBrowse] = useGoalWorkBrowse();
  const listView = browse.view === "list";
  const selectedLaneId = browse.owner;
  const {query, filter} = browse;
  const queryMatches = (text: string) => text.toLocaleLowerCase(locale).includes(query.trim().toLocaleLowerCase(locale));
  const boardRef = useRef<HTMLElement>(null);
  const [boardVisible, setBoardVisible] = useState(false);
  useEffect(() => {
    const node = boardRef.current;
    if (!node) return;
    const measure = () => setBoardVisible(node.getClientRects().length > 0);
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    measure();
    return () => observer.disconnect();
  }, []);
  const selectedTodoRef = useRef<HTMLElement | null>(null);
  function selectFromButton(button: HTMLButtonElement, next: WorkspaceDrawerSelection) {
    // macOS pointer activation need not focus buttons. The drawer uses the
    // active element to return to the originating task when it closes.
    button.focus({ preventScroll: true });
    onSelect(next);
  }
  useEffect(() => {
    if (!selectedTodoId) return;
    const frame = window.requestAnimationFrame(() => selectedTodoRef.current?.scrollIntoView({ block: "nearest", inline: "nearest" }));
    return () => window.cancelAnimationFrame(frame);
  }, [selectedTodoId, listView, selectedLaneId, query, filter]);
  const attentionItems = userTodos
    .filter((todo) => todo.goalId === goal.goalId)
    .filter(todo => (filter === "all" || filter === "needsYou") && queryMatches(todo.text))
    .map((todo) => ({ ...todo, goalTitle: goal.title }));
  const priorityRank = (todo: WorkspaceGoal["agentTodos"][number]) =>
    todo.priority === "P0" ? 0 : todo.priority === "P1" ? 1 : todo.priority === "P2" ? 2 : todo.priority === "P3" ? 3 : todo.priority === "P4" ? 4 : 50;
  const agentLanes = useMemo(() => {
    const lanes = new Map((goal.agentLanes ?? []).map((lane) => [lane.agentId, lane]));
    for (const todo of goal.agentTodos) {
      if (todo.claimedBy && !lanes.has(todo.claimedBy)) {
        lanes.set(todo.claimedBy, { agentId: todo.claimedBy, label: todo.claimedBy });
      }
    }
    return [...lanes.values()];
  }, [goal.agentLanes, goal.agentTodos]);
  const laneMatches = (agentId?: string | null) => selectedLaneId === "all" || (agentId ?? "") === selectedLaneId;
  const localDigest = useGoalWorkspaceDigest(goal, userTodos, items);
  const workDigest = sharedDigest ?? localDigest;
  const displayClasses = new Map(workDigest.work.rows.map(row => [row.todoId, row.displayClass]));
  const filteredIds = filter === "all" ? null : new Set(workDigest.work.rail[filter]);
  const taskMatches = (todo: WorkspaceGoal["agentTodos"][number]) => laneMatches(todo.claimedBy)
    && queryMatches(todo.text) && (!filteredIds || filteredIds.has(todo.todoId));
  const selectedTodo = goal.agentTodos.find(todo => todo.todoId === selectedTodoId);
  const selectedHidden = selectedTodo && !taskMatches(selectedTodo);
  const openAgentTodos = goal.agentTodos
    .filter((todo) => isGoalWorkTodo(todo) && !todo.done)
    .filter(taskMatches)
    .sort((left, right) => priorityRank(left) - priorityRank(right));
  const doneAgentTodos = goal.agentTodos
    .filter((todo) => isGoalWorkTodo(todo) && todo.done)
    .filter((todo) => laneMatches(todo.claimedBy));
  const scheduleItems = items.filter((item): item is Extract<WorkspaceTimelineItem, { kind: "schedule" }> =>
    item.kind === "schedule" && item.schedule.goalId === goal.goalId && filter === "all"
    && laneMatches(item.schedule.agentId) && queryMatches(item.schedule.label));
  const executionRuns = items.filter((item): item is Extract<WorkspaceTimelineItem, { kind: "run" }> =>
    item.kind === "run" && item.run.goalId === goal.goalId && Boolean(item.run.todoId) && laneMatches(item.run.agentId));
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
  const filterLabels: Record<GoalWorkBrowseState["filter"], string> = zh
    ? {all: "全部", needsYou: "等待决定", open: "未完成", blocked: "受阻与延后", done: "已完成"}
    : {all: "All", needsYou: "Needs a decision", open: "Open", blocked: "Blocked and deferred", done: "Done"};

  return (
    <section aria-label={t("header.tasks")} className={`personal-task-board${listView ? " is-list-view" : ""}`} tabIndex={-1} ref={boardRef}>
      <header className="personal-task-view-toolbar">
        <div><strong>{t("header.tasks")}</strong></div>
        <div className="personal-task-view-switch" role="group" aria-label={t("tasks.viewLabel")}>
          <button type="button" aria-pressed={listView} onClick={() => changeBrowse({view: "list"})}>{t("tasks.listView")}</button>
          <button type="button" aria-pressed={!listView} onClick={() => changeBrowse({view: "board"})}>{t("tasks.boardView")}</button>
        </div>
      </header>
      <div className="personal-task-browse-controls">
        <label className="personal-task-search"><Search size={16} aria-hidden/><input type="search" maxLength={256} value={query}
          aria-label={zh ? "搜索当前已列出的任务" : "Search listed tasks"} placeholder={zh ? "搜索任务" : "Search tasks"}
          onChange={event => changeBrowse({query: event.target.value}, true)}/></label>
        <div className="personal-task-filters" role="group" aria-label={zh ? "任务状态筛选" : "Task state filters"}>
          {(Object.keys(filterLabels) as GoalWorkBrowseState["filter"][]).map(value => <button key={value} type="button" aria-pressed={filter === value} onClick={() => changeBrowse({filter: value})}>{filterLabels[value]}</button>)}
          {query || selectedLaneId !== "all" || filter !== "all" ? <button type="button" onClick={() => changeBrowse({query: "", owner: "all", filter: "all"})}>{zh ? "清除筛选" : "Clear filters"}</button> : null}
        </div>
      </div>
      {selectedHidden ? <p className="personal-task-selection-note" role="status">{zh ? "所选任务不在当前筛选范围中，详情仍保留。" : "The selected task is outside these filters. Its details are retained."}
        <button type="button" onClick={() => changeBrowse({query: "", owner: "all", filter: "all"})}>{zh ? "显示所选任务" : "Show selected task"}</button></p> : null}
      {agentLanes.length > 1 || selectedLaneId !== "all" || goal.agentTodos.some(todo => !todo.claimedBy) ? (
        <section aria-label={t("tasks.agentLaneFilter")} className="personal-task-lane-filter">
          <div>
            <Bot size={15} />
            <span><strong>{t("tasks.agentLane")}</strong></span>
          </div>
          <label>
            <span className="sr-only">{t("tasks.agentLaneFilter")}</span>
            <select
              aria-label={t("tasks.agentLaneFilter")}
              onChange={(event) => changeBrowse({owner: event.target.value})}
              value={selectedLaneId}
            >
              <option value="all">{t("tasks.allAgentLanes", { count: agentLanes.length })}</option>
              <option value="">{t("drawer.notAssigned")}</option>
              {agentLanes.map((lane) => <option key={lane.agentId} value={lane.agentId}>{lane.label}</option>)}
              {selectedLaneId && selectedLaneId !== "all" && !agentLanes.some(lane => lane.agentId === selectedLaneId) ? <option value={selectedLaneId}>{selectedLaneId} · {zh ? "当前未列出" : "Not currently listed"}</option> : null}
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
      {filter === "all" || filter === "needsYou" ? <TaskLane listView={listView} count={attentionItems.length} label={t("timeline.waitingConfirmation")} tone="attention">
        {attentionItems.map((attention) => {
          const age = localizedAttentionAge(attention.updatedAt, t);
          return (
            <button key={attention.todoId} onClick={(event) => selectFromButton(event.currentTarget, { item: attention, kind: "attention" })} type="button">
              <span aria-hidden="true" className="is-attention">!</span>
              <strong>{attention.text}</strong>
              <small>
                <span className={`personal-row-status ${attention.blocking ? "is-blocking" : "is-pending"}`}>{attention.blocking ? t("tasks.blocked") : t("tasks.pending")}</span>
                {age ? <span className="personal-task-age">{t("tasks.waitingAge", { age })}</span> : null}
              </small>
            </button>
          );
        })}
        {!attentionItems.length ? <ContextHelp label={locale === "zh-CN" ? "暂无待确认任务" : "No tasks awaiting confirmation"} icon={CircleHelp}><p className="personal-task-empty">{t("tasks.emptyConfirm")}</p></ContextHelp> : null}
      </TaskLane> : null}
      {filter !== "done" ? <TaskLane listView={listView} count={openAgentTodos.length} label={t("tasks.pendingAndRunning")} tone="progress">
        {openAgentTodos.map((todo) => {
          const enriched = { ...todo, goalId: goal.goalId, goalTitle: goal.title, ownerLabel: todo.claimedBy ?? t("drawer.notAssigned") };
          const execution = executionRuns.find((item) => item.run.todoId === todo.todoId)?.run;
          const state = displayClasses.get(todo.todoId);
          const reason = state === "blocked" ? todo.blockedReason : state === "deferred" ? todo.resumeWhen : null;
          return (
            <div className={`personal-task-card${execution ? " has-session" : ""}${selectedTodoId === todo.todoId ? " is-selected" : ""}`} data-work-class={displayClasses.get(todo.todoId)} key={todo.todoId} ref={selectedTodoId === todo.todoId ? (element) => { selectedTodoRef.current = element; } : undefined}>
              <button aria-pressed={selectedTodoId === todo.todoId} onClick={(event) => selectFromButton(event.currentTarget, { item: enriched, kind: "todo" })} type="button">
                <span>○</span><strong>{todo.text}</strong>
                <small>
                  {todo.priority ? <span className={`personal-priority-badge is-${todo.priority.toLowerCase()}`}>{todo.priority}</span> : null}
                  <span className="personal-task-session-status">{goalWorkClassLabel(displayClasses.get(todo.todoId) ?? "open", locale === "zh-CN")}</span>
                  {execution && execution.status !== "running" ? <span className="personal-task-session-status">{localizedSessionStatus(execution.status, t)}</span> : null}
                  <span>{enriched.ownerLabel}</span>
                </small>
                {reason ? <span className="personal-task-blocker">{reason}</span> : null}
              </button>
              <div className="personal-task-card-actions">
                {execution ? <button className="personal-task-session-link" aria-label={t("tasks.openExecution", { name: todo.text })} onClick={(event) => selectFromButton(event.currentTarget, { item: execution, kind: "run" })} title={execution.status === "completed" ? t("tasks.viewResult") : t("tasks.viewExecution")} type="button"><ExternalLink size={14} /><span>{execution.status === "completed" ? t("tasks.viewResult") : t("tasks.viewExecution")}</span></button> : null}
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
                <button aria-label={t("tasks.moreActions", { name: todo.text })} onClick={(event) => selectFromButton(event.currentTarget, { item: enriched, kind: "todo" })} title={t("common.actions")} type="button"><MoreHorizontal size={14} /></button>
              </div>
            </div>
          );
        })}
        {!openAgentTodos.length ? <ContextHelp label={locale === "zh-CN" ? "暂无运行记录" : "No run records"} icon={CircleHelp}><p className="personal-task-empty">{t("tasks.emptyRunning")}</p></ContextHelp> : null}
      </TaskLane> : null}
      {filter === "all" ? <TaskLane listView={listView} count={scheduleItems.length} label={t("tasks.scheduled")} tone="schedule">
        {scheduleItems.map((item) => (
          <button key={item.id} onClick={(event) => selectFromButton(event.currentTarget, { item: item.schedule, kind: "schedule" })} type="button">
            <span>◷</span><strong>{item.schedule.label}</strong><small>{item.schedule.status === "paused" ? t("schedule.paused") : t("schedule.active")}</small>
          </button>
        ))}
        {!scheduleItems.length ? <ContextHelp label={locale === "zh-CN" ? "暂无定时任务" : "No schedules"} icon={CircleHelp}><p className="personal-task-empty">{t("tasks.emptySchedules")}</p></ContextHelp> : null}
      </TaskLane> : null}
      {filter === "all" || filter === "done" ? <CompletedTaskLane searchable key={`${goal.goalId}:${selectedLaneId}`} goal={goal} agentId={selectedLaneId || "all"} unassignedOnly={selectedLaneId === ""} seed={doneAgentTodos} query={query} refreshRevision={historyRefreshRevision} enabled={historyEnabled && historyActive && boardVisible} listView={listView} onSelect={onSelect} selectedTodoId={selectedTodoId} /> : null}
      </div>
      {isEmpty ? (
        <ContextHelp label={locale === "zh-CN" ? "当前任务范围" : "Current task scope"} icon={CircleHelp}><p className="personal-task-empty">
          {t("tasks.emptyGoal")}
        </p></ContextHelp>
      ) : null}
    </section>
  );
}
