/** Durable WAL/transaction commit engine and public entrypoint for quota monitor-poll. */
import { access, readFile, rm } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";

import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import type { JsonObject, SettlementIdentity } from "../effect_program.ts";
import { appendJsonLine, atomicWriteJson, atomicWriteText, withFileMutationLock } from "../effect_runtime_io.ts";
import { jsonObject, requireJsonObject as requiredObject } from "../runtime_decode.ts";
import {
  admission,
  buildRecord,
  indexRecordFor,
  monitorMarkdown,
  payloadFor,
  projectAuxiliaryContinuation,
  type Admission,
} from "./monitor_poll_admission.ts";
import {
  conflictResult,
  MONITOR_PENDING_ADMISSION_SCHEMA,
  monitorResult,
  providerPlanObject,
  pythonJson,
  QUOTA_LEASED_MONITOR_POLL_COMMIT_REQUEST_SCHEMA,
  QUOTA_MONITOR_POLL_CLASSIFICATION,
  QUOTA_MONITOR_POLL_COMMIT_RECEIPT_SCHEMA,
  QUOTA_MONITOR_POLL_COMMIT_REQUEST_SCHEMA,
  QUOTA_MONITOR_POLL_COMMIT_RESULT_SCHEMA,
  receiptObject,
  requestDigest,
  requestObject,
  sha256,
  sha256Hex,
  type DurableMonitorReceipt,
  type MonitorProviderPlan,
  type MonitorReceipt,
  type MonitorRequest,
  type PendingMonitorReceipt,
  type QuotaMonitorPollCommitResult,
} from "./monitor_poll_codec.ts";
import {
  conflictFields,
  providerDispatchPlan,
  providerPlanFor,
  requestWithProvider,
  validatedProviderReceipt,
  validateNoEffect,
} from "./monitor_poll_provider.ts";
import {
  quotaGoalRef,
  quotaOwnerOwnsProjection,
  withBorrowedQuotaAccountingOwner,
  type QuotaAccountingOwner,
} from "./source_admission.ts";

export {
  QUOTA_LEASED_MONITOR_POLL_COMMIT_REQUEST_SCHEMA,
  QUOTA_MONITOR_POLL_CLASSIFICATION,
  QUOTA_MONITOR_POLL_COMMIT_RECEIPT_SCHEMA,
  QUOTA_MONITOR_POLL_COMMIT_REQUEST_SCHEMA,
  QUOTA_MONITOR_POLL_COMMIT_RESULT_SCHEMA,
  type QuotaMonitorPollCommitResult,
};

function isNodeErrorCode(error: unknown, code: string): boolean {
  return error instanceof Error && "code" in error && error.code === code;
}

async function readOptionalBytes(path: string): Promise<Buffer | null> {
  try { return await readFile(path); }
  catch (error) { if (isNodeErrorCode(error, "ENOENT")) return null; throw error; }
}

async function readOptionalText(path: string): Promise<string | null> {
  return (await readOptionalBytes(path))?.toString("utf8") ?? null;
}

async function pathExists(path: string): Promise<boolean> {
  try { await access(path); return true; }
  catch (error) { if (isNodeErrorCode(error, "ENOENT")) return false; throw error; }
}

function runStem(generatedAt: string): string {
  const normalized = generatedAt.replace(/[^0-9A-Za-z-]+/g, "-").replace(/^-+/, "");
  let trailingHyphenStart = normalized.length;
  while (trailingHyphenStart > 0 && normalized[trailingHyphenStart - 1] === "-") trailingHyphenStart -= 1;
  const stem = normalized.slice(0, trailingHyphenStart);
  if (!stem) throw new EffectRuntimeRequestError("generated_at cannot form a run artifact name");
  return stem;
}

async function nextArtifactPaths(
  runsDir: string,
  generatedAt: string,
  effectId: string,
): Promise<{ jsonPath: string; markdownPath: string }> {
  const base = `${runStem(generatedAt)}-quota-monitor-poll-${sha256Hex(effectId).slice(0, 24)}`;
  for (let index = 1; ; index += 1) {
    const stem = index === 1 ? base : `${base}-${index}`;
    const jsonPath = join(runsDir, `${stem}.json`), markdownPath = join(runsDir, `${stem}.md`);
    if (!await pathExists(jsonPath) && !await pathExists(markdownPath)) return { jsonPath, markdownPath };
  }
}

const transactionPath = (runsDir: string, effectId: string): string =>
  join(runsDir, ".transactions", "quota-monitor-poll", `${sha256Hex(effectId).slice(0, 24)}.json`);

const committedReceiptStagePath = (receiptPath: string): string => `${receiptPath}.committed`;

export async function quotaMonitorPollIndexDigest(indexPath: string): Promise<string | null> {
  const content = await readOptionalBytes(indexPath);
  return content === null ? null : sha256(content);
}

function indexRecords(content: string | null): JsonObject[] {
  if (content === null) return [];
  const records: JsonObject[] = [];
  for (const [index, line] of content.split(/\r?\n/).entries()) {
    if (!line.trim()) continue;
    let value: unknown;
    try { value = JSON.parse(line); }
    catch { throw new EffectRuntimeRequestError(`quota run index line ${index + 1} is malformed`, "malformed_run_index"); }
    records.push(requiredObject(value, `quota run index line ${index + 1}`));
  }
  return records;
}

function matchingIndexRecord(records: readonly JsonObject[], effectId: string): JsonObject | null {
  for (const record of [...records].reverse()) {
    if (jsonObject(record.quota_monitor_poll_commit)?.effect_id === effectId) return record;
  }
  return null;
}

function pendingIndexHistoryIntact(pending: PendingMonitorReceipt, current: Buffer | null): boolean {
  const expectedBytes = pending.expected_index_bytes;
  if (!Number.isSafeInteger(expectedBytes) || expectedBytes < 0 || (current?.length ?? 0) < expectedBytes) {
    return false;
  }
  if (pending.expected_index_digest === null) return expectedBytes === 0;
  return sha256((current ?? Buffer.alloc(0)).subarray(0, expectedBytes)) === pending.expected_index_digest;
}

async function readReceipt(path: string): Promise<MonitorReceipt | null> {
  const content = await readOptionalText(path);
  if (content === null) return null;
  try { return receiptObject(JSON.parse(content)); }
  catch (error) {
    if (error instanceof SyntaxError) {
      throw new EffectRuntimeRequestError("quota monitor-poll transaction receipt is malformed", "malformed_transaction_receipt");
    }
    throw error;
  }
}

/** A stored observation may already have reached its provider even before the
 * run index is settled. Its existing receipt owner must recover or prove no
 * effect before a competing unavailable closeout can be admitted. */
export async function hasMonitorPollTransaction(
  runtimeRoot: string,
  identity: SettlementIdentity,
  owner: QuotaAccountingOwner,
): Promise<boolean> {
  if (!identity.todo_id) return false;
  const runsDir = join(runtimeRoot, "goals", identity.goal_id, "runs");
  const base = `quota-monitor-poll:${identity.goal_id}:${identity.agent_id}:${identity.turn_instance_id}`;
  for (const effectId of [base, `${base}:todo:${identity.todo_id}`]) {
    const receipt = await readReceipt(transactionPath(runsDir, effectId));
    if (!receipt) continue;
    validateReceiptScope(receipt, runsDir, join(runsDir, "index.jsonl"));
    const facts = receipt.status === "provider_pending" ? receipt.provider_plan : receipt.index_record;
    if (receipt.effect_id !== effectId || facts.goal_id !== identity.goal_id || facts.todo_id !== identity.todo_id) {
      continue;
    }
    if (receipt.status === "provider_pending") {
      // The provider plan names its actor, not the Turn's accounting Agent.
      // Legacy pending receipts retain this exact accounting effect identity.
      if (
        receipt.schema_version === MONITOR_PENDING_ADMISSION_SCHEMA &&
        (receipt.admitted_decision.goal_id !== identity.goal_id ||
          receipt.admitted_decision.agent_id !== identity.agent_id)
      ) {
        throw new EffectRuntimeRequestError(
          "stored Monitor admission conflicts with its accounting identity", "malformed_transaction_receipt",
        );
      }
    } else if (facts.agent_id !== identity.agent_id) continue;
    const goalRef = receipt.status === "provider_pending" ? receipt.goal_ref : facts.goal_ref;
    if (receipt.status === "provider_pending" && goalRef === undefined && owner.kind !== "alias") {
      throw new EffectRuntimeRequestError(
        "historical pending Monitor transaction lacks GoalRef scope; reconcile its original owner before unavailable closeout",
      );
    }
    if (!quotaOwnerOwnsProjection(owner, goalRef)) continue;
    return true;
  }
  return false;
}

function validateReceiptScope(receipt: MonitorReceipt, runsDir: string, indexPath: string): void {
  if (resolve(receipt.index_path) !== resolve(indexPath)) {
    throw new EffectRuntimeRequestError(
      "quota monitor-poll receipt artifact paths do not match the transaction scope", "malformed_transaction_receipt",
    );
  }
  if (receipt.status === "provider_pending") return;
  const jsonPath = resolve(receipt.json_path);
  const markdownPath = resolve(receipt.markdown_path);
  const resolvedRunsDir = resolve(runsDir);
  const effectToken = sha256Hex(receipt.effect_id).slice(0, 24);
  const jsonName = basename(jsonPath);
  const markdownName = basename(markdownPath);
  const artifact = new RegExp(`-quota-monitor-poll-${effectToken}(?:-[2-9][0-9]*)?\\.(json|md)$`);
  if (
    dirname(jsonPath) !== resolvedRunsDir || dirname(markdownPath) !== resolvedRunsDir ||
    !artifact.test(jsonName) || !artifact.test(markdownName) ||
    jsonName.slice(0, -5) !== markdownName.slice(0, -3)
  ) {
    throw new EffectRuntimeRequestError(
      "quota monitor-poll receipt artifact paths do not match the transaction scope", "malformed_transaction_receipt",
    );
  }
}

function repairedTruncatedTail(content: Buffer, receipt: DurableMonitorReceipt): string | null {
  if (content.length <= receipt.expected_index_bytes) return null;
  const prefix = content.subarray(0, receipt.expected_index_bytes);
  const tail = content.subarray(receipt.expected_index_bytes);
  const expectedLine = Buffer.from(`${JSON.stringify(receipt.index_record)}\n`, "utf8");
  if (tail.length >= expectedLine.length || !expectedLine.subarray(0, tail.length).equals(tail)) return null;
  if (receipt.expected_index_digest === null ? prefix.length !== 0 : sha256(prefix) !== receipt.expected_index_digest) {
    return null;
  }
  indexRecords(prefix.toString("utf8"));
  return `${prefix.toString("utf8")}${expectedLine.toString("utf8")}`;
}

async function ensureJsonArtifact(path: string, expected: JsonObject): Promise<boolean> {
  const content = await readOptionalText(path);
  if (content === null) { await atomicWriteJson(path, expected); return true; }
  let actual: unknown;
  try { actual = JSON.parse(content); }
  catch { throw new EffectRuntimeRequestError("quota monitor-poll JSON artifact is malformed", "artifact_conflict"); }
  if (pythonJson(actual) !== pythonJson(expected)) {
    throw new EffectRuntimeRequestError("quota monitor-poll JSON artifact conflicts with its transaction receipt", "artifact_conflict");
  }
  return false;
}

async function ensureMarkdownArtifact(path: string, expected: string): Promise<boolean> {
  const markdown = await readOptionalText(path);
  if (markdown === null) { await atomicWriteText(path, expected); return true; }
  if (markdown !== expected) {
    throw new EffectRuntimeRequestError("quota monitor-poll Markdown artifact conflicts with its transaction receipt", "artifact_conflict");
  }
  return false;
}

async function ensureContentArtifacts(receipt: DurableMonitorReceipt): Promise<boolean> {
  const [jsonRepaired, markdownRepaired] = await Promise.all([
    ensureJsonArtifact(receipt.json_path, receipt.record),
    ensureMarkdownArtifact(receipt.markdown_path, receipt.markdown),
  ]);
  return jsonRepaired || markdownRepaired;
}

async function ensureIndexArtifact(receipt: DurableMonitorReceipt, contentRepaired: boolean): Promise<boolean> {
  let repaired = contentRepaired;
  const bytes = await readOptionalBytes(receipt.index_path);
  let content = bytes?.toString("utf8") ?? null;
  let records: JsonObject[];
  try {
    records = indexRecords(content);
  } catch (error) {
    const recovered = bytes ? repairedTruncatedTail(bytes, receipt) : null;
    if (recovered === null) throw error;
    await atomicWriteText(receipt.index_path, recovered);
    content = recovered;
    records = indexRecords(content);
    repaired = true;
  }
  const match = matchingIndexRecord(records, receipt.effect_id);
  if (match) {
    const metadata = requiredObject(match.quota_monitor_poll_commit, "index quota_monitor_poll_commit");
    if (metadata.request_digest !== receipt.request_digest) {
      throw new EffectRuntimeRequestError("quota monitor-poll effect identity is already bound to a different request", "effect_id_conflict");
    }
    if (pythonJson(match) !== pythonJson(receipt.index_record)) {
      throw new EffectRuntimeRequestError("quota monitor-poll index record conflicts with its transaction receipt", "artifact_conflict");
    }
  } else {
    const currentBytes = bytes ?? Buffer.alloc(0);
    const currentDigest = bytes === null ? null : sha256(bytes);
    if (currentBytes.length !== receipt.expected_index_bytes || currentDigest !== receipt.expected_index_digest) {
      throw new EffectRuntimeRequestError("quota monitor-poll index history conflicts with its transaction receipt", "artifact_conflict");
    }
    const prefix = content ?? "";
    if (prefix && !prefix.endsWith("\n")) {
      await atomicWriteText(receipt.index_path, `${prefix}\n${JSON.stringify(receipt.index_record)}\n`);
    } else {
      await appendJsonLine(receipt.index_path, receipt.index_record);
    }
    repaired = true;
  }
  return repaired;
}

async function ensureArtifacts(receipt: DurableMonitorReceipt): Promise<boolean> {
  return await ensureIndexArtifact(receipt, await ensureContentArtifacts(receipt));
}

function effectConflict(
  request: MonitorRequest,
  fingerprint: string,
  indexDigest: string | null,
  receipt: MonitorReceipt,
): QuotaMonitorPollCommitResult {
  return conflictResult(
    request, fingerprint, "quota monitor-poll effect identity is already bound to a different request",
    indexDigest, "effect_id_conflict", { conflict_fields: conflictFields(request, receipt) },
  );
}

function verifiedProviderPlan(existing: PendingMonitorReceipt | null, request: MonitorRequest): MonitorProviderPlan {
  const plan = existing ? providerPlanObject(existing.provider_plan) : providerPlanFor(request);
  const expectedPlan = providerPlanFor(
    { ...request, generated_at: plan.generated_at },
    plan.gate_scope_guard === true,
    plan.legacy_batch_version ?? null,
  );
  if (pythonJson(plan) !== pythonJson(expectedPlan)) {
    throw new EffectRuntimeRequestError(
      "quota monitor-poll provider plan conflicts with its transaction receipt", "malformed_transaction_receipt",
    );
  }
  return plan;
}

async function replayDurableReceipt(
  request: MonitorRequest,
  fingerprint: string,
  receiptPath: string,
  receipt: DurableMonitorReceipt,
): Promise<QuotaMonitorPollCommitResult> {
  const repaired = await ensureArtifacts(receipt);
  if (receipt.status === "prepared") await rm(committedReceiptStagePath(receiptPath), { force: true });
  const replayPayload: JsonObject = {
    ...receipt.payload,
    appended: repaired,
    replayed: request.turn_instance_id ? true : receipt.payload.replayed,
    todo_writeback: null,
    transaction_repaired: repaired,
    reason: repaired
      ? "quota monitor-poll commit repaired its durable transaction artifacts"
      : "replayed existing monitor poll event for the same effect identity",
  };
  await projectAuxiliaryContinuation(request, replayPayload);
  return monitorResult(
    request, fingerprint, repaired ? "repaired" : "replayed", receipt.record, replayPayload,
    String(replayPayload.reason), await quotaMonitorPollIndexDigest(receipt.index_path), null, receipt.index_record,
  );
}

async function evaluateDryRunRequest(
  request: MonitorRequest,
  fingerprint: string,
  providerNeeded: boolean,
): Promise<QuotaMonitorPollCommitResult> {
  // A provider may mutate the Todo registry, so previews must pass admission
  // before returning a provider plan.
  const allowed = await admission(request);
  if (request.phase === "preflight") {
    const providerPlan = providerPlanFor(request);
    const indexDigest = request.runtime_root
      ? await quotaMonitorPollIndexDigest(join(request.runtime_root, "goals", request.goal_id, "runs", "index.jsonl"))
      : null;
    return monitorResult(
      request, fingerprint, "provider_required", null, { ok: true, dry_run: true, provider_required: true },
      "quota monitor-poll Todo provider is required", indexDigest, providerDispatchPlan(providerPlan, request),
    );
  }
  let effectiveRequest = request;
  if (providerNeeded) {
    if (!request.provider_receipt) throw new EffectRuntimeRequestError("quota monitor-poll Todo commit requires a provider receipt");
    const plan = providerPlanFor(request);
    effectiveRequest = requestWithProvider(request, plan, validatedProviderReceipt(request.provider_receipt, plan));
  }
  const record = buildRecord(effectiveRequest, allowed);
  const runsDir = request.runtime_root ? join(request.runtime_root, "goals", request.goal_id, "runs") : null;
  const indexPath = runsDir ? join(runsDir, "index.jsonl") : "";
  const paths = runsDir
    ? await nextArtifactPaths(runsDir, effectiveRequest.generated_at, request.effect_id)
    : { jsonPath: "", markdownPath: "" };
  return monitorResult(
    effectiveRequest, fingerprint, "preview", record,
    await payloadFor(effectiveRequest, record, paths.jsonPath, paths.markdownPath, indexPath, {
      appended: false, replayed: false, repaired: false,
    }),
    "quota monitor-poll transaction preview evaluated by TypeScript",
    indexPath ? await quotaMonitorPollIndexDigest(indexPath) : null,
  );
}

async function evaluateQuotaMonitorPollRequest(
  request: MonitorRequest,
  indexLockHeld: boolean,
): Promise<QuotaMonitorPollCommitResult> {
  const fingerprint = requestDigest(request);
  if (request.phase === "provider_rejected" && !request.execute) {
    throw new EffectRuntimeRequestError("provider rejection recovery requires execute");
  }
  if (request.phase === "event") {
    const record = buildRecord(request, await admission(request));
    return monitorResult(request, fingerprint, "preview", record, { record }, "quota monitor-poll event preview evaluated by TypeScript");
  }
  const providerNeeded = Boolean(request.observation.todo_id || request.observation.target_key);
  if (request.phase === "preflight" && !providerNeeded) {
    throw new EffectRuntimeRequestError("quota monitor-poll preflight requires a Todo provider target");
  }
  if (!request.execute) return await evaluateDryRunRequest(request, fingerprint, providerNeeded);
  if (!request.runtime_root) throw new EffectRuntimeRequestError("executing quota monitor-poll commit requires runtime_root");

  const runsDir = join(request.runtime_root, "goals", request.goal_id, "runs");
  const indexPath = join(runsDir, "index.jsonl");
  const commit = async (): Promise<QuotaMonitorPollCommitResult> => {
    const receiptPath = transactionPath(runsDir, request.effect_id);
    const existing = await readReceipt(receiptPath);
    if (existing) {
      validateReceiptScope(existing, runsDir, indexPath);
      if (existing.effect_id !== request.effect_id || existing.request_digest !== fingerprint) {
        return effectConflict(request, fingerprint, await quotaMonitorPollIndexDigest(indexPath), existing);
      }
      if (existing.status !== "provider_pending") {
        if (request.phase === "provider_rejected") {
          throw new EffectRuntimeRequestError("cannot abort a prepared or committed Monitor effect");
        }
        return await replayDurableReceipt(request, fingerprint, receiptPath, existing);
      }
    }

    if (request.phase === "provider_rejected") {
      if (!existing || existing.status !== "provider_pending") {
        throw new EffectRuntimeRequestError("provider rejection recovery requires an exact pending Monitor receipt");
      }
      validateNoEffect(request.provider_receipt, providerPlanObject(existing.provider_plan));
      const bytes = await readOptionalBytes(indexPath);
      if (!pendingIndexHistoryIntact(existing, bytes) || matchingIndexRecord(indexRecords(bytes?.toString("utf8") ?? null), request.effect_id)) {
        throw new EffectRuntimeRequestError("cannot abort Monitor effect with conflicting index history");
      }
      // Remove only this proven uncommitted reservation under the effect lock.
      // Any timeout, unknown outcome, changed request or committed effect keeps
      // its original recovery fence and never reaches this branch.
      await rm(receiptPath);
      return monitorResult(
        request, fingerprint, "aborted", null, { ok: false, appended: false },
        "provider rejected before commit; pending Monitor reservation released",
      );
    }

    // A pending v1 receipt preserves the decision that admitted this effect.
    // Validate that historical basis, never the post-business-commit projection.
    // It only authorizes settlement; the provider still fences any new mutation.
    let admittedRequest = request;
    if (existing?.schema_version === MONITOR_PENDING_ADMISSION_SCHEMA) {
      const decision = existing.admitted_decision;
      if (decision.goal_id !== request.goal_id || decision.agent_id !== request.decision.agent_id) {
        throw new EffectRuntimeRequestError("pending Monitor admission goal/agent does not match request", "malformed_transaction_receipt");
      }
      admittedRequest = { ...request, decision };
    }
    let allowed: Admission;
    try {
      allowed = await admission(admittedRequest, existing?.schema_version === MONITOR_PENDING_ADMISSION_SCHEMA);
    } catch (error) {
      if (
        existing?.schema_version === QUOTA_MONITOR_POLL_COMMIT_RECEIPT_SCHEMA &&
        error instanceof EffectRuntimeRequestError && error.code === "monitor_poll_admission_rejected"
      ) {
        throw new EffectRuntimeRequestError(
          "legacy pending Monitor receipt has no frozen admission; current admission is unavailable; preserve the receipt for reconciliation",
          "legacy_monitor_admission_unavailable",
        );
      }
      throw error;
    }

    const indexBytes = await readOptionalBytes(indexPath);
    const currentDigest = indexBytes ? sha256(indexBytes) : null;
    if (!existing && request.expected_index_digest !== currentDigest) {
      return conflictResult(request, fingerprint, "quota run index compare-and-swap precondition failed", currentDigest, "index_digest_conflict");
    }
    if (matchingIndexRecord(indexRecords(indexBytes?.toString("utf8") ?? null), request.effect_id)) {
      const reason = existing?.status === "provider_pending"
        ? "quota monitor-poll effect identity exists while its provider receipt is pending"
        : "quota monitor-poll effect identity exists without a transaction receipt";
      return conflictResult(request, fingerprint, reason, currentDigest, "effect_id_conflict");
    }

    if (request.phase === "preflight") {
      if (existing && !pendingIndexHistoryIntact(existing, indexBytes)) {
        return conflictResult(request, fingerprint, "quota run index changed after monitor Todo provider preflight", currentDigest, "index_digest_conflict");
      }
      const plan = verifiedProviderPlan(existing, request);
      if (!existing) {
        const pending = {
          schema_version: MONITOR_PENDING_ADMISSION_SCHEMA,
          effect_id: request.effect_id,
          request_digest: fingerprint,
          status: "provider_pending",
          index_path: indexPath,
          expected_index_digest: currentDigest,
          expected_index_bytes: indexBytes?.length ?? 0,
          provider_plan: plan,
          admitted_decision: request.decision,
          ...(quotaGoalRef(request.owner) === null ? {} : { goal_ref: quotaGoalRef(request.owner) }),
        } satisfies PendingMonitorReceipt;
        await atomicWriteJson(receiptPath, pending);
      }
      return monitorResult(
        request, fingerprint, "provider_required", null, { ok: true, dry_run: false, provider_required: true },
        "quota monitor-poll Todo provider is required", currentDigest, providerDispatchPlan(plan, admittedRequest),
      );
    }

    let effectiveRequest = admittedRequest;
    const expectedDigest = currentDigest;
    const expectedBytes = indexBytes?.length ?? 0;
    if (providerNeeded) {
      if (!existing || existing.status !== "provider_pending") {
        throw new EffectRuntimeRequestError("quota monitor-poll Todo commit requires a durable provider preflight");
      }
      if (!request.provider_receipt) {
        throw new EffectRuntimeRequestError("quota monitor-poll Todo commit requires a provider receipt");
      }
      const plan = verifiedProviderPlan(existing, request);
      if (!pendingIndexHistoryIntact(existing, indexBytes)) {
        return conflictResult(request, fingerprint, "quota run index changed after monitor Todo provider preflight", currentDigest, "index_digest_conflict");
      }
      effectiveRequest = requestWithProvider(admittedRequest, plan, validatedProviderReceipt(request.provider_receipt, plan));
    } else if (existing?.status === "provider_pending") {
      return effectConflict(request, fingerprint, currentDigest, existing);
    }

    const record = buildRecord(effectiveRequest, allowed);
    const { jsonPath, markdownPath } = await nextArtifactPaths(runsDir, effectiveRequest.generated_at, request.effect_id);
    const payload = await payloadFor(effectiveRequest, record, jsonPath, markdownPath, indexPath, {
      appended: true, replayed: false, repaired: false,
    });
    const indexRecord = indexRecordFor(effectiveRequest, record, jsonPath, markdownPath, fingerprint);
    const prepared = {
      schema_version: QUOTA_MONITOR_POLL_COMMIT_RECEIPT_SCHEMA,
      effect_id: request.effect_id,
      request_digest: fingerprint,
      status: "prepared",
      json_path: jsonPath,
      markdown_path: markdownPath,
      index_path: indexPath,
      expected_index_digest: expectedDigest,
      expected_index_bytes: expectedBytes,
      record,
      index_record: indexRecord,
      markdown: monitorMarkdown(record),
      payload,
    } satisfies DurableMonitorReceipt;
    const [, contentRepaired] = await Promise.all([atomicWriteJson(receiptPath, prepared), ensureContentArtifacts(prepared)]);
    // The conservative prepared receipt is the durable write-ahead recovery
    // authority; the public index remains the commit proof. Keeping the WAL in
    // that state avoids a second full-receipt fsync without ever publishing a
    // committed receipt before the index append succeeds.
    await ensureIndexArtifact(prepared, contentRepaired);
    return monitorResult(
      effectiveRequest, fingerprint, "written", record, payload,
      "quota monitor-poll transaction committed by TypeScript",
      await quotaMonitorPollIndexDigest(indexPath), null, indexRecord,
    );
  };
  return indexLockHeld ? await commit() : await withFileMutationLock(indexPath, commit);
}

export async function evaluateQuotaMonitorPollCommit(value: unknown): Promise<QuotaMonitorPollCommitResult> {
  const request = requestObject(value);
  return await withBorrowedQuotaAccountingOwner(
    request.owner,
    async (indexLockHeld) => await evaluateQuotaMonitorPollRequest(request, indexLockHeld),
  );
}
