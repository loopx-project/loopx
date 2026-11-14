import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import {
  exportLocalToPgGenesisSeed,
  sanitizeGenesisSeedProjection,
  scanForbiddenPrivacyTokens,
  setupPostgreSqlAuthoritySchemaAndRole,
} from "../../loopx/control_plane/coordination/postgresql_authority_seed.ts";
import { FileAuthorityStore } from "../../loopx/control_plane/coordination/file_authority_store.ts";
import type {PostgreSqlAuthorityDatabase, PostgreSqlAuthorityConnection} from "../../loopx/control_plane/coordination/postgresql_authority_store.ts";

test("sanitizeGenesisSeedProjection strips expired leases", () => {
  const past = new Date(Date.now() - 3600_000).toISOString();
  const future = new Date(Date.now() + 3600_000).toISOString();

  const projection = {
    schema_version: "loopx_authority_projection_v0",
    goal_id: "test-goal",
    task_leases: {
      leases: {
        "lease-expired": {
          lease_id: "lease-expired",
          holder: "agent-1",
          expires_at: past,
        },
        "lease-active": {
          lease_id: "lease-active",
          holder: "agent-2",
          expires_at: future,
        },
      },
    },
  };

  const { projection: sanitized, report } = sanitizeGenesisSeedProjection(projection);
  const leases = (sanitized.task_leases as { leases?: Record<string, unknown> })?.leases ?? {};
  assert.equal(leases["lease-expired"], undefined);
  assert.ok(leases["lease-active"] !== undefined);
  assert.equal(report.leases_stripped, 1);
});

test("sanitizeGenesisSeedProjection redacts local paths and .local references", () => {
  const projection = {
    schema_version: "loopx_authority_projection_v0",
    goal_id: "test-goal",
    runtime_root: "/Users/alice/Developer/loopx/.local/runtime",
    project_root: "/Users/alice/Developer/my-repo",
    registered_materials: [
      {
        material_id: "mat-1",
        path: "/Users/alice/Developer/my-repo/docs/readme.md",
        boundary: "public",
      },
      {
        material_id: "mat-2",
        path: "/Users/alice/.local/notes/secret.md",
        boundary: "local_private",
      },
    ],
  };

  const { projection: sanitized, report } = sanitizeGenesisSeedProjection(projection);
  // .local path should be redacted
  assert.ok(!JSON.stringify(sanitized).includes("/Users/alice/.local"));
  assert.ok(!JSON.stringify(sanitized).includes("/Users/alice/Developer"));
  // private boundary material must be stripped
  const materials = (sanitized.registered_materials as Array<{ material_id: string }>) ?? [];
  assert.equal(materials.length, 1);
  assert.equal(materials[0].material_id, "mat-1");
  assert.equal(report.private_materials_stripped, 1);
  assert.ok(report.paths_redacted > 0);
});

test("sanitizeGenesisSeedProjection redacts Feishu IDs", () => {
  const feishuUser = "ou_abcdef1234567890abcdef12345678";
  const feishuChat = "oc_1234567890abcdef1234567890abcd";
  const projection = {
    schema_version: "loopx_authority_projection_v0",
    goal_id: "test-goal",
    owner_identity: feishuUser,
    channel: {
      chat_id: feishuChat,
      app_id: "cli_a1b2c3d4e5f6g7h8",
    },
  };

  const { projection: sanitized, report } = sanitizeGenesisSeedProjection(projection);
  const serialized = JSON.stringify(sanitized);
  assert.ok(!serialized.includes(feishuUser));
  assert.ok(!serialized.includes(feishuChat));
  assert.ok(!serialized.includes("cli_a1b2c3d4e5f6g7h8"));
  assert.ok(serialized.includes("[feishu_id_redacted]"));
  assert.ok(report.feishu_ids_redacted >= 3);
});

test("scanForbiddenPrivacyTokens fails closed on sensitive credentials", () => {
  const clean = {
    goal_id: "clean-goal",
    objective: "Just a clean goal with safe public description",
  };
  assert.doesNotThrow(() => scanForbiddenPrivacyTokens(clean));

  const withSecret = {
    goal_id: "leaky-goal",
    apiKey: "sk-ant-api03-abcdef1234567890abcdef1234567890abcdef",
  };
  assert.throws(() => scanForbiddenPrivacyTokens(withSecret), /Privacy boundary violation/);

  const withGithubToken = {
    token: "ghp_1234567890abcdefghijklmnopqrstuvwxyz",
  };
  assert.throws(() => scanForbiddenPrivacyTokens(withGithubToken), /Privacy boundary violation/);
});

test("setupPostgreSqlAuthoritySchemaAndRole installs DDL, RLS, and restricted role", async () => {
  const executedStatements: string[] = [];
  const storeId = "postgresql:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
  const connection: PostgreSqlAuthorityConnection = {
    query: async (sql: string, params?: readonly unknown[]) => {
      executedStatements.push(sql);
      if (sql.includes("FROM loopx_control_plane.authority_store_metadata")) {
        return {
          rows: [{schema_version: "loopx_postgresql_authority_store_v0", store_identity: storeId}],
          rowCount: 1,
        };
      }
      return {rows: [], rowCount: 1};
    },
    release: () => {},
  };

  const database: PostgreSqlAuthorityDatabase = {
    connect: async () => connection,
  };

  const result = await setupPostgreSqlAuthoritySchemaAndRole(database, {
    storeIdentity: storeId,
    runtimeRole: "loopx_runtime_worker",
  });

  assert.equal(result.status, "installed");
  assert.equal(result.store_identity, "postgresql:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
  assert.equal(result.runtime_role, "loopx_runtime_worker");

  // Verify key DDL statements were executed
  assert.ok(executedStatements.some(s => s.includes("CREATE SCHEMA IF NOT EXISTS loopx_control_plane")));
  assert.ok(executedStatements.some(s => s.includes("CREATE TABLE IF NOT EXISTS loopx_control_plane.authority_checkpoints")));
  assert.ok(executedStatements.some(s => s.includes("CREATE TABLE IF NOT EXISTS loopx_control_plane.authority_commits")));
  assert.ok(executedStatements.some(s => s.includes("CREATE ROLE \"loopx_runtime_worker\"")));
  assert.ok(executedStatements.some(s => s.includes("GRANT USAGE ON SCHEMA loopx_control_plane TO \"loopx_runtime_worker\"")));
  assert.ok(executedStatements.some(s => s.includes("GRANT SELECT, INSERT ON\n             loopx_control_plane.authority_checkpoints")));
});

test("exportLocalToPgGenesisSeed exports sanitized cursor=1 genesis seed JSON", async () => {
  const root = await mkdtemp(join(tmpdir(), "genesis-seed-test-"));
  try {
    const goalId = "export-goal";
    const storePath = join(root, "goals", goalId, "authority");
    const store = new FileAuthorityStore(storePath, goalId);

    const past = new Date(Date.now() - 3600_000).toISOString();
    const future = new Date(Date.now() + 3600_000).toISOString();

    const commit1 = await store.commitAuthority({
      operation_id: "op-1",
      expected_provider_revision: null,
      events: [],
      receipts: [],
      next_projection: {
        schema_version: "loopx_authority_projection_v0",
        goal_id: goalId,
        leases: [
          { todo_id: "todo-1", expires_at: past, owner: "agent-old" },
          { todo_id: "todo-2", expires_at: future, owner: "agent-active" },
        ],
        todos: [
          {
            id: "todo-1",
            title: "Task with Feishu ID ou_1234567890abcdef1234567890",
            required_write_scopes: ["/Users/developer/loopx/packages/core", ".local/temp"],
          },
        ],
      },
    });
    assert.equal(commit1.status, "applied");

    const outputPath = join(root, "genesis-seed.json");
    const result = await exportLocalToPgGenesisSeed({
      runtimeRoot: root,
      goalId,
      localStore: store,
      outputPath,
      tenantId: "custom-tenant",
    });
    if (result.status !== "exported") {
      console.error("Export failed:", result);
    }

    assert.equal(result.status, "exported");
    assert.equal(result.cursor, "1");
    assert.equal(result.goal_id, goalId);
    assert.equal(result.tenant_id, "custom-tenant");
    assert.ok(result.seed_digest);
    assert.equal(result.sanitization_report?.leases_stripped, 1);

    const writtenJson = JSON.parse(await readFile(outputPath, "utf8"));
    assert.equal(writtenJson.schema_version, "loopx_pg_genesis_seed_v0");
    assert.equal(writtenJson.cursor, "1");
    assert.equal(writtenJson.projection.leases.length, 1);
    assert.equal(writtenJson.projection.leases[0].todo_id, "todo-2");
    assert.ok(!JSON.stringify(writtenJson).includes("ou_1234567890abcdef1234567890"));
    assert.ok(!JSON.stringify(writtenJson).includes(".local/temp"));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

