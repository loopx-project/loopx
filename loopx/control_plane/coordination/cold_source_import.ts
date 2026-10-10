/** Reviewed coordination cutover for a stopped source without active capture.
 * The recovery carrier preserves source bytes; it is not a whole-Goal backup.
 * Host shutdown is an explicit operator attestation, never inferred from locks.
 */
import {createHash} from "node:crypto";
import {createReadStream} from "node:fs";
import {lstat, readFile, readdir} from "node:fs/promises";
import {join, isAbsolute, relative, resolve} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import {durableWriteJson} from "../effect_runtime_io.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {canonicalAuthorityBytes, canonicalAuthorityObject, canonicalAuthoritySha256,
  hasExactAuthorityKeys, requireAuthorityStoreId} from "./authority_store_codec.ts";
import {FileAuthorityStore} from "./file_authority_store.ts";
import {localAuthorityProviderPaths, openLocalAuthorityStoreHandle,
  requireLocalAuthorityRuntimeRoot, localAuthorityOpenFailure, selectLocalAuthorityTarget,
  type LocalAuthorityProviderDependencies} from "./local_authority_provider.ts";
import {COLD_SOURCE_IMPORT_WRITER_FENCE_SCHEMA, engageLegacyCoordinationWriterFenceUnderLocks,
  loadLegacyCoordinationWriterFence} from "./legacy_writer_fence.ts";
import {readShadowManagementState, ShadowManagementError, shadowManagementDirectory,
  withShadowMaintenanceLock} from "./shadow_management.ts";
import {verifyShadowSourceSnapshot, withShadowSourceLocks, type ShadowRequest} from "./runtime_shadow.ts";
import {planHandoffPolicyMigration} from "./handoff_policy_migration.ts";
import {canonicalTaskLease} from "./task_lease_state.ts";
import {indexCoordinationProjection} from "./coordination_projection.ts";
import {commitPromotionAndReadBack, readPromotionReceipt} from "./promotion_receipt.ts";

export const COLD_SOURCE_IMPORT_REQUEST_SCHEMA = "loopx_cold_source_import_request_v0";
const PLAN_SCHEMA = "loopx_cold_source_import_plan_v0";
const RESULT_SCHEMA = "loopx_cold_source_import_result_v0";

function reject(code: string): never { throw new ShadowManagementError(code); }
function digest(bytes: Buffer): string { return `sha256:${createHash("sha256").update(bytes).digest("hex")}`; }
function same(left: unknown, right: unknown): boolean { return canonicalAuthorityBytes(left).equals(canonicalAuthorityBytes(right)); }
function carrierPath(root: string, goal: string, operation: string): string {
  return join(shadowManagementDirectory(root, goal), "cold-imports", `${canonicalAuthoritySha256(operation)}.json`);
}
function sourceInventory(projection: JsonObject, goal: string): JsonObject {
  const index = indexCoordinationProjection(projection, goal);
  return {todo_count: index.todos.size,
    archived_todo_count: [...index.todos.values()].filter(row => row.archive_state === "archive").length,
    lease_count: index.leases.size, source_handoff_mode: projection.handoff_mode};
}
async function optionalJson(path: string): Promise<JsonObject | null> {
  try { return canonicalAuthorityObject(JSON.parse(await readFile(path, "utf8")), "import carrier"); }
  catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") return null; throw error; }
}
async function names(path: string): Promise<string[]> {
  try { return (await readdir(path)).sort(); }
  catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") return []; throw error; }
}

/** Check every retained lease, including orphans and expired active records.
 * A settled source token remains history, never a fresh execution grant. */
async function requireStoppedSource(request: ShadowRequest): Promise<void> {
  const managed = await readShadowManagementState(request.runtime_root, request.goal_id);
  if (managed && managed.status !== "inactive") reject("cold_import_capture_requires_disposition");
  if ((await names(join(request.runtime_root, "authority-shadow", "outbox", request.goal_id))).length) {
    reject("cold_import_outbox_requires_disposition");
  }
  const shadow = new FileAuthorityStore(join(request.runtime_root, "authority-shadow", "file-v0"), request.goal_id, {existingOnly: true});
  if ((await shadow.loadAuthority()).status !== "missing") reject("cold_import_shadow_requires_disposition");
  const directory = join(request.runtime_root, "goals", request.goal_id, "task-leases");
  try { if (!(await lstat(directory)).isDirectory()) reject("cold_import_lease_source_unsafe"); }
  catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
  for (const name of await names(directory)) {
    if (!name.endsWith(".json")) continue;
    if (!/^[A-Za-z0-9_.-]+\.json$/.test(name)) reject("cold_import_lease_source_unsupported");
    const path = join(directory, name);
    if (!(await lstat(path)).isFile()) reject("cold_import_lease_source_unsafe");
    const raw = canonicalAuthorityObject(JSON.parse(await readFile(path, "utf8")), "source lease");
    const lease = canonicalTaskLease(raw, request.goal_id, name.slice(0, -5));
    if (lease.status !== "released") reject("cold_import_lease_requires_settlement");
  }
  await verifyShadowSourceSnapshot(request);
}

async function sourceWitness(request: ShadowRequest): Promise<JsonObject[]> {
  const snapshot = request.source_snapshot;
  const inventory = snapshot.lease_inventory as JsonObject[];
  const files = [
    {path: snapshot.state_path, bytes_sha256: snapshot.state_bytes_sha256},
    {path: (snapshot.registry_source as JsonObject).path, bytes_sha256: `sha256:${(snapshot.registry_source as JsonObject).sha256}`},
    ...(snapshot.evidence_files as JsonObject[]),
    ...inventory.map(item => ({path: join(request.runtime_root, "goals", request.goal_id, "task-leases", String(item.name)), bytes_sha256: item.bytes_sha256})),
  ];
  return Promise.all(files.map(async file => {
    let bytes: Buffer | null = null;
    try {
      if (!(await lstat(String(file.path))).isFile()) reject("cold_import_source_unsafe");
      bytes = await readFile(String(file.path));
    } catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
    if ((bytes === null ? null : digest(bytes)) !== file.bytes_sha256) reject("source_changed_retry");
    return {...file, bytes_base64: bytes === null ? null : bytes.toString("base64")};
  }));
}

/** The trusted Host tar adapter witnesses actual archived members. This typed
 * owner checks source coverage and pins both saved artifacts through apply.
 * It qualifies coordination-source bytes, not complete-state reactivation. */
async function verifySourceBackup(value: unknown, root: string, sources: JsonObject[] = []): Promise<void> {
  const backup = requireJsonObject(value, "source backup");
  if (!hasExactAuthorityKeys(backup, ["schema_version", "archive_path", "archive_sha256",
      "manifest_path", "manifest_sha256", "included", "members"]) ||
      backup.schema_version !== "loopx_state_backup_source_witness_v0" ||
      !Array.isArray(backup.included) || !Array.isArray(backup.members)) reject("cold_import_backup_invalid");
  for (const kind of ["archive", "manifest"] as const) {
    const path = backup[`${kind}_path`];
    const expected = backup[`${kind}_sha256`];
    if (typeof path !== "string" || !isAbsolute(path) || typeof expected !== "string" ||
        !BARE_SHA256_PATTERN.test(expected)) reject("cold_import_backup_invalid");
    if (!(await lstat(path)).isFile()) reject("cold_import_backup_unsafe");
    const hash = createHash("sha256");
    for await (const chunk of createReadStream(path)) hash.update(chunk);
    if (hash.digest("hex") !== expected) reject("cold_import_backup_changed");
  }
  const included = backup.included as JsonObject[];
  if (!included.some(item => item.source_path === root)) reject("cold_import_backup_runtime_missing");
  const members = new Map<string, string>();
  for (const item of backup.members as JsonObject[]) {
    if (!hasExactAuthorityKeys(item, ["archive_path", "sha256"]) || typeof item.archive_path !== "string" ||
        typeof item.sha256 !== "string" || !BARE_SHA256_PATTERN.test(item.sha256) ||
        members.has(item.archive_path)) reject("cold_import_backup_invalid");
    members.set(item.archive_path, item.sha256);
  }
  for (const item of included) {
    if (!hasExactAuthorityKeys(item, ["source_path", "archive_path"]) ||
        typeof item.source_path !== "string" || !isAbsolute(item.source_path) ||
        typeof item.archive_path !== "string" || item.archive_path.startsWith("/") ||
        item.archive_path.split("/").some(part => part === "..") || item.archive_path.includes("\\")) {
      reject("cold_import_backup_invalid");
    }
  }
  for (const file of sources) {
    if (file.bytes_sha256 === null) continue;
    const matched = included.some(item => {
      const suffix = relative(String(item.source_path), String(file.path));
      if (isAbsolute(suffix) || suffix === ".." || suffix.startsWith(`..${process.platform === "win32" ? "\\" : "/"}`)) return false;
      const member = suffix === "" ? String(item.archive_path) : `${item.archive_path}/${suffix.replaceAll("\\", "/")}`;
      return `sha256:${members.get(member)}` === file.bytes_sha256;
    });
    if (!matched) reject("cold_import_backup_source_missing_or_changed");
  }
}

function verifyCarrier(raw: JsonObject, root: string, goal: string, operation: string, expected: unknown): JsonObject {
  if (!hasExactAuthorityKeys(raw, ["plan", "plan_sha256"])) reject("cold_import_carrier_invalid");
  const plan = requireJsonObject(raw.plan, "import plan");
  if (!hasExactAuthorityKeys(plan, ["schema_version", "runtime_root", "goal_id", "operation_id",
      "source", "source_witness", "source_backup", "target_projection", "target_provider", "target_store_identity"]) ||
      plan.schema_version !== PLAN_SCHEMA || plan.runtime_root !== root || plan.goal_id !== goal ||
      plan.operation_id !== operation || canonicalAuthoritySha256(plan) !== raw.plan_sha256 ||
      raw.plan_sha256 !== expected) reject("cold_import_reviewed_plan_changed");
  if (!Array.isArray(plan.source_witness)) reject("cold_import_carrier_invalid");
  const source = requireJsonObject(plan.source, "retained import source");
  const projection = requireJsonObject(plan.target_projection, "retained import target");
  if (!hasExactAuthorityKeys(source, ["runtime_root", "goal_id", "projection", "source_snapshot"]) ||
      source.runtime_root !== root || source.goal_id !== goal ||
      (projection.handoff_mode !== "soft_claim" && projection.handoff_mode !== "hard_lease")) reject("cold_import_carrier_invalid");
  const snapshot = requireJsonObject(source.source_snapshot, "retained source snapshot");
  const registration = requireJsonObject(snapshot.registry_source, "retained registry source");
  const migration = planHandoffPolicyMigration(requireJsonObject(source.projection, "retained source projection"),
    goal, projection.handoff_mode, registration.registered_agents as string[], new Date());
  if (!migration.ready || !same(migration.target_projection, projection)) reject("cold_import_carrier_invalid");
  for (const item of plan.source_witness as JsonObject[]) {
    if (!hasExactAuthorityKeys(item, ["path", "bytes_sha256", "bytes_base64"]) ||
        (item.bytes_base64 === null ? item.bytes_sha256 !== null :
          typeof item.bytes_base64 !== "string" || digest(Buffer.from(item.bytes_base64, "base64")) !== item.bytes_sha256)) {
      reject("cold_import_carrier_invalid");
    }
  }
  return plan;
}

/** Prepare persists an immutable reviewed source/target carrier. Apply
 * revalidates it under the primary locks before fencing. Recovery uses only
 * the original durable carrier and fence, never a new Markdown snapshot. */
export async function executeColdSourceImport(value: unknown,
  dependencies: LocalAuthorityProviderDependencies = {}): Promise<JsonObject> {
  let fenced: boolean | null = null;
  let attempted = false;
  try {
    const input = requireJsonObject(value, "cold source import request");
    const action = input.action;
    if (input.schema_version !== COLD_SOURCE_IMPORT_REQUEST_SCHEMA ||
        (action !== "prepare" && action !== "apply" && action !== "recover" && action !== "readback") ||
        !hasExactAuthorityKeys(input, action === "prepare"
          ? ["schema_version", "action", "runtime_root", "goal_id", "operation_id", "projection", "source_snapshot", "target_handoff_mode", "target_provider", "source_backup"]
          : action === "apply"
            ? ["schema_version", "action", "runtime_root", "goal_id", "operation_id", "expected_plan_sha256", "writers_stopped"]
            : ["schema_version", "action", "runtime_root", "goal_id", "operation_id", "expected_plan_sha256"])) {
      reject("cold_import_request_invalid");
    }
    const root = requireLocalAuthorityRuntimeRoot(input.runtime_root);
    const goal = requireAuthorityStoreId(input.goal_id, "goal id");
    if (/[/\\\0]/.test(goal) || goal === "." || goal === "..") reject("cold_import_request_invalid");
    const operation = requireAuthorityStoreId(input.operation_id, "operation id");
    const path = carrierPath(root, goal, operation);
    if (action === "prepare") {
      if (input.target_provider !== "file" && input.target_provider !== "sqlite") reject("cold_import_provider_unsupported");
      if (input.target_handoff_mode !== "soft_claim" && input.target_handoff_mode !== "hard_lease") reject("cold_import_handoff_mode_invalid");
      // Empty-target selection owns its own M lock, as on canonical creation.
      // Preflight does not replace the locked source/backup checks below. It
      // rejects absent source/backup before publishing target identity metadata.
      const source: ShadowRequest = {runtime_root: root, goal_id: goal,
        projection: canonicalAuthorityObject(input.projection, "source projection"),
        source_snapshot: canonicalAuthorityObject(input.source_snapshot, "source snapshot")};
      await requireStoppedSource(source);
      await verifySourceBackup(input.source_backup, root, await sourceWitness(source));
      await selectLocalAuthorityTarget(root, goal, input.target_provider, true);
    }
    return await withShadowMaintenanceLock(root, goal, async () => {
      const prior = await loadLegacyCoordinationWriterFence(root, goal);
      fenced = prior.status === "loaded" ? true : prior.status === "missing" ? false : null;
      if (prior.status === "failed") reject(prior.reason_code);
      const opened = await openLocalAuthorityStoreHandle(root, goal, dependencies,
        {existingOnly: action !== "prepare"});
      if (opened.provider !== "file" && opened.provider !== "sqlite") reject("cold_import_provider_unsupported");
      const targetIdentity = await opened.store.storeIdentity();
      if (targetIdentity.status !== "available") reject(targetIdentity.reason_code);
      if (action === "prepare") {
        if (prior.status !== "missing") reject("cold_import_already_fenced");
        if (input.target_handoff_mode !== "soft_claim" && input.target_handoff_mode !== "hard_lease") reject("cold_import_handoff_mode_invalid");
        const targetMode = input.target_handoff_mode;
        const source: ShadowRequest = {runtime_root: root, goal_id: goal,
          projection: canonicalAuthorityObject(input.projection, "source projection"),
          source_snapshot: canonicalAuthorityObject(input.source_snapshot, "source snapshot")};
        return await withShadowSourceLocks(source, async () => {
          if ((await opened.store.loadAuthority()).status !== "missing") reject("cold_import_target_not_empty");
          await requireStoppedSource(source);
          const registered = (source.source_snapshot.registry_source as JsonObject).registered_agents as string[];
          const migration = planHandoffPolicyMigration(source.projection, goal, targetMode, registered, new Date());
          if (!migration.ready) reject(migration.reason_code ?? "cold_import_handoff_conflict");
          const witness = await sourceWitness(source);
          await verifySourceBackup(input.source_backup, root, witness);
          const plan = {schema_version: PLAN_SCHEMA, runtime_root: root, goal_id: goal, operation_id: operation,
            source, source_witness: witness, source_backup: input.source_backup, target_projection: migration.target_projection,
            target_provider: opened.provider, target_store_identity: targetIdentity.store_identity};
          const carrier = {plan, plan_sha256: canonicalAuthoritySha256(plan)};
          const existing = await optionalJson(path);
          if (existing !== null && !same(existing, carrier)) reject("cold_import_operation_conflict");
          if (existing === null) await durableWriteJson(path, carrier);
          verifyCarrier((await optionalJson(path))!, root, goal, operation, carrier.plan_sha256);
          return {schema_version: RESULT_SCHEMA, ok: true, status: "prepared", operation_id: operation,
            plan_path: path, plan_sha256: carrier.plan_sha256, target_provider: opened.provider,
            source_inventory: sourceInventory(source.projection, goal), authority_changed: false,
            target_handoff_mode: targetMode, legacy_writer_fenced: false,
            execution_authority_granted: false, coordination_source_backup_verified: true, complete_goal_backup_verified: false};
        });
      }
      const raw = await optionalJson(path);
      if (raw === null) reject("cold_import_carrier_missing");
      const plan = verifyCarrier(raw, root, goal, operation, input.expected_plan_sha256);
      if (opened.provider !== plan.target_provider || targetIdentity.store_identity !== plan.target_store_identity) {
        reject("cold_import_target_identity_changed");
      }
      const fence = {schema_version: COLD_SOURCE_IMPORT_WRITER_FENCE_SCHEMA, state: "engaged", goal_id: goal,
        fence_id: `cold-import:${operation}`, import_operation_id: operation, import_plan_sha256: raw.plan_sha256};
      if (prior.status === "loaded" && !same(prior.fence, fence)) reject("cold_import_fence_conflict");
      const source = plan.source as ShadowRequest;
      const projection = requireJsonObject(plan.target_projection, "import target projection");
      const identity = {operation_id: operation, projection_sha256: canonicalAuthoritySha256(projection),
        receipt: {schema_version: "loopx_cold_source_import_receipt_v0", goal_id: goal,
          operation_id: operation, import_plan_sha256: raw.plan_sha256, source_projection_sha256: canonicalAuthoritySha256(source.projection)}};
      const marker = {operation_id: operation, import_plan_sha256: raw.plan_sha256,
        target_store_identity: targetIdentity.store_identity, receipt_sha256: canonicalAuthoritySha256(identity.receipt)};
      const completed = await optionalJson(`${path}.completed.json`);
      if (completed !== null && !same(completed, marker)) reject("cold_import_completion_identity_changed");
      if (action === "readback") {
        // Page reload observes the original intent; it never completes a
        // partially committed operation or treats a preview as confirmation.
        const original = prior.status === "loaded" ? await readPromotionReceipt(opened.store, identity) : null;
        if (!original?.matched) {
          if (completed !== null) reject("cold_import_completed_authority_missing");
          const head = await opened.store.loadAuthority();
          if (head.status !== "missing") reject(original?.reason_code ?? "cold_import_target_not_empty");
        }
        return {schema_version: RESULT_SCHEMA, ok: true,
          status: original?.matched ? "replayed" : "prepared",
          operation_id: operation, plan_sha256: raw.plan_sha256, target_provider: opened.provider,
          target_handoff_mode: projection.handoff_mode, source_inventory: sourceInventory(source.projection, goal),
          legacy_writer_fenced: prior.status === "loaded", authority_changed: false,
          execution_authority_granted: false, complete_goal_backup_verified: false};
      }
      // File's selected identity already exists; allow only the first head in
      // that exact identity, never recreation of a lost identity.
      const store = opened.provider === "file" && dependencies.createStore === undefined
        ? new FileAuthorityStore(localAuthorityProviderPaths(root, goal).file, goal, {expectedIdentity: targetIdentity.store_identity})
        : opened.store;
      const complete = async () => {
        const original = await readPromotionReceipt(store, identity);
        if (original.matched) {
          if (completed === null) await durableWriteJson(`${path}.completed.json`, marker);
          return {status: "replayed", ...original};
        }
        if (completed !== null) reject("cold_import_completed_authority_missing");
        if ((await store.loadAuthority()).status !== "missing") reject(original.reason_code);
        await verifySourceBackup(plan.source_backup, root, plan.source_witness as JsonObject[]);
        attempted = true;
        const result = await commitPromotionAndReadBack(store, identity, projection,
          {...identity.receipt, schema_version: "loopx_cold_source_import_event_v0"});
        if (!result.readback.matched) reject(result.readback.reason_code);
        await durableWriteJson(`${path}.completed.json`, marker);
        return {status: result.interrupted || result.commit?.status === "ambiguous" ? "recovered" : "applied", ...result.readback};
      };
      let result: JsonObject;
      if (action === "recover") {
        if (prior.status !== "loaded") reject("cold_import_recovery_requires_fence");
        result = await complete();
      } else {
        if (input.writers_stopped !== true) reject("cold_import_operator_stop_confirmation_required");
        result = await withShadowSourceLocks(source, async () => {
          if (prior.status === "missing") {
            await requireStoppedSource(source);
            if (!same(await sourceWitness(source), plan.source_witness)) reject("source_changed_retry");
            await verifySourceBackup(plan.source_backup, root, plan.source_witness as JsonObject[]);
            if ((await store.loadAuthority()).status !== "missing") reject("cold_import_target_not_empty");
            const applied = await engageLegacyCoordinationWriterFenceUnderLocks(root, goal,
              String(source.source_snapshot.state_path), fence);
            if (applied.status !== "applied" && applied.status !== "replayed") reject("cold_import_fence_unverified");
            fenced = true;
          }
          return await complete();
        });
      }
      return {schema_version: RESULT_SCHEMA, ok: true, ...result, operation_id: operation,
        authority_changed: attempted, target_handoff_mode: projection.handoff_mode,
        source_inventory: sourceInventory(source.projection, goal),
        executed: attempted,
        plan_sha256: raw.plan_sha256, target_provider: opened.provider, legacy_writer_fenced: true,
        stop_confirmation_source: "operator_attestation", execution_authority_granted: false,
        complete_goal_backup_verified: false, legacy_fallback_used: false};
    });
  } catch (error) {
    return {schema_version: RESULT_SCHEMA, ok: false, status: "failed", executed: attempted,
      authority_changed: attempted ? null : false, execution_authority_granted: false,
      reason_code: error instanceof ShadowManagementError ? error.reason_code : "cold_import_unavailable",
      reason: error instanceof Error ? error.message : "cold import unavailable",
      legacy_writer_fenced: fenced, legacy_fallback_used: false, ...localAuthorityOpenFailure(error)};
  }
}
