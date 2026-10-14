import {executeCanonicalTaskLeaseAcquire} from "../../loopx/control_plane/coordination/task_lease_acquire.ts";
/** Disposable child for acquisition/lifecycle crash and race qualification. */
import {readFileSync, writeFileSync} from "node:fs";
import {openLocalAuthorityStore} from "../../loopx/control_plane/coordination/local_authority_provider.ts";
import {executeCanonicalTaskLeaseLifecycle} from "../../loopx/control_plane/coordination/task_lease_lifecycle.ts";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
const [path, mode, ttl] = process.argv.slice(2);
const request = JSON.parse(readFileSync(path!, "utf8"));
const stageMarkerPath = `${path}.stage`;
const store = await openLocalAuthorityStore(request.runtime_root, request.goal_id);
let release: () => void = () => {};
const barrier = new Promise<void>(resolve => {release = resolve;});
process.on("message", () => release());
const measured: AuthorityStore = {
  storeIdentity: () => store.storeIdentity(), readReceipt: id => store.readReceipt(id),
  scanCommitted: (...args) => store.scanCommitted(...args),
  loadAuthority: async () => {
    const head = await store.loadAuthority();
    if (mode === "race") {process.send!({ready: true}); await barrier;}
    return head;
  },
  commitAuthority: async input => {
    if (mode === "before") {
      writeFileSync(stageMarkerPath, mode);
      process.kill(process.pid, "SIGKILL");
    }
    const result = await store.commitAuthority(input);
    if (mode === "after" && result.status === "applied") {
      writeFileSync(stageMarkerPath, mode);
      process.kill(process.pid, "SIGKILL");
    }
    return result;
  },
};
const execute = request.operation === "acquire" ? executeCanonicalTaskLeaseAcquire : executeCanonicalTaskLeaseLifecycle;
const result = await execute(measured, {...request,
  registered_agents: ["agent-a", "agent-b"], now: new Date(request.now), ttl_seconds: request.operation === "release" ? null : Number(ttl)});
process.stdout.write(JSON.stringify(result));
if (process.connected) process.disconnect();
