import assert from "node:assert/strict";
import test from "node:test";

import {
  POSTGRESQL_SCHEMA_VERSION,
  rotatePostgreSqlAuthorityStoreIdentity,
  PostgreSqlAuthorityStore,
  type PostgreSqlAuthorityConnection,
  type PostgreSqlAuthorityDatabase,
} from "../../loopx/control_plane/coordination/postgresql_authority_store.ts";

const STORE_IDENTITY = `postgresql:${"a".repeat(32)}`;
const NEXT_STORE_IDENTITY = `postgresql:${"b".repeat(32)}`;

type RotationQueryResult = {rows: readonly unknown[]; rowCount: number | null};

interface RotationHarness {
  readonly database: PostgreSqlAuthorityDatabase;
  readonly calls: {text: string; values: readonly unknown[] | undefined}[];
  readonly releases: (Error | undefined)[];
  readonly connectAttempts: () => number;
}

/** Scripted provider so rotation admission and transaction outcomes stay unit-testable. */
function rotationHarness(
  handler: (text: string, values: readonly unknown[] | undefined) => RotationQueryResult,
): RotationHarness {
  const calls: {text: string; values: readonly unknown[] | undefined}[] = [];
  const releases: (Error | undefined)[] = [];
  let connectAttempts = 0;
  const connection: PostgreSqlAuthorityConnection = {
    query: async (text, values) => {
      calls.push({text, values});
      return handler(text, values);
    },
    release: error => {
      releases.push(error);
    },
  };
  return {
    database: {
      connect: async () => {
        connectAttempts += 1;
        return connection;
      },
    },
    calls,
    releases,
    connectAttempts: () => connectAttempts,
  };
}

function metadataResult(storeIdentity: string): RotationQueryResult {
  return {
    rows: [{schema_version: POSTGRESQL_SCHEMA_VERSION, store_identity: storeIdentity}],
    rowCount: 1,
  };
}

test("PostgreSQL identity rotation rejects invalid and unchanged incarnations", async () => {
  const harness = rotationHarness(() => {
    throw new Error("a rejected rotation must not query the provider");
  });

  assert.deepEqual(
    await rotatePostgreSqlAuthorityStoreIdentity(
      harness.database,
      "postgresql:not-an-incarnation",
      NEXT_STORE_IDENTITY,
    ),
    {
      status: "failed",
      reason_code: "invalid_store_identity",
      reason: "PostgreSQL store identities must match postgresql:<32 lowercase hex>",
    },
  );
  assert.deepEqual(
    await rotatePostgreSqlAuthorityStoreIdentity(
      harness.database,
      STORE_IDENTITY,
      STORE_IDENTITY,
    ),
    {
      status: "failed",
      reason_code: "store_identity_unchanged",
      reason: "PostgreSQL store identity rotation requires a new incarnation",
    },
  );
  assert.equal(harness.connectAttempts(), 0);
});

test("PostgreSQL identity rotation reports an unavailable provider before BEGIN", async () => {
  const result = await rotatePostgreSqlAuthorityStoreIdentity(
    {
      connect: async () => {
        throw new Error("synthetic unavailable provider");
      },
    },
    STORE_IDENTITY,
    NEXT_STORE_IDENTITY,
  );

  assert.deepEqual(result, {
    status: "failed",
    reason_code: "provider_connection_unavailable",
    reason: "PostgreSQL connection was unavailable before identity rotation",
  });
});

test("PostgreSQL identity rotation fails closed when the stored incarnation drifted", async () => {
  const harness = rotationHarness(text =>
    text.includes("authority_store_metadata")
      ? metadataResult(NEXT_STORE_IDENTITY)
      : {rows: [], rowCount: 1}
  );

  const result = await rotatePostgreSqlAuthorityStoreIdentity(
    harness.database,
    STORE_IDENTITY,
    NEXT_STORE_IDENTITY,
  );

  assert.deepEqual(result, {
    status: "failed",
    reason_code: "store_identity_mismatch",
    reason: "PostgreSQL store identity does not match the expected database incarnation",
  });
  assert.equal(harness.calls[0]?.text, "BEGIN");
  assert.equal(harness.calls.at(-1)?.text, "ROLLBACK");
  assert.equal(
    harness.calls.some(call => call.text.includes("UPDATE loopx_control_plane")),
    false,
  );
  assert.deepEqual(harness.releases, [undefined]);
});

test("PostgreSQL identity rotation maps provider protocol violations without committing", async () => {
  const harness = rotationHarness(text =>
    text.includes("authority_store_metadata")
      ? {
        rows: [
          {schema_version: POSTGRESQL_SCHEMA_VERSION, store_identity: STORE_IDENTITY},
          {schema_version: POSTGRESQL_SCHEMA_VERSION, store_identity: STORE_IDENTITY},
        ],
        rowCount: 2,
      }
      : {rows: [], rowCount: 1}
  );

  const result = await rotatePostgreSqlAuthorityStoreIdentity(
    harness.database,
    STORE_IDENTITY,
    NEXT_STORE_IDENTITY,
  );

  assert.equal(result.status, "failed");
  if (result.status === "failed") {
    assert.equal(result.reason_code, "provider_protocol_violation");
    assert.equal(result.reason, "PostgreSQL store metadata returned more than one row");
  }
  assert.equal(harness.calls.some(call => call.text === "COMMIT"), false);
  assert.deepEqual(harness.releases, [undefined]);
});

test("PostgreSQL identity rotation reports a transaction that failed before COMMIT", async () => {
  const harness = rotationHarness(text => {
    if (text === "BEGIN") throw new Error("synthetic BEGIN failure");
    return {rows: [], rowCount: 1};
  });

  const result = await rotatePostgreSqlAuthorityStoreIdentity(
    harness.database,
    STORE_IDENTITY,
    NEXT_STORE_IDENTITY,
  );

  assert.deepEqual(result, {
    status: "failed",
    reason_code: "provider_transaction_failed",
    reason: "PostgreSQL identity rotation failed before COMMIT",
  });
  assert.equal(harness.calls.at(-1)?.text, "ROLLBACK");
  assert.deepEqual(harness.releases, [undefined]);
});

test("PostgreSQL identity rotation commits the next incarnation", async () => {
  const harness = rotationHarness(text =>
    text.includes("authority_store_metadata")
      ? metadataResult(STORE_IDENTITY)
      : {rows: [], rowCount: 1}
  );

  const result = await rotatePostgreSqlAuthorityStoreIdentity(
    harness.database,
    STORE_IDENTITY,
    NEXT_STORE_IDENTITY,
  );

  assert.deepEqual(result, {
    status: "rotated",
    previous_store_identity: STORE_IDENTITY,
    store_identity: NEXT_STORE_IDENTITY,
  });
  assert.equal(harness.calls[0]?.text, "BEGIN");
  const identityLock = harness.calls.findIndex(call =>
    call.text.includes("pg_advisory_xact_lock")
  );
  const metadataRead = harness.calls.findIndex(call =>
    call.text.includes("FROM loopx_control_plane.authority_store_metadata")
  );
  assert.ok(identityLock > 0);
  assert.ok(metadataRead > identityLock);
  const update = harness.calls.find(call =>
    call.text.includes("UPDATE loopx_control_plane.authority_store_metadata")
  );
  assert.deepEqual(update?.values, [NEXT_STORE_IDENTITY]);
  assert.equal(harness.calls.at(-1)?.text, "COMMIT");
  assert.deepEqual(harness.releases, [undefined]);
});

test("PostgreSQL store inserts checkpoints and deltas on commit and rebinds projections in scanCommitted", async () => {
  const executed: {text: string; values: readonly unknown[] | undefined}[] = [];
  const connection: PostgreSqlAuthorityConnection = {
    query: async (text, values) => {
      executed.push({text, values});
      try {
        if (text === "BEGIN" || text === "COMMIT" || text.includes("pg_advisory_xact_lock")) {
          return {rows: [], rowCount: 1};
        }
        if (text.includes("set_config('loopx.tenant_id'")) {
          return {rows: [{tenant_id: values?.[0]}], rowCount: 1};
        }
        if (text.includes("FROM loopx_control_plane.authority_store_metadata")) {
          return metadataResult(STORE_IDENTITY);
        }
        if (text.includes("FROM loopx_control_plane.authority_heads")) {
          return {
            rows: [{provider_revision: "0", cursor: "0", head: null}],
            rowCount: 1,
          };
        }
        if (text.includes("INSERT INTO loopx_control_plane.authority_checkpoints")) {
          return {rows: [], rowCount: 1};
        }
        if (text.includes("INSERT INTO loopx_control_plane.authority_commits")) {
          return {rows: [], rowCount: 1};
        }
        if (text.includes("INSERT INTO loopx_control_plane.authority_events")) {
          return {rows: [], rowCount: 1};
        }
        if (text.includes("INSERT INTO loopx_control_plane.authority_receipts")) {
          return {rows: [], rowCount: 1};
        }
        if (text.includes("UPDATE loopx_control_plane.authority_heads")) {
          return {rows: [], rowCount: 1};
        }
        if (text.includes("INSERT INTO loopx_control_plane.authority_heads")) {
          return {rows: [], rowCount: 1};
        }
        if (text.includes("FROM loopx_control_plane.authority_checkpoints")) {
          return {
            rows: [
              {
                checkpoint_cursor: "1",
                projection: {schema_version: "loopx_authority_projection_v0", counter: 1},
              },
            ],
            rowCount: 1,
          };
        }
        return {rows: [], rowCount: 0};
      } catch (e) {
        console.error("Mock query error:", e);
        throw e;
      }
    },
    release: () => {},
  };

  const database: PostgreSqlAuthorityDatabase = {
    connect: async () => connection,
  };

  const store = new PostgreSqlAuthorityStore(database, {
    tenant_id: "tenant-test",
    goal_id: "goal-test",
  });

  const commitRes = await store.commitAuthority({
    operation_id: "op-genesis",
    expected_provider_revision: null,
    events: [{kind: "init"}],
    receipts: [{test: true}],
    next_projection: {schema_version: "loopx_authority_projection_v0", counter: 1},
  });

  assert.equal(commitRes.status, "applied");
  if (commitRes.status === "applied") {
    assert.equal(commitRes.cursor, "1");
  }

  // Verify checkpoint insert was executed for cursor 1
  const checkpointCall = executed.find(c =>
    c.text.includes("INSERT INTO loopx_control_plane.authority_checkpoints")
  );
  assert.ok(checkpointCall, "Checkpoint insert must be called for genesis cursor 1");
  assert.equal(checkpointCall?.values?.[2], "1"); // checkpoint_cursor

  // Verify commit insert stored delta and null projection
  const commitCall = executed.find(c =>
    c.text.includes("INSERT INTO loopx_control_plane.authority_commits")
  );
  assert.ok(commitCall, "Commit insert must be called");
  assert.equal(commitCall?.values?.[2], "1"); // cursor
  assert.equal(commitCall?.values?.[4], "op-genesis"); // operation_id
  assert.ok(commitCall.text.includes("NULL)"), "projection in authority_commits should be literal NULL");
  assert.ok(commitCall?.values?.[5], "delta in authority_commits must be present");
});

