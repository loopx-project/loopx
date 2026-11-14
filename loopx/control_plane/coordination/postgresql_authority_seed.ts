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
import { writeFile } from "node:fs/promises";
import type { JsonObject } from "../effect_program.ts";
import {
  canonicalAuthorityObject,
  canonicalAuthoritySha256,
} from "./authority_store_codec.ts";
import {
  POSTGRESQL_SCHEMA_VERSION,
  POSTGRESQL_STORE_IDENTITY_PATTERN,
  installPostgreSqlAuthorityStoreSchema,
  PostgreSqlAuthorityStore,
  type PostgreSqlAuthorityDatabase,
  type PostgreSqlAuthorityConnection,
} from "./postgresql_authority_store.ts";
import { openLocalAuthorityStoreHandle } from "./local_authority_provider.ts";

export const PG_GENESIS_SEED_SCHEMA_VERSION = "loopx_pg_genesis_seed_v0";

// Privacy token / ID patterns
const FEISHU_ID_PATTERNS = [
  /\bou_[0-9a-zA-Z]{20,}\b/g,
  /\bon_[0-9a-zA-Z]{20,}\b/g,
  /\bcli_[0-9a-zA-Z]{16,}\b/g,
  /\boc_[0-9a-zA-Z]{20,}\b/g,
];

const LOCAL_PATH_PATTERNS = [
  /(?:\/Users|\/home|\/private\/(?:var|tmp))\/[a-zA-Z0-9_.-]+(?:\/[^\s"')]*)?/g,
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
    if (pattern.test(result)) {
      result = result.replace(pattern, () => {
        report.feishu_ids_redacted += 1;
        return "[feishu_id_redacted]";
      });
    }
  }

  // Redact local absolute paths in text
  for (const pattern of LOCAL_PATH_PATTERNS) {
    if (pattern.test(result)) {
      result = result.replace(pattern, () => {
        report.paths_redacted += 1;
        return "[local_path_redacted]";
      });
    }
  }

  // Remove .local/ references
  if (result.includes(".local/")) {
    result = result.replace(/(?:^|\s|\/)\.local\/[^\s)"]*/g, () => {
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
    const res: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(val)) {
      res[k] = sanitizeObjectRecursively(v, report);
    }
    return res;
  }
  return val;
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
    if (value.includes(".local/")) {
      throw new GenesisPrivacyBoundaryViolation(
        `Privacy boundary violation at ${path}: contains raw unredacted .local/ reference`,
      );
    }
    for (const pattern of LOCAL_PATH_PATTERNS) {
      if (pattern.test(value)) {
        throw new GenesisPrivacyBoundaryViolation(
          `Privacy boundary violation at ${path}: contains raw unredacted local absolute path`,
        );
      }
    }
    for (const pattern of FEISHU_ID_PATTERNS) {
      if (pattern.test(value)) {
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
    for (const [k, v] of Object.entries(value)) {
      scanForbiddenPrivacyTokens(v, `${path}.${k}`);
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
    projection.leases = (projection.leases as JsonObject[]).filter((lease) => {
      if (!lease || typeof lease !== "object") return false;
      const expiresAt = lease.expires_at;
      if (typeof expiresAt === "number" && expiresAt <= nowMs) return false;
      if (typeof expiresAt === "string") {
        const parsed = Date.parse(expiresAt);
        if (!isNaN(parsed) && parsed <= nowMs) return false;
      }
      return true;
    });
    report.leases_stripped += originalLeaseCount - projection.leases.length;
  }
  if (projection.task_leases && typeof projection.task_leases === "object") {
    const taskLeases = projection.task_leases as JsonObject;
    if (taskLeases.leases && typeof taskLeases.leases === "object" && !Array.isArray(taskLeases.leases)) {
      const remaining: Record<string, JsonObject> = {};
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
        const boundary = String(material.boundary ?? material.visibility ?? "");
        if (
          boundary === "local_private" ||
          boundary === "private" ||
          boundary === "restricted"
        ) {
          return false;
        }
        const url = String(material.source_url ?? material.url ?? "");
        const path = String(material.source_path ?? material.path ?? "");
        if (url.includes(".local/") || path.includes(".local/")) return false;
        return true;
      });
      report.private_materials_stripped +=
        originalMaterialCount - (projection[matKey] as JsonObject[]).length;
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
          .filter((scope) => typeof scope === "string" && !scope.includes(".local/"))
          .map((scope) => {
            // Strip machine-specific user prefix
            for (const pattern of LOCAL_PATH_PATTERNS) {
              if (pattern.test(scope)) {
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
    await installPostgreSqlAuthorityStoreSchema(
      resolved.database,
      storeIdentity,
    );

    if (options.runtimeRole) {
      const role = options.runtimeRole.trim();
      if (!/^[a-z][a-z0-9_]{1,62}$/.test(role)) {
        return {
          status: "failed",
          schema_version: POSTGRESQL_SCHEMA_VERSION,
          store_identity: storeIdentity,
          runtime_role: role,
          reason:
            "runtime role must be a valid SQL identifier (lowercase letters, digits, underscores, starting with a letter)",
        };
      }

      const connection = await resolved.database.connect();
      try {
        await connection.query(
          `DO $$
           BEGIN
             IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${role}') THEN
               CREATE ROLE "${role}" NOLOGIN;
             END IF;
           END
           $$;`,
        );
        await connection.query(
          `GRANT USAGE ON SCHEMA loopx_control_plane TO "${role}";`,
        );
        await connection.query(
          `GRANT SELECT ON loopx_control_plane.authority_store_metadata TO "${role}";`,
        );
        await connection.query(
          `GRANT SELECT, INSERT, UPDATE ON loopx_control_plane.authority_heads TO "${role}";`,
        );
        await connection.query(
          `GRANT SELECT, INSERT ON
             loopx_control_plane.authority_checkpoints,
             loopx_control_plane.authority_commits,
             loopx_control_plane.authority_events,
             loopx_control_plane.authority_receipts TO "${role}";`,
        );
      } finally {
        await connection.release();
      }
    }

    return {
      status: "installed",
      schema_version: POSTGRESQL_SCHEMA_VERSION,
      store_identity: storeIdentity,
      runtime_role: options.runtimeRole ?? null,
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

/**
 * Exports a local authority store into a sanitized genesis seed (cursor=1).
 * When targetDatabase and execute=true are provided, atomically commits into PG.
 */
export async function exportLocalToPgGenesisSeed(
  options: LocalToPgSeedExportOptions,
): Promise<LocalToPgSeedExportResult> {
  const tenantId = options.tenantId || "default";
  const operationId = `${options.goalId}-genesis-seed`;

  try {
    const store =
      options.localStore ??
      (
        await openLocalAuthorityStoreHandle(
          options.runtimeRoot,
          options.goalId,
        )
      ).store;
    const loaded = await store.loadAuthority();
    if (loaded.status !== "loaded" || loaded.head === null) {
      return {
        status: "failed",
        schema_version: PG_GENESIS_SEED_SCHEMA_VERSION,
        goal_id: options.goalId,
        tenant_id: tenantId,
        cursor: "1",
        provider_revision: "1",
        operation_id: operationId,
        reason: `Local authority head is not available for goal ${options.goalId} (status: ${loaded.status})`,
      };
    }

    const { projection, report } = sanitizeGenesisSeedProjection(loaded.head);
    const seedDigest = canonicalAuthoritySha256(projection);

    const seedPayload: JsonObject = {
      schema_version: PG_GENESIS_SEED_SCHEMA_VERSION,
      goal_id: options.goalId,
      tenant_id: tenantId,
      cursor: "1",
      provider_revision: "1",
      operation_id: operationId,
      seed_digest: seedDigest,
      sanitization_report: report as unknown as JsonObject,
      projection,
    };

    if (options.outputPath) {
      await writeFile(
        options.outputPath,
        JSON.stringify(seedPayload, null, 2),
        "utf8",
      );
    }

    if (options.execute && (options.targetDatabase || options.databaseUrl)) {
      const resolved = await resolveDatabase(options);
      if (!resolved) {
        return {
          status: "failed",
          schema_version: PG_GENESIS_SEED_SCHEMA_VERSION,
          goal_id: options.goalId,
          tenant_id: tenantId,
          cursor: "1",
          provider_revision: "1",
          operation_id: operationId,
          reason: "Target PostgreSQL database is not available for execution",
        };
      }

      try {
        const storeIdentity =
          options.storeIdentity ??
          `postgresql:${randomBytes(16).toString("hex").toLowerCase()}`;
        await installPostgreSqlAuthorityStoreSchema(
          resolved.database,
          storeIdentity,
        );

        const pgStore = new PostgreSqlAuthorityStore(resolved.database, {
          tenant_id: tenantId,
          goal_id: options.goalId,
        });

        const existing = await pgStore.loadAuthority();
        if (existing.status === "loaded" && existing.head !== null) {
          return {
            status: "failed",
            schema_version: PG_GENESIS_SEED_SCHEMA_VERSION,
            goal_id: options.goalId,
            tenant_id: tenantId,
            cursor: "1",
            provider_revision: "1",
            operation_id: operationId,
            reason: `Target PostgreSQL goal ${options.goalId} already has committed authority; refuse to overwrite`,
          };
        }

        const commitResult = await pgStore.commitAuthority({
          operation_id: operationId,
          expected_provider_revision: null,
          events: [],
          receipts: [
            {
              schema_version: "genesis_seed_receipt_v0",
              source: "local-to-pg-seed",
              source_goal_id: options.goalId,
              seed_digest: seedDigest,
              sanitization_report: report as unknown as JsonObject,
            },
          ],
          next_projection: projection,
        });

        if (commitResult.status !== "applied") {
          return {
            status: "failed",
            schema_version: PG_GENESIS_SEED_SCHEMA_VERSION,
            goal_id: options.goalId,
            tenant_id: tenantId,
            cursor: "1",
            provider_revision: "1",
            operation_id: operationId,
            reason: `Failed to commit genesis seed to PostgreSQL: ${JSON.stringify(commitResult)}`,
          };
        }

        return {
          status: "seeded",
          schema_version: PG_GENESIS_SEED_SCHEMA_VERSION,
          goal_id: options.goalId,
          tenant_id: tenantId,
          cursor: "1",
          provider_revision: commitResult.provider_revision,
          operation_id: operationId,
          seed_digest: seedDigest,
          sanitization_report: report,
          output_path: options.outputPath,
          seed_payload: seedPayload,
        };
      } finally {
        await resolved.close();
      }
    }

    return {
      status: "exported",
      schema_version: PG_GENESIS_SEED_SCHEMA_VERSION,
      goal_id: options.goalId,
      tenant_id: tenantId,
      cursor: "1",
      provider_revision: "1",
      operation_id: operationId,
      seed_digest: seedDigest,
      sanitization_report: report,
      output_path: options.outputPath,
      seed_payload: seedPayload,
    };
  } catch (error) {
    return {
      status: "failed",
      schema_version: PG_GENESIS_SEED_SCHEMA_VERSION,
      goal_id: options.goalId,
      tenant_id: tenantId,
      cursor: "1",
      provider_revision: "1",
      operation_id: operationId,
      reason: error instanceof Error ? error.message : String(error),
    };
  }
}
