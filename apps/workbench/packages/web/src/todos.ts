import type { Todo } from './api';

/** A plan with its sub-items. Children keep done ones in place: they strike through where they are. */
export interface TodoNode extends Todo {
  children: TodoNode[];
}

/** The plan list in display order: open plans first, then the recently finished ones. */
export interface TodoBoard {
  open: TodoNode[];
  /** Finished plans with their sub-items, most recently finished first. */
  done: TodoNode[];
}

/**
 * Shapes what the server returns into what the panel shows:
 * - sub-items nested under their plan, both levels ordered by `position`
 * - finished plans sink to the bottom group, newest first
 */
export function buildTodoBoard(todos: Todo[]): TodoBoard {
  const byParent = new Map<string, Todo[]>();
  for (const t of todos) {
    if (!t.parentId) continue;
    const list = byParent.get(t.parentId);
    if (list) list.push(t);
    else byParent.set(t.parentId, [t]);
  }
  for (const list of byParent.values()) list.sort(byPosition);

  const topLevel = todos.filter((t) => !t.parentId).sort(byPosition);
  const node = (t: Todo): TodoNode => ({ ...t, children: (byParent.get(t.id) ?? []).map(node) });
  return {
    open: topLevel.filter((t) => !t.done).map(node),
    done: topLevel.filter((t) => t.done).map(node).sort(byFinished),
  };
}

const byPosition = (a: Todo, b: Todo): number => a.position - b.position || a.createdAt.localeCompare(b.createdAt);

const byFinished = (a: Todo, b: Todo): number =>
  (b.doneAt ?? b.createdAt).localeCompare(a.doneAt ?? a.createdAt);

/** Threads that can still be linked to a plan: not linked yet, most recently active first. */
export function linkableThreads(todos: Todo[], todoId: string, threads: Todo['threads']): Todo['threads'] {
  const todo = todos.find((t) => t.id === todoId);
  const linked = new Set(todo?.threads.map((t) => t.id) ?? []);
  return threads.filter((t) => !linked.has(t.id));
}

/** Where an item goes: under `parentId` (null: top level), just before `before` (null: last). */
export interface TodoPlace {
  parentId: string | null;
  before: string | null;
}

/**
 * The list after a move, as the server will have it: the item takes its new place and the level it
 * lands in is renumbered from 1. Shown at once, before the server answers.
 */
export function moveTodo(todos: Todo[], id: string, to: TodoPlace): Todo[] {
  const moving = todos.find((t) => t.id === id);
  if (!moving) return todos;
  const siblings = todos.filter((t) => t.parentId === to.parentId && t.id !== id).sort(byPosition);
  const at = to.before === null ? -1 : siblings.findIndex((t) => t.id === to.before);
  siblings.splice(at === -1 ? siblings.length : at, 0, { ...moving, parentId: to.parentId });
  const placed = new Map(siblings.map((t, i) => [t.id, { ...t, position: i + 1 }]));
  return todos.map((t) => placed.get(t.id) ?? t);
}

/**
 * Tab on a plan: it becomes the last sub-item of the open plan above it. Not for a sub-item (one
 * level only), a plan with sub-items of its own, or the first plan.
 */
export function indentPlace(board: TodoBoard, id: string): TodoPlace | null {
  const i = board.open.findIndex((t) => t.id === id);
  if (i <= 0 || board.open[i].children.length > 0) return null;
  return { parentId: board.open[i - 1].id, before: null };
}

/** Shift+Tab on a sub-item: it becomes a plan of its own, right after the one it was under. */
export function outdentPlace(todos: Todo[], id: string): TodoPlace | null {
  const parentId = todos.find((t) => t.id === id)?.parentId;
  if (!parentId) return null;
  const top = todos.filter((t) => t.parentId === null).sort(byPosition);
  const next = top[top.findIndex((t) => t.id === parentId) + 1];
  return { parentId: null, before: next?.id ?? null };
}

/** The rows of the open list as they read, top to bottom, sub-items after their plan. */
export function openRows(board: TodoBoard): TodoNode[] {
  return board.open.flatMap((t) => [t, ...t.children]);
}
