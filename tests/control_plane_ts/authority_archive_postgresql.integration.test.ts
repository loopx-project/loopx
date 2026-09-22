import assert from "node:assert/strict";
import {randomUUID} from "node:crypto";
import {mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import {Pool} from "pg";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {installPostgreSqlAuthorityStoreSchema, PostgreSqlAuthorityStore,
  type PostgreSqlAuthorityDatabase} from "../../loopx/control_plane/coordination/postgresql_authority_store.ts";
import {exportAuthorityArchive, restoreAuthorityArchive} from "../../loopx/control_plane/coordination/authority_archive.ts";
import {productionScaleCoordinationFixture} from "./production_scale_coordination_fixture.ts";

const url = process.env.LOOPX_TEST_POSTGRES_URL;
for (const local of ["file", "sqlite"] as const) {
  for (const direction of ["to-postgresql", "from-postgresql"] as const) {
    test(`${local} ${direction}: portable complete graph and retained receipts`, {skip: !url}, async () => {
      const pool = new Pool({connectionString: url, max: 3});
      const database: PostgreSqlAuthorityDatabase = {connect: async () => {
        const client = await pool.connect();
        return {query: (sql, params) => client.query(sql, params ? [...params] : undefined), release: () => client.release()};
      }};
      const root = await mkdtemp(join(tmpdir(), "authority-archive-pg-"));
      try {
        await installPostgreSqlAuthorityStoreSchema(database, "postgresql:" + "b".repeat(32));
        const options = {tenant_id: `archive-${randomUUID()}`, goal_id: "goal"};
        const pg = new PostgreSqlAuthorityStore(database, options);
        const file = local === "file" ? new FileAuthorityStore(join(root, "store"), "goal")
          : new SqliteAuthorityStore(join(root, "store"), "goal");
        const [source, target]: [AuthorityStore, AuthorityStore] = direction === "to-postgresql" ? [file, pg] : [pg, file];
        const projection = productionScaleCoordinationFixture("goal", "native").projection;
        let previous: string | null = null;
        for (let i = 1; i <= 3; i++) {
          const committed = await source.commitAuthority({expected_provider_revision: previous,
            operation_id: `historical-${i}`, events: [{kind: "observation", i}],
            next_projection: {...projection, observation: i}, receipts: [{request: `same-key-${i}`, changed: false}]});
          assert.equal(committed.status, "applied");
          if (committed.status === "applied") previous = committed.provider_revision;
        }
        const archive = join(root, "archive.ndjson");
        const exported = await exportAuthorityArchive(source, "goal", archive, {pageSize: 1});
        const restored = await restoreAuthorityArchive(archive, target, exported.archive_sha256);
        assert.equal(restored.status, "restored");
        const reopened = direction === "to-postgresql" ? new PostgreSqlAuthorityStore(database, options) : target;
        const rows = await reopened.scanCommitted(null, 10);
        assert.equal(rows.status, "page");
        if (rows.status !== "page") throw new Error("readback failed");
        assert.equal(rows.transactions.length, 3);
        for (const [index, row] of rows.transactions.entries()) {
          assert.deepEqual(row.projection, {...projection, observation: index + 1});
          const receipt = await reopened.readReceipt(`historical-${index + 1}`);
          assert.equal(receipt.status, "found");
          if (receipt.status === "found") assert.deepEqual(receipt.receipts,
            [{request: `same-key-${index + 1}`, changed: false}]);
        }
        // Prove the restored native provider can continue its own CAS chain.
        assert.equal((await reopened.commitAuthority({expected_provider_revision: restored.target_provider_revision,
          operation_id: "isolated-recovery-probe", events: [], receipts: [{probe: true}],
          next_projection: {...projection, observation: 4}})).status, "applied");
        await assert.rejects(restoreAuthorityArchive(archive, reopened, exported.archive_sha256), /extra commits/);
        const unchanged = await source.loadAuthority();
        assert.equal(unchanged.status, "loaded");
        if (unchanged.status === "loaded") assert.equal(unchanged.cursor, "3");
      } finally { await pool.end(); await rm(root, {recursive: true, force: true}); }
    });
  }
}
