/** Real-process fault boundaries: SIGKILL leaves durable provider/selector bytes
 * and the actual writer lock behind, rather than simulating an in-memory error. */
import fs from "node:fs/promises";
import {syncBuiltinESMExports} from "node:module";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {localAuthorityProviderPaths} from "../../loopx/control_plane/coordination/local_authority_provider.ts";
import {manageLocalAuthorityArchive} from "../../loopx/control_plane/coordination/local_authority_archive.ts";

const request = JSON.parse(process.argv[2]);
request.runtime_root = await fs.realpath(request.runtime_root);
const boundary = process.argv[3];
async function suspend() {
  process.send?.({boundary});
  setInterval(() => {}, 1000);
  await new Promise(() => {});
}
if (boundary === "restoring") {
  const commit = SqliteAuthorityStore.prototype.commitAuthority;
  SqliteAuthorityStore.prototype.commitAuthority = async function (value) {
    const result = await commit.call(this, value);
    if (result.status === "applied") await suspend();
    return result;
  };
} else if (boundary === "published" || boundary === "publication-error") {
  const rename = fs.rename;
  const marker = localAuthorityProviderPaths(request.runtime_root, request.goal_id).marker;
  fs.rename = async (from, to) => {
    await rename(from, to);
    if (to === marker && JSON.parse(await fs.readFile(marker, "utf8")).provider === "sqlite") {
      if (boundary === "publication-error") throw new Error("Injected failure after selector rename");
      await suspend();
    }
  };
  syncBuiltinESMExports();
}
const result = await manageLocalAuthorityArchive(request);
process.send?.({unexpected: result});
process.disconnect?.();
