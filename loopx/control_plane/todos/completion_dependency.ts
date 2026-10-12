/** Cycles in the normalized, unfinished todo_done graph. Each Todo has at most
 * one outgoing edge. Missing records terminate observation, not prove a cycle.
 * Cache both acyclic paths and cycle dependents so a batch stays linear. */
export interface CompletionDependencyCycle {
  todo_ids: string[];
  size: number;
}

export function completionDependencyCycles(
  edges: ReadonlyMap<string, string>,
): ReadonlyMap<string, CompletionDependencyCycle | null> {
  const resolved = new Map<string, CompletionDependencyCycle | null>();
  for (const start of edges.keys()) {
    if (resolved.has(start)) continue;
    const path: string[] = [];
    const positions = new Map<string, number>();
    let current: string | undefined = start;
    while (current !== undefined && !resolved.has(current) && !positions.has(current)) {
      positions.set(current, path.length);
      path.push(current);
      current = edges.get(current);
    }
    let cycle = current === undefined ? null : resolved.get(current) ?? null;
    if (current !== undefined && positions.has(current)) {
      const members = path.slice(positions.get(current)!);
      // Stable rotation makes evidence independent of provider record order.
      const first = members.reduce((best, id, index) => id < members[best] ? index : best, 0);
      cycle = {size: members.length, todo_ids: Array.from({length: Math.min(8, members.length)},
        (_, index) => members[(first + index) % members.length])};
    }
    for (const id of path) resolved.set(id, cycle);
  }
  return resolved;
}
