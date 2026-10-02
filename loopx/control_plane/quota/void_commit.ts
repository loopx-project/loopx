import { createHash } from "node:crypto";
import { readFile, realpath } from "node:fs/promises";
import { basename, isAbsolute, join, relative, resolve, sep } from "node:path";

import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  commitQuotaAccountingArtifactTransaction,
  nextQuotaAccountingArtifactPaths,
  parseQuotaAccountingIndex,
  quotaAccountingIndexDigest,
  renderQuotaSlotMarkdown,
  type QuotaAccountingArtifactPrepareContext,
  type QuotaAccountingArtifactPreparation,
} from "./accounting_artifact_transaction.ts";
import {
  jsonObject,
  optionalNonEmptyString as optionalString,
  requireBoolean as requiredBoolean,
  requireJsonObject as requiredObject,
  requireNonEmptyString as requiredString,
  requireStringLiteral,
} from "../runtime_decode.ts";
import {
  parseQuotaAccountingOwner,
  quotaGoalRef,
  requireQuotaOwnerProjection,
  withQuotaAccountingOwner,
  type QuotaAccountingOwner,
} from "./source_admission.ts";

export const QUOTA_VOID_COMMIT_REQUEST_SCHEMA =
  "loopx_quota_void_commit_request_v0";
export const QUOTA_VOID_COMMIT_RESULT_SCHEMA =
  "loopx_quota_void_commit_result_v0";
export const QUOTA_VOID_COMMIT_RECEIPT_SCHEMA =
  "quota_void_commit_receipt_v0";
export const QUOTA_SLOT_VOIDED_CLASSIFICATION = "quota_slot_voided";
const QUOTA_SLOT_SPENT_CLASSIFICATION = "quota_slot_spent";
const QUOTA_VOID_SOURCES = [
  "heartbeat",
  "controller",
  "adapter",
  "visible-goal",
] as const;
const ROLLING_WINDOW_NOTE =
  "quota void-slot appends a quota_slot_voided accounting event. It does not delete the " +
  "original spend event; rolling-window ledgers subtract the void only when the target " +
  "spend event is inside the same accounting window.";

type QuotaVoidSource = (typeof QUOTA_VOID_SOURCES)[number];
type QuotaVoidCommitStatus =
  | "preview"
  | "not_found"
  | "written"
  | "replayed"
  | "repaired"
  | "conflict";

interface QuotaVoidCommitRequest {
  schema_version: typeof QUOTA_VOID_COMMIT_REQUEST_SCHEMA;
  operation: "commit" | "preview";
  effect_id: string;
  runtime_root: string;
  goal_id: string;
  voided_run_generated_at: string;
  source: QuotaVoidSource;
  reason_summary: string | null;
  generated_at: string;
  execute: boolean;
  expected_index_digest: string | null;
  before: JsonObject;
  owner: QuotaAccountingOwner;
}

interface QuotaVoidProjectionRequest {
  schema_version: typeof QUOTA_VOID_COMMIT_REQUEST_SCHEMA;
  operation: "project_record";
  preview: JsonObject;
  source: QuotaVoidSource;
  reason_summary: string | null;
  generated_at: string;
}

export interface QuotaVoidCommitResult extends JsonObject {
  schema_version: typeof QUOTA_VOID_COMMIT_RESULT_SCHEMA;
  effect_id: string | null;
  status: QuotaVoidCommitStatus;
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

interface TargetSpend {
  run: JsonObject;
  event: JsonObject;
}

interface QuotaVoidDecisionFacts {
  shouldRun: boolean;
  normalDeliveryAllowed: boolean;
  recoveryDeliveryAllowed: boolean;
  effectiveAction: string | null;
  selfRepairAllowed: boolean;
  capabilityRepairAllowed: boolean;
  workspaceRepairAllowed: boolean;
  state: string;
  safeBypassAllowed: boolean;
  safeBypassKind: string | null;
  blockedActionScope: string | null;
  quota: JsonObject;
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
  if (
    goalId === "." ||
    goalId === ".." ||
    goalId.includes("/") ||
    goalId.includes("\\")
  ) {
    throw new EffectRuntimeRequestError("goal_id must be a single path segment");
  }
  if (basename(goalId) !== goalId) {
    throw new EffectRuntimeRequestError("goal_id must not include path traversal");
  }
  return goalId;
}

function commitRequest(value: unknown): QuotaVoidCommitRequest {
  const request = requiredObject(value, "quota.void.commit params");
  if (request.schema_version !== QUOTA_VOID_COMMIT_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError("Quota void commit request schema mismatch");
  }
  const operation = request.operation === undefined
    ? "commit"
    : requireStringLiteral(
      request.operation,
      ["commit", "preview"] as const,
      "operation",
    );
  const runtimeRoot = requiredString(request.runtime_root, "runtime_root").trim();
  if (!isAbsolute(runtimeRoot)) {
    throw new EffectRuntimeRequestError("runtime_root must be absolute");
  }
  const effectId = requiredString(request.effect_id, "effect_id").trim();
  if (effectId.length > 256) {
    throw new EffectRuntimeRequestError("effect_id exceeds 256 characters");
  }
  const before = requiredObject(request.before, "before");
  decodeQuotaVoidDecision(before, "before");
  const execute = requiredBoolean(request.execute, "execute");
  if (operation === "preview" && execute) {
    throw new EffectRuntimeRequestError(
      "quota void preview operation cannot execute durable effects",
    );
  }
  const goalId = safeGoalId(request.goal_id);
  return {
    schema_version: QUOTA_VOID_COMMIT_REQUEST_SCHEMA,
    operation,
    effect_id: effectId,
    runtime_root: runtimeRoot,
    goal_id: goalId,
    voided_run_generated_at:
      typeof request.voided_run_generated_at === "string"
        ? request.voided_run_generated_at.trim()
        : "",
    source: requireStringLiteral(
      request.source,
      QUOTA_VOID_SOURCES,
      "source",
      `quota slot void source must be one of: ${QUOTA_VOID_SOURCES.join(", ")}`,
    ),
    reason_summary: optionalString(
      request.reason_summary,
      "reason_summary",
    )?.trim() ?? null,
    generated_at: requiredString(request.generated_at, "generated_at").trim(),
    execute,
    expected_index_digest: optionalString(
      request.expected_index_digest,
      "expected_index_digest",
    ),
    before,
    owner: parseQuotaAccountingOwner({
      goalRefValue: request.goal_ref,
      sourceAdmissionValue: request.source_admission,
      runtimeRoot,
      goalId,
    }),
  };
}

function projectionRequest(value: unknown): QuotaVoidProjectionRequest {
  const request = requiredObject(value, "quota void record projection params");
  if (request.schema_version !== QUOTA_VOID_COMMIT_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError("Quota void commit request schema mismatch");
  }
  return {
    schema_version: QUOTA_VOID_COMMIT_REQUEST_SCHEMA,
    operation: "project_record",
    preview: requiredObject(request.preview, "preview"),
    source: requireStringLiteral(
      request.source,
      QUOTA_VOID_SOURCES,
      "source",
      `quota slot void source must be one of: ${QUOTA_VOID_SOURCES.join(", ")}`,
    ),
    reason_summary: optionalString(
      request.reason_summary,
      "reason_summary",
    )?.trim() ?? null,
    generated_at: requiredString(request.generated_at, "generated_at").trim(),
  };
}

function requestDigest(request: QuotaVoidCommitRequest): string {
  const goalRef = quotaGoalRef(request.owner);
  return sha256(canonicalJson({
    schema_version: request.schema_version,
    effect_id: request.effect_id,
    runtime_root: request.runtime_root,
    goal_id: request.goal_id,
    voided_run_generated_at: request.voided_run_generated_at,
    source: request.source,
    reason_summary: request.reason_summary,
    before: request.before,
    ...(goalRef === null ? {} : { goal_ref: goalRef }),
  }));
}

function legacyInteger(value: unknown, fallback: number): number {
  if (typeof value === "boolean") return fallback;
  if (typeof value === "number" && Number.isFinite(value)) {
    return Math.trunc(value);
  }
  if (typeof value === "string" && value.trim()) {
    const normalized = value.trim();
    if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(normalized)) {
      return fallback;
    }
    const parsed = Number(normalized);
    if (Number.isFinite(parsed)) return Math.trunc(parsed);
  }
  return fallback;
}

function cloneObject(value: JsonObject): JsonObject {
  return JSON.parse(JSON.stringify(value)) as JsonObject;
}

function nullableString(value: unknown, label: string): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string") {
    throw new EffectRuntimeRequestError(`${label} must be a string or null`);
  }
  return value;
}

function decodeQuotaVoidDecision(
  value: unknown,
  label: string,
): QuotaVoidDecisionFacts {
  const decision = requiredObject(value, label);
  if (typeof decision.state !== "string") {
    throw new EffectRuntimeRequestError(`${label}.state must be a string`);
  }
  return {
    shouldRun: requiredBoolean(decision.should_run, `${label}.should_run`),
    normalDeliveryAllowed: requiredBoolean(
      decision.normal_delivery_allowed,
      `${label}.normal_delivery_allowed`,
    ),
    recoveryDeliveryAllowed: requiredBoolean(
      decision.recovery_delivery_allowed,
      `${label}.recovery_delivery_allowed`,
    ),
    effectiveAction: nullableString(
      decision.effective_action,
      `${label}.effective_action`,
    ),
    selfRepairAllowed: requiredBoolean(
      decision.self_repair_allowed,
      `${label}.self_repair_allowed`,
    ),
    capabilityRepairAllowed: requiredBoolean(
      decision.capability_repair_allowed,
      `${label}.capability_repair_allowed`,
    ),
    workspaceRepairAllowed: requiredBoolean(
      decision.workspace_repair_allowed,
      `${label}.workspace_repair_allowed`,
    ),
    state: decision.state,
    safeBypassAllowed: requiredBoolean(
      decision.safe_bypass_allowed,
      `${label}.safe_bypass_allowed`,
    ),
    safeBypassKind: nullableString(
      decision.safe_bypass_kind,
      `${label}.safe_bypass_kind`,
    ),
    blockedActionScope: nullableString(
      decision.blocked_action_scope,
      `${label}.blocked_action_scope`,
    ),
    quota: requiredObject(decision.quota, `${label}.quota`),
  };
}

function normalizedAgentId(before: JsonObject): string | null {
  const identity = jsonObject(before.agent_identity);
  if (!identity || typeof identity.agent_id !== "string") return null;
  const value = identity.agent_id.trim().toLowerCase().replace(/ +/g, "-");
  return /^[a-z][a-z0-9_.:@-]{0,79}$/.test(value) ? value : null;
}

function compactDecision(value: unknown): JsonObject {
  const decision = jsonObject(value) ?? {};
  const quota = jsonObject(decision.quota) ?? {};
  return {
    should_run: Boolean(decision.should_run),
    normal_delivery_allowed: Boolean(decision.normal_delivery_allowed),
    recovery_delivery_allowed: Boolean(decision.recovery_delivery_allowed),
    effective_action: decision.effective_action ?? null,
    self_repair_allowed: Boolean(decision.self_repair_allowed),
    capability_repair_allowed: Boolean(decision.capability_repair_allowed),
    workspace_repair_allowed: Boolean(decision.workspace_repair_allowed),
    state: String(decision.state ?? ""),
    safe_bypass_allowed: Boolean(decision.safe_bypass_allowed),
    safe_bypass_kind: decision.safe_bypass_kind ?? null,
    blocked_action_scope: decision.blocked_action_scope ?? null,
    compute: quota.compute ?? null,
    window_hours: quota.window_hours ?? null,
    slot_minutes: quota.slot_minutes ?? null,
    spent_slots: quota.spent_slots ?? null,
    allowed_slots: quota.allowed_slots ?? null,
  };
}

async function readTargetEvent(
  runsDir: string,
  run: JsonObject,
  goalId: string,
): Promise<JsonObject | null> {
  const inline = jsonObject(run.quota_event);
  if (inline) return inline;
  if (typeof run.json_path !== "string" || !run.json_path.trim()) return null;
  let targetPath: string;
  try {
    targetPath = await realpath(resolve(run.json_path.trim()));
  } catch (error) {
    if (error instanceof Error && "code" in error && error.code === "ENOENT") {
      return null;
    }
    throw error;
  }
  const relativePath = relative(await realpath(runsDir), targetPath);
  if (
    !relativePath ||
    relativePath === ".." ||
    relativePath.startsWith(`..${sep}`) ||
    isAbsolute(relativePath)
  ) {
    throw new EffectRuntimeRequestError(
      "quota spend json_path must stay inside the goal runs directory",
    );
  }
  let content: string;
  try {
    content = await readFile(targetPath, "utf8");
  } catch (error) {
    if (error instanceof Error && "code" in error && error.code === "ENOENT") return null;
    throw error;
  }
  let value: unknown;
  try {
    value = JSON.parse(content);
  } catch {
    throw new EffectRuntimeRequestError("quota spend JSON artifact is malformed");
  }
  const record = requiredObject(value, "quota spend JSON artifact");
  if (
    record.classification !== QUOTA_SLOT_SPENT_CLASSIFICATION ||
    (record.goal_id !== undefined && record.goal_id !== goalId)
  ) {
    throw new EffectRuntimeRequestError(
      "quota spend JSON artifact identity does not match its index row",
    );
  }
  return jsonObject(record.quota_event);
}

async function findTargetSpend(
  runsDir: string,
  records: readonly JsonObject[],
  goalId: string,
  generatedAt: string,
  owner: QuotaAccountingOwner,
): Promise<TargetSpend | null> {
  for (const run of [...records].reverse()) {
    if (String(run.goal_id || goalId) !== goalId) continue;
    if (String(run.generated_at ?? "") !== generatedAt) continue;
    if (run.classification !== QUOTA_SLOT_SPENT_CLASSIFICATION) continue;
    const event = await readTargetEvent(runsDir, run, goalId);
    if (event?.event_type !== QUOTA_SLOT_SPENT_CLASSIFICATION) continue;
    requireQuotaOwnerProjection(owner, run.goal_ref, "quota void target");
    requireQuotaOwnerProjection(
      owner,
      event.goal_ref,
      "quota void target event",
    );
    return { run, event };
  }
  return null;
}

function missingTargetPayload(
  goalId: string,
  generatedAt: string,
): JsonObject {
  if (!generatedAt) {
    return {
      ok: false,
      mode: "void-slot",
      dry_run: true,
      goal_id: goalId,
      appended: false,
      registry_mutated: false,
      reason: "`quota void-slot` requires --void-generated-at",
    };
  }
  return {
    ok: false,
    mode: "void-slot",
    dry_run: true,
    goal_id: goalId,
    voided_run_generated_at: generatedAt,
    appended: false,
    registry_mutated: false,
    reason: "target quota_slot_spent run was not found in the goal runtime index",
  };
}

function previewFor(
  request: QuotaVoidCommitRequest,
  target: TargetSpend,
): JsonObject {
  const slots = Math.max(1, legacyInteger(target.event.slots, 1));
  const before = cloneObject(request.before);
  const beforeQuota = jsonObject(before.quota) ?? {};
  const after = cloneObject(before);
  const afterQuota = { ...beforeQuota };
  afterQuota.spent_slots = Math.max(
    0,
    legacyInteger(beforeQuota.spent_slots, 0) - slots,
  );
  after.quota = afterQuota;
  const preview: JsonObject = {
    ok: true,
    mode: "void-slot",
    dry_run: true,
    goal_id: request.goal_id,
    slots,
    voided_run_generated_at: request.voided_run_generated_at,
    voided_run_classification: target.run.classification,
    voided_run_json_path: target.run.json_path ?? null,
    appended: false,
    registry_mutated: false,
    before,
    after,
    would_throttle: false,
    reason:
      `dry-run preview: voiding ${slots} slot(s) from ${request.goal_id} ` +
      `quota spend run ${request.voided_run_generated_at}`,
    rolling_window_note: ROLLING_WINDOW_NOTE,
    classification: QUOTA_SLOT_VOIDED_CLASSIFICATION,
  };
  const goalRef = quotaGoalRef(request.owner);
  if (goalRef !== null) preview.goal_ref = goalRef;
  return preview;
}

function recordFor(
  preview: JsonObject,
  source: QuotaVoidSource,
  reasonSummary: string | null,
  generatedAt: string,
  effectId: string | null,
  fingerprint: string,
  goalRef: JsonObject | null = null,
): JsonObject {
  if (preview.ok !== true) {
    throw new EffectRuntimeRequestError(
      typeof preview.reason === "string"
        ? preview.reason
        : "quota slot void requires a valid preview",
    );
  }
  const safeReason = reasonSummary ||
    "void duplicate or invalid quota slot spend event";
  const before = jsonObject(preview.before) ?? {};
  const after = jsonObject(preview.after) ?? {};
  const agentId = normalizedAgentId(before);
  const event: JsonObject = {
    event_type: QUOTA_SLOT_VOIDED_CLASSIFICATION,
    source,
    slots: Math.max(1, legacyInteger(preview.slots, 1)),
    reason_summary: safeReason,
    voided_run_generated_at: preview.voided_run_generated_at ?? null,
    voided_run_classification: preview.voided_run_classification ?? null,
    before: Object.keys(before).length ? compactDecision(before) : {},
    after: Object.keys(after).length ? compactDecision(after) : {},
  };
  const record: JsonObject = {
    generated_at: generatedAt,
    goal_id: preview.goal_id ?? null,
    classification: QUOTA_SLOT_VOIDED_CLASSIFICATION,
    recommended_action: safeReason,
    health_check:
      "quota slot void event public-safe; original spend preserved for audit",
    quota_event: event,
  };
  if (agentId) {
    record.agent_id = agentId;
    event.agent_id = agentId;
  }
  if (effectId) {
    record.quota_void_commit = {
      schema_version: QUOTA_VOID_COMMIT_RECEIPT_SCHEMA,
      effect_id: effectId,
      request_digest: fingerprint,
    };
  }
  if (goalRef !== null) {
    record.goal_ref = goalRef;
    event.goal_ref = goalRef;
  }
  return record;
}

function artifactsFor(
  request: QuotaVoidCommitRequest,
  fingerprint: string,
  preview: JsonObject,
  context: Pick<
    QuotaAccountingArtifactPrepareContext,
    "jsonPath" | "markdownPath" | "indexPath"
  >,
): Extract<QuotaAccountingArtifactPreparation, { kind: "prepared" }> {
  const record = recordFor(
    preview,
    request.source,
    request.reason_summary,
    request.generated_at,
    request.effect_id,
    fingerprint,
    quotaGoalRef(request.owner),
  );
  const event = requiredObject(record.quota_event, "record.quota_event");
  const payload: JsonObject = {
    ...preview,
    dry_run: !request.execute,
    appended: request.execute,
    registry_mutated: false,
    source: event.source,
    classification: QUOTA_SLOT_VOIDED_CLASSIFICATION,
    generated_at: request.generated_at,
    agent_id: record.agent_id ?? null,
    effect_id: request.effect_id,
    quota_event: event,
    json_path: context.jsonPath,
    markdown_path: context.markdownPath,
    index_path: context.indexPath,
    reason:
      `${request.execute ? "appended" : "dry-run preview"} quota slot void event: ` +
      `${request.goal_id} voided ${event.slots} slot(s) from ` +
      `${event.voided_run_generated_at}`,
  };
  if (request.execute) {
    payload.before = event.before;
    payload.after = event.after;
  }
  const indexRecord: JsonObject = {
    generated_at: request.generated_at,
    goal_id: request.goal_id,
    classification: QUOTA_SLOT_VOIDED_CLASSIFICATION,
    recommended_action: record.recommended_action,
    health_check: record.health_check,
    json_path: context.jsonPath,
    markdown_path: context.markdownPath,
    quota_void_commit: record.quota_void_commit,
  };
  if (record.agent_id) indexRecord.agent_id = record.agent_id;
  const goalRef = quotaGoalRef(request.owner);
  if (goalRef !== null) {
    indexRecord.goal_ref = goalRef;
    payload.goal_ref = goalRef;
  }
  return {
    kind: "prepared",
    record,
    indexRecord,
    markdown: renderQuotaSlotMarkdown(
      payload,
      QUOTA_SLOT_VOIDED_CLASSIFICATION,
    ),
    payload,
  };
}

async function prepareArtifacts(
  request: QuotaVoidCommitRequest,
  fingerprint: string,
  runsDir: string,
  context: QuotaAccountingArtifactPrepareContext,
): Promise<QuotaAccountingArtifactPreparation> {
  const target = await findTargetSpend(
    runsDir,
    context.indexRecords,
    request.goal_id,
    request.voided_run_generated_at,
    request.owner,
  );
  if (!target) {
    const payload = missingTargetPayload(
      request.goal_id,
      request.voided_run_generated_at,
    );
    return {
      kind: "not_found",
      reason: String(payload.reason),
      payload,
    };
  }
  return artifactsFor(
    request,
    fingerprint,
    previewFor(request, target),
    context,
  );
}

function result(
  effectId: string | null,
  fingerprint: string,
  status: QuotaVoidCommitStatus,
  indexDigest: string | null,
  reason: string,
  record: JsonObject | null,
  payload: JsonObject,
  reasonCode?: string,
): QuotaVoidCommitResult {
  return {
    schema_version: QUOTA_VOID_COMMIT_RESULT_SCHEMA,
    effect_id: effectId,
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
    ...(reasonCode ? { reason_code: reasonCode } : {}),
  };
}

async function previewCommit(
  request: QuotaVoidCommitRequest,
  fingerprint: string,
  runsDir: string,
): Promise<QuotaVoidCommitResult> {
  const indexPath = join(runsDir, "index.jsonl");
  let content: string | null;
  try {
    content = await readFile(indexPath, "utf8");
  } catch (error) {
    if (error instanceof Error && "code" in error && error.code === "ENOENT") {
      content = null;
    } else {
      throw error;
    }
  }
  const records = parseQuotaAccountingIndex(content);
  const indexDigest = await quotaAccountingIndexDigest(indexPath);
  if (request.operation === "preview") {
    const target = await findTargetSpend(
      runsDir,
      records,
      request.goal_id,
      request.voided_run_generated_at,
      request.owner,
    );
    if (!target) {
      const payload = missingTargetPayload(
        request.goal_id,
        request.voided_run_generated_at,
      );
      return result(
        request.effect_id,
        fingerprint,
        "not_found",
        indexDigest,
        String(payload.reason),
        null,
        payload,
        "target_not_found",
      );
    }
    const payload = previewFor(request, target);
    return result(
      request.effect_id,
      fingerprint,
      "preview",
      indexDigest,
      String(payload.reason),
      null,
      payload,
    );
  }
  const paths = await nextQuotaAccountingArtifactPaths(
    "void",
    runsDir,
    request.generated_at,
    request.effect_id,
  );
  const preparation = await prepareArtifacts(request, fingerprint, runsDir, {
    jsonPath: paths.jsonPath,
    markdownPath: paths.markdownPath,
    indexPath,
    indexDigest,
    indexRecords: records,
  });
  if (preparation.kind === "not_found") {
    return result(
      request.effect_id,
      fingerprint,
      "not_found",
      indexDigest,
      preparation.reason,
      null,
      preparation.payload,
      "target_not_found",
    );
  }
  return result(
    request.effect_id,
    fingerprint,
    "preview",
    indexDigest,
    "quota void transaction preview evaluated by TypeScript",
    preparation.record,
    preparation.payload,
  );
}

async function evaluateCommit(
  request: QuotaVoidCommitRequest,
): Promise<QuotaVoidCommitResult> {
  const fingerprint = requestDigest(request);
  const runsDir = join(
    request.runtime_root,
    "goals",
    request.goal_id,
    "runs",
  );
  return await withQuotaAccountingOwner(request.owner, async (indexLockHeld) => {
    if (!request.execute) {
      return await previewCommit(request, fingerprint, runsDir);
    }
    const goalRef = quotaGoalRef(request.owner);
    const outcome = await commitQuotaAccountingArtifactTransaction({
      kind: "void",
      runsDir,
      generatedAt: request.generated_at,
      effectId: request.effect_id,
      requestDigest: fingerprint,
      expectedIndexDigest: request.expected_index_digest,
      ...(goalRef === null ? {} : { goalRef }),
      indexLockHeld,
      prepare: async (context) =>
        await prepareArtifacts(request, fingerprint, runsDir, context),
    });
    if (outcome.status === "conflict") {
      return result(
        request.effect_id,
        fingerprint,
        "conflict",
        outcome.indexDigest,
        outcome.reason,
        null,
        {
          ok: false,
          mode: "void-slot",
          goal_id: request.goal_id,
          effect_id: request.effect_id,
          appended: false,
          registry_mutated: false,
        },
        outcome.reasonCode,
      );
    }
    if (outcome.status === "not_found") {
      return result(
        request.effect_id,
        fingerprint,
        "not_found",
        outcome.indexDigest,
        outcome.reason,
        null,
        outcome.payload,
        "target_not_found",
      );
    }
    const replayed = outcome.status === "replayed";
    const repaired = outcome.status === "repaired";
    const responsePayload: JsonObject = {
      ...outcome.receipt.payload,
      appended: outcome.status === "written" || repaired,
      idempotent_replay: replayed,
      transaction_repaired: repaired,
      reason: replayed
        ? "quota void commit replayed for the same effect identity"
        : repaired
        ? "quota void commit repaired its prepared durable transaction"
        : outcome.receipt.payload.reason,
    };
    return result(
      request.effect_id,
      fingerprint,
      outcome.status,
      outcome.indexDigest,
      String(responsePayload.reason ?? ""),
      outcome.receipt.record,
      responsePayload,
    );
  });
}

function evaluateProjection(
  request: QuotaVoidProjectionRequest,
): QuotaVoidCommitResult {
  const fingerprint = sha256(canonicalJson(request));
  const record = recordFor(
    request.preview,
    request.source,
    request.reason_summary,
    request.generated_at,
    null,
    fingerprint,
  );
  return result(
    null,
    fingerprint,
    "preview",
    null,
    "quota void record projected by TypeScript",
    record,
    request.preview,
  );
}

export async function evaluateQuotaVoidCommit(
  value: unknown,
): Promise<QuotaVoidCommitResult> {
  const raw = requiredObject(value, "quota.void.commit params");
  if (raw.operation === "project_record") {
    return evaluateProjection(projectionRequest(raw));
  }
  return await evaluateCommit(commitRequest(raw));
}

export async function quotaVoidIndexDigest(
  indexPath: string,
): Promise<string | null> {
  return await quotaAccountingIndexDigest(indexPath);
}
