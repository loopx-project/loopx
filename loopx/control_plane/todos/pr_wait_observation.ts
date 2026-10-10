/** Poll only exact unfinished PR dependencies, independent of review queue membership.
 * This read plan grants no Todo transition or delivery authority. Python transports
 * GitHub metadata; the existing merge-event reducer remains the satisfaction owner.
 */
import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { requireJsonObject, requireNonEmptyString } from "../runtime_decode.ts";
import { evaluateTodoResumeConditions, normalizeTodoResumeWhen,
  TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION, TODO_RESUME_NORMALIZE_REQUEST_SCHEMA_VERSION,
} from "./resume_condition.ts";

export const PR_WAIT_OBSERVATION_REQUEST = "todo_pr_wait_observation_request_v0";
export const PR_WAIT_OBSERVATION_RESULT = "todo_pr_wait_observation_result_v0";
// Local classification of existing validation events, not a second PR lifecycle.
export const PR_WAIT_OBSERVATION_CLASSIFICATION = "pr_wait_observation";
export const PR_WAIT_POLL_INTERVAL_SECONDS = 1800;
const MAX_POLL_TARGETS = 4;

function clock(value: unknown): number | null {
  if (typeof value !== "string") return null;
  const normalized = normalizeTodoResumeWhen({schema_version: TODO_RESUME_NORMALIZE_REQUEST_SCHEMA_VERSION,
    resume_when: `resume_at:${value}`});
  return normalized ? Date.parse(normalized.slice("resume_at:".length)) : null;
}

export function planPrWaitObservations(value: unknown): JsonObject {
  const request = requireJsonObject(value, "PR wait observation request");
  if (request.schema_version !== PR_WAIT_OBSERVATION_REQUEST || !Array.isArray(request.items)
    || !Array.isArray(request.rollout_events)) throw new EffectRuntimeRequestError("invalid PR wait observation request");
  const actor = requireNonEmptyString(request.agent_id, "agent_id");
  const now = clock(request.generated_at);
  if (now === null) throw new EffectRuntimeRequestError("PR observation requires a timezone-aware clock");
  const items = request.items.map(row => requireJsonObject(row, "PR wait Todo"))
    .filter(row => row.role === "agent" && ["open", "deferred"].includes(String(row.status))
      && row.task_class === "advancement_task" && (row.archive_state == null || row.archive_state === "active")
      && (!row.claimed_by || row.claimed_by === actor)
      && !(Array.isArray(row.excluded_agents) && row.excluded_agents.includes(actor)));
  const evaluation = evaluateTodoResumeConditions({schema_version: TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION,
    items, source_items: [], rollout_events: request.rollout_events, kinds: ["pr_merged"]});
  const targets = new Map<string, JsonObject>();
  const unresolved: JsonObject[] = [];
  for (const row of evaluation.conditions as JsonObject[]) {
    const condition = requireJsonObject(row.condition, "PR wait condition");
    if (condition.kind !== "pr_merged" || condition.satisfied === true) continue;
    if (typeof condition.pr_repo !== "string" || typeof condition.pr_number !== "number"
      || condition.repository_binding_state === "ambiguous") {
      unresolved.push({todo_id: row.todo_id, resume_when: condition.resume_when});
      continue;
    }
    const ref = `${condition.pr_repo}#${condition.pr_number}`;
    const target = targets.get(ref) ?? {pr_ref: ref, repo: condition.pr_repo,
      number: condition.pr_number, url: `https://github.com/${condition.pr_repo}/pull/${condition.pr_number}`,
      todo_ids: [], last_checked_at: null, last_observed_state: null};
    (target.todo_ids as JsonObject[string][]).push(row.todo_id);
    targets.set(ref, target);
  }
  for (const raw of request.rollout_events) {
    const event = requireJsonObject(raw, "PR observation event");
    if (event.event_kind !== "validation" || event.classification !== PR_WAIT_OBSERVATION_CLASSIFICATION) continue;
    const refs = event.code_refs as JsonObject | undefined;
    const target = typeof refs?.pr_ref === "string" ? targets.get(refs.pr_ref) : undefined;
    const at = clock(event.recorded_at);
    if (!target || at === null || at > now) continue;
    const prior = clock(target.last_checked_at);
    if (prior === null || at > prior) {
      target.last_checked_at = event.recorded_at;
      target.last_observed_state = event.status ?? null;
    }
  }
  const waiting: JsonObject[] = [...targets.values()].map(target => ({...target,
    next_due_at: target.last_checked_at === null ? request.generated_at
      : new Date(clock(target.last_checked_at)! + PR_WAIT_POLL_INTERVAL_SECONDS * 1000).toISOString()}));
  const due = waiting.filter(target => clock(target.next_due_at)! <= now)
    .sort((a, b) => clock(a.next_due_at)! - clock(b.next_due_at)! || String(a.pr_ref).localeCompare(String(b.pr_ref)));
  return {schema_version: PR_WAIT_OBSERVATION_RESULT, targets: due.slice(0, MAX_POLL_TARGETS),
    waiting_targets: waiting, unresolved, due_count: due.length,
    observation_classification: PR_WAIT_OBSERVATION_CLASSIFICATION,
    poll_interval_seconds: PR_WAIT_POLL_INTERVAL_SECONDS, max_poll_targets: MAX_POLL_TARGETS};
}
