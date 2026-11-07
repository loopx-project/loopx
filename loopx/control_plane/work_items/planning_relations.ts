import type { JsonObject } from "../effect_program.ts";
import { todoResumeDependencyId } from "../todos/resume_condition.ts";

export const PLANNING_TODO_ID = /^todo_[A-Za-z0-9_-]{3,80}$/;

export const ENFORCEMENT_BY_RELATION = {
  successor: "lineage_only",
  unblocks: "typed_lifecycle",
  resumes_when: "typed_condition",
  superseded_by: "lineage_only",
  routes_via: "read_only_context",
} as const;

export type PlanningRelationKind = keyof typeof ENFORCEMENT_BY_RELATION;

export type PlanningInventoryRelation = {
  [Kind in PlanningRelationKind]: JsonObject & {
    from_todo_id: string;
    to_ref: string;
    relation: Kind;
    enforcement: (typeof ENFORCEMENT_BY_RELATION)[Kind];
  };
}[PlanningRelationKind];

export function relation<Kind extends PlanningRelationKind>(
  fromTodoId: string,
  toRef: string,
  kind: Kind,
): Extract<PlanningInventoryRelation, { relation: Kind }> {
  return {
    from_todo_id: fromTodoId,
    to_ref: toRef,
    relation: kind,
    enforcement: ENFORCEMENT_BY_RELATION[kind],
  } as Extract<PlanningInventoryRelation, { relation: Kind }>;
}

function candidateRelations(item: PlanningRelationSource): PlanningInventoryRelation[] {
  const projected: PlanningInventoryRelation[] = [];
  for (const successor of new Set(item.successor_todo_ids)) {
    if (successor !== item.todo_id) {
      projected.push(relation(item.todo_id, successor, "successor"));
    }
  }
  if (item.unblocks_todo_id && item.unblocks_todo_id !== item.todo_id) {
    projected.push(relation(item.todo_id, item.unblocks_todo_id, "unblocks"));
  }
  if (item.resume_when) {
    projected.push(relation(item.todo_id, item.resume_when, "resumes_when"));
  }
  if (item.superseded_by && item.superseded_by !== item.todo_id) {
    projected.push(relation(item.todo_id, item.superseded_by, "superseded_by"));
  }
  const routeRef = item.route_id || item.route_key;
  if (routeRef) {
    projected.push(relation(item.todo_id, `route:${routeRef}`, "routes_via"));
  }
  return projected;
}

export function relationKey(value: PlanningInventoryRelation): string {
  return `${value.from_todo_id}\u0000${value.relation}\u0000${value.to_ref}`;
}

export function projectRelations(items: readonly PlanningRelationSource[]): PlanningInventoryRelation[] {
  const projected: PlanningInventoryRelation[] = [];
  const seen = new Set<string>();
  for (const item of items) {
    for (const value of candidateRelations(item)) {
      const key = relationKey(value);
      if (seen.has(key)) continue;
      seen.add(key);
      projected.push(value);
    }
  }
  return projected;
}

export function todoRef(value: string): string | null {
  if (PLANNING_TODO_ID.test(value)) return value;
  return todoResumeDependencyId(value);
}

export interface PlanningRelationSource {
  todo_id: string;
  successor_todo_ids: string[];
  unblocks_todo_id?: string;
  resume_when?: string;
  superseded_by?: string;
  route_id?: string;
  route_key?: string;
}
