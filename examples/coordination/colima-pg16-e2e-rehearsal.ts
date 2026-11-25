/**
 * Colima / Docker PostgreSQL 16 Dual-Actor Coordination Rehearsal Suite
 *
 * Validates:
 * 1. Schema DDL initialization and least-privilege restricted role assembly.
 * 2. Genesis seed sanitization and ingestion via exportLocalToPgGenesisSeed.
 * 3. Dual-actor lease competition between operator.local and worker.cloud over HTTPS/TLS.
 * 4. Active lease conflict prevention (actor B rejected while actor A holds valid lease).
 * 5. Default-deny security boundary (unauthorized goal and invalid bearer credential rejected).
 * 6. Controlled-clock expired lease takeover + lease_epoch increment + stale-actor fencing.
 * 7. Verified task progress, completion, and historical recover readback over HTTPS/TLS.
 * 8. Cross-machine snapshot and direct restricted-role PostgreSQL authority readback.
 */

import {execFile} from "node:child_process";
import {createHash, randomBytes} from "node:crypto";
import {realpathSync} from "node:fs";
import {mkdtemp, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join, resolve} from "node:path";
import {fileURLToPath} from "node:url";
import {parseArgs, promisify} from "node:util";
import {Pool, type PoolClient, type PoolConfig} from "pg";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {
  coordinationTodoReadModel,
} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {
  canonicalTodoDomainRecord,
  TODO_DOMAIN_ITEM_SCHEMA,
  TODO_DOMAIN_READ_RECORD_SCHEMA,
} from "../../loopx/control_plane/coordination/coordination_state_contract.ts";
import {
  openLocalAuthorityStore,
} from "../../loopx/control_plane/coordination/local_authority_provider.ts";
import {
  exportLocalToPgGenesisSeed,
  setupPostgreSqlAuthoritySchemaAndRole,
} from "../../loopx/control_plane/coordination/postgresql_authority_seed.ts";
import {
  POSTGRESQL_SCHEMA_VERSION,
  PostgreSqlAuthorityStore,
  type PostgreSqlAuthorityConnection,
  type PostgreSqlAuthorityDatabase,
} from "../../loopx/control_plane/coordination/postgresql_authority_store.ts";
import {
  decodeRemoteAuthorityRequest,
  type RemoteAuthorityRequest,
} from "../../loopx/control_plane/coordination/remote_authority_contract.ts";
import {
  callRemoteAuthority,
  createRemoteAuthorityServer,
} from "../../loopx/control_plane/coordination/remote_authority_http.ts";
import {
  fileRemoteAuthorityAdmission,
  REMOTE_AUTHORITY_POLICY,
} from "../../loopx/control_plane/coordination/remote_authority_policy.ts";
import {
  RemoteAuthorityService,
} from "../../loopx/control_plane/coordination/remote_authority_service.ts";
import {
  materializeTodoRecordForSchema,
} from "../../loopx/control_plane/coordination/todo_presentation.ts";

export interface RehearsalStepResult {
  step: string;
  ok: boolean;
  duration_ms: number;
  detail: Record<string, unknown>;
}

export interface RehearsalReport {
  schema_version: "colima_pg16_coordination_rehearsal_report_v0";
  ok: boolean;
  mode: "real_pg" | "simulation";
  execution_boundary:
    | "isolated_real_pg16_tls"
    | "isolated_real_postgresql_tls"
    | "in_memory_simulated_pg_tls";
  transport: "https_tls";
  clock_mode: "controlled_service_clock";
  runtime_role_verified: boolean;
  server_version: string | null;
  server_version_num: number | null;
  server_major_version: number | null;
  pg16_qualified: boolean;
  tenant_id: string;
  goal_id: string;
  store_identity: string;
  total_duration_ms: number;
  actors: {
    actor_a: string;
    actor_b: string;
  };
  steps: RehearsalStepResult[];
  error?: string;
}

export interface PostgreSqlServerVersionInfo {
  server_version: string | null;
  server_version_num: number | null;
  server_major_version: number | null;
}

/**
 * Parses PostgreSQL `SHOW server_version` and `SHOW server_version_num` values
 * into normalized version fields before any database mutation runs.
 */
export function parsePostgreSqlServerVersion(
  serverVersionRaw: unknown,
  serverVersionNumRaw?: unknown,
): PostgreSqlServerVersionInfo {
  const serverVersion =
    typeof serverVersionRaw === "string" && serverVersionRaw.trim().length > 0
      ? serverVersionRaw.trim()
      : null;

  if (serverVersionNumRaw !== undefined && serverVersionNumRaw !== null) {
    const rawNum = String(serverVersionNumRaw).trim();
    if (/^\d+$/.test(rawNum)) {
      const parsedNum = Number(rawNum);
      if (Number.isSafeInteger(parsedNum) && parsedNum > 0) {
        return {
          server_version: serverVersion,
          server_version_num: parsedNum,
          server_major_version: Math.floor(parsedNum / 10_000),
        };
      }
    }
  }

  if (serverVersion !== null) {
    const match = serverVersion.match(/^(\d+)(?:\.(\d+))?/);
    if (match) {
      const major = Number(match[1]);
      const minor = match[2] ? Number(match[2]) : 0;
      if (Number.isSafeInteger(major) && major > 0 && Number.isSafeInteger(minor) && minor >= 0) {
        return {
          server_version: serverVersion,
          server_version_num: major * 10_000 + minor,
          server_major_version: major,
        };
      }
    }
  }

  return {
    server_version: serverVersion,
    server_version_num: null,
    server_major_version: null,
  };
}

export interface RehearsalOptions {
  databaseUrl?: string;
  simulate?: boolean;
  verbose?: boolean;
  leaseTtlSeconds?: number;
}

function asJsonObject(value: unknown): JsonObject | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonObject)
    : undefined;
}

function databaseFromPool(pool: Pool): PostgreSqlAuthorityDatabase {
  return {
    connect: async () => {
      const client: PoolClient = await pool.connect();
      const connection: PostgreSqlAuthorityConnection = {
        query: async (text, values) =>
          await client.query(text, values ? [...values] : undefined),
        release: (error) => client.release(error),
      };
      return connection;
    },
  };
}

export class ColimaPgCoordinationRehearsal {
  private readonly tenantId = "tenant-canary";
  private readonly goalId = "loopx-quality-review";
  private readonly actorA = "operator.local";
  private readonly actorB = "worker.cloud";
  private readonly tokenA = `rehearsal-local-${"a".repeat(32)}`;
  private readonly tokenB = `rehearsal-cloud-${"b".repeat(32)}`;
  private readonly invalidToken = `invalid-rehearsal-${"x".repeat(32)}`;
  private readonly todoId = "todo_canary_001";
  private readonly options: RehearsalOptions;
  private serviceClock: Date;

  constructor(options: RehearsalOptions = {}) {
    this.options = options;
    this.serviceClock = new Date();
  }

  async run(): Promise<RehearsalReport> {
    const startTime = Date.now();
    const steps: RehearsalStepResult[] = [];
    const storeIdentity = `postgresql:${randomBytes(16).toString("hex").toLowerCase()}`;
    const explicitEmptyUrl =
      this.options.databaseUrl !== undefined && this.options.databaseUrl.trim().length === 0;
    const isRealPg = Boolean(this.options.databaseUrl?.trim() && !this.options.simulate);
    const mode: RehearsalReport["mode"] = isRealPg || explicitEmptyUrl ? "real_pg" : "simulation";
    let executionBoundary: RehearsalReport["execution_boundary"] = mode === "real_pg"
      ? "isolated_real_postgresql_tls"
      : "in_memory_simulated_pg_tls";
    let serverVersion: string | null = null;
    let serverVersionNum: number | null = null;
    let serverMajorVersion: number | null = null;

    const disconnects = new Map<Pool, Set<Promise<void>>>();
    const trackedPool = (config: PoolConfig): Pool => {
      const created = new Pool(config);
      const pending = new Set<Promise<void>>();
      disconnects.set(created, pending);
      created.on("connect", client => {
        const closed = new Promise<void>(resolve => client.once("end", resolve));
        pending.add(closed);
        void closed.then(() => pending.delete(closed));
      });
      return created;
    };
    const closePool = async (target?: Pool | null): Promise<void> => {
      if (!target) return;
      await target.end();
      await Promise.all(disconnects.get(target) ?? []);
    };

    const tempRoots: string[] = [];
    let adminPool: Pool | null = null;
    let setupPool: Pool | null = null;
    let runtimePool: Pool | null = null;
    let databaseCreated = false;
    let roleCreated = false;
    let dbName = "";
    let roleName = "";
    let runtimeRoleVerified = false;
    let listener: ReturnType<typeof createRemoteAuthorityServer> | null = null;

    try {
      if (explicitEmptyUrl && !this.options.simulate) {
        throw new Error("databaseUrl must be a non-empty PostgreSQL connection URL");
      }

      const suffix = randomBytes(8).toString("hex");
      dbName = `rehearsal_${suffix}`;
      roleName = `loopx_app_${suffix}`;
      const rolePassword = randomBytes(24).toString("hex");

      let setupDatabase: PostgreSqlAuthorityDatabase;
      let runtimeDatabase: PostgreSqlAuthorityDatabase;

      if (isRealPg) {
        const baseUrl = this.options.databaseUrl!;
        adminPool = trackedPool({
          connectionString: baseUrl,
          max: 2,
          connectionTimeoutMillis: 5000,
          statement_timeout: 10000,
        });
        // Inspect server version before any CREATE DATABASE / role / schema mutation.
        const versionRes = await adminPool.query("SHOW server_version");
        const versionNumRes = await adminPool.query("SHOW server_version_num");
        const parsedVersion = parsePostgreSqlServerVersion(
          asJsonObject(versionRes.rows[0])?.server_version,
          asJsonObject(versionNumRes.rows[0])?.server_version_num,
        );
        serverVersion = parsedVersion.server_version;
        serverVersionNum = parsedVersion.server_version_num;
        serverMajorVersion = parsedVersion.server_major_version;
        if (serverMajorVersion !== 16) {
          throw new Error(
            `PostgreSQL 16 is required for PG16 qualification rehearsal; connected server reports ${
              serverVersion ?? "unknown"
            } (server_version_num=${serverVersionNum ?? "unknown"}). Refusing before database setup.`,
          );
        }
        executionBoundary = "isolated_real_pg16_tls";

        await adminPool.query(`CREATE DATABASE ${dbName}`);
        databaseCreated = true;

        const dbUrl = new URL(baseUrl);
        dbUrl.pathname = `/${dbName}`;
        setupPool = trackedPool({
          connectionString: dbUrl.href,
          max: 8,
          connectionTimeoutMillis: 5000,
          statement_timeout: 10000,
        });
        await setupPool.query(
          `CREATE ROLE ${roleName} LOGIN PASSWORD '${rolePassword}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT`,
        );
        roleCreated = true;
        await setupPool.query(`GRANT CONNECT ON DATABASE ${dbName} TO ${roleName}`);
        setupDatabase = databaseFromPool(setupPool);

        const runtimeUrl = new URL(dbUrl);
        runtimeUrl.username = roleName;
        runtimeUrl.password = rolePassword;
        runtimePool = trackedPool({
          connectionString: runtimeUrl.href,
          max: 8,
          connectionTimeoutMillis: 5000,
          statement_timeout: 10000,
        });
        runtimeDatabase = databaseFromPool(runtimePool);
      } else {
        setupDatabase = this.createSimulatedDatabase(storeIdentity);
        runtimeDatabase = setupDatabase;
      }

      // Step 1: Schema initialization and restricted role assembly (admin/setup connection only)
      const s1Start = Date.now();
      const setupResult = await setupPostgreSqlAuthoritySchemaAndRole({
        database: setupDatabase,
        storeIdentity,
        runtimeRole: roleName,
      });
      const s1Ok = setupResult.status === "installed" && setupResult.runtime_role === roleName;
      steps.push({
        step: "schema_initialization_and_role_assembly",
        ok: s1Ok,
        duration_ms: Date.now() - s1Start,
        detail: {
          status: setupResult.status,
          store_identity: setupResult.store_identity,
          runtime_role: setupResult.runtime_role,
          mode,
        },
      });
      if (!s1Ok) {
        throw new Error(`Schema setup failed: ${setupResult.reason ?? "unknown"}`);
      }

      // Step 2: Genesis seed sanitization & ingestion via real exportLocalToPgGenesisSeed
      const s2Start = Date.now();
      const seedRuntimeRoot = await mkdtemp(join(tmpdir(), "loopx-pg-rehearsal-seed-"));
      tempRoots.push(seedRuntimeRoot);
      const localStore = await openLocalAuthorityStore(seedRuntimeRoot, this.goalId);
      const todoDomain = canonicalTodoDomainRecord({
        schema_version: TODO_DOMAIN_ITEM_SCHEMA,
        todo_id: this.todoId,
        role: "agent",
        status: "open",
        done: false,
        archive_state: "active",
        text: "[Canary] Verify cross-machine PG lease coordination",
        priority: "P1",
        task_class: "advancement_task",
        required_write_scopes: [`${this.todoId}/**`],
      }, "canary rehearsal todo domain record");
      const canaryTodo = materializeTodoRecordForSchema(
        todoDomain,
        TODO_DOMAIN_READ_RECORD_SCHEMA,
        "canary rehearsal todo",
      );
      const localInit = await localStore.commitAuthority({
        operation_id: `local-seed-prep-${randomBytes(6).toString("hex")}`,
        expected_provider_revision: null,
        next_projection: {
          goal_id: this.goalId,
          handoff_mode: "hard_lease",
          todos: [canaryTodo],
          leases: [],
          todo_read_model: coordinationTodoReadModel([canaryTodo], TODO_DOMAIN_READ_RECORD_SCHEMA),
        },
        events: [],
        receipts: [],
      });
      if (localInit.status !== "applied") {
        throw new Error(`Local synthetic seed store initialization failed: ${JSON.stringify(localInit)}`);
      }
      const seedResult = await exportLocalToPgGenesisSeed({
        runtimeRoot: seedRuntimeRoot,
        goalId: this.goalId,
        tenantId: this.tenantId,
        targetDatabase: setupDatabase,
        storeIdentity,
        execute: true,
      });
      const s2Ok = seedResult.status === "seeded" && seedResult.cursor === "1";
      steps.push({
        step: "genesis_seed_ingestion",
        ok: s2Ok,
        duration_ms: Date.now() - s2Start,
        detail: {
          status: seedResult.status,
          provider_revision: seedResult.provider_revision,
          cursor: seedResult.cursor,
          seed_digest: seedResult.seed_digest,
          todos_sanitized: seedResult.sanitization_report?.todos_sanitized ?? 0,
        },
      });
      if (!s2Ok) {
        throw new Error(`Genesis seed ingestion failed: ${JSON.stringify(seedResult)}`);
      }

      // Close setup/admin provisioning pool before starting runtime service.
      if (setupPool) {
        await closePool(setupPool);
        setupPool = null;
      }

      if (isRealPg && runtimePool) {
        const roleCheck = await runtimePool.query(
          `SELECT current_user AS current_user, r.rolsuper, r.rolbypassrls
           FROM pg_roles AS r
           WHERE r.rolname = current_user`,
        );
        const row = asJsonObject(roleCheck.rows[0]);
        if (!row || row.current_user !== roleName || row.rolsuper !== false || row.rolbypassrls !== false) {
          throw new Error(`Runtime connection is not using least-privilege role ${roleName}: ${JSON.stringify(row)}`);
        }
        let appendOnlyDenied = false;
        try {
          await runtimePool.query("DELETE FROM loopx_control_plane.authority_commits");
        } catch (err) {
          const pgErr = err as {code?: string};
          if (pgErr.code === "42501") {
            appendOnlyDenied = true;
          }
        }
        if (!appendOnlyDenied) {
          throw new Error("Restricted runtime role unexpectedly allowed DELETE on authority_commits");
        }
        runtimeRoleVerified = true;
      }

      // Prepare self-signed localhost TLS certificate & policy file for HTTPS client/server transport
      const tlsRoot = await mkdtemp(join(tmpdir(), "loopx-pg-rehearsal-tls-"));
      tempRoots.push(tlsRoot);
      const keyPath = join(tlsRoot, "key.pem");
      const certPath = join(tlsRoot, "cert.pem");
      await promisify(execFile)("openssl", [
        "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
        "-subj", "/CN=localhost",
        "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
        "-keyout", keyPath,
        "-out", certPath,
      ]);
      const cert = await readFile(certPath);
      const key = await readFile(keyPath);

      const policyPath = join(tlsRoot, "policy.json");
      await writeFile(policyPath, JSON.stringify({
        schema_version: REMOTE_AUTHORITY_POLICY,
        scopes: [
          {
            tenant_id: this.tenantId,
            goal_id: this.goalId,
            store_identity: storeIdentity,
            registered_agents: [this.actorA, this.actorB],
          },
        ],
        principals: [
          {
            principal_id: `principal-${this.actorA}`,
            credential_sha256: createHash("sha256").update(this.tokenA).digest("hex"),
            grants: [
              {
                tenant_id: this.tenantId,
                goal_id: this.goalId,
                actor_id: this.actorA,
                operations: [
                  "read", "claim", "progress", "renew", "release",
                  "complete", "block", "defer", "todo_add", "snapshot",
                ],
              },
            ],
          },
          {
            principal_id: `principal-${this.actorB}`,
            credential_sha256: createHash("sha256").update(this.tokenB).digest("hex"),
            grants: [
              {
                tenant_id: this.tenantId,
                goal_id: this.goalId,
                actor_id: this.actorB,
                operations: [
                  "read", "claim", "progress", "renew", "release",
                  "complete", "block", "defer", "todo_add", "snapshot",
                ],
              },
            ],
          },
        ],
      }), {mode: 0o600});

      const service = new RemoteAuthorityService({
        database: runtimeDatabase,
        admit: fileRemoteAuthorityAdmission(policyPath),
        now: () => this.serviceClock,
      });
      listener = createRemoteAuthorityServer(service, {cert, key});
      const port = await listener.listen("127.0.0.1", 0);
      const endpoint = `https://127.0.0.1:${port}`;
      const callTls = (credential: string, request: RemoteAuthorityRequest, callMode: "execute" | "recover" = "execute") =>
        callRemoteAuthority(endpoint, credential, request, {ca: cert, mode: callMode});

      // Step 3: Actor A claims lease over HTTPS/TLS
      const s3Start = Date.now();
      const claimAId = `claim-a-${randomBytes(6).toString("hex")}`;
      const claimAReq = decodeRemoteAuthorityRequest({
        schema_version: "loopx_remote_authority_request_v0",
        tenant_id: this.tenantId,
        goal_id: this.goalId,
        actor_id: this.actorA,
        store_identity: storeIdentity,
        operation: "claim",
        request_id: claimAId,
        todo_id: this.todoId,
      });
      const claimARes = await callTls(this.tokenA, claimAReq);
      const claimAResult = asJsonObject(claimARes.result);
      const claimALease = asJsonObject(claimAResult?.lease);
      const expiresAt = typeof claimALease?.expires_at === "string" ? claimALease.expires_at : "";
      const epochA = typeof claimALease?.lease_epoch === "number" ? claimALease.lease_epoch : Number.NaN;
      const s3Ok = claimARes.status === "result" &&
        claimAResult?.status === "applied" &&
        claimAResult?.claimed_by === this.actorA &&
        Boolean(expiresAt) &&
        Number.isFinite(epochA);
      steps.push({
        step: "actor_a_lease_claim",
        ok: s3Ok,
        duration_ms: Date.now() - s3Start,
        detail: {
          status: claimARes.status,
          result_status: claimAResult?.status,
          claimed_by: claimAResult?.claimed_by,
          lease_epoch: epochA,
          expires_at: expiresAt,
          transport: "https_tls",
        },
      });
      if (!s3Ok) {
        throw new Error(`Actor A claim failed: ${JSON.stringify(claimARes)}`);
      }

      // Step 4: Actor B attempts to claim while lease is active (must reject conflict)
      const s4Start = Date.now();
      const claimBConflictReq = decodeRemoteAuthorityRequest({
        schema_version: "loopx_remote_authority_request_v0",
        tenant_id: this.tenantId,
        goal_id: this.goalId,
        actor_id: this.actorB,
        store_identity: storeIdentity,
        operation: "claim",
        request_id: `claim-b-conflict-${randomBytes(6).toString("hex")}`,
        todo_id: this.todoId,
      });
      const claimBConflictRes = await callTls(this.tokenB, claimBConflictReq);
      const claimBResult = asJsonObject(claimBConflictRes.result);
      const s4Ok = claimBConflictRes.status === "result" &&
        (claimBResult?.status === "rejected" || claimBResult?.status === "failed");
      steps.push({
        step: "active_lease_conflict_rejection",
        ok: s4Ok,
        duration_ms: Date.now() - s4Start,
        detail: {
          status: claimBConflictRes.status,
          result_status: claimBResult?.status,
          reason_code: claimBResult?.reason_code,
        },
      });

      // Step 5: Default-deny security boundary enforcement over HTTPS/TLS
      const s5Start = Date.now();
      const unauthGoalReq = decodeRemoteAuthorityRequest({
        schema_version: "loopx_remote_authority_request_v0",
        tenant_id: this.tenantId,
        goal_id: "ungranted-canary-goal",
        actor_id: this.actorB,
        store_identity: storeIdentity,
        operation: "read",
        todo_id: this.todoId,
      });
      const unauthGoalRes = await callTls(this.tokenB, unauthGoalReq);
      const unauthCredRes = await callTls(this.invalidToken, claimAReq);

      const s5Ok = unauthGoalRes.status === "rejected" &&
        unauthCredRes.status === "rejected" &&
        unauthCredRes.reason_code === "principal_unauthenticated";
      steps.push({
        step: "default_deny_boundary_enforcement",
        ok: s5Ok,
        duration_ms: Date.now() - s5Start,
        detail: {
          unauthorized_goal_status: unauthGoalRes.status,
          unauthorized_goal_reason: unauthGoalRes.reason_code,
          unauthorized_cred_status: unauthCredRes.status,
          unauthorized_cred_reason: unauthCredRes.reason_code,
        },
      });

      // Step 6: Advance controlled service clock past observed expires_at -> Actor B takeover + stale Actor A fencing
      const s6Start = Date.now();
      const expiresAtMs = Date.parse(expiresAt);
      if (!Number.isFinite(expiresAtMs)) {
        throw new Error(`Invalid observed expires_at timestamp: ${expiresAt}`);
      }
      this.serviceClock = new Date(expiresAtMs + 1_000);
      const advancedClockIso = this.serviceClock.toISOString();

      const takeoverId = `claim-b-takeover-${randomBytes(6).toString("hex")}`;
      const takeoverReq = decodeRemoteAuthorityRequest({
        schema_version: "loopx_remote_authority_request_v0",
        tenant_id: this.tenantId,
        goal_id: this.goalId,
        actor_id: this.actorB,
        store_identity: storeIdentity,
        operation: "claim",
        request_id: takeoverId,
        todo_id: this.todoId,
      });
      const takeoverRes = await callTls(this.tokenB, takeoverReq);
      const takeoverResult = asJsonObject(takeoverRes.result);
      const takeoverLease = asJsonObject(takeoverResult?.lease);
      const newEpoch = typeof takeoverLease?.lease_epoch === "number"
        ? takeoverLease.lease_epoch
        : Number.NaN;

      const staleProgressReq = decodeRemoteAuthorityRequest({
        schema_version: "loopx_remote_authority_request_v0",
        tenant_id: this.tenantId,
        goal_id: this.goalId,
        actor_id: this.actorA,
        store_identity: storeIdentity,
        operation: "progress",
        request_id: `stale-prog-a-${randomBytes(6).toString("hex")}`,
        claim_request_id: claimAId,
        todo_id: this.todoId,
        note: "Stale actor A progress must be rejected after takeover",
      });
      const staleProgressRes = await callTls(this.tokenA, staleProgressReq);
      const staleProgressResult = asJsonObject(staleProgressRes.result);
      const staleActorFenced = staleProgressRes.status === "result" &&
        (staleProgressResult?.status === "failed" || staleProgressResult?.status === "rejected");

      const s6Ok = takeoverRes.status === "result" &&
        takeoverResult?.status === "applied" &&
        takeoverResult?.claimed_by === this.actorB &&
        newEpoch === epochA + 1 &&
        staleActorFenced;
      steps.push({
        step: "lease_expiration_takeover",
        ok: s6Ok,
        duration_ms: Date.now() - s6Start,
        detail: {
          status: takeoverRes.status,
          result_status: takeoverResult?.status,
          claimed_by: takeoverResult?.claimed_by,
          observed_expires_at: expiresAt,
          advanced_service_clock: advancedClockIso,
          previous_lease_epoch: epochA,
          new_lease_epoch: newEpoch,
          stale_actor_fenced: staleActorFenced,
          stale_actor_result_status: staleProgressResult?.status,
          stale_actor_reason_code: staleProgressResult?.reason_code,
        },
      });

      // Step 7: Actor B reports progress, completes task, and verifies recover receipt over HTTPS/TLS
      const s7Start = Date.now();
      const progressReq = decodeRemoteAuthorityRequest({
        schema_version: "loopx_remote_authority_request_v0",
        tenant_id: this.tenantId,
        goal_id: this.goalId,
        actor_id: this.actorB,
        store_identity: storeIdentity,
        operation: "progress",
        request_id: `prog-b-${randomBytes(6).toString("hex")}`,
        claim_request_id: takeoverId,
        todo_id: this.todoId,
        note: "Colima PG16 dual-actor TLS rehearsal in progress",
      });
      const progressRes = await callTls(this.tokenB, progressReq);
      const progressResult = asJsonObject(progressRes.result);

      const completeReq = decodeRemoteAuthorityRequest({
        schema_version: "loopx_remote_authority_request_v0",
        tenant_id: this.tenantId,
        goal_id: this.goalId,
        actor_id: this.actorB,
        store_identity: storeIdentity,
        operation: "complete",
        request_id: `comp-b-${randomBytes(6).toString("hex")}`,
        claim_request_id: takeoverId,
        todo_id: this.todoId,
        evidence: "canary-rehearsal:pg16-tls-dual-actor-pass",
        note: "Verified dual-actor TLS lease coordination and task completion",
        no_followup: true,
      });
      const completeRes = await callTls(this.tokenB, completeReq);
      const compResult = asJsonObject(completeRes.result);
      const recoverRes = await callTls(this.tokenB, completeReq, "recover");

      const s7Ok = progressRes.status === "result" &&
        progressResult?.status === "applied" &&
        completeRes.status === "result" &&
        compResult?.status === "applied" &&
        compResult?.completed === true &&
        recoverRes.status === "found" &&
        recoverRes.historical_only === true;
      steps.push({
        step: "actor_b_task_completion",
        ok: s7Ok,
        duration_ms: Date.now() - s7Start,
        detail: {
          progress_status: progressRes.status,
          progress_result_status: progressResult?.status,
          complete_status: completeRes.status,
          result_status: compResult?.status,
          completed: compResult?.completed,
          recover_status: recoverRes.status,
          historical_only: recoverRes.historical_only,
        },
      });

      // Step 8: Cross-machine snapshot, read, and direct restricted-role PostgreSQL authority readback
      const s8Start = Date.now();
      const snapshotReq = decodeRemoteAuthorityRequest({
        schema_version: "loopx_remote_authority_request_v0",
        tenant_id: this.tenantId,
        goal_id: this.goalId,
        actor_id: this.actorA,
        store_identity: storeIdentity,
        operation: "snapshot",
      });
      const snapshotRes = await callTls(this.tokenA, snapshotReq);
      const readReq = decodeRemoteAuthorityRequest({
        schema_version: "loopx_remote_authority_request_v0",
        tenant_id: this.tenantId,
        goal_id: this.goalId,
        actor_id: this.actorA,
        store_identity: storeIdentity,
        operation: "read",
        todo_id: this.todoId,
      });
      const readRes = await callTls(this.tokenA, readReq);
      const readTodo = asJsonObject(readRes.todo);
      const directHead = await new PostgreSqlAuthorityStore(runtimeDatabase, {
        tenant_id: this.tenantId,
        goal_id: this.goalId,
        expected_store_identity: storeIdentity,
      }).loadAuthority();
      const todos = Array.isArray(snapshotRes.todos) ? (snapshotRes.todos as JsonObject[]) : [];
      const s8Ok = snapshotRes.status === "snapshot" &&
        typeof snapshotRes.receipt_sha256 === "string" &&
        snapshotRes.receipt_sha256.length === 64 &&
        todos.some(t => t.todo_id === this.todoId && t.status === "done") &&
        readRes.status === "found" &&
        readTodo?.status === "done" &&
        directHead.status === "loaded" &&
        directHead.cursor === "5";
      steps.push({
        step: "cross_machine_snapshot_readback",
        ok: s8Ok,
        duration_ms: Date.now() - s8Start,
        detail: {
          status: snapshotRes.status,
          receipt_sha256: snapshotRes.receipt_sha256,
          todo_count: todos.length,
          read_status: readRes.status,
          direct_cursor: directHead.status === "loaded" ? directHead.cursor : null,
        },
      });

      const allOk = steps.every(s => s.ok) && (!isRealPg || (runtimeRoleVerified && serverMajorVersion === 16));
      return {
        schema_version: "colima_pg16_coordination_rehearsal_report_v0",
        ok: allOk,
        mode,
        execution_boundary: executionBoundary,
        transport: "https_tls",
        clock_mode: "controlled_service_clock",
        runtime_role_verified: runtimeRoleVerified,
        server_version: serverVersion,
        server_version_num: serverVersionNum,
        server_major_version: serverMajorVersion,
        pg16_qualified: isRealPg && allOk && serverMajorVersion === 16,
        tenant_id: this.tenantId,
        goal_id: this.goalId,
        store_identity: storeIdentity,
        total_duration_ms: Date.now() - startTime,
        actors: {
          actor_a: this.actorA,
          actor_b: this.actorB,
        },
        steps,
      };
    } catch (error) {
      return {
        schema_version: "colima_pg16_coordination_rehearsal_report_v0",
        ok: false,
        mode,
        execution_boundary: executionBoundary,
        transport: "https_tls",
        clock_mode: "controlled_service_clock",
        runtime_role_verified: runtimeRoleVerified,
        server_version: serverVersion,
        server_version_num: serverVersionNum,
        server_major_version: serverMajorVersion,
        pg16_qualified: false,
        tenant_id: this.tenantId,
        goal_id: this.goalId,
        store_identity: storeIdentity,
        total_duration_ms: Date.now() - startTime,
        actors: {
          actor_a: this.actorA,
          actor_b: this.actorB,
        },
        steps,
        error: error instanceof Error ? error.message : String(error),
      };
    } finally {
      if (listener) {
        await listener.close();
      }
      await closePool(runtimePool);
      await closePool(setupPool);
      if (adminPool) {
        try {
          if (databaseCreated) await adminPool.query(`DROP DATABASE IF EXISTS ${dbName}`);
          if (roleCreated) await adminPool.query(`DROP ROLE IF EXISTS ${roleName}`);
        } finally {
          await closePool(adminPool);
        }
      }
      for (const dir of tempRoots) {
        await rm(dir, {recursive: true, force: true});
      }
    }
  }

  private createSimulatedDatabase(storeIdentity: string): PostgreSqlAuthorityDatabase {
    interface SimulatedCommit {
      tenant_id: string;
      goal_id: string;
      cursor: number;
      provider_revision: string;
      operation_id: string;
    }
    interface SimulatedReceipt {
      tenant_id: string;
      goal_id: string;
      cursor: number;
      receipt_index: number;
      receipt: unknown;
    }

    const heads = new Map<string, {
      tenant_id: string;
      goal_id: string;
      provider_revision: number;
      cursor: number;
      head: JsonObject | null;
    }>();
    const commits: SimulatedCommit[] = [];
    const receipts: SimulatedReceipt[] = [];

    return {
      connect: async () => {
        let activeTenantId = "default";
        return {
          query: async (sql: string, args?: readonly unknown[]) => {
            const s = sql.trim();
            if (
              s.startsWith("BEGIN") || s.startsWith("COMMIT") || s.startsWith("ROLLBACK") ||
              s.startsWith("CREATE") || s.startsWith("ALTER") || s.startsWith("GRANT") ||
              s.startsWith("DO") || s.startsWith("DROP POLICY")
            ) {
              return {rows: [], rowCount: 0};
            }
            if (s.includes("set_config")) {
              activeTenantId = String(args?.[0] ?? "default");
              return {rows: [{tenant_id: activeTenantId}], rowCount: 1};
            }
            if (s.includes("pg_advisory_")) {
              return {rows: [], rowCount: 1};
            }
            if (s.includes("INSERT INTO loopx_control_plane.authority_store_metadata")) {
              return {rows: [], rowCount: 1};
            }
            if (s.includes("FROM loopx_control_plane.authority_store_metadata")) {
              return {
                rows: [{schema_version: POSTGRESQL_SCHEMA_VERSION, store_identity: storeIdentity}],
                rowCount: 1,
              };
            }
            if (s.includes("INSERT INTO loopx_control_plane.authority_heads")) {
              const tenant = String(args?.[0]);
              const goal = String(args?.[1]);
              const key = `${tenant}:${goal}`;
              if (!heads.has(key)) {
                heads.set(key, {tenant_id: tenant, goal_id: goal, provider_revision: 0, cursor: 0, head: null});
              }
              return {rows: [], rowCount: 1};
            }
            if (s.includes("UPDATE loopx_control_plane.authority_heads")) {
              const tenant = String(args?.[0]);
              const goal = String(args?.[1]);
              const rev = Number(args?.[2]);
              const cursor = Number(args?.[3]);
              const headObj = args?.[4]
                ? (typeof args[4] === "string" ? JSON.parse(args[4]) : args[4]) as JsonObject
                : null;
              heads.set(`${tenant}:${goal}`, {
                tenant_id: tenant,
                goal_id: goal,
                provider_revision: rev,
                cursor,
                head: headObj,
              });
              return {rows: [], rowCount: 1};
            }
            if (s.includes("FROM loopx_control_plane.authority_heads")) {
              const tenant = String(args?.[0]);
              const goal = String(args?.[1]);
              const head = heads.get(`${tenant}:${goal}`);
              if (!head || head.head === null) {
                return {
                  rows: head ? [{provider_revision: "0", cursor: "0", head: null}] : [],
                  rowCount: head ? 1 : 0,
                };
              }
              return {
                rows: [{
                  provider_revision: String(head.provider_revision),
                  cursor: String(head.cursor),
                  head: head.head,
                }],
                rowCount: 1,
              };
            }
            if (s.includes("INSERT INTO loopx_control_plane.authority_checkpoints")) {
              return {rows: [], rowCount: 1};
            }
            if (s.includes("INSERT INTO loopx_control_plane.authority_commits")) {
              commits.push({
                tenant_id: String(args?.[0]),
                goal_id: String(args?.[1]),
                cursor: Number(args?.[2]),
                provider_revision: String(args?.[3]),
                operation_id: String(args?.[4]),
              });
              return {rows: [], rowCount: 1};
            }
            if (s.includes("INSERT INTO loopx_control_plane.authority_receipts")) {
              receipts.push({
                tenant_id: String(args?.[0]),
                goal_id: String(args?.[1]),
                cursor: Number(args?.[2]),
                receipt_index: Number(args?.[3]),
                receipt: args?.[4] ? (typeof args[4] === "string" ? JSON.parse(args[4]) : args[4]) : {},
              });
              return {rows: [], rowCount: 1};
            }
            if (s.includes("INSERT INTO loopx_control_plane.authority_events")) {
              return {rows: [], rowCount: 1};
            }
            if (s.includes("FROM loopx_control_plane.authority_commits AS commit") && s.includes("commit.operation_id =")) {
              const tenant = String(args?.[0]);
              const goal = String(args?.[1]);
              const opId = String(args?.[2]);
              const match = commits.find(c => c.tenant_id === tenant && c.goal_id === goal && c.operation_id === opId);
              if (!match) return {rows: [], rowCount: 0};
              const matchedReceipts = receipts
                .filter(r => r.tenant_id === tenant && r.goal_id === goal && r.cursor === match.cursor)
                .sort((a, b) => a.receipt_index - b.receipt_index)
                .map(r => r.receipt);
              return {
                rows: [{
                  cursor: String(match.cursor),
                  provider_revision: match.provider_revision,
                  operation_id: match.operation_id,
                  receipts: matchedReceipts,
                }],
                rowCount: 1,
              };
            }
            if (s.includes("FROM loopx_control_plane.authority_commits") && s.includes("operation_id =")) {
              const tenant = String(args?.[0]);
              const goal = String(args?.[1]);
              const opId = String(args?.[2]);
              const match = commits.find(c => c.tenant_id === tenant && c.goal_id === goal && c.operation_id === opId);
              if (!match) return {rows: [], rowCount: 0};
              return {
                rows: [{
                  cursor: String(match.cursor),
                  provider_revision: match.provider_revision,
                }],
                rowCount: 1,
              };
            }
            return {rows: [], rowCount: 0};
          },
          release: () => {},
        };
      },
    };
  }
}

function isDirectEntry(): boolean {
  const entry = process.argv[1];
  if (!entry) {
    return false;
  }
  try {
    return (
      realpathSync(resolve(entry)) ===
      realpathSync(fileURLToPath(import.meta.url))
    );
  } catch (error: unknown) {
    const code =
      typeof error === "object" && error !== null && "code" in error
        ? (error as {code?: unknown}).code
        : undefined;
    if (code === "ENOENT" || code === "ENOTDIR") {
      return false;
    }
    throw error;
  }
}

// CLI entrypoint
if (isDirectEntry()) {
  const {values} = parseArgs({
    options: {
      "database-url": {type: "string"},
      simulate: {type: "boolean", default: false},
      verbose: {type: "boolean", default: false},
      "lease-ttl": {type: "string", default: "2"},
    },
  });

  if (values.simulate && values["database-url"] !== undefined) {
    process.stderr.write("Error: --simulate and --database-url are mutually exclusive.\n");
    process.exit(1);
  }
  if (values["database-url"] !== undefined && values["database-url"].trim().length === 0) {
    process.stderr.write("Error: --database-url must be a non-empty PostgreSQL connection URL.\n");
    process.exit(1);
  }

  const effectiveUrl = values.simulate
    ? undefined
    : (values["database-url"] ?? process.env.LOOPX_AUTHORITY_DATABASE_URL);
  const rehearsal = new ColimaPgCoordinationRehearsal({
    databaseUrl: effectiveUrl,
    simulate: values.simulate || !effectiveUrl?.trim(),
    verbose: values.verbose,
    leaseTtlSeconds: Number(values["lease-ttl"] || "2"),
  });

  const report = await rehearsal.run();
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
  if (!report.ok) {
    process.exit(1);
  }
}
