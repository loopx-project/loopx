import assert from "node:assert/strict";
import test from "node:test";

import {
  POSTGRESQL_SCHEMA_VERSION,
  POSTGRESQL_STORE_IDENTITY_PATTERN,
  rotatePostgreSqlAuthorityStoreIdentity,
  type PostgreSqlAuthorityConnection,
  type PostgreSqlAuthorityDatabase,
} from "../../loopx/control_plane/coordination/postgresql_authority_store.ts";
import {
  PostgreSqlAuthorityService,
  type PostgreSqlPrincipalAuthenticationResult,
  type PostgreSqlAuthorityServiceOpenRequest,
  type PostgreSqlTenantAuthorizationResult,
} from "../../loopx/control_plane/coordination/postgresql_authority_service.ts";
import {
  POSTGRESQL_AUTHORITY_SERVICE_FIXTURE_SCHEMA,
  postgresqlAuthorityServiceFixture,
} from "./postgresql_authority_service_fixture.ts";

const STORE_IDENTITY = `postgresql:${"a".repeat(32)}`;
const OTHER_STORE_IDENTITY = `postgresql:${"b".repeat(32)}`;
const TENANT_ID = "tenant-service-fixture";
const GOAL_ID = "goal-service-fixture";

function metadataDatabase(
  storeIdentity: string,
  options: {onConnect?: () => void; onQuery?: (text: string) => void} = {},
): PostgreSqlAuthorityDatabase {
  return {
    connect: async () => {
      options.onConnect?.();
      const connection: PostgreSqlAuthorityConnection = {
        query: async (text) => {
          options.onQuery?.(text);
          if (text.includes("authority_store_metadata")) {
            return {
              rows: [{
                schema_version: POSTGRESQL_SCHEMA_VERSION,
                store_identity: storeIdentity,
              }],
              rowCount: 1,
            };
          }
          throw new Error(`unexpected PostgreSQL service query: ${text}`);
        },
        release: () => {},
      };
      return connection;
    },
  };
}

function principalResult(
  principalId = "principal-service-fixture",
): PostgreSqlPrincipalAuthenticationResult {
  return {status: "authenticated", principal: {principal_id: principalId}};
}

function allowedTenant(): PostgreSqlTenantAuthorizationResult {
  return {status: "allowed"};
}

test("PostgreSQL service fixture is public-safe and versioned", () => {
  assert.equal(
    postgresqlAuthorityServiceFixture.schema_version,
    POSTGRESQL_AUTHORITY_SERVICE_FIXTURE_SCHEMA,
  );
  assert.equal(postgresqlAuthorityServiceFixture.source_authority, "postgresql_v0");
  assert.equal(postgresqlAuthorityServiceFixture.credential_persistence, "forbidden");
  assert.equal(postgresqlAuthorityServiceFixture.agent_database_access, "forbidden");
  assert.deepEqual(
    postgresqlAuthorityServiceFixture.principal_cases.map(item => item.id),
    ["authenticated-tenant", "unauthenticated", "tenant-denied", "identity-drift"],
  );
});

test("PostgreSQL service rejects malformed requests before authentication", async () => {
  let authenticationCalls = 0;
  const service = new PostgreSqlAuthorityService({
    database: metadataDatabase(STORE_IDENTITY),
    authenticatePrincipal: () => {
      authenticationCalls += 1;
      return principalResult();
    },
    authorizeTenant: allowedTenant,
  });

  const result = await service.openStore({
    credential: {secret: "opaque"},
    tenant_id: TENANT_ID,
    goal_id: GOAL_ID,
    store_identity: "postgresql:not-an-incarnation",
  });
  assert.equal(result.status, "rejected");
  if (result.status === "rejected") assert.equal(result.reason_code, "invalid_service_request");
  assert.equal(authenticationCalls, 0);
});

test("PostgreSQL service authenticates and authorizes before opening the provider", async () => {
  let connectionAttempts = 0;
  let seenCredential: unknown;
  let authorizedPrincipal: string | null = null;
  let authorizedTenant: string | null = null;
  const credential = {kind: "opaque", token: "never-persist"};
  const service = new PostgreSqlAuthorityService({
    database: metadataDatabase(STORE_IDENTITY, {onConnect: () => connectionAttempts += 1}),
    authenticatePrincipal: received => {
      seenCredential = received;
      return principalResult();
    },
    authorizeTenant: (principalId, tenantId) => {
      authorizedPrincipal = principalId;
      authorizedTenant = tenantId;
      return allowedTenant();
    },
  });

  const result = await service.openStore({
    credential,
    tenant_id: TENANT_ID,
    goal_id: GOAL_ID,
    store_identity: STORE_IDENTITY,
  });
  assert.equal(result.status, "opened");
  if (result.status !== "opened") return;
  assert.equal(result.provider, "postgresql");
  assert.equal(result.principal_id, "principal-service-fixture");
  assert.equal(result.store_identity, STORE_IDENTITY);
  assert.strictEqual(seenCredential, credential);
  assert.equal(authorizedPrincipal, "principal-service-fixture");
  assert.equal(authorizedTenant, TENANT_ID);
  assert.equal(connectionAttempts, 1);
});

test("PostgreSQL service fails closed for authentication, tenant, and identity drift", async () => {
  let connectionAttempts = 0;
  const database = metadataDatabase(STORE_IDENTITY, {onConnect: () => connectionAttempts += 1});

  const unauthenticated = new PostgreSqlAuthorityService({
    database,
    authenticatePrincipal: () => ({
      status: "rejected",
      reason_code: "credential_invalid",
      reason: "synthetic credential is invalid",
    }),
    authorizeTenant: allowedTenant,
  });
  const rejectedPrincipal = await unauthenticated.openStore({
    credential: "invalid",
    tenant_id: TENANT_ID,
    goal_id: GOAL_ID,
    store_identity: STORE_IDENTITY,
  });
  assert.deepEqual(
    rejectedPrincipal,
    {
      status: "rejected",
      reason_code: "principal_unauthenticated",
      reason: "PostgreSQL service principal authentication was rejected",
      tenant_id: TENANT_ID,
      goal_id: GOAL_ID,
    },
  );
  assert.equal(connectionAttempts, 0);

  const denied = new PostgreSqlAuthorityService({
    database,
    authenticatePrincipal: () => principalResult(),
    authorizeTenant: () => ({
      status: "denied",
      reason_code: "tenant_not_granted",
      reason: "synthetic tenant policy denied the request",
    }),
  });
  const rejectedTenant = await denied.openStore({
    credential: "valid",
    tenant_id: TENANT_ID,
    goal_id: GOAL_ID,
    store_identity: STORE_IDENTITY,
  });
  assert.equal(rejectedTenant.status, "rejected");
  if (rejectedTenant.status === "rejected") {
    assert.equal(rejectedTenant.reason_code, "tenant_unauthorized");
    assert.equal(rejectedTenant.principal_id, "principal-service-fixture");
  }
  assert.equal(connectionAttempts, 0);

  const drifted = new PostgreSqlAuthorityService({
    database,
    authenticatePrincipal: () => principalResult(),
    authorizeTenant: allowedTenant,
  });
  const rejectedIdentity = await drifted.openStore({
    credential: "valid",
    tenant_id: TENANT_ID,
    goal_id: GOAL_ID,
    store_identity: OTHER_STORE_IDENTITY,
  });
  assert.equal(rejectedIdentity.status, "rejected");
  if (rejectedIdentity.status === "rejected") {
    assert.equal(rejectedIdentity.reason_code, "store_identity_mismatch");
    assert.equal(rejectedIdentity.principal_id, "principal-service-fixture");
  }
  assert.equal(connectionAttempts, 1);
});

test("PostgreSQL service rejects malformed memberships before opening the provider", async () => {
  let connectionAttempts = 0;
  const database = metadataDatabase(STORE_IDENTITY, {
    onConnect: () => connectionAttempts += 1,
  });
  const request = {
    credential: {kind: "opaque", token: "never-persist"},
    tenant_id: TENANT_ID,
    goal_id: GOAL_ID,
    store_identity: STORE_IDENTITY,
  };

  const malformed = new PostgreSqlAuthorityService({
    database,
    authenticatePrincipal: () => principalResult(),
    authorizeTenant: allowedTenant,
  });
  const nullRequest = await malformed.openStore(
    null as unknown as PostgreSqlAuthorityServiceOpenRequest,
  );
  assert.deepEqual(nullRequest, {
    status: "rejected",
    reason_code: "invalid_service_request",
    reason: "PostgreSQL service request is invalid",
  });
  const untrimmedTenant = await malformed.openStore({
    ...request,
    tenant_id: ` ${TENANT_ID}`,
  });
  assert.equal(untrimmedTenant.status, "rejected");
  if (untrimmedTenant.status === "rejected") {
    assert.equal(untrimmedTenant.reason_code, "invalid_service_request");
    assert.equal(untrimmedTenant.reason, "tenant id must be a non-empty trimmed string");
  }

  const unverified = new PostgreSqlAuthorityService({
    database,
    authenticatePrincipal: () => {
      throw new Error("synthetic verifier outage");
    },
    authorizeTenant: allowedTenant,
  });
  assert.deepEqual(await unverified.openStore(request), {
    status: "rejected",
    reason_code: "principal_verification_unavailable",
    reason: "PostgreSQL service could not verify the principal",
    tenant_id: TENANT_ID,
    goal_id: GOAL_ID,
  });

  const invalidPrincipal = new PostgreSqlAuthorityService({
    database,
    authenticatePrincipal: () => principalResult(""),
    authorizeTenant: allowedTenant,
  });
  assert.deepEqual(await invalidPrincipal.openStore(request), {
    status: "rejected",
    reason_code: "principal_unauthenticated",
    reason: "PostgreSQL service returned an invalid authenticated principal",
    tenant_id: TENANT_ID,
    goal_id: GOAL_ID,
  });

  const unavailableAuthorization = new PostgreSqlAuthorityService({
    database,
    authenticatePrincipal: () => principalResult(),
    authorizeTenant: () => {
      throw new Error("synthetic authorization outage");
    },
  });
  assert.deepEqual(await unavailableAuthorization.openStore(request), {
    status: "rejected",
    reason_code: "tenant_authorization_unavailable",
    reason: "PostgreSQL service could not authorize the tenant",
    principal_id: "principal-service-fixture",
    tenant_id: TENANT_ID,
    goal_id: GOAL_ID,
  });
  assert.equal(connectionAttempts, 0);
});

test("PostgreSQL service treats provider metadata failures as unavailable", async () => {
  const service = new PostgreSqlAuthorityService({
    database: {
      connect: async () => {
        throw new Error("synthetic unavailable provider");
      },
    },
    authenticatePrincipal: () => principalResult(),
    authorizeTenant: allowedTenant,
  });
  const result = await service.openStore({
    credential: "valid",
    tenant_id: TENANT_ID,
    goal_id: GOAL_ID,
    store_identity: STORE_IDENTITY,
  });
  assert.equal(result.status, "rejected");
  if (result.status === "rejected") assert.equal(result.reason_code, "store_identity_unavailable");
});

test("PostgreSQL identity rotation reports a lost COMMIT response as ambiguous", async () => {
  let releasedWith: Error | undefined;
  const database: PostgreSqlAuthorityDatabase = {
    connect: async () => {
      const connection: PostgreSqlAuthorityConnection = {
        query: async text => {
          if (
            text === "BEGIN" ||
            text.includes("pg_advisory_xact_lock") ||
            text.includes("UPDATE loopx_control_plane.authority_store_metadata")
          ) {
            return {rows: [], rowCount: 1};
          }
          if (text.includes("authority_store_metadata")) {
            return {
              rows: [{
                schema_version: POSTGRESQL_SCHEMA_VERSION,
                store_identity: STORE_IDENTITY,
              }],
              rowCount: 1,
            };
          }
          if (text === "COMMIT") throw new Error("synthetic response loss after COMMIT");
          throw new Error(`unexpected PostgreSQL rotation query: ${text}`);
        },
        release: error => {
          releasedWith = error;
        },
      };
      return connection;
    },
  };

  const result = await rotatePostgreSqlAuthorityStoreIdentity(
    database,
    STORE_IDENTITY,
    OTHER_STORE_IDENTITY,
  );
  assert.deepEqual(result, {
    status: "ambiguous",
    reason_code: "store_identity_rotation_outcome_unknown",
    reason: "PostgreSQL identity rotation outcome is unknown; read store metadata before retrying",
  });
  assert.match(releasedWith?.message ?? "", /response loss/);
});

test("PostgreSQL service identity format stays provider-specific", () => {
  assert.equal(POSTGRESQL_STORE_IDENTITY_PATTERN.test(STORE_IDENTITY), true);
  assert.equal(POSTGRESQL_STORE_IDENTITY_PATTERN.test(OTHER_STORE_IDENTITY), true);
  assert.equal(POSTGRESQL_STORE_IDENTITY_PATTERN.test("sqlite:abc"), false);
});

const realPathUrl = process.env.LOOPX_TEST_POSTGRES_URL;
if (realPathUrl) {
  test("PostgreSQL service real-path integration (set LOOPX_TEST_POSTGRES_URL)", async () => {
    // The disposable PostgreSQL integration is intentionally kept in the
    // provider integration suite; this test documents the required real-path
    // command without sharing mutable singleton metadata with that suite.
    assert.match(realPathUrl, /^postgres(?:ql)?:\/\//);
  });
} else {
  test("PostgreSQL service real-path integration (set LOOPX_TEST_POSTGRES_URL)", {skip: true}, () => {});
}
