import assert from "node:assert/strict";
import test from "node:test";
import {existsSync} from "node:fs";
import {mkdir, mkdtemp, readFile, rename, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {dirname, join} from "node:path";
import {authorityBackupDirectory, captureAuthorityBackup, inspectAuthorityBackup} from "../../loopx/control_plane/coordination/authority_backup.ts";
import {verifyAuthorityArchive} from "../../loopx/control_plane/coordination/authority_archive.ts";
import {openLocalAuthorityStore, localAuthorityProviderPaths, selectLocalSqliteAuthority} from "../../loopx/control_plane/coordination/local_authority_provider.ts";
import {legacyCoordinationWriterFencePath, COLD_SOURCE_IMPORT_WRITER_FENCE_SCHEMA} from "../../loopx/control_plane/coordination/legacy_writer_fence.ts";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";

for (const provider of ["file", "sqlite"] as const) {
  test(`${provider} owner capture retains its inspected checkpoint after a later append and source loss`, async () => {
    const root = await mkdtemp(join(tmpdir(), "loopx-authority-backup-"));
    try {
      const source = join(root, "source");
      if (provider === "sqlite") await selectLocalSqliteAuthority(source, "goal-a", true);
      const store = await openLocalAuthorityStore(source, "goal-a");
      const projection = (value: number) => authorityProjectionFixture("goal-a", [], [], "native", {value});
      const first = await store.commitAuthority({expected_provider_revision: null, operation_id: "first",
        events: [], receipts: [{operation_id: "first", retained: true}], next_projection: projection(1)});
      assert.equal(first.status, "applied");
      let appended = false;
      // Wrap only scheduling of a real provider read. Both commits and the
      // exporter use the real File/SQLite owner and its retained journal.
      const interleaved = new Proxy(store, {get(target, key) {
        if (key === "scanCommitted") return async (...args: Parameters<AuthorityStore["scanCommitted"]>) => {
          const page = await target.scanCommitted(...args);
          if (!appended) {
            appended = true;
            const second = await target.commitAuthority({expected_provider_revision: first.provider_revision!,
              operation_id: "second", events: [], receipts: [], next_projection: projection(2)});
            assert.equal(second.status, "applied");
          }
          return page;
        };
        const value = Reflect.get(target, key);
        return typeof value === "function" ? value.bind(target) : value;
      }});
      const destination = join(root, "capture");
      const result = await captureAuthorityBackup({runtime_root: source, goal_id: "goal-a", destination},
        {createStore: () => interleaved});
      assert.equal(result.state, "readable");
      const archivePath = join(destination, authorityBackupDirectory("goal-a"), "authority.jsonl");
      const exported = await verifyAuthorityArchive(archivePath);
      assert.equal(exported.commits, "1");
      const later = await store.loadAuthority();
      assert.equal(later.status, "loaded");
      assert.equal(later.cursor, "2");
      assert.equal(appended, true);
      await rename(source, join(root, "unavailable-source"));
      const bytes = await readFile(archivePath);
      const inspected = await inspectAuthorityBackup(destination, result);
      assert.equal(inspected.provider_revision, first.provider_revision);
      assert.equal(inspected.cursor, "1");
      assert.equal(inspected.history_commits, 1);
      // A manifest's claimed position cannot replace the archive owner's
      // verified source position, even when it names a real later revision.
      await assert.rejects(() => inspectAuthorityBackup(destination, {...result,
        archive: {...exported, source_provider_revision: later.provider_revision}}), /summary mismatch/);
      assert.deepEqual(await readFile(archivePath), bytes);
      assert.equal(existsSync(source), false);
    } finally { await rm(root, {recursive: true, force: true}); }
  });
}

for (const moved of ["selector", "writer_fence", "identity"] as const) {
  test(`owner capture refuses a moved ${moved} and keeps the source change`, async () => {
    const root = await mkdtemp(join(tmpdir(), "loopx-authority-backup-"));
    try {
      const store = await openLocalAuthorityStore(root, "goal-a");
      await store.commitAuthority({expected_provider_revision: null, operation_id: "first",
        events: [], receipts: [], next_projection: {goal_id: "goal-a"}});
      const identity = await store.storeIdentity();
      assert.equal(identity.status, "available");
      const paths = localAuthorityProviderPaths(root, "goal-a");
      const changedPath = moved === "selector" ? paths.marker : moved === "writer_fence"
        ? legacyCoordinationWriterFencePath(root, "goal-a") : join(paths.file, "store-identity");
      // Parent creation is source-fixture setup, never capture behavior.
      await mkdir(dirname(changedPath), {recursive: true});
      let changed = false;
      const interleaved = new Proxy(store, {get(target, key) {
        if (key === "scanCommitted") return async (...args: Parameters<AuthorityStore["scanCommitted"]>) => {
          const page = await target.scanCommitted(...args);
          if (!changed) {
            changed = true;
            await writeFile(changedPath, moved === "identity" ? `file:${"a".repeat(32)}` : JSON.stringify(moved === "selector"
              ? {schema_version: "loopx_local_authority_provider_v0", goal_id: "goal-a", provider: "file", store_identity: identity.store_identity}
              : {schema_version: COLD_SOURCE_IMPORT_WRITER_FENCE_SCHEMA, state: "engaged", goal_id: "goal-a",
                fence_id: "new-fence", import_operation_id: "import", import_plan_sha256: "b".repeat(64)}));
          }
          return page;
        };
        const value = Reflect.get(target, key);
        return typeof value === "function" ? value.bind(target) : value;
      }});
      const destination = join(root, "capture");
      const result = await captureAuthorityBackup({runtime_root: root, goal_id: "goal-a", destination},
        {createStore: () => interleaved});
      assert.equal(result.state, "unknown");
      assert.equal(existsSync(join(destination, authorityBackupDirectory("goal-a"))), false);
      assert.equal(changed, true);
      assert.ok((await readFile(changedPath)).length);
    } finally { await rm(root, {recursive: true, force: true}); }
  });
}

test("missing authority creates no source store; an existing capture is never removed", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-authority-backup-"));
  try {
    const request = {runtime_root: root, goal_id: "goal-a", destination: join(root, "capture")};
    assert.equal((await captureAuthorityBackup(request)).state, "unknown");
    assert.equal(existsSync(join(root, "authority")), false);
    await mkdir(join(request.destination, authorityBackupDirectory("goal-a")), {recursive: true});
    const existing = join(request.destination, authorityBackupDirectory("goal-a"), "retained");
    await writeFile(existing, "existing user artifact");
    await assert.rejects(() => captureAuthorityBackup(request), /EEXIST/);
    assert.equal(await readFile(existing, "utf8"), "existing user artifact");
  } finally { await rm(root, {recursive: true, force: true}); }
});
