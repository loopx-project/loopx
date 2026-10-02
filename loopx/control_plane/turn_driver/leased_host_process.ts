/** Renew the original canonical execution while its delegated CLI runs.
 * The CLI includes Host execution and independent Turn validation. */
import {canonicalTaskLease} from "../coordination/task_lease_state.ts";
import {leaseEpoch, leaseIsActive, leaseVersion, normalizeTtl, type LeaseRecord} from "../work_items/task_lease_acquire.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {parseIsoTimestamp} from "../runtime_timestamp.ts";
import {runHostProcess, type HostProcessRequest, type HostProcessResult, type HostProcessOutput} from "./host_process.ts";

export interface DelegatedHostLease {
  lease: LeaseRecord;
  renew_argv: string[];
  read_argv: string[];
  ttl_seconds: number;
}

class LeaseTransportUnavailable extends Error {}

export function decodeDelegatedHostLease(raw: unknown): DelegatedHostLease {
  const value = requireJsonObject(raw, "delegated Host lease");
  const record = requireJsonObject(value.lease, "delegated lease record");
  if (typeof record.goal_id !== "string" || typeof record.todo_id !== "string") throw new TypeError("delegated lease identity required");
  const lease = canonicalTaskLease(record, record.goal_id, record.todo_id);
  const argv = (field: string): string[] => {
    const list = value[field];
    if (!Array.isArray(list) || !list.length || list.some(x => typeof x !== "string" || !x || x.includes("\0"))) {
      throw new TypeError("invalid delegated lease command");
    }
    return list as string[];
  };
  return {lease, renew_argv: argv("renew_argv"), read_argv: argv("read_argv"), ttl_seconds: normalizeTtl(value.ttl_seconds)};
}

export async function runLeasedHostProcess(request: HostProcessRequest, context: DelegatedHostLease,
  output: (item: HostProcessOutput) => Promise<void>, owner: AbortSignal): Promise<HostProcessResult> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  owner.addEventListener("abort", abort, {once: true});
  if (owner.aborted) abort();
  let lease = context.lease, lost = false, finished = false;
  let renewalTimer: ReturnType<typeof setTimeout> | undefined;
  let expiryTimer: ReturnType<typeof setTimeout> | undefined;
  let pending: Promise<void> | undefined;
  // A receipt is historical. This exact claim replay proves the original
  // execution is still current and returns its latest version, not a new key.
  const currentProof = (raw: unknown): LeaseRecord => {
    const result = requireJsonObject(raw, "delegated current proof");
    const record = requireJsonObject(result.lease, "delegated current lease");
    const observed = canonicalTaskLease(record, String(lease.goal_id), String(lease.todo_id));
    if (result.ok !== true || !leaseIsActive(observed, new Date()) || observed.owner !== lease.owner ||
        observed.idempotency_key !== lease.idempotency_key || leaseEpoch(observed) !== leaseEpoch(lease) ||
        leaseVersion(observed) < leaseVersion(lease)) throw new Error("delegated execution proof lost");
    return observed;
  };
  const cli = async (argv: string[]): Promise<unknown> => {
    let stdout = "";
    const result = await runHostProcess({...request, argv, input: "", timeout_ms: 60_000,
      drain_timeout_ms: 2000, stdout_limit_bytes: 128_000}, async item => {
      if (item.kind === "stdout") stdout += item.text;
    }, controller.signal);
    if (result.outcome !== "exited" || !result.output_complete) {
      throw new LeaseTransportUnavailable("delegated lease command unavailable");
    }
    try { return JSON.parse(stdout); }
    catch { throw new LeaseTransportUnavailable("delegated lease reply unavailable"); }
  };
  const lose = () => { lost = true; abort(); };
  const clearTimers = () => {
    clearTimeout(renewalTimer); clearTimeout(expiryTimer);
  };
  const schedule = () => {
    clearTimers();
    const expires = parseIsoTimestamp(String(lease.expires_at));
    const remaining = expires === null ? 0 : expires.valueOf() - Date.now();
    if (remaining <= 0) { lose(); return; }
    // Stop at the last proved deadline even if renewal or its transport hangs.
    expiryTimer = setTimeout(lose, remaining);
    renewalTimer = setTimeout(() => {
      pending = (async () => {
        const argv = [...context.renew_argv, "--expected-version", String(leaseVersion(lease)),
          "--ttl-seconds", String(context.ttl_seconds)];
        try {
          let renewed: unknown;
          try { renewed = await cli(argv); }
          catch (error) {
            if (!(error instanceof LeaseTransportUnavailable) || controller.signal.aborted) throw error;
            // One bounded same-intent recovery for a lost response. Never
            // adopt a newer version by editing the rejected renewal request.
            renewed = await cli(argv);
          }
          if (requireJsonObject(renewed, "delegated renewal").ok !== true) throw new Error("delegated renewal rejected");
          const observed = currentProof(await cli(context.read_argv));
          if (leaseVersion(observed) <= leaseVersion(lease)) throw new Error("delegated lease did not renew");
          lease = observed;
          if (!finished) schedule();
        } catch { lose(); }
      })();
    }, Math.max(1, Math.min(30_000, Math.floor(remaining / 2))));
  };
  try {
    // No Host input or process launch precedes current execution readback.
    try { lease = currentProof(await cli(context.read_argv)); }
    catch { lose(); }
    schedule();
    // The CLI's TERM adapter unwinds the nested Host transport (bounded at
    // five seconds). Allow that acknowledgement before a forced group kill.
    // This is private supervisor behavior, not a model-controlled timeout.
    const result = await runHostProcess(request, output, controller.signal, 6000);
    // Keep the proved expiry armed while a renewal reply is in flight. A
    // returned model result cannot make a hung lease transport authoritative.
    finished = true;
    clearTimeout(renewalTimer);
    await pending;
    if (!lost) {
      try { lease = currentProof(await cli(context.read_argv)); }
      catch { lose(); }
    }
    finished = true;
    clearTimers();
    return lost ? {...result, outcome: "cancelled", output_complete: false} : result;
  } finally {
    finished = true;
    clearTimers();
    abort();
    await pending;
    owner.removeEventListener("abort", abort);
  }
}
