/**
 * Administrative PostgreSQL schema setup, restricted role assembly, and
 * local-to-pg-seed genesis snapshot sanitizer and exporter.
 *
 * Requirements:
 * 1. Administrator-explicit PG schema and restricted role assembly.
 * 2. Read-only local-to-pg-seed snapshot sanitization (cursor=1 clean seed):
 *    - Strips expired leases
 *    - Strips local absolute paths (/Users/..., /home/..., /private/...)
 *    - Strips .local/ paths and private material references
 *    - Strips Feishu/Lark user/chat/app/union IDs (ou_*, oc_*, cli_*, on_*)
 *    - Fail-closed if any privacy/credential pattern remains.
 */
import { randomBytes, randomUUID } from "node:crypto";
import { constants } from "node:fs";
import { link, lstat, open, unlink } from "node:fs/promises";
import { dirname, join } from "node:path";
import type { JsonObject } from "../effect_program.ts";
import {
  canonicalAuthoritySha256,
  isAuthorityJsonObject,
  requireAuthorityStoreId,
} from "./authority_store_codec.ts";
import {
  POSTGRESQL_RUNTIME_ROLE_PATTERN,
  POSTGRESQL_SCHEMA_VERSION,
  POSTGRESQL_STORE_IDENTITY_PATTERN,
  installPostgreSqlAuthorityStoreSchema,
  PostgreSqlAuthorityStore,
  type PostgreSqlAuthorityDatabase,
} from "./postgresql_authority_store.ts";
import { syncAuthorityDirectory } from "./file_authority_store.ts";
import { openLocalAuthorityStoreHandle } from "./local_authority_provider.ts";

export const PG_GENESIS_SEED_SCHEMA_VERSION = "loopx_pg_genesis_seed_v0";

// Privacy token / ID patterns
const FEISHU_ID_PATTERNS = [
  /(?<![a-zA-Z0-9])(?:ou|on|oc|cli)_[a-zA-Z0-9]{12,}(?![a-zA-Z0-9])/g,
];

const LOCAL_PATH_PATTERNS = [
  /(?:\/(?:Users|home|private\/(?:var|tmp)|var\/folders|tmp|root|Volumes)\/|\b[A-Za-z]:[\\/]|\\\\)[^\s"'<>]+/g,
];

const SECRET_PATTERNS = [
  /\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,255}\b/,
  /\b(?:sk|pk)_(?:live|test)_[0-9a-zA-Z]{24,}\b/,
  /\bsk-[a-zA-Z0-9_-]{20,}\b/,
  /\bAKIA[0-9A-Z]{16}\b/,
  /\bBearer\s+[A-Za-z0-9\-._~+/]+=*\b/i,
  /\b(?:password|passwd|secret|api_key|access_token|private_key)\s*[:=]\s*["']?[^\s"']{8,}["']?/i,
];

export interface GenesisSanitizationReport {
  leases_stripped: number;
  private_materials_stripped: number;
  paths_redacted: number;
  feishu_ids_redacted: number;
  todos_sanitized: number;
}

export class GenesisPrivacyBoundaryViolation extends Error {
  readonly reasonCode = "genesis_privacy_boundary_violation";
  constructor(message: string) {
    super(message);
    this.name = "GenesisPrivacyBoundaryViolation";
  }
}

export function sanitizeText(
  text: string,
  report: GenesisSanitizationReport,
): string {
  let result = text;

  // Redact Feishu IDs
  for (const pattern of FEISHU_ID_PATTERNS) {
    result = result.replace(pattern, () => {
      report.feishu_ids_redacted += 1;
      return "[feishu_id_redacted]";
    });
  }

  // Redact local absolute paths in text
  for (const pattern of LOCAL_PATH_PATTERNS) {
    result = result.replace(pattern, () => {
      report.paths_redacted += 1;
      return "[local_path_redacted]";
    });
  }

  // Remove .local/ references
  if (isLocalReference(result)) {
    result = result.replace(/\.local[\\/][^\s)"']*/g, () => {
      report.paths_redacted += 1;
      return "[local_scope_redacted]";
    });
  }

  return result;
}

export function sanitizeObjectRecursively(
  val: unknown,
  report: GenesisSanitizationReport,
): unknown {
  if (typeof val === "string") {
    return sanitizeText(val, report);
  }
  if (Array.isArray(val)) {
    return val.map((item) => sanitizeObjectRecursively(item, report));
  }
  if (val !== null && typeof val === "object") {
    return Object.fromEntries(Object.entries(val).map(([key, value]) =>
      [key, sanitizeObjectRecursively(value, report)]));
  }
  return val;
}

function isLocalReference(value: string): boolean {
  return /\.local[\\/]/.test(value);
}

function matchesRedaction(pattern: RegExp, value: string): boolean {
  // search starts at zero even for the global patterns used by replace.
  return value.search(pattern) !== -1;
}

export function scanForbiddenPrivacyTokens(value: unknown, path = "projection"): void {
  if (typeof value === "string") {
    for (const pattern of SECRET_PATTERNS) {
      if (pattern.test(value)) {
        throw new GenesisPrivacyBoundaryViolation(
          `Privacy boundary violation at ${path}: matched sensitive credential pattern`,
        );
      }
    }
    if (isLocalReference(value)) {
      throw new GenesisPrivacyBoundaryViolation(
        `Privacy boundary violation at ${path}: contains raw unredacted .local/ reference`,
      );
    }
    for (const pattern of LOCAL_PATH_PATTERNS) {
      if (matchesRedaction(pattern, value)) {
        throw new GenesisPrivacyBoundaryViolation(
          `Privacy boundary violation at ${path}: contains raw unredacted local absolute path`,
        );
      }
    }
    for (const pattern of FEISHU_ID_PATTERNS) {
      if (matchesRedaction(pattern, value)) {
        throw new GenesisPrivacyBoundaryViolation(
          `Privacy boundary violation at ${path}: contains raw unredacted Feishu ID`,
        );
      }
    }
    return;
  }

  if (Array.isArray(value)) {
    for (let i = 0; i < value.length; i++) {
      scanForbiddenPrivacyTokens(value[i], `${path}[${i}]`);
    }
    return;
  }

  if (value !== null && typeof value === "object") {
    for (const [index, [key, field]] of Object.entries(value).entries()) {
      // A field name can itself be a private ID or secret. Never echo it.
      const location = path + "{" + index + "}";
      scanForbiddenPrivacyTokens(key, location);
      scanForbiddenPrivacyTokens(field, location);
    }
  }
}

/**
 * Sanitizes a projection for genesis PG export.
 * Resets metadata, strips expired leases, sanitizes paths and Feishu IDs,
 * strips private materials, and runs a fail-closed boundary scan.
 */
export function sanitizeGenesisSeedProjection(rawProjection: JsonObject): {
  projection: JsonObject;
  report: GenesisSanitizationReport;
} {
  const report: GenesisSanitizationReport = {
    leases_stripped: 0,
    private_materials_stripped: 0,
    paths_redacted: 0,
    feishu_ids_redacted: 0,
    todos_sanitized: 0,
  };

  const projection = structuredClone(rawProjection);

  // 1. Strip expired leases (support both leases array and task_leases object/array)
  const nowMs = Date.now();
  if (Array.isArray(projection.leases)) {
    const originalLeaseCount = projection.leases.length;
    const retainedLeases = (projection.leases as JsonObject[]).filter((lease) => {
      if (!lease || typeof lease !== "object") return false;
      const expiresAt = lease.expires_at;
      if (typeof expiresAt === "number" && expiresAt <= nowMs) return false;
      if (typeof expiresAt === "string") {
        const parsed = Date.parse(expiresAt);
        if (!isNaN(parsed) && parsed <= nowMs) return false;
      }
      return true;
    });
    projection.leases = retainedLeases;
    report.leases_stripped += originalLeaseCount - retainedLeases.length;
  }
  if (projection.task_leases && typeof projection.task_leases === "object") {
    const taskLeases = projection.task_leases as JsonObject;
    if (taskLeases.leases && typeof taskLeases.leases === "object" && !Array.isArray(taskLeases.leases)) {
      const remaining: Record<string, JsonObject> = Object.create(null);
      for (const [k, v] of Object.entries(taskLeases.leases as Record<string, JsonObject>)) {
        if (!v || typeof v !== "object") continue;
        const expiresAt = v.expires_at;
        if (typeof expiresAt === "number" && expiresAt <= nowMs) {
          report.leases_stripped += 1;
          continue;
        }
        if (typeof expiresAt === "string") {
          const parsed = Date.parse(expiresAt);
          if (!isNaN(parsed) && parsed <= nowMs) {
            report.leases_stripped += 1;
            continue;
          }
        }
        remaining[k] = v;
      }
      taskLeases.leases = remaining;
    }
  }

  // 2. Strip private materials (support project_materials and registered_materials)
  for (const matKey of ["project_materials", "registered_materials"] as const) {
    if (Array.isArray(projection[matKey])) {
      const originalMaterialCount = (projection[matKey] as JsonObject[]).length;
      projection[matKey] = (
        projection[matKey] as JsonObject[]
      ).filter((material) => {
        if (!material || typeof material !== "object") return false;
        return !privateMaterial(material);
      });
      report.private_materials_stripped +=
        originalMaterialCount - (projection[matKey] as JsonObject[]).length;
    }
  }

  // The direction registry, scope digest and usage receipts are one lineage.
  // A redacted registry cannot retain source attestations or be re-signed as
  // original evidence in a synthetic seed.
  const registry = projection.authority_registry;
  if (isAuthorityJsonObject(registry) && isAuthorityJsonObject(registry.project_materials)) {
    const materials = Object.values(registry.project_materials);
    const removed = materials.filter(row => isAuthorityJsonObject(row) && privateMaterial(row)).length;
    const sanitizedRegistry = sanitizeObjectRecursively(registry, {...report});
    if (removed > 0 || canonicalAuthoritySha256(sanitizedRegistry) !== canonicalAuthoritySha256(registry)) {
      report.private_materials_stripped += removed;
      delete projection.authority_registry;
      delete projection.goal_direction_scope;
      delete projection.material_usage_receipts;
    }
  }

  // 3. Sanitize Todos (required_write_scopes, text, title, note, evidence)
  if (Array.isArray(projection.todos)) {
    for (const todo of projection.todos as JsonObject[]) {
      if (!todo || typeof todo !== "object") continue;
      report.todos_sanitized += 1;

      // Sanitize required_write_scopes: strip .local/ and make paths relative
      if (Array.isArray(todo.required_write_scopes)) {
        todo.required_write_scopes = (
          todo.required_write_scopes as string[]
        )
          .filter((scope) => typeof scope === "string" && !isLocalReference(scope))
          .map((scope) => {
            // Strip machine-specific user prefix
            for (const pattern of LOCAL_PATH_PATTERNS) {
              if (matchesRedaction(pattern, scope)) {
                report.paths_redacted += 1;
                // Convert e.g. /Users/.../loopx/packages/foo -> packages/foo
                const parts = scope.split("/");
                const loopxIdx = parts.indexOf("loopx");
                if (loopxIdx >= 0 && loopxIdx < parts.length - 1) {
                  return parts.slice(loopxIdx + 1).join("/");
                }
                return parts[parts.length - 1] ?? "<redacted-scope>";
              }
            }
            return scope;
          });
      }
    }
  }

  // 4. Sanitize topic_authority and direction
  if (Array.isArray(projection.topic_authority)) {
    projection.topic_authority = (
      projection.topic_authority as JsonObject[]
    ).filter((topic) => {
      if (!topic || typeof topic !== "object") return false;
      const boundary = String(topic.boundary ?? "");
      return boundary !== "local_private" && boundary !== "private";
    });
  }

  // 5. Recursively sanitize all strings (Feishu IDs, local paths, .local references)
  const sanitized = sanitizeObjectRecursively(projection, report) as JsonObject;

  // 6. Fail-closed scan: any remaining credential or sensitive token triggers immediate failure
  scanForbiddenPrivacyTokens(sanitized, "projection");

  return { projection: sanitized, report };
}

function privateMaterial(material: JsonObject): boolean {
  const boundary = String(material.boundary ?? material.visibility ?? "");
  return ["local_private", "private", "restricted"].includes(boundary) ||
    isLocalReference(String(material.source_url ?? material.url ?? "")) ||
    isLocalReference(String(material.source_path ?? material.path ?? ""));
}

export interface PostgreSqlAdminSetupOptions {
  database?: PostgreSqlAuthorityDatabase;
  databaseUrl?: string;
  storeIdentity?: string;
  runtimeRole?: string;
}

export interface PostgreSqlAdminSetupResult {
  status: "installed" | "failed";
  schema_version: string;
  store_identity: string;
  runtime_role: string | null;
  reason?: string;
}

async function resolveDatabase(options: {
  database?: PostgreSqlAuthorityDatabase;
  targetDatabase?: PostgreSqlAuthorityDatabase;
  databaseUrl?: string;
}): Promise<{ database: PostgreSqlAuthorityDatabase; close: () => Promise<void> } | null> {
  const db = options.database ?? options.targetDatabase;
  if (db) {
    return { database: db, close: async () => {} };
  }
  if (options.databaseUrl) {
    const { Pool } = await import("pg");
    const pool = new Pool({ connectionString: options.databaseUrl });
    const database: PostgreSqlAuthorityDatabase = {
      connect: async () => {
        const client = await pool.connect();
        return {
          query: async (text: string, values?: readonly unknown[]) =>
            await client.query(text, values ? [...values] : undefined),
          release: (error?: Error) => client.release(error),
        };
      },
    };
    return { database, close: async () => await pool.end() };
  }
  return null;
}

/**
 * Administrative explicit schema and restricted role assembly.
 */
export async function setupPostgreSqlAuthoritySchemaAndRole(
  databaseOrOptions: PostgreSqlAuthorityDatabase | PostgreSqlAdminSetupOptions,
  maybeOptions?: PostgreSqlAdminSetupOptions,
): Promise<PostgreSqlAdminSetupResult> {
  const options: PostgreSqlAdminSetupOptions =
    databaseOrOptions && "connect" in databaseOrOptions
      ? { ...maybeOptions, database: databaseOrOptions as PostgreSqlAuthorityDatabase }
      : (databaseOrOptions as PostgreSqlAdminSetupOptions);

  const storeIdentity =
    options.storeIdentity ??
    `postgresql:${randomBytes(16).toString("hex").toLowerCase()}`;

  if (!POSTGRESQL_STORE_IDENTITY_PATTERN.test(storeIdentity)) {
    return {
      status: "failed",
      schema_version: POSTGRESQL_SCHEMA_VERSION,
      store_identity: storeIdentity,
      runtime_role: options.runtimeRole ?? null,
      reason: "PostgreSQL store identity must match postgresql:<32 lowercase hex>",
    };
  }

  const role = options.runtimeRole?.trim();
  if (role !== undefined && !POSTGRESQL_RUNTIME_ROLE_PATTERN.test(role)) {
    return {
      status: "failed",
      schema_version: POSTGRESQL_SCHEMA_VERSION,
      store_identity: storeIdentity,
      runtime_role: null,
      reason: "runtime role must be a valid lowercase SQL identifier",
    };
  }

  const resolved = await resolveDatabase(options);
  if (!resolved) {
    return {
      status: "failed",
      schema_version: POSTGRESQL_SCHEMA_VERSION,
      store_identity: storeIdentity,
      runtime_role: options.runtimeRole ?? null,
      reason: "PostgreSQL database or databaseUrl must be provided for schema setup",
    };
  }

  try {
    await installPostgreSqlAuthorityStoreSchema(resolved.database, storeIdentity, role);

    return {
      status: "installed",
      schema_version: POSTGRESQL_SCHEMA_VERSION,
      store_identity: storeIdentity,
      runtime_role: role ?? null,
    };
  } catch (error) {
    return {
      status: "failed",
      schema_version: POSTGRESQL_SCHEMA_VERSION,
      store_identity: storeIdentity,
      runtime_role: options.runtimeRole ?? null,
      reason: error instanceof Error ? error.message : String(error),
    };
  } finally {
    await resolved.close();
  }
}

export interface LocalToPgSeedExportOptions {
  runtimeRoot: string;
  goalId: string;
  tenantId?: string;
  outputPath?: string;
  localStore?: import("./authority_store.ts").AuthorityStore;
  targetDatabase?: PostgreSqlAuthorityDatabase;
  databaseUrl?: string;
  storeIdentity?: string;
  execute?: boolean;
}

export interface LocalToPgSeedExportResult {
  status: "exported" | "seeded" | "failed";
  schema_version: string;
  goal_id: string;
  tenant_id: string;
  cursor: string;
  provider_revision: string;
  operation_id: string;
  seed_digest?: string;
  sanitization_report?: GenesisSanitizationReport;
  output_path?: string;
  reason?: string;
  seed_payload?: JsonObject;
}

class GenesisSeedOutputConflict extends Error {
  constructor() {
    super("Genesis seed output must be absent or an identical regular file");
  }
}

async function existingSeedMatches(path: string, expected: Buffer): Promise<boolean> {
  let declared;
  try {
    declared = await lstat(path);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return false;
    throw error;
  }
  if (!declared.isFile() || declared.size !== expected.length) throw new GenesisSeedOutputConflict();
  const file = await open(path, constants.O_RDONLY | (constants.O_NONBLOCK ?? 0) | (constants.O_NOFOLLOW ?? 0));
  try {
    const stat = await file.stat();
    if (!stat.isFile() || stat.dev !== declared.dev || stat.ino !== declared.ino) throw new GenesisSeedOutputConflict();
    let offset = 0;
    for await (const part of file.createReadStream({autoClose: false})) {
      if (!part.equals(expected.subarray(offset, offset + part.length))) {
        throw new GenesisSeedOutputConflict();
      }
      offset += part.length;
    }
    if (offset !== expected.length) throw new GenesisSeedOutputConflict();
    return true;
  } finally {
    await file.close();
  }
}

async function writeSeedOutput(path: string, bytes: Buffer): Promise<void> {
  const directory = dirname(path);
  if (!await existingSeedMatches(path, bytes)) {
    // Publish a complete private file without replacing a concurrent writer's
    // output. A concurrent replay may reuse the winner's identical bytes.
    const temporary = join(directory, `.loopx-seed-${randomUUID()}.partial`);
    const file = await open(temporary, "wx", 0o600);
    try {
      try {
        await file.writeFile(bytes);
        await file.sync();
      } finally {
        await file.close();
      }
      try {
        await link(temporary, path);
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "EEXIST" ||
            !await existingSeedMatches(path, bytes)) throw error;
      }
    } finally {
      await unlink(temporary);
    }
  }
  await syncAuthorityDirectory(directory);
}

/**
 * Exports a local authority store into a sanitized genesis seed (cursor=1).
 * When targetDatabase and execute=true are provided, atomically commits into PG.
 */
export async function exportLocalToPgGenesisSeed(
  options: LocalToPgSeedExportOptions,
): Promise<LocalToPgSeedExportResult> {
  const tenantId = options.tenantId ?? "default";
  const operationId = `${options.goalId}-genesis-seed`;
  const base = {
    schema_version: PG_GENESIS_SEED_SCHEMA_VERSION,
    goal_id: options.goalId,
    tenant_id: tenantId,
    cursor: "1",
    provider_revision: "1",
    operation_id: operationId,
  };
  let scopeValid = false;
  const failed = (reason: string): LocalToPgSeedExportResult =>
    ({...base, ...(!scopeValid ? {goal_id: "", tenant_id: "", operation_id: ""} : {}), status: "failed", reason});

  try {
    for (const value of [options.goalId, tenantId, operationId]) {
      requireAuthorityStoreId(value, "genesis scope id");
      scanForbiddenPrivacyTokens(value, "genesis scope");
    }
    scopeValid = true;
    if (options.execute && !options.targetDatabase && !options.databaseUrl) {
      return failed("Executing a genesis seed requires a target PostgreSQL database");
    }
    if (options.storeIdentity !== undefined &&
        !POSTGRESQL_STORE_IDENTITY_PATTERN.test(options.storeIdentity)) {
      return failed("Target PostgreSQL store identity is invalid");
    }

    const store = options.localStore ??
      (await openLocalAuthorityStoreHandle(options.runtimeRoot, options.goalId, {}, {existingOnly: true})).store;
    if (store.providerKind === "postgresql") return failed("Genesis export requires a local File or SQLite source");
    const loaded = await store.loadAuthority();
    if (loaded.status !== "loaded") return failed("Local authority head is not available");

    const {projection, report} = sanitizeGenesisSeedProjection(loaded.head);
    const seedDigest = canonicalAuthoritySha256(projection);
    const seedPayload: JsonObject = {
      ...base, seed_digest: seedDigest, sanitization_report: {...report}, projection,
    };
    const exported = {
      ...base, seed_digest: seedDigest, sanitization_report: report,
      output_path: options.outputPath, seed_payload: seedPayload,
    };
    if (options.outputPath) {
      await writeSeedOutput(options.outputPath, Buffer.from(JSON.stringify(seedPayload, null, 2)));
    }
    // Export never opens the target or installs anything in either store.
    if (!options.execute) return {...exported, status: "exported"};

    const resolved = await resolveDatabase(options);
    if (!resolved) return failed("Target PostgreSQL database is not available for execution");
    try {
      // Discover the provisioned incarnation if the caller did not pin one.
      // Subsequent writes check this identity inside the commit's rotation lock.
      const scope = {tenant_id: tenantId, goal_id: options.goalId};
      const identity = await new PostgreSqlAuthorityStore(resolved.database, {
        ...scope, expected_store_identity: options.storeIdentity,
      }).storeIdentity();
      if (identity.status !== "available") return failed("Target PostgreSQL store identity is unavailable or mismatched");
      const pgStore = new PostgreSqlAuthorityStore(resolved.database, {
        ...scope, expected_store_identity: identity.store_identity,
      });

      const original = async () => {
        const receipt = await pgStore.readReceipt(operationId);
        if (receipt.status !== "found" || receipt.cursor !== "1" || receipt.receipts.length !== 1) return null;
        const record = receipt.receipts[0];
        return record.schema_version === "genesis_seed_receipt_v0" &&
          record.source_goal_id === options.goalId && record.seed_digest === seedDigest ? receipt : null;
      };
      const existing = await pgStore.loadAuthority();
      if (existing.status === "loaded") {
        const receipt = await original();
        return receipt ? {...exported, status: "seeded", provider_revision: receipt.provider_revision}
          : failed("Target PostgreSQL goal already has a different committed authority");
      }
      if (existing.status !== "missing") return failed("Target PostgreSQL authority state is unavailable");

      const committed = await pgStore.commitAuthority({
        operation_id: operationId,
        expected_provider_revision: null,
        events: [],
        receipts: [{
          schema_version: "genesis_seed_receipt_v0",
          source: "local-to-pg-seed",
          source_goal_id: options.goalId,
          seed_digest: seedDigest,
          sanitization_report: {...report},
        }],
        next_projection: projection,
      });
      if (committed.status === "applied") {
        return {...exported, status: "seeded", provider_revision: committed.provider_revision};
      }
      if (committed.status === "ambiguous" || committed.status === "conflict") {
        const receipt = await original();
        if (receipt) return {...exported, status: "seeded", provider_revision: receipt.provider_revision};
        if (committed.status === "ambiguous") {
          return failed("PostgreSQL seed outcome is unknown; retry the same seed to resolve the original receipt");
        }
      }
      return failed(`PostgreSQL genesis seed was not applied (${committed.status})`);
    } finally {
      await resolved.close();
    }
  } catch (error) {
    return failed(error instanceof GenesisPrivacyBoundaryViolation || error instanceof GenesisSeedOutputConflict
      ? error.message : "Genesis seed could not be prepared or written");
  }
}
