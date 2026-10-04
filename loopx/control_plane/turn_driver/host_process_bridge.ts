/** Private parent/child transport. EOF means the owning Python process left. */
import {once} from "node:events";
import {decodeHostProcessRequest, runHostProcess} from "./host_process.ts";
const owner = new AbortController();
process.stdin.on("end", () => owner.abort());
process.on("SIGTERM", () => owner.abort());
process.on("SIGINT", () => owner.abort());
process.stdout.on("error", () => owner.abort());
let pending = Buffer.alloc(0), accepted = false;
const emit = async (item: unknown) => {
  if (owner.signal.aborted && process.stdout.destroyed) throw new Error("owner disconnected");
  if (!process.stdout.write(JSON.stringify(item) + "\n")) await once(process.stdout, "drain");
};
process.stdin.on("data", (chunk: Buffer) => {
  if (accepted) return;
  pending = Buffer.concat([pending, chunk]);
  if (pending.length > 8 * 1024 * 1024) { owner.abort(); process.exitCode = 1; process.stdin.destroy(); return; }
  const newline = pending.indexOf(10);
  if (newline < 0) return;
  accepted = true;
  const line = pending.subarray(0, newline).toString("utf8"); pending = Buffer.alloc(0);
  void (async () => {
    try {
      const raw = JSON.parse(line);
      const lease = raw.delegated_lease;
      delete raw.delegated_lease;
      const request = decodeHostProcessRequest(raw);
      // The owned process group is reported before either path can run unaccounted.
      if (lease === undefined) await emit(await runHostProcess(request, emit, owner.signal, undefined, {spawned: emit}));
      else {
        // Ordinary Hosts do not load canonical lease/provider modules.
        const {decodeDelegatedHostLease, runLeasedHostProcess} = await import("./leased_host_process.ts");
        await emit(await runLeasedHostProcess(request, decodeDelegatedHostLease(lease), emit, owner.signal, emit));
      }
    }
    catch { process.exitCode = 1; }
    finally { process.stdin.destroy(); }
  })();
});
