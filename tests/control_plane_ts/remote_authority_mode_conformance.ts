import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import type {PostgreSqlAuthorityDatabase} from "../../loopx/control_plane/coordination/postgresql_authority_store.ts";
import {executeHandoffModeSet} from "../../loopx/control_plane/coordination/handoff_mode_transaction.ts";
import {RemoteAuthorityService} from "../../loopx/control_plane/coordination/remote_authority_service.ts";
import {fileRemoteAuthorityAdmission} from "../../loopx/control_plane/coordination/remote_authority_policy.ts";
import {createRemoteAuthorityServer} from "../../loopx/control_plane/coordination/remote_authority_http.ts";
import {remoteAuthorityOperationId, type RemoteAuthorityRequest} from "../../loopx/control_plane/coordination/remote_authority_contract.ts";
import {postgresRemoteFixture, claimRequest} from "./remote_authority_fixture.ts";

export function registerRemoteAuthorityModeConformance(connectionString: string | undefined) {
  test("remote authority requires hard leases on the committing observation", {skip: !connectionString}, async t => {
    const fixture = await postgresRemoteFixture(t, connectionString!);
    for (const operation of ["claim", "release"] as const) for (const timing of ["already-soft", "after-preflight", "before-commit"]) {
      await t.test(`mode change ${timing} cannot commit a remote ${operation} under soft mode`, async child => {
        const claim = claimRequest("a", `todo_${operation}_${timing.replaceAll("-", "_")}`);
        const {request_id: _, ...scope} = claim as Extract<RemoteAuthorityRequest, {operation: "claim"}>;
        const request = operation === "claim" ? claim
          : {...scope, operation, idempotency_key: "missing-lease-key", expected_version: 0};
        await fixture.seed([request.todo_id]);
        let switched = false, headReads = 0, commitAttempts = 0;
        let switchedHead: Awaited<ReturnType<typeof fixture.store.loadAuthority>>;
        async function switchMode() {
          switched = true;
          assert.equal((await executeHandoffModeSet(fixture.store, {goal_id: request.goal_id,
            operation_id: `mode-${operation}-${timing}`, requested_mode: "soft_claim",
            observed_at: new Date().toISOString(), dry_run: false})).status, "applied");
          switchedHead = await fixture.store.loadAuthority();
        }
        if (timing === "already-soft") await switchMode();
        // Only schedule the race. Reads, mode changes, conflicts and commits
        // still use the real PostgreSQL owners and the restricted runtime role.
        const database: PostgreSqlAuthorityDatabase = {connect: async () => {
          const connection = await fixture.runtimeDatabase.connect();
          return {...connection, query: async (sql, args) => {
            if (sql.includes("INSERT INTO loopx_control_plane.authority_heads")) {
              commitAttempts++;
              if (!switched && timing === "before-commit") await switchMode();
            }
            const rows = await connection.query(sql, args);
            if (sql.includes("FROM loopx_control_plane.authority_heads") && !sql.includes("FOR UPDATE")) {
              headReads++;
              if (!switched && timing === "after-preflight") await switchMode();
            }
            return rows;
          }};
        }};
        const service = new RemoteAuthorityService({database, admit: fileRemoteAuthorityAdmission(fixture.policyPath)});
        const listener = createRemoteAuthorityServer(service, fixture);
        const endpoint = `https://127.0.0.1:${await listener.listen()}`;
        child.after(() => listener.close());
        const response = await fixture.client(endpoint, "a", request);
        assert.equal(response.code, 1);
        assert.equal(switched, true);
        assert.deepEqual(await fixture.store.loadAuthority(), switchedHead!, "rejected remote work must not write");
        assert.equal((await fixture.store.readReceipt(remoteAuthorityOperationId(request, "principal-a"))).status, "missing");
        if (timing === "already-soft") {
          assert.equal(response.value.status, "rejected");
          assert.equal(response.value.reason_code, "remote_authority_requires_hard_lease");
        } else {
          const result = response.value.result as JsonObject;
          assert.equal(response.value.status, "result");
          if (operation === "release" && timing === "before-commit") {
            assert.equal(result.status, "conflict"); // Lifecycle commands do not retry a stale observation.
          } else {
            assert.equal(result.status, "failed");
            assert.equal(result.reason_code, `${operation === "claim" ? "claim" : "lease"}_handoff_mode_mismatch`);
          }
          assert(headReads >= (operation === "claim" && timing === "before-commit" ? 3 : 2));
        }
        assert.equal(commitAttempts, timing === "before-commit" ? 1 : 0);
        const recovered = await fixture.client(endpoint, "a", request, true);
        assert.equal(recovered.code, 0); assert.equal(recovered.value.status, "missing");
        assert.deepEqual(await fixture.store.loadAuthority(), switchedHead!);
      });
    }
  });
}
