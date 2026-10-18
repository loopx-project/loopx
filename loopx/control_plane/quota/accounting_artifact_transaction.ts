import { createHash } from "node:crypto";
import { access, lstat, readFile } from "node:fs/promises";
import { basename, dirname, extname, join, resolve } from "node:path";

import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  appendJsonLine,
  atomicWriteJson,
  atomicWriteText,
  withFileMutationLock,
} from "../effect_runtime_io.ts";
import { parseExactGoalRef } from "../goals/goal_instance_identity.ts";
import {
  jsonObject,
  optionalNonEmptyString as optionalString,
  requireInteger as requiredInteger,
  requireJsonObject as requiredObject,
  requireNonEmptyString as requiredString,
  requireStringLiteral,
} from "../runtime_decode.ts";

export type QuotaAccountingArtifactKind = "spend" | "void";

interface QuotaAccountingArtifactContract {
  receiptSchema: "quota_spend_commit_receipt_v0" | "quota_void_commit_receipt_v0";
  transactionDirectory: "quota-spend" | "quota-void";
  artifactSlug: "quota-slot-spent" | "quota-slot-voided";
  classification: "quota_slot_spent" | "quota_slot_voided";
  metadataField: "quota_spend_commit" | "quota_void_commit";
  label: "quota spend" | "quota void";
}

const QUOTA_ACCOUNTING_ARTIFACT_CONTRACTS = {
  spend: {
    receiptSchema: "quota_spend_commit_receipt_v0",
    transactionDirectory: "quota-spend",
    artifactSlug: "quota-slot-spent",
    classification: "quota_slot_spent",
    metadataField: "quota_spend_commit",
    label: "quota spend",
  },
  void: {
    receiptSchema: "quota_void_commit_receipt_v0",
    transactionDirectory: "quota-void",
    artifactSlug: "quota-slot-voided",
    classification: "quota_slot_voided",
    metadataField: "quota_void_commit",
    label: "quota void",
  },
} as const satisfies Record<QuotaAccountingArtifactKind, QuotaAccountingArtifactContract>;

export interface QuotaAccountingArtifactReceipt extends JsonObject {
  schema_version:
    | "quota_spend_commit_receipt_v0"
    | "quota_void_commit_receipt_v0";
  effect_id: string;
  request_digest: string;
  status: "prepared" | "committed";
  json_path: string;
  markdown_path: string;
  index_path: string;
  expected_index_digest: string | null;
  expected_index_bytes: number;
  record: JsonObject;
  index_record: JsonObject;
  markdown: string;
  payload: JsonObject;
  goal_ref?: JsonObject;
}

export type QuotaAccountingEffectResolution =
  | { kind: "absent" }
  | { kind: "matched"; record: JsonObject }
  | {
    kind: "conflict";
    reason: string;
    reasonCode: "effect_id_conflict" | "goal_instance_conflict";
  };

export interface QuotaAccountingArtifactPrepareContext {
  jsonPath: string;
  markdownPath: string;
  indexPath: string;
  indexDigest: string | null;
  indexRecords: readonly JsonObject[];
}

export type QuotaAccountingArtifactPreparation =
  | {
    kind: "prepared";
    record: JsonObject;
    indexRecord: JsonObject;
    markdown: string;
    payload: JsonObject;
  }
  | {
    kind: "not_found";
    reason: string;
    payload: JsonObject;
  };

export interface QuotaAccountingArtifactCommitRequest {
  kind: QuotaAccountingArtifactKind;
  runsDir: string;
  generatedAt: string;
  effectId: string;
  requestDigest: string;
  expectedIndexDigest: string | null;
  goalRef?: JsonObject;
  indexLockHeld?: boolean;
  prepare: (
    context: QuotaAccountingArtifactPrepareContext,
  ) =>
    | QuotaAccountingArtifactPreparation
    | Promise<QuotaAccountingArtifactPreparation>;
}

export type QuotaAccountingArtifactCommitOutcome =
  | {
    status: "written" | "replayed" | "repaired";
    receipt: QuotaAccountingArtifactReceipt;
    indexDigest: string | null;
  }
  | {
    status: "conflict";
    reason: string;
    reasonCode:
      | "effect_id_conflict"
      | "goal_instance_conflict"
      | "index_digest_conflict";
    indexDigest: string | null;
  }
  | {
    status: "not_found";
    reason: string;
    payload: JsonObject;
    indexDigest: string | null;
  };

function contractFor(
  kind: QuotaAccountingArtifactKind,
): QuotaAccountingArtifactContract {
  return QUOTA_ACCOUNTING_ARTIFACT_CONTRACTS[kind];
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

function sha256Bytes(value: Uint8Array): string {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}

function exactGoalRef(value: unknown, label: string): JsonObject | null {
  if (value === undefined) return null;
  const parsed = parseExactGoalRef(value);
  if (parsed.kind === "invalid") {
    throw new EffectRuntimeRequestError(
      `${label} must be an exact GoalRef`,
      "malformed_transaction_receipt",
    );
  }
  return {
    goal_id: parsed.value.goalId.value,
    goal_instance_id: parsed.value.goalInstanceId.value,
  };
}

function sameGoalRef(left: JsonObject | null, right: JsonObject | null): boolean {
  return left?.goal_id === right?.goal_id
    && left?.goal_instance_id === right?.goal_instance_id;
}

function ownerConflict(
  existing: JsonObject | null,
  requested: JsonObject | null,
  label: string,
): string | null {
  if (existing === null && requested === null) return null;
  if (sameGoalRef(existing, requested)) return null;
  if (existing === null) {
    return `${label} belongs to a legacy Goal alias and cannot authorize an exact Goal instance`;
  }
  if (requested === null) {
    return `${label} belongs to an exact Goal instance and cannot be consumed without GoalRef admission`;
  }
  return `${label} belongs to a different Goal instance`;
}

function runStem(generatedAt: string): string {
  const stem = generatedAt.replace(/[^0-9A-Za-z-]+/g, "-").replace(/^-+|-+$/g, "");
  if (!stem) {
    throw new EffectRuntimeRequestError(
      "generated_at cannot form a run artifact name",
    );
  }
  return stem;
}

function isNodeErrorCode(error: unknown, code: string): boolean {
  return error instanceof Error && "code" in error && error.code === code;
}

async function pathExists(path: string): Promise<boolean> {
  try {
    await access(path);
    return true;
  } catch (error) {
    if (isNodeErrorCode(error, "ENOENT")) return false;
    throw error;
  }
}

export async function nextQuotaAccountingArtifactPaths(
  kind: QuotaAccountingArtifactKind,
  runsDir: string,
  generatedAt: string,
  effectId: string,
): Promise<{ jsonPath: string; markdownPath: string }> {
  const contract = contractFor(kind);
  const effectDigest = sha256(effectId).slice(
    "sha256:".length,
    "sha256:".length + 24,
  );
  const base = `${runStem(generatedAt)}-${contract.artifactSlug}-${effectDigest}`;
  for (let index = 1; ; index += 1) {
    const stem = index === 1 ? base : `${base}-${index}`;
    const jsonPath = join(runsDir, `${stem}.json`);
    const markdownPath = join(runsDir, `${stem}.md`);
    if (!await pathExists(jsonPath) && !await pathExists(markdownPath)) {
      return { jsonPath, markdownPath };
    }
  }
}

function transactionPath(
  contract: QuotaAccountingArtifactContract,
  runsDir: string,
  effectId: string,
): string {
  const digest = sha256(effectId).slice("sha256:".length, "sha256:".length + 24);
  return join(runsDir, ".transactions", contract.transactionDirectory, `${digest}.json`);
}

async function readOptionalText(path: string): Promise<string | null> {
  try {
    return await readFile(path, "utf8");
  } catch (error) {
    if (isNodeErrorCode(error, "ENOENT")) return null;
    throw error;
  }
}

async function readOptionalBytes(path: string): Promise<Buffer | null> {
  try {
    return await readFile(path);
  } catch (error) {
    if (isNodeErrorCode(error, "ENOENT")) return null;
    throw error;
  }
}

export async function quotaAccountingIndexDigest(
  indexPath: string,
): Promise<string | null> {
  const content = await readOptionalBytes(indexPath);
  return content === null ? null : sha256Bytes(content);
}

export function parseQuotaAccountingIndex(content: string | null): JsonObject[] {
  if (content === null) return [];
  const records: JsonObject[] = [];
  for (const [index, line] of content.split(/\r?\n/).entries()) {
    if (!line.trim()) continue;
    let value: unknown;
    try {
      value = JSON.parse(line);
    } catch {
      throw new EffectRuntimeRequestError(
        `quota run index line ${index + 1} is malformed`,
        "malformed_run_index",
      );
    }
    records.push(requiredObject(value, `quota run index line ${index + 1}`));
  }
  return records;
}

function pyValue(value: unknown): string {
  if (value === true) return "True";
  if (value === false) return "False";
  if (value === null || value === undefined) return "None";
  return String(value);
}

function markdownScalar(value: unknown): string {
  return pyValue(value).replace(/\r/g, " ").replace(/\n/g, " ").replace(/\|/g, "\\|").trim();
}

export function renderQuotaSlotMarkdown(
  payload: JsonObject,
  defaultClassification: "quota_slot_spent" | "quota_slot_voided",
): string {
  const before = jsonObject(payload.before) ?? {};
  const after = jsonObject(payload.after) ?? {};
  const beforeQuota = jsonObject(before.quota) ?? before;
  const afterQuota = jsonObject(after.quota) ?? after;
  const lines = [
    "# LoopX Quota Slot Preview",
    "",
    `- ok: \`${pyValue(payload.ok)}\``,
    `- dry_run: \`${pyValue(payload.dry_run)}\``,
    `- goal_id: \`${pyValue(payload.goal_id)}\``,
    `- classification: \`${pyValue(payload.classification ?? defaultClassification)}\``,
    `- agent_id: \`${pyValue(payload.agent_id ?? "")}\``,
    `- slots: \`${pyValue(payload.slots)}\``,
    `- appended: \`${pyValue(payload.appended)}\``,
    `- registry_mutated: \`${pyValue(payload.registry_mutated)}\``,
    `- would_throttle: \`${pyValue(payload.would_throttle)}\``,
  ];
  if (payload.json_path) lines.push(`- json_path: \`${pyValue(payload.json_path)}\``);
  if (payload.index_path) lines.push(`- index_path: \`${pyValue(payload.index_path)}\``);
  if (payload.reason) lines.push(`- reason: ${pyValue(payload.reason)}`);
  if (Object.keys(before).length) {
    lines.push(
      `- before: state=${pyValue(before.state)} should_run=${pyValue(before.should_run)} ` +
        `slots=${pyValue(beforeQuota.spent_slots)}/${pyValue(beforeQuota.allowed_slots)}`,
    );
  }
  if (Object.keys(after).length) {
    lines.push(
      `- after: state=${pyValue(after.state)} should_run=${pyValue(after.should_run)} ` +
        `slots=${pyValue(afterQuota.spent_slots)}/${pyValue(afterQuota.allowed_slots)}`,
    );
    const summary = jsonObject(after.plan_summary);
    if (summary) {
      lines.push(
        `- after_plan_next_automatic_turn: ${pyValue(summary.next_automatic_turn ?? "none")}`,
      );
    }
  }
  const accountingProjection = jsonObject(payload.accounting_projection);
  if (accountingProjection) {
    lines.push(
      "- accounting_projection: " +
        `settlement_event=${pyValue(accountingProjection.settlement_event_semantics)} ` +
        `spent_slots=${pyValue(accountingProjection.spent_slots_semantics)} ` +
        `before_after=${pyValue(accountingProjection.before_after_semantics)} ` +
        `window_hours=${pyValue(accountingProjection.window_hours)}`,
    );
  }
  if (payload.rolling_window_note) {
    lines.push(`- rolling_window_note: ${pyValue(payload.rolling_window_note)}`);
  }
  const operatorAction = jsonObject(payload.operator_action);
  if (operatorAction) {
    if (payload.error_code) lines.push(`- error_code: \`${pyValue(payload.error_code)}\``);
    if (payload.incident_channel) {
      lines.push(`- incident_channel: \`${pyValue(payload.incident_channel)}\``);
    }
    lines.push(
      "- operator_action: " +
        `action=${markdownScalar(operatorAction.action ?? "")} ` +
        `holder_pid=${markdownScalar(operatorAction.holder_pid ?? "") || "unknown"} ` +
        `retry_mode=${markdownScalar(operatorAction.retry_mode ?? "")}`,
    );
    if (Array.isArray(operatorAction.steps)) {
      for (const step of operatorAction.steps) lines.push(`  - ${markdownScalar(step)}`);
    }
  }
  return `${lines.join("\n")}\n`;
}

function repairedTruncatedTail(
  content: Buffer,
  expectedRecord: JsonObject,
  expectedIndexDigest: string | null,
  expectedIndexBytes: number,
): string | null {
  if (content.length <= expectedIndexBytes) return null;
  const validPrefix = content.subarray(0, expectedIndexBytes);
  const truncatedTail = content.subarray(expectedIndexBytes);
  const expectedLine = Buffer.from(`${JSON.stringify(expectedRecord)}\n`, "utf8");
  if (
    truncatedTail.length >= expectedLine.length ||
    !expectedLine.subarray(0, truncatedTail.length).equals(truncatedTail)
  ) {
    return null;
  }
  if (
    expectedIndexDigest === null
      ? validPrefix.length !== 0
      : sha256Bytes(validPrefix) !== expectedIndexDigest
  ) {
    return null;
  }
  const validPrefixText = validPrefix.toString("utf8");
  parseQuotaAccountingIndex(validPrefixText);
  return `${validPrefixText}${expectedLine.toString("utf8")}`;
}

function effectIdentityValue(
  value: unknown,
): { value: string | null; malformed: boolean } {
  if (value === null || value === undefined || value === "") {
    return { value: null, malformed: false };
  }
  if (typeof value !== "string" || !value.trim()) {
    return { value: null, malformed: true };
  }
  return { value: value.trim(), malformed: false };
}

function resolveEffectIdentity(
  contract: QuotaAccountingArtifactContract,
  record: JsonObject,
  expectedEffectId: string,
  expectedGoalRef: JsonObject | null,
): QuotaAccountingEffectResolution {
  const rawMetadata = record[contract.metadataField];
  const recordEffect = effectIdentityValue(record.effect_ref);
  const recordReferencesExpected = recordEffect.value === expectedEffectId;
  const metadataPresent = rawMetadata !== undefined;
  const metadata = metadataPresent ? jsonObject(rawMetadata) : null;
  if (metadataPresent && metadata === null) {
    if (!recordReferencesExpected) return { kind: "absent" };
    return {
      kind: "conflict",
      reason: `${contract.label} index row has malformed effect metadata`,
      reasonCode: "effect_id_conflict",
    };
  }
  const metadataEffect = effectIdentityValue(metadata?.effect_id);
  const referencesExpected = metadataEffect.value === expectedEffectId ||
    recordReferencesExpected;
  if (!referencesExpected) return { kind: "absent" };
  if (
    metadataEffect.malformed ||
    recordEffect.malformed ||
    (metadataPresent && metadataEffect.value === null)
  ) {
    return {
      kind: "conflict",
      reason: `${contract.label} index row has malformed effect identity`,
      reasonCode: "effect_id_conflict",
    };
  }
  if (
    metadataEffect.value !== null &&
    recordEffect.value !== null &&
    metadataEffect.value !== recordEffect.value
  ) {
    return {
      kind: "conflict",
      reason: `${contract.label} index row has conflicting effect identities`,
      reasonCode: "effect_id_conflict",
    };
  }
  const goalConflict = ownerConflict(
    exactGoalRef(record.goal_ref, `${contract.label} index row goal_ref`),
    expectedGoalRef,
    `${contract.label} effect identity`,
  );
  if (goalConflict) {
    return {
      kind: "conflict",
      reason: goalConflict,
      reasonCode: "goal_instance_conflict",
    };
  }
  return { kind: "matched", record };
}

export function resolveQuotaAccountingEffect(
  kind: QuotaAccountingArtifactKind,
  records: readonly JsonObject[],
  effectId: string,
  goalRef: JsonObject | null = null,
): QuotaAccountingEffectResolution {
  const contract = contractFor(kind);
  for (const record of [...records].reverse()) {
    if (record.classification !== contract.classification) continue;
    const resolution = resolveEffectIdentity(
      contract,
      record,
      effectId,
      goalRef,
    );
    if (resolution.kind !== "absent") return resolution;
  }
  return { kind: "absent" };
}

export async function lookupQuotaAccountingReplay(
  kind: QuotaAccountingArtifactKind,
  indexPath: string,
  effectId: string,
  readOnly: boolean,
  goalRef: JsonObject | null = null,
  indexLockHeld = false,
): Promise<{
  resolution: QuotaAccountingEffectResolution;
  indexDigest: string | null;
}> {
  const lookup = async () => {
    const content = await readOptionalText(indexPath);
    return {
      resolution: resolveQuotaAccountingEffect(
        kind,
        parseQuotaAccountingIndex(content),
        effectId,
        goalRef,
      ),
      indexDigest: await quotaAccountingIndexDigest(indexPath),
    };
  };
  return readOnly || indexLockHeld
    ? await lookup()
    : await withFileMutationLock(indexPath, lookup);
}

function receiptObject(
  contract: QuotaAccountingArtifactContract,
  value: unknown,
): QuotaAccountingArtifactReceipt {
  const receipt = requiredObject(value, `${contract.label} transaction receipt`);
  if (receipt.schema_version !== contract.receiptSchema) {
    const capitalizedLabel = `${contract.label[0]?.toUpperCase()}${contract.label.slice(1)}`;
    throw new EffectRuntimeRequestError(
      `${capitalizedLabel} transaction receipt schema mismatch`,
    );
  }
  const status = requireStringLiteral(
    receipt.status,
    ["prepared", "committed"] as const,
    "receipt.status",
  );
  const expectedIndexBytes = requiredInteger(
    receipt.expected_index_bytes,
    "receipt.expected_index_bytes",
  );
  const goalRef = exactGoalRef(
    receipt.goal_ref,
    "transaction receipt goal_ref",
  );
  if (expectedIndexBytes < 0) {
    throw new EffectRuntimeRequestError(
      "receipt.expected_index_bytes cannot be negative",
    );
  }
  return {
    schema_version: contract.receiptSchema,
    effect_id: requiredString(receipt.effect_id, "receipt.effect_id"),
    request_digest: requiredString(receipt.request_digest, "receipt.request_digest"),
    status,
    json_path: requiredString(receipt.json_path, "receipt.json_path"),
    markdown_path: requiredString(receipt.markdown_path, "receipt.markdown_path"),
    index_path: requiredString(receipt.index_path, "receipt.index_path"),
    expected_index_digest: optionalString(
      receipt.expected_index_digest,
      "receipt.expected_index_digest",
    ),
    expected_index_bytes: expectedIndexBytes,
    record: requiredObject(receipt.record, "receipt.record"),
    index_record: requiredObject(receipt.index_record, "receipt.index_record"),
    markdown: requiredString(receipt.markdown, "receipt.markdown"),
    payload: requiredObject(receipt.payload, "receipt.payload"),
    ...(goalRef === null ? {} : { goal_ref: goalRef }),
  };
}

function validateReceiptPaths(
  contract: QuotaAccountingArtifactContract,
  runsDir: string,
  receipt: QuotaAccountingArtifactReceipt,
): void {
  const resolvedRunsDir = resolve(runsDir);
  const resolvedIndexPath = resolve(receipt.index_path);
  if (resolvedIndexPath !== resolve(runsDir, "index.jsonl")) {
    throw new EffectRuntimeRequestError(
      `${contract.label} transaction receipt index path is outside its run directory`,
      "malformed_transaction_receipt",
    );
  }
  for (const [label, path, extension] of [
    ["JSON", receipt.json_path, ".json"],
    ["Markdown", receipt.markdown_path, ".md"],
  ] as const) {
    const resolvedPath = resolve(path);
    if (dirname(resolvedPath) !== resolvedRunsDir || extname(resolvedPath) !== extension) {
      throw new EffectRuntimeRequestError(
        `${contract.label} transaction receipt ${label} path is outside its run directory`,
        "malformed_transaction_receipt",
      );
    }
  }
  const generatedAt = requiredString(
    receipt.record.generated_at,
    "receipt.record.generated_at",
  );
  const effectDigest = sha256(receipt.effect_id).slice(
    "sha256:".length,
    "sha256:".length + 24,
  );
  const base = `${runStem(generatedAt)}-${contract.artifactSlug}-${effectDigest}`;
  const jsonName = basename(receipt.json_path);
  const jsonStem = jsonName.slice(0, -".json".length);
  const suffix = jsonStem.slice(base.length);
  if (
    !jsonStem.startsWith(base) ||
    (suffix !== "" && !/^-(?:[2-9]|[1-9][0-9]+)$/.test(suffix)) ||
    basename(receipt.markdown_path) !== `${jsonStem}.md`
  ) {
    throw new EffectRuntimeRequestError(
      `${contract.label} transaction receipt artifact names do not match its effect identity`,
      "malformed_transaction_receipt",
    );
  }
  if (
    receipt.index_record.json_path !== receipt.json_path ||
    receipt.index_record.markdown_path !== receipt.markdown_path ||
    receipt.payload.json_path !== receipt.json_path ||
    receipt.payload.markdown_path !== receipt.markdown_path ||
    receipt.payload.index_path !== receipt.index_path
  ) {
    throw new EffectRuntimeRequestError(
      `${contract.label} transaction receipt artifact paths do not match its projections`,
      "malformed_transaction_receipt",
    );
  }
  const recordMetadata = requiredObject(
    receipt.record[contract.metadataField],
    `receipt.record.${contract.metadataField}`,
  );
  const indexMetadata = requiredObject(
    receipt.index_record[contract.metadataField],
    `receipt.index_record.${contract.metadataField}`,
  );
  const receiptGoalRef = receipt.goal_ref ?? null;
  const quotaEvent = requiredObject(
    receipt.record.quota_event,
    "receipt.record.quota_event",
  );
  for (const [label, value] of [
    ["record", receipt.record.goal_ref],
    ["quota event", quotaEvent.goal_ref],
    ["index record", receipt.index_record.goal_ref],
    ["payload", receipt.payload.goal_ref],
  ] as const) {
    const projected = exactGoalRef(
      value,
      `receipt ${label} goal_ref`,
    );
    if (!sameGoalRef(projected, receiptGoalRef)) {
      throw new EffectRuntimeRequestError(
        `${contract.label} transaction receipt ${label} GoalRef does not match its owner`,
        "malformed_transaction_receipt",
      );
    }
  }
  for (const [label, projection, expected] of [
    ["record classification", receipt.record.classification, contract.classification],
    ["index classification", receipt.index_record.classification, contract.classification],
    ["record effect", recordMetadata.effect_id, receipt.effect_id],
    ["index effect", indexMetadata.effect_id, receipt.effect_id],
    ["record digest", recordMetadata.request_digest, receipt.request_digest],
    ["index digest", indexMetadata.request_digest, receipt.request_digest],
  ] as const) {
    if (projection !== expected) {
      throw new EffectRuntimeRequestError(
        `${contract.label} transaction receipt ${label} does not match its identity`,
        "malformed_transaction_receipt",
      );
    }
  }
}

async function rejectSymlinkPath(path: string, label: string): Promise<void> {
  try {
    if ((await lstat(path)).isSymbolicLink()) {
      throw new EffectRuntimeRequestError(
        `${label} must not be a symbolic link`,
        "malformed_transaction_receipt",
      );
    }
  } catch (error) {
    if (isNodeErrorCode(error, "ENOENT")) return;
    throw error;
  }
}

async function readReceipt(
  contract: QuotaAccountingArtifactContract,
  path: string,
  runsDir: string,
): Promise<QuotaAccountingArtifactReceipt | null> {
  const content = await readOptionalText(path);
  if (content === null) return null;
  let value: unknown;
  try {
    value = JSON.parse(content);
  } catch {
    throw new EffectRuntimeRequestError(
      `${contract.label} transaction receipt is malformed`,
      "malformed_transaction_receipt",
    );
  }
  const receipt = receiptObject(contract, value);
  validateReceiptPaths(contract, runsDir, receipt);
  await Promise.all([
    rejectSymlinkPath(receipt.json_path, `${contract.label} JSON artifact`),
    rejectSymlinkPath(receipt.markdown_path, `${contract.label} Markdown artifact`),
    rejectSymlinkPath(receipt.index_path, `${contract.label} run index`),
  ]);
  return receipt;
}

async function ensureJsonArtifact(
  contract: QuotaAccountingArtifactContract,
  path: string,
  expected: JsonObject,
): Promise<boolean> {
  const existing = await readOptionalText(path);
  if (existing === null) {
    await atomicWriteJson(path, expected);
    return true;
  }
  let actual: unknown;
  try {
    actual = JSON.parse(existing);
  } catch {
    throw new EffectRuntimeRequestError(
      `${contract.label} JSON artifact is malformed`,
      "artifact_conflict",
    );
  }
  if (canonicalJson(actual) !== canonicalJson(expected)) {
    throw new EffectRuntimeRequestError(
      `${contract.label} JSON artifact conflicts with its transaction receipt`,
      "artifact_conflict",
    );
  }
  return false;
}

async function ensureMarkdownArtifact(
  contract: QuotaAccountingArtifactContract,
  path: string,
  expected: string,
): Promise<boolean> {
  const existing = await readOptionalText(path);
  if (existing === null) {
    await atomicWriteText(path, expected);
    return true;
  }
  if (existing !== expected) {
    throw new EffectRuntimeRequestError(
      `${contract.label} Markdown artifact conflicts with its transaction receipt`,
      "artifact_conflict",
    );
  }
  return false;
}

async function readReceiptIndex(
  receipt: QuotaAccountingArtifactReceipt,
): Promise<{ content: string | null; records: JsonObject[]; repaired: boolean }> {
  const indexBytes = await readOptionalBytes(receipt.index_path);
  let content = indexBytes === null ? null : indexBytes.toString("utf8");
  try {
    return {
      content,
      records: parseQuotaAccountingIndex(content),
      repaired: false,
    };
  } catch (error) {
    const recovered = indexBytes === null
      ? null
      : repairedTruncatedTail(
        indexBytes,
        receipt.index_record,
        receipt.expected_index_digest,
        receipt.expected_index_bytes,
      );
    if (recovered === null) throw error;
    await atomicWriteText(receipt.index_path, recovered);
    content = recovered;
    return {
      content,
      records: parseQuotaAccountingIndex(content),
      repaired: true,
    };
  }
}

function assertReceiptIndexPrefix(
  contract: QuotaAccountingArtifactContract,
  receipt: QuotaAccountingArtifactReceipt,
  content: string | null,
): void {
  const current = Buffer.from(content ?? "", "utf8");
  const expectedBytes = receipt.expected_index_bytes;
  const expectedDigest = receipt.expected_index_digest;
  const prefixMatches = current.length >= expectedBytes && (
    expectedDigest === null
      ? expectedBytes === 0
      : sha256Bytes(current.subarray(0, expectedBytes)) === expectedDigest
  );
  if (!prefixMatches) {
    throw new EffectRuntimeRequestError(
      `${contract.label} run index no longer retains its transaction prefix`,
      "artifact_conflict",
    );
  }
}

async function ensureReceiptArtifacts(
  kind: QuotaAccountingArtifactKind,
  contract: QuotaAccountingArtifactContract,
  receipt: QuotaAccountingArtifactReceipt,
): Promise<boolean> {
  const index = await readReceiptIndex(receipt);
  let repaired = index.repaired;
  const matchResolution = resolveQuotaAccountingEffect(
    kind,
    index.records,
    receipt.effect_id,
    receipt.goal_ref ?? null,
  );
  if (matchResolution.kind === "conflict") {
    throw new EffectRuntimeRequestError(
      matchResolution.reason,
      matchResolution.reasonCode,
    );
  }
  const match = matchResolution.kind === "matched"
    ? matchResolution.record
    : null;
  if (match) {
    const metadata = jsonObject(match[contract.metadataField]);
    if (metadata?.request_digest !== receipt.request_digest) {
      throw new EffectRuntimeRequestError(
        `${contract.label} effect identity is already bound to a different request`,
        "effect_id_conflict",
      );
    }
    if (canonicalJson(match) !== canonicalJson(receipt.index_record)) {
      throw new EffectRuntimeRequestError(
        `${contract.label} index record conflicts with its transaction receipt`,
        "artifact_conflict",
      );
    }
  } else {
    assertReceiptIndexPrefix(contract, receipt, index.content);
  }
  repaired = await ensureJsonArtifact(contract, receipt.json_path, receipt.record) ||
    repaired;
  repaired = await ensureMarkdownArtifact(
    contract,
    receipt.markdown_path,
    receipt.markdown,
  ) || repaired;
  if (!match) {
    const prefix = index.content ?? "";
    if (prefix && !prefix.endsWith("\n")) {
      await atomicWriteText(
        receipt.index_path,
        `${prefix}\n${JSON.stringify(receipt.index_record)}\n`,
      );
    } else {
      await appendJsonLine(receipt.index_path, receipt.index_record);
    }
    repaired = true;
  }
  return repaired;
}

export async function commitQuotaAccountingArtifactTransaction(
  request: QuotaAccountingArtifactCommitRequest,
): Promise<QuotaAccountingArtifactCommitOutcome> {
  const contract = contractFor(request.kind);
  const indexPath = join(request.runsDir, "index.jsonl");
  const requestedGoalRef = exactGoalRef(
    request.goalRef,
    `${contract.label} request goal_ref`,
  );
  const commit = async (): Promise<QuotaAccountingArtifactCommitOutcome> => {
    await rejectSymlinkPath(indexPath, `${contract.label} run index`);
    const receiptPath = transactionPath(contract, request.runsDir, request.effectId);
    const existingReceipt = await readReceipt(contract, receiptPath, request.runsDir);
    if (existingReceipt) {
      const conflict = ownerConflict(
        existingReceipt.goal_ref ?? null,
        requestedGoalRef,
        `${contract.label} transaction`,
      );
      if (conflict) {
        return {
          status: "conflict",
          reason: conflict,
          reasonCode: "goal_instance_conflict",
          indexDigest: await quotaAccountingIndexDigest(indexPath),
        };
      }
      if (
        existingReceipt.effect_id !== request.effectId ||
        existingReceipt.request_digest !== request.requestDigest
      ) {
        return {
          status: "conflict",
          reason: `${contract.label} effect identity is already bound to a different request`,
          reasonCode: "effect_id_conflict",
          indexDigest: await quotaAccountingIndexDigest(indexPath),
        };
      }
      const repaired = await ensureReceiptArtifacts(
        request.kind,
        contract,
        existingReceipt,
      );
      const committedReceipt = {
        ...existingReceipt,
        status: "committed",
      } satisfies QuotaAccountingArtifactReceipt;
      if (existingReceipt.status !== "committed" || repaired) {
        await atomicWriteJson(receiptPath, committedReceipt);
      }
      return {
        status: repaired ? "repaired" : "replayed",
        receipt: committedReceipt,
        indexDigest: await quotaAccountingIndexDigest(indexPath),
      };
    }

    const currentIndexBytes = await readOptionalBytes(indexPath);
    const currentIndexContent = currentIndexBytes === null
      ? null
      : currentIndexBytes.toString("utf8");
    const currentDigest = currentIndexBytes === null
      ? null
      : sha256Bytes(currentIndexBytes);
    if (request.expectedIndexDigest !== currentDigest) {
      return {
        status: "conflict",
        reason: "quota run index compare-and-swap precondition failed",
        reasonCode: "index_digest_conflict",
        indexDigest: currentDigest,
      };
    }
    const currentRecords = parseQuotaAccountingIndex(currentIndexContent);
    const duplicateResolution = resolveQuotaAccountingEffect(
      request.kind,
      currentRecords,
      request.effectId,
      requestedGoalRef,
    );
    if (duplicateResolution.kind === "conflict") {
      return {
        status: "conflict",
        reason: duplicateResolution.reason,
        reasonCode: duplicateResolution.reasonCode,
        indexDigest: currentDigest,
      };
    }
    if (duplicateResolution.kind === "matched") {
      return {
        status: "conflict",
        reason: `${contract.label} effect identity already exists without a matching transaction receipt`,
        reasonCode: "effect_id_conflict",
        indexDigest: currentDigest,
      };
    }

    const { jsonPath, markdownPath } = await nextQuotaAccountingArtifactPaths(
      request.kind,
      request.runsDir,
      request.generatedAt,
      request.effectId,
    );
    const preparation = await request.prepare({
      jsonPath,
      markdownPath,
      indexPath,
      indexDigest: currentDigest,
      indexRecords: currentRecords,
    });
    if (preparation.kind === "not_found") {
      return {
        status: "not_found",
        reason: preparation.reason,
        payload: preparation.payload,
        indexDigest: currentDigest,
      };
    }
    const prepared = {
      schema_version: contract.receiptSchema,
      effect_id: request.effectId,
      request_digest: request.requestDigest,
      status: "prepared",
      json_path: jsonPath,
      markdown_path: markdownPath,
      index_path: indexPath,
      expected_index_digest: currentDigest,
      expected_index_bytes: currentIndexBytes?.length ?? 0,
      record: preparation.record,
      index_record: preparation.indexRecord,
      markdown: preparation.markdown,
      payload: preparation.payload,
      ...(requestedGoalRef === null ? {} : { goal_ref: requestedGoalRef }),
    } satisfies QuotaAccountingArtifactReceipt;
    validateReceiptPaths(contract, request.runsDir, prepared);
    await atomicWriteJson(receiptPath, prepared);
    await ensureReceiptArtifacts(request.kind, contract, prepared);
    const committedReceipt = {
      ...prepared,
      status: "committed",
    } satisfies QuotaAccountingArtifactReceipt;
    await atomicWriteJson(receiptPath, committedReceipt);
    return {
      status: "written",
      receipt: committedReceipt,
      indexDigest: await quotaAccountingIndexDigest(indexPath),
    };
  };
  return request.indexLockHeld
    ? await commit()
    : await withFileMutationLock(indexPath, commit);
}
