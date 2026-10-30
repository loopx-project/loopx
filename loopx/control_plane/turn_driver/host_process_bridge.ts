/** Private parent/child transport. EOF means the owning Python process left. */
import {once} from "node:events";
import {readFile} from "node:fs/promises";
import {decodeHostProcessRequest, observeHostLaunch, runHostProcess} from "./host_process.ts";
const owner = new AbortController();
// Private transport observations; none grants Goal or execution authority.
type LaunchObservation = {kind: "ready"} | {kind: "not_launched"};
const launchDeadline = process.argv[2] === "--guarded-launch" ? Number(process.argv[3]) : null;
const launchReportIndex = process.argv.indexOf("--windows-launch-status");
const launchReport = launchReportIndex < 0 ? null : process.argv[launchReportIndex + 1];
if (launchReportIndex >= 0 && (!launchReport || process.platform !== "win32"))
  throw new TypeError("invalid Windows Host launch report");
if (launchDeadline !== null && (!Number.isFinite(launchDeadline) || launchDeadline <= 0))
  throw new TypeError("invalid guarded Host bridge deadline");
const abort = () => {
  owner.abort();
  if (!accepted) {
    void emit({kind: "not_launched"} satisfies LaunchObservation).finally(() => process.stdin.destroy());
  }
};
process.stdin.on("end", abort);
process.on("SIGTERM", abort);
process.on("SIGINT", abort);
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
      if (launchDeadline !== null) {
        const remaining = launchDeadline - Date.now();
        if (remaining <= 0 || owner.signal.aborted) {
          await emit({kind: "not_launched"} satisfies LaunchObservation);
          return;
        }
        if (raw.timeout_ms !== null) raw.timeout_ms = Math.min(raw.timeout_ms, remaining);
      }
      const lease = raw.delegated_lease;
      delete raw.delegated_lease;
      const request = decodeHostProcessRequest(raw);
      // The owned process group is reported before either path can run unaccounted.
      let result;
      if (lease === undefined) result = await runHostProcess(request, emit, owner.signal, undefined, {spawned: emit});
      else {
        // Ordinary Hosts do not load canonical lease/provider modules.
        const {decodeDelegatedHostLease, runLeasedHostProcess} = await import("./leased_host_process.ts");
        result = await runLeasedHostProcess(request, decodeDelegatedHostLease(lease), emit, owner.signal, emit);
      }
      // Read only after supervision has completed. Missing/corrupt evidence
      // fails the transport; business stdout cannot supply this observation.
      if (launchReport && result.outcome === "exited")
        result = observeHostLaunch(result, JSON.parse(await readFile(launchReport, "utf8")));
      await emit(result);
    }
    catch { process.exitCode = 1; }
    finally { process.stdin.destroy(); }
  })();
});
if (launchDeadline !== null) await emit({kind: "ready"} satisfies LaunchObservation);
