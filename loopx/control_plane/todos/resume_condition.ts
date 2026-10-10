import { createHash } from "node:crypto";

import { readGoalRolloutEventSnapshot } from "../rollout_receipt_log.ts";

import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  optionalNonEmptyString,
  requireInteger,
  requireJsonObject,
  requireNonEmptyString,
  requireStringArray,
} from "../runtime_decode.ts";

import type { JsonObject } from "../effect_program.ts";
import {
  TODO_RESUME_EVALUATION_REQUEST_SCHEMA,
  TODO_RESUME_EVALUATION_RESULT_SCHEMA,
  TODO_RESUME_EXTERNAL_WAIT_REQUEST_SCHEMA,
  TODO_RESUME_EXTERNAL_WAIT_RESULT_SCHEMA,
  TODO_RESUME_NORMALIZE_REQUEST_SCHEMA,
} from "../coordination/coordination_state_contract.generated.ts";

export const TODO_RESUME_NORMALIZE_REQUEST_SCHEMA_VERSION =
  TODO_RESUME_NORMALIZE_REQUEST_SCHEMA;
export const TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION =
  TODO_RESUME_EVALUATION_REQUEST_SCHEMA;
export const TODO_RESUME_EVALUATION_SCHEMA_VERSION =
  TODO_RESUME_EVALUATION_RESULT_SCHEMA;
export const TODO_EXTERNAL_WAIT_REQUEST_SCHEMA_VERSION =
  TODO_RESUME_EXTERNAL_WAIT_REQUEST_SCHEMA;
export const TODO_EXTERNAL_WAIT_TRANSITION_SCHEMA_VERSION =
  TODO_RESUME_EXTERNAL_WAIT_RESULT_SCHEMA;

export const TODO_RESUME_KINDS = [
  "todo_done",
  "pr_merged",
  "capacity_available",
  "monitor_changed",
  "resume_at",
] as const;

export const UNSUPPORTED_TODO_RESUME_CONDITION_MESSAGE =
  "unsupported Todo resume condition; supported conditions are: " +
  "todo_done:<todo_id>, monitor_changed:<monitor_todo_id>, " +
  "pr_merged:[owner/repo]#<number>, capacity_available:<capability>, " +
  "or resume_at:<timezone-aware-rfc3339-timestamp>";

type TodoResumeKind = typeof TODO_RESUME_KINDS[number];

const TODO_ID_PATTERN = /^todo_[a-z\d_-]{3,64}$/;
const CAPABILITY_PATTERN = /^[a-z][a-z\d_:-]{0,63}$/;
const RESUME_PATTERN = /^[a-z][a-z\d_-]{0,31}(?::[a-z\d_.:@#/-]{1,181})?$/;
const PR_RESUME_PATTERN =
  /^pr_merged:(?:[a-z\d_.-]{1,80}\/[a-z\d_.-]{1,100})?#[1-9]\d{0,8}$/;
const RESUME_AT_PATTERN =
  /^resume_at:(?<timestamp>[1-9]\d{3}-(?<month>\d{2})-(?<day>\d{2})[tT](?<hour>\d{2}):(?<minute>\d{2}):(?<second>\d{2})(?:\.(?<fraction>\d{1,3}))?(?<timezone>[zZ]|(?<sign>[+-])(?<offsetHour>\d{2}):(?<offsetMinute>\d{2})))$/;
const GITHUB_PULL_URL_PATTERN =
  /^https:\/\/github\.com\/([^/]+\/[^/]+)\/pull\/(\d+)(?:\b|\/|#|\?)/i;
const PR_REF_PATTERN =
  /^(?:([a-z\d_.-]+\/[a-z\d_.-]+)#|#|pr[-_\s]*)(\d+)$/i;
const PR_MERGED_EVENT_KINDS = new Set([
  "pr_merge",
  "pr_merged",
  "pull_request_merge",
  "pull_request_merged",
]);

interface ResumeSpec {
  kind: TodoResumeKind;
  target: string;
  normalized: string;
}

interface ResumeTimestamp {
  iso: string;
  milliseconds: number;
}

function normalizedTimestamp(milliseconds: number): string {
  return new Date(milliseconds).toISOString().replace(".000Z", "Z");
}

/** Strict RFC3339 date-time parsing for user-authored wakeups. A timezone is
 * mandatory; treating a missing offset as local or UTC would make two hosts
 * disagree about the same Todo. */
function resumeTimestamp(value: unknown): ResumeTimestamp | null {
  if (typeof value !== "string") return null;
  const match = RESUME_AT_PATTERN.exec(`resume_at:${value.trim()}`);
  if (!match?.groups) return null;
  const [year, month, day, hour, minute, second] = value.trim()
    .slice(0, 19)
    .split(/[-tT:]/)
    .map((part) => Number.parseInt(part, 10));
  const fraction = Number.parseInt((match.groups.fraction ?? "").padEnd(3, "0") || "0", 10);
  const local = new Date(0);
  local.setUTCFullYear(year, month - 1, day);
  local.setUTCHours(hour, minute, second, fraction);
  if (local.getUTCFullYear() !== year || local.getUTCMonth() !== month - 1 ||
      local.getUTCDate() !== day || local.getUTCHours() !== hour ||
      local.getUTCMinutes() !== minute || local.getUTCSeconds() !== second) return null;
  const offsetHour = Number.parseInt(match.groups.offsetHour ?? "0", 10);
  const offsetMinute = Number.parseInt(match.groups.offsetMinute ?? "0", 10);
  if (offsetHour > 14 || offsetMinute > 59 || (offsetHour === 14 && offsetMinute !== 0)) return null;
  const milliseconds = Date.parse(value.trim());
  if (Number.isNaN(milliseconds)) return null;
  const utcYear = new Date(milliseconds).getUTCFullYear();
  if (utcYear < 1000 || utcYear > 9999) return null;
  return { iso: normalizedTimestamp(milliseconds), milliseconds };
}

function parsedResumeAt(value: string): ResumeTimestamp | null {
  if (!value.toLowerCase().startsWith("resume_at:")) return null;
  return resumeTimestamp(value.slice("resume_at:".length));
}

interface TodoItem extends JsonObject {
  todo_id: string;
  role?: string;
  status?: string;
  task_class?: string;
  resume_when?: string;
  resume_ready?: boolean;
  resume_monitor_generation?: number;
  material_change_generation?: number;
}

function nonNegativeInteger(value: unknown, label: string): number | null {
  if (value === null || value === undefined || value === "") return null;
  const normalized = typeof value === "string" && /^\d+$/.test(value)
    ? Number.parseInt(value, 10)
    : requireInteger(value, label);
  if (!Number.isSafeInteger(normalized) || normalized < 0) {
    throw new EffectRuntimeRequestError(`${label} must be a non-negative integer`);
  }
  return normalized;
}

function todoId(value: unknown, label: string): string {
  const normalized = requireNonEmptyString(value, label).trim().toLowerCase();
  if (!TODO_ID_PATTERN.test(normalized)) {
    throw new EffectRuntimeRequestError(`${label} must be a valid todo_id`);
  }
  return normalized;
}

function optionalString(value: unknown, label: string): string | undefined {
  const normalized = optionalNonEmptyString(value, label);
  return normalized === null ? undefined : normalized.trim();
}

function todoItem(value: unknown, label: string): TodoItem {
  const raw = requireJsonObject(value, label);
  const item: TodoItem = { todo_id: todoId(raw.todo_id, `${label}.todo_id`) };
  for (const field of [
    "role",
    "status",
    "task_class",
    "archive_state",
    "source_section",
    "claimed_by",
    "task_repository",
  ] as const) {
    const normalized = optionalString(raw[field], `${label}.${field}`);
    if (normalized !== undefined) item[field] = normalized;
  }
  const resumeWhen = optionalString(raw.resume_when, `${label}.resume_when`);
  // Preserve the RFC3339 `T`/offset spelling until the typed parser has
  // normalized it. Lower-casing the entire token makes offset timestamps
  // fail strict parsing on the read path.
  if (resumeWhen !== undefined) item.resume_when = resumeWhen;
  if (typeof raw.resume_ready === "boolean") item.resume_ready = raw.resume_ready;
  const resumeGeneration = nonNegativeInteger(
    raw.resume_monitor_generation,
    `${label}.resume_monitor_generation`,
  );
  if (resumeGeneration !== null) item.resume_monitor_generation = resumeGeneration;
  const materialGeneration = nonNegativeInteger(
    raw.material_change_generation,
    `${label}.material_change_generation`,
  );
  if (materialGeneration !== null) {
    item.material_change_generation = materialGeneration;
  }
  return item;
}

function parseResumeWhen(value: unknown): ResumeSpec | null {
  if (typeof value !== "string") return null;
  const candidate = value.trim();
  const resumeAt = parsedResumeAt(candidate);
  if (resumeAt) {
    return {
      kind: "resume_at",
      target: resumeAt.iso,
      normalized: `resume_at:${resumeAt.iso}`,
    };
  }
  const normalized = candidate.toLowerCase();
  if (normalized.startsWith("resume_at:")) return null;
  if (!normalized || !RESUME_PATTERN.test(normalized)) return null;
  const separator = normalized.indexOf(":");
  if (separator < 1) return null;
  const kind = normalized.slice(0, separator);
  const target = normalized.slice(separator + 1);
  if (!TODO_RESUME_KINDS.includes(kind as TodoResumeKind)) return null;
  if ((kind === "todo_done" || kind === "monitor_changed") && !TODO_ID_PATTERN.test(target)) {
    return null;
  }
  if (kind === "capacity_available" && !CAPABILITY_PATTERN.test(target)) {
    return null;
  }
  if (kind === "pr_merged" && !PR_RESUME_PATTERN.test(normalized)) return null;
  return { kind: kind as TodoResumeKind, target, normalized };
}

function requireResumeWhen(value: unknown, label: string): ResumeSpec {
  const parsed = parseResumeWhen(value);
  if (!parsed) {
    throw new EffectRuntimeRequestError(
      `${label} must use todo_done:<todo_id>, monitor_changed:<monitor_todo_id>, ` +
        "pr_merged:[owner/repo]#<number>, capacity_available:<capability>, " +
        "or resume_at:<timezone-aware-rfc3339-timestamp>",
    );
  }
  return parsed;
}

export function normalizeTodoResumeWhen(value: unknown): string | null {
  const request = requireJsonObject(value, "todo_resume_normalize_request");
  if (request.schema_version !== TODO_RESUME_NORMALIZE_REQUEST_SCHEMA_VERSION) {
    throw new EffectRuntimeRequestError("Todo resume normalize request schema mismatch");
  }
  return parseResumeWhen(request.resume_when)?.normalized ?? null;
}

interface PrRef {
  repo: string | null;
  number: number;
  normalized: string;
}

function normalizedPrRef(value: unknown): PrRef | null {
  const candidate = typeof value === "string" ? value.trim().toLowerCase() : "";
  if (!candidate) return null;
  const pullUrl = GITHUB_PULL_URL_PATTERN.exec(candidate);
  if (pullUrl) {
    return {
      repo: pullUrl[1],
      number: Number.parseInt(pullUrl[2], 10),
      normalized: `${pullUrl[1]}#${pullUrl[2]}`,
    };
  }
  const match = PR_REF_PATTERN.exec(candidate);
  if (!match) return null;
  const repo = match[1] || null;
  const number = Number.parseInt(match[2], 10);
  return { repo, number, normalized: repo ? `${repo}#${number}` : `#${number}` };
}

function normalizedString(value: unknown): string {
  return typeof value === "string" ? value.trim().toLowerCase() : "";
}

function sourcePrRefCandidates(value: unknown): unknown[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((rawRef) => {
    if (typeof rawRef !== "object" || rawRef === null || Array.isArray(rawRef)) {
      return [];
    }
    const sourceRef = rawRef as JsonObject;
    const kind = normalizedString(sourceRef.kind);
    return kind === "pull_request" || kind === "pr" ? [sourceRef.ref] : [];
  });
}

function uniquePrRefs(candidates: unknown[]): PrRef[] {
  const refs = candidates
    .map(normalizedPrRef)
    .filter((ref): ref is PrRef => ref !== null);
  return [...new Map(
    refs.map((ref) => [`${ref.repo ?? ""}#${ref.number}`, ref]),
  ).values()];
}

function rolloutEventPrRefs(event: JsonObject): PrRef[] {
  const codeRefs = typeof event.code_refs === "object" && event.code_refs !== null &&
      !Array.isArray(event.code_refs)
    ? event.code_refs as JsonObject
    : {};
  return uniquePrRefs([
    codeRefs.pr_ref,
    event.pr_ref,
    ...sourcePrRefCandidates(event.source_refs),
  ]);
}

function githubRepository(value: unknown): string | null {
  const candidate = typeof value === "string" ? value.trim().toLowerCase() : "";
  const prefix = "git:github.com/";
  if (!candidate.startsWith(prefix)) return null;
  const repository = candidate.slice(prefix.length).replace(/\/+$/, "");
  return repository.split("/").length === 2 ? repository : null;
}

interface PrMergedEvent {
  event: JsonObject;
  refs: PrRef[];
}

function prMergedEvents(rolloutEvents: unknown[]): PrMergedEvent[] {
  return rolloutEvents.flatMap((rawEvent, index) => {
    const event = requireJsonObject(rawEvent, `rollout_events[${index}]`);
    if (!PR_MERGED_EVENT_KINDS.has(normalizedString(event.event_kind))) return [];
    return [{ event, refs: rolloutEventPrRefs(event) }];
  });
}

function resolvePrRepositoryBinding(
  targetRef: PrRef,
  item: TodoItem,
  events: PrMergedEvent[],
): { repository: string | null; projection: JsonObject } {
  if (targetRef.repo) {
    return {
      repository: targetRef.repo,
      projection: {
        pr_repo: targetRef.repo,
        repository_binding_source: "qualified_resume_when",
      },
    };
  }
  const taskRepository = githubRepository(item.task_repository);
  if (taskRepository) {
    return {
      repository: taskRepository,
      projection: {
        pr_repo: taskRepository,
        repository_binding_source: "task_repository",
      },
    };
  }
  const candidateRefs = new Set(
    events.flatMap(({ refs }) =>
      refs
        .filter((ref) => ref.number === targetRef.number && ref.repo !== null)
        .map((ref) => ref.normalized)
    ),
  );
  return {
    repository: null,
    projection: {
      repository_binding_state: "ambiguous",
      repository_binding_reason: item.task_repository
        ? "task_repository_not_github"
        : "task_repository_missing",
      candidate_pr_refs: [...candidateRefs]
        .sort((left, right) => left.localeCompare(right))
        .slice(0, 8),
    },
  };
}

function matchingPrMerge(
  events: PrMergedEvent[],
  targetRef: PrRef,
  targetRepo: string,
): JsonObject | null {
  for (const { event, refs } of events) {
    const matched = refs.find((ref) =>
      ref.number === targetRef.number && ref.repo === targetRepo
    );
    if (!matched) continue;
    return {
      satisfied: true,
      matched_event_id: event.event_id ?? null,
      matched_event_kind: event.event_kind ?? null,
      matched_pr_ref: matched.normalized,
      matched_event_at: event.recorded_at ?? null,
    };
  }
  return null;
}

/** Complete retained facts, reduced by exact dependency rather than an event
 * count. The first recorded merge remains the proof as unrelated history grows.
 * Unbound references retain only the existing eight diagnostic candidates; they
 * never become a repository binding or satisfaction evidence.
 */
export function compactTodoResumeMergeEvidence(items: unknown[], events: readonly JsonObject[]): JsonObject[] {
  const exact = new Set<string>();
  const unbound = new Set<number>();
  for (const [index, raw] of items.entries()) {
    const item = todoItem(raw, `items[${index}]`);
    const spec = parseResumeWhen(item.resume_when);
    if (spec?.kind !== "pr_merged") continue;
    const ref = normalizedPrRef(spec.target)!;
    const repo = ref.repo ?? githubRepository(item.task_repository);
    if (repo) exact.add(`${repo}#${ref.number}`);
    else unbound.add(ref.number);
  }
  const facts = new Map<string, JsonObject>();
  const diagnostics = new Map<number, Map<string, JsonObject>>();
  for (const {event, refs} of prMergedEvents([...events])) {
    for (const ref of refs) {
      const fact: JsonObject = {event_kind: normalizedString(event.event_kind), pr_ref: ref.normalized};
      for (const key of ["event_id", "recorded_at"]) {
        if (typeof event[key] === "string" && event[key].trim()) fact[key] = event[key].trim();
      }
      if (exact.has(ref.normalized) && !facts.has(ref.normalized)) facts.set(ref.normalized, fact);
      if (unbound.has(ref.number) && ref.repo !== null) {
        const candidates = diagnostics.get(ref.number) ?? new Map<string, JsonObject>();
        if (!candidates.has(ref.normalized)) candidates.set(ref.normalized, fact);
        diagnostics.set(ref.number, candidates);
      }
    }
  }
  for (const candidates of diagnostics.values()) {
    for (const ref of [...candidates.keys()].sort((a, b) => a.localeCompare(b)).slice(0, 8)) {
      if (!facts.has(ref)) facts.set(ref, candidates.get(ref)!);
    }
  }
  return [...facts.values()];
}

/** Host transport supplies the existing Goal log location, never a display
 * tail as complete evidence. The pure evaluator and all non-local callers keep
 * their existing request shape and semantics. No Todo or log is written here.
 */
export async function readTodoResumeRolloutEvents(request: JsonObject): Promise<readonly JsonObject[]> {
  if (request.rollout_event_source === undefined) {
    return Array.isArray(request.rollout_events) ? request.rollout_events as JsonObject[] : [];
  }
  const source = requireJsonObject(request.rollout_event_source, "rollout_event_source");
  const runtimeRoot = requireNonEmptyString(source.runtime_root, "runtime_root");
  const goalId = requireNonEmptyString(source.goal_id, "goal_id");
  return (await readGoalRolloutEventSnapshot(runtimeRoot, goalId))?.events ?? [];
}

export async function evaluateTodoResumeConditionsFromSource(value: unknown): Promise<JsonObject> {
  const request = requireJsonObject(value, "todo_resume_evaluation_request");
  const evaluated = evaluateTodoResumeConditions(request); // Reject bad input before IO.
  if (request.rollout_event_source === undefined) return evaluated;
  const items = request.items as JsonObject[];
  const waits = items.some(raw => parseResumeWhen(raw.resume_when)?.kind === "pr_merged");
  if (!waits) return evaluated;
  return evaluateTodoResumeConditions({...request, rollout_events: [
    ...compactTodoResumeMergeEvidence(items, await readTodoResumeRolloutEvents(request)),
    ...(Array.isArray(request.rollout_events) ? request.rollout_events : []),
  ]});
}

function prMergedCondition(
  spec: ResumeSpec,
  item: TodoItem,
  rolloutEvents: unknown[],
): JsonObject {
  const condition: JsonObject = {
    pr_number: null,
    pr_repo: null,
    source: "rollout_event_log",
  };
  const targetRef = normalizedPrRef(spec.target);
  if (!targetRef) return { ...condition, invalid_target: true };
  condition.pr_number = targetRef.number;
  const events = prMergedEvents(rolloutEvents);
  const binding = resolvePrRepositoryBinding(targetRef, item, events);
  const boundCondition = { ...condition, ...binding.projection };
  if (!binding.repository) return boundCondition;
  const matched = matchingPrMerge(events, targetRef, binding.repository);
  return matched ? { ...boundCondition, ...matched } : boundCondition;
}

function conditionFor(
  item: TodoItem,
  spec: ResumeSpec,
  byId: Map<string, TodoItem>,
  rolloutEvents: unknown[],
  availableCapabilities: Set<string> | null,
  evaluatedAt: ResumeTimestamp | null,
): JsonObject {
  const condition: JsonObject = {
    schema_version: "todo_resume_condition_v0",
    resume_when: spec.normalized,
    kind: spec.kind,
    target: spec.target,
    satisfied: false,
  };
  if (spec.kind === "todo_done") {
    const target = byId.get(spec.target);
    condition.target_todo_id = spec.target;
    condition.target_status = target?.status ?? null;
    if (target) {
      condition.target_archive_state = target.archive_state ?? null;
      condition.target_source_section = target.source_section ?? null;
      condition.target_task_class = target.task_class ?? null;
      if (target.claimed_by) condition.target_claimed_by = target.claimed_by;
    }
    condition.satisfied = target?.status === "done";
    return condition;
  }
  if (spec.kind === "pr_merged") {
    return { ...condition, ...prMergedCondition(spec, item, rolloutEvents) };
  }
  if (spec.kind === "capacity_available") {
    condition.provider = "runtime_available_capabilities";
    condition.provider_required = availableCapabilities === null;
    condition.capability = spec.target;
    condition.satisfied = availableCapabilities?.has(spec.target) === true;
    return condition;
  }
  if (spec.kind === "resume_at") {
    const scheduled = resumeTimestamp(spec.target);
    if (!scheduled || !evaluatedAt) {
      return { ...condition, invalid_state: "runtime_clock_unavailable" };
    }
    const satisfied = evaluatedAt.milliseconds >= scheduled.milliseconds;
    const receiptId = `resume_at_${createHash("sha256")
      .update(`${item.todo_id}\n${spec.normalized}`, "utf8")
      .digest("hex")
      .slice(0, 24)}`;
    return {
      ...condition,
      satisfied,
      scheduled_for: scheduled.iso,
      evaluated_at: evaluatedAt.iso,
      clock_provider: "runtime_clock",
      generation_fence: "once_at_or_after_scheduled_for",
      material_change: satisfied,
      material_change_generation: satisfied ? 1 : 0,
      resume_receipt: satisfied ? {
        schema_version: "todo_resume_receipt_v0",
        receipt_id: receiptId,
        idempotency_key: receiptId,
        todo_id: item.todo_id,
        resume_when: spec.normalized,
        condition_kind: spec.kind,
        triggered_at: scheduled.iso,
        material_change_generation: 1,
      } : null,
    };
  }
  const monitor = byId.get(spec.target);
  const baseline = item.resume_monitor_generation;
  const generation = monitor?.material_change_generation ?? 0;
  condition.target_todo_id = spec.target;
  condition.target_status = monitor?.status ?? null;
  condition.target_task_class = monitor?.task_class ?? null;
  condition.baseline_generation = baseline ?? null;
  condition.material_change_generation = generation;
  condition.generation_fence = "strictly_greater_than_baseline";
  if (!monitor) condition.invalid_state = "monitor_not_found";
  else if (monitor.task_class !== "continuous_monitor") {
    condition.invalid_state = "target_not_continuous_monitor";
  } else if (baseline === undefined) {
    condition.invalid_state = "baseline_generation_missing";
  } else {
    condition.satisfied = generation > baseline;
  }
  return condition;
}

export type ResumeConditionDiagnosis = {
  kind: TodoResumeKind | null;
} & (
  | { state: "satisfied" | "pending" }
  | { state: "invalid"; reason: string }
);

/** Shared by canonical evaluation and old compact projections. Missing source
 * facts are not proof of an invalid dependency. A historical completed monitor
 * remains satisfied; a live monitor completion wait requires an explicit replan,
 * never an inferred monitor_changed generation baseline. */
export function diagnoseTodoResumeCondition(
  condition: JsonObject, waitingTodoId: string | null = null,
): ResumeConditionDiagnosis {
  const parsed = parseResumeWhen(condition.resume_when);
  const kind = TODO_RESUME_KINDS.find((value) => value === condition.kind) ?? parsed?.kind ?? null;
  const targetId = condition.target_todo_id ?? condition.target ?? parsed?.target;
  if ((kind === "todo_done" || kind === "monitor_changed") && waitingTodoId && targetId === waitingTodoId) {
    return { kind, state: "invalid", reason: "dependency_self_reference" };
  }
  if (typeof condition.invalid_state === "string") {
    return { kind, state: "invalid", reason: condition.invalid_state };
  }
  if (condition.invalid_target === true) return { kind, state: "invalid", reason: "invalid_target" };
  if (kind === "todo_done" && condition.target_task_class === "continuous_monitor"
    && condition.target_status && condition.target_status !== "done") {
    return { kind, state: "invalid", reason: "monitor_completion_requires_replan" };
  }
  return { kind, state: condition.satisfied === true ? "satisfied" : "pending" };
}

function diagnosedCondition(condition: JsonObject, waitingTodoId: string): JsonObject {
  const diagnosis = diagnoseTodoResumeCondition(condition, waitingTodoId);
  return {
    ...condition,
    availability_reason: `resume_condition_${diagnosis.state}`,
    ...(diagnosis.state === "invalid" ? { satisfied: false, invalid_state: diagnosis.reason } : {}),
  };
}

/** Positive wait proof for consumers that may relax supervision. Historical
 * absence of an invalid marker is not proof of a valid, identified target. */
export function resumeConditionHasKnownPendingTarget(condition: JsonObject, waitingTodo: JsonObject): boolean {
  const spec = parseResumeWhen(waitingTodo.resume_when);
  if (!spec || condition.resume_when !== spec.normalized || condition.kind !== spec.kind
    || (condition.target !== undefined && condition.target !== spec.target)) return false;
  if (condition.schema_version !== "todo_resume_condition_v0" || condition.satisfied !== false
    || diagnoseTodoResumeCondition(condition, String(waitingTodo.todo_id)).state !== "pending") return false;
  if ((spec.kind === "todo_done" || spec.kind === "monitor_changed")
    && (condition.target_todo_id !== spec.target || spec.target === waitingTodo.todo_id)) return false;
  switch (spec.kind) {
    case "todo_done":
      return ["open", "deferred"].includes(String(condition.target_status))
        && ["advancement_task", "user_gate", "user_action", "blocker"].includes(String(condition.target_task_class))
        && (condition.target_archive_state === null || condition.target_archive_state === "active");
    case "monitor_changed":
      return condition.target_task_class === "continuous_monitor" && condition.target_status === "open"
        && typeof condition.baseline_generation === "number" && Number.isSafeInteger(condition.baseline_generation)
        && condition.baseline_generation >= 0 && condition.baseline_generation === waitingTodo.resume_monitor_generation
        && typeof condition.material_change_generation === "number"
        && Number.isSafeInteger(condition.material_change_generation) && condition.material_change_generation >= 0
        && condition.material_change_generation <= condition.baseline_generation;
    case "capacity_available": return condition.provider_required === false
      && condition.provider === "runtime_available_capabilities" && condition.capability === spec.target;
    case "pr_merged": {
      // Binding identifies what to observe; absence of a merge event does not
      // establish that GitHub is still pending. Match blocked-wait settlement:
      // only a registered causal monitor/Todo can relax delivery supervision.
      return false;
    }
    case "resume_at": {
      const scheduled = resumeTimestamp(spec.target);
      const evaluated = resumeTimestamp(String(condition.evaluated_at ?? ""));
      return scheduled !== null && evaluated !== null
        && condition.clock_provider === "runtime_clock"
        && condition.scheduled_for === scheduled.iso
        && condition.material_change === false
        && condition.material_change_generation === 0
        && condition.resume_receipt === null
        && evaluated.milliseconds < scheduled.milliseconds;
    }
    default: return false;
  }
}

export function evaluateTodoResumeConditions(value: unknown): JsonObject {
  const request = requireJsonObject(value, "todo_resume_evaluation_request");
  if (request.schema_version !== TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION) {
    throw new EffectRuntimeRequestError("Todo resume evaluation request schema mismatch");
  }
  if (!Array.isArray(request.items) || !Array.isArray(request.source_items)) {
    throw new EffectRuntimeRequestError("Todo resume evaluation items must be arrays");
  }
  const items = request.items.map((item, index) =>
    todoItem(item, `todo_resume_evaluation_request.items[${index}]`)
  );
  const sourceItems = request.source_items.map((item, index) =>
    todoItem(item, `todo_resume_evaluation_request.source_items[${index}]`)
  );
  const rolloutEvents = Array.isArray(request.rollout_events)
    ? request.rollout_events
    : [];
  const availableCapabilities = request.available_capabilities === undefined ||
      request.available_capabilities === null
    ? null
    : new Set(requireStringArray(
      request.available_capabilities,
      "todo_resume_evaluation_request.available_capabilities",
    ).map((item) => item.trim().toLowerCase()));
  const requestedKinds = request.kinds === undefined || request.kinds === null
    ? null
    : new Set(requireStringArray(request.kinds, "todo_resume_evaluation_request.kinds"));
  const needsRuntimeClock = items.some((item) => {
    const spec = parseResumeWhen(item.resume_when);
    return spec?.kind === "resume_at" && (!requestedKinds || requestedKinds.has(spec.kind));
  });
  const evaluatedAt = needsRuntimeClock ? resumeTimestamp(request.evaluated_at) : null;
  if (needsRuntimeClock && !evaluatedAt) {
    throw new EffectRuntimeRequestError(
      "todo_resume_evaluation_request.evaluated_at must be a timezone-aware RFC3339 timestamp",
    );
  }
  const byId = new Map<string, TodoItem>();
  for (const item of [...sourceItems, ...items]) byId.set(item.todo_id, item);
  const conditions: JsonObject[] = [];
  for (const item of items) {
    const spec = parseResumeWhen(item.resume_when);
    if (!spec || (requestedKinds && !requestedKinds.has(spec.kind))) continue;
    const condition = conditionFor(
      item,
      spec,
      byId,
      rolloutEvents,
      availableCapabilities,
      evaluatedAt,
    );
    conditions.push({
      todo_id: item.todo_id,
      condition: diagnosedCondition(condition, item.todo_id),
    });
  }
  return {
    schema_version: TODO_RESUME_EVALUATION_SCHEMA_VERSION,
    conditions,
  };
}

function externalWaitItems(request: JsonObject): TodoItem[] {
  if (!Array.isArray(request.items)) {
    throw new EffectRuntimeRequestError(
      "todo_external_wait_request.items must be an array",
    );
  }
  return request.items.map((item, index) =>
    todoItem(item, `todo_external_wait_request.items[${index}]`)
  );
}

function externalWaitTodo(
  request: JsonObject,
  byId: Map<string, TodoItem>,
): { todoId: string; todo: TodoItem } {
  const requestedId = todoId(
    request.todo_id,
    "todo_external_wait_request.todo_id",
  );
  const todo = byId.get(requestedId);
  if (!todo) {
    throw new EffectRuntimeRequestError(
      "external-wait Todo is absent from current state",
      "external_wait_todo_absent",
    );
  }
  if (todo.role !== "agent") {
    throw new EffectRuntimeRequestError(
      "external-wait Todo must have role=agent",
      "external_wait_todo_role_invalid",
    );
  }
  if (todo.status !== "open") {
    throw new EffectRuntimeRequestError(
      "external-wait Todo must remain status=open; resume_when excludes it from runnable selection until the condition is satisfied",
      "external_wait_todo_status_must_remain_open",
    );
  }
  if (todo.task_class !== "advancement_task") {
    throw new EffectRuntimeRequestError(
      "external-wait Todo must have task_class=advancement_task",
      "external_wait_todo_task_class_invalid",
    );
  }
  return { todoId: requestedId, todo };
}

function externalWaitDependency(
  spec: ResumeSpec,
  waitingTodoId: string,
  byId: Map<string, TodoItem>,
): TodoItem {
  if (spec.kind !== "todo_done" && spec.kind !== "monitor_changed") {
    throw new EffectRuntimeRequestError(
      "external-wait transition supports todo_done or monitor_changed; use ordinary " +
        "resume_when authoring for PR and capacity conditions",
      "external_wait_resume_kind_invalid",
    );
  }
  if (spec.target === waitingTodoId) {
    throw new EffectRuntimeRequestError(
      "external-wait Todo cannot resume from itself",
      "external_wait_dependency_self_reference",
    );
  }
  const dependency = byId.get(spec.target);
  if (!dependency) {
    throw new EffectRuntimeRequestError(
      "external-wait dependency is absent from current state",
      "external_wait_dependency_absent",
    );
  }
  if (spec.kind === "todo_done" && dependency.task_class === "continuous_monitor") {
    throw new EffectRuntimeRequestError(
      "todo_done cannot wait on a continuous_monitor; use monitor_changed:<todo_id>",
      "external_wait_monitor_condition_required",
    );
  }
  if (spec.kind === "todo_done" && dependency.status === "done") {
    throw new EffectRuntimeRequestError(
      "todo_done dependency is already complete",
      "external_wait_dependency_already_complete",
    );
  }
  if (
    spec.kind === "monitor_changed" &&
    (dependency.status !== "open" || dependency.task_class !== "continuous_monitor")
  ) {
    throw new EffectRuntimeRequestError(
      "monitor_changed requires an open continuous_monitor target",
      "external_wait_monitor_target_invalid",
    );
  }
  return dependency;
}

function externalWaitSuccessors(
  request: JsonObject,
  waitingTodoId: string,
  byId: Map<string, TodoItem>,
): string[] {
  const successorIds = requireStringArray(
    request.successor_todo_ids,
    "todo_external_wait_request.successor_todo_ids",
  ).map((item, index) => todoId(item, `successor_todo_ids[${index}]`));
  const successors = [...new Set(successorIds)];
  if (successors.length === 0) {
    throw new EffectRuntimeRequestError(
      "external-wait transition requires at least one independent runnable successor",
      "external_wait_successor_required",
    );
  }
  for (const successorId of successors) {
    const successor = byId.get(successorId);
    if (!successor || successorId === waitingTodoId) {
      throw new EffectRuntimeRequestError(
        "external-wait successor is absent or self-referential",
        "external_wait_successor_absent_or_self",
      );
    }
    if (successor.status !== "open" || successor.task_class !== "advancement_task") {
      throw new EffectRuntimeRequestError(
        "external-wait successor must be an open advancement_task",
        "external_wait_successor_not_open_advancement",
      );
    }
    if (successor.resume_when && successor.resume_ready !== true) {
      throw new EffectRuntimeRequestError(
        "external-wait successor must be runnable, not resume-gated",
        "external_wait_successor_resume_gated",
      );
    }
  }
  return successors;
}

function externalWaitMetadata(
  waitingTodo: TodoItem,
  dependency: TodoItem,
  spec: ResumeSpec,
  byId: Map<string, TodoItem>,
): { updates: JsonObject; baselineGeneration: number | null } {
  const updates: JsonObject = { resume_when: spec.normalized };
  if (spec.kind !== "monitor_changed") {
    updates.resume_monitor_generation = null;
    return { updates, baselineGeneration: null };
  }
  const sameCondition = waitingTodo.resume_when === spec.normalized;
  const currentCondition = conditionFor(waitingTodo, spec, byId, [], null, null);
  if (sameCondition && currentCondition.satisfied === true) {
    throw new EffectRuntimeRequestError(
      "clear the satisfied resume_when before re-arming the same monitor wait",
      "external_wait_satisfied_condition_requires_clear",
    );
  }
  const baselineGeneration =
    sameCondition && waitingTodo.resume_monitor_generation !== undefined
      ? waitingTodo.resume_monitor_generation
      : dependency.material_change_generation ?? 0;
  updates.resume_monitor_generation = baselineGeneration;
  return { updates, baselineGeneration };
}

export function planTodoExternalWaitTransition(value: unknown): JsonObject {
  const request = requireJsonObject(value, "todo_external_wait_request");
  if (request.schema_version !== TODO_EXTERNAL_WAIT_REQUEST_SCHEMA_VERSION) {
    throw new EffectRuntimeRequestError("Todo external-wait request schema mismatch");
  }
  const byId = new Map<string, TodoItem>(
    externalWaitItems(request).map((item) => [item.todo_id, item]),
  );
  const waiting = externalWaitTodo(request, byId);
  const spec = requireResumeWhen(
    request.resume_when,
    "todo_external_wait_request.resume_when",
  );
  const dependency = externalWaitDependency(spec, waiting.todoId, byId);
  const successors = externalWaitSuccessors(request, waiting.todoId, byId);
  const metadata = externalWaitMetadata(waiting.todo, dependency, spec, byId);
  return {
    schema_version: TODO_EXTERNAL_WAIT_TRANSITION_SCHEMA_VERSION,
    state: waiting.todo.resume_when === spec.normalized ? "already_waiting" : "waiting",
    todo_id: waiting.todoId,
    resume_when: spec.normalized,
    resume_kind: spec.kind,
    dependency_todo_id: spec.target,
    successor_todo_ids: successors,
    baseline_generation: metadata.baselineGeneration,
    metadata_updates: metadata.updates,
    runnable_state: "excluded_until_resume_condition_satisfied",
    idempotency: "preserve_existing_monitor_baseline",
  };
}
