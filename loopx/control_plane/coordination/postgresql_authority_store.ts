import {AuthorityJournalScan} from "./authority_journal_scan.ts";
import type { JsonObject } from "../effect_program.ts";
import type {
  AuthorityStore,
  AuthorityStoreCommit,
  AuthorityStoreCommitResult,
  AuthorityStoreIdentityResult,
  AuthorityStoreLoadResult,
  AuthorityStoreReadFailure,
  AuthorityStoreReceiptResult,
  AuthorityStoreScanResult,
} from "./authority_store.ts";
import {
  AuthorityStoreProtocolError,
  canonicalAuthorityBytes,
  canonicalAuthorityObject,
  canonicalAuthorityObjectList,
  normalizeAuthorityStoreCommit,
  parseAuthorityCursor,
  requireAuthorityStoreId,
} from "./authority_store_codec.ts";

export const POSTGRESQL_STORE_IDENTITY_PATTERN = /^postgresql:[0-9a-f]{32}$/;
const POSTGRESQL_PROVIDER_REVISION_PATTERN = /^postgresql:([0-9a-f]{32}):([1-9]\d*)$/;
const POSTGRESQL_STORE_IDENTITY_LOCK_KEY = "5522768808912313929";
export const POSTGRESQL_SCHEMA_VERSION = "loopx_postgresql_authority_store_v0";
export const DEFAULT_POSTGRESQL_MAX_COMMIT_BYTES = 16 * 1024 * 1024;

/**
 * Structural subset of a server-owned PostgreSQL driver connection.
 * A node-postgres PoolClient can be wrapped without making `pg` a LoopX-core
 * dependency. The authenticated service owns the pool; Agents never receive it.
 */
export interface PostgreSqlAuthorityConnection {
  query(text: string, values?: readonly unknown[]): Promise<{
    rows: readonly unknown[];
    rowCount: number | null;
  }>;
  /** A supplied error marks unknown session state and must evict the connection. */
  release(error?: Error): void | Promise<void>;
}

export interface PostgreSqlAuthorityDatabase {
  connect(): Promise<PostgreSqlAuthorityConnection>;
}

export interface PostgreSqlAuthorityStoreOptions {
  tenant_id: string;
  goal_id: string;
  /**
   * Maximum canonical bytes admitted for one atomic commit. Deployments may
   * lower this envelope; promotion still requires separately measured
   * retention and sustained-capacity evidence.
   */
  max_commit_bytes?: number;
}

interface HeadRow extends JsonObject {
  provider_revision: string;
  cursor: string;
  head: JsonObject | null;
}

interface TransactionRow extends JsonObject {
  cursor: string;
  provider_revision: string;
  operation_id: string;
  projection: JsonObject;
  events: JsonObject[];
  receipts: JsonObject[];
}

export const POSTGRESQL_AUTHORITY_STORE_SCHEMA_SQL = `
CREATE SCHEMA IF NOT EXISTS loopx_control_plane;

CREATE TABLE IF NOT EXISTS loopx_control_plane.authority_store_metadata (
  singleton boolean PRIMARY KEY DEFAULT TRUE CHECK (singleton),
  schema_version text NOT NULL,
  store_identity text NOT NULL UNIQUE,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp()
);

CREATE TABLE IF NOT EXISTS loopx_control_plane.authority_heads (
  tenant_id text NOT NULL,
  goal_id text NOT NULL,
  provider_revision bigint NOT NULL DEFAULT 0 CHECK (provider_revision >= 0),
  cursor bigint NOT NULL DEFAULT 0 CHECK (cursor >= 0),
  head jsonb,
  updated_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  PRIMARY KEY (tenant_id, goal_id),
  CHECK ((cursor = 0 AND provider_revision = 0 AND head IS NULL) OR
         (cursor > 0 AND provider_revision > 0 AND head IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS loopx_control_plane.authority_commits (
  tenant_id text NOT NULL,
  goal_id text NOT NULL,
  cursor bigint NOT NULL CHECK (cursor > 0),
  provider_revision bigint NOT NULL CHECK (provider_revision > 0),
  operation_id text NOT NULL,
  projection jsonb NOT NULL,
  committed_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  PRIMARY KEY (tenant_id, goal_id, cursor),
  UNIQUE (tenant_id, goal_id, operation_id),
  FOREIGN KEY (tenant_id, goal_id)
    REFERENCES loopx_control_plane.authority_heads (tenant_id, goal_id)
    ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS loopx_control_plane.authority_events (
  tenant_id text NOT NULL,
  goal_id text NOT NULL,
  cursor bigint NOT NULL,
  event_index integer NOT NULL CHECK (event_index >= 0),
  event jsonb NOT NULL,
  PRIMARY KEY (tenant_id, goal_id, cursor, event_index),
  FOREIGN KEY (tenant_id, goal_id, cursor)
    REFERENCES loopx_control_plane.authority_commits (tenant_id, goal_id, cursor)
    ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS loopx_control_plane.authority_receipts (
  tenant_id text NOT NULL,
  goal_id text NOT NULL,
  cursor bigint NOT NULL,
  receipt_index integer NOT NULL CHECK (receipt_index >= 0),
  receipt jsonb NOT NULL,
  PRIMARY KEY (tenant_id, goal_id, cursor, receipt_index),
  FOREIGN KEY (tenant_id, goal_id, cursor)
    REFERENCES loopx_control_plane.authority_commits (tenant_id, goal_id, cursor)
    ON DELETE CASCADE
);

ALTER TABLE loopx_control_plane.authority_heads ENABLE ROW LEVEL SECURITY;
ALTER TABLE loopx_control_plane.authority_heads FORCE ROW LEVEL SECURITY;
ALTER TABLE loopx_control_plane.authority_commits ENABLE ROW LEVEL SECURITY;
ALTER TABLE loopx_control_plane.authority_commits FORCE ROW LEVEL SECURITY;
ALTER TABLE loopx_control_plane.authority_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE loopx_control_plane.authority_events FORCE ROW LEVEL SECURITY;
ALTER TABLE loopx_control_plane.authority_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE loopx_control_plane.authority_receipts FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS authority_heads_tenant_scope
  ON loopx_control_plane.authority_heads;
CREATE POLICY authority_heads_tenant_scope
  ON loopx_control_plane.authority_heads
  USING (tenant_id = current_setting('loopx.tenant_id', TRUE))
  WITH CHECK (tenant_id = current_setting('loopx.tenant_id', TRUE));

DROP POLICY IF EXISTS authority_commits_tenant_scope
  ON loopx_control_plane.authority_commits;
CREATE POLICY authority_commits_tenant_scope
  ON loopx_control_plane.authority_commits
  USING (tenant_id = current_setting('loopx.tenant_id', TRUE))
  WITH CHECK (tenant_id = current_setting('loopx.tenant_id', TRUE));

DROP POLICY IF EXISTS authority_events_tenant_scope
  ON loopx_control_plane.authority_events;
CREATE POLICY authority_events_tenant_scope
  ON loopx_control_plane.authority_events
  USING (tenant_id = current_setting('loopx.tenant_id', TRUE))
  WITH CHECK (tenant_id = current_setting('loopx.tenant_id', TRUE));

DROP POLICY IF EXISTS authority_receipts_tenant_scope
  ON loopx_control_plane.authority_receipts;
CREATE POLICY authority_receipts_tenant_scope
  ON loopx_control_plane.authority_receipts
  USING (tenant_id = current_setting('loopx.tenant_id', TRUE))
  WITH CHECK (tenant_id = current_setting('loopx.tenant_id', TRUE));
`;

const SELECT_HEAD_SQL = `
SELECT provider_revision::text AS provider_revision,
       cursor::text AS cursor,
       head
FROM loopx_control_plane.authority_heads
WHERE tenant_id = $1 AND goal_id = $2
`;

const SELECT_TRANSACTION_COLUMNS_SQL = `
SELECT commit.cursor::text AS cursor,
       commit.provider_revision::text AS provider_revision,
       commit.operation_id,
       commit.projection,
       COALESCE((
         SELECT jsonb_agg(event.event ORDER BY event.event_index)
         FROM loopx_control_plane.authority_events AS event
         WHERE event.tenant_id = commit.tenant_id
           AND event.goal_id = commit.goal_id
           AND event.cursor = commit.cursor
       ), '[]'::jsonb) AS events,
       COALESCE((
         SELECT jsonb_agg(receipt.receipt ORDER BY receipt.receipt_index)
         FROM loopx_control_plane.authority_receipts AS receipt
         WHERE receipt.tenant_id = commit.tenant_id
           AND receipt.goal_id = commit.goal_id
           AND receipt.cursor = commit.cursor
       ), '[]'::jsonb) AS receipts
FROM loopx_control_plane.authority_commits AS commit
`;

function rows(result: { rows: readonly unknown[] }): readonly JsonObject[] {
  return result.rows.map((row) => canonicalAuthorityObject(row, "PostgreSQL row"));
}

function oneRow(
  result: { rows: readonly unknown[] },
  name: string,
): JsonObject | null {
  const values = rows(result);
  if (values.length > 1) {
    throw new AuthorityStoreProtocolError(`${name} returned more than one row`);
  }
  return values[0] ?? null;
}

function requiredRowString(row: JsonObject, field: string): string {
  return requireAuthorityStoreId(row[field], field);
}

function decodeHeadRow(value: JsonObject): HeadRow {
  const providerRevision = requiredRowString(value, "provider_revision");
  const cursor = requiredRowString(value, "cursor");
  if (!/^\d+$/.test(providerRevision) || !/^\d+$/.test(cursor)) {
    throw new AuthorityStoreProtocolError("PostgreSQL head revision or cursor is invalid");
  }
  const parsedCursor = BigInt(cursor);
  const parsedRevision = BigInt(providerRevision);
  if (parsedCursor === 0n && parsedRevision === 0n && value.head === null) {
    return { provider_revision: providerRevision, cursor, head: null };
  }
  if (parsedCursor < 1n || parsedRevision < 1n) {
    throw new AuthorityStoreProtocolError("PostgreSQL initialized head is invalid");
  }
  return {
    provider_revision: providerRevision,
    cursor,
    head: canonicalAuthorityObject(value.head, "PostgreSQL authority head"),
  };
}

function decodeTransactionRow(value: JsonObject): TransactionRow {
  const cursor = requiredRowString(value, "cursor");
  const providerRevision = requiredRowString(value, "provider_revision");
  parseAuthorityCursor(cursor);
  if (!/^[1-9]\d*$/.test(providerRevision)) {
    throw new AuthorityStoreProtocolError("PostgreSQL provider revision is invalid");
  }
  return {
    cursor,
    provider_revision: providerRevision,
    operation_id: requiredRowString(value, "operation_id"),
    projection: canonicalAuthorityObject(value.projection, "transaction projection"),
    events: canonicalAuthorityObjectList(value.events, "transaction events"),
    receipts: canonicalAuthorityObjectList(value.receipts, "transaction receipts"),
  };
}

function providerRevisionToken(storeIdentity: string, revision: string): string {
  if (!POSTGRESQL_STORE_IDENTITY_PATTERN.test(storeIdentity)) {
    throw new AuthorityStoreProtocolError("PostgreSQL store identity is invalid");
  }
  if (!/^[1-9]\d*$/.test(revision)) {
    throw new AuthorityStoreProtocolError("PostgreSQL provider revision is invalid");
  }
  return `${storeIdentity}:${revision}`;
}

function parseExpectedRevision(value: string | null): {
  store_identity: string;
  revision: string;
} | null {
  if (value === null) return null;
  const match = POSTGRESQL_PROVIDER_REVISION_PATTERN.exec(value);
  if (!match) {
    throw new AuthorityStoreProtocolError("expected PostgreSQL provider revision is invalid");
  }
  return { store_identity: `postgresql:${match[1]!}`, revision: match[2]! };
}

function canonicalCommitEnvelopeBytes(commit: AuthorityStoreCommit): number {
  return canonicalAuthorityBytes({
    operation_id: commit.operation_id,
    events: commit.events,
    next_projection: commit.next_projection,
    receipts: commit.receipts,
  }).byteLength;
}

function readFailure(error: unknown): AuthorityStoreReadFailure {
  if (error instanceof AuthorityStoreProtocolError || error instanceof SyntaxError) {
    return {
      status: "failed",
      reason_code: "provider_protocol_violation",
      reason: error.message,
    };
  }
  return {
    status: "unavailable",
    reason_code: "provider_read_unavailable",
    reason: "PostgreSQL authority store is unavailable",
  };
}

function asError(error: unknown): Error {
  return error instanceof Error ? error : new Error("PostgreSQL connection failed");
}

async function rollback(connection: PostgreSqlAuthorityConnection): Promise<Error | null> {
  try {
    await connection.query("ROLLBACK");
    return null;
  } catch (error) {
    // A failed connection also causes PostgreSQL to discard an uncommitted
    // transaction. The caller must evict that connection from its pool.
    return asError(error);
  }
}

async function beginTenantTransaction(
  connection: PostgreSqlAuthorityConnection,
  tenantId: string,
  options: { readOnly: boolean },
): Promise<void> {
  await connection.query(options.readOnly ? "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY" : "BEGIN");
  try {
    const context = oneRow(await connection.query(
      "SELECT set_config('loopx.tenant_id', $1, TRUE) AS tenant_id",
      [tenantId],
    ), "PostgreSQL tenant context");
    if (context?.tenant_id !== tenantId) {
      throw new AuthorityStoreProtocolError(
        "PostgreSQL transaction did not retain the requested tenant context",
      );
    }
  } catch (error) {
    const rollbackError = await rollback(connection);
    if (rollbackError !== null) throw rollbackError;
    throw error;
  }
}

async function requireStoreIdentity(
  connection: PostgreSqlAuthorityConnection,
): Promise<string> {
  const metadata = oneRow(await connection.query(
    `SELECT schema_version, store_identity
     FROM loopx_control_plane.authority_store_metadata
     WHERE singleton = TRUE`,
  ), "PostgreSQL store metadata");
  if (
    metadata === null || metadata.schema_version !== POSTGRESQL_SCHEMA_VERSION ||
    !POSTGRESQL_STORE_IDENTITY_PATTERN.test(String(metadata.store_identity))
  ) {
    throw new AuthorityStoreProtocolError("PostgreSQL store metadata is not initialized");
  }
  return String(metadata.store_identity);
}

async function lockStoreIdentityTransaction(
  connection: PostgreSqlAuthorityConnection,
  mode: "shared" | "exclusive",
): Promise<void> {
  const lockFunction = mode === "shared"
    ? "pg_advisory_xact_lock_shared"
    : "pg_advisory_xact_lock";
  await connection.query(
    `SELECT ${lockFunction}($1::bigint)`,
    [POSTGRESQL_STORE_IDENTITY_LOCK_KEY],
  );
}

/**
 * Administrative schema installation. Call this only from the authenticated
 * service deployment path with a newly minted database-incarnation identity.
 */
export async function installPostgreSqlAuthorityStoreSchema(
  database: PostgreSqlAuthorityDatabase,
  storeIdentity: string,
): Promise<void> {
  if (!POSTGRESQL_STORE_IDENTITY_PATTERN.test(storeIdentity)) {
    throw new AuthorityStoreProtocolError(
      "PostgreSQL store identity must match postgresql:<32 lowercase hex>",
    );
  }
  const connection = await database.connect();
  let commitStarted = false;
  let releaseError: Error | undefined;
  try {
    await connection.query("BEGIN");
    await connection.query(POSTGRESQL_AUTHORITY_STORE_SCHEMA_SQL);
    await connection.query(
      `INSERT INTO loopx_control_plane.authority_store_metadata
         (singleton, schema_version, store_identity)
       VALUES (TRUE, $1, $2)
       ON CONFLICT (singleton) DO NOTHING`,
      [POSTGRESQL_SCHEMA_VERSION, storeIdentity],
    );
    const metadata = oneRow(await connection.query(
      `SELECT schema_version, store_identity
       FROM loopx_control_plane.authority_store_metadata
       WHERE singleton = TRUE
       FOR UPDATE`,
    ), "PostgreSQL store metadata");
    if (
      metadata === null || metadata.schema_version !== POSTGRESQL_SCHEMA_VERSION ||
      metadata.store_identity !== storeIdentity
    ) {
      throw new AuthorityStoreProtocolError(
        "PostgreSQL store identity does not match the installed database incarnation",
      );
    }
    commitStarted = true;
    await connection.query("COMMIT");
  } catch (error) {
    if (commitStarted) {
      releaseError = asError(error);
    } else {
      releaseError = (await rollback(connection)) ?? undefined;
    }
    throw error;
  } finally {
    await connection.release(releaseError);
  }
}

export type PostgreSqlAuthorityIdentityRotationResult =
  | {
    status: "rotated";
    previous_store_identity: string;
    store_identity: string;
  }
  | {
    status: "ambiguous";
    reason_code: "store_identity_rotation_outcome_unknown";
    reason: string;
  }
  | {
    status: "failed";
    reason_code:
      | "invalid_store_identity"
      | "store_identity_unchanged"
      | "provider_connection_unavailable"
      | "store_identity_mismatch"
      | "provider_protocol_violation"
      | "provider_transaction_failed";
    reason: string;
  };

class PostgreSqlAuthorityIdentityRotationRejected extends Error {
  readonly reasonCode: "store_identity_mismatch";

  constructor(reason: string) {
    super(reason);
    this.name = "PostgreSqlAuthorityIdentityRotationRejected";
    this.reasonCode = "store_identity_mismatch";
  }
}

/**
 * Rotate the service-managed database incarnation after a restore. Revision
 * tokens minted before the rotation become unusable because their opaque
 * identity no longer matches; no Goal or operation state is rewritten.
 */
export async function rotatePostgreSqlAuthorityStoreIdentity(
  database: PostgreSqlAuthorityDatabase,
  expectedStoreIdentity: string,
  nextStoreIdentity: string,
): Promise<PostgreSqlAuthorityIdentityRotationResult> {
  if (!POSTGRESQL_STORE_IDENTITY_PATTERN.test(expectedStoreIdentity) ||
      !POSTGRESQL_STORE_IDENTITY_PATTERN.test(nextStoreIdentity)) {
    return {
      status: "failed",
      reason_code: "invalid_store_identity",
      reason: "PostgreSQL store identities must match postgresql:<32 lowercase hex>",
    };
  }
  if (expectedStoreIdentity === nextStoreIdentity) {
    return {
      status: "failed",
      reason_code: "store_identity_unchanged",
      reason: "PostgreSQL store identity rotation requires a new incarnation",
    };
  }

  let connection: PostgreSqlAuthorityConnection;
  try {
    connection = await database.connect();
  } catch {
    return {
      status: "failed",
      reason_code: "provider_connection_unavailable",
      reason: "PostgreSQL connection was unavailable before identity rotation",
    };
  }

  let commitStarted = false;
  let releaseError: Error | undefined;
  try {
    await connection.query("BEGIN");
    await lockStoreIdentityTransaction(connection, "exclusive");
    const metadata = oneRow(await connection.query(
      `SELECT schema_version, store_identity
       FROM loopx_control_plane.authority_store_metadata
       WHERE singleton = TRUE
       FOR UPDATE`,
    ), "PostgreSQL store metadata");
    if (
      metadata === null || metadata.schema_version !== POSTGRESQL_SCHEMA_VERSION ||
      !POSTGRESQL_STORE_IDENTITY_PATTERN.test(String(metadata.store_identity)) ||
      metadata.store_identity !== expectedStoreIdentity
    ) {
      throw new PostgreSqlAuthorityIdentityRotationRejected(
        "PostgreSQL store identity does not match the expected database incarnation",
      );
    }
    await connection.query(
      `UPDATE loopx_control_plane.authority_store_metadata
       SET store_identity = $1
       WHERE singleton = TRUE`,
      [nextStoreIdentity],
    );
    commitStarted = true;
    await connection.query("COMMIT");
    return {
      status: "rotated",
      previous_store_identity: expectedStoreIdentity,
      store_identity: nextStoreIdentity,
    };
  } catch (error) {
    if (commitStarted) {
      releaseError = asError(error);
      return {
        status: "ambiguous",
        reason_code: "store_identity_rotation_outcome_unknown",
        reason: "PostgreSQL identity rotation outcome is unknown; read store metadata before retrying",
      };
    }
    releaseError = (await rollback(connection)) ?? undefined;
    if (error instanceof PostgreSqlAuthorityIdentityRotationRejected) {
      return {status: "failed", reason_code: error.reasonCode, reason: error.message};
    }
    return {
      status: "failed",
      reason_code: error instanceof AuthorityStoreProtocolError
        ? "provider_protocol_violation"
        : "provider_transaction_failed",
      reason: error instanceof AuthorityStoreProtocolError
        ? error.message
        : "PostgreSQL identity rotation failed before COMMIT",
    };
  } finally {
    await connection.release(releaseError);
  }
}

/** PostgreSQL Stage 2B store; domain decisions remain in LoopX authority. */
export class PostgreSqlAuthorityStore implements AuthorityStore {
  readonly providerKind = "postgresql" as const;
  readonly database: PostgreSqlAuthorityDatabase;
  readonly tenantId: string;
  readonly goalId: string;
  readonly maxCommitBytes: number;

  constructor(
    database: PostgreSqlAuthorityDatabase,
    options: PostgreSqlAuthorityStoreOptions,
  ) {
    this.database = database;
    this.tenantId = requireAuthorityStoreId(options.tenant_id, "tenant id");
    this.goalId = requireAuthorityStoreId(options.goal_id, "goal id");
    this.maxCommitBytes = options.max_commit_bytes ?? DEFAULT_POSTGRESQL_MAX_COMMIT_BYTES;
    if (!Number.isSafeInteger(this.maxCommitBytes) || this.maxCommitBytes < 1) {
      throw new AuthorityStoreProtocolError(
        "PostgreSQL max commit bytes must be a positive safe integer",
      );
    }
  }

  private async connect(): Promise<PostgreSqlAuthorityConnection> {
    return await this.database.connect();
  }

  private async readInTenantTransaction<T>(
    operation: (connection: PostgreSqlAuthorityConnection) => Promise<T>,
  ): Promise<T> {
    const connection = await this.connect();
    let transactionOpen = false;
    let releaseError: Error | undefined;
    try {
      await beginTenantTransaction(connection, this.tenantId, { readOnly: true });
      transactionOpen = true;
      const result = await operation(connection);
      const rollbackError = await rollback(connection);
      transactionOpen = false;
      if (rollbackError !== null) {
        releaseError = rollbackError;
        throw rollbackError;
      }
      return result;
    } catch (error) {
      if (transactionOpen) {
        releaseError = (await rollback(connection)) ?? undefined;
      } else if (releaseError === undefined) {
        // BEGIN or tenant-context setup may have failed after reaching the
        // server. Do not return a session with unknown state to the pool.
        releaseError = asError(error);
      }
      throw error;
    } finally {
      await connection.release(releaseError);
    }
  }

  async storeIdentity(): Promise<AuthorityStoreIdentityResult> {
    let connection: PostgreSqlAuthorityConnection | null = null;
    let releaseError: Error | undefined;
    try {
      connection = await this.connect();
      return { status: "available", store_identity: await requireStoreIdentity(connection) };
    } catch (error) {
      releaseError = asError(error);
      const failure = readFailure(error);
      return failure.status === "failed"
        ? { ...failure, reason_code: "store_identity_invalid" }
        : { ...failure, reason_code: "store_identity_unavailable" };
    } finally {
      await connection?.release(releaseError);
    }
  }

  async loadAuthority(): Promise<AuthorityStoreLoadResult> {
    try {
      return await this.readInTenantTransaction(async (connection) => {
        const storeIdentity = await requireStoreIdentity(connection);
        const value = oneRow(
          await connection.query(SELECT_HEAD_SQL, [this.tenantId, this.goalId]),
          "PostgreSQL authority head",
        );
        if (value === null) return { status: "missing" } as const;
        const head = decodeHeadRow(value);
        if (head.head === null) return { status: "missing" } as const;
        return {
          status: "loaded",
          head: structuredClone(head.head),
          provider_revision: providerRevisionToken(storeIdentity, head.provider_revision),
          cursor: head.cursor,
        } as const;
      });
    } catch (error) {
      return readFailure(error);
    }
  }

  async commitAuthority(commit: AuthorityStoreCommit): Promise<AuthorityStoreCommitResult> {
    let normalized: AuthorityStoreCommit;
    let expectedRevision: ReturnType<typeof parseExpectedRevision>;
    try {
      normalized = normalizeAuthorityStoreCommit(commit);
      expectedRevision = parseExpectedRevision(normalized.expected_provider_revision);
    } catch (error) {
      return {
        status: "failed",
        reason_code: "invalid_commit_request",
        reason: error instanceof Error ? error.message : "invalid commit request",
      };
    }
    const commitBytes = canonicalCommitEnvelopeBytes(normalized);
    if (commitBytes > this.maxCommitBytes) {
      return {
        status: "failed",
        reason_code: "store_capacity_exhausted",
        reason:
          `PostgreSQL authority commit is ${commitBytes} canonical bytes; configured maximum is ${this.maxCommitBytes}`,
      };
    }

    let connection: PostgreSqlAuthorityConnection;
    try {
      connection = await this.connect();
    } catch {
      return {
        status: "failed",
        reason_code: "provider_connection_unavailable",
        reason: "PostgreSQL connection was unavailable before the transaction",
      };
    }

    let commitStarted = false;
    let releaseError: Error | undefined;
    try {
      await beginTenantTransaction(connection, this.tenantId, { readOnly: false });
      // Keep the identity lock ahead of the per-Goal head lock. Shared mode
      // preserves cross-Goal concurrency while rotation takes exclusive mode.
      await lockStoreIdentityTransaction(connection, "shared");
      const storeIdentity = await requireStoreIdentity(connection);
      await connection.query(
        `INSERT INTO loopx_control_plane.authority_heads
           (tenant_id, goal_id, provider_revision, cursor, head)
         VALUES ($1, $2, 0, 0, NULL)
         ON CONFLICT (tenant_id, goal_id) DO NOTHING`,
        [this.tenantId, this.goalId],
      );
      const rawHead = oneRow(await connection.query(
        `${SELECT_HEAD_SQL} FOR UPDATE`,
        [this.tenantId, this.goalId],
      ), "locked PostgreSQL authority head");
      if (rawHead === null) {
        throw new AuthorityStoreProtocolError("PostgreSQL authority head lock disappeared");
      }
      const current = decodeHeadRow(rawHead);
      const currentRevision = current.head === null ? null : current.provider_revision;
      if (
        currentRevision !== (expectedRevision?.revision ?? null) ||
        (expectedRevision !== null && expectedRevision.store_identity !== storeIdentity)
      ) {
        releaseError = (await rollback(connection)) ?? undefined;
        return {
          status: "conflict",
          conflict_kind: "provider_revision_mismatch",
          current_provider_revision: currentRevision === null
            ? null
            : providerRevisionToken(storeIdentity, currentRevision),
          current_cursor: current.head === null ? null : current.cursor,
        };
      }

      const existing = oneRow(await connection.query(
        `SELECT cursor::text AS cursor, provider_revision::text AS provider_revision
         FROM loopx_control_plane.authority_commits
         WHERE tenant_id = $1 AND goal_id = $2 AND operation_id = $3`,
        [this.tenantId, this.goalId, normalized.operation_id],
      ), "PostgreSQL operation identity");
      if (existing !== null) {
        releaseError = (await rollback(connection)) ?? undefined;
        return {
          status: "conflict",
          conflict_kind: "operation_id_exists",
          current_provider_revision: currentRevision === null
            ? null
            : providerRevisionToken(storeIdentity, currentRevision),
          current_cursor: current.head === null ? null : current.cursor,
        };
      }

      const nextRevision = (BigInt(current.provider_revision) + 1n).toString();
      const nextCursor = (BigInt(current.cursor) + 1n).toString();
      await connection.query(
        `INSERT INTO loopx_control_plane.authority_commits
           (tenant_id, goal_id, cursor, provider_revision, operation_id, projection)
         VALUES ($1, $2, $3::bigint, $4::bigint, $5, $6::jsonb)`,
        [
          this.tenantId,
          this.goalId,
          nextCursor,
          nextRevision,
          normalized.operation_id,
          JSON.stringify(normalized.next_projection),
        ],
      );
      for (const [index, event] of normalized.events.entries()) {
        await connection.query(
          `INSERT INTO loopx_control_plane.authority_events
             (tenant_id, goal_id, cursor, event_index, event)
           VALUES ($1, $2, $3::bigint, $4, $5::jsonb)`,
          [this.tenantId, this.goalId, nextCursor, index, JSON.stringify(event)],
        );
      }
      for (const [index, receipt] of normalized.receipts.entries()) {
        await connection.query(
          `INSERT INTO loopx_control_plane.authority_receipts
             (tenant_id, goal_id, cursor, receipt_index, receipt)
           VALUES ($1, $2, $3::bigint, $4, $5::jsonb)`,
          [this.tenantId, this.goalId, nextCursor, index, JSON.stringify(receipt)],
        );
      }
      const updated = await connection.query(
        `UPDATE loopx_control_plane.authority_heads
         SET provider_revision = $3::bigint,
             cursor = $4::bigint,
             head = $5::jsonb,
             updated_at = transaction_timestamp()
         WHERE tenant_id = $1 AND goal_id = $2 AND provider_revision = $6::bigint`,
        [
          this.tenantId,
          this.goalId,
          nextRevision,
          nextCursor,
          JSON.stringify(normalized.next_projection),
          current.provider_revision,
        ],
      );
      if (updated.rowCount !== 1) {
        throw new AuthorityStoreProtocolError("locked PostgreSQL head revision changed");
      }
      commitStarted = true;
      await connection.query("COMMIT");
      return {
        status: "applied",
        provider_revision: providerRevisionToken(storeIdentity, nextRevision),
        cursor: nextCursor,
      };
    } catch (error) {
      if (commitStarted) {
        releaseError = asError(error);
        return {
          status: "ambiguous",
          reason_code: "commit_outcome_unknown",
          reason: "PostgreSQL COMMIT outcome is unknown; reconcile by operation receipt",
        };
      }
      releaseError = (await rollback(connection)) ?? undefined;
      return {
        status: "failed",
        reason_code: error instanceof AuthorityStoreProtocolError
          ? "provider_protocol_violation"
          : "provider_transaction_failed",
        reason: error instanceof AuthorityStoreProtocolError
          ? error.message
          : "PostgreSQL transaction failed before COMMIT",
      };
    } finally {
      await connection.release(releaseError);
    }
  }

  async readReceipt(operationId: string): Promise<AuthorityStoreReceiptResult> {
    let normalized: string;
    try {
      normalized = requireAuthorityStoreId(operationId, "operation id");
    } catch (error) {
      return {
        status: "failed",
        reason_code: "invalid_operation_id",
        reason: error instanceof Error ? error.message : "invalid operation id",
      };
    }
    try {
      return await this.readInTenantTransaction(async (connection) => {
        const storeIdentity = await requireStoreIdentity(connection);
        const value = oneRow(await connection.query(
          `${SELECT_TRANSACTION_COLUMNS_SQL}
           WHERE commit.tenant_id = $1 AND commit.goal_id = $2 AND commit.operation_id = $3`,
          [this.tenantId, this.goalId, normalized],
        ), "PostgreSQL authority receipt");
        if (value === null) return { status: "missing" } as const;
        const transaction = decodeTransactionRow(value);
        return {
          status: "found",
          cursor: transaction.cursor,
          provider_revision: providerRevisionToken(storeIdentity, transaction.provider_revision),
          receipts: structuredClone(transaction.receipts),
        } as const;
      });
    } catch (error) {
      return readFailure(error);
    }
  }

  async scanCommitted(
    afterCursor: string | null,
    limit: number,
  ): Promise<AuthorityStoreScanResult> {
    const scan = AuthorityJournalScan.prepare(afterCursor, limit);
    if (!(scan instanceof AuthorityJournalScan)) return scan;
    try {
      return await this.readInTenantTransaction(async (connection) => {
        const storeIdentity = await requireStoreIdentity(connection);
        const current = oneRow(
          await connection.query(SELECT_HEAD_SQL, [this.tenantId, this.goalId]),
          "PostgreSQL authority head",
        );
        if (current === null) return scan.page([], null);
        const head = decodeHeadRow(current);
        const snapshot = head.head === null ? null : {cursor: head.cursor,
          provider_revision: providerRevisionToken(storeIdentity, head.provider_revision), head: head.head};
        const range = scan.rangeFailure(snapshot?.cursor ?? null);
        if (range) return range;
        const result = rows(await connection.query(
          `${SELECT_TRANSACTION_COLUMNS_SQL}
           WHERE commit.tenant_id = $1 AND commit.goal_id = $2 AND commit.cursor > $3::bigint
           ORDER BY commit.cursor
           LIMIT $4`,
          [this.tenantId, this.goalId, scan.offset.toString(), (BigInt(limit) + 1n).toString()],
        )).map(decodeTransactionRow);
        const transactions = result.map(value => ({...value,
          provider_revision: providerRevisionToken(storeIdentity, value.provider_revision)}));
        return scan.page(transactions, snapshot);
      });
    } catch (error) {
      return readFailure(error);
    }
  }
}
