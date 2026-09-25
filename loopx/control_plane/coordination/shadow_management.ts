/** Durable, per-goal shadow lifecycle. The journal never grants decision authority. */
import { createHash, randomUUID } from "node:crypto";
import { realpathSync } from "node:fs";
import { lstat, mkdir, open, readFile, readdir, rename } from "node:fs/promises";
import { dirname, isAbsolute, join, resolve } from "node:path";
import type { JsonObject } from "../effect_program.ts";
import { atomicWriteJson, withFileMutationLock } from "../effect_runtime_io.ts";
import {
  canonicalAuthorityBytes, canonicalAuthorityObject, canonicalAuthoritySha256,
  hasExactAuthorityKeys, isAuthorityJsonObject, requireAuthorityStoreId,
} from "./authority_store_codec.ts";
import { FileAuthorityStore } from "./file_authority_store.ts";
import {
  SHADOW_MANAGEMENT_STATE_SCHEMA, SHADOW_MANAGEMENT_MANIFEST_SCHEMA, SHADOW_OUTBOX_MANIFEST_SCHEMA,
} from "./coordination_state_contract.generated.ts";

export { SHADOW_MANAGEMENT_STATE_SCHEMA, SHADOW_MANAGEMENT_MANIFEST_SCHEMA, SHADOW_OUTBOX_MANIFEST_SCHEMA };
export const SHADOW_CAPTURE_PROFILE = "file_outbox_v1";
const DIGEST = /^sha256:[0-9a-f]{64}$/;

export interface ShadowCaptureBinding extends JsonObject {
  capture_profile: string;
  capture_lineage_id: string;
  source_root_digest: string;
  store_identity: string;
  bootstrap_operation_id: string;
  bootstrap_provider_revision: string;
}
export interface ShadowManagementState extends JsonObject {
  schema_version: string;
  goal_id: string;
  source_root_digest: string;
  status: "bootstrapping" | "active" | "rolling_back" | "inactive";
  binding: ShadowCaptureBinding | null;
  operation: JsonObject;
  previous_operation_id: string | null;
  result: JsonObject | null;
}
export interface ShadowManagementDependencies {
  withPrimaryLocks: <T>(operation: () => Promise<T>) => Promise<T>;
  verifySourceSnapshot?: () => Promise<void>;
  /** A filesystem effect boundary used by real-process crash qualification. */
  afterEffect?: (phase: string) => Promise<void>;
}
export class ShadowManagementError extends Error {
  readonly code: string;
  readonly reason_code: string;
  readonly payload: JsonObject;
  constructor(code: string, message = code) {
    super(message);
    this.code = code;
    this.reason_code = code;
    this.payload = { status: "blocked", reason_code: code };
  }
}
function canonicalSourceRoot(root: string): string {
  const lexical = resolve(root);
  try { return realpathSync(lexical); } catch { return lexical; }
}
function sourceRootDigests(root: string): Set<string> {
  return new Set([resolve(root), canonicalSourceRoot(root)].map(path =>
    `sha256:${createHash("sha256").update(path, "utf8").digest("hex")}`));
}
export function shadowSourceRootDigest(root: string): string {
  return `sha256:${createHash("sha256").update(canonicalSourceRoot(root), "utf8").digest("hex")}`;
}
function validSourceRootDigest(value: unknown, root: string): value is string {
  return typeof value === "string" && sourceRootDigests(root).has(value);
}
function sameSourceRoot(left: string, right: string): boolean {
  return canonicalSourceRoot(left) === canonicalSourceRoot(right);
}
export function shadowManagementDirectory(root: string, goal: string): string {
  const digest = createHash("sha256").update(goal, "utf8").digest("hex").slice(0, 16);
  return join(root, "authority-transition", "file-v0", `shadow-management-${digest}`);
}
export function shadowMaintenanceLockPath(root: string, goal: string): string {
  return join(shadowManagementDirectory(root, goal), "maintenance");
}
export function shadowManagementStatePath(root: string, goal: string): string {
  return join(shadowManagementDirectory(root, goal), "state.json");
}
export async function withShadowMaintenanceLock<T>(root: string, goal: string, operation: () => Promise<T>): Promise<T> {
  await makeDirectoryDurable(shadowManagementDirectory(root, goal));
  return await withFileMutationLock(shadowMaintenanceLockPath(root, goal), operation);
}
function exact(value: unknown, fields: string[]): value is JsonObject {
  return isAuthorityJsonObject(value) && hasExactAuthorityKeys(value, fields);
}
function text(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && value.trim() === value;
}
function validBinding(value: unknown, digest: string): value is ShadowCaptureBinding {
  return exact(value, ["capture_profile", "capture_lineage_id", "source_root_digest", "store_identity", "bootstrap_operation_id", "bootstrap_provider_revision"])
    && Object.values(value).every(text) && value.capture_profile === SHADOW_CAPTURE_PROFILE
    && value.source_root_digest === digest && /^file:[0-9a-f]{32}$/.test(String(value.store_identity))
    && /^file:[1-9][0-9]*:[0-9a-f]{24}$/.test(String(value.bootstrap_provider_revision));
}
function decodeState(value: unknown, root: string, goal: string): ShadowManagementState {
  const fail = () => { throw new ShadowManagementError("shadow_management_state_invalid"); };
  if (!exact(value, ["schema_version", "goal_id", "source_root_digest", "status", "binding", "operation", "previous_operation_id", "result"])) return fail();
  if (value.schema_version !== SHADOW_MANAGEMENT_STATE_SCHEMA || value.goal_id !== goal
      || !DIGEST.test(String(value.source_root_digest))) return fail();
  const digest = String(value.source_root_digest);
  if (!["bootstrapping", "active", "rolling_back", "inactive"].includes(String(value.status))) return fail();
  const operation = value.operation;
  if (!exact(operation, ["kind", "operation_id", "request_digest", "manifest_digest", "phase"]) || !text(operation.operation_id)
      || !DIGEST.test(String(operation.request_digest)) || !DIGEST.test(String(operation.manifest_digest))) return fail();
  const kind = ["bootstrapping", "active"].includes(String(value.status)) ? "bootstrap" : "rollback";
  const terminal = ["active", "inactive"].includes(String(value.status));
  const phases = terminal ? ["complete"] : kind === "bootstrap" ? ["prepared", "candidate_committed", "outbox_ready"] : ["prepared", "candidate_archived", "outbox_archived"];
  if (operation.kind !== kind || !phases.includes(String(operation.phase))) return fail();
  if (value.previous_operation_id !== null && !text(value.previous_operation_id)) return fail();
  if (terminal ? !isAuthorityJsonObject(value.result) : value.result !== null) return fail();
  if (value.binding !== null && !validBinding(value.binding, digest)) return fail();
  if ((value.status === "active" && value.binding === null) || (value.status === "inactive" && value.binding !== null)) return fail();
  return value as ShadowManagementState;
}
async function readJson(path: string): Promise<JsonObject | null> {
  let raw: string;
  try { raw = await readFile(path, "utf8"); }
  catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") return null; throw error; }
  return canonicalAuthorityObject(JSON.parse(raw), "management document");
}
export async function readShadowManagementState(root: string, goal: string): Promise<ShadowManagementState | null> {
  try {
    const value = await readJson(shadowManagementStatePath(root, goal));
    if (value === null) return null;
    const state = decodeState(value, root, goal);
    if (!validSourceRootDigest(state.source_root_digest, root)) {
      const locator: ManagementRequest = {
        runtime_root: root,
        goal_id: goal,
        operation_id: String(state.operation.operation_id),
      };
      const manifest = await readJson(manifestPath(locator));
      if (!manifest || managementDigest(manifest) !== state.operation.manifest_digest
          || manifest.source_root_digest !== state.source_root_digest
          || manifest.request_digest !== state.operation.request_digest
          || !isAuthorityJsonObject(manifest.request)) {
        throw new ShadowManagementError("shadow_management_state_invalid");
      }
      const original = requestOf(manifest.request);
      if (original.goal_id !== goal || original.operation_id !== state.operation.operation_id
          || requestDigest(original) !== manifest.request_digest
          || !sameSourceRoot(original.runtime_root, root)) {
        throw new ShadowManagementError("shadow_management_state_invalid");
      }
    }
    return state;
  } catch (error) {
    if (error instanceof ShadowManagementError) throw error;
    throw new ShadowManagementError("shadow_management_state_invalid");
  }
}
export async function requireShadowPrimaryWriteAllowed(root: string, goal: string): Promise<ShadowCaptureBinding | null> {
  const state = await readShadowManagementState(root, goal);
  if (!state || state.status === "inactive") return null;
  if (state.status !== "active") throw new ShadowManagementError("shadow_management_in_progress");
  return state.binding;
}
export async function requireShadowCaptureBinding(root: string, goal: string): Promise<ShadowCaptureBinding> {
  const binding = await requireShadowPrimaryWriteAllowed(root, goal);
  if (!binding) throw new ShadowManagementError("bootstrap_required");
  return binding;
}

/** Read the source path established by this active bootstrap. The caller owns
 * exclusion; this helper takes no locks and never creates or repairs files.
 */
export async function readShadowBootstrapSourcePath(root: string, goal: string, binding: ShadowCaptureBinding): Promise<string> {
  const state = await readShadowManagementState(root, goal);
  if (!state || state.status === "inactive") throw new ShadowManagementError("bootstrap_required");
  if (state.status !== "active") throw new ShadowManagementError("shadow_management_in_progress");
  if (!same(state.binding, binding) || state.operation.operation_id !== binding.bootstrap_operation_id) {
    throw new ShadowManagementError("stale_generation");
  }
  const locator: ManagementRequest = { runtime_root: root, goal_id: goal, operation_id: binding.bootstrap_operation_id };
  const invalid = () => { throw new ShadowManagementError("shadow_management_manifest_invalid"); };
  let manifest: JsonObject | null;
  try { manifest = await readJson(manifestPath(locator)); } catch { return invalid(); }
  if (!manifest || managementDigest(manifest) !== state.operation.manifest_digest
      || manifest.schema_version !== SHADOW_MANAGEMENT_MANIFEST_SCHEMA || manifest.kind !== "bootstrap"
      || manifest.goal_id !== goal || manifest.operation_id !== binding.bootstrap_operation_id
      || manifest.capture_lineage_id !== binding.capture_lineage_id
      || manifest.source_root_digest !== binding.source_root_digest
      || manifest.request_digest !== state.operation.request_digest
      || !isAuthorityJsonObject(manifest.request)) return invalid();
  const request = manifest.request;
  if (!sameSourceRoot(String(request.runtime_root), root) || request.goal_id !== goal || request.operation_id !== binding.bootstrap_operation_id
      || requestDigest(request as ManagementRequest) !== manifest.request_digest
      || !isAuthorityJsonObject(request.source_snapshot)) return invalid();
  const path = request.source_snapshot.state_path;
  if (!text(path) || !isAbsolute(path) || path.includes("\0")) return invalid();
  const current = await readShadowManagementState(root, goal);
  if (!same(current, state)) throw new ShadowManagementError("stale_generation");
  return path;
}

interface ManagementRequest extends JsonObject { runtime_root: string; goal_id: string; operation_id: string }
function requestOf(value: unknown): ManagementRequest {
  const request = canonicalAuthorityObject(value, "shadow management request");
  for (const name of ["runtime_root", "goal_id", "operation_id"]) requireAuthorityStoreId(request[name], name);
  if (resolve(String(request.runtime_root)) !== request.runtime_root) throw new ShadowManagementError("invalid_shadow_management_request");
  if ([".", ".."].includes(String(request.goal_id)) || /[/\\\0]/.test(String(request.goal_id))) throw new ShadowManagementError("invalid_shadow_management_request");
  return request as ManagementRequest;
}
function operationDirectory(request: ManagementRequest): string {
  const digest = createHash("sha256").update(request.operation_id).digest("hex");
  return join(shadowManagementDirectory(request.runtime_root, request.goal_id), "operations", digest);
}
function manifestPath(request: ManagementRequest): string { return join(operationDirectory(request), "manifest.json"); }
function resultPath(request: ManagementRequest): string { return join(operationDirectory(request), "result.json"); }
function outboxPath(request: ManagementRequest): string { return join(request.runtime_root, "authority-shadow", "outbox", request.goal_id); }
function archiveOutboxPath(request: ManagementRequest): string { return join(operationDirectory(request), "outbox"); }
function provider(request: ManagementRequest, existingOnly = true, dependencies?: ShadowManagementDependencies): FileAuthorityStore {
  class ManagedFileStore extends FileAuthorityStore {
    protected override async archiveRenamed(): Promise<void> {
      await dependencies?.afterEffect?.("rollback_candidate_renamed");
    }
  }
  return new ManagedFileStore(join(request.runtime_root, "authority-shadow", "file-v0"), request.goal_id, { existingOnly });
}
function managementDigest(value: unknown): string {
  return `sha256:${canonicalAuthoritySha256(value)}`;
}
function requestDigest(request: ManagementRequest): string {
  // Rollback selects an immutable target. Fresh primary readback is evidence,
  // not part of the operation identity after that target has been archived.
  if ("expected_provider_revision" in request) {
    return managementDigest({
      kind: "rollback", schema_version: request.schema_version ?? null,
      runtime_root: request.runtime_root, goal_id: request.goal_id, operation_id: request.operation_id,
      expected_provider_revision: request.expected_provider_revision,
      expected_bootstrap_operation_id: request.expected_bootstrap_operation_id ?? null,
    });
  }
  return managementDigest(request);
}
function same(left: unknown, right: unknown): boolean {
  return canonicalAuthorityBytes(left).equals(canonicalAuthorityBytes(right));
}
async function syncDirectory(path: string): Promise<void> {
  if (process.platform === "win32") return;
  const handle = await open(path, "r");
  try { await handle.sync(); } finally { await handle.close(); }
}
async function makeDirectoryDurable(path: string): Promise<void> {
  const missing: string[] = [];
  let cursor = path;
  for (;;) {
    try { await lstat(cursor); break; }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
    missing.push(cursor);
    const parent = dirname(cursor);
    if (parent === cursor) throw new ShadowManagementError("shadow_management_directory_unavailable");
    cursor = parent;
  }
  for (const directory of missing.reverse()) {
    await mkdir(directory, { recursive: true, mode: 0o700 });
    await syncDirectory(dirname(directory));
  }
}
async function writeImmutable(path: string, value: JsonObject): Promise<void> {
  const existing = await readJson(path);
  if (existing) {
    if (!same(existing, value)) throw new ShadowManagementError("management_operation_identity_mismatch");
    return;
  }
  await makeDirectoryDurable(dirname(path));
  await atomicWriteJson(path, value);
}
async function persist(request: ManagementRequest, state: ShadowManagementState): Promise<void> {
  decodeState(state, request.runtime_root, request.goal_id);
  await atomicWriteJson(shadowManagementStatePath(request.runtime_root, request.goal_id), state);
}
async function effect(dependencies: ShadowManagementDependencies, phase: string): Promise<void> {
  await dependencies.afterEffect?.(phase);
}
async function retainTerminal(request: ManagementRequest, state: ShadowManagementState | null): Promise<void> {
  if (!state || !state.result) return;
  const original = { ...request, operation_id: String(state.operation.operation_id) };
  await writeImmutable(resultPath(original), {
    request_digest: state.operation.request_digest,
    manifest_digest: state.operation.manifest_digest,
    result: state.result,
  });
}
async function replay(request: ManagementRequest, state: ShadowManagementState | null): Promise<JsonObject | null> {
  const digest = requestDigest(request);
  if (state?.operation.operation_id === request.operation_id) {
    if (state.operation.request_digest !== digest) throw new ShadowManagementError("management_operation_identity_mismatch");
    if (!state.result) return null;
    await validateReplayResult(request, await loadManifest(request, state), state.result, state);
    return { ...state.result, status: "replayed", current_management_status: state.status, current_capture_lineage_id: state.binding?.capture_lineage_id ?? null };
  }
  const prior = await readJson(resultPath(request));
  if (!prior) return null;
  if (prior.request_digest !== digest) throw new ShadowManagementError("management_operation_identity_mismatch");
  if (!isAuthorityJsonObject(prior.result)) throw new ShadowManagementError("shadow_management_state_invalid");
  const manifest = await loadManifest(request, { operation: { manifest_digest: prior.manifest_digest } });
  await validateReplayResult(request, manifest, prior.result, state);
  return { ...prior.result, status: "replayed", current_management_status: state?.status ?? "missing", current_capture_lineage_id: state?.binding?.capture_lineage_id ?? null };
}
async function loadManifest(request: ManagementRequest, state: { operation: JsonObject }): Promise<JsonObject> {
  const manifest = await readJson(manifestPath(request));
  if (!manifest || managementDigest(manifest) !== state.operation.manifest_digest
      || manifest.schema_version !== SHADOW_MANAGEMENT_MANIFEST_SCHEMA
      || manifest.kind !== ("expected_provider_revision" in request ? "rollback" : "bootstrap")
      || manifest.goal_id !== request.goal_id || manifest.operation_id !== request.operation_id
      || !validSourceRootDigest(manifest.source_root_digest, request.runtime_root)
      || manifest.request_digest !== requestDigest(request)
      || !isAuthorityJsonObject(manifest.request)
      || manifest.request.runtime_root !== request.runtime_root || manifest.request.goal_id !== request.goal_id
      || manifest.request.operation_id !== request.operation_id
      || requestDigest(manifest.request as ManagementRequest) !== manifest.request_digest) {
    throw new ShadowManagementError("shadow_management_manifest_invalid");
  }
  return manifest;
}

/** A cached result is historical evidence, not an independent authority. */
async function validateReplayResult(request: ManagementRequest, manifest: JsonObject, result: JsonObject, state: ShadowManagementState | null): Promise<void> {
  const invalid = () => { throw new ShadowManagementError("shadow_management_result_invalid"); };
  if (result.operation_id !== request.operation_id || result.capture_lineage_id !== manifest.capture_lineage_id
      || result.primary_writeback_preserved !== true || result.decision_read_from_shadow !== false) return invalid();
  if (manifest.kind === "rollback") {
    const candidate = manifest.candidate;
    if (candidate !== null && !isAuthorityJsonObject(candidate)) return invalid();
    const store = provider(request);
    if (!["applied", "recovered"].includes(String(result.status))
        || result.archive_id !== store.authorityArchiveId(request.operation_id)
        || result.archived_provider_revision !== (candidate?.provider_revision ?? null)
        || result.archived_cursor !== (candidate?.cursor ?? null)
        || result.candidate_archive_path !== (candidate ? store.authorityArchivePath(request.operation_id) : null)
        || result.outbox_archive_path !== (manifest.outbox ? archiveOutboxPath(request) : null)
        || result.active_shadow_removed !== true || result.archive_retained !== true
        || result.capture_status !== "bootstrap_required") return invalid();
    return;
  }
  if (result.status === "aborted") {
    if (result.reason_code !== "bootstrap_aborted" || !text(result.rollback_operation_id)) return invalid();
    const locator = { ...request, operation_id: result.rollback_operation_id };
    const raw = await readJson(manifestPath(locator));
    if (!raw || !isAuthorityJsonObject(raw.request)) return invalid();
    const rollbackRequest = requestOf(raw.request);
    if (rollbackRequest.goal_id !== request.goal_id || rollbackRequest.runtime_root !== request.runtime_root
        || rollbackRequest.operation_id !== result.rollback_operation_id
        || rollbackRequest.expected_bootstrap_operation_id !== request.operation_id) return invalid();
    const terminal = state?.operation.operation_id === result.rollback_operation_id
      ? { ...state.operation, result: state.result } : await readJson(resultPath(locator));
    if (!terminal || terminal.request_digest !== requestDigest(rollbackRequest)
        || !isAuthorityJsonObject(terminal.result)) return invalid();
    const rollback = await loadManifest(rollbackRequest, { operation: { manifest_digest: terminal.manifest_digest } });
    if (!same(rollback.aborted_bootstrap, manifest)) return invalid();
    await validateReplayResult(rollbackRequest, rollback, terminal.result, state);
    return;
  }
  if (!["applied", "recovered"].includes(String(result.status))
      || result.capture_profile !== SHADOW_CAPTURE_PROFILE || result.source_root_digest !== manifest.source_root_digest
      || result.bootstrap_operation_id !== request.operation_id || result.cursor !== "1"
      || result.provider_revision !== result.bootstrap_provider_revision
      || !/^file:1:[0-9a-f]{24}$/.test(String(result.bootstrap_provider_revision))
      || !/^file:[0-9a-f]{32}$/.test(String(result.store_identity))
      || result.bootstrap_receipts_empty !== true || result.mode_declaration !== "legacy_canonical_shadow") return invalid();
  // The active binding is an exact anchor when this bootstrap is still current.
  // A historical manifest predates provider identity/revision assignment; its
  // cached revision shape alone does not establish live candidate authority.
  if (state?.binding?.bootstrap_operation_id === request.operation_id
      && Object.entries(state.binding).some(([key, value]) => result[key] !== value)) return invalid();
}
function initialState(request: ManagementRequest, kind: "bootstrap" | "rollback", manifest: JsonObject, prior: ShadowManagementState | null): ShadowManagementState {
  return {
    schema_version: SHADOW_MANAGEMENT_STATE_SCHEMA, goal_id: request.goal_id,
    source_root_digest: kind === "rollback" && prior
      ? prior.source_root_digest
      : shadowSourceRootDigest(request.runtime_root),
    status: kind === "bootstrap" ? "bootstrapping" : "rolling_back",
    binding: kind === "rollback" ? prior?.binding ?? null : null,
    operation: { kind, operation_id: request.operation_id, request_digest: requestDigest(request), manifest_digest: managementDigest(manifest), phase: "prepared" },
    previous_operation_id: prior ? String(prior.operation.operation_id) : null, result: null,
  };
}
async function advance(request: ManagementRequest, state: ShadowManagementState, phase: string): Promise<void> {
  state.operation.phase = phase;
  await persist(request, state);
}
function failure(error: unknown): JsonObject {
  const code = error instanceof ShadowManagementError ? error.code
    : text((error as { code?: unknown })?.code) ? String((error as { code: string }).code) : "shadow_management_unavailable";
  return { status: "failed", reason_code: code, reconciliation_required: true, primary_writeback_preserved: true, decision_read_from_shadow: false };
}

/** Inventory raw bytes, including malformed cursors, without interpreting delivery. */
async function inventory(path: string): Promise<JsonObject | null> {
  try { const stat = await lstat(path); if (!stat.isDirectory()) throw new ShadowManagementError("shadow_outbox_layout_invalid"); }
  catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") return null; throw error; }
  const entries: JsonObject[] = [];
  async function visit(directory: string, prefix: string): Promise<void> {
    for (const name of (await readdir(directory)).sort()) {
      const relative = prefix ? `${prefix}/${name}` : name;
      const full = join(directory, name);
      const stat = await lstat(full);
      if (stat.isDirectory()) {
        entries.push({ path: relative, kind: "directory" });
        await visit(full, relative);
      } else if (stat.isFile()) {
        const bytes = await readFile(full);
        entries.push({ path: relative, kind: "file", size: bytes.length, sha256: `sha256:${createHash("sha256").update(bytes).digest("hex")}` });
      } else throw new ShadowManagementError("shadow_outbox_layout_invalid");
    }
  }
  await visit(path, "");
  return { entries, digest: managementDigest(entries) };
}
async function fileDigest(path: string): Promise<string | null> {
  try { return `sha256:${createHash("sha256").update(await readFile(path)).digest("hex")}`; }
  catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") return null; throw error; }
}
async function candidateSnapshot(store: FileAuthorityStore): Promise<JsonObject | null> {
  const loaded = await store.loadAuthority();
  if (loaded.status === "missing") return null;
  if (loaded.status !== "loaded") throw new ShadowManagementError(loaded.reason_code);
  const identity = await store.storeIdentity();
  if (identity.status !== "available") throw new ShadowManagementError(identity.reason_code);
  const first = await store.scanCommitted(null, 1);
  if (first.status !== "page" || first.transactions.length !== 1) throw new ShadowManagementError("candidate_history_unavailable");
  return {
    provider_revision: loaded.provider_revision, cursor: loaded.cursor, sha256: await fileDigest(store.path),
    store_identity: identity.store_identity, capture_lineage_id: loaded.head.capture_lineage_id ?? null,
    source_root_digest: loaded.head.source_root_digest ?? null, capture_profile: loaded.head.capture_profile ?? null,
    bootstrap_operation_id: first.transactions[0].operation_id,
    bootstrap_provider_revision: first.transactions[0].provider_revision,
  };
}

export async function bootstrapManagedShadow(value: unknown, dependencies: ShadowManagementDependencies): Promise<JsonObject> {
  try {
    const request = requestOf(value);
    requireAuthorityStoreId(request.source_version, "source_version");
    canonicalAuthorityObject(request.projection, "projection");
    if (!isAuthorityJsonObject(request.source_snapshot) || !dependencies.verifySourceSnapshot) throw new ShadowManagementError("source_snapshot_required");
    return await withShadowMaintenanceLock(request.runtime_root, request.goal_id, async () => {
      let state = await readShadowManagementState(request.runtime_root, request.goal_id);
      const priorResult = await replay(request, state);
      if (priorResult) return priorResult;
      const recovering = state?.operation.operation_id === request.operation_id;
      if (state && state.status !== "inactive" && !(recovering && state.status === "bootstrapping")) throw new ShadowManagementError("shadow_management_in_progress");
      return await dependencies.withPrimaryLocks(async () => {
        await dependencies.verifySourceSnapshot!();
        let manifest: JsonObject;
        if (recovering) {
          manifest = await loadManifest(request, state!);
        } else {
          if (await candidateSnapshot(provider(request)) !== null) throw new ShadowManagementError("legacy_shadow_read_only");
          const oldOutbox = await inventory(outboxPath(request));
          if (oldOutbox && (oldOutbox.entries as JsonObject[]).length !== 0) throw new ShadowManagementError("legacy_shadow_read_only");
          await retainTerminal(request, state);
          // An orphan manifest before intent publication is reusable only for this exact request.
          const orphan = await readJson(manifestPath(request));
          if (orphan && orphan.request_digest !== requestDigest(request)) throw new ShadowManagementError("management_operation_identity_mismatch");
          const lineage = orphan?.capture_lineage_id ?? randomUUID();
          manifest = {
            schema_version: SHADOW_MANAGEMENT_MANIFEST_SCHEMA, kind: "bootstrap", goal_id: request.goal_id,
            operation_id: request.operation_id, source_root_digest: shadowSourceRootDigest(request.runtime_root),
            request_digest: requestDigest(request), request,
            predecessor_operation_id: state?.operation.operation_id ?? null, capture_lineage_id: lineage,
          };
          await writeImmutable(manifestPath(request), manifest);
          state = initialState(request, "bootstrap", manifest, state);
          await persist(request, state);
          await effect(dependencies, "bootstrap_prepared");
        }
        const lineage = String(manifest.capture_lineage_id);
        const sourceRootDigest = String(manifest.source_root_digest);
        const projection: JsonObject = { ...canonicalAuthorityObject(request.projection, "projection"), capture_profile: SHADOW_CAPTURE_PROFILE, capture_lineage_id: lineage, source_root_digest: sourceRootDigest };
        const event: JsonObject = {
          schema_version: "loopx_coordination_runtime_shadow_bootstrap_event_v0", operation_id: request.operation_id,
          source_version: request.source_version, source_projection_sha256: canonicalAuthoritySha256(projection),
          mode_declaration: "legacy_canonical_shadow",
        };
        const store = provider(request, false);
        await makeDirectoryDurable(store.directory);
        let loaded = await store.loadAuthority();
        if (loaded.status === "missing") {
          const committed = await store.commitAuthority({ expected_provider_revision: null, operation_id: request.operation_id, events: [event], next_projection: projection, receipts: [] });
          if (committed.status !== "applied" && committed.status !== "ambiguous" && committed.status !== "conflict") throw new ShadowManagementError(committed.reason_code);
          loaded = await store.loadAuthority();
        }
        if (loaded.status !== "loaded" || loaded.cursor !== "1" || !same(loaded.head, projection)) throw new ShadowManagementError("bootstrap_readback_mismatch");
        const scan = await store.scanCommitted(null, 2);
        if (scan.status !== "page" || scan.transactions.length !== 1 || scan.transactions[0].operation_id !== request.operation_id
            || !same(scan.transactions[0].events, [event]) || scan.transactions[0].receipts.length !== 0) throw new ShadowManagementError("bootstrap_readback_mismatch");
        const identity = await store.storeIdentity();
        if (identity.status !== "available") throw new ShadowManagementError(identity.reason_code);
        const binding: ShadowCaptureBinding = {
          capture_profile: SHADOW_CAPTURE_PROFILE, capture_lineage_id: lineage,
          source_root_digest: sourceRootDigest, store_identity: identity.store_identity,
          bootstrap_operation_id: request.operation_id, bootstrap_provider_revision: loaded.provider_revision,
        };
        await effect(dependencies, "bootstrap_candidate_committed");
        await advance(request, state!, "candidate_committed");
        await writeImmutable(join(outboxPath(request), "manifest.json"), { schema_version: SHADOW_OUTBOX_MANIFEST_SCHEMA, goal_id: request.goal_id, ...binding });
        await effect(dependencies, "bootstrap_outbox_ready");
        await advance(request, state!, "outbox_ready");
        await dependencies.verifySourceSnapshot!();
        const result: JsonObject = {
          status: recovering ? "recovered" : "applied", operation_id: request.operation_id,
          ...binding, provider_revision: loaded.provider_revision, cursor: loaded.cursor,
          bootstrap_receipts_empty: true, mode_declaration: "legacy_canonical_shadow",
          primary_writeback_preserved: true, decision_read_from_shadow: false,
        };
        state!.status = "active"; state!.binding = binding; state!.result = result; state!.operation.phase = "complete";
        await persist(request, state!);
        await effect(dependencies, "bootstrap_complete");
        return result;
      });
    });
  } catch (error) { return failure(error); }
}

export async function rollbackManagedShadow(value: unknown, dependencies: ShadowManagementDependencies): Promise<JsonObject> {
  try {
    const request = requestOf(value);
    if (request.expected_provider_revision !== null && !text(request.expected_provider_revision)) throw new ShadowManagementError("invalid_shadow_rollback_request");
    if (request.expected_bootstrap_operation_id !== undefined && request.expected_bootstrap_operation_id !== null && !text(request.expected_bootstrap_operation_id)) throw new ShadowManagementError("invalid_shadow_rollback_request");
    if (text(request.expected_provider_revision) === text(request.expected_bootstrap_operation_id)) throw new ShadowManagementError("invalid_shadow_rollback_request");
    return await withShadowMaintenanceLock(request.runtime_root, request.goal_id, async () => {
      let state = await readShadowManagementState(request.runtime_root, request.goal_id);
      const priorResult = await replay(request, state);
      if (priorResult) return priorResult;
      const recovering = state?.status === "rolling_back" && state.operation.operation_id === request.operation_id;
      const aborting = state?.status === "bootstrapping" && request.expected_bootstrap_operation_id === state.operation.operation_id;
      if (text(request.expected_bootstrap_operation_id) && !recovering && !aborting) throw new ShadowManagementError("bootstrap_operation_not_pending");
      if (state?.status === "rolling_back" && !recovering) throw new ShadowManagementError("shadow_management_in_progress");
      if (state?.status === "bootstrapping" && !aborting) throw new ShadowManagementError("bootstrap_operation_identity_required");
      return await dependencies.withPrimaryLocks(async () => {
        const store = provider(request, true, dependencies);
        let manifest: JsonObject;
        if (recovering) manifest = await loadManifest(request, state!);
        else {
          if (!state || state.status === "inactive") throw new ShadowManagementError("shadow_rollback_source_missing");
          const candidate = await candidateSnapshot(store);
          if (!aborting && (candidate?.provider_revision ?? null) !== request.expected_provider_revision) throw new ShadowManagementError("provider_revision_mismatch");
          if (candidate === null && !aborting) throw new ShadowManagementError("shadow_rollback_source_missing");
          let bootstrapManifest: JsonObject | null = null;
          if (aborting) {
            const original = { ...request, operation_id: String(state.operation.operation_id) };
            bootstrapManifest = await readJson(manifestPath(original));
            if (!bootstrapManifest || managementDigest(bootstrapManifest) !== state.operation.manifest_digest) throw new ShadowManagementError("shadow_management_manifest_invalid");
          }
          if (candidate) {
            const expectedLineage = state.binding?.capture_lineage_id ?? bootstrapManifest?.capture_lineage_id;
            const expectedBootstrap = state.binding?.bootstrap_operation_id ?? bootstrapManifest?.operation_id;
            if (candidate.capture_profile !== SHADOW_CAPTURE_PROFILE || candidate.capture_lineage_id !== expectedLineage
                || candidate.source_root_digest !== state.source_root_digest
                || candidate.bootstrap_operation_id !== expectedBootstrap
                || (state.binding && (candidate.store_identity !== state.binding.store_identity
                  || candidate.bootstrap_provider_revision !== state.binding.bootstrap_provider_revision))) {
              throw new ShadowManagementError("rollback_candidate_identity_mismatch");
            }
          }
          const pending = await inventory(outboxPath(request));
          await retainTerminal(request, state);
          manifest = {
            schema_version: SHADOW_MANAGEMENT_MANIFEST_SCHEMA, kind: "rollback", goal_id: request.goal_id,
            operation_id: request.operation_id, source_root_digest: state.source_root_digest,
            request_digest: requestDigest(request), request,
            predecessor_operation_id: state.operation.operation_id, prior_binding: state.binding,
            candidate, outbox: pending, aborted_bootstrap: bootstrapManifest,
            capture_lineage_id: state.binding?.capture_lineage_id ?? bootstrapManifest?.capture_lineage_id ?? null,
          };
          await writeImmutable(manifestPath(request), manifest);
          state = initialState(request, "rollback", manifest, state);
          await persist(request, state);
          await effect(dependencies, "rollback_prepared");
        }
        const expected = manifest.candidate as JsonObject | null;
        const sourceDigest = await fileDigest(store.path);
        const archiveDigest = await fileDigest(store.authorityArchivePath(request.operation_id));
        if (expected === null) {
          if (sourceDigest !== null || archiveDigest !== null) throw new ShadowManagementError("rollback_candidate_identity_mismatch");
        } else if (sourceDigest === expected.sha256 && archiveDigest === null) {
          const archived = await store.archiveAuthorityDocument(String(expected.provider_revision), request.operation_id);
          if (archived.status !== "applied" && archived.status !== "replayed") throw new ShadowManagementError("rollback_candidate_archive_unavailable");
        } else if (sourceDigest !== null || archiveDigest !== expected.sha256) {
          throw new ShadowManagementError("rollback_candidate_identity_mismatch");
        }
        if (expected !== null) {
          await syncDirectory(store.directory);
          await syncDirectory(dirname(store.authorityArchivePath(request.operation_id)));
        }
        await effect(dependencies, "rollback_candidate_archived");
        await advance(request, state!, "candidate_archived");
        const expectedOutbox = manifest.outbox as JsonObject | null;
        const activeOutbox = await inventory(outboxPath(request));
        const archivedOutbox = await inventory(archiveOutboxPath(request));
        if (expectedOutbox === null) {
          if (activeOutbox !== null || archivedOutbox !== null) throw new ShadowManagementError("rollback_outbox_identity_mismatch");
        } else if (same(activeOutbox, expectedOutbox) && archivedOutbox === null) {
          await rename(outboxPath(request), archiveOutboxPath(request));
          await effect(dependencies, "rollback_outbox_renamed");
          await syncDirectory(dirname(outboxPath(request)));
          await syncDirectory(operationDirectory(request));
        } else if (activeOutbox !== null || !same(archivedOutbox, expectedOutbox)) {
          throw new ShadowManagementError("rollback_outbox_identity_mismatch");
        }
        if (expectedOutbox !== null) {
          await syncDirectory(dirname(outboxPath(request)));
          await syncDirectory(operationDirectory(request));
        }
        await effect(dependencies, "rollback_outbox_archived");
        await advance(request, state!, "outbox_archived");
        if ((expected !== null && await fileDigest(store.authorityArchivePath(request.operation_id)) !== expected.sha256)
            || !same(await inventory(archiveOutboxPath(request)), expectedOutbox)) throw new ShadowManagementError("rollback_archive_readback_mismatch");
        const result: JsonObject = {
          status: recovering ? "recovered" : "applied", operation_id: request.operation_id,
          archive_id: store.authorityArchiveId(request.operation_id),
          archived_provider_revision: expected?.provider_revision ?? null, archived_cursor: expected?.cursor ?? null,
          capture_lineage_id: manifest.capture_lineage_id,
          candidate_archive_path: expected ? store.authorityArchivePath(request.operation_id) : null,
          outbox_archive_path: expectedOutbox ? archiveOutboxPath(request) : null,
          active_shadow_removed: true, archive_retained: true, capture_status: "bootstrap_required",
          primary_writeback_preserved: true, decision_read_from_shadow: false,
        };
        if (isAuthorityJsonObject(manifest.aborted_bootstrap)) {
          const aborted = manifest.aborted_bootstrap;
          await writeImmutable(resultPath({ ...request, operation_id: String(aborted.operation_id) }), {
            request_digest: aborted.request_digest, manifest_digest: managementDigest(aborted),
            result: { status: "aborted", reason_code: "bootstrap_aborted", operation_id: aborted.operation_id,
              rollback_operation_id: request.operation_id, capture_lineage_id: aborted.capture_lineage_id,
              primary_writeback_preserved: true, decision_read_from_shadow: false },
          });
        }
        state!.status = "inactive"; state!.binding = null; state!.result = result; state!.operation.phase = "complete";
        await persist(request, state!);
        await effect(dependencies, "rollback_complete");
        return result;
      });
    });
  } catch (error) { return failure(error); }
}
