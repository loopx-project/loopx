/** Private, single-flight read-only preview transport. No authority/result cache.
 * The original Host owner supervises one fixed-cwd worker through cleanup. */
import {once} from "node:events";
import {decodeHostProcessRequest, runHostProcess, type HostProcessResult} from "../turn_driver/host_process.ts";

const LIMIT = 1024 * 1024;
const MAX_REQUESTS = 128;
const IDLE_MS = 30000;
const LIFETIME_MS = 300000;
type StopReason = HostProcessResult["outcome"] | "idle" | "lifetime" | "retired" | "input_limit" | "invalid_request";
const owner = new AbortController();
let started = false, stopped = false, sequence = 0;
let buffer = Buffer.alloc(0), output = "";
let pending: {id: number; timer: ReturnType<typeof setTimeout>} | undefined;
let idle: ReturnType<typeof setTimeout> | undefined;
let lifetime: ReturnType<typeof setTimeout> | undefined;
let failure: StopReason = "cancelled";
let write: ((text: string) => Promise<void>) | undefined;
let running: Promise<HostProcessResult> | undefined;

const emit = async (item: unknown) => {
  if (process.stdout.destroyed) throw new Error("owner disconnected");
  if (!process.stdout.write(JSON.stringify(item) + "\n")) await once(process.stdout, "drain");
};
const stop = (reason: StopReason = "cancelled") => {
  if (stopped) return;
  stopped = true; failure = reason; owner.abort();
  if (idle) clearTimeout(idle);
  if (lifetime) clearTimeout(lifetime);
  if (pending) clearTimeout(pending.timer);
};
const armIdle = () => { idle = setTimeout(() => stop("idle"), IDLE_MS); };

async function accept(value: unknown) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("invalid preview frame");
  const v = value as Record<string, unknown>;
  if (!started) {
    if (v.kind !== "start" || Object.keys(v).length !== 2) throw new Error("invalid preview start");
    const request = decodeHostProcessRequest(v.request);
    if (request.input !== "") throw new Error("preview input must be framed");
    started = true;
    // Stop accepting before the Host's independent lifetime deadline begins
    // cleanup; otherwise a new request could be admitted into a dying worker.
    lifetime = setTimeout(() => stop("lifetime"), LIFETIME_MS);
    running = runHostProcess({...request, timeout_ms: LIFETIME_MS,
      stdout_limit_bytes: LIMIT * MAX_REQUESTS}, async item => {
      if (item.kind !== "stdout") return; // Never relay private worker diagnostics.
      output += item.text;
      if (Buffer.byteLength(output) > LIMIT) throw new Error("preview output exceeds frame limit");
      let newline: number;
      while ((newline = output.indexOf("\n")) >= 0) {
        const response = JSON.parse(output.slice(0, newline)); output = output.slice(newline + 1);
        if (!pending || response.id !== pending.id || response.kind !== "preview" ||
            Object.keys(response).length !== 4 || !Number.isInteger(response.returncode) ||
            !response.value || typeof response.value !== "object" || Array.isArray(response.value)) {
          throw new Error("invalid preview response");
        }
        clearTimeout(pending.timer); pending = undefined;
        await emit(response);
        if (response.id >= MAX_REQUESTS) stop("retired");
        else if (!pending) armIdle();
      }
    }, owner.signal, undefined, input => { write = input; });
    void running.then(async result => {
      const originalPending = pending;
      stop(result.outcome);
      // A deadline/cancel/crash is reported only AFTER the Host owner has
      // stopped the process group. A stopped leader alone is insufficient.
      if (originalPending) await emit({kind: "failure", id: originalPending.id,
        outcome: failure === "cancelled" ? result.outcome : failure === "lifetime" ? "timeout" : failure});
      else if (result.output_complete && (failure === "idle" || failure === "retired" || failure === "lifetime")) {
        // This fence follows process-group cleanup. A racing request beyond
        // last_id was never accepted; its caller may start a new owned worker.
        await emit({kind: "retired", last_id: sequence});
      }
    }).catch(() => { process.exitCode = 1; }).finally(() => process.stdin.destroy());
    armIdle();
    await emit({kind: "ready"});
    return;
  }
  if (stopped || pending || sequence >= MAX_REQUESTS || !write || v.kind !== "request" || Object.keys(v).length !== 4 ||
      v.id !== sequence + 1 || !Array.isArray(v.argv) || !v.argv.length ||
      v.argv.some(x => typeof x !== "string" || x.includes("\0")) ||
      typeof v.timeout_ms !== "number" || !Number.isFinite(v.timeout_ms) ||
      v.timeout_ms <= 0 || v.timeout_ms > 60000) throw new Error("invalid preview request");
  if (idle) clearTimeout(idle);
  sequence++;
  pending = {id: sequence, timer: setTimeout(() => stop("timeout"), v.timeout_ms)};
  await write(JSON.stringify({id: sequence, argv: v.argv}) + "\n");
}

// One request may be in flight; reject pipelining instead of retaining a queue.
process.stdin.on("data", (chunk: Buffer) => {
  buffer = Buffer.concat([buffer, chunk]);
  if (buffer.length > LIMIT) { stop("input_limit"); process.stdin.destroy(); return; }
  const newline = buffer.indexOf(10);
  if (newline < 0) return;
  const line = buffer.subarray(0, newline).toString("utf8"); buffer = buffer.subarray(newline + 1);
  if (buffer.length) { stop("invalid_request"); return; }
  void Promise.resolve().then(() => accept(JSON.parse(line))).catch(() => {
    stop("invalid_request"); if (!running) process.stdin.destroy();
  });
});
process.stdin.on("end", () => stop());
process.on("SIGTERM", () => stop());
process.on("SIGINT", () => stop());
process.stdout.on("error", () => stop());
