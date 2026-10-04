/** Own a managed Host process through exit, pipe drain and descendant cleanup.
 * This is process supervision, not a task lease or a sandbox. */
import {spawn, type ChildProcessWithoutNullStreams} from "node:child_process";
import {setTimeout as delay} from "node:timers/promises";
import {StringDecoder} from "node:string_decoder";
import {waitForProcessGroupStop} from "./host_process_group.ts";

export interface HostProcessRequest {
  argv: string[];
  cwd: string;
  input: string;
  timeout_ms: number | null;
  drain_timeout_ms: number;
  stdout_limit_bytes: number | null;
}
export interface HostProcessResult {
  kind: "result";
  outcome: "exited" | "timeout" | "cancelled" | "output_limit" | "spawn_failed";
  returncode: number | null;
  signal: string | null;
  output_complete: boolean;
  cleanup_scope: "process_group" | "process_tree_best_effort";
  group_signal_sent: boolean;
}
export type HostProcessOutput = {kind: "stdout" | "stderr"; text: string};
/** The group this owner will clean up, reported once the Host is spawned.
 * ``process_group`` is null where cleanup is tree best effort (Windows). */
export type HostProcessSpawned = {kind: "spawned"; pid: number; process_group: number | null};
export const HOST_PROCESS_TERMINATE_GRACE_MS = 300;

/** Restrict transport size separately from the caller's public result budget. */
export function decodeHostProcessRequest(value: unknown): HostProcessRequest {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new TypeError("invalid Host request");
  const v = value as Record<string, unknown>;
  const fields = ["argv", "cwd", "input", "timeout_ms", "drain_timeout_ms", "stdout_limit_bytes"];
  if (Object.keys(v).length !== fields.length || fields.some(k => !Object.hasOwn(v, k)) ||
      !Array.isArray(v.argv) || !v.argv.length || !v.argv[0] || v.argv.some(x => typeof x !== "string" || x.includes("\0")) ||
      typeof v.cwd !== "string" || !v.cwd || v.cwd.includes("\0") || typeof v.input !== "string" ||
      (v.timeout_ms !== null && (typeof v.timeout_ms !== "number" || !Number.isFinite(v.timeout_ms) || v.timeout_ms <= 0 || v.timeout_ms > 2147483647)) ||
      typeof v.drain_timeout_ms !== "number" || !Number.isFinite(v.drain_timeout_ms) || v.drain_timeout_ms < 0 || v.drain_timeout_ms > 30000 ||
      (v.stdout_limit_bytes !== null && (typeof v.stdout_limit_bytes !== "number" ||
        !Number.isSafeInteger(v.stdout_limit_bytes) || v.stdout_limit_bytes < 1))) throw new TypeError("invalid Host request fields");
  return v as unknown as HostProcessRequest;
}

/** A stopped leader does not prove that its process group has stopped. */
function signalGroup(child: ChildProcessWithoutNullStreams, signal: NodeJS.Signals): boolean {
  if (!child.pid) return false;
  try { process.kill(-child.pid, signal); return true; }
  catch (error) { if ((error as NodeJS.ErrnoException).code === "ESRCH") return false; throw error; }
}

/** Optional caller hooks: record the spawned group, or keep stdin open for framed input. */
export type HostProcessHooks = {
  spawned?: (item: HostProcessSpawned) => Promise<void>;
  openInput?: (write: (text: string) => Promise<void>) => void;
};

export async function runHostProcess(request: HostProcessRequest,
  output: (item: HostProcessOutput) => Promise<void>, signal?: AbortSignal,
  terminationGraceMs = HOST_PROCESS_TERMINATE_GRACE_MS,
  {spawned, openInput}: HostProcessHooks = {}): Promise<HostProcessResult> {
  const base: HostProcessResult = {kind: "result", outcome: "spawn_failed", returncode: null, signal: null,
    output_complete: true, cleanup_scope: process.platform === "win32" ? "process_tree_best_effort" : "process_group",
    group_signal_sent: false};
  if (signal?.aborted) return {...base, outcome: "cancelled"};
  const child = spawn(request.argv[0], request.argv.slice(1), {cwd: request.cwd,
    stdio: ["pipe", "pipe", "pipe"], detached: process.platform !== "win32", windowsHide: true});
  let outcome: HostProcessResult["outcome"] = "exited";
  let complete = true, forcedDrain = false, stdoutBytes = 0;
  let cleanup: Promise<void> | undefined;
  const clean = () => cleanup ??= (async () => {
    if (process.platform === "win32") {
      if (!child.pid) return;
      // Try the tree while its leader is still discoverable, before the direct
      // kill fallback. A dead leader still makes this best effort on Windows.
      await new Promise<void>(resolve => {
        const killer = spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"],
          {stdio: "ignore", windowsHide: true, timeout: 1000});
        killer.once("error", () => resolve());
        killer.once("exit", code => { base.group_signal_sent = code === 0; resolve(); });
      });
      if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
      return;
    }
    // Always signal the owned group, including after the leader's exit.
    const sent = signalGroup(child, "SIGTERM");
    base.group_signal_sent ||= sent;
    if (sent) {
      await delay(terminationGraceMs);
      signalGroup(child, "SIGKILL");
      // KILL delivery is asynchronous. Closed pipes and a reaped leader do not
      // establish that descendants have stopped executing or writing.
      await waitForProcessGroupStop(child.pid!);
    }
  })();
  const stop = (reason: HostProcessResult["outcome"]) => {
    if (outcome === "exited") outcome = reason;
    void clean().catch(() => { complete = false; child.kill("SIGKILL"); });
  };
  const abort = () => stop("cancelled");
  signal?.addEventListener("abort", abort, {once: true});
  const deadline = request.timeout_ms === null ? undefined : setTimeout(() => stop("timeout"), request.timeout_ms);
  let drainTimer: ReturnType<typeof setTimeout> | undefined;
  const exited = new Promise<void>(resolve => {
    child.once("error", () => { outcome = "spawn_failed"; resolve(); });
    child.once("exit", (code, sig) => {
      base.returncode = code; base.signal = sig;
      // Descendants may hold inherited pipes forever after the leader exits.
      drainTimer = setTimeout(() => {
        complete = false; forcedDrain = true;
        void clean().catch(() => { complete = false; }).finally(() => {
          child.stdout.destroy(); child.stderr.destroy();
        });
      }, request.drain_timeout_ms);
      resolve();
    });
  });
  const read = async (kind: "stdout" | "stderr") => {
    const decoder = new StringDecoder("utf8");
    try {
      for await (const chunk of child[kind]) {
        const bytes = chunk as Buffer;
        if (kind === "stdout" && request.stdout_limit_bytes !== null) {
          stdoutBytes += bytes.length;
          if (stdoutBytes > request.stdout_limit_bytes) { complete = false; stop("output_limit"); continue; }
        }
        const text = decoder.write(bytes);
        if (text) await output({kind, text}); // Backpressure, not an unbounded output queue.
      }
      const tail = decoder.end();
      if (tail && !(kind === "stdout" && outcome === "output_limit")) await output({kind, text: tail});
    } catch { complete = false; if (!forcedDrain) stop("cancelled"); }
  };
  const reads = Promise.all([read("stdout"), read("stderr")]);
  child.stdin.on("error", () => {}); // A Host may close stdin before consuming it.
  if (child.pid && spawned) {
    // A caller that cannot record the owned group cancels rather than run unaccounted.
    try { await spawned({kind: "spawned", pid: child.pid,
      process_group: process.platform === "win32" ? null : child.pid}); }
    catch { complete = false; stop("cancelled"); }
  }
  if (openInput) {
    // The private preflight transport retains stdin between read-only requests.
    // It owns framing/backpressure, not child lifetime or process-group cleanup.
    // One-shot managed Hosts retain their original EOF behavior.
    const write = async (text: string) => {
      if (child.stdin.destroyed || outcome !== "exited") throw new Error("Host input closed");
      await new Promise<void>((resolve, reject) => {
        child.stdin.write(text, error => error ? reject(error) : resolve());
      });
    };
    try { openInput(write); }
    catch { complete = false; stop("cancelled"); }
  } else child.stdin.end(request.input);
  try {
    await exited;
    await reads;
    if (drainTimer) clearTimeout(drainTimer);
    await clean();
    return {...base, outcome, output_complete: complete};
  } finally {
    if (deadline) clearTimeout(deadline);
    if (drainTimer) clearTimeout(drainTimer);
    signal?.removeEventListener("abort", abort);
    child.stdin.destroy(); child.stdout.destroy(); child.stderr.destroy();
  }
}
