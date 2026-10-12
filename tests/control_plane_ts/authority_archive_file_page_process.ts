/** Owned process death around File's archive-page atomic publication. */
import {open, rename} from "node:fs/promises";
import {FileAuthorityStore, syncAuthorityDirectory} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {restoreAuthorityArchive} from "../../loopx/control_plane/coordination/authority_archive.ts";
const [archive, target, digest, boundary] = process.argv.slice(2);
let resume: () => void = () => {};
const barrier = new Promise<void>(resolve => {resume = resolve;});
process.on("message", () => resume());
class InterruptedFile extends FileAuthorityStore {
  protected override async replaceDurably(path: string, bytes: Uint8Array) {
    if (path !== this.path || JSON.parse(Buffer.from(bytes).toString()).cursor !== "64") {
      return super.replaceDurably(path, bytes);
    }
    // Filesystem fault seam only: original lock, journal codec and CAS execute
    // normally. The parent kills this owned worker at the named rename boundary.
    const temporary = `${path}.tmp-crash-fixture`;
    const handle = await open(temporary, "wx", 0o600);
    await handle.writeFile(bytes); await handle.sync(); await handle.close();
    if (boundary === "after") await rename(temporary, path);
    process.send!({status: "publication", boundary, cursor: "64"});
    await barrier;
    if (boundary === "before") await rename(temporary, path);
    await syncAuthorityDirectory(this.directory);
  }
}
await restoreAuthorityArchive(archive, new InterruptedFile(target, "goal"), digest);
process.exitCode = 2;
