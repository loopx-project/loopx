/** Offline backup inspection. Existing owners decide readability, never activation. */
import {createHash} from "node:crypto";
import {lstat, readFile} from "node:fs/promises";
import {join, relative} from "node:path";
import type {JsonObject} from "./effect_program.ts";
import {requireJsonObject} from "./runtime_decode.ts";
import {canonicalAuthoritySha256, AuthorityStoreProtocolError, parseAuthorityCursor} from "./coordination/authority_store_codec.ts";
import {EffectRuntimeRequestError} from "./effect_runtime_errors.ts";
import {verifyConfigurationBackup} from "./configuration_backup.ts";
import {decodeLocalAuthoritySelection, localAuthorityProviderPaths,
  openLocalAuthorityStoreHandle} from "./coordination/local_authority_provider.ts";
import {sqliteAuthorityRuntime} from "./coordination/sqlite_runtime.ts";
import {sqliteAuthorityPath} from "./coordination/sqlite_authority_store.ts";
import {canonicalTodoCollection} from "./coordination/local_authority_read.ts";
import {STATE_RECOVERY_AUDIT_SCHEMA, decodeRecoveryMeasurements, decodeStateRecoveryAudit, isRecoveryDigest, recoveryGoalLabel,
  type RecoveryComponent, type RecoveryComponentState} from "./state_recovery_contract.ts";

const MAX_GOALS = 128;
const MAX_SQLITE_FILES = 128;
const MAX_COMMITS = 20_000;
const hash = (value: string) => createHash("sha256").update(value).digest("hex");
const digest = isRecoveryDigest;
class RecoveryInputError extends Error {
  readonly code: string;
  constructor(code: string) { super(code); this.code = code; }
}
function check(value: unknown, code: string): asserts value {
  if (!value) throw new RecoveryInputError(code);
}
function reject(code: string): JsonObject {
  return {ok: false, error_code: code, execution_authority_granted: false};
}
function manifestCore(value: JsonObject): JsonObject {
  const execution = {...requireJsonObject(value.execution, "backup execution")};
  execution.archive_sha256 = null;
  execution.archive_size_bytes = null;
  delete execution.archive_to_logical_ratio;
  return {...value, execution};
}

function externalEvidence(request: JsonObject): {external: JsonObject; execution: JsonObject} {
  const external = requireJsonObject(request.external_manifest, "external backup manifest");
  check(external.schema_version === "loopx_state_backup_v0", "backup_schema_unsupported");
  check(external.dry_run === false && external.execute_requested === true && external.ok === true,
    "backup_not_committed");
  check(typeof external.backup_id === "string" && external.backup_id.length > 0, "backup_identity_missing");
  const execution = requireJsonObject(external.execution, "backup execution");
  check(digest(request.archive_sha256) && execution.archive_sha256 === request.archive_sha256,
    "archive_digest_mismatch");
  check(Number.isSafeInteger(request.archive_size_bytes) && execution.archive_size_bytes === request.archive_size_bytes,
    "archive_size_mismatch");
  return {external, execution};
}
function inputFailure(error: unknown): JsonObject {
  if (error instanceof RecoveryInputError) return reject(error.code);
  if (error instanceof EffectRuntimeRequestError || error instanceof AuthorityStoreProtocolError) {
    return reject("backup_manifest_invalid");
  }
  throw error;
}

/** Reject a mismatched archive before its untrusted tar structure is decoded. */
export function verifyStateRecoveryArchive(value: unknown): JsonObject {
  try {
    externalEvidence(requireJsonObject(value, "recovery archive verification"));
    return {ok: true, archive_digest_verified: true, execution_authority_granted: false};
  } catch (error) { return inputFailure(error); }
}

/** Host IO supplies observed bytes; input manifests never supply observed facts. */
export function verifyStateRecoveryManifest(value: unknown): JsonObject {
  try {
    const request = requireJsonObject(value, "recovery manifest verification");
    const {external, execution} = externalEvidence(request);
    const embedded = requireJsonObject(request.embedded_manifest, "embedded backup manifest");
    check(embedded.schema_version === external.schema_version,
      "backup_schema_unsupported");
    check(canonicalAuthoritySha256(manifestCore(external)) === canonicalAuthoritySha256(manifestCore(embedded)),
      "manifest_disagreement");
    check(Array.isArray(request.files), "member_inventory_invalid");
    check(Number.isSafeInteger(request.sqlite_member_count) && Number(request.sqlite_member_count) >= 0
      && Number(request.sqlite_member_count) <= MAX_SQLITE_FILES, "sqlite_inventory_capacity");
    const observed = new Map<string, JsonObject>();
    for (const item of request.files) {
      const file = requireJsonObject(item, "observed member");
      check(typeof file.archive_path === "string" && !observed.has(file.archive_path)
        && digest(file.sha256) && Number.isSafeInteger(file.size_bytes) && Number(file.size_bytes) >= 0,
      "member_inventory_invalid");
      observed.set(file.archive_path, file);
    }
    const witnesses = execution.file_members;
    if (witnesses !== undefined) {
      check(Array.isArray(witnesses) && witnesses.length === observed.size, "member_witness_mismatch");
      const seen = new Set<string>();
      for (const item of witnesses) {
        const witness = requireJsonObject(item, "member witness");
        const path = String(witness.archive_path);
        check(!seen.has(path), "member_witness_mismatch");
        seen.add(path);
        const file = observed.get(path);
        check(file && file.sha256 === witness.sha256 && file.size_bytes === witness.size_bytes,
          "member_witness_mismatch");
      }
    }
    check(digest(request.external_manifest_sha256), "manifest_digest_invalid");
    return {ok: true, schema_version: STATE_RECOVERY_AUDIT_SCHEMA,
      recovery_id: hash(`${request.archive_sha256}:${request.external_manifest_sha256}`),
      backup: {schema_version: external.schema_version, archive_sha256: request.archive_sha256,
        external_manifest_sha256: request.external_manifest_sha256},
      file_count: observed.size, file_witnesses_verified: witnesses !== undefined,
      manifest_verified: true, capture_profile: "legacy_best_effort", execution_authority_granted: false};
  } catch (error) {
    return inputFailure(error);
  }
}

function component(id: string, owner: string, state: RecoveryComponentState, reason: string,
  next: string, facts: JsonObject = {}, dependencies: string[] = []): RecoveryComponent {
  return {component_id: id, owner_kind: owner, state, reason_code: reason,
    next_action: next, facts, dependency_ids: dependencies};
}
async function regular(path: string): Promise<void> {
  const stat = await lstat(path);
  check(stat.isFile() && !stat.isSymbolicLink(), "owner_source_not_regular");
}

async function inspectGoal(root: string, goalId: string, standaloneSqlite: Set<string>): Promise<RecoveryComponent> {
  const id = `goal:${hash(goalId).slice(0, 20)}`;
  const paths = localAuthorityProviderPaths(root, goalId);
  const goalFacts = recoveryGoalLabel(goalId) === null ? {} : {goal_id: goalId};
  try {
    let selection: ReturnType<typeof decodeLocalAuthoritySelection> | null = null;
    try {
      await regular(paths.marker);
      selection = decodeLocalAuthoritySelection(JSON.parse(await readFile(paths.marker, "utf8")), goalId);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
    if (selection?.provider === "postgresql") return component(id, "coordination_authority", "unknown",
      "offline_provider_unsupported", "use_provider_recovery_owner", goalFacts, ["configuration"]);
    if (selection?.provider === "sqlite") check(standaloneSqlite.has(sqliteAuthorityPath(paths.sqlite, goalId)),
      "sqlite_requires_owner_recovery");
    // The existing owner retains legacy File reads and rejects missing selected
    // storage or a lost SQLite selector. existingOnly forbids initializing either.
    const handle = await openLocalAuthorityStoreHandle(root, goalId, {}, {existingOnly: true});
    const loaded = await handle.store.loadAuthority();
    check(loaded.status === "loaded", "authority_head_unavailable");
    check(parseAuthorityCursor(loaded.cursor) <= BigInt(MAX_COMMITS), "authority_history_capacity");
    // Byte-valid storage alone is not a readable Goal work model. Reuse the
    // same full-record, lease and acceptance decoder as canonical Todo reads.
    const collection = canonicalTodoCollection(loaded.head, goalId, true);
    let cursor: string | null = null;
    let count = 0;
    let final: JsonObject | null = null;
    let finalRevision: string | null = null;
    while (true) {
      const page = await handle.store.scanCommitted(cursor, 200);
      check(page.status === "page", "authority_history_unavailable");
      count += page.transactions.length;
      check(count <= MAX_COMMITS, "authority_history_capacity");
      for (const row of page.transactions) { final = row.projection; finalRevision = row.provider_revision; }
      if (!page.has_more) break;
      check(page.next_cursor !== null && page.next_cursor !== cursor && page.transactions.length > 0,
        "authority_history_cursor_invalid");
      cursor = page.next_cursor;
    }
    check(String(count) === loaded.cursor && final !== null && finalRevision === loaded.provider_revision
      && canonicalAuthoritySha256(final) === canonicalAuthoritySha256(loaded.head), "authority_history_head_mismatch");
    const readback = await handle.store.loadAuthority();
    check(readback.status === "loaded" && readback.provider_revision === loaded.provider_revision,
      "authority_changed_during_audit");
    return component(id, "coordination_authority", "readable", "historical_head_and_history_readable",
      "review_goal_identity_before_adoption", {...goalFacts, provider: handle.provider,
        head_digest: canonicalAuthoritySha256(loaded.head), history_commits: count,
        todo_count: collection.projection.todo_ids.length,
        historical_lease_count: collection.leaseIndex?.lease_todo_ids.length ?? 0}, ["configuration"]);
  } catch (error) {
    // Inspection profile overflow aborts publication; it is not evidence that
    // this owner's captured state is merely unknown or unreadable.
    if (error instanceof RecoveryInputError && error.code === "authority_history_capacity") throw error;
    return component(id, "coordination_authority", "unknown",
      error instanceof RecoveryInputError ? error.code : "authority_source_unavailable",
      "inspect_captured_authority_with_owner", goalFacts, ["configuration"]);
  }
}

/** Reads only a host-validated inert directory; never registers or repairs a store. */
async function auditOwners(value: unknown): Promise<JsonObject> {
  const started = performance.now();
  const request = requireJsonObject(value, "recovery owner audit");
  const measurements = decodeRecoveryMeasurements({
    ...requireJsonObject(request.measurements, "host recovery measurements"), owner_read_ms: 0});
  const basis = requireJsonObject(request.verified, "verified recovery basis");
  check(basis.ok === true && basis.manifest_verified === true && basis.schema_version === STATE_RECOVERY_AUDIT_SCHEMA,
    "recovery_basis_invalid");
  check(typeof request.data_root === "string" && Array.isArray(request.sqlite_members), "recovery_owner_request_invalid");
  const dataRoot = request.data_root;
  check(request.sqlite_members.length <= MAX_SQLITE_FILES, "sqlite_inventory_capacity");
  const components: RecoveryComponent[] = [];
  let configuration: JsonObject | null = null;
  try {
    const path = join(dataRoot, "configuration-backup.json");
    await regular(path);
    const candidate = requireJsonObject(JSON.parse(await readFile(path, "utf8")), "captured configuration");
    const verified = verifyConfigurationBackup(candidate);
    configuration = candidate;
    components.push(component("configuration", "configuration", "readable", "configuration_digest_verified",
      "retain_isolated_configuration", {goal_count: verified.goal_count}));
  } catch {
    components.push(component("configuration", "configuration", "unknown", "configuration_unavailable",
      "inspect_configuration_with_owner"));
  }
  check(Array.isArray(request.standalone_sqlite_members), "recovery_owner_request_invalid");
  const standaloneSqlite = new Set(request.standalone_sqlite_members.map(raw => join(dataRoot, String(raw))));
  const ownerReadableSqlite = new Set<string>();
  for (const raw of request.sqlite_members) {
    const path = String(raw);
    // The IO adapter has already verified all names and refused symlink ancestors.
    check(!path.startsWith("/") && !path.split("/").includes(".."), "sqlite_member_invalid");
    const id = `sqlite:${hash(path).slice(0, 20)}`;
    try {
      const fullPath = join(dataRoot, path);
      check(relative(dataRoot, fullPath) === path && standaloneSqlite.has(fullPath), "sqlite_requires_owner_recovery");
      await regular(fullPath);
      const {driver} = sqliteAuthorityRuntime();
      const limits = new driver.DatabaseSync(":memory:");
      try {
        // SQLite's heap limit is process-wide; this module is invoked only in
        // the disposable verifier, never in the shared Effect daemon.
        limits.exec("PRAGMA hard_heap_limit=67108864");
        const actual = Number(limits.prepare("PRAGMA hard_heap_limit").get()?.hard_heap_limit);
        check(actual > 0 && actual <= 64 * 1024 * 1024, "sqlite_memory_limit_unavailable");
      } finally { limits.close(); }
      const database = new driver.DatabaseSync(fullPath, {readOnly: true});
      try {
        database.exec("PRAGMA query_only=ON; PRAGMA trusted_schema=OFF");
        const tables = database.prepare("PRAGMA table_list").all();
        const physicalTablesOnly = tables.every(row => row.type === "table");
        check(physicalTablesOnly, "sqlite_schema_requires_owner_recovery");
        const rows = database.prepare("PRAGMA integrity_check").all();
        check(rows.length === 1 && rows[0]?.integrity_check === "ok", "sqlite_integrity_failed");
        // Live store readers assume their own physical tables. Never evaluate
        // a restored view or virtual table as an authority table.
        ownerReadableSqlite.add(fullPath);
      } finally { database.close(); }
      components.push(component(id, "sqlite_storage", "historical_only", "sqlite_bytes_verified",
        "retain_historical_database"));
    } catch (error) {
      components.push(component(id, "sqlite_storage", "unknown",
        error instanceof RecoveryInputError ? error.code : "sqlite_verification_failed",
        "inspect_database_with_owner"));
    }
  }
  if (configuration !== null) {
    const goals = (configuration.data as JsonObject).goals as JsonObject[];
    check(goals.length <= MAX_GOALS, "goal_inventory_capacity");
    for (const goal of goals) components.push(await inspectGoal(join(dataRoot, "runtime-root"), String(goal.goal_id), ownerReadableSqlite));
  }
  // A v0 file inventory does not identify every session/effect/extension owner.
  // Retained bytes must not masquerade as a successful owner-local read.
  components.push(component("remaining_state", "recovery", "unknown", "legacy_owner_inventory_incomplete",
    "classify_remaining_owner_state"));
  const audit = {...basis, status: "held", written: true, observed_at: new Date().toISOString(),
    measurements: {...measurements, owner_read_ms: Math.floor(performance.now() - started)},
    workspace: {isolation: "inert", registered: false}, components,
    findings: [{reason_code: "legacy_manifest_incomplete", owner_kind: "backup_capture",
      next_action: "review_capture_consistency"}],
    summary: {readable: components.filter(row => row.state === "readable").length,
      historical_only: components.filter(row => row.state === "historical_only").length,
      unknown: components.filter(row => row.state === "unknown").length},
    activation_eligible: false, execution_authority_granted: false};
  return decodeStateRecoveryAudit({...audit, audit_sha256: canonicalAuthoritySha256(audit)});
}

export async function auditStateRecoveryOwners(value: unknown): Promise<JsonObject> {
  try { return await auditOwners(value); }
  catch (error) { return inputFailure(error); }
}

export function verifySavedStateRecoveryAudit(value: unknown): JsonObject {
  try {
    const audit = decodeStateRecoveryAudit(value);
    const {audit_sha256, ...body} = audit;
    check(canonicalAuthoritySha256(body) === audit_sha256, "recovery_audit_digest_mismatch");
    const backup = requireJsonObject(audit.backup, "recovery backup basis");
    check(audit.recovery_id === hash(`${backup.archive_sha256}:${backup.external_manifest_sha256}`),
      "recovery_audit_identity_mismatch");
    return audit;
  } catch (error) {
    if (error instanceof RecoveryInputError) return reject(error.code);
    if (error instanceof EffectRuntimeRequestError || error instanceof AuthorityStoreProtocolError) return reject("recovery_audit_invalid");
    throw error;
  }
}
