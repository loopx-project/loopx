/** Missing-checkpoint commit. Index/source claims survive the requesting CLI;
 * the real provider fence lasts through the synchronous durable append. */
import {createHash} from "node:crypto";
import {closeSync, fsyncSync, lstatSync, openSync, readFileSync, writeFileSync} from "node:fs";
import {dirname, join, resolve} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import {settlementIdentity, settlementIdentityPayload} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {claimFileMutationLock, mutationLockOwner, releaseFileMutationLock,
  releaseFileMutationLockClaim, withFileMutationLock, type FileMutationLockClaim} from "../effect_runtime_io.ts";
import {jsonObject, requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {requireLocalAuthorityRuntimeRoot} from "../coordination/local_authority_provider.ts";
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
import {evaluateCheckpointReadContext} from "./checkpoint_read_context.ts";
import {withCheckpointAuthority} from "./checkpoint_authority.ts";
import {goalPathSegment} from "../rollout_receipt_log.ts";

const digest = (bytes: Uint8Array): string => createHash("sha256").update(bytes).digest("hex");
function unknown(message: string): never {
  throw new EffectRuntimeRequestError(`${message}; read back the original Turn before retrying`, "checkpoint_commit_unknown");
}

function indexBytes(path: string): Buffer {
  const bytes = readFileSync(path);
  // The ordinary history reader tolerates damaged rows. A commit cannot infer
  // absence from that reader or append behind a torn (even JSON-valid) tail.
  if (bytes.length && bytes[bytes.length - 1] !== 10) unknown("checkpoint index has an incomplete tail");
  const checkpoints = new Map<string, string>();
  for (const line of bytes.toString("utf8").split(/\r?\n/)) {
    if (!line.trim()) continue;
    try {
      const row = requireJsonObject(JSON.parse(line), "run index row");
      const checkpoint = jsonObject(row.vision_checkpoint);
      const effect = jsonObject(row.settlement_identity)?.effect_id;
      if (checkpoint?.satisfied === true && checkpoint.read_context && typeof effect === "string") {
        const version = canonicalAuthoritySha256({checkpoint, recovery: row.refresh_recovery,
          json_path: row.json_path, markdown_path: row.markdown_path});
        if (checkpoints.has(effect) && checkpoints.get(effect) !== version) unknown("checkpoint index has conflicting decisions");
        checkpoints.set(effect, version);
      }
    }
    catch { unknown("checkpoint index is malformed"); }
  }
  return bytes;
}

function committedArtifacts(prior: JsonObject, runsDir: string): JsonObject {
  const jsonPath = runPath(prior.json_path, runsDir, ".json");
  const markdownPath = runPath(prior.markdown_path, runsDir, ".md");
  let record: JsonObject;
  try {
    record = requireJsonObject(JSON.parse(readFileSync(jsonPath, "utf8")), "checkpoint record");
    readFileSync(markdownPath);
  } catch { unknown("checkpoint artifacts are unavailable"); }
  for (const field of ["settlement_identity", "refresh_recovery", "vision_checkpoint"]) {
    if (canonicalAuthoritySha256(record[field]) !== canonicalAuthoritySha256(prior[field])) {
      unknown("checkpoint artifacts disagree with the committed index");
    }
  }
  return {ok: true, replayed: true, context: jsonObject(prior.vision_checkpoint)?.read_context,
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
  if (dirname(path) !== resolve(runsDir) || !path.endsWith(extension)) {
    unknown("checkpoint artifact path is invalid");
  }
  try { if (!lstatSync(path).isFile()) unknown("checkpoint artifact is not a regular file"); }
  catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
  return path;
}

function writeSynced(path: string, text: string, append = false): void {
  const fd = openSync(path, append ? "a" : "w", 0o600);
  try { writeFileSync(fd, text, {encoding: "utf8"}); fsyncSync(fd); }
  finally { closeSync(fd); }
}

export async function commitCheckpoint(value: unknown): Promise<JsonObject> {
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
  const indexPath = join(runsDir, "index.jsonl");
  const statePath = resolve(requireNonEmptyString(request.state_file, "state_file"));
  const targets = [indexPath, shadowMaintenanceLockPath(root, identity.goal_id),
    legacyCoordinationTodoLockPath(root, identity.goal_id), statePath].map(path => resolve(path));
  const owner = parseQuotaAccountingOwner({
    goalRefValue: request.goal_ref,
    sourceAdmissionValue: request.source_admission,
    runtimeRoot: root,
    goalId: identity.goal_id,
  });
  const retry = requireJsonObject(request.refresh_retry, "refresh retry");
  const receiptPath = join(root, "goals", identity.goal_id, "checkpoint-contexts",
    `${createHash("sha256").update(identity.effect_id).digest("hex")}.json`);

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
    if (recovery.decision !== "replay") return null;
    const prior = requireJsonObject(readback.writeback_run, "committed checkpoint");
    if (jsonObject(prior.vision_checkpoint)?.satisfied !== true ||
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
    if (recovery.decision !== "supplement_checkpoint") {
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
      let receipt: unknown = null;
      try { receipt = JSON.parse(readFileSync(receiptPath, "utf8")); }
      catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
      const context = evaluateCheckpointReadContext({phase: "check", identity: binding,
        read_context_id: retry.checkpoint_read_context_id, receipt, facts: current});
      if (context.ok !== true) return context;
      const record = requireJsonObject(request.record, "checkpoint record");
      const row = requireJsonObject(request.index_record, "checkpoint index record");
      for (const projected of [record, row]) {
        if (canonicalAuthoritySha256(projected.settlement_identity) !== canonicalAuthoritySha256(binding) ||
            canonicalAuthoritySha256(projected.refresh_recovery) !== canonicalAuthoritySha256(recovery) ||
            jsonObject(projected.vision_checkpoint)?.satisfied !== true) {
          throw new EffectRuntimeRequestError("checkpoint projection does not match typed admission");
        }
        projected.vision_checkpoint = {...requireJsonObject(projected.vision_checkpoint, "vision checkpoint"), read_context: context};
      }
      const jsonPath = runPath(row.json_path, runsDir, ".json");
      const markdownPath = runPath(row.markdown_path, runsDir, ".md");
      try {
        writeSynced(jsonPath, JSON.stringify(record, null, 2) + "\n");
        writeSynced(markdownPath, requireNonEmptyString(request.markdown, "checkpoint Markdown"));
        writeSynced(indexPath, JSON.stringify(row) + "\n", true);
      } catch { unknown("checkpoint append outcome is uncertain"); }
      return {ok: true, replayed: false, context, json_path: jsonPath, markdown_path: markdownPath};
    });
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
