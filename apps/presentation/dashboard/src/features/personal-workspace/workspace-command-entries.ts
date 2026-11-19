import type { WorkspaceAttention, WorkspaceDrawerSelection, WorkspaceGoal, WorkspaceTimelineItem } from "./personal-workspace-model";

/** Presentation destinations only: no executable action or free-text request. */
export type WorkspaceCommandEntry = {
  id: string;
  title: string;
  group: "goal" | "todo" | "output" | "proposal";
  disabled?: "loading" | "unavailable";
  destination: { kind: "goal"; goalId: string } | { kind: "detail"; selection: WorkspaceDrawerSelection };
};

export function workspaceCommandEntries({ goals, goalId, items, userTodos, sourceId, readOnly, unavailable }: {
  goals: WorkspaceGoal[]; goalId: string | null; items: WorkspaceTimelineItem[];
  userTodos: WorkspaceAttention[]; sourceId?: string;
  readOnly: boolean; unavailable: boolean;
}): WorkspaceCommandEntry[] {
  if (unavailable) return [];
  const entries: WorkspaceCommandEntry[] = goals.map(goal => ({
    id: `goal:${goal.goalId}`, title: goal.title, group: "goal",
    disabled: goal.loadState === "loading" ? "loading" : goal.loadState === "error" ? "unavailable" : undefined,
    destination: { kind: "goal", goalId: goal.goalId },
  }));
  const goal = goals.find(candidate => candidate.goalId === goalId);
  if (!goal || goal.loadState) return entries;
  for (const todo of goal.agentTodos) entries.push({
    id: `todo:${todo.todoId}`, title: todo.text, group: "todo",
    destination: { kind: "detail", selection: { kind: "todo", item: {
      ...todo, goalId: goal.goalId, goalTitle: goal.title, ownerLabel: todo.claimedBy,
    } } },
  });
  for (const item of userTodos) {
    if (item.goalId !== goalId || item.sourceId !== sourceId) continue;
    entries.push({
      id: `attention:${item.todoId}`, title: item.text, group: "todo",
      destination: { kind: "detail", selection: { kind: "attention", item } },
    });
  }
  for (const item of items) {
    if (item.kind === "output" && item.output.goalId === goalId) entries.push({
      id: `output:${item.output.outputId}`, title: item.output.title, group: "output",
      destination: { kind: "detail", selection: { kind: "output", item: item.output } },
    });
    // Only already projected, current Goal previews. Opening never creates one.
    if (!readOnly && !goal.sourceAccess?.readOnly && item.kind === "proposal"
      && item.proposal.goalId === goalId && ["draft", "ready", "gated"].includes(item.proposal.status)) entries.push({
      id: `proposal:${item.proposal.previewId}`, title: item.proposal.title, group: "proposal",
      destination: { kind: "detail", selection: { kind: "proposal", item: item.proposal } },
    });
  }
  return [...new Map(entries.map(entry => [entry.id, entry])).values()];
}

export function filterWorkspaceCommands(entries: WorkspaceCommandEntry[], query: string): WorkspaceCommandEntry[] {
  const words = query.normalize("NFKC").toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
  return entries.filter(entry => {
    const text = `${entry.title} ${entry.id}`.normalize("NFKC").toLocaleLowerCase();
    return words.every(word => text.includes(word));
  });
}

export function isPaletteShortcut(event: { key: string; metaKey: boolean; ctrlKey: boolean;
  altKey: boolean; shiftKey: boolean; isComposing: boolean; keyCode: number; repeat: boolean }) {
  return !event.isComposing && event.keyCode !== 229 && !event.repeat
    && !event.altKey && !event.shiftKey && (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k";
}
