import type { JsonObject } from "../../effect_program.ts";
import { EffectRuntimeRequestError } from "../../effect_runtime_errors.ts";
import {
  requireJsonObject, requireStringArray, requireBoolean,
  requireNonEmptyString, optionalNonEmptyString, requireStringLiteral,
} from "../../runtime_decode.ts";
import {
  evaluateTodoResumeConditions, diagnoseTodoResumeCondition,
  resumeConditionHasKnownPendingTarget,
} from "../../todos/resume_condition.ts";
import { projectTodoResumePlanning } from "../../todos/resume_planning.ts";

type Disposition = "runnable" | "waiting" | "unresolved" | "uncertain";
interface Declaration { candidates: string[]; unresolved: string }
const RESULT = "goal_fallback_disposition_v0";

function objects(value: unknown, label: string): JsonObject[] {
  if (!Array.isArray(value)) throw new EffectRuntimeRequestError(`${label} must be an array`);
  return value.map((item) => requireJsonObject(item, label));
}

function disposition(
  item: JsonObject, agent: string | null, condition: JsonObject | undefined,
): Disposition {
  const claim = optionalNonEmptyString(item.claimed_by, "claimed_by");
  const excluded = requireStringArray(item.excluded_agents, "excluded_agents");
  if (item.task_class !== "advancement_task" ||
      (item.role !== undefined && item.role !== "agent") ||
      (item.archive_state !== undefined && item.archive_state !== "active") ||
      requireBoolean(item.done, "done") ||
      requireBoolean(item.removed_continuation, "removed_continuation") ||
      (agent && (excluded.includes(agent) || (claim !== null && claim !== agent)))) return "unresolved";
  if (!["open", "deferred"].includes(String(item.status))) return "unresolved";
  if (!item.resume_when) return item.status === "open" ? "runnable" : "unresolved";
  if (!condition) return "uncertain";
  const diagnosis = diagnoseTodoResumeCondition(condition, String(item.todo_id));
  if (diagnosis.state === "invalid") return "unresolved";
  if (condition.provider_required === true) return "uncertain";
  if (diagnosis.state === "satisfied") return "runnable";
  // A false ready bit alone is not a durable wait. Reuse the same positive
  // target/generation/repository proof as supervision and settlement.
  return resumeConditionHasKnownPendingTarget(condition, item) ? "waiting" : "unresolved";
}

function authoritativeDispositions(
  request: JsonObject, agent: string | null, declarations: Declaration[],
): Map<string, Disposition> {
  const items = objects(request.items, "items");
  const declared = new Set(declarations.flatMap((entry) => entry.candidates));
  const evaluation = requireJsonObject(request.resume_evaluation, "resume_evaluation");
  // The same source rows feed target classification and condition evaluation;
  // callers cannot supply a contradictory second set of dependency facts.
  const evaluated = evaluateTodoResumeConditions({
    ...evaluation, source_items: items,
    items: items.filter((item) => declared.has(String(item.todo_id))),
  });
  const conditions = new Map(objects(evaluated.conditions, "conditions").map((row) =>
    [String(row.todo_id), requireJsonObject(row.condition, "condition")] as const));
  const byId = new Map<string, Disposition>();
  const counts = new Map<string, number>();
  for (const item of items) {
    const id = requireNonEmptyString(item.todo_id, "todo_id");
    counts.set(id, (counts.get(id) ?? 0) + 1);
  }
  for (const item of items) {
    const id = String(item.todo_id);
    if (!declared.has(id)) continue;
    const condition = conditions.get(id);
    const target = condition?.target_todo_id;
    // Duplicate target/dependency identity is uncertainty, never last-row-wins.
    byId.set(id, counts.get(id) !== 1 ||
      (typeof target === "string" && (counts.get(target) ?? 0) > 1)
      ? "uncertain" : disposition(item, agent, condition));
  }
  return byId;
}

export function projectFallbackDisposition(value: unknown): JsonObject {
  const request = requireJsonObject(value, "fallback disposition");
  if (request.schema_version !== "goal_fallback_disposition_request_v0")
    throw new EffectRuntimeRequestError("unsupported fallback disposition schema");
  const source = requireStringLiteral(request.source_state,
    ["complete", "unavailable", "omitted"], "source_state");
  const agent = optionalNonEmptyString(request.agent_id, "agent_id");
  const declarations: Declaration[] = objects(request.declarations, "declarations").map((entry) => ({
    candidates: requireStringArray(entry.candidate_ids, "candidate_ids"),
    unresolved: requireNonEmptyString(entry.unresolved_id, "unresolved_id"),
  }));
  if (declarations.length > 4 || declarations.some((entry) => entry.candidates.length > 2))
    throw new EffectRuntimeRequestError("fallback declaration bound exceeded");
  const resolved: JsonObject = { schema_version: RESULT, kind: "resolved",
    unresolved_todo_ids: [], lookup_uncertain_todo_ids: [] };
  if (requireBoolean(request.terminal, "terminal") || declarations.length === 0) return resolved;
  const planning = projectTodoResumePlanning(request.resume_planning);
  const blocked = objects(planning.blocked_successor_items, "blocked_successor_items");
  if (!requireBoolean(request.blocker_present, "blocker_present") && blocked.length === 0) return resolved;
  const states = source === "complete" ? authoritativeDispositions(request, agent, declarations)
    : new Map<string, Disposition>();
  if (source === "omitted") {
    for (const id of requireStringArray(request.legacy_selectable_ids, "legacy_selectable_ids"))
      states.set(id, "runnable");
    for (const row of blocked) states.set(String(row.todo_id), "waiting");
  }
  const created = new Set(requireStringArray(request.created_or_reopened_ids, "created_or_reopened_ids"));
  const unresolved = new Set<string>();
  const uncertain = new Set<string>();
  for (const entry of declarations) {
    // Alternatives resolve a declaration symmetrically. Uncertainty belongs
    // only to declarations without a positively resolved alternative.
    if (entry.candidates.some((id) => created.has(id) ||
      states.get(id) === "runnable" || states.get(id) === "waiting")) continue;
    const unknown = entry.candidates.filter((id) => states.get(id) === "uncertain" ||
      (source !== "complete" && !states.has(id)));
    if (unknown.length) {
      for (const id of unknown) uncertain.add(id);
    } else unresolved.add(entry.unresolved);
  }
  return {
    schema_version: RESULT,
    kind: unresolved.size ? "vision_fallback_unresolved"
      : uncertain.size ? "vision_fallback_lookup_uncertain" : "resolved",
    unresolved_todo_ids: [...unresolved].sort().slice(0, 3),
    lookup_uncertain_todo_ids: [...uncertain].sort().slice(0, 3),
  };
}
