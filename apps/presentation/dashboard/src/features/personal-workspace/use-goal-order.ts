import { useEffect, useRef, useState, type PointerEvent } from "react";
import { decodeGoalOrder, goalOrderStorageKey, moveGoal, orderedGoals } from "./goal-order";
import type { WorkspaceGoal } from "./personal-workspace-model";

// The sidebar is keyed by source, so neither gestures nor preferences cross machines.
export function useGoalOrder(goals: WorkspaceGoal[], source: string, canMove = (_from: string, _to: string) => true, browserOnly = false) {
  const key = goalOrderStorageKey(source);
  const [order, setOrder] = useState(() => {
    try { return decodeGoalOrder(localStorage.getItem(key)); } catch { return []; }
  });
  const orderRef = useRef(order);
  const editVersion = useRef(0);
  const saver = useRef<((next: string[]) => void) | null>(null);
  const [saveFailed, setSaveFailed] = useState(false);
  useEffect(() => {
    if (browserOnly) {
      setSaveFailed(false);
      saver.current = (next) => {
        try { localStorage.setItem(key, JSON.stringify(next)); setSaveFailed(false); }
        catch { setSaveFailed(true); }
      };
      return () => { saver.current = null; };
    }
    let disposed = false;
    let loaded = false;
    let queue = Promise.resolve();
    const initialVersion = editVersion.current;
    async function request(next?: string[], onlyIfAbsent = false) {
      const response = await fetch(`/api/chat/sidebar-goal-order?source=${encodeURIComponent(source)}`, {
        ...(next ? { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ source, order: next, only_if_absent: onlyIfAbsent }) } : {}),
        signal: AbortSignal.timeout(5_000),
      });
      const body: unknown = await response.json();
      if (!response.ok || !body || typeof body !== "object" || !("ok" in body) || body.ok !== true
        || !("order" in body) || (body.order !== null && (!Array.isArray(body.order)
          || !body.order.every((id: unknown) => typeof id === "string")))) throw new Error("Goal order unavailable");
      return body.order as string[] | null;
    }
    async function load() {
      const saved = await request();
      loaded = true;
      if (!disposed && initialVersion === editVersion.current && saved !== null) {
        orderRef.current = saved;
        setOrder(saved);
        try { localStorage.setItem(key, JSON.stringify(saved)); } catch { /* Durable save remains authoritative. */ }
      }
      return saved;
    }
    const ready = load();
    // Each move updates immediately, but writes wait for hydration and for the
    // preceding write. Neither a late read nor a late failure can undo a move.
    function persist(next: string[], version: number, migration = false) {
      queue = queue.then(async () => {
        try { await ready; } catch { if (!loaded) await load(); }
        if (disposed) return;
        const saved = await request(next, migration);
        if (!disposed && version === editVersion.current) {
          if (migration && saved !== null) { orderRef.current = saved; setOrder(saved); }
          setSaveFailed(false);
        }
      }).catch(() => { if (!disposed && version === editVersion.current) setSaveFailed(true); });
    }
    saver.current = (next) => persist(next, editVersion.current);
    void ready.then((saved) => {
      if (disposed || initialVersion !== editVersion.current) return;
      if (saved === null && orderRef.current.length) persist(orderRef.current, initialVersion, true);
      else setSaveFailed(false);
    }).catch(() => { if (!disposed) setSaveFailed(true); });
    return () => { disposed = true; saver.current = null; };
  }, [key, source, browserOnly]);
  const [lastMoved, setLastMoved] = useState<{ title: string; position: number } | null>(null);
  const [target, setTarget] = useState<{ id: string; after: boolean } | null>(null);
  const gesture = useRef<{ id: string; x: number; y: number; dragging: boolean } | null>(null);
  const suppressClick = useRef(false);
  const sorted = orderedGoals(goals, order);
  const visible = sorted.map((goal) => goal.goalId);
  function move(from: string, to: string, after: boolean) {
    if (!canMove(from, to)) return;
    const current = orderRef.current;
    const next = moveGoal(current, visible, from, to, after);
    if (next === current) return;
    orderRef.current = next;
    editVersion.current += 1;
    setOrder(next);
    const nextGoals = orderedGoals(goals, next);
    const position = nextGoals.findIndex((goal) => goal.goalId === from);
    const moved = nextGoals[position];
    if (moved) setLastMoved({ title: moved.title, position: position + 1 });
    try { localStorage.setItem(key, JSON.stringify(next)); } catch { /* The server can still save it. */ }
    saver.current?.(next);
  }
  function cancel() { gesture.current = null; setTarget(null); }
  return {
    sorted, target, saveFailed, lastMoved, move,
    moveBy(id: string, delta: -1 | 1) {
      const siblings = sorted.filter(goal => canMove(id, goal.goalId));
      const neighbor = siblings[siblings.findIndex(goal => goal.goalId === id) + delta];
      if (neighbor) move(id, neighbor.goalId, delta === 1);
    },
    pointerProps: (id: string) => ({
      onPointerDown(event: PointerEvent<HTMLButtonElement>) {
        suppressClick.current = false;
        // Touch retains vertical scrolling; the explicit move buttons work on touch.
        if (event.pointerType !== "mouse" || event.button !== 0) return;
        gesture.current = { id, x: event.clientX, y: event.clientY, dragging: false };
        event.currentTarget.setPointerCapture(event.pointerId);
      },
      onPointerMove(event: PointerEvent<HTMLButtonElement>) {
        const current = gesture.current;
        if (!current) return;
        if (!current.dragging && Math.hypot(event.clientX - current.x, event.clientY - current.y) < 6) return;
        current.dragging = true;
        suppressClick.current = true;
        const row = document.elementFromPoint(event.clientX, event.clientY)?.closest<HTMLElement>("[data-reorder-goal]");
        const list = event.currentTarget.closest(".personal-goal-list");
        const targetId = row?.dataset.reorderGoal;
        if (!row || !targetId || !list?.contains(row) || targetId === current.id || !canMove(current.id, targetId)) { setTarget(null); return; }
        const bounds = row.getBoundingClientRect();
        setTarget({ id: targetId, after: event.clientY > bounds.top + bounds.height / 2 });
      },
      onPointerUp() {
        if (gesture.current?.dragging && target) move(gesture.current.id, target.id, target.after);
        cancel();
      },
      onPointerCancel: cancel,
      onLostPointerCapture: cancel,
      onKeyDown(event: React.KeyboardEvent<HTMLButtonElement>) {
        if (event.key === "Escape") cancel();
      },
      onClickCapture(event: React.MouseEvent<HTMLButtonElement>) {
        if (suppressClick.current) { event.preventDefault(); event.stopPropagation(); suppressClick.current = false; }
      },
    }),
  };
}
