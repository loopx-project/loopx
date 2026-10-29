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
import {checkpointBasisSnapshot, evaluateCheckpointReadContext} from "./checkpoint_read_context.ts";
import {withCheckpointAuthority} from "./checkpoint_authority.ts";
import {goalPathSegment} from "../rollout_receipt_log.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {checkpointContinuationRequired, resultFreshnessRequired} from "../quota/heartbeat_receipt_identity.ts";

const digest = (bytes: Uint8Array): string => createHash("sha256").update(bytes).digest("hex");
function readReceiptValue(path: string): unknown {
  try { return JSON.parse(readFileSync(path, "utf8")); }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    return null;
  }
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
          json_path: row.json_path, markdown_path: row.markdown_path});
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

function committedArtifacts(prior: JsonObject, runsDir: string): JsonObject {
  const jsonPath = runPath(prior.json_path, runsDir, ".json");
  const markdownPath = runPath(prior.markdown_path, runsDir, ".md");
  let record: JsonObject;
  try {
    record = requireJsonObject(JSON.parse(readFileSync(jsonPath, "utf8")), "checkpoint record");
    readFileSync(markdownPath);
  } catch { unknown("checkpoint artifacts are unavailable"); }
  for (const field of ["settlement_identity", "refresh_recovery", "vision_checkpoint", "result_work_context"]) {
    if (canonicalAuthoritySha256(record[field] ?? null) !== canonicalAuthoritySha256(prior[field] ?? null)) {
      unknown("checkpoint artifacts disagree with the committed index");
    }
  }
  return {ok: true, replayed: true, context: prior.result_work_context ?? jsonObject(prior.vision_checkpoint)?.read_context,
    json_path: jsonPath, markdown_path: markdownPath};
}

/** Read-only verification before the Python adapter returns an early replay.
 * Its index lock is already held. No receipt freshness check invalidates a
 * historically committed decision. */
export function inspectCheckpointReplay(value: unknown): JsonObject {
  const request = requireJsonObject(value, "checkpoint replay inspection");
  const root = requireLocalAuthorityRuntimeRoot(request.runtime_root);
  const prior = requireJsonObject(request.prior, "checkpoint prior");
  const goal = goalPathSegment(request.goal_id);
  const runsDir = join(root, "goals", goal, "runs");
  const rows = indexBytes(join(runsDir, "index.jsonl")).toString("utf8").split(/\r?\n/).filter(line => line.trim());
  if (!rows.some(line => canonicalAuthoritySha256(JSON.parse(line)) === canonicalAuthoritySha256(prior))) {
    unknown("checkpoint replay is not an indexed record");
  }
  return committedArtifacts(prior, runsDir);
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

function checkpointReceiptName(effect: string, work: boolean): string {
  return createHash("sha256").update(effect + (work ? ":delivery_result" : "")).digest("hex") + ".json";
}

function indexedReadProof(rows: JsonObject[], goal: string, name: string): {row: JsonObject; purpose: string} | null {
  for (const row of rows) {
    const binding = jsonObject(row.settlement_identity);
    if (binding?.goal_id !== goal) continue;
    const effect = binding.effect_id;
    if (typeof effect !== "string") continue;
    const direction = jsonObject(row.vision_checkpoint)?.satisfied === true &&
      jsonObject(jsonObject(row.vision_checkpoint)?.read_context) !== null;
    if (direction && name === checkpointReceiptName(effect, false)) return {row, purpose: "first_delivery"};
    const work = jsonObject(row.result_work_context) !== null;
    if ((direction || work) && name === checkpointReceiptName(effect, true)) {
      return {row, purpose: work ? "delivery_result" : "first_delivery"};
    }
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
  for (const name of names) {
    if (typeof name !== "string" || !name.endsWith(".json") || !BARE_SHA256_PATTERN.test(name.slice(0, -5))) {
      throw new EffectRuntimeRequestError("invalid checkpoint receipt name");
    }
    const proof = indexedReadProof(rows, goal, name);
    if (!proof) continue;
    try {
      const result = inspectCheckpointAttemptFromRows({runtime_root: root, identity: proof.row.settlement_identity,
        purpose: proof.purpose}, rows);
      if (result.status === "committed") verified.push({receipt_name: name, identity: proof.row.settlement_identity,
        purpose: proof.purpose});
    } catch (error) {
      if (!(error instanceof EffectRuntimeRequestError) || error.code !== "checkpoint_commit_unknown") throw error;
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
  const matching = rows.filter(row => jsonObject(row.settlement_identity)?.effect_id === identity.effect_id &&
    (work ? row.result_work_context != null : jsonObject(row.vision_checkpoint)?.satisfied === true &&
      jsonObject(row.vision_checkpoint)?.read_context != null));
  const path = join(root, "goals", goalPathSegment(identity.goal_id), "checkpoint-contexts",
    checkpointReceiptName(requireNonEmptyString(identity.effect_id, "effect_id"), work));
  let receipt: JsonObject | null = null;
  let receiptError: unknown;
  try { receipt = requireJsonObject(JSON.parse(readFileSync(path, "utf8")), "read receipt"); }
  catch (error) { receiptError = error; }
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
          // Reuse receipt-first artifact verification without scanning other reads.
          // A work proof alone cannot establish that a direction append committed.
          const verified = inspectCheckpointAttemptFromRows({runtime_root: root, identity: proof.row.settlement_identity,
            purpose: proof.purpose}, rows);
          if (verified.status === "committed") continue;
        }
        unknown("checkpoint read receipt is unavailable; preserve original Turn artifacts before retrying");
      }
      const owner = jsonObject(other.identity);
      if (other.purpose === "first_delivery" && other.commit_attempt != null && owner?.effect_id !== identity.effect_id &&
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
    `${createHash("sha256").update(identity.effect_id + (resultWork ? ":delivery_result" : "")).digest("hex")}.json`);

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
      const receipt = readReceiptValue(receiptPath);
      const context = evaluateCheckpointReadContext({phase: "check", identity: binding,
        checkpoint_continuation: checkpointContinuationRequired(jsonObject(readback.heartbeat_receipt)),
        purpose: request.purpose, decision_scope: request.decision_scope,
        read_context_id: resultWork ? request.read_context_id : retry.checkpoint_read_context_id, receipt, facts: current});
      if (context.ok !== true) return context;
      const record = requireJsonObject(request.record, "checkpoint record");
      const row = requireJsonObject(request.index_record, "checkpoint index record");
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
        } else projected.vision_checkpoint = {...requireJsonObject(projected.vision_checkpoint, "vision checkpoint"), read_context: context};
      }
      const jsonPath = runPath(row.json_path, runsDir, ".json");
      const markdownPath = runPath(row.markdown_path, runsDir, ".md");
      try {
        if (firstDelivery || resultWork) {
          // Persist uncertainty before the first run artifact. A replacement
          // read cannot discard this attempt after a crash or a lost response.
          writeReceipt(receiptPath, {...requireJsonObject(receipt, "first delivery receipt"),
            commit_attempt: {vision_request_digest: recovery.vision_request_digest,
              mutation_digest: recovery.mutation_digest, json_path: jsonPath, markdown_path: markdownPath,
              index_record: row}});
        }
        const currentDirectory = statSync(physicalRuns, {bigint: true});
        if (realpathSync(runsDir) !== physicalRuns || currentDirectory.dev !== directoryIdentity.dev ||
            currentDirectory.ino !== directoryIdentity.ino) unknown("checkpoint artifact directory changed");
        writeSynced(jsonPath, JSON.stringify(record, null, 2) + "\n");
        writeSynced(markdownPath, requireNonEmptyString(request.markdown, "checkpoint Markdown"));
        appendJsonLineSync(indexPath, row);
      } catch { unknown("checkpoint append outcome is uncertain"); }
      return {ok: true, replayed: false, context, json_path: jsonPath, markdown_path: markdownPath};
    }, firstDelivery && !checkpointContinuationRequired(jsonObject(readback.heartbeat_receipt)), false,
      firstDelivery || resultWork || checkpointContinuationRequired(jsonObject(readback.heartbeat_receipt)), dependencies);
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
