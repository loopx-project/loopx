/** Organization inside one status source; never an execution or access grant. */
export type GoalParent = { goalId: string; parentGoalId?: string | null };

export function goalHierarchy<T extends GoalParent>(goals: readonly T[]) {
  const byId = new Map(goals.map(goal => [goal.goalId, goal]));
  const parents = new Map<string, string>();
  const invalidParents = new Set<string>();
  for (const goal of goals) {
    if (!goal.parentGoalId) continue;
    if (byId.has(goal.parentGoalId)) parents.set(goal.goalId, goal.parentGoalId);
    else invalidParents.add(goal.goalId);
  }
  // Break every edge in a cycle. Orphans and cyclic Goals remain visible roots;
  // a malformed relation cannot hide work or make a parent its own descendant.
  const visited = new Set<string>();
  for (const goal of goals) {
    const path: string[] = [];
    const positions = new Map<string, number>();
    let id: string | undefined = goal.goalId;
    while (id !== undefined && !visited.has(id) && !positions.has(id)) {
      positions.set(id, path.length);
      path.push(id);
      id = parents.get(id);
    }
    if (id !== undefined && positions.has(id)) {
      for (const member of path.slice(positions.get(id))) {
        parents.delete(member);
        invalidParents.add(member);
      }
    }
    for (const member of path) visited.add(member);
  }
  const children = new Map<string | null, T[]>();
  for (const goal of goals) {
    const parent = parents.get(goal.goalId) ?? null;
    const siblings = children.get(parent) ?? [];
    siblings.push(goal);
    children.set(parent, siblings);
  }
  return { byId, parents, children, invalidParents };
}

export function goalSubtree<T extends GoalParent>(goals: T[], rootId?: string | null): T[] {
  if (!rootId) return goals;
  const tree = goalHierarchy(goals);
  if (!tree.byId.has(rootId)) return [];
  const included = new Set([rootId]);
  const pending = [rootId];
  while (pending.length) {
    for (const child of tree.children.get(pending.pop()!) ?? []) {
      included.add(child.goalId);
      pending.push(child.goalId);
    }
  }
  return goals.filter(goal => included.has(goal.goalId));
}

export function visibleGoalTree<T extends GoalParent>(goals: readonly T[], expanded: ReadonlySet<string>) {
  const tree = goalHierarchy(goals);
  const pending = (tree.children.get(null) ?? []).map(goal => ({ goal, depth: 0 })).reverse();
  const rows: Array<{ goal: T; depth: number; hasChildren: boolean }> = [];
  while (pending.length) {
    const row = pending.pop()!;
    const children = tree.children.get(row.goal.goalId) ?? [];
    rows.push({ ...row, hasChildren: children.length > 0 });
    if (expanded.has(row.goal.goalId)) {
      for (let i = children.length - 1; i >= 0; i--) pending.push({ goal: children[i]!, depth: row.depth + 1 });
    }
  }
  return rows;
}
