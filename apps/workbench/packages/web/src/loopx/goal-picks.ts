/**
 * Which Goals the sidebar shows: at most five, the pinned ones always in front, the rest filled by
 * 「需要你 → 最近有动静」. Only top-level, unstopped Goals take sidebar room — children live under
 * their parent, stopped ones on the All Goals page. Pinning is this device's own choice, kept in
 * localStorage; the core knows nothing about it.
 */

import { useEffect, useState } from 'react';
import type { LoopxAttention, LoopxGoal } from './status';

const PINS_KEY = 'calli.loopx.goalPins';

export function loadGoalPins(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(PINS_KEY) ?? '[]');
    return Array.isArray(raw) ? raw.filter((id): id is string => typeof id === 'string') : [];
  } catch {
    return [];
  }
}

export function saveGoalPins(ids: string[]): void {
  try {
    localStorage.setItem(PINS_KEY, JSON.stringify(ids.filter(id => typeof id === 'string')));
  } catch {
    // Storage unavailable: pinning lasts until the page reloads.
  }
  for (const notify of listeners) notify();
}

/** The sidebar and the All Goals page both show pins; this keeps them the same list at once. */
const listeners = new Set<() => void>();

export function useGoalPins(): [string[], (goalId: string, pinned: boolean) => void] {
  const [pins, setPins] = useState(loadGoalPins);
  useEffect(() => {
    const notify = () => setPins(loadGoalPins());
    listeners.add(notify);
    window.addEventListener('storage', notify);
    return () => { listeners.delete(notify); window.removeEventListener('storage', notify); };
  }, []);
  const set = (goalId: string, pinned: boolean) => {
    saveGoalPins(pinned ? [...new Set([...loadGoalPins(), goalId])] : loadGoalPins().filter(id => id !== goalId));
    setPins(loadGoalPins());
  };
  return [pins, set];
}

/** The pinned goals that still exist and may show in the sidebar, in the order they were pinned. */
export function orderedPins(goals: LoopxGoal[], pins: string[]): LoopxGoal[] {
  const byId = new Map(goals.map(goal => [goal.goalId, goal]));
  return pins.map(id => byId.get(id)).filter((goal): goal is LoopxGoal =>
    Boolean(goal && goal.parentId === null && goal.activationState === 'active'));
}

/** Sidebar picks: pinned first, then the needs-you ones, then whatever moved lately — never over the cap. */
export function sidebarGoals(goals: LoopxGoal[], attention: LoopxAttention[], pins: string[], cap = 5): LoopxGoal[] {
  const needsYou = new Set(attention.filter(item =>
    item.userTodos.open > 0 || item.waitingOn === 'user' || item.waitingOn === 'owner').map(item => item.goalId));
  const openTodos = (goalId: string) => attention.find(item => item.goalId === goalId);
  const lastActivity = (goal: LoopxGoal): number => {
    const item = openTodos(goal.goalId);
    const stamps = [goal.updatedAt, ...(item ? [item.lastActivityAt] : [])]
      .filter((stamp): stamp is string => typeof stamp === 'string')
      .map(stamp => Date.parse(stamp)).filter(n => Number.isFinite(n));
    return stamps.length ? Math.max(...stamps) : 0;
  };
  const eligible = goals.filter(goal => goal.parentId === null && goal.activationState === 'active');
  const picked = orderedPins(eligible, pins);
  const chosen = new Set(picked.map(goal => goal.goalId));
  const rest = eligible
    .filter(goal => !chosen.has(goal.goalId))
    .sort((a, b) => {
      const aNeeds = needsYou.has(a.goalId) ? 1 : 0;
      const bNeeds = needsYou.has(b.goalId) ? 1 : 0;
      if (aNeeds !== bNeeds) return bNeeds - aNeeds;
      return lastActivity(b) - lastActivity(a);
    });
  for (const goal of rest) {
    if (picked.length >= cap) break;
    picked.push(goal);
    chosen.add(goal.goalId);
  }
  return picked.slice(0, cap);
}
