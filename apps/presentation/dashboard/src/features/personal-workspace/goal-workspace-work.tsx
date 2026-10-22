import {useEffect, useMemo, useRef, useState, type ReactNode} from "react";
import {Circle, CircleAlert, CircleCheck, Clock, Hand, Play, Plus, Search} from "lucide-react";
import type {GoalWorkDisplayClass, GoalWorkspaceDigest} from "../../../../../../loopx/control_plane/presentation/goal_workspace_digest.js";
import type {WorkspaceActionPreview, WorkspaceActionPreviewRequest, WorkspaceDrawerSelection, WorkspaceGoal, WorkspaceModel, WorkspaceTimelineItem} from "./personal-workspace-model";
import {localizedSessionStatus, useWorkspaceI18n} from "./i18n";
import {CompletedTaskLane} from "./completed-task-lane";
import {boundedLength, todoCreateRequest, todoTextLimit} from "./workspace-action-form";
import "./goal-workspace-work.css";

const glyphs = {running_observed: Play, needs_you: Hand, open: Circle, waiting_dependency: Clock, blocked: CircleAlert, deferred: Clock, done: CircleCheck};
export function goalWorkClassLabel(state: GoalWorkDisplayClass, zh: boolean) {
  return (zh ? {running_observed: "执行中", needs_you: "等待决定", open: "待处理", waiting_dependency: "等待前置任务", blocked: "受阻", deferred: "已延期", done: "已完成"}
    : {running_observed: "Running", needs_you: "Needs a decision", open: "Open", waiting_dependency: "Waiting on work", blocked: "Blocked", deferred: "Deferred", done: "Done"})[state];
}

function AddTask({goal, proposals, onPreview, onSelect}: {
  goal: WorkspaceGoal; proposals: Record<string, WorkspaceActionPreview>;
  onPreview: (request: WorkspaceActionPreviewRequest) => Promise<WorkspaceActionPreview>;
  onSelect: (selection: WorkspaceDrawerSelection) => void;
}) {
  const {locale} = useWorkspaceI18n();
  const zh = locale === "zh-CN";
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const [reviewed, setReviewed] = useState<{text: string; id: string} | null>(null);
  const proposal = reviewed ? proposals[reviewed.id] : undefined;
  useEffect(() => {
    if (!reviewed || proposal) return;
    const successor = Object.values(proposals).find(item => item.regeneratedFrom === reviewed.id);
    if (successor) setReviewed({...reviewed, id: successor.previewId});
  }, [reviewed, proposal, proposals]);
  useEffect(() => {
    if (proposal?.status !== "applied") return;
    setText(current => current === reviewed?.text ? "" : current);
    setReviewed(null);
  }, [proposal?.status, reviewed]);
  const tooLong = boundedLength(text) > todoTextLimit;
  return <div className="goal-inline-create">
    <button aria-expanded={open} type="button" onClick={() => setOpen(value => !value)}><Plus size={15} aria-hidden/>{zh ? "添加任务" : "Add task"}</button>
    {open ? <form onSubmit={async event => {
      event.preventDefault();
      if (submitting.current || !text.trim() || tooLong) return;
      if (proposal && reviewed?.text === text) {onSelect({kind: "proposal", item: proposal}); return;}
      submitting.current = true;
      setBusy(true); setError("");
      try {
        const result = await onPreview(todoCreateRequest(text, goal.goalId, zh ? "创建任务" : "Create task"));
        setReviewed({text, id: result.previewId});
      } catch (failure) {setError(failure instanceof Error ? failure.message : String(failure));}
      finally {submitting.current = false; setBusy(false);}
    }}>
      <label>{zh ? "任务内容" : "Task"}<textarea autoFocus aria-label={zh ? "任务内容" : "Task"} value={text} onChange={event => setText(event.target.value)} disabled={busy}/></label>
      {tooLong ? <p role="status">{zh ? `请将任务精简至 ${todoTextLimit} 字以内。` : `Shorten the task to ${todoTextLimit} characters.`}</p> : null}
      {error ? <p role="alert">{error}</p> : null}
      {proposal?.status === "error" || proposal?.status === "stale" ? <p role="status">{zh ? "任务尚未确认创建，草稿已保留。" : "Creation is not confirmed; your draft is retained."}</p> : null}
      <button disabled={busy || !text.trim() || tooLong} type="submit">{busy ? (zh ? "正在检查…" : "Reviewing…") : (zh ? "检查任务" : "Review task")}</button>
    </form> : null}
  </div>;
}

export function GoalWorkspaceWork({goal, digest, items, userTodos, selectedTodoId, onSelect, board, map, results,
  readOnly, active, historyRefreshRevision, proposals, onPreview, railOpen, onCloseRail, onOpenAgentConversation}: {
  onOpenAgentConversation?: (agentId: string) => void;
  railOpen: boolean; onCloseRail: () => void;
  goal: WorkspaceGoal; digest: GoalWorkspaceDigest; items: WorkspaceTimelineItem[]; userTodos: WorkspaceModel["userTodos"];
  selectedTodoId: string | null; onSelect: (selection: WorkspaceDrawerSelection) => void;
  board: ReactNode; map: ReactNode; results: ReactNode; readOnly: boolean; active: boolean; historyRefreshRevision: number;
  proposals: Record<string, WorkspaceActionPreview>; onPreview: (request: WorkspaceActionPreviewRequest) => Promise<WorkspaceActionPreview>;
}) {
  const {locale, t} = useWorkspaceI18n();
  const zh = locale === "zh-CN";
  const [compactRail, setCompactRail] = useState(() => typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(max-width: 1199px)").matches);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 1199px)");
    const update = () => setCompactRail(media.matches);
    update(); media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    const node = dialog.current;
    if (compactRail && railOpen && active) node?.showModal(); else node?.close();
    return () => node?.close();
  }, [compactRail, railOpen, active]);
  const select = (selection: WorkspaceDrawerSelection) => {
    if (compactRail && railOpen) {dialog.current?.close(); onCloseRail();}
    onSelect(selection);
  };
  const [lens, setLens] = useState<"owner" | "board" | "map">("owner");
  const [visited, setVisited] = useState(() => new Set(["owner"]));
  const [railTab, setRailTab] = useState<"work" | "activity">("work");
  const [filter, setFilter] = useState<"all" | "needsYou" | "open" | "blocked" | "done">("all");
  const [query, setQuery] = useState("");
  const todos = useMemo(() => new Map(goal.agentTodos.map(todo => [todo.todoId, todo])), [goal.agentTodos]);
  const rows = useMemo(() => new Map(digest.work.rows.map(row => [row.todoId, row])), [digest]);
  const attention = userTodos.filter(todo => todo.goalId === goal.goalId);
  const ownerName = (id: string | null) => id === null ? (zh ? "未分配" : "Unassigned") : goal.agentLanes?.find(lane => lane.agentId === id)?.label ?? id;
  const matches = (id: string) => (todos.get(id)?.text ?? "").toLocaleLowerCase(locale).includes(query.trim().toLocaleLowerCase(locale));
  function task(id: string, compact = false) {
    const todo = todos.get(id), row = rows.get(id);
    if (!todo || !row) return null;
    const Glyph = glyphs[row.displayClass];
    const dependency = row.prerequisiteIds.map(ref => todos.get(ref)?.text ?? attention.find(item => item.todoId === ref)?.text).find(Boolean);
    const run = items.find(item => item.kind === "run" && item.run.todoId === id && item.run.goalId === goal.goalId);
    const reason = row.displayClass === "blocked" ? todo.blockedReason
      : row.displayClass === "deferred" ? todo.resumeWhen
        : row.displayClass === "waiting_dependency" || row.displayClass === "needs_you" ? (dependency ? `${zh ? "需要" : "Requires"}: ${dependency}` : null)
          : row.displayClass === "running_observed" && run?.kind === "run" ? run.run.title : null;
    return <button className={`goal-work-row${compact ? " is-compact" : ""}`} data-todo-id={id} data-work-class={row.displayClass}
      aria-pressed={selectedTodoId === id} key={id} type="button" onClick={event => {
        event.currentTarget.focus({preventScroll: true});
        select({kind: "todo", item: {...todo, goalId: goal.goalId, goalTitle: goal.title, ownerLabel: todo.claimedBy}});
      }}>
      <Glyph size={16} aria-hidden/>
      <span className="goal-work-row-text"><strong>{todo.text}</strong>{reason ? <small>{reason}</small> : null}</span>
      <span className={`goal-work-badge is-${row.displayClass}`}>{goalWorkClassLabel(row.displayClass, zh)}</span>
      {!compact ? <time dateTime={todo.completedAt ?? undefined}>{todo.completedAt ? new Date(todo.completedAt).toLocaleDateString(locale) : "—"}</time> : null}
    </button>;
  }
  const quietWork = digest.work.coverage === "complete" && digest.work.total === 0
    && digest.needsYou.availability === "ok" && digest.needsYou.blocking === 0
    && digest.needsYou.optional === 0 && !attention.length;
  const sectionLabels = {needsYou: zh ? "等待决定" : "Needs a decision", open: zh ? "未完成" : "Open", blocked: zh ? "受阻与延后" : "Blocked and deferred", done: zh ? "已完成" : "Done"};
  const activity = items.filter(item => (item.kind === "run" && item.run.goalId === goal.goalId && ["completed", "failed", "interrupted"].includes(item.run.status))
    || (item.kind === "output" && item.output.goalId === goal.goalId)
    || (item.kind === "proposal" && item.proposal.goalId === goal.goalId && item.proposal.status === "applied"
      && item.proposal.actionKind === "gate.resolve"));
  const rail = <aside className="goal-all-work" aria-label={zh ? "全部工作" : "All work"}>
      <div role="group" aria-label={zh ? "工作侧栏" : "Work rail"}>{(["work", "activity"] as const).map(value => <button type="button" key={value} aria-pressed={railTab === value} onClick={() => setRailTab(value)}>{value === "work" ? (zh ? "全部工作" : "All work") : (zh ? "活动" : "Activity")}</button>)}</div>
      {railTab === "work" ? <>
        <label className="goal-work-search"><Search size={14} aria-hidden/><input aria-label={zh ? "搜索当前已列出的任务" : "Search listed tasks"} value={query} onChange={event => setQuery(event.target.value)} placeholder={zh ? "搜索任务" : "Search tasks"}/></label>
        <div className="goal-work-filters" role="group" aria-label={zh ? "工作筛选" : "Work filters"}>
          <button type="button" aria-pressed={filter === "all"} onClick={() => setFilter("all")}>{zh ? "全部" : "All"} · {digest.work.total ?? "—"}</button>
          {(["needsYou", "open", "blocked", "done"] as const).map(section => <button key={section} type="button" aria-pressed={filter === section} onClick={() => setFilter(section)}>{sectionLabels[section]} · {section === "done" ? digest.work.done ?? "—" : digest.work.rail[section].length}</button>)}
        </div>
        {filter === "all" && attention.length ? <section className="goal-rail-section"><h3>{zh ? "需要你" : "Needs you"}</h3>{attention.filter(item => item.text.toLocaleLowerCase(locale).includes(query.trim().toLocaleLowerCase(locale))).map(item => <button type="button" key={item.todoId} onClick={() => select({kind: "attention", item})}><Hand size={14} aria-hidden/><span>{item.text}</span></button>)}</section> : null}
        {(["needsYou", "open", "blocked"] as const).filter(section => filter === "all" || filter === section).map(section => <section className="goal-rail-section" key={section}>
          <h3>{sectionLabels[section]}</h3>{digest.work.rail[section].filter(matches).map(id => task(id, true))}
        </section>)}
        {filter === "all" || filter === "done" ? <CompletedTaskLane key={`${goal.goalId}:${readOnly}`} goal={goal} agentId="all" seed={digest.work.rail.done.flatMap(id => todos.get(id) ?? [])}
          enabled={!readOnly && active && (!compactRail || railOpen)} refreshRevision={historyRefreshRevision} listView onSelect={select} selectedTodoId={selectedTodoId} query={query}/> : null}
      </> : <div className="goal-material-activity">{activity.map(item => <button type="button" key={item.id} onClick={() => item.kind === "run" ? select({kind: "run", item: item.run}) : item.kind === "output" ? select({kind: "output", item: item.output}) : item.kind === "proposal" ? select({kind: "proposal", item: item.proposal}) : undefined}>
        {item.kind === "run" ? <>{item.run.status === "completed" ? <CircleCheck size={14} aria-hidden/> : <CircleAlert size={14} aria-hidden/>}<span>{item.run.title}<small>{localizedSessionStatus(item.run.status, t)}</small></span></> : item.kind === "output" ? <><CircleCheck size={14} aria-hidden/><span>{item.output.title}<small>{zh ? "成果返回" : "Result returned"}</small></span></> : item.kind === "proposal" ? <><Hand size={14} aria-hidden/><span>{item.proposal.title}<small>{zh ? "决定已记录" : "Decision recorded"}</small></span></> : null}
      </button>)}{!activity.length ? <p>{zh ? "当前时间线尚无已完成或返回的记录。" : "No completed work or returned results in this timeline."}</p> : null}</div>}
      <footer className="goal-work-scope">{zh ? "统计仅覆盖此 Goal；任务与历史可能分批载入。" : "Counts cover this Goal; tasks and history may be loaded in bounded pages."}<time dateTime={digest.observedAt}>{new Date(digest.observedAt).toLocaleTimeString(locale)}</time></footer>
    </aside>;
  return <><div className={`goal-workspace-composition${quietWork && !railOpen ? " is-quiet" : ""}`}>
    <div className="goal-workspace-document">
      <section className="goal-now" aria-label={zh ? "当前工作" : "Now"}>
        <header className="goal-now-header"><h2>{zh ? "当前工作" : "Now"}</h2>
          {!quietWork ? <div role="group" aria-label={zh ? "工作视图" : "Work view"} className="goal-work-lenses">
            {(["owner", "board", "map"] as const).map(value => <button key={value} type="button" aria-pressed={lens === value} onClick={() => {setVisited(current => new Set([...current, value])); setLens(value);}}>
              {value === "owner" ? (zh ? "按负责人" : "By owner") : value === "board" ? (zh ? "看板" : "Board") : (zh ? "关系图" : "Map")}
            </button>)}
          </div> : null}
          {!readOnly ? <AddTask goal={goal} proposals={proposals} onPreview={onPreview} onSelect={select}/> : null}
        </header>
        {digest.work.coverage !== "complete" ? <p className="goal-work-scope" role="status">{zh ? `当前列出 ${digest.work.listedOpen} 项；总数或覆盖范围不完整。` : `${digest.work.listedOpen} items listed; total or coverage is incomplete.`}</p> : null}
        <div className="goal-owner-groups" hidden={lens !== "owner"}>{digest.work.groups.map(group => <section className="goal-owner-group" key={group.ownerId ?? ""} data-owner-id={group.ownerId ?? ""}>
          <header><h3>{ownerName(group.ownerId)}</h3><span>{group.todoIds.length} {zh ? "项任务" : "tasks"} · {group.todoIds.filter(id => rows.get(id)?.displayClass === group.displayClass).length} {goalWorkClassLabel(group.displayClass, zh)}</span>
            {!readOnly && group.ownerId && onOpenAgentConversation ? <button type="button" className="goal-owner-conversation" data-agent-id={group.ownerId} aria-label={zh ? `与 ${ownerName(group.ownerId)} 对话` : `Talk to ${ownerName(group.ownerId)}`} onClick={() => onOpenAgentConversation(group.ownerId!)}>{zh ? "工作记录" : "Work conversation"}</button> : null}
          </header>
          {group.todoIds.map(id => task(id))}
        </section>)}
          {!digest.work.groups.length ? <p className="goal-work-scope">{digest.work.coverage === "complete" ? (zh ? "当前没有可列出的进行中任务。延后与完成记录在全部工作中。" : "No current work to list. Deferred and completed work remain in All work.") : (zh ? "当前快照没有可列出的任务。" : "No work is listed in this snapshot.")}</p> : null}
        </div>
        {visited.has("board") ? <div hidden={lens !== "board"}>{board}</div> : null}
        {visited.has("map") ? <div hidden={lens !== "map"}>{map}</div> : null}
      </section>
      <section className="goal-workspace-results" aria-label={zh ? "成果" : "Results"}><h2>{zh ? "成果" : "Results"}</h2>{results}</section>
    </div>
    {!quietWork || railOpen ? compactRail ? <dialog className="goal-work-rail-dialog" aria-label={zh ? "全部工作" : "All work"} ref={dialog} onCancel={event => {event.preventDefault(); onCloseRail();}}>
      <button type="button" className="goal-work-rail-close" onClick={onCloseRail}>{zh ? "关闭全部工作" : "Close all work"}</button>
      {rail}
    </dialog> : rail : null}
  </div>
  </>;
}
