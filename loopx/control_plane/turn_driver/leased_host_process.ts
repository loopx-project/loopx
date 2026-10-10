/** Renew the original canonical execution while its delegated CLI runs.
 * The CLI includes Host execution and independent Turn validation. */
import {canonicalTaskLease} from "../coordination/task_lease_state.ts";
import {leaseEpoch, leaseIsActive, leaseVersion, normalizeTtl, type LeaseRecord} from "../work_items/task_lease_acquire.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {parseIsoTimestamp} from "../runtime_timestamp.ts";
import {runHostProcess, type HostProcessRequest, type HostProcessResult, type HostProcessOutput,
  type HostProcessSpawned} from "./host_process.ts";

export interface DelegatedHostLease {
  lease: LeaseRecord;
  renew_argv: string[];
  read_argv: string[];
  ttl_seconds: number;
}

type LeaseFailureReason = "owner_cancelled" | "execution_proof_rejected" | "lease_inactive"
  | "execution_identity_changed" | "transport_unavailable" | "renewal_rejected"
  | "renewal_not_advanced" | "proved_deadline_elapsed" | "lease_observation_failed";
type LeaseFailureBoundary = "initial_proof" | "renewal" | "final_proof" | "deadline";
interface LeaseFailure {reason: LeaseFailureReason; boundary: LeaseFailureBoundary}
export type LeasedHostProcessResult = HostProcessResult & {lease_failure?: LeaseFailure};

class LeaseSupervisionFailure extends Error {
  readonly reason: LeaseFailureReason;
  constructor(reason: LeaseFailureReason, message: string = reason) {
    super(message);
    this.reason = reason;
  }
}
class LeaseTransportUnavailable extends LeaseSupervisionFailure {
  constructor(message: string) { super("transport_unavailable", message); }
}

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
  output: (item: HostProcessOutput) => Promise<void>, owner: AbortSignal,
  spawned?: (item: HostProcessSpawned) => Promise<void>): Promise<LeasedHostProcessResult> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  owner.addEventListener("abort", abort, {once: true});
  if (owner.aborted) abort();
  let lease = context.lease, finished = false;
  let failure: LeaseFailure | undefined;
  let renewalTimer: ReturnType<typeof setTimeout> | undefined;
  let expiryTimer: ReturnType<typeof setTimeout> | undefined;
  let pending: Promise<void> | undefined;
  // A receipt is historical. This exact claim replay proves the original
  // execution is still current and returns its latest version, not a new key.
  const currentProof = (raw: unknown): LeaseRecord => {
    const result = requireJsonObject(raw, "delegated current proof");
    if (result.ok !== true) throw new LeaseSupervisionFailure("execution_proof_rejected");
    const record = requireJsonObject(result.lease, "delegated current lease");
    const observed = canonicalTaskLease(record, String(lease.goal_id), String(lease.todo_id));
    if (!leaseIsActive(observed, new Date())) throw new LeaseSupervisionFailure("lease_inactive");
    if (observed.owner !== lease.owner ||
        observed.idempotency_key !== lease.idempotency_key || leaseEpoch(observed) !== leaseEpoch(lease) ||
        leaseVersion(observed) < leaseVersion(lease)) throw new LeaseSupervisionFailure("execution_identity_changed");
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
  const lose = (reason: LeaseFailureReason, boundary: LeaseFailureBoundary) => {
    // Keep the first failure, before aborting its in-flight transport. A later
    // cancellation or deadline must not replace the original causal boundary.
    failure ??= {reason: owner.aborted ? "owner_cancelled" : reason, boundary};
    abort();
  };
  const reject = (error: unknown, boundary: LeaseFailureBoundary) =>
    lose(error instanceof LeaseSupervisionFailure ? error.reason : "lease_observation_failed", boundary);
  const clearTimers = () => {
    clearTimeout(renewalTimer); clearTimeout(expiryTimer);
  };
  const schedule = () => {
    clearTimers();
    const expires = parseIsoTimestamp(String(lease.expires_at));
    const remaining = expires === null ? 0 : expires.valueOf() - Date.now();
    if (remaining <= 0) { lose("proved_deadline_elapsed", "deadline"); return; }
    // Stop at the last proved deadline even if renewal or its transport hangs.
    expiryTimer = setTimeout(() => lose("proved_deadline_elapsed", "deadline"), remaining);
    // A returned Host still needs final proof under the latest proved expiry.
    // Only periodic renewal ends at Host return; expiry supervision does not.
    if (finished) return;
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
          if (requireJsonObject(renewed, "delegated renewal").ok !== true) throw new LeaseSupervisionFailure("renewal_rejected");
          const observed = currentProof(await cli(context.read_argv));
          if (leaseVersion(observed) <= leaseVersion(lease)) throw new LeaseSupervisionFailure("renewal_not_advanced");
          lease = observed;
          schedule();
        } catch (error) { reject(error, "renewal"); }
      })();
    }, Math.max(1, Math.min(30_000, Math.floor(remaining / 2))));
  };
  try {
    // No Host input or process launch precedes current execution readback.
    try { lease = currentProof(await cli(context.read_argv)); }
    catch (error) { reject(error, "initial_proof"); }
    schedule();
    // The CLI's TERM adapter unwinds the nested Host transport (bounded at
    // five seconds). Allow that acknowledgement before a forced group kill.
    // This is private supervisor behavior, not a model-controlled timeout.
    const result = await runHostProcess(request, output, controller.signal, 6000, {spawned});
    // Keep the proved expiry armed while a renewal reply is in flight. A
    // returned model result cannot make a hung lease transport authoritative.
    finished = true;
    clearTimeout(renewalTimer);
    await pending;
    if (!failure) {
      try { lease = currentProof(await cli(context.read_argv)); }
      catch (error) { reject(error, "final_proof"); }
    }
    finished = true;
    clearTimers();
    return failure ? {...result, outcome: "cancelled", output_complete: false, lease_failure: failure} : result;
  } finally {
    finished = true;
    clearTimers();
    abort();
    await pending;
    owner.removeEventListener("abort", abort);
  }
}
