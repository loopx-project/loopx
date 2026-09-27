import { createHash } from "node:crypto";
import { readFile, stat } from "node:fs/promises";
import { existsSync } from "node:fs";
import { isAbsolute, join } from "node:path";
import { pathToFileURL } from "node:url";
import { parseArgs } from "node:util";
import { durableWriteJson, withFileMutationLock } from "../effect_runtime_io.ts";
import {
  authorityStoreSourceAuthority,
  type AuthorityStore,
  type AuthorityStoreProviderKind,
  type AuthorityStoreSourceAuthority,
} from "./authority_store.ts";
import { hasExactAuthorityKeys, isAuthorityJsonObject, requireAuthorityStoreId } from "./authority_store_codec.ts";
import { FileAuthorityStore } from "./file_authority_store.ts";
import { SqliteAuthorityStore, sqliteAuthorityPath } from "./sqlite_authority_store.ts";
import { loadLegacyCoordinationWriterFence } from "./legacy_writer_fence.ts";
import { shadowMaintenanceLockPath } from "./shadow_management.ts";

const SCHEMA = "loopx_local_authority_provider_v0";
export const DEFAULT_LOCAL_AUTHORITY_PROVIDER = "file" as const satisfies AuthorityStoreProviderKind;
export type LocalAuthorityProviderKind = "file" | "sqlite" | "postgresql";
export type LocalAuthoritySource = Extract<AuthorityStoreSourceAuthority,
  "file_v0" | "sqlite_v0" | "postgresql_v0">;
type ProviderOpenReason =
  | "local_authority_selector_unavailable"
  | "local_authority_selector_invalid"
  | "local_authority_selector_missing"
  | "local_authority_provider_unavailable"
  | "local_authority_provider_missing"
  | "local_authority_provider_open_failed"
  | "local_authority_provider_identity_mismatch";

/**
 * The selector contains only public binding facts. Credentials and database
 * clients stay in the service-owned factory supplied by the caller.
 */
export interface LocalPostgreSqlAuthoritySelection {
  schema_version: typeof SCHEMA;
  provider: "postgresql";
  goal_id: string;
  tenant_id: string;
  store_identity: string;
}

export type LocalPostgreSqlAuthorityFactory = (
  selection: LocalPostgreSqlAuthoritySelection,
) => Promise<AuthorityStore> | AuthorityStore;

export interface LocalAuthorityProviderDependencies {
  /** Existing injected runtime store seam; production uses the configured provider. */
  createStore?: (directory: string, goalId: string) => AuthorityStore;
  /** Service-owned hook for the medium-term PostgreSQL profile. */
  openPostgresqlStore?: LocalPostgreSqlAuthorityFactory;
}

export interface LocalAuthorityStoreHandle {
  store: AuthorityStore;
  provider: LocalAuthorityProviderKind;
  sourceAuthority: LocalAuthoritySource;
}

/** Null source means selection could not be validated, never a file fallback. */
export class LocalAuthorityProviderOpenError extends Error {
  readonly sourceAuthority: LocalAuthoritySource | null;
  readonly reasonCode: ProviderOpenReason;
  readonly causeReasonCode: string | undefined;

  constructor(source: LocalAuthoritySource | null, code: ProviderOpenReason, reason: string, causeCode?: string) {
    super(reason);
    this.name = "LocalAuthorityProviderOpenError";
    this.sourceAuthority = source;
    this.reasonCode = code;
    this.causeReasonCode = causeCode;
  }
}

/** Shared runtime projection; unrelated request/domain failures keep their contracts. */
export function localAuthorityOpenFailure(error: unknown): Record<string, unknown> {
  if (!(error instanceof LocalAuthorityProviderOpenError)) return {};
  return {source_authority: error.sourceAuthority, reason_code: error.reasonCode, reason: error.message,
    ...(error.causeReasonCode ? {provider_reason_code: error.causeReasonCode} : {}),
    decision_read_from_provider: false, legacy_fallback_used: false};
}
export function localAuthorityProviderPaths(root: string, goalId: string) {
  if (!isAbsolute(root)) throw new Error("runtime root must be absolute");
  requireAuthorityStoreId(goalId, "goal id");
  return {marker: join(root, "authority", `provider-${createHash("sha256").update(goalId).digest("hex")}.json`),
    sqlite: join(root, "authority", "sqlite-v0"), file: join(root, "authority", "file-v0")};
}

function sourceFor(provider: LocalAuthorityProviderKind): LocalAuthoritySource {
  return `${provider}_v0` as LocalAuthoritySource;
}

function selectorError(reason: string): LocalAuthorityProviderOpenError {
  return new LocalAuthorityProviderOpenError(null, "local_authority_selector_invalid", reason);
}

function decodePostgreSqlSelection(value: Record<string, unknown>, goalId: string): LocalPostgreSqlAuthoritySelection {
  if (!hasExactAuthorityKeys(value, ["schema_version", "provider", "goal_id", "tenant_id", "store_identity"]) ||
      value.provider !== "postgresql" || typeof value.tenant_id !== "string" ||
      value.tenant_id.trim() !== value.tenant_id || value.tenant_id.length === 0 ||
      typeof value.store_identity !== "string" ||
      !/^postgresql:[0-9a-f]{32}$/.test(value.store_identity)) {
    throw selectorError("Invalid PostgreSQL authority provider selector");
  }
  return {
    schema_version: SCHEMA,
    provider: "postgresql",
    goal_id: goalId,
    tenant_id: requireAuthorityStoreId(value.tenant_id, "PostgreSQL tenant id"),
    store_identity: value.store_identity,
  };
}

export function decodeLocalAuthoritySelection(config: unknown, goalId: string):
  LocalPostgreSqlAuthoritySelection | {schema_version: typeof SCHEMA; provider: "file" | "sqlite"; goal_id: string; store_identity: string} {
  if (!isAuthorityJsonObject(config) || config.schema_version !== SCHEMA || config.goal_id !== goalId ||
      (config.provider !== "file" && config.provider !== "sqlite" && config.provider !== "postgresql")) {
    throw selectorError("Invalid local authority provider selector");
  }
  if (config.provider === "postgresql") return decodePostgreSqlSelection(config, goalId);
  if (!hasExactAuthorityKeys(config, ["schema_version", "provider", "goal_id", "store_identity"]) ||
      typeof config.store_identity !== "string" || !new RegExp(`^${config.provider}:[0-9a-f]{32}$`).test(config.store_identity)) {
    throw selectorError("Invalid local authority provider selector");
  }
  return {schema_version: SCHEMA, provider: config.provider, goal_id: goalId, store_identity: config.store_identity};
}

/** Both local providers reject a missing or replaced selected lineage. */
async function openSelectedLocal(root: string, goalId: string, provider: "file" | "sqlite", storeIdentity: string): Promise<AuthorityStore> {
  const source = sourceFor(provider);
  const directory = localAuthorityProviderPaths(root, goalId)[provider];
  const Store = provider === "file" ? FileAuthorityStore : SqliteAuthorityStore;
  try {
    const store = new Store(directory, goalId, {existingOnly: true});
    try { await stat(store.path); }
    catch (error) {
      const missing = (error as NodeJS.ErrnoException).code === "ENOENT";
      throw new LocalAuthorityProviderOpenError(source,
        missing ? "local_authority_provider_missing" : "local_authority_provider_open_failed",
        missing ? `Selected ${provider === "sqlite" ? "SQLite authority database" : "File authority document"} is missing` : `Selected ${provider} authority could not be opened`);
    }
    const identity = await store.storeIdentity();
    if (identity.status !== "available") {
      throw new LocalAuthorityProviderOpenError(source, "local_authority_provider_open_failed", identity.reason, identity.reason_code);
    }
    if (identity.store_identity !== storeIdentity) {
      throw new LocalAuthorityProviderOpenError(source, "local_authority_provider_identity_mismatch", `Selected ${provider} authority identity changed`);
    }
    // The binding is checked again on every operation, including cached reads.
    return new Store(directory, goalId, {existingOnly: true, expectedIdentity: storeIdentity});
  } catch (error) {
    if (error instanceof LocalAuthorityProviderOpenError) throw error;
    throw new LocalAuthorityProviderOpenError(source, "local_authority_provider_open_failed", `Selected ${provider} authority could not be opened`);
  }
}

/** Called under the Goal's canonical writer guard, only after the target's
 * complete history/receipts were independently verified. No implicit fallback. */
export async function publishLocalAuthoritySelection(root: string, goalId: string,
  provider: "file" | "sqlite", storeIdentity: string): Promise<void> {
  if (!new RegExp(`^${provider}:[0-9a-f]{32}$`).test(storeIdentity)) throw selectorError("Invalid local store identity");
  await durableWriteJson(localAuthorityProviderPaths(root, goalId).marker,
    {schema_version: SCHEMA, provider, goal_id: goalId, store_identity: storeIdentity});
}

async function openSelectedPostgreSql(
  selection: LocalPostgreSqlAuthoritySelection,
  dependencies: LocalAuthorityProviderDependencies,
): Promise<AuthorityStore> {
  if (dependencies.openPostgresqlStore === undefined) {
    throw new LocalAuthorityProviderOpenError(
      "postgresql_v0",
      "local_authority_provider_unavailable",
      "PostgreSQL authority requires a service-owned store factory",
    );
  }
  let store: AuthorityStore;
  try {
    store = await dependencies.openPostgresqlStore(selection);
  } catch (error) {
    throw new LocalAuthorityProviderOpenError(
      "postgresql_v0",
      "local_authority_provider_open_failed",
      "Selected PostgreSQL authority could not be opened",
      error instanceof LocalAuthorityProviderOpenError ? error.reasonCode : undefined,
    );
  }
  if (store === null || typeof store !== "object" || authorityStoreSourceAuthority(store) !== "postgresql_v0") {
    throw new LocalAuthorityProviderOpenError(
      "postgresql_v0",
      "local_authority_provider_identity_mismatch",
      "PostgreSQL provider factory returned a different provider",
    );
  }
  let identity;
  try {
    identity = await store.storeIdentity();
  } catch (error) {
    throw new LocalAuthorityProviderOpenError(
      "postgresql_v0",
      "local_authority_provider_open_failed",
      "Selected PostgreSQL authority identity could not be read",
      error instanceof LocalAuthorityProviderOpenError ? error.reasonCode : undefined,
    );
  }
  if (identity.status !== "available") {
    throw new LocalAuthorityProviderOpenError("postgresql_v0", "local_authority_provider_open_failed", identity.reason, identity.reason_code);
  }
  if (identity.store_identity !== selection.store_identity) {
    throw new LocalAuthorityProviderOpenError("postgresql_v0", "local_authority_provider_identity_mismatch", "Selected PostgreSQL authority identity changed");
  }
  return store;
}

export async function openLocalAuthorityStoreHandle(
  root: string,
  goalId: string,
  dependencies: LocalAuthorityProviderDependencies = {},
  options: {existingOnly?: boolean} = {},
): Promise<LocalAuthorityStoreHandle> {
  const p = localAuthorityProviderPaths(root, goalId);
  let raw: string;
  try { raw = await readFile(p.marker, "utf8"); }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
      throw new LocalAuthorityProviderOpenError(null, "local_authority_selector_unavailable", "Local authority provider selector could not be read");
    }
    // Lost selector must never silently redirect an initialized SQLite goal.
    try { await stat(sqliteAuthorityPath(p.sqlite, goalId)); }
    catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") {
        return {store: new FileAuthorityStore(p.file, goalId, options), provider: DEFAULT_LOCAL_AUTHORITY_PROVIDER,
          sourceAuthority: sourceFor(DEFAULT_LOCAL_AUTHORITY_PROVIDER)};
      }
      throw new LocalAuthorityProviderOpenError(null, "local_authority_selector_unavailable", "Local authority selection could not be resolved");
    }
    throw new LocalAuthorityProviderOpenError(null, "local_authority_selector_missing", "SQLite authority exists but its provider selector is missing");
  }
  let config: unknown;
  try { config = JSON.parse(raw); }
  catch { throw new LocalAuthorityProviderOpenError(null, "local_authority_selector_invalid", "Invalid local authority provider selector JSON"); }
  const selection = decodeLocalAuthoritySelection(config, goalId);
  if (selection.provider === "postgresql") {
    const store = await openSelectedPostgreSql(selection, dependencies);
    return {store, provider: "postgresql", sourceAuthority: sourceFor("postgresql")};
  }
  const store = await openSelectedLocal(root, goalId, selection.provider, selection.store_identity);
  return {store, provider: selection.provider, sourceAuthority: sourceFor(selection.provider)};
}

export async function openLocalAuthorityStore(
  root: string,
  goalId: string,
  dependencies: LocalAuthorityProviderDependencies = {},
  options: {existingOnly?: boolean} = {},
): Promise<AuthorityStore> {
  return (await openLocalAuthorityStoreHandle(root, goalId, dependencies, options)).store;
}

/** Administrative opt-in for an empty, unpromoted goal; no implicit migration. */
export async function selectLocalSqliteAuthority(root: string, goalId: string, execute: boolean) {
  const p = localAuthorityProviderPaths(root, goalId);
  return withFileMutationLock(shadowMaintenanceLockPath(root, goalId), async () => {
    if (existsSync(p.marker)) {
      const selected = await openLocalAuthorityStoreHandle(root, goalId);
      if (selected.provider !== "sqlite") throw new Error("Provider selection cannot replace an existing authority; use a reviewed migration");
      return {ok: true, provider: "sqlite", changed: false, executed: execute};
    }
    const fence = await loadLegacyCoordinationWriterFence(root, goalId);
    if (fence.status !== "missing") throw new Error("Provider selection requires an unpromoted goal without a writer fence");
    const file = new FileAuthorityStore(p.file, goalId, {existingOnly: true});
    if ((await file.loadAuthority()).status !== "missing") throw new Error("Provider selection cannot replace existing file authority");
    const store = new SqliteAuthorityStore(p.sqlite, goalId);
    const existing = await store.loadAuthority();
    if (existing.status === "failed" || existing.status === "unavailable") throw new Error(existing.reason);
    if (existing.status !== "missing") throw new Error("Unselected SQLite authority is not empty");
    if (!execute) return {ok: true, provider: "sqlite", changed: false, executed: false};
    const identity = await store.storeIdentity();
    if (identity.status !== "available") throw new Error(JSON.stringify(identity));
    await durableWriteJson(p.marker, {schema_version: SCHEMA, provider: "sqlite", goal_id: goalId,
      store_identity: identity.store_identity});
    return {ok: true, provider: "sqlite", changed: true, executed: true};
  });
}

/** One runtime seam owns provider construction for every local command. */
export async function openRuntimeAuthorityStore(
  root: string,
  goalId: string,
  dependencies: LocalAuthorityProviderDependencies,
  options: {existingOnly?: boolean} = {},
): Promise<AuthorityStore> {
  if (dependencies.createStore !== undefined) {
    return dependencies.createStore(join(root, "authority", "file-v0"), goalId);
  }
  return await openLocalAuthorityStore(root, goalId, dependencies, options);
}

export function requireLocalAuthorityRuntimeRoot(value: unknown): string {
  if (typeof value !== "string" || value.trim() !== value || !isAbsolute(value)) {
    throw new Error("runtime_root must be an absolute path");
  }
  return value;
}

// Narrow administrative entrypoint; business writes continue through loopx todo.
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const {values} = parseArgs({options: {"runtime-root": {type: "string"}, "goal-id": {type: "string"}, execute: {type: "boolean"}}});
    const result = await selectLocalSqliteAuthority(values["runtime-root"] ?? "", values["goal-id"] ?? "", values.execute === true);
    process.stdout.write(`${JSON.stringify(result)}\n`);
  } catch (error) {
    process.stdout.write(`${JSON.stringify({ok: false, error: error instanceof Error ? error.message : "Provider selection failed",
      ...localAuthorityOpenFailure(error)})}\n`);
    process.exitCode = 1;
  }
}
