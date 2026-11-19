/** Receipt-bound work and direction commits. Index/source claims survive the requesting CLI;
 * the real provider fence lasts through the synchronous durable append. */
import {createHash} from "node:crypto";
import {closeSync, existsSync, fsyncSync, lstatSync, openSync, readFileSync, readdirSync, realpathSync, renameSync, statSync, writeFileSync} from "node:fs";
import {basename, dirname, join, resolve} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import {settlementIdentity, settlementIdentityPayload} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {appendJsonLineSync, claimFileMutationLock, mutationLockOwner, releaseFileMutationLock,
  releaseFileMutationLockClaim, withFileMutationLock, type FileMutationLockClaim} from "../effect_runtime_io.ts";
import {jsonObject, requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {requireLocalAuthorityRuntimeRoot, type LocalAuthorityProviderDependencies} from "../coordination/local_authority_provider.ts";
import {canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {legacyCoordinationTodoLockPath} from "../coordination/legacy_writer_lock_paths.ts";
import {shadowMaintenanceLockPath, requireShadowPrimaryWriteAllowed} from "../coordination/shadow_management.ts";
import {
  QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA,
  readAdmittedQuotaSettlementFromSnapshot,
  readQuotaSettlementFromSnapshot,
  readQuotaSettlementSnapshot,
} from "../quota/settlement_readback.ts";
import {
  parseQuotaAccountingOwner,
  quotaAccountingOwnerLocks,
  requireCurrentQuotaAccountingOwner,
} from "../quota/source_admission.ts";
import {checkpointBasisSnapshot, evaluateCheckpointReadContext, evaluateResultWorkBinding,
  progressQualificationBasis, checkpointReceiptName, originalWorkReceiptDigest,
  inFlightWorkAuthorized, isCommittedResultContext} from "./checkpoint_read_context.ts";
import {reopenedTodoRead, withCheckpointAuthority} from "./checkpoint_authority.ts";
import {goalPathSegment} from "../rollout_receipt_log.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {checkpointContinuationRequired, resultFreshnessRequired} from "../quota/heartbeat_receipt_identity.ts";
import {decodeRefreshRetry, type RefreshRetryRequest} from "../quota/refresh_recovery.ts";
import {isCanonicalTodoId} from "../todos/identity.ts";

import {readWorkReceiptWitness, workReceiptWitnessPath, verifyCompletedWorkReceipt, writeWindowsWorkReceipt} from "./checkpoint_work_receipt.ts";

const digest = (bytes: Uint8Array): string => createHash("sha256").update(bytes).digest("hex");
function readReceiptValue(path: string): unknown {
  try { return JSON.parse(readFileSync(path, "utf8")); }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    return null;
  }
}

/** An initial direction can retain work without changing its authority.
 * A wider reference, new policy or admitted replan needs the exact Todo revision. */
function initialContinuation(
  retry: RefreshRetryRequest | null, receipt: JsonObject | null, binding: JsonObject, record: JsonObject,
  current: JsonObject, replanObligation: unknown,
): boolean {
  if (retry === null) return false;
  const basis = jsonObject(receipt?.work_basis);
  const todo = jsonObject(basis?.todo);
  const packet = retry.vision;
  const patch = jsonObject(packet?.vision_patch);
  const path = jsonObject(packet?.path_delta);
  const delta = packet?.todo_delta;
  const ack = jsonObject(record.autonomous_replan_ack);
  const semantic = jsonObject(ack?.semantic_delta);
  const replan = path?.outcome === "replan" && typeof replanObligation === "string" &&
    ack?.schema_version === "autonomous_replan_ack_v0" && ack.recorded === true &&
    semantic?.schema_version === "replan_semantic_delta_v0" && semantic.accepted === true &&
    semantic.obligation_id === replanObligation && Array.isArray(semantic.satisfying_outcomes) &&
    semantic.satisfying_outcomes.includes("fresh_vision_path_outcome") &&
    retry.mutation.autonomous_replan_recorded === true;
  const eligible = retry.delivery_outcome === "outcome_progress" && retry.delivery_boundary === "semantic_closeout" &&
    retry.unchanged_reason === null && !retry.merge_patch &&
    Object.entries(retry.mutation).every(([key, value]) =>
      value == null || value === false || (Array.isArray(value) && value.length === 0) ||
      (replan && (key === "autonomous_replan_recorded" ||
        (key === "repair_delta_kinds" && Array.isArray(value) && value.length === 1 && value[0] === "goal_vision_patch")))) &&
    jsonObject(retry.external_delivery)?.resume_key == null &&
    record.completion_todo_id == null && record.completion_turn_key == null &&
    receipt?.result_only === true && typeof receipt.provider_revision === "string" &&
    receipt.commit_attempt == null && receipt.terminal_validation_effect == null &&
    inFlightWorkAuthorized(basis, binding) && basis?.agent_vision == null &&
    todo?.role === "agent" && todo.archive_state === "active" &&
    todo.task_class === "advancement_task" && todo.resume_when == null &&
    packet?.schema_version === "goal_vision_replan_contract_v0" && packet.state === "vision_patch_proposed" &&
    Object.keys(packet).every(key => ["schema_version", "state", "vision_patch", "path_delta", "todo_delta"].includes(key)) &&
    patch !== null && Object.keys(patch).every(key => key === "acceptance_summary" || key === "advancement_policy") &&
    typeof patch.acceptance_summary === "string" && patch.acceptance_summary.trim() !== "" &&
    (replan || (replanObligation == null && path?.outcome === "continue")) && path !== null && Object.keys(path).every(key =>
      ["schema_version", "outcome", "prior_assumption", "observed_reality", "retained", "evidence_refs"].includes(key));
  if (!eligible || !Array.isArray(delta) || delta.length !== 1 ||
      typeof delta[0] !== "string" || !delta[0].startsWith("retain:")) return false;
  const retained = delta[0].slice("retain:".length);
  const policy = patch?.advancement_policy;
  if (replan) {
    return retained === binding.todo_id && policy === undefined && current.provider_revision === receipt?.provider_revision;
  }
  if (retained === binding.todo_id && policy === undefined) return true;
  return current.provider_revision === receipt?.provider_revision && isCanonicalTodoId(retained) &&
    (policy === undefined || policy === "as_needed" || policy === "repeat_until_closed") &&
    Array.isArray(current.todos) && current.todos.some(value => {
      const item = jsonObject(value);
      return item?.todo_id === retained && item.role === "agent" && item.archive_state === "active" &&
        (item.status === "open" || item.status === "deferred");
    });
}
function unknown(message: string): never {
  throw new EffectRuntimeRequestError(`${message}; read back the original Turn before retrying`, "checkpoint_commit_unknown");
}

function checkpointIndex(path: string): {bytes: Buffer; rows: JsonObject[]} {
  let bytes: Buffer;
  try { bytes = readFileSync(path); }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    return {bytes: Buffer.alloc(0), rows: []};
  }
  // The ordinary history reader tolerates damaged rows. A commit cannot infer
  // absence from that reader or append behind a torn (even JSON-valid) tail.
  if (bytes.length && bytes[bytes.length - 1] !== 10) unknown("checkpoint index has an incomplete tail");
  const checkpoints = new Map<string, string>();
  const rows: JsonObject[] = [];
  for (const line of bytes.toString("utf8").split(/\r?\n/)) {
    if (!line.trim()) continue;
    try {
      const row = requireJsonObject(JSON.parse(line), "run index row");
      rows.push(row);
      const checkpoint = jsonObject(row.vision_checkpoint);
      const effect = jsonObject(row.settlement_identity)?.effect_id;
      if ((row.result_work_context || checkpoint?.satisfied === true && checkpoint.read_context) && typeof effect === "string") {
        const version = canonicalAuthoritySha256({checkpoint, result_work_context: row.result_work_context ?? null, recovery: row.refresh_recovery,
          json_path: row.json_path, markdown_path: row.markdown_path,
          result_artifact_digests: row.result_artifact_digests ?? null,
          result_work_receipt_sha256: row.result_work_receipt_sha256 ?? null,
          direction_artifact_digests: row.direction_artifact_digests ?? null});
        // A retained result and its later direction settlement share a Turn,
        // but are distinct effects. Only competing proofs of the same purpose conflict.
        const key = `${effect}:${row.result_work_context ? "delivery_result" : "first_delivery"}`;
        if (checkpoints.has(key) && checkpoints.get(key) !== version) unknown("checkpoint index has conflicting decisions");
        checkpoints.set(key, version);
      }
    }
    catch { unknown("checkpoint index is malformed"); }
  }
  return {bytes, rows};
}

function indexBytes(path: string): Buffer { return checkpointIndex(path).bytes; }

/** One strict capture per observation; never a cache or an admission witness. */
export function readCheckpointHistoryRows(root: string, goal: string): JsonObject[] {
  return checkpointIndex(join(root, "goals", goalPathSegment(goal), "runs", "index.jsonl")).rows;
}

function committedArtifacts(prior: JsonObject, runsDir: string, includeRetainedBinding = false,
    includeRetainedContent = false, qualifiedHistoryBinding: JsonObject | null = null): JsonObject {
  const jsonPath = runPath(prior.json_path, runsDir, ".json");
  const markdownPath = runPath(prior.markdown_path, runsDir, ".md");
  let record: JsonObject;
  let jsonBytes: Buffer, markdownBytes: Buffer;
  try {
    jsonBytes = readFileSync(jsonPath);
    markdownBytes = readFileSync(markdownPath);
    record = requireJsonObject(JSON.parse(jsonBytes.toString("utf8")), "checkpoint record");
  } catch { unknown("checkpoint artifacts are unavailable"); }
  // New protected result appends freeze byte witnesses in both the original
  // work attempt and its index row. Missing historical witnesses stay legacy;
  // a new witness cannot be removed from just one side to downgrade its check.
  const effect = jsonObject(prior.settlement_identity)?.effect_id;
  // Only the history observer supplies this binding, after independently
  // verifying its indexed Q. It cannot restore a work read for a writer.
  if (qualifiedHistoryBinding !== null && (includeRetainedBinding ||
      qualifiedHistoryBinding.index_record_sha256 !== canonicalAuthoritySha256(prior) ||
      !BARE_SHA256_PATTERN.test(String(prior.result_work_receipt_sha256)) ||
      !BARE_SHA256_PATTERN.test(String(qualifiedHistoryBinding.work_receipt_sha256)) ||
      ["settlement_identity", "refresh_recovery", "result_artifact_digests", "result_work_context",
        "result_work_receipt_sha256"].some(field =>
        canonicalAuthoritySha256(qualifiedHistoryBinding[field] ?? null) !== canonicalAuthoritySha256(prior[field] ?? null)))) {
    unknown("qualified history does not bind the original retained result");
  }
  let work: JsonObject | null = null;
  if (qualifiedHistoryBinding === null && prior.result_work_context != null && typeof effect === "string") {
    try {
      work = jsonObject(readReceiptValue(join(dirname(runsDir), "checkpoint-contexts",
        checkpointReceiptName(effect, true))));
    } catch (error) {
      if (!(error instanceof SyntaxError) && (error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
    if (work === null && prior.result_work_receipt_sha256 !== undefined) {
      try { work = readWorkReceiptWitness(dirname(dirname(dirname(runsDir))), requireJsonObject(prior.settlement_identity, "original identity"), prior.result_work_receipt_sha256, jsonObject(prior.result_work_context)?.read_context_id); }
      catch { unknown("checkpoint artifact witness is unavailable"); }
    }
  }
  const indexed = jsonObject(jsonObject(work?.commit_attempt)?.index_record);
  if (prior.result_artifact_digests !== undefined || indexed?.result_artifact_digests !== undefined ||
      prior.result_work_receipt_sha256 !== undefined || indexed?.result_work_receipt_sha256 !== undefined) {
    const witness = jsonObject(prior.result_artifact_digests);
    if (witness === null || (qualifiedHistoryBinding === null && (indexed === null ||
        canonicalAuthoritySha256(indexed) !== canonicalAuthoritySha256(prior))) ||
        !BARE_SHA256_PATTERN.test(String(witness.json_sha256)) ||
        !BARE_SHA256_PATTERN.test(String(witness.markdown_sha256)) ||
        digest(jsonBytes) !== witness.json_sha256 || digest(markdownBytes) !== witness.markdown_sha256) {
      unknown("checkpoint artifacts disagree with the original result byte witness");
    }
    if (qualifiedHistoryBinding === null &&
        (prior.result_work_receipt_sha256 !== undefined || indexed?.result_work_receipt_sha256 !== undefined) &&
        (work === null || !BARE_SHA256_PATTERN.test(String(prior.result_work_receipt_sha256)) ||
          originalWorkReceiptDigest(work) !== prior.result_work_receipt_sha256)) {
      unknown("checkpoint original work and validation evidence changed");
    }
  }
  for (const field of ["settlement_identity", "refresh_recovery", "vision_checkpoint", "result_work_context"]) {
    if (canonicalAuthoritySha256(record[field] ?? null) !== canonicalAuthoritySha256(prior[field] ?? null)) {
      unknown("checkpoint artifacts disagree with the committed index");
    }
  }
  let directionIndex: JsonObject | null = null;
  if (typeof effect === "string" && (prior.direction_artifact_digests !== undefined ||
      jsonObject(jsonObject(prior.vision_checkpoint)?.read_context)?.retained_result_binding != null)) {
    try {
      const direction = jsonObject(readReceiptValue(join(dirname(runsDir), "checkpoint-contexts",
        checkpointReceiptName(effect, false))));
      directionIndex = jsonObject(jsonObject(direction?.commit_attempt)?.index_record);
    } catch { /* An indexed Q is stronger than its disposable direction read. */ }
  }
  if (prior.direction_artifact_digests !== undefined || directionIndex?.direction_artifact_digests !== undefined) {
    const witness = jsonObject(prior.direction_artifact_digests);
    if (witness === null || !BARE_SHA256_PATTERN.test(String(witness.json_sha256)) ||
        !BARE_SHA256_PATTERN.test(String(witness.markdown_sha256)) ||
        digest(jsonBytes) !== witness.json_sha256 || digest(markdownBytes) !== witness.markdown_sha256 ||
        (directionIndex !== null && canonicalAuthoritySha256(directionIndex) !== canonicalAuthoritySha256(prior))) {
      unknown("qualified checkpoint artifacts disagree with their original byte witness");
    }
  }
  let retainedBinding: JsonObject | null = null;
  if (includeRetainedBinding && prior.result_work_receipt_sha256 !== undefined) {
    // The IO owner has verified the original index/artifacts above. Restore the
    // original typed work proof too; a Host-supplied digest is not that proof.
    let restored: JsonObject | null = null;
    try {
      if (work) restored = evaluateResultWorkBinding({phase: "restore", identity: prior.settlement_identity, receipt: work});
    } catch { unknown("retained result work proof is malformed"); }
    const context = jsonObject(prior.result_work_context);
    if (work === null || restored?.ok !== true ||
        work.read_context_id !== context?.read_context_id ||
        (isCommittedResultContext(context) ? work.result_only !== true :
          canonicalAuthoritySha256(work.versions) !== canonicalAuthoritySha256(context?.versions ?? null)) ||
        jsonObject(work.terminal_validation_effect)?.status === "pending") {
      unknown("retained result work or validation evidence is unavailable");
    }
    retainedBinding = {settlement_identity: prior.settlement_identity,
      refresh_recovery: prior.refresh_recovery,
      index_record_sha256: canonicalAuthoritySha256(prior),
      result_artifact_digests: prior.result_artifact_digests,
      result_work_receipt_sha256: prior.result_work_receipt_sha256,
      result_work_context: prior.result_work_context,
      work_receipt_sha256: canonicalAuthoritySha256(work)};
  }
  return {ok: true, replayed: true, context: prior.result_work_context ?? jsonObject(prior.vision_checkpoint)?.read_context,
    json_path: jsonPath, markdown_path: markdownPath,
    ...(retainedBinding ? {retained_result_binding: retainedBinding, retained_work_receipt: work,
      retained_result_artifacts: {json_path: jsonPath, markdown_path: markdownPath,
        index_record_sha256: retainedBinding.index_record_sha256, delivery_workspace: prior.delivery_workspace ?? null},
      ...(includeRetainedContent ? {retained_result_content: {
        json_text: jsonBytes.toString("utf8"), markdown: markdownBytes.toString("utf8"),
      }} : {})} : {})};
}

/** Read-only verification before the Python adapter returns an early replay.
 * Its index lock is already held. No receipt freshness check invalidates a
 * historically committed decision. */
export function inspectCheckpointReplay(value: unknown): JsonObject {
  const request = requireJsonObject(value, "checkpoint replay inspection");
  const root = requireLocalAuthorityRuntimeRoot(request.runtime_root);
  return inspectCheckpointReplayFromRows(request, readCheckpointHistoryRows(root, goalPathSegment(request.goal_id)));
}

/** Internal caller already holds the index lock and captured strict rows. */
export function inspectCheckpointReplayFromRows(value: unknown, rows: JsonObject[]): JsonObject {
  const request = requireJsonObject(value, "checkpoint replay inspection");
  const root = requireLocalAuthorityRuntimeRoot(request.runtime_root);
  const prior = requireJsonObject(request.prior, "checkpoint prior");
  const goal = goalPathSegment(request.goal_id);
  const runsDir = join(root, "goals", goal, "runs");
  const priorDigest = canonicalAuthoritySha256(prior);
  if (!rows.some(row => canonicalAuthoritySha256(row) === priorDigest)) {
    unknown("checkpoint replay is not an indexed record");
  }
  return committedArtifacts(prior, runsDir, request.include_retained_binding === true, request.include_retained_content === true);
}

function runPath(value: unknown, runsDir: string, extension: string): string {
  const path = resolve(requireNonEmptyString(value, "run artifact path"));
  let parent: string;
  try { parent = realpathSync(dirname(path)); }
  catch { unknown("checkpoint artifact directory is unavailable"); }
  if (parent !== realpathSync(runsDir) || !path.endsWith(extension)) {
    unknown("checkpoint artifact path is invalid");
  }
  // Resolve only the directory alias. Following the leaf would admit a file
  // symlink, and writing through the original alias could follow a later retarget.
  const physical = join(parent, basename(path));
  try { if (!lstatSync(physical).isFile()) unknown("checkpoint artifact is not a regular file"); }
  catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
  return physical;
}

function writeSynced(path: string, text: string): void {
  const fd = openSync(path, "w", 0o600);
  try { writeFileSync(fd, text, {encoding: "utf8"}); fsyncSync(fd); }
  finally { closeSync(fd); }
}

function writeReceipt(path: string, receipt: JsonObject): void {
  const temporary = `${path}.${process.pid}.tmp`;
  writeSynced(temporary, JSON.stringify(receipt) + "\n");
  renameSync(temporary, path);
}

type IndexedReadProof = {row: JsonObject; effect: string; purpose: string};

function* indexedReadProofs(rows: JsonObject[], goal: string): Generator<[string, IndexedReadProof]> {
  for (const row of rows) {
    const binding = jsonObject(row.settlement_identity);
    if (binding?.goal_id !== goal) continue;
    const effect = binding.effect_id;
    if (typeof effect !== "string") continue;
    const direction = jsonObject(row.vision_checkpoint)?.satisfied === true &&
      jsonObject(jsonObject(row.vision_checkpoint)?.read_context) !== null;
    if (direction) yield [checkpointReceiptName(effect, false), {row, effect, purpose: "first_delivery"}];
    const work = jsonObject(row.result_work_context) !== null;
    if (direction || work) {
      yield [checkpointReceiptName(effect, true), {row, effect, purpose: work ? "delivery_result" : "first_delivery"}];
    }
  }
}

function indexedReadProof(rows: JsonObject[], goal: string, name: string): IndexedReadProof | null {
  for (const [candidate, proof] of indexedReadProofs(rows, goal)) {
    if (candidate === name) return proof;
  }
  return null;
}

/** Observation only: recover bindings from verified indexed effects, never from
 * unreadable bytes. Pending accounting remains visible under the recovered identity. */
export function inspectCheckpointReadHistory(value: unknown): JsonObject {
  const request = requireJsonObject(value, "checkpoint read history inspection");
  const root = requireLocalAuthorityRuntimeRoot(request.runtime_root);
  return inspectCheckpointReadHistoryFromRows(request, readCheckpointHistoryRows(root, goalPathSegment(request.goal_id)));
}

export function inspectCheckpointReadHistoryFromRows(value: unknown, rows: JsonObject[]): JsonObject {
  const request = requireJsonObject(value, "checkpoint read history inspection");
  const root = requireLocalAuthorityRuntimeRoot(request.runtime_root);
  const goal = goalPathSegment(request.goal_id);
  if (!Array.isArray(request.receipt_names)) throw new EffectRuntimeRequestError("receipt_names must be an array");
  const names = new Set<unknown>(request.receipt_names);
  const contexts = join(root, "goals", goal, "checkpoint-contexts");
  // Deleted reads cannot appear in a directory scan. Recover their indexed
  // binding too; accounting/terminal owners still decide whether it is pending.
  for (const row of rows) {
    const binding = jsonObject(row.settlement_identity);
    if (binding?.goal_id !== goal || typeof binding.effect_id !== "string" ||
        (request.agent_id != null && binding.agent_id !== request.agent_id)) continue;
    const effect = binding.effect_id;
    if (!existsSync(join(contexts, checkpointReceiptName(effect, false))) &&
        !existsSync(join(contexts, checkpointReceiptName(effect, true)))) {
      if (row.result_work_context != null) names.add(checkpointReceiptName(effect, true));
      if (jsonObject(row.vision_checkpoint)?.satisfied === true &&
          jsonObject(jsonObject(row.vision_checkpoint)?.read_context)) names.add(checkpointReceiptName(effect, false));
    }
  }
  const verified: JsonObject[] = [];
  if (!names.size) return {ok: true, verified};
  const proofs = new Map<string, IndexedReadProof>();
  for (const [name, proof] of indexedReadProofs(rows, goal)) {
    if (names.has(name) && !proofs.has(name)) proofs.set(name, proof);
  }
  // Preserve all rows for each effect so the attempt owner still rejects duplicate or conflicting proofs.
  const rowsByEffect = new Map<string, JsonObject[]>();
  for (const row of rows) {
    const effect = jsonObject(row.settlement_identity)?.effect_id;
    if (typeof effect !== "string") continue;
    const related = rowsByEffect.get(effect) ?? [];
    related.push(row);
    rowsByEffect.set(effect, related);
  }
  for (const name of names) {
    if (typeof name !== "string" || !name.endsWith(".json") || !BARE_SHA256_PATTERN.test(name.slice(0, -5))) {
      throw new EffectRuntimeRequestError("invalid checkpoint receipt name");
    }
    const proof = proofs.get(name);
    if (!proof) continue;
    try {
      const result = inspectCheckpointAttemptFromRows({runtime_root: root, identity: proof.row.settlement_identity,
        purpose: proof.purpose}, rowsByEffect.get(proof.effect) ?? []);
      if (result.status === "committed") verified.push({receipt_name: name, identity: proof.row.settlement_identity,
        purpose: proof.purpose});
    } catch (error) {
      if (!(error instanceof EffectRuntimeRequestError) || error.code !== "checkpoint_commit_unknown") throw error;
      if (proof.purpose !== "delivery_result") continue;
      const related = rowsByEffect.get(proof.effect) ?? [];
      const retained = related.filter(row => row.result_work_context != null);
      const qualified = related.filter(row => jsonObject(row.vision_checkpoint)?.satisfied === true &&
        jsonObject(jsonObject(row.vision_checkpoint)?.read_context) !== null);
      if (retained.length !== 1 || qualified.length !== 1 ||
          canonicalAuthoritySha256(retained[0].goal_ref ?? null) !== canonicalAuthoritySha256(qualified[0].goal_ref ?? null)) continue;
      try {
        const direction = inspectCheckpointAttemptFromRows({runtime_root: root,
          identity: proof.row.settlement_identity, purpose: "first_delivery"}, related);
        const binding = jsonObject(jsonObject(jsonObject(qualified[0].vision_checkpoint)?.read_context)?.retained_result_binding);
        if (direction.status !== "committed" || binding === null ||
            jsonObject(qualified[0].direction_artifact_digests) === null) continue;
        // Q retains the accepted work proof. Still verify the original R's
        // index and byte witnesses; losing its disposable read grants no work.
        committedArtifacts(retained[0], join(root, "goals", goal, "runs"), false, false, binding);
        verified.push({receipt_name: name, identity: proof.row.settlement_identity, purpose: "first_delivery"});
      } catch (recoveryError) {
        if (!(recoveryError instanceof EffectRuntimeRequestError) || recoveryError.code !== "checkpoint_commit_unknown") throw recoveryError;
      }
    }
  }
  return {ok: true, verified};
}

/** Caller holds the index lock. Unindexed artifacts never prove a committed
 * direction. A completely absent attempt may be retried; torn writes stay held. */
export function inspectCheckpointAttempt(value: unknown): JsonObject {
  const request = requireJsonObject(value, "checkpoint attempt inspection");
  const root = requireLocalAuthorityRuntimeRoot(request.runtime_root);
  const identity = requireJsonObject(request.identity, "settlement identity");
  return inspectCheckpointAttemptFromRows(request, readCheckpointHistoryRows(root, goalPathSegment(identity.goal_id)));
}

export function inspectCheckpointAttemptFromRows(value: unknown, rows: JsonObject[]): JsonObject {
  const request = requireJsonObject(value, "checkpoint attempt inspection");
  const root = requireLocalAuthorityRuntimeRoot(request.runtime_root);
  const identity = requireJsonObject(request.identity, "settlement identity");
  const runsDir = join(root, "goals", goalPathSegment(identity.goal_id), "runs");
  const work = request.purpose === "delivery_result";
  const scoped = Object.hasOwn(request, "goal_ref");
  const sameScope = (value: unknown) => !scoped ||
    canonicalAuthoritySha256(value ?? null) === canonicalAuthoritySha256(request.goal_ref ?? null);
  const readGoalRef = (value: JsonObject) => jsonObject(jsonObject(value.work_basis ?? value.decision_basis)?.source)?.goal_ref;
  const matching = rows.filter(row => sameScope(row.goal_ref) &&
    jsonObject(row.settlement_identity)?.effect_id === identity.effect_id &&
    (work ? row.result_work_context != null : jsonObject(row.vision_checkpoint)?.satisfied === true &&
      jsonObject(row.vision_checkpoint)?.read_context != null));
  const path = join(root, "goals", goalPathSegment(identity.goal_id), "checkpoint-contexts",
    checkpointReceiptName(requireNonEmptyString(identity.effect_id, "effect_id"), work));
  let receipt: JsonObject | null = null;
  let receiptError: unknown;
  try { receipt = requireJsonObject(JSON.parse(readFileSync(path, "utf8")), "read receipt"); }
  catch (error) { receiptError = error; }
  if (!matching.length && receipt !== null && !sameScope(readGoalRef(receipt))) {
    unknown("checkpoint read receipt belongs to another GoalRef; preserve its original source and attempt");
  }
  // An exact indexed decision and its matching artifacts are stronger than a
  // disposable read receipt. Recover the original decision without reauthoring.
  if (matching.length) {
    if (matching.length !== 1 || canonicalAuthoritySha256(matching[0].settlement_identity) !== canonicalAuthoritySha256(identity)) {
      unknown("checkpoint attempt conflicts with its index identity");
    }
    if (receipt !== null) {
      if (canonicalAuthoritySha256(receipt.identity) !== canonicalAuthoritySha256(identity)) unknown("checkpoint receipt identity differs");
      const attempt = jsonObject(receipt.commit_attempt);
      if (attempt && canonicalAuthoritySha256(matching[0]) !== canonicalAuthoritySha256(attempt.index_record)) {
        unknown("checkpoint attempt conflicts with its index");
      }
    }
    return {...committedArtifacts(matching[0], runsDir), status: "committed"};
  }
  if (request.check_other_attempts === true) {
    const contexts = join(root, "goals", goalPathSegment(identity.goal_id), "checkpoint-contexts");
    let names: string[] = [];
    try { names = readdirSync(contexts); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
    const resultReadName = checkpointReceiptName(requireNonEmptyString(identity.effect_id, "effect_id"), true);
    for (const name of names.filter(name => name.endsWith(".json") && BARE_SHA256_PATTERN.test(name.slice(0, -5)))) {
      // This disjoint path can hold only the original work read, never a direction append.
      // Losing it cannot erase the successful result or create direction uncertainty.
      if (name === resultReadName) continue;
      let other: JsonObject;
      try { other = requireJsonObject(JSON.parse(readFileSync(join(contexts, name), "utf8")), "read receipt"); }
      catch {
        const proof = indexedReadProof(rows, goalPathSegment(identity.goal_id), name);
        if (proof) {
          // An independent writer observes the same verified history as status;
          // this cannot restore the damaged read as its own execution authority.
          const observed = inspectCheckpointReadHistoryFromRows({runtime_root: root,
            goal_id: identity.goal_id, receipt_names: [name]},
          rows.filter(row => jsonObject(row.settlement_identity)?.effect_id === proof.effect));
          if ((observed.verified as JsonObject[]).some(row => row.receipt_name === name)) continue;
        }
        unknown("checkpoint read receipt is unavailable; preserve original Turn artifacts before retrying");
      }
      if (scoped && readGoalRef(other) !== undefined && !sameScope(readGoalRef(other))) continue;
      const owner = jsonObject(other.identity);
      if (["first_delivery", "supplement_checkpoint"].includes(String(other.purpose)) &&
          other.commit_attempt != null && owner?.effect_id !== identity.effect_id &&
          owner?.agent_id === identity.agent_id && owner?.todo_id === identity.todo_id) {
        const proof = indexedReadProof(rows, goalPathSegment(identity.goal_id), name);
        if (!proof || proof.purpose !== "first_delivery" ||
            inspectCheckpointAttemptFromRows({runtime_root: root, identity: owner, purpose: "first_delivery"}, rows).status !== "committed") {
          unknown("another original Turn has an unresolved direction append for this Todo");
        }
      }
    }
  }
  if (receipt === null) {
    if ((receiptError as NodeJS.ErrnoException)?.code === "ENOENT") return {ok: true, status: "absent"};
    unknown("checkpoint read receipt is unavailable");
  }
  if (canonicalAuthoritySha256(receipt.identity) !== canonicalAuthoritySha256(identity)) unknown("checkpoint receipt identity differs");
  const attempt = jsonObject(receipt.commit_attempt);
  if (attempt == null) return {ok: true, status: "not_started"};
  for (const [field, extension] of [["json_path", ".json"], ["markdown_path", ".md"]]) {
    const artifact = runPath(attempt[field], runsDir, extension);
    try {
      const stat = lstatSync(artifact);
      // The caller reserves empty paths before persisting the attempt marker.
      // Both empty regular files are an absent effect; any bytes remain unknown.
      if (!stat.isFile() || stat.size !== 0) unknown("checkpoint append has unindexed artifacts");
    }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
  }
  // No content and no row: there was no visible effect. Clear only the marker,
  // preserving the original read identity; a subsequent basis check still runs.
  const {commit_attempt: _attempt, ...unstarted} = receipt;
  return {ok: true, status: "absent", read_context_id: receipt.read_context_id, receipt: unstarted};
}

export async function commitCheckpoint(value: unknown,
  dependencies: LocalAuthorityProviderDependencies = {},
): Promise<JsonObject> {
  const request = requireJsonObject(value, "checkpoint commit");
  const root = requireLocalAuthorityRuntimeRoot(request.runtime_root);
  const binding = requireJsonObject(request.identity, "settlement identity");
  const identity = settlementIdentity({goal_id: goalPathSegment(binding.goal_id), agent_id: String(binding.agent_id),
    todo_id: binding.todo_id == null ? null : String(binding.todo_id),
    turn_instance_id: String(binding.turn_instance_id),
    replan_obligation_id: binding.replan_obligation_id == null ? null : String(binding.replan_obligation_id)});
  if (canonicalAuthoritySha256(binding) !== canonicalAuthoritySha256(settlementIdentityPayload(identity))) {
    throw new EffectRuntimeRequestError("checkpoint settlement identity is invalid");
  }
  const runsDir = join(root, "goals", identity.goal_id, "runs");
  const physicalRuns = realpathSync(runsDir);
  const directoryIdentity = statSync(physicalRuns, {bigint: true});
  const indexPath = join(physicalRuns, "index.jsonl");
  const statePath = resolve(requireNonEmptyString(request.state_file, "state_file"));
  const firstDelivery = request.purpose === "first_delivery";
  const resultWork = request.purpose === "delivery_result";
  const registryBound = jsonObject(jsonObject(request.facts)?.source)?.registry_goal != null;
  const registryPath = firstDelivery || resultWork || registryBound
    ? resolve(requireNonEmptyString(request.registry_path, "registry_path")) : null;
  // The handoff names the caller's acquired lock; physical IO stays frozen
  // separately, and directory identity is rechecked before the append.
  const targets = [join(runsDir, "index.jsonl"), ...(registryPath ? [registryPath] : []), shadowMaintenanceLockPath(root, identity.goal_id),
    legacyCoordinationTodoLockPath(root, identity.goal_id), statePath].map(path => resolve(path));
  const owner = parseQuotaAccountingOwner({
    goalRefValue: request.goal_ref,
    sourceAdmissionValue: request.source_admission,
    runtimeRoot: root,
    goalId: identity.goal_id,
  });
  const retry = requireJsonObject(request.refresh_retry, "refresh retry");
  const receiptPath = join(root, "goals", identity.goal_id, "checkpoint-contexts",
    checkpointReceiptName(identity.effect_id, resultWork));

  async function admission(): Promise<JsonObject> {
    indexBytes(indexPath);
    const value = {schema_version: QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA,
      runtime_root: root, goal_id: identity.goal_id, agent_id: identity.agent_id, todo_id: identity.todo_id,
      replan_obligation_id: identity.replan_obligation_id, turn_instance_id: identity.turn_instance_id,
      infer_turn_instance_id: false, allow_unbound_binding: false, refresh_retry: retry,
      ...(owner.kind === "exact_source"
        ? {goal_ref: request.goal_ref, source_admission: request.source_admission}
        : {})};
    const snapshot = await readQuotaSettlementSnapshot(root, identity.goal_id);
    return owner.kind === "exact_source"
      ? readAdmittedQuotaSettlementFromSnapshot(value, snapshot)
      : readQuotaSettlementFromSnapshot(value, snapshot);
  }
  function replay(readback: JsonObject): JsonObject | null {
    const recovery = requireJsonObject(readback.refresh_recovery, "refresh recovery");
    if (recovery.decision !== "replay" && recovery.decision !== "repair_receipt") return null;
    const prior = requireJsonObject(readback.writeback_run, "committed checkpoint");
    if ((!resultWork && jsonObject(prior.vision_checkpoint)?.satisfied !== true) ||
        (resultWork && prior.result_work_context == null) ||
        jsonObject(prior.refresh_recovery)?.vision_request_digest !== recovery.vision_request_digest) {
      unknown("checkpoint replay does not identify a committed decision");
    }
    return committedArtifacts(prior, runsDir);
  }

  const claims: {
    target: string;
    token: string;
    claim: FileMutationLockClaim;
    borrowed: boolean;
  }[] = [];
  let adopted = false;
  let sourceClaimed = false;
  try {
    if (!Array.isArray(request.locks) || request.locks.length !== targets.length) {
      throw new EffectRuntimeRequestError("checkpoint requires the complete internal lock handoff");
    }
    const checkpointWitnesses = request.locks.map((value, i) => {
      const witness = requireJsonObject(value, "lock witness");
      const token = requireNonEmptyString(witness.token, "lock token");
      if (resolve(String(witness.target)) !== targets[i]) throw new EffectRuntimeRequestError("checkpoint lock target mismatch");
      return {
        target: targets[i],
        pid: witness.pid,
        token,
      };
    });
    const sourceLocks = quotaAccountingOwnerLocks(owner);
    if (
      owner.kind === "exact_source"
      && (
        sourceLocks[0].target !== checkpointWitnesses[0].target
        || sourceLocks[0].pid !== checkpointWitnesses[0].pid
        || sourceLocks[0].token !== checkpointWitnesses[0].token
      )
    ) {
      throw new EffectRuntimeRequestError(
        "checkpoint index handoff does not match quota source admission",
      );
    }
    const handoff = owner.kind === "exact_source"
      ? [
        ...sourceLocks.map((witness) => ({...witness, borrowed: true})),
        ...checkpointWitnesses.slice(1).map((witness) => ({
          ...witness,
          borrowed: false,
        })),
      ]
      : checkpointWitnesses.map((witness) => ({...witness, borrowed: false}));
    for (const [i, witness] of handoff.entries()) {
      const claim = await claimFileMutationLock(witness.target, witness.token);
      if (!claim) break;
      claims.push({
        target: witness.target,
        token: witness.token,
        claim,
        borrowed: witness.borrowed,
      });
      const current = await mutationLockOwner(witness.target);
      if (current?.token !== witness.token || current.pid !== witness.pid) break;
      if (owner.kind === "exact_source" && i === sourceLocks.length - 1) {
        sourceClaimed = true;
      }
      if (i === handoff.length - 1) adopted = true;
    }
    if (!adopted) {
      // A runtime retry can arrive after a successful save released the locks.
      // It may only read an exact committed result, never reuse the old handoff.
      if (sourceClaimed) {
        requireCurrentQuotaAccountingOwner(owner);
        const result = replay(await admission());
        if (result) return result;
        unknown("checkpoint lock handoff expired");
      }
      for (const entry of claims.splice(0).reverse()) {
        await releaseFileMutationLockClaim(entry.claim);
      }
      return await withFileMutationLock(indexPath, async () => {
        const result = replay(await admission());
        if (result) return result;
        unknown("checkpoint lock handoff expired");
      });
    }
    requireCurrentQuotaAccountingOwner(owner);
    const readback = await admission();
    const repeated = replay(readback);
    if (repeated) return repeated;
    const continuation = checkpointContinuationRequired(jsonObject(readback.heartbeat_receipt));
    const work = resultWork ? jsonObject(readReceiptValue(receiptPath)) : null;
    if (work?.result_only === true) await verifyCompletedWorkReceipt(root, binding, work, dependencies);
    const protectedSupplement = !firstDelivery && !resultWork && continuation;
    if (protectedSupplement && jsonObject(readReceiptValue(receiptPath))?.commit_attempt != null) {
      // Inspect the old operation before current basis/decision checks. A new
      // read id or request digest cannot replace an uncertain append.
      const attempt = inspectCheckpointAttempt({runtime_root: root, identity: binding,
        purpose: "supplement_checkpoint", ...(owner.kind === "exact_source" ? {goal_ref: request.goal_ref} : {})});
      if (attempt.status === "committed") {
        throw new EffectRuntimeRequestError("Qualified checkpoint already committed; recover its original request and remaining settlement",
          "checkpoint_commit_rejected");
      }
    }
    const recovery = requireJsonObject(readback.refresh_recovery, "refresh recovery");
    if (resultWork && !resultFreshnessRequired(jsonObject(readback.heartbeat_receipt))) {
      throw new EffectRuntimeRequestError("In-flight work check requires the original protected admission");
    }
    if (recovery.decision !== (firstDelivery || resultWork ? "append" : "supplement_checkpoint")) {
      throw new EffectRuntimeRequestError(String(recovery.reason ?? "checkpoint supplement rejected"), "checkpoint_commit_rejected");
    }
    await requireShadowPrimaryWriteAllowed(root, identity.goal_id);
    const facts = requireJsonObject(request.facts, "checkpoint facts");
    const source = requireJsonObject(facts.source, "checkpoint source");
    if (resolve(String(source.state_file)) !== statePath || resolve(String(source.runtime_root)) !== resolve(root)) {
      throw new EffectRuntimeRequestError("checkpoint adapted source binding mismatch");
    }
    const expectedIndex = requireNonEmptyString(request.index_sha256, "index digest");
    const expectedState = requireNonEmptyString(request.state_sha256, "state digest");
    return await withCheckpointAuthority(root, identity.goal_id, facts, current => {
      // No await from final head read through append, including for SQLite.
      if (digest(indexBytes(indexPath)) !== expectedIndex || digest(readFileSync(statePath)) !== expectedState) {
        unknown("checkpoint sources changed during lock handoff");
      }
      // Format-neutral byte guard under the adopted source lock, not a JSON
      // decoder. Keep it synchronous with the final head read and durable append.
      if (registryPath && digest(readFileSync(registryPath)) !== request.registry_sha256) {
        unknown("checkpoint registry changed during lock handoff");
      }
      let receipt = readReceiptValue(receiptPath);
      let directionToSave: JsonObject | null = null;
      const blockedWait = jsonObject(request.blocked_wait);
      if (blockedWait !== null) {
        const observation = jsonObject(retry.progress_observation);
        const mutation = requireJsonObject(retry.mutation, "checkpoint mutation");
        const hasOtherMutation = Object.values(mutation).some(value =>
          value != null && value !== false && !(Array.isArray(value) && value.length === 0));
        const record = requireJsonObject(request.record, "checkpoint record");
        const row = requireJsonObject(request.index_record, "checkpoint index record");
        if (!resultWork || retry.delivery_outcome !== "outcome_gap" ||
            retry.delivery_boundary !== "semantic_closeout" || observation?.result_class !== "blocked" ||
            typeof observation.blocker_id !== "string" || !observation.blocker_id ||
            (blockedWait.schema_version === "quota_blocked_causal_wait_v0" &&
              observation.blocker_id !== jsonObject(blockedWait.target_todo)?.todo_id) ||
            retry.vision != null || retry.unchanged_reason != null || hasOtherMutation ||
            record.completion_todo_id != null || record.completion_turn_key != null ||
            jsonObject(retry.external_delivery)?.resume_key != null ||
            canonicalAuthoritySha256(record.blocked_retry) !== canonicalAuthoritySha256(blockedWait) ||
            canonicalAuthoritySha256(row.blocked_retry) !== canonicalAuthoritySha256(blockedWait)) {
          throw new EffectRuntimeRequestError("Wait proof must match the original blocked writeback");
        }
      }
      const context = evaluateCheckpointReadContext({phase: "check", identity: binding,
        checkpoint_continuation: continuation,
        purpose: request.purpose, decision_scope: request.decision_scope,
        blocked_wait: blockedWait, require_blocked_wait: blockedWait !== null,
        read_context_id: resultWork ? request.read_context_id : retry.checkpoint_read_context_id, receipt, facts: current});
      if (context.ok !== true) return context;
      if (resultWork && continuation && retry.delivery_boundary === "in_flight_continuation" &&
          (context.result_committed === true || !inFlightWorkAuthorized(jsonObject(receipt)?.work_basis, binding))) {
        const reason = "In-flight progress requires an open Todo and its current work authority. Preserve the original Turn and wait without spending.";
        return {ok: false, error_code: "checkpoint_direction_source_changed", reread_required: false,
          error: reason, recommended_action: reason};
      }
      // A valid read or CLI judgment cannot rearm a stopped direction. The
      // explicit Host recovery path must first return an admissible decision.
      if ((firstDelivery || protectedSupplement) && jsonObject(receipt)?.direction_stopped === true) {
        return evaluateResultWorkBinding({phase: "direction_dispatch", receipt, explicit_resume: false});
      }
      if (protectedSupplement) {
        // A fresh direction token cannot qualify different retained bytes.
        // Recheck the original result under the final source/provider fence.
        const prior = requireJsonObject(readback.writeback_run, "retained result");
        const verified = committedArtifacts(prior, runsDir, true);
        if (verified.retained_result_binding !== undefined) {
          const directionReceipt = requireJsonObject(receipt, "direction receipt");
          if (canonicalAuthoritySha256(directionReceipt.retained_result_binding ?? null) !==
              canonicalAuthoritySha256(verified.retained_result_binding)) {
            unknown("direction does not bind the original retained result and work evidence");
          }
          const currentWork = checkpointBasisSnapshot({phase: "read", purpose: "delivery_result", identity: binding,
            checkpoint_continuation: true, full_frontier: false, facts: current});
          const qualification = evaluateResultWorkBinding({phase: "progress_qualification", identity: binding,
            receipt: verified.retained_work_receipt, retained_result_binding: verified.retained_result_binding,
            retained_result_artifacts: verified.retained_result_artifacts, result_writeback: prior,
            direction_receipt: directionReceipt, current_work: currentWork}).qualification;
          if (canonicalAuthoritySha256(progressQualificationBasis(directionReceipt.current_progress_qualification)) !==
              canonicalAuthoritySha256(qualification)) {
            throw new EffectRuntimeRequestError("Current progress proof changed; reread and judge the same R", "checkpoint_read_context_stale");
          }
          const decision = jsonObject(directionReceipt.current_agent_decision) ?? {
            read_context_id: directionReceipt.read_context_id,
            ...(retry.vision != null ? {agent_vision: retry.vision} : {vision_unchanged_reason: retry.unchanged_reason})};
          const response = evaluateResultWorkBinding({phase: "direction_response", identity: binding,
            receipt: directionReceipt, decision});
          if (response.ok !== true) throw new EffectRuntimeRequestError("Current progress judgment is not bound to R", "checkpoint_direction_binding_mismatch");
          if (directionReceipt.current_agent_decision != null) {
            // The Host may save an empty optional reason that the CLI omits at writeback.
            // Keep non-empty reasons and the saved response binding exact.
            const optionalReason = (value: unknown): unknown => value === "" ? null : value ?? null;
            if (response.ok !== true || canonicalAuthoritySha256(response.binding) !==
                canonicalAuthoritySha256(directionReceipt.current_agent_binding ?? null) ||
                canonicalAuthoritySha256(decision.agent_vision ?? null) !== canonicalAuthoritySha256(retry.vision ?? null) ||
                optionalReason(decision.vision_unchanged_reason) !== optionalReason(retry.unchanged_reason)) {
              unknown("saved direction response does not bind the retained result");
            }
          } else {
            directionToSave = {...directionReceipt, current_agent_decision: decision, current_agent_binding: response.binding,
              current_progress_qualification: response.current_progress_qualification};
          }
          context.retained_result_binding = verified.retained_result_binding;
          context.current_progress_qualification = response.current_progress_qualification;
          context.retained_result_artifacts = verified.retained_result_artifacts;
        }
      }
      const record = requireJsonObject(request.record, "checkpoint record");
      const row = requireJsonObject(request.index_record, "checkpoint index record");
      const vision = jsonObject(record.vision_checkpoint);
      const combinedDirection = resultWork && continuation && vision?.required === true && vision.satisfied === true;
      if (combinedDirection) {
        const workReceipt = requireJsonObject(receipt, "original result read");
        if (workReceipt.commit_attempt != null) unknown("original combined result append is uncertain");
        const restored = evaluateResultWorkBinding({phase: "restore", identity: binding, receipt: workReceipt});
        if (restored.ok !== true) return restored;
        const selectedObligation = jsonObject(readback.semantic_replan_guard)?.selected_obligation_id;
        if (owner.kind === "exact_source" || context.result_committed === true ||
            !initialContinuation(decodeRefreshRetry(retry), workReceipt, binding, record, current, selectedObligation)) {
          return {ok: false, error_code: "checkpoint_read_context_required", reread_required: false,
            error: "This direction requires its own post-result read. Retain the original result and judge direction on its original Turn."};
        }
        if (canonicalAuthoritySha256(row.vision_checkpoint ?? null) !== canonicalAuthoritySha256(vision) ||
            canonicalAuthoritySha256(row.autonomous_replan_ack ?? null) !== canonicalAuthoritySha256(record.autonomous_replan_ack ?? null)) {
          throw new EffectRuntimeRequestError("Direction and result index disagree");
        }
      }
      for (const projected of [record, row]) {
        if (canonicalAuthoritySha256(projected.settlement_identity) !== canonicalAuthoritySha256(binding) ||
            canonicalAuthoritySha256(projected.refresh_recovery) !== canonicalAuthoritySha256(recovery) ||
            (!resultWork && jsonObject(projected.vision_checkpoint)?.satisfied !== true)) {
          throw new EffectRuntimeRequestError("checkpoint projection does not match typed admission");
        }
        if (resultWork) {
          if (projected.delivery_outcome !== retry.delivery_outcome ||
              jsonObject(projected.vision_checkpoint)?.delivery_boundary !== retry.delivery_boundary) {
            throw new EffectRuntimeRequestError("Work-basis append must match the admitted result writeback");
          }
          projected.result_work_context = context;
          if (combinedDirection) projected.vision_checkpoint = {
            ...requireJsonObject(projected.vision_checkpoint, "vision checkpoint"), read_context: context,
          };
        } else projected.vision_checkpoint = {...requireJsonObject(projected.vision_checkpoint, "vision checkpoint"), read_context: context};
      }
      const jsonPath = runPath(row.json_path, runsDir, ".json");
      const markdownPath = runPath(row.markdown_path, runsDir, ".md");
      const artifactText = resultWork && continuation
        ? {json: JSON.stringify(record, null, 2) + "\n",
          markdown: requireNonEmptyString(request.markdown, "checkpoint Markdown")} : null;
      if (artifactText !== null) {
        row.result_artifact_digests = {json_sha256: digest(Buffer.from(artifactText.json)),
          markdown_sha256: digest(Buffer.from(artifactText.markdown))};
        row.result_work_receipt_sha256 = originalWorkReceiptDigest(requireJsonObject(receipt, "original work receipt"));
      }
      const saveAttempt = () => {
        const attempted: JsonObject = {...requireJsonObject(receipt, "checkpoint receipt"),
          commit_attempt: {vision_request_digest: recovery.vision_request_digest,
            mutation_digest: recovery.mutation_digest, json_path: jsonPath, markdown_path: markdownPath,
            index_record: row}};
        if (resultWork && continuation) {
          // The private snapshot was prepared by the IO adapter. Persist its
          // exact attempt before either the primary receipt or first R file.
          readWorkReceiptWitness(root, binding, row.result_work_receipt_sha256, attempted.read_context_id);
          const persist = process.platform === "win32" ? writeWindowsWorkReceipt : writeReceipt;
          persist(workReceiptWitnessPath(root, binding, attempted.read_context_id), attempted);
          persist(receiptPath, attempted);
        } else writeReceipt(receiptPath, attempted);
      };
      try {
        if (directionToSave !== null) {
          // Interactive CLI judgment is saved before Q starts, without claiming
          // a Host model attempt or publishing canonical Vision ahead of Q.
          writeReceipt(receiptPath, directionToSave);
          receipt = directionToSave;
        }
        if (firstDelivery || resultWork || protectedSupplement) {
          // Persist uncertainty before the first run artifact. A replacement
          // read cannot discard this attempt after a crash or a lost response.
          saveAttempt();
        }
        const currentDirectory = statSync(physicalRuns, {bigint: true});
        if (realpathSync(runsDir) !== physicalRuns || currentDirectory.dev !== directoryIdentity.dev ||
            currentDirectory.ino !== directoryIdentity.ino) unknown("checkpoint artifact directory changed");
        const jsonText = artifactText?.json ?? JSON.stringify(record, null, 2) + "\n";
        writeSynced(jsonPath, jsonText);
        const markdownText = artifactText?.markdown ?? requireNonEmptyString(request.markdown, "checkpoint Markdown");
        writeSynced(markdownPath, markdownText);
        if (protectedSupplement) {
          row.direction_artifact_digests = {json_sha256: digest(Buffer.from(jsonText)),
            markdown_sha256: digest(Buffer.from(markdownText))};
          // Extend only this same attempt after both durable files exist, before
          // indexing it. A failure in either write stays unknown with no new Q.
          saveAttempt();
        }
        appendJsonLineSync(indexPath, row);
      } catch { unknown("checkpoint append outcome is uncertain"); }
      return {ok: true, replayed: false, context, json_path: jsonPath, markdown_path: markdownPath};
    }, firstDelivery && !continuation, false,
      firstDelivery || resultWork || continuation, dependencies,
      reopenedTodoRead(request.blocked_wait, work));
  } finally {
    // The effect owns the end of the handed-off critical section. Releasing the
    // markers here also handles a caller that timed out but is still alive.
    for (const entry of claims.reverse()) {
      if (adopted && !entry.borrowed) {
        await releaseFileMutationLock(entry.target, entry.token, entry.claim, true);
      }
      else await releaseFileMutationLockClaim(entry.claim);
    }
  }
}
