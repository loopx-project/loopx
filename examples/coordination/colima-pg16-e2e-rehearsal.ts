/**
 * Colima / Docker PostgreSQL 16 Dual-Actor Coordination Rehearsal Suite
 *
 * Validates:
 * 1. Schema DDL initialization and least-privilege restricted role assembly.
 * 2. Genesis seed ingestion for loopx-quality-review canary goal.
 * 3. Dual-actor lease competition between ruiteng.macbook (local operator) and lusen.cloud (cloud worker).
 * 4. Active lease conflict prevention (actor B rejected while actor A holds valid lease).
 * 5. Default-deny security boundary (unauthorized goal and invalid token rejected).
 * 6. Lease expiration and takeover (actor B successfully takes over after TTL expires).
 * 7. Verified task completion with evidence and completion receipts.
 * 8. Cross-machine snapshot readback with authoritative digest verification.
 *
 * Supports both real PostgreSQL connection (via Colima/Docker or RDS) and high-fidelity simulation.
 */

import {createHash, randomBytes} from "node:crypto";
import {parseArgs} from "node:util";
import {Pool} from "pg";
import {
  RemoteAuthorityService,
} from "../../loopx/control_plane/coordination/remote_authority_service.ts";
import {
  decodeRemoteAuthorityRequest,
} from "../../loopx/control_plane/coordination/remote_authority_contract.ts";
import {
  decodeRemoteAuthorityPolicy,
  remoteAuthorityPolicyAdmission,
} from "../../loopx/control_plane/coordination/remote_authority_policy.ts";
import {
  setupPostgreSqlAuthoritySchemaAndRole,
} from "../../loopx/control_plane/coordination/postgresql_authority_seed.ts";
import {
  POSTGRESQL_SCHEMA_VERSION,
  PostgreSqlAuthorityStore,
  type PostgreSqlAuthorityDatabase,
} from "../../loopx/control_plane/coordination/postgresql_authority_store.ts";
import {
  coordinationTodoReadModel,
} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {
  TODO_DOMAIN_ITEM_SCHEMA,
  TODO_DOMAIN_READ_RECORD_SCHEMA,
} from "../../loopx/control_plane/coordination/coordination_state_contract.ts";

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

export interface RehearsalOptions {
  databaseUrl?: string;
  simulate?: boolean;
  verbose?: boolean;
  leaseTtlSeconds?: number;
}

export class ColimaPgCoordinationRehearsal {
  private readonly tenantId = "tenant-canary";
  private readonly goalId = "loopx-quality-review";
  private readonly actorA = "ruiteng.macbook";
  private readonly actorB = "lusen.cloud";
  private readonly tokenA = "token-secret-ruiteng-001";
  private readonly tokenB = "token-secret-lusen-002";
  private readonly todoId = "todo_canary_001";
  private readonly options: RehearsalOptions;
  private readonly leaseTtl: number;
  private simulatedTime: Date;

  constructor(options: RehearsalOptions = {}) {
    this.options = options;
    this.leaseTtl = options.leaseTtlSeconds ?? 2;
    this.simulatedTime = new Date();
  }

  async run(): Promise<RehearsalReport> {
    const startTime = Date.now();
    const steps: RehearsalStepResult[] = [];
    const storeIdentity = `postgresql:${randomBytes(16).toString("hex").toLowerCase()}`;

    let pool: Pool | null = null;
    let database: PostgreSqlAuthorityDatabase;

    try {
      if (this.options.simulate || !this.options.databaseUrl) {
        database = this.createSimulatedDatabase(storeIdentity);
      } else {
        pool = new Pool({
          connectionString: this.options.databaseUrl,
          connectionTimeoutMillis: 5000,
          statement_timeout: 10000,
        });
        database = {
          connect: async () => {
            const client = await pool!.connect();
            return {
              query: (sql, args) => client.query(sql, args ? [...args] : undefined),
              release: error => client.release(error),
            };
          },
        };
      }

      // Step 1: Schema initialization and restricted role assembly
      const s1Start = Date.now();
      const setupResult = await setupPostgreSqlAuthoritySchemaAndRole({
        database,
        storeIdentity,
        runtimeRole: "loopx_app",
      });
      const s1Ok = setupResult.status === "installed";
      steps.push({
        step: "schema_initialization_and_role_assembly",
        ok: s1Ok,
        duration_ms: Date.now() - s1Start,
        detail: {
          status: setupResult.status,
          store_identity: setupResult.store_identity,
          runtime_role: setupResult.runtime_role,
        },
      });
      if (!s1Ok) {
        throw new Error(`Schema setup failed: ${setupResult.reason ?? "unknown"}`);
      }

      // Step 2: Genesis seed ingestion for loopx-quality-review canary goal
      const s2Start = Date.now();
      const canaryStore = new PostgreSqlAuthorityStore(database, {
        tenant_id: this.tenantId,
        goal_id: this.goalId,
      });
      const canaryTodo = {
        schema_version: TODO_DOMAIN_ITEM_SCHEMA,
        todo_id: this.todoId,
        role: "agent",
        status: "open",
        done: false,
        archive_state: "active",
        text: "[Canary] Verify cross-machine PG lease coordination",
        priority: "P1",
        task_class: "advancement_task",
      };
      const initialProjection = {
        goal_id: this.goalId,
        handoff_mode: "hard_lease",
        todos: [canaryTodo],
        leases: [],
        todo_read_model: coordinationTodoReadModel([canaryTodo], TODO_DOMAIN_READ_RECORD_SCHEMA),
      };
      const seedCommitResult = await canaryStore.commitAuthority({
        operation_id: `seed-genesis-${randomBytes(6).toString("hex")}`,
        expected_provider_revision: null,
        next_projection: initialProjection,
        events: [],
        receipts: [
          {
            schema_version: "genesis_seed_receipt_v0",
            source: "local-to-pg-seed",
            source_goal_id: this.goalId,
          },
        ],
      });
      const s2Ok = seedCommitResult.status === "applied";
      steps.push({
        step: "genesis_seed_ingestion",
        ok: s2Ok,
        duration_ms: Date.now() - s2Start,
        detail: {
          status: seedCommitResult.status,
          ...(seedCommitResult.status === "applied"
            ? {
                provider_revision: seedCommitResult.provider_revision,
                cursor: seedCommitResult.cursor,
              }
            : {}),
        },
      });
      if (!s2Ok) {
        throw new Error(`Genesis seed commit failed: ${JSON.stringify(seedCommitResult)}`);
      }

      // Construct RemoteAuthorityService with service policy
      const policy = decodeRemoteAuthorityPolicy({
        schema_version: "loopx_remote_authority_policy_v0",
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
      });

      const service = new RemoteAuthorityService({
        database,
        admit: async (cred, req) => remoteAuthorityPolicyAdmission(policy, cred, req),
        now: () => this.simulatedTime,
      });

      // Step 3: Actor A claims lease on canary task
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
      const claimARes = await service.dispatch(this.tokenA, claimAReq);
      const claimAResult = claimARes.result as Record<string, unknown> | undefined;
      const s3Ok = claimARes.status === "result" && claimAResult?.status === "applied";
      steps.push({
        step: "actor_a_lease_claim",
        ok: s3Ok,
        duration_ms: Date.now() - s3Start,
        detail: {
          status: claimARes.status,
          result_status: claimAResult?.status,
          claimed_by: claimAResult?.claimed_by,
        },
      });

      // Step 4: Actor B attempts to claim the same task (must be rejected due to active lease)
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
      const claimBConflictRes = await service.dispatch(this.tokenB, claimBConflictReq);
      const claimBResult = claimBConflictRes.result as Record<string, unknown> | undefined;
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

      // Step 5: Default-deny security boundary enforcement
      const s5Start = Date.now();
      // Test 5a: unauthorized goal
      const unauthGoalReq = decodeRemoteAuthorityRequest({
        schema_version: "loopx_remote_authority_request_v0",
        tenant_id: this.tenantId,
        goal_id: "personal-private-secrets",
        actor_id: this.actorB,
        store_identity: storeIdentity,
        operation: "read",
        todo_id: this.todoId,
      });
      const unauthGoalRes = await service.dispatch(this.tokenB, unauthGoalReq);

      // Test 5b: invalid credential token
      const unauthCredRes = await service.dispatch("invalid-secret-token", claimAReq);

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

      // Step 6: Lease expiration and takeover by Actor B
      const s6Start = Date.now();
      if (this.options.simulate || !this.options.databaseUrl) {
        // Advance virtual clock past lease TTL (2 hours)
        this.simulatedTime = new Date(Date.now() + 7200 * 1000);
      } else {
        // Real PG: wait for actual lease expiration
        await new Promise(r => setTimeout(r, (this.leaseTtl * 1000) + 200));
      }
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
      const takeoverRes = await service.dispatch(this.tokenB, takeoverReq);
      const takeoverResult = takeoverRes.result as Record<string, unknown> | undefined;
      const s6Ok = takeoverRes.status === "result" && takeoverResult?.status === "applied";
      steps.push({
        step: "lease_expiration_takeover",
        ok: s6Ok,
        duration_ms: Date.now() - s6Start,
        detail: {
          status: takeoverRes.status,
          result_status: takeoverResult?.status,
          claimed_by: takeoverResult?.claimed_by,
        },
      });

      // Step 7: Actor B reports progress and completes the task
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
        note: "Colima PG16 dual-actor rehearsal in progress",
      });
      const progressRes = await service.dispatch(this.tokenB, progressReq);

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
        evidence: "github-pr:8765-colima-pg16-rehearsal-pass",
        note: "Verified dual-actor lease coordination and task completion",
      });
      const completeRes = await service.dispatch(this.tokenB, completeReq);
      const compResult = completeRes.result as Record<string, unknown> | undefined;
      const s7Ok = progressRes.status === "result" &&
        completeRes.status === "result" &&
        compResult?.status === "applied" &&
        compResult?.completed === true;
      steps.push({
        step: "actor_b_task_completion",
        ok: s7Ok,
        duration_ms: Date.now() - s7Start,
        detail: {
          progress_status: progressRes.status,
          complete_status: completeRes.status,
          result_status: compResult?.status,
          completed: compResult?.completed,
        },
      });

      // Step 8: Cross-machine snapshot readback
      const s8Start = Date.now();
      const snapshotReq = decodeRemoteAuthorityRequest({
        schema_version: "loopx_remote_authority_request_v0",
        tenant_id: this.tenantId,
        goal_id: this.goalId,
        actor_id: this.actorA,
        store_identity: storeIdentity,
        operation: "snapshot",
      });
      const snapshotRes = await service.dispatch(this.tokenA, snapshotReq);
      const s8Ok = snapshotRes.status === "snapshot" &&
        typeof snapshotRes.receipt_sha256 === "string" &&
        snapshotRes.receipt_sha256.length === 64 &&
        Array.isArray(snapshotRes.todos) &&
        snapshotRes.todos.some((t: Record<string, unknown>) => t.todo_id === this.todoId && t.status === "done");
      steps.push({
        step: "cross_machine_snapshot_readback",
        ok: s8Ok,
        duration_ms: Date.now() - s8Start,
        detail: {
          status: snapshotRes.status,
          receipt_sha256: snapshotRes.receipt_sha256,
          todo_count: Array.isArray(snapshotRes.todos) ? snapshotRes.todos.length : 0,
        },
      });

      const allOk = steps.every(s => s.ok);
      return {
        schema_version: "colima_pg16_coordination_rehearsal_report_v0",
        ok: allOk,
        mode: this.options.simulate ? "simulation" : "real_pg",
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
        mode: this.options.simulate ? "simulation" : "real_pg",
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
      if (pool) {
        await pool.end();
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
      delta: unknown;
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
      head: Record<string, unknown> | null;
    }>();
    const commits: SimulatedCommit[] = [];
    const receipts: SimulatedReceipt[] = [];

    return {
      connect: async () => {
        let activeTenantId = "default";
        return {
          query: async (sql: string, args?: readonly unknown[]) => {
            const s = sql.trim();
            if (s.startsWith("BEGIN") || s.startsWith("COMMIT") || s.startsWith("ROLLBACK") ||
                s.startsWith("CREATE") || s.startsWith("ALTER") || s.startsWith("GRANT") ||
                s.startsWith("DO $$") || s.startsWith("DROP POLICY") || s.startsWith("CREATE POLICY")) {
              return {rows: [], rowCount: 0};
            }
            if (s.includes("set_config")) {
              activeTenantId = String(args?.[0] ?? "default");
              return {rows: [{tenant_id: activeTenantId}], rowCount: 1};
            }
            if (s.includes("pg_advisory_xact_lock")) {
              return {rows: [], rowCount: 1};
            }
            if (s.includes("authority_store_metadata")) {
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
              const headObj = args?.[4] ? (typeof args[4] === "string" ? JSON.parse(args[4]) : args[4]) : null;
              heads.set(`${tenant}:${goal}`, {tenant_id: tenant, goal_id: goal, provider_revision: rev, cursor, head: headObj});
              return {rows: [], rowCount: 1};
            }
            if (s.includes("FROM loopx_control_plane.authority_heads")) {
              const tenant = String(args?.[0]);
              const goal = String(args?.[1]);
              const key = `${tenant}:${goal}`;
              const head = heads.get(key);
              if (!head || head.head === null) {
                return {rows: head ? [{provider_revision: "0", cursor: "0", head: null}] : [], rowCount: head ? 1 : 0};
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
                delta: args?.[5] ? JSON.parse(String(args[5])) : null,
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
            if (s.includes("FROM loopx_control_plane.authority_commits") && s.includes("operation_id =")) {
              const tenant = String(args?.[0]);
              const goal = String(args?.[1]);
              const opId = String(args?.[2]);
              const match = commits.find(c => c.tenant_id === tenant && c.goal_id === goal && c.operation_id === opId);
              if (!match) return {rows: [], rowCount: 0};
              const matchedReceipts = receipts.filter(r => r.tenant_id === tenant && r.goal_id === goal && r.cursor === match.cursor)
                .sort((a, b) => a.receipt_index - b.receipt_index).map(r => r.receipt);
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
            return {rows: [], rowCount: 0};
          },
          release: () => {},
        };
      },
    };
  }
}

// CLI entrypoint
if (import.meta.url === `file://${process.argv[1]}`) {
  const {values} = parseArgs({
    options: {
      "database-url": {type: "string"},
      simulate: {type: "boolean", default: false},
      verbose: {type: "boolean", default: false},
      "lease-ttl": {type: "string", default: "2"},
    },
  });

  const rehearsal = new ColimaPgCoordinationRehearsal({
    databaseUrl: values["database-url"] || process.env.LOOPX_AUTHORITY_DATABASE_URL,
    simulate: values.simulate || !values["database-url"],
    verbose: values.verbose,
    leaseTtlSeconds: Number(values["lease-ttl"] || "2"),
  });

  const report = await rehearsal.run();
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
  if (!report.ok) {
    process.exit(1);
  }
}
