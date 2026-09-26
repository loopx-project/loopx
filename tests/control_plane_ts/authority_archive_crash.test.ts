import assert from "node:assert/strict";
import {fork} from "node:child_process";
import {once} from "node:events";
import {mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {exportAuthorityArchive, restoreAuthorityArchive} from "../../loopx/control_plane/coordination/authority_archive.ts";
import {auditAuthorityArchive} from "../../loopx/control_plane/coordination/authority_archive_audit.ts";

for (const provider of ["file", "sqlite"] as const) {
  test(`${provider}: killed after checkpoint commit; reopen, resume, audit and continue CAS`, {timeout: 60000}, async () => {
    const root = await mkdtemp(join(tmpdir(), "archive-crash-"));
    const source = new SqliteAuthorityStore(join(root, "source"), "goal");
    let worker: ReturnType<typeof fork> | undefined;
    try {
      let previous: string | null = null;
      for (let i = 1; i <= 67; i++) {
        const result = await source.commitAuthority({expected_provider_revision: previous,
          operation_id: `step-${i}`, events: [{round: i}],
          receipts: [{accepted: true, round: i}], next_projection: {goal_id: "goal", round: i}});
        assert.equal(result.status, "applied");
        if (result.status === "applied") previous = result.provider_revision;
      }
      const archive = join(root, "archive");
      const summary = await exportAuthorityArchive(source, "goal", archive);
      const destination = join(root, "target");
      worker = fork(new URL("./authority_archive_restore_process.ts", import.meta.url),
        [archive, destination, summary.archive_sha256, provider, "65"],
        {execArgv: ["--no-warnings", "--experimental-strip-types", "--experimental-sqlite"],
          env: {...process.env, TMPDIR: root, TEMP: root, TMP: root},
          stdio: ["ignore", "ignore", "pipe", "ipc"]});
      let stderr = "";
      worker.stderr!.on("data", chunk => { stderr += String(chunk); });
      const boundary = once(worker, "message");
      const ended = once(worker, "exit");
      const notification = await Promise.race([boundary, ended.then(() => { throw new Error(`worker exited before crash: ${stderr}`); })]);
      assert.deepEqual(notification[0], {status: "durable", cursor: "65"});
      worker.kill("SIGKILL");
      const exit = await ended;
      assert.equal(exit[1], "SIGKILL");
      const reopened = provider === "sqlite" ? new SqliteAuthorityStore(destination, "goal", {existingOnly: true})
        : new FileAuthorityStore(destination, "goal", {existingOnly: true});
      const partial = await reopened.loadAuthority();
      assert.equal(partial.status, "loaded");
      if (partial.status === "loaded") assert.equal(partial.cursor, "65");
      const restored = await restoreAuthorityArchive(archive, reopened, summary.archive_sha256);
      const audited = await auditAuthorityArchive(archive, reopened, summary.archive_sha256);
      assert.equal(audited.status, "matched");
      const original = await reopened.readReceipt("step-1");
      assert.equal(original.status, "found");
      if (original.status === "found") assert.deepEqual(original.receipts, [{accepted: true, round: 1}]);
      assert.equal((await reopened.commitAuthority({expected_provider_revision: restored.target_provider_revision,
        operation_id: "after-recovery", events: [], receipts: [{accepted: true}],
        next_projection: {goal_id: "goal", round: 68}})).status, "applied");
      assert.equal((await auditAuthorityArchive(archive, reopened, summary.archive_sha256, "retained_prefix")).status, "matched");
      assert.equal((await auditAuthorityArchive(archive, reopened, summary.archive_sha256)).status, "mismatch");
      const untouched = await source.loadAuthority();
      assert.equal(untouched.status, "loaded");
      if (untouched.status === "loaded") assert.equal(untouched.cursor, "67");
    } finally {
      if (worker && worker.exitCode === null && worker.signalCode === null) {
        const exited = once(worker, "exit"); worker.kill("SIGKILL"); await exited;
      }
      await rm(root, {recursive: true, force: true});
    }
  });
}
