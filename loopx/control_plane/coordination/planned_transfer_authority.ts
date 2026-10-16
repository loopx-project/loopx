/** Authority preparation for the planned-transfer composition.
 *
 * This owner closes canonical writes and pins the complete native journal. It
 * does not certify Host/effect drain, portable-artifact custody or destination
 * admission. The product coordinator must obtain those owners' decisions before
 * exposing a cutover. No public activation entry is registered here.
 */
import {mkdir, readFile} from "node:fs/promises";
import {join} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import {durableWriteJson} from "../effect_runtime_io.ts";
import {EffectRuntimeConflictError} from "../effect_runtime_errors.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {canonicalAuthorityBytes, canonicalAuthoritySha256, hasExactAuthorityKeys, requireAuthorityStoreId} from "./authority_store_codec.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {exportAuthorityArchive, verifyAuthorityArchive, type AuthorityArchiveSummary} from "./authority_archive.ts";
import {indexCoordinationProjection} from "./coordination_projection.ts";
import {canonicalTaskLease} from "./task_lease_state.ts";
import {openLocalAuthorityStoreHandle, requireLocalAuthorityRuntimeRoot} from "./local_authority_provider.ts";
import {loadLegacyCoordinationWriterFence, preparePlannedTransferWriterFenceUnderLock,
  PLANNED_TRANSFER_WRITER_FENCE_SCHEMA} from "./legacy_writer_fence.ts";
import {readShadowManagementState, shadowManagementDirectory, withShadowMaintenanceLock} from "./shadow_management.ts";

const PREPARATION_SCHEMA = "loopx_planned_transfer_authority_preparation_v0";

function reject(message: string): never { throw new EffectRuntimeConflictError(message); }
function same(a: unknown, b: unknown): boolean { return canonicalAuthorityBytes(a).equals(canonicalAuthorityBytes(b)); }
async function optionalJson(path: string): Promise<JsonObject | null> {
  try { return requireJsonObject(JSON.parse(await readFile(path, "utf8")), "transfer preparation"); }
  catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") return null; throw error; }
}
async function retain(path: string, value: JsonObject): Promise<void> {
  const prior = await optionalJson(path);
  if (prior) {
    if (!same(prior, value)) reject("Transfer preparation belongs to different source facts; recover its original operation");
    return;
  }
  await durableWriteJson(path, value);
  if (!same(await optionalJson(path), value)) reject("Transfer preparation readback failed");
}

/** The maintenance guard serializes preparation against every ordinary native
 * writer. Expiration does not settle a lease: every retained token must have
 * been released through its original owner before this operation can fence it.
 *
 * Retrying the same operation recovers the original capture after interruption.
 * It never reopens the source or rewrites a receipt following later changes.
 */
export async function prepareAuthorityForPlannedTransfer(value: JsonObject): Promise<JsonObject> {
  if (!hasExactAuthorityKeys(value, ["runtime_root", "goal_id", "operation_id", "expected_provider_revision",
      "expected_writer_fence_sha256", "execute"]) || value.execute !== true ||
      typeof value.expected_writer_fence_sha256 !== "string" || !BARE_SHA256_PATTERN.test(value.expected_writer_fence_sha256)) {
    reject("Transfer authority preparation requires an explicit execution and the complete reviewed source identity");
  }
  const root = requireLocalAuthorityRuntimeRoot(value.runtime_root);
  const goal = requireAuthorityStoreId(value.goal_id, "transfer Goal id");
  const operation = requireAuthorityStoreId(value.operation_id, "transfer operation id");
  const expectedRevision = requireAuthorityStoreId(value.expected_provider_revision, "reviewed source revision");
  const request = {goal_id: goal, operation_id: operation, expected_provider_revision: expectedRevision,
    expected_writer_fence_sha256: value.expected_writer_fence_sha256};
  const directory = join(shadowManagementDirectory(root, goal), "planned-transfers", canonicalAuthoritySha256(operation));
  return withShadowMaintenanceLock(root, goal, async () => {
    const managed = await readShadowManagementState(root, goal);
    if (managed && managed.status !== "inactive") reject("Source capture requires disposition before planned transfer");
    const current = await loadLegacyCoordinationWriterFence(root, goal);
    if (current.status !== "loaded") reject("Transfer requires the original verified canonical writer fence");
    const opened = await openLocalAuthorityStoreHandle(root, goal, {}, {existingOnly: true});
    if (opened.provider !== "file" && opened.provider !== "sqlite") reject("Planned transfer requires a qualified local File or SQLite source");
    const head = await opened.store.loadAuthority(), identity = await opened.store.storeIdentity();
    if (head.status !== "loaded" || identity.status !== "available") reject("Transfer source authority is unavailable; restore it before preparing a move");
    if (head.provider_revision !== expectedRevision) reject("Source authority changed; review a fresh transfer preview");
    const index = indexCoordinationProjection(head.head, goal);
    const leases = [...index.leases].map(([todo, lease]) => canonicalTaskLease(lease, goal, todo));
    if (leases.some(lease => lease.status !== "released")) {
      reject("Source retains an unreleased lease; settle its original operation before planned transfer");
    }
    const pendingPath = join(directory, "preparation.json");
    const pending = await optionalJson(pendingPath);
    const facts = {schema_version: PREPARATION_SCHEMA, request,
      source_provider: opened.provider, source_store_identity: identity.store_identity,
      source_cursor: head.cursor, source_projection_sha256: canonicalAuthoritySha256(head.head)};
    let priorFence: JsonObject;
    if (pending) {
      priorFence = requireJsonObject(pending.original_writer_fence, "original source writer fence");
      if (!same(pending, {...facts, original_writer_fence: priorFence})) reject("Transfer source or original operation changed");
      // A carrier is written before the fence, so an interrupted first call may
      // still find the exact original fence. Neither case permits other bytes.
      const expectedFence = {schema_version: PLANNED_TRANSFER_WRITER_FENCE_SCHEMA, state: "engaged", goal_id: goal,
        fence_id: `planned-transfer:${operation}`, operation_id: operation, source_identity_sha256: canonicalAuthoritySha256(pending)};
      if (!same(current.fence, priorFence) && !same(current.fence, expectedFence)) reject("Source writer fence changed during transfer preparation");
    } else {
      if (current.fence.schema_version === PLANNED_TRANSFER_WRITER_FENCE_SCHEMA) reject("Source is already prepared by another transfer operation");
      priorFence = current.fence;
    }
    const preparation = {...facts, original_writer_fence: priorFence};
    if (canonicalAuthoritySha256(priorFence) !== value.expected_writer_fence_sha256) reject("Source writer fence changed; review a fresh transfer preview");
    await mkdir(directory, {recursive: true, mode: 0o700});
    await retain(pendingPath, preparation);
    await preparePlannedTransferWriterFenceUnderLock(root, goal, priorFence, {
      schema_version: PLANNED_TRANSFER_WRITER_FENCE_SCHEMA, state: "engaged", goal_id: goal,
      fence_id: `planned-transfer:${operation}`, operation_id: operation,
      source_identity_sha256: canonicalAuthoritySha256(preparation),
    });
    // Export reuses the native retained-journal owner and its independent audit.
    // The fence stays closed on every error, including interrupted publication.
    const archive = join(directory, "authority.ndjson");
    let captured: AuthorityArchiveSummary;
    try { captured = await verifyAuthorityArchive(archive); }
    catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
      captured = await exportAuthorityArchive(opened.store, goal, archive);
    }
    if (captured.goal_id !== goal || captured.source_store_identity !== identity.store_identity ||
        captured.source_provider_revision !== head.provider_revision || captured.commits !== head.cursor ||
        captured.projection_sha256 !== facts.source_projection_sha256) reject("Transfer archive differs from its prepared source");
    const result: JsonObject = {schema_version: PREPARATION_SCHEMA, operation_id: operation, goal_id: goal,
      authority_write_fenced: true, authority_archive: {...captured}, archive,
      host_drain_qualified: false, portable_artifact_qualified: false, execution_authority_granted: false};
    await retain(join(directory, "receipt.json"), result);
    return result;
  });
}
