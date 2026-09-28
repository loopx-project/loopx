/** A real process death after durable SQLite/File commit and before readback. */
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {restoreAuthorityArchive} from "../../loopx/control_plane/coordination/authority_archive.ts";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
const [archive, target, digest, kind, stopAt] = process.argv.slice(2);
const store = kind === "sqlite" ? new SqliteAuthorityStore(target, "goal") : new FileAuthorityStore(target, "goal");
// Hold the IPC channel across the durable boundary. A pending promise alone
// leaves the event loop empty, so the child exits 13 ("unfinished top-level
// await") before the parent can deliver SIGKILL. The message listener pins the
// channel, the same barrier the task-lease crash worker uses.
let resume: () => void = () => {};
const crashBarrier = new Promise<void>(resolve => {resume = resolve;});
process.on("message", () => resume());
const interrupted: AuthorityStore = {
  providerKind: store.providerKind,
  storeIdentity: () => store.storeIdentity(), loadAuthority: () => store.loadAuthority(),
  readReceipt: id => store.readReceipt(id), scanCommitted: (after, limit) => store.scanCommitted(after, limit),
  commitAuthority: async request => {
    const committed = await store.commitAuthority(request);
    if (committed.status === "applied" && committed.cursor === stopAt) {
      process.send!({status: "durable", cursor: committed.cursor});
      // The parent requires the named crash boundary and signals SIGKILL here.
      await crashBarrier;
    }
    return committed;
  },
};
await restoreAuthorityArchive(archive, interrupted, digest);
process.exitCode = 2; // The parent requires the named crash boundary, never this path.
