import {indexInferredSuccessors} from "./succession.ts";
/** Capture actual archived dependency records, never cached resume conclusions.
 * Missing legacy roles can be reconstructed only from an explicit agent-only
 * task class. A recorded Agent role can retain its legacy read classification;
 * that projection cannot establish a role or user decision authority. */
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {normalizeTodoResumeWhen, TODO_RESUME_NORMALIZE_REQUEST_SCHEMA_VERSION} from "./resume_condition.ts";
import {AGENT_TODO_TASK_CLASSES as AGENT_CLASSES, USER_TODO_TASK_CLASSES as USER_CLASSES} from "./authoring_scope.ts";
import {isStandingDecisionReceipt} from "./standing_decision.ts";

export const ARCHIVE_CAPTURE_REQUEST_SCHEMA = "todo_archive_dependency_capture_request_v2";
const fail = (reason: string): never => {throw new EffectRuntimeRequestError(`archive dependency capture: ${reason}`);};

// The codec supplies the existing Markdown read classification separately from
// recorded metadata. Only an explicit Agent identity may adopt that fallback.
function capturedTaskClass(item: JsonObject): string {
  const value = item.task_class ?? (item.role === "agent" ? item.legacy_task_class : null);
  return typeof value === "string" ? value : "";
}

export function captureArchivedTodoDependencies(value: unknown): JsonObject {
  const request = requireJsonObject(value, "archive dependency capture");
  if (request.schema_version !== ARCHIVE_CAPTURE_REQUEST_SCHEMA ||
      !Array.isArray(request.active) || !Array.isArray(request.archived)) fail("invalid request");
  const active = (request.active as unknown[]).map(v => requireJsonObject(v, "active Todo"));
  const archived = (request.archived as unknown[]).map(v => requireJsonObject(v, "archived Todo"));
  const byId = new Map<string, {item: JsonObject; index: number}[]>();
  for (const [index, item] of archived.entries()) {
    if (typeof item.todo_id !== "string") continue;
    const records = byId.get(item.todo_id) ?? [];
    records.push({item, index}); byId.set(item.todo_id, records);
  }
  const normalizedResume = (item: JsonObject) => normalizeTodoResumeWhen({
    schema_version: TODO_RESUME_NORMALIZE_REQUEST_SCHEMA_VERSION, resume_when: item.resume_when ?? null});
  const inferred = indexInferredSuccessors([...active, ...archived].map(item => {
    const resume = normalizedResume(item);
    return {id: typeof item.todo_id === "string" ? item.todo_id : null,
      advancement: capturedTaskClass(item) === "advancement_task",
      unblocks: typeof item.unblocks_todo_id === "string" ? item.unblocks_todo_id : null,
      resumes: resume?.startsWith("todo_done:") ? resume.slice("todo_done:".length) : null};
  }));
  const activeIds = new Set(active.map(item => item.todo_id));
  const selected = new Map<string, JsonObject>();
  const queue = [...active];
  // Standing authority depends on the full decision chronology, not only on
  // records reachable from resume conditions. Preserve every eligible
  // archived approval, rejection and cancellation before walking topology.
  for (const [index, item] of archived.entries()) {
    if (!isStandingDecisionReceipt(item) || typeof item.todo_id !== "string") continue;
    const candidates = byId.get(item.todo_id);
    if (!candidates || candidates.length !== 1 || activeIds.has(item.todo_id)) {
      fail("duplicate standing decision identity");
    }
    if (item.archive_state !== "archive") fail("standing decision is not archived");
    selected.set(item.todo_id, {index, role: "user", task_class: item.task_class});
    queue.push(item);
  }
  for (let cursor = 0; cursor < queue.length; cursor++) {
    const current = queue[cursor]!;
    const token = normalizedResume(current);
    const dependency = token && (token.startsWith("todo_done:") || token.startsWith("monitor_changed:"))
      ? token.slice(token.indexOf(":") + 1).split("@", 1)[0] : null;
    const links = [...new Set([dependency, current.superseded_by,
      ...(Array.isArray(current.successor_todo_ids) ? current.successor_todo_ids : []),
      ...(inferred.get(String(current.todo_id)) ?? [])].filter((value): value is string => typeof value === "string"))];
    for (const id of links) {
      const candidates = byId.get(id);
      if (!candidates) continue; // A genuinely absent target remains an unsatisfied condition.
      if (candidates.length !== 1 || activeIds.has(id)) fail("duplicate dependency identity");
      const {item, index} = candidates[0]!;
      // Capture records, not satisfaction. Deferred history is valid evidence
      // of an unmet todo_done condition; its status must remain deferred.
      if (item.archive_state !== "archive" || !["done", "deferred"].includes(String(item.status)) || item.done !== true) {
        fail("dependency is not an archived terminal record");
      }
      if (selected.has(id)) continue;
      const taskClass = capturedTaskClass(item);
      const role = item.role ?? (AGENT_CLASSES.has(taskClass) ? "agent" : null);
      if (!((role === "agent" && AGENT_CLASSES.has(taskClass)) ||
            (role === "user" && USER_CLASSES.has(taskClass)))) {
        fail(`archive index ${index}: dependency requires a recorded role and compatible task_class; ` +
          "legacy classification is accepted only for a recorded agent role");
      }
      // Contradictory user authority on an agent record is not repaired by inference.
      if (role === "agent" && ["decision_scope", "decision_outcome", "global_gate", "blocks_agent", "bound_agent", "goal_bound"]
          .some(field => item[field] != null && item[field] !== false)) fail("agent dependency carries user authority");
      selected.set(id, {index, role, task_class: taskClass}); queue.push(item);
    }
  }
  return {schema_version: "todo_archive_dependency_capture_result_v1", records: [...selected.values()]};
}
