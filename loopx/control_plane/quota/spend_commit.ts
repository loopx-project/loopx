import { EffectiveAction, type EffectiveActionValue } from "./effective_action.generated.ts";
import { createHash } from "node:crypto";
import { basename, isAbsolute, join } from "node:path";

import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  commitQuotaAccountingArtifactTransaction,
  lookupQuotaAccountingReplay,
  nextQuotaAccountingArtifactPaths,
  quotaAccountingIndexDigest,
  renderQuotaSlotMarkdown,
} from "./accounting_artifact_transaction.ts";
import {
  jsonObject,
  optionalNonEmptyString as optionalString,
  requireBoolean as requiredBoolean,
  requireInteger as requiredInteger,
  requireJsonObject as requiredObject,
  requireNonEmptyString as requiredString,
  requireStringLiteral,
} from "../runtime_decode.ts";
import {
  parseQuotaAccountingOwner,
  quotaGoalRef,
  withQuotaAccountingOwner,
  type QuotaAccountingOwner,
} from "./source_admission.ts";

export const QUOTA_SPEND_COMMIT_REQUEST_SCHEMA =
  "loopx_quota_spend_commit_request_v0";
export const QUOTA_SPEND_COMMIT_RESULT_SCHEMA =
  "loopx_quota_spend_commit_result_v0";
export const QUOTA_SPEND_COMMIT_RECEIPT_SCHEMA =
  "quota_spend_commit_receipt_v0";
export const QUOTA_SLOT_SPENT_CLASSIFICATION = "quota_slot_spent";
export const QUOTA_SLOT_ACCOUNTING_PROJECTION_SCHEMA =
  "quota_slot_accounting_projection_v0";

const ROLLING_WINDOW_NOTE =
  "before -> after is a same-status-payload projection. Later quota status " +
  "recomputes spent_slots from quota_slot_spent events still inside window_hours, " +
  "so the visible total can stay flat or decrease as older spends expire; that " +
  "does not replay or undo the appended settlement event.";

export const QUOTA_SPEND_SOURCES = [
  "heartbeat",
  "controller",
  "adapter",
  "visible-goal",
] as const;
export type QuotaSpendSource = (typeof QUOTA_SPEND_SOURCES)[number];

const SELF_REPAIR_SPEND_ACTIONS: ReadonlySet<string> = new Set<EffectiveActionValue>([
  EffectiveAction.CONTROL_PLANE_HEALTH_REPAIR,
  EffectiveAction.CONTROL_PLANE_PROJECTION_REPAIR,
  EffectiveAction.STATE_PROJECTION_GAP_REPAIR,
  EffectiveAction.BOUNDARY_PROJECTION_REPAIR,
  EffectiveAction.TODO_DECISION_SCOPE_PROJECTION_REPAIR,
]);

type QuotaSpendCommitStatus =
  | "preview"
  | "written"
  | "replayed"
  | "repaired"
  | "conflict";

interface CompactQuotaDecision extends JsonObject {
  should_run: boolean;
  normal_delivery_allowed: boolean;
  recovery_delivery_allowed: boolean;
  effective_action: string | null;
  self_repair_allowed: boolean;
  capability_repair_allowed: boolean;
  workspace_repair_allowed: boolean;
  state: string;
  safe_bypass_allowed: boolean;
  safe_bypass_kind: string | null;
  blocked_action_scope: string | null;
  compute: unknown;
  window_hours: unknown;
  slot_minutes: unknown;
  spent_slots: number;
  allowed_slots: unknown;
}

interface QuotaSpendCommitRequest {
  schema_version: typeof QUOTA_SPEND_COMMIT_REQUEST_SCHEMA;
  effect_id: string;
  runtime_root: string | null;
  goal_id: string;
  source: QuotaSpendSource;
  generated_at: string;
  execute: boolean;
  expected_index_digest: string | null;
  preview: JsonObject;
  before: CompactQuotaDecision;
  after: CompactQuotaDecision;
  resolved_agent_id: string | null;
  owner: QuotaAccountingOwner;
}

interface QuotaSpendReplayRequest {
  schema_version: typeof QUOTA_SPEND_COMMIT_REQUEST_SCHEMA;
  operation: "replay";
  runtime_root: string;
  goal_id: string;
  effect_id: string;
  resolved_agent_id: string | null;
  read_only: boolean;
  owner: QuotaAccountingOwner;
}

type SpendDisposition =
  | "delivery_completion"
  | "eligible"
  | "outcome_floor_recovery"
  | "control_plane_self_repair"
  | "capability_repair"
  | "safe_bypass";

export interface QuotaSpendCommitResult extends JsonObject {
  schema_version: typeof QUOTA_SPEND_COMMIT_RESULT_SCHEMA;
  effect_id: string;
  status: QuotaSpendCommitStatus;
  written: boolean;
  replayed: boolean;
  repaired: boolean;
  conflict: boolean;
  request_digest: string;
  index_digest: string | null;
  reason: string;
  record: JsonObject | null;
  payload: JsonObject;
  reason_code?: string;
}

function stableValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stableValue);
  const object = jsonObject(value);
  if (!object) return value;
  return Object.fromEntries(
    Object.entries(object)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, child]) => [key, stableValue(child)]),
  );
}

function canonicalJson(value: unknown): string {
  return JSON.stringify(stableValue(value));
}

function sha256(value: string): string {
  return `sha256:${createHash("sha256").update(value, "utf8").digest("hex")}`;
}

function safeGoalId(value: unknown): string {
  const goalId = requiredString(value, "goal_id").trim();
  if (goalId === "." || goalId === ".." || goalId.includes("/") || goalId.includes("\\")) {
    throw new EffectRuntimeRequestError("goal_id must be a single path segment");
  }
  if (basename(goalId) !== goalId) {
    throw new EffectRuntimeRequestError("goal_id must not include path traversal");
  }
  return goalId;
}

function optionalInteger(value: unknown, label: string): number | null {
  if (value === null || value === undefined) return null;
  return requiredInteger(value, label);
}

function compactDecision(value: unknown, label: string): CompactQuotaDecision {
  const decision = requiredObject(value, label);
  return {
    should_run: requiredBoolean(decision.should_run, `${label}.should_run`),
    normal_delivery_allowed: requiredBoolean(
      decision.normal_delivery_allowed,
      `${label}.normal_delivery_allowed`,
    ),
    recovery_delivery_allowed: requiredBoolean(
      decision.recovery_delivery_allowed,
      `${label}.recovery_delivery_allowed`,
    ),
    effective_action: optionalString(
      decision.effective_action,
      `${label}.effective_action`,
    ),
    self_repair_allowed: requiredBoolean(
      decision.self_repair_allowed,
      `${label}.self_repair_allowed`,
    ),
    capability_repair_allowed: requiredBoolean(
      decision.capability_repair_allowed,
      `${label}.capability_repair_allowed`,
    ),
    workspace_repair_allowed: requiredBoolean(
      decision.workspace_repair_allowed,
      `${label}.workspace_repair_allowed`,
    ),
    state: typeof decision.state === "string" ? decision.state : "",
    safe_bypass_allowed: requiredBoolean(
      decision.safe_bypass_allowed,
      `${label}.safe_bypass_allowed`,
    ),
    safe_bypass_kind: optionalString(
      decision.safe_bypass_kind,
      `${label}.safe_bypass_kind`,
    ),
    blocked_action_scope: optionalString(
      decision.blocked_action_scope,
      `${label}.blocked_action_scope`,
    ),
    compute: decision.compute ?? null,
    window_hours: decision.window_hours ?? null,
    slot_minutes: decision.slot_minutes ?? null,
    spent_slots: optionalInteger(decision.spent_slots, `${label}.spent_slots`) ?? 0,
    allowed_slots: decision.allowed_slots ?? null,
  };
}

function replayRequestObject(value: unknown): QuotaSpendReplayRequest {
  const request = requiredObject(value, "quota.spend.commit replay params");
  if (request.schema_version !== QUOTA_SPEND_COMMIT_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError("Quota spend commit request schema mismatch");
  }
  if (request.operation !== "replay") {
    throw new EffectRuntimeRequestError("quota spend replay operation is invalid");
  }
  const runtimeRoot = requiredString(request.runtime_root, "runtime_root").trim();
  if (!isAbsolute(runtimeRoot)) {
    throw new EffectRuntimeRequestError("runtime_root must be absolute");
  }
  const goalId = safeGoalId(request.goal_id);
  return {
    schema_version: QUOTA_SPEND_COMMIT_REQUEST_SCHEMA,
    operation: "replay",
    runtime_root: runtimeRoot,
    goal_id: goalId,
    effect_id: requiredString(request.effect_id, "effect_id").trim(),
    resolved_agent_id: optionalString(
      request.resolved_agent_id,
      "resolved_agent_id",
    )?.trim() ?? null,
    read_only: request.read_only === undefined
      ? false
      : requiredBoolean(request.read_only, "read_only"),
    owner: parseQuotaAccountingOwner({
      goalRefValue: request.goal_ref,
      sourceAdmissionValue: request.source_admission,
      runtimeRoot,
      goalId,
    }),
  };
}

function requestObject(value: unknown): QuotaSpendCommitRequest {
  const request = requiredObject(value, "quota.spend.commit params");
  if (request.schema_version !== QUOTA_SPEND_COMMIT_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError("Quota spend commit request schema mismatch");
  }
  const execute = requiredBoolean(request.execute, "execute");
  const runtimeRoot = optionalString(request.runtime_root, "runtime_root")?.trim() ?? null;
  if (execute && !runtimeRoot) {
    throw new EffectRuntimeRequestError("executing quota spend commit requires runtime_root");
  }
  const preview = requiredObject(request.preview, "preview");
  const before = compactDecision(request.before, "before");
  const after = compactDecision(request.after, "after");
  const goalId = safeGoalId(request.goal_id);
  const source = requireStringLiteral(
    request.source,
    QUOTA_SPEND_SOURCES,
    "source",
    `quota slot spend source must be one of: ${QUOTA_SPEND_SOURCES.join(", ")}`,
  );
  const requestedEffectId = optionalString(request.effect_id, "effect_id")?.trim();
  const owner = parseQuotaAccountingOwner({
    goalRefValue: request.goal_ref,
    sourceAdmissionValue: request.source_admission,
    runtimeRoot: runtimeRoot ?? "",
    goalId,
  });
  return {
    schema_version: QUOTA_SPEND_COMMIT_REQUEST_SCHEMA,
    effect_id: requestedEffectId || derivedEffectId(
      preview,
      goalId,
      source,
      before,
      after,
    ),
    runtime_root: runtimeRoot,
    goal_id: goalId,
    source,
    generated_at: requiredString(request.generated_at, "generated_at").trim(),
    execute,
    expected_index_digest: optionalString(
      request.expected_index_digest,
      "expected_index_digest",
    ),
    preview,
    before,
    after,
    resolved_agent_id: optionalString(
      request.resolved_agent_id,
      "resolved_agent_id",
    )?.trim() ?? null,
    owner,
  };
}

function derivedEffectId(
  preview: JsonObject,
  goalId: string,
  source: QuotaSpendSource,
  before: CompactQuotaDecision,
  after: CompactQuotaDecision,
): string {
  const effectRef = optionalString(preview.effect_ref, "preview.effect_ref")?.trim();
  if (effectRef) return effectRef;
  const identity = jsonObject(preview.settlement_identity);
  const settlementEffectId = identity
    ? optionalString(identity.effect_id, "preview.settlement_identity.effect_id")?.trim()
    : null;
  if (settlementEffectId) return `${settlementEffectId}#quota_spend`;
  const digest = sha256(canonicalJson({
    goal_id: goalId,
    agent_id: preview.agent_id ?? null,
    todo_id: preview.todo_id ?? null,
    replan_obligation_id: preview.replan_obligation_id ?? null,
    turn_instance_id: preview.turn_instance_id ?? null,
    source,
    slots: preview.slots,
    before,
    after,
    delivery_completion_spend: preview.delivery_completion_spend === true,
    delivery_run_generated_at: preview.delivery_run_generated_at ?? null,
    delivery_run_classification: preview.delivery_run_classification ?? null,
  }));
  return `quota-spend:${digest.slice("sha256:".length)}`;
}

function requestDigest(request: QuotaSpendCommitRequest): string {
  const goalRef = quotaGoalRef(request.owner);
  return sha256(canonicalJson({
    schema_version: request.schema_version,
    effect_id: request.effect_id,
    runtime_root: request.runtime_root,
    goal_id: request.goal_id,
    source: request.source,
    // generated_at, execute, and the CAS observation are transport facts.
    // A retry of the same semantic effect may observe a later clock/index
    // after a response loss; the prepared receipt must remain repairable.
    preview: request.preview,
    before: request.before,
    after: request.after,
    resolved_agent_id: request.resolved_agent_id,
    ...(goalRef === null ? {} : { goal_ref: goalRef }),
  }));
}

function positiveSlots(preview: JsonObject): number {
  const value = preview.slots;
  if (typeof value !== "number" || !Number.isInteger(value) || value < 1) {
    throw new EffectRuntimeRequestError("preview.slots must be a positive integer");
  }
  return value;
}

function spendDisposition(request: QuotaSpendCommitRequest): SpendDisposition {
  if (request.preview.ok !== true) {
    throw new EffectRuntimeRequestError(
      typeof request.preview.reason === "string"
        ? request.preview.reason
        : "quota slot spend requires an eligible preview",
    );
  }
  const slots = positiveSlots(request.preview);
  if (request.after.spent_slots !== request.before.spent_slots + slots) {
    throw new EffectRuntimeRequestError(
      "after.spent_slots must equal before.spent_slots + slots",
    );
  }
  const action = request.before.effective_action;
  const deliveryCompletionSpend = request.preview.delivery_completion_spend === true;
  const selfRepairSpend = request.before.should_run &&
    action !== null && SELF_REPAIR_SPEND_ACTIONS.has(action) &&
    request.before.self_repair_allowed;
  const capabilityRepairSpend = request.before.should_run &&
    action === EffectiveAction.CAPABILITY_BRIDGE_REPAIR &&
    request.before.capability_repair_allowed;
  const eligibleSpend = request.before.should_run &&
    request.before.state === "eligible" &&
    action !== EffectiveAction.EXTERNAL_EVIDENCE_OBSERVE &&
    !selfRepairSpend && !capabilityRepairSpend &&
    !request.before.workspace_repair_allowed && !deliveryCompletionSpend;
  const safeBypassSpend = request.preview.safe_bypass_spend === true &&
    (
      request.before.state === "operator_gate" ||
      request.before.recovery_delivery_allowed ||
      action === EffectiveAction.OUTCOME_FLOOR_RECOVERY
    ) && request.before.safe_bypass_allowed;
  // A recovered settlement describes work that already happened. The current
  // frontier may now ask for capability or control-plane repair, but that later
  // projection cannot rewrite the attribution of the completed delivery.
  if (deliveryCompletionSpend) return "delivery_completion";
  if (eligibleSpend) return "eligible";
  if (safeBypassSpend && action === EffectiveAction.OUTCOME_FLOOR_RECOVERY) {
    return "outcome_floor_recovery";
  }
  if (selfRepairSpend) return "control_plane_self_repair";
  if (capabilityRepairSpend) return "capability_repair";
  if (safeBypassSpend) return "safe_bypass";
  throw new EffectRuntimeRequestError(
    "quota slot spend requires an eligible, safe-bypass, control-plane self-repair, " +
      "capability bridge repair, or latest validated delivery-completion quota should-run decision",
  );
}

function reasonSummary(
  request: QuotaSpendCommitRequest,
  disposition: SpendDisposition,
  slots: number,
): string {
  switch (disposition) {
    case "eligible":
      return `${slots} automatic agent slot(s) completed under an eligible quota guard`;
    case "outcome_floor_recovery":
      return `${slots} automatic agent slot(s) completed as outcome-floor recovery safe-bypass work`;
    case "control_plane_self_repair":
      return `${slots} automatic agent slot(s) completed as control-plane self-repair work`;
    case "capability_repair":
      return `${slots} automatic agent slot(s) completed as capability bridge repair work`;
    case "delivery_completion":
      return `${slots} automatic agent slot(s) accounted after validated delivery ${optionalString(request.preview.delivery_run_classification, "preview.delivery_run_classification") ?? ""}`;
    case "safe_bypass":
      return `${slots} automatic agent slot(s) completed as safe-bypass work`;
  }
}

function healthCheck(
  disposition: SpendDisposition,
): string {
  switch (disposition) {
    case "eligible":
      return "quota should-run eligible; quota slot spend event public-safe";
    case "outcome_floor_recovery":
      return "quota outcome-floor recovery safe-bypass; quota slot spend event public-safe";
    case "control_plane_self_repair":
      return "quota control-plane self-repair; quota slot spend event public-safe";
    case "capability_repair":
      return "quota capability bridge repair; quota slot spend event public-safe";
    case "delivery_completion":
      return "quota validated delivery completion; quota slot spend event public-safe";
    case "safe_bypass":
      return "quota safe-bypass delivery; quota slot spend event public-safe";
  }
}

function accountingProjection(request: QuotaSpendCommitRequest): JsonObject {
  return {
    schema_version: QUOTA_SLOT_ACCOUNTING_PROJECTION_SCHEMA,
    settlement_event_semantics: "append_only",
    spent_slots_semantics: "rolling_window_aggregate",
    before_after_semantics: "same_status_payload_projection",
    window_hours: request.before.window_hours,
  };
}

function buildSpendRecord(
  request: QuotaSpendCommitRequest,
  fingerprint: string,
): JsonObject {
  const disposition = spendDisposition(request);
  const slots = positiveSlots(request.preview);
  const deliveryAction = typeof request.preview.delivery_run_recommended_action === "string"
    ? request.preview.delivery_run_recommended_action.trim()
    : "";
  const recommendedAction = disposition === "delivery_completion" && deliveryAction
    ? deliveryAction
    : typeof request.preview.after_recommended_action === "string"
    ? request.preview.after_recommended_action
    : "inspect next quota should-run decision";
  const quotaEvent: JsonObject = {
    event_type: QUOTA_SLOT_SPENT_CLASSIFICATION,
    source: request.source,
    todo_id: request.preview.todo_id ?? null,
    replan_obligation_id: request.preview.replan_obligation_id ?? null,
    turn_instance_id: request.preview.turn_instance_id ?? null,
    settlement_identity: request.preview.settlement_identity ?? null,
    effect_ref: request.preview.effect_ref ?? null,
    slots,
    reason_summary: reasonSummary(request, disposition, slots),
    delivery_run_generated_at: request.preview.delivery_run_generated_at ?? null,
    delivery_run_classification: request.preview.delivery_run_classification ?? null,
    delivery_run_agent_id: request.preview.delivery_run_agent_id ?? null,
    delivery_run_recommended_action: deliveryAction || null,
    delivery_workspace: request.preview.delivery_workspace ?? null,
    delivery_workspace_causality: request.preview.delivery_workspace_causality ?? null,
    delivery_workspace_validated: request.preview.delivery_workspace_validated === true,
    accounting_projection: accountingProjection(request),
    rolling_window_note: ROLLING_WINDOW_NOTE,
    before: request.before,
    after: request.after,
  };
  const commit = {
    schema_version: QUOTA_SPEND_COMMIT_RECEIPT_SCHEMA,
    effect_id: request.effect_id,
    request_digest: fingerprint,
  } satisfies JsonObject;
  const record: JsonObject = {
    generated_at: request.generated_at,
    goal_id: request.goal_id,
    classification: QUOTA_SLOT_SPENT_CLASSIFICATION,
    recommended_action: recommendedAction,
    health_check: healthCheck(disposition),
    quota_event: quotaEvent,
    quota_spend_commit: commit,
  };
  const goalRef = quotaGoalRef(request.owner);
  if (goalRef !== null) {
    record.goal_ref = goalRef;
    quotaEvent.goal_ref = goalRef;
  }
  if (request.resolved_agent_id) {
    record.agent_id = request.resolved_agent_id;
    quotaEvent.agent_id = request.resolved_agent_id;
  }
  const effectRef = optionalString(request.preview.effect_ref, "preview.effect_ref");
  if (effectRef) record.effect_ref = effectRef;
  const turnInstanceId = optionalString(
    request.preview.turn_instance_id,
    "preview.turn_instance_id",
  );
  if (turnInstanceId) {
    record.turn_instance_id = turnInstanceId;
    const todoId = optionalString(request.preview.todo_id, "preview.todo_id");
    const replanId = optionalString(
      request.preview.replan_obligation_id,
      "preview.replan_obligation_id",
    );
    if (todoId) record.todo_id = todoId;
    if (replanId) record.replan_obligation_id = replanId;
    const identity = jsonObject(request.preview.settlement_identity);
    if (identity) record.settlement_identity = identity;
  }
  return record;
}

export async function quotaSpendIndexDigest(indexPath: string): Promise<string | null> {
  return await quotaAccountingIndexDigest(indexPath);
}

async function evaluateQuotaSpendReplay(
  value: unknown,
): Promise<QuotaSpendCommitResult> {
  const request = replayRequestObject(value);
  const indexPath = join(
    request.runtime_root,
    "goals",
    request.goal_id,
    "runs",
    "index.jsonl",
  );
  const goalRef = quotaGoalRef(request.owner);
  const lookup = await withQuotaAccountingOwner(
    request.owner,
    async (indexLockHeld) => await lookupQuotaAccountingReplay(
      "spend",
      indexPath,
      request.effect_id,
      request.read_only,
      goalRef,
      indexLockHeld,
    ),
  );
  const requestFingerprint = sha256(canonicalJson(value));
  if (lookup.resolution.kind === "conflict") {
    return {
      schema_version: QUOTA_SPEND_COMMIT_RESULT_SCHEMA,
      effect_id: request.effect_id,
      status: "conflict",
      written: false,
      replayed: false,
      repaired: false,
      conflict: true,
      request_digest: requestFingerprint,
      index_digest: lookup.indexDigest,
      reason: lookup.resolution.reason,
      record: null,
      payload: {
        ok: false,
        appended: false,
        replay_found: true,
        goal_id: request.goal_id,
        effect_ref: request.effect_id,
        reason: lookup.resolution.reason,
      },
      reason_code: lookup.resolution.reasonCode,
    };
  }
  const candidate = lookup.resolution.kind === "matched"
    ? lookup.resolution.record
    : null;
  const basePayload: JsonObject = {
    ok: false,
    appended: false,
    replay_found: candidate !== null,
    goal_id: request.goal_id,
    effect_ref: request.effect_id,
  };
  if (candidate === null) {
    return {
      schema_version: QUOTA_SPEND_COMMIT_RESULT_SCHEMA,
      effect_id: request.effect_id,
      status: "preview",
      written: false,
      replayed: false,
      repaired: false,
      conflict: false,
      request_digest: requestFingerprint,
      index_digest: lookup.indexDigest,
      reason: "quota spend replay was not found",
      record: null,
      payload: basePayload,
    };
  }
  const candidateGoalId = typeof candidate.goal_id === "string"
    ? candidate.goal_id.trim()
    : "";
  const candidateAgentId = typeof candidate.agent_id === "string"
    ? candidate.agent_id.trim()
    : "";
  if (
    candidateGoalId !== request.goal_id ||
    !request.resolved_agent_id ||
    candidateAgentId !== request.resolved_agent_id
  ) {
    return {
      schema_version: QUOTA_SPEND_COMMIT_RESULT_SCHEMA,
      effect_id: request.effect_id,
      status: "preview",
      written: false,
      replayed: false,
      repaired: false,
      conflict: false,
      request_digest: requestFingerprint,
      index_digest: lookup.indexDigest,
      reason: "quota spend replay requires the same valid agent identity",
      record: null,
      payload: { ...basePayload, reason: "agent identity mismatch" },
    };
  }
  return {
    schema_version: QUOTA_SPEND_COMMIT_RESULT_SCHEMA,
    effect_id: request.effect_id,
    status: "replayed",
    written: false,
    replayed: true,
    repaired: false,
    conflict: false,
    request_digest: requestFingerprint,
    index_digest: lookup.indexDigest,
    reason: "quota spend replayed for the same provider effect",
    record: candidate,
    payload: {
      ...candidate,
      ...basePayload,
      ok: true,
      idempotent_replay: true,
      agent_id: candidateAgentId,
      reason: "quota spend replayed for the same provider effect",
    },
  };
}

function indexRecordFor(
  request: QuotaSpendCommitRequest,
  record: JsonObject,
  jsonPath: string,
  markdownPath: string,
  fingerprint: string,
): JsonObject {
  const indexRecord: JsonObject = {
    generated_at: request.generated_at,
    goal_id: request.goal_id,
    classification: QUOTA_SLOT_SPENT_CLASSIFICATION,
    recommended_action: record.recommended_action,
    health_check: record.health_check,
    json_path: jsonPath,
    markdown_path: markdownPath,
    quota_spend_commit: {
      schema_version: QUOTA_SPEND_COMMIT_RECEIPT_SCHEMA,
      effect_id: request.effect_id,
      request_digest: fingerprint,
    },
  };
  const goalRef = quotaGoalRef(request.owner);
  if (goalRef !== null) indexRecord.goal_ref = goalRef;
  for (const field of [
    "agent_id",
    "effect_ref",
    "turn_instance_id",
    "todo_id",
    "replan_obligation_id",
    "settlement_identity",
  ]) {
    if (record[field] !== undefined && record[field] !== null && record[field] !== "") {
      indexRecord[field] = record[field];
    }
  }
  return indexRecord;
}

function payloadFor(
  request: QuotaSpendCommitRequest,
  record: JsonObject,
  jsonPath: string,
  markdownPath: string,
  indexPath: string,
  options: { appended: boolean; replayed: boolean; repaired: boolean },
): JsonObject {
  const event = requiredObject(record.quota_event, "record.quota_event");
  const payload: JsonObject = {
    ...request.preview,
    accounting_projection: accountingProjection(request),
    rolling_window_note: ROLLING_WINDOW_NOTE,
    dry_run: !request.execute,
    appended: options.appended,
    registry_mutated: false,
    source: event.source,
    classification: QUOTA_SLOT_SPENT_CLASSIFICATION,
    generated_at: record.generated_at,
    agent_id: record.agent_id ?? null,
    quota_event: event,
    json_path: jsonPath,
    markdown_path: markdownPath,
    index_path: indexPath,
    effect_id: request.effect_id,
    idempotent_replay: options.replayed,
    transaction_repaired: options.repaired,
    reason: options.replayed
      ? "quota spend commit replayed for the same effect identity"
      : options.repaired
      ? "quota spend commit repaired its prepared durable transaction"
      : `${request.execute ? "appended" : "dry-run preview"} quota slot spend event: ` +
        `${request.goal_id} ${request.before.spent_slots}->${request.after.spent_slots} slots`,
  };
  const goalRef = quotaGoalRef(request.owner);
  if (goalRef !== null) payload.goal_ref = goalRef;
  if (request.execute) {
    payload.before = request.before;
    payload.after = request.after;
  }
  return payload;
}

function result(
  request: QuotaSpendCommitRequest,
  fingerprint: string,
  status: QuotaSpendCommitStatus,
  indexDigest: string | null,
  reason: string,
  record: JsonObject | null,
  payload: JsonObject,
  extra: Partial<QuotaSpendCommitResult> = {},
): QuotaSpendCommitResult {
  return {
    schema_version: QUOTA_SPEND_COMMIT_RESULT_SCHEMA,
    effect_id: request.effect_id,
    status,
    written: status === "written",
    replayed: status === "replayed",
    repaired: status === "repaired",
    conflict: status === "conflict",
    request_digest: fingerprint,
    index_digest: indexDigest,
    reason,
    record,
    payload,
    ...extra,
  };
}

export async function evaluateQuotaSpendCommit(
  value: unknown,
): Promise<QuotaSpendCommitResult> {
  const rawRequest = requiredObject(value, "quota.spend.commit params");
  if (rawRequest.operation === "replay") {
    return await evaluateQuotaSpendReplay(rawRequest);
  }
  const request = requestObject(value);
  const fingerprint = requestDigest(request);
  const record = buildSpendRecord(request, fingerprint);
  if (!request.runtime_root) {
    const payload = payloadFor(request, record, "", "", "", {
      appended: false,
      replayed: false,
      repaired: false,
    });
    return result(
      request,
      fingerprint,
      "preview",
      null,
      "quota spend event preview evaluated by TypeScript",
      record,
      payload,
    );
  }

  const runsDir = join(request.runtime_root, "goals", request.goal_id, "runs");
  const indexPath = join(runsDir, "index.jsonl");
  if (!request.execute) {
    const { jsonPath, markdownPath } = await nextQuotaAccountingArtifactPaths(
      "spend",
      runsDir,
      request.generated_at,
      request.effect_id,
    );
    const payload = payloadFor(
      request,
      record,
      jsonPath,
      markdownPath,
      indexPath,
      { appended: false, replayed: false, repaired: false },
    );
    return result(
      request,
      fingerprint,
      "preview",
      await quotaSpendIndexDigest(indexPath),
      "quota spend transaction preview evaluated by TypeScript",
      record,
      payload,
    );
  }

  const goalRef = quotaGoalRef(request.owner);
  const outcome = await withQuotaAccountingOwner(
    request.owner,
    async (indexLockHeld) => await commitQuotaAccountingArtifactTransaction({
      kind: "spend",
      runsDir,
      generatedAt: request.generated_at,
      effectId: request.effect_id,
      requestDigest: fingerprint,
      expectedIndexDigest: request.expected_index_digest,
      ...(goalRef === null ? {} : { goalRef }),
      indexLockHeld,
      prepare: ({ jsonPath, markdownPath, indexPath: lockedIndexPath }) => {
        const payload = payloadFor(
          request,
          record,
          jsonPath,
          markdownPath,
          lockedIndexPath,
          { appended: true, replayed: false, repaired: false },
        );
        return {
          kind: "prepared",
          record,
          indexRecord: indexRecordFor(
            request,
            record,
            jsonPath,
            markdownPath,
            fingerprint,
          ),
          markdown: renderQuotaSlotMarkdown(
            payload,
            QUOTA_SLOT_SPENT_CLASSIFICATION,
          ),
          payload,
        };
      },
    }),
  );

  if (outcome.status === "conflict") {
    return result(
      request,
      fingerprint,
      "conflict",
      outcome.indexDigest,
      outcome.reason,
      null,
      { ...request.preview, ok: false, appended: false },
      { reason_code: outcome.reasonCode },
    );
  }
  if (outcome.status === "not_found") {
    throw new EffectRuntimeRequestError(
      "quota spend transaction preparation did not produce an artifact",
    );
  }
  if (outcome.status === "written") {
    return result(
      request,
      fingerprint,
      "written",
      outcome.indexDigest,
      "quota spend transaction committed by TypeScript",
      outcome.receipt.record,
      outcome.receipt.payload,
    );
  }

  const repaired = outcome.status === "repaired";
  const replayPayload = {
    ...outcome.receipt.payload,
    appended: repaired,
    idempotent_replay: !repaired,
    transaction_repaired: repaired,
    reason: repaired
      ? "quota spend commit repaired its prepared durable transaction"
      : "quota spend commit replayed for the same effect identity",
  };
  return result(
    request,
    fingerprint,
    outcome.status,
    outcome.indexDigest,
    optionalString(replayPayload.reason, "replay payload reason") ?? "",
    outcome.receipt.record,
    replayPayload,
  );
}
