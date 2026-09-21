import { useEffect, useId, useRef, useState } from "react";
import { z } from "zod";
import {compactWorkspaceText} from "./personal-workspace-model";
import type { WorkspaceAgentTodo, WorkspaceDrawerSelection, WorkspaceGoal } from "./personal-workspace-model";
import { useWorkspaceI18n } from "./i18n";

const pageSchema = z.object({
  ok: z.literal(true), total: z.number().int().nonnegative(), next_cursor: z.string().nullable(),
  items: z.array(z.object({ todo_id: z.string(), text: z.string(), claimed_by: z.string().nullable(), evidence: z.string().nullable(), priority: z.string().nullable(), task_class: z.string().nullable() })).max(40),
});

/** Fixed-height previews keep layout/DOM cost bounded; the drawer retains full text. */
export function CompletedTaskLane({ goal, agentId, seed, enabled, listView = false, onSelect }: {
  goal: WorkspaceGoal; agentId: string; seed: WorkspaceAgentTodo[]; enabled: boolean;
  listView?: boolean; onSelect: (selection: WorkspaceDrawerSelection) => void;
}) {
  const { t } = useWorkspaceI18n();
  const historyId = useId();
  const [expanded, setExpanded] = useState(false);
  const visible = !listView || expanded;
  const rowHeight = listView ? 96 : 148;
  const [rows, setRows] = useState(seed);
  const [total, setTotal] = useState(agentId === "all" ? goal.doneTodoCount ?? seed.length : seed.length);
  const [cursor, setCursor] = useState<string | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const [expired, setExpired] = useState(false);
  const [viewport, setViewport] = useState({ top: 0, height: 600 });
  const [focused, setFocused] = useState<number | null>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const request = useRef<AbortController | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const element = scroll.current;
    if (!element) return;
    const observer = new ResizeObserver(() => setViewport({ top: element.scrollTop, height: element.clientHeight }));
    observer.observe(element);
    return () => { observer.disconnect(); request.current?.abort(); };
  }, []);
  const needsPage = cursor === undefined || viewport.top + viewport.height >= rows.length * rowHeight - rowHeight * 2;
  useEffect(() => {
    if (!enabled || !visible || !needsPage || cursor === null || error || request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    const query = new URLSearchParams({ goal_id: goal.goalId });
    if (agentId !== "all") query.set("agent_id", agentId);
    if (cursor) query.set("cursor", cursor);
    void fetch(`/api/chat/completed-todos?${query}`, { signal: controller.signal }).then(async (response) => {
      if (response.status === 409) setExpired(true);
      if (!response.ok) throw new Error("history unavailable");
      const page = pageSchema.parse(await response.json());
      if (controller.signal.aborted) return;
      const next = page.items.map((todo): WorkspaceAgentTodo => ({ todoId: todo.todo_id, text: todo.text, claimedBy: todo.claimed_by, evidence: todo.evidence, priority: todo.priority, taskClass: todo.task_class, done: true, status: "done" }));
      setRows((current) => cursor === undefined ? next : [...current, ...next.filter((todo) => !current.some((existing) => existing.todoId === todo.todoId))]);
      setTotal(page.total);
      setCursor(page.next_cursor);
    }).catch(() => { if (!controller.signal.aborted) setError(true); }).finally(() => {
      if (!controller.signal.aborted) { request.current = null; setBusy(false); }
    });
  }, [enabled, visible, needsPage, cursor, error, retry, goal.goalId, agentId, busy]);
  useEffect(() => {
    if (scroll.current) scroll.current.scrollTop = 0;
    setViewport({ top: 0, height: scroll.current?.clientHeight ?? 600 });
    setFocused(null);
  }, [listView]);
  const first = Math.max(0, Math.floor(viewport.top / rowHeight) - 3);
  const last = Math.min(rows.length, Math.ceil((viewport.top + viewport.height) / rowHeight) + 3);
  const indices = Array.from({ length: Math.max(0, last - first) }, (_, index) => first + index);
  if (focused !== null && focused < rows.length && !indices.includes(focused)) indices.push(focused);
  return <section className={`personal-object-list personal-task-lane${listView ? " personal-completed-list" : ""}`} data-testid="completed-task-lane">
    <header>{listView ? <button type="button" aria-controls={historyId} aria-expanded={expanded} onClick={() => setExpanded(value => !value)}><span aria-hidden="true">{expanded ? "▾" : "▸"}</span> {t("tasks.completed")}</button> : <strong><i aria-hidden="true" className="personal-kanban-dot tone-done" />{t("tasks.completed")}</strong>}<span>{total}</span></header>
    <div id={historyId} hidden={!visible} aria-label={t("tasks.completed")} className="personal-task-lane-scroll" ref={scroll} role="region" tabIndex={0} onScroll={(event) => setViewport({ top: event.currentTarget.scrollTop, height: event.currentTarget.clientHeight })}>
      <div className="personal-completed-window" style={{ height: rows.length * rowHeight }}>
        {indices.map((index) => {
          const todo = rows[index]!;
          return <div className="personal-task-card personal-completed-row" key={todo.todoId} style={{ top: index * rowHeight, height: rowHeight }}>
            <button type="button" onFocus={() => setFocused(index)} onBlur={() => setFocused(null)} onClick={() => onSelect({ kind: "todo", item: { ...todo, goalId: goal.goalId, goalTitle: goal.title, ownerLabel: todo.claimedBy ?? goal.agentLabel ?? goal.agentId } })}>
              <span className="is-done">✓</span><strong>{compactWorkspaceText(todo.text, 112)}</strong><small>{todo.claimedBy ?? goal.agentLabel ?? goal.agentId}</small>
            </button>
          </div>;
        })}
      </div>
      <div className="personal-completed-footer" role="status">
        {busy ? t("tasks.historyLoading") : error ? <><span>{t(expired ? "tasks.historyExpired" : "tasks.historyError")}</span><button type="button" onClick={() => { if (expired) { setCursor(undefined); if (scroll.current) scroll.current.scrollTop = 0; } setExpired(false); setError(false); setRetry((value) => value + 1); }}>{t("tasks.historyRetry")}</button></> : !enabled ? t("tasks.historyLocalOnly") : cursor === null ? t("tasks.historyEnd") : <button type="button" onClick={() => { if (scroll.current) scroll.current.scrollTop = rows.length * rowHeight; }}>{t("tasks.historyMore")}</button>}
      </div>
    </div>
  </section>;
}
