/** Stop at the real provider CAS after native source-lock adoption. */
import {existsSync, readFileSync, writeFileSync} from "node:fs";
import {join} from "node:path";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {commitLocalTeamPlan} from "../../loopx/control_plane/work_items/team_plan_authority.ts";

const [barrier, provider] = process.argv.slice(2) as [string, string];
const prototype = provider === "sqlite" ? SqliteAuthorityStore.prototype : FileAuthorityStore.prototype;
const original = prototype.commitAuthority;
prototype.commitAuthority = async function(commit) {
  writeFileSync(join(barrier, "adopted"), String(process.pid));
  const deadline = Date.now() + 20000;
  while (!existsSync(join(barrier, "release"))) {
    if (Date.now() >= deadline) throw new Error("team plan test barrier timed out");
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 10);
  }
  return await original.call(this as FileAuthorityStore & SqliteAuthorityStore, commit);
};
const result = await commitLocalTeamPlan(JSON.parse(readFileSync(join(barrier, "request.json"), "utf8")));
writeFileSync(join(barrier, "result.json"), JSON.stringify(result));
writeFileSync(join(barrier, "finished"), "");
