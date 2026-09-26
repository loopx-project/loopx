/** A real process death after durable SQLite/File commit and before readback. */
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {restoreAuthorityArchive} from "../../loopx/control_plane/coordination/authority_archive.ts";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
const [archive, target, digest, kind, stopAt] = process.argv.slice(2);
const store = kind === "sqlite" ? new SqliteAuthorityStore(target, "goal") : new FileAuthorityStore(target, "goal");
const interrupted: AuthorityStore = {
  providerKind: store.providerKind,
  storeIdentity: () => store.storeIdentity(), loadAuthority: () => store.loadAuthority(),
  readReceipt: id => store.readReceipt(id), scanCommitted: (after, limit) => store.scanCommitted(after, limit),
  commitAuthority: async request => {
    const committed = await store.commitAuthority(request);
    if (committed.status === "applied" && committed.cursor === stopAt) {
      process.send!({status: "durable", cursor: committed.cursor});
      // IPC keeps the worker alive until the parent sends SIGKILL.
      await new Promise<never>(() => {});
    }
    return committed;
  },
};
await restoreAuthorityArchive(archive, interrupted, digest);
process.exitCode = 2; // The parent requires the named crash boundary, never this path.
