import assert from "node:assert/strict";
import {randomUUID} from "node:crypto";
import test from "node:test";
import {Pool, type PoolClient} from "pg";

import {
  installPostgreSqlAuthorityStoreSchema,
  type PostgreSqlAuthorityConnection,
  type PostgreSqlAuthorityDatabase,
  PostgreSqlAuthorityStore,
  rotatePostgreSqlAuthorityStoreIdentity,
} from "../../loopx/control_plane/coordination/postgresql_authority_store.ts";
import {
  PostgreSqlAuthorityService,
} from "../../loopx/control_plane/coordination/postgresql_authority_service.ts";
import {authorityStoreCommitFixture as commit} from "./authority_store_conformance.ts";

const connectionString = process.env.LOOPX_TEST_POSTGRES_SERVICE_URL;
const pool = connectionString ? new Pool({connectionString, max: 4}) : null;
const STORE_IDENTITY = `postgresql:${"c".repeat(32)}`;
const NEXT_STORE_IDENTITY = `postgresql:${"d".repeat(32)}`;

function databaseFromPool(value: Pool): PostgreSqlAuthorityDatabase {
  return {
    connect: async () => {
      const client: PoolClient = await value.connect();
      const connection: PostgreSqlAuthorityConnection = {
        query: async (text, values) =>
          await client.query(text, values ? [...values] : undefined),
        release: (error) => client.release(error),
      };
      return connection;
    },
  };
}

function pauseAfterStoreIdentityRead(database: PostgreSqlAuthorityDatabase): {
  database: PostgreSqlAuthorityDatabase;
  paused: Promise<void>;
  release: () => void;
} {
  let reportPaused: (() => void) | undefined;
  const paused = new Promise<void>(resolve => {
    reportPaused = resolve;
  });
  let release: (() => void) | undefined;
  const gate = new Promise<void>(resolve => {
    release = resolve;
  });
  let shouldPause = true;
  return {
    database: {
      connect: async () => {
        const connection = await database.connect();
        return {
          ...connection,
          query: async (text, values) => {
            const result = await connection.query(text, values);
            if (shouldPause && text.includes("authority_store_metadata")) {
              shouldPause = false;
              reportPaused?.();
              await gate;
            }
            return result;
          },
        };
      },
    },
    paused,
    release: () => release?.(),
  };
}

function databaseWithTransactionLockTimeout(
  database: PostgreSqlAuthorityDatabase,
  timeout: string,
): PostgreSqlAuthorityDatabase {
  return {
    connect: async () => {
      const connection = await database.connect();
      return {
        ...connection,
        query: async (text, values) => {
          const result = await connection.query(text, values);
          if (text === "BEGIN") {
            await connection.query("SELECT set_config('lock_timeout', $1, TRUE)", [timeout]);
          }
          return result;
        },
      };
    },
  };
}

async function cleanScope(tenantId: string, goalId: string): Promise<void> {
  if (!pool) return;
  await pool.query(
    `DELETE FROM loopx_control_plane.authority_heads
     WHERE tenant_id = $1 AND goal_id = $2`,
    [tenantId, goalId],
  );
}

if (pool) {
  const database = databaseFromPool(pool);
  const installed = installPostgreSqlAuthorityStoreSchema(database, STORE_IDENTITY);

  test("PostgreSQL commits fence identity rotation without serializing different Goals", async t => {
    await installed;
    const tenantId = `tenant-${randomUUID()}`;
    const heldGoalId = `goal-held-${randomUUID()}`;
    const parallelGoalId = `goal-parallel-${randomUUID()}`;
    t.after(() => Promise.all([
      cleanScope(tenantId, heldGoalId),
      cleanScope(tenantId, parallelGoalId),
    ]));

    const held = pauseAfterStoreIdentityRead(database);
    const heldStore = new PostgreSqlAuthorityStore(held.database, {
      tenant_id: tenantId,
      goal_id: heldGoalId,
  });
    const heldCommitPromise = heldStore.commitAuthority(
      commit(null, "held-before-rotation", 1, 1),
    );
    await held.paused;

    const boundedDatabase = databaseWithTransactionLockTimeout(database, "500ms");
    let parallelCommit;
    let blockedRotation;
    try {
      const parallelStore = new PostgreSqlAuthorityStore(boundedDatabase, {
        tenant_id: tenantId,
        goal_id: parallelGoalId,
      });
      parallelCommit = await parallelStore.commitAuthority(
        commit(null, "parallel-goal", 1, 1),
      );
      blockedRotation = await rotatePostgreSqlAuthorityStoreIdentity(
        boundedDatabase,
        STORE_IDENTITY,
        NEXT_STORE_IDENTITY,
      );
    } finally {
      held.release();
    }
    const heldCommit = await heldCommitPromise;

    assert.equal(parallelCommit?.status, "applied", JSON.stringify(parallelCommit));
    assert.equal(blockedRotation?.status, "failed", JSON.stringify(blockedRotation));
    if (blockedRotation?.status === "failed") {
      assert.equal(blockedRotation.reason_code, "provider_transaction_failed");
    }
    assert.equal(heldCommit.status, "applied", JSON.stringify(heldCommit));
    if (heldCommit.status === "applied") {
      assert.equal(heldCommit.provider_revision, `${STORE_IDENTITY}:1`);
    }
    const readback = await new PostgreSqlAuthorityStore(database, {
      tenant_id: tenantId,
      goal_id: heldGoalId,
    }).loadAuthority();
    assert.equal(readback.status, "loaded", JSON.stringify(readback));
    if (readback.status === "loaded") {
      assert.equal(readback.provider_revision, `${STORE_IDENTITY}:1`);
    }
    });

  test("PostgreSQL service admits an authorized tenant and rotates a restored incarnation", async t => {
    await installed;
    const tenantId = `tenant-${randomUUID()}`;
    const goalId = `goal-${randomUUID()}`;
    t.after(() => cleanScope(tenantId, goalId));

    const service = new PostgreSqlAuthorityService({
      database,
      authenticatePrincipal: credential =>
        credential === "fixture-credential"
          ? {status: "authenticated", principal: {principal_id: "principal-fixture"}}
          : {
            status: "rejected",
            reason_code: "credential_invalid",
            reason: "fixture credential rejected",
          },
      authorizeTenant: (principalId, requestedTenant) =>
        principalId === "principal-fixture" && requestedTenant === tenantId
          ? {status: "allowed"}
          : {
            status: "denied",
            reason_code: "tenant_not_granted",
            reason: "fixture tenant policy rejected",
          },
    });

    const opened = await service.openStore({
      credential: "fixture-credential",
      tenant_id: tenantId,
      goal_id: goalId,
      store_identity: STORE_IDENTITY,
    });
    assert.equal(opened.status, "opened", JSON.stringify(opened));
    if (opened.status !== "opened") return;
    const first = await opened.store.commitAuthority(commit(null, "service-operation", 1, 1));
    assert.equal(first.status, "applied", JSON.stringify(first));
    if (first.status !== "applied") return;

    const rotated = await rotatePostgreSqlAuthorityStoreIdentity(
      database,
      STORE_IDENTITY,
      NEXT_STORE_IDENTITY,
    );
    assert.deepEqual(rotated, {
      status: "rotated",
      previous_store_identity: STORE_IDENTITY,
      store_identity: NEXT_STORE_IDENTITY,
    });

    const stale = await opened.store.commitAuthority(
      commit(first.provider_revision, "stale-after-restore", 2, 2),
    );
    assert.equal(stale.status, "conflict", JSON.stringify(stale));
    if (stale.status === "conflict") {
      assert.equal(stale.conflict_kind, "provider_revision_mismatch");
      assert.equal(stale.current_provider_revision, `${NEXT_STORE_IDENTITY}:1`);
    }
    assert.equal((await opened.store.readReceipt("service-operation")).status, "found");

    const oldBinding = await service.openStore({
      credential: "fixture-credential",
      tenant_id: tenantId,
      goal_id: goalId,
      store_identity: STORE_IDENTITY,
    });
    assert.equal(oldBinding.status, "rejected");
    if (oldBinding.status === "rejected") {
      assert.equal(oldBinding.reason_code, "store_identity_mismatch");
    }

    const newBinding = await service.openStore({
      credential: "fixture-credential",
      tenant_id: tenantId,
      goal_id: goalId,
      store_identity: NEXT_STORE_IDENTITY,
    });
    assert.equal(newBinding.status, "opened", JSON.stringify(newBinding));
    if (newBinding.status === "opened") {
      const loaded = await newBinding.store.loadAuthority();
      assert.equal(loaded.status, "loaded", JSON.stringify(loaded));
      if (loaded.status === "loaded") {
        assert.equal(loaded.provider_revision, `${NEXT_STORE_IDENTITY}:1`);
      }
    }

    const wrongExpected = await rotatePostgreSqlAuthorityStoreIdentity(
      database,
      STORE_IDENTITY,
      `postgresql:${"e".repeat(32)}`,
    );
    assert.equal(wrongExpected.status, "failed");
    if (wrongExpected.status === "failed") {
      assert.equal(wrongExpected.reason_code, "store_identity_mismatch");
    }
  });
} else {
  test(
    "PostgreSQL service real-path integration (set LOOPX_TEST_POSTGRES_SERVICE_URL)",
    {skip: true},
    () => {},
  );
}

test.after(async () => {
  await pool?.end();
});
